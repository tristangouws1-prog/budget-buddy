"""
============================================================================
 multi_runner.py  —  WATCH A WHOLE WATCHLIST AT ONCE
============================================================================
The single-symbol bot.py watches one asset. This runner watches MANY (a
"watchlist") in one process, the way a real desk does.

THREE THINGS IT GETS RIGHT (and why each matters):

1. PER-SYMBOL STATE.
   Each symbol needs its OWN indicators, its OWN open position, and its OWN
   prefilter memory. BTC being oversold says nothing about whether you should
   sell the AAPL you hold. So we keep a separate little "SymbolState" object
   per symbol. Mixing these up (e.g. using BTC's RSI in an AAPL decision) is
   the classic multi-symbol bug — we avoid it by construction.

2. POOLED EXPENSIVE RESOURCES.
   The opposite is true for the costly stuff. There is ONE shared TieredDecider
   across all symbols, so its prompt cache and its cost counters span the whole
   watchlist. The static instruction block is written to cache once and then
   reused for every symbol's decision — caching + multi-symbol compound here.

3. OPPORTUNITY RANKING.
   When several symbols signal in the same cycle but you have limited cash, you
   must CHOOSE. The runner gathers everything that fired this cycle and ranks
   them, so capital goes to the strongest setup instead of whatever happens to
   be first alphabetically.

This runner reuses the EXISTING layers unchanged — datafeed, strategy,
prefilter, model_tier, risk, execution. That's the payoff of building them as
separate, swappable pieces earlier: composing them into a bigger system is easy.

NOTE: still paper-only and, by default, the model is advisory (logged, not
driving trades). Same safety stance as bot.py.
============================================================================
"""

import datafeed
import strategy as strat
from prefilter import PreFilter
from model_tier import TieredDecider
from risk import RiskManager
from execution import PaperBroker


class SymbolState:
    """
    Everything that must be tracked SEPARATELY for one symbol.
    Bundling it in a class keeps each symbol's world cleanly isolated.
    """
    def __init__(self, symbol, data):
        self.symbol = symbol
        self.data = data            # this symbol's candles + indicators + signal
        self.gate = PreFilter()     # its OWN prefilter (own memory of last state)
        self.row_iter = data.iterrows()   # walk its candles independently


class MultiRunner:
    def __init__(self, watchlist, start, end, interval="1d",
                 strategy="ma_crossover", starting_cash=1000.0):
        self.watchlist = watchlist
        self.strategy_fn = strat.STRATEGIES[strategy]
        self.strategy_name = strategy

        # --- SHARED resources (one set for the whole watchlist) ------------
        # One broker holding the shared cash pool. (All symbols draw from the
        # same account, so position sizing naturally competes for capital.)
        self.broker = PaperBroker(starting_cash=starting_cash,
                                  db_path="paper_trades_multi.db")
        self.risk = RiskManager()
        self.decider = TieredDecider(live_api=False)   # shared cache + counters

        # --- PER-SYMBOL state ----------------------------------------------
        self.states = {}
        for sym in watchlist:
            data = datafeed.get_data(sym, start, end, interval)
            data = datafeed.add_indicators(data)
            data = self.strategy_fn(data)
            self.states[sym] = SymbolState(sym, data)

        # Bookkeeping for the cost report.
        self.cycles = 0
        self.api_skipped = 0
        self.last_price = {}   # most recent price seen per symbol (for valuation)

    def _score_opportunity(self, sym, row, verdict):
        """
        Turn a fired signal into a comparable NUMBER so we can rank symbols
        against each other this cycle. Higher = more attractive.

        We use the model's confidence as the base, nudged by how far RSI is
        into an extreme (more extreme = stronger mean-reversion setup). This is
        a simple, transparent ranking — not a magic formula. Tune freely.
        """
        confidence = verdict.get("confidence", 0.5)
        rsi = row["rsi"]
        # Distance of RSI from the neutral midpoint (50), scaled to 0..1.
        rsi_extremity = abs(rsi - 50) / 50
        return confidence + 0.2 * rsi_extremity

    def run(self):
        """
        Step ALL symbols forward together, cycle by cycle, until every
        symbol's data is exhausted. Within each cycle we:
          1. advance every symbol one candle,
          2. enforce risk (stops) on any open positions,
          3. run each symbol's free prefilter,
          4. for those that pass, get a (shared, cached) tiered decision,
          5. RANK the candidates and act on the best ones with available cash.
        """
        print(f"\n[multi] Watching {len(self.watchlist)} symbols "
              f"with '{self.strategy_name}'...\n")

        exhausted = set()
        while len(exhausted) < len(self.watchlist):
            self.cycles += 1
            candidates = []   # symbols that want to BUY this cycle, with scores

            for sym, state in self.states.items():
                if sym in exhausted:
                    continue
                # 1) advance this symbol one candle
                try:
                    _, row = next(state.row_iter)
                except StopIteration:
                    exhausted.add(sym)
                    continue

                price = row["Close"]
                if price != price:        # indicators not ready yet
                    continue
                self.last_price[sym] = price   # remember for final valuation

                pos = self.broker.position
                holding_this = (pos is not None and pos["symbol"] == sym)

                # 2) RISK FIRST: stops/targets on this symbol if we hold it
                if holding_this:
                    self.broker.check_stops(price)
                    pos = self.broker.position  # may have closed

                # 3) FREE prefilter gate
                worth_it, reasons = state.gate.should_call_api(row, holding_this)
                if not worth_it:
                    self.api_skipped += 1
                    continue

                # 4) shared, cached tiered decision
                context = {
                    "symbol": sym,
                    "price": round(float(price), 2),
                    "rsi": round(float(row["rsi"]), 1),
                    "sma_fast": round(float(row["sma_fast"]), 2),
                    "sma_slow": round(float(row["sma_slow"]), 2),
                    "holding": holding_this,
                    "triggers": reasons,
                }
                verdict = self.decider.decide(context)

                # Use the deterministic strategy signal to decide direction,
                # exactly like bot.py (model is advisory here).
                want_in = (row["position"] == 1)

                if want_in and not holding_this and self.broker.position is None:
                    # candidate BUY — score it for ranking
                    score = self._score_opportunity(sym, row, verdict)
                    candidates.append((score, sym, price))
                elif not want_in and holding_this:
                    # exit immediately — exits aren't a competition for cash
                    self.broker.sell(price, reason="SIGNAL-EXIT")

            # 5) RANK buys and act on the best while cash allows. Our simple
            #    broker holds one position at a time, so we take the top pick;
            #    the structure already supports more if you extend the broker.
            candidates.sort(reverse=True)   # highest score first
            for score, sym, price in candidates:
                if self.broker.position is not None:
                    break  # capital committed; rest wait for a future cycle
                value = self.broker.equity(price)
                sizing = self.risk.position_size(value, price)
                self.broker.buy(
                    symbol=sym, price=price,
                    cash_to_deploy=sizing["cash_to_deploy"],
                    stop=self.risk.stop_loss_price(price),
                    target=self.risk.take_profit_price(price),
                    reason=f"SIGNAL-ENTER (rank score {score:.2f})",
                )

        self._report()

    def _report(self):
        rep = self.decider.cost_report()
        naive = self.cycles * len(self.watchlist)   # every symbol, every cycle
        print("\n" + "=" * 56)
        print(" MULTI-SYMBOL RUN COMPLETE")
        print("=" * 56)
        print(f" Watchlist          : {', '.join(self.watchlist)}")
        print(f" Strategy           : {self.strategy_name}")
        # Value any still-open position at its symbol's last seen price.
        pos = self.broker.position
        if pos is not None:
            final_value = self.broker.equity(self.last_price.get(pos["symbol"], pos["entry"]))
        else:
            final_value = self.broker.cash
        print(f" Final account value: ${final_value:,.2f}")
        print("-" * 56)
        print(" API COST SAVINGS (pooled across the whole watchlist)")
        print(f"   Naive calls (every symbol, every cycle): {naive}")
        print(f"   Skipped free by prefilters             : {self.api_skipped}")
        print(f"   Cheap-model triage calls               : {rep['cheap_calls']}")
        print(f"   Expensive-model calls                  : {rep['expensive_calls']}")
        if naive:
            print(f"   Expensive calls as % of naive          : "
                  f"{rep['expensive_calls']/naive*100:.0f}%")
        print("=" * 56)
        print(" Paper-only. Model is advisory. Not financial advice.")
        print("=" * 56 + "\n")


if __name__ == "__main__":
    # Demo watchlist spanning all three asset classes.
    runner = MultiRunner(
        watchlist=["BTC-USD", "ETH-USD", "AAPL", "EURUSD=X"],
        start="2022-01-01",
        end="2024-01-01",
        interval="1d",
        strategy="ma_crossover",
        starting_cash=1000.0,
    )
    runner.run()
