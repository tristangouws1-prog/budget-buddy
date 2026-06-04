"""
============================================================================
 bot.py  —  THE ORCHESTRATOR (ties layers 1-4 together)
============================================================================
This is the conductor. It pulls data (layer 1), gets signals (layer 2),
applies risk rules (layer 3), and executes paper trades (layer 4), walking
through history one candle at a time exactly as it would walk through live
candles in real time.

Running it over history like this is really a 'replay' — a high-fidelity
paper-trading run. The exact same loop structure is what a live bot uses; the
only difference live is that candles arrive once every interval instead of
all at once. That's why building it this way is good practice: the learning
transfers directly.

RUN IT:   python bot.py
============================================================================
"""

import datetime as dt
import datafeed
import strategy as strat
from risk import RiskManager
from execution import PaperBroker
from prefilter import PreFilter         # free Python gate (cost layer 1)
from model_tier import TieredDecider    # cheap->expensive escalation (cost layer 2/3)
from config import CONFIG


def run():
    cfg = CONFIG

    # SAFETY GATE: refuse to run if someone flips live_trading on, because
    # this build has no real-broker integration or the extra safeguards live
    # money demands.
    if cfg["live_trading"]:
        raise SystemExit(
            "Live trading is not implemented in this build, for your safety. "
            "Keep 'live_trading' = False."
        )

    # --- LAYER 1: data + indicators ---------------------------------------
    data = datafeed.get_data(cfg["symbol"], cfg["start"], cfg["end"], cfg["interval"])
    data = datafeed.add_indicators(data)

    # --- LAYER 2: turn data into signals ----------------------------------
    strategy_fn = strat.STRATEGIES[cfg["strategy"]]
    data = strategy_fn(data)

    # --- LAYER 3 + 4: set up risk rules and the paper broker --------------
    risk = RiskManager(
        risk_per_trade=cfg["risk_per_trade"],
        max_position_pct=cfg["max_position_pct"],
        stop_loss_pct=cfg["stop_loss_pct"],
        take_profit_pct=cfg["take_profit_pct"],
        max_daily_loss_pct=cfg["max_daily_loss_pct"],
    )
    broker = PaperBroker(
        starting_cash=cfg["starting_cash"],
        fee_pct=cfg["fee_pct"],
        slippage_pct=cfg["slippage_pct"],
    )

    # --- COST LAYERS: the free gate, then the cheap->expensive escalator ----
    gate = PreFilter()                          # decides IF we spend anything
    decider = TieredDecider(live_api=False)     # decides HOW MUCH we spend
    # Counters so we can report the savings at the end.
    cycles = 0
    api_skipped = 0

    print(f"\n[bot] Replaying {cfg['symbol']} with '{cfg['strategy']}' strategy...\n")

    day_start_value = broker.starting_cash
    halted_today = False

    # --- THE MAIN LOOP: one iteration per candle --------------------------
    # In a live bot this loop body runs once every `interval`; here we just
    # step through history fast.
    for timestamp, row in data.iterrows():
        price = row["Close"]
        position_signal = row["position"]   # 1 = want to hold, 0 = want cash

        # Skip early candles where indicators aren't ready yet (NaN values).
        if price != price:   # NaN check (NaN is the only value != itself)
            continue

        # 1) RISK FIRST: enforce stop-loss / take-profit on any open trade.
        broker.check_stops(price)

        # 2) Circuit breaker: if we've lost too much today, sit out.
        current_value = broker.equity(price)
        if not halted_today and risk.check_daily_limit(day_start_value, current_value):
            halted_today = True
            broker.sell(price, reason="DAILY-LIMIT-FLATTEN")  # go to cash for safety

        if halted_today:
            continue  # do nothing more today

        # --- COST GATE 1 (free): is anything worth a paid look? -------------
        # The prefilter runs in plain Python every cycle. If nothing
        # interesting changed, we skip the expensive decision entirely.
        cycles += 1
        holding = broker.position is not None
        worth_it, reasons = gate.should_call_api(row, holding)

        if not worth_it:
            api_skipped += 1
            # We still honour the deterministic strategy signal below — the
            # gate only governs the EXPENSIVE model call, not basic trading.
        else:
            # --- COST GATE 2/3: cheap triage, escalate only if needed -------
            # Build a small context dict of the indicators for the model.
            context = {
                "price": round(float(price), 2),
                "rsi": round(float(row["rsi"]), 1),
                "sma_fast": round(float(row["sma_fast"]), 2),
                "sma_slow": round(float(row["sma_slow"]), 2),
                "holding": holding,
                "triggers": reasons,
            }
            # In this build the model's opinion is logged but the actual trade
            # still follows the deterministic strategy (safe + free). To let
            # the model drive trades instead, use `verdict["action"]` below.
            verdict = decider.decide(context)
            # (verdict carries action/confidence/tier — wire in as you wish.)

        # 3) ACT ON THE SIGNAL.
        if position_signal == 1 and broker.position is None:
            # Strategy wants in and we're flat -> size and buy.
            sizing = risk.position_size(current_value, price)
            broker.buy(
                symbol=cfg["symbol"],
                price=price,
                cash_to_deploy=sizing["cash_to_deploy"],
                stop=risk.stop_loss_price(price),
                target=risk.take_profit_price(price),
                reason="SIGNAL-ENTER",
            )
        elif position_signal == 0 and broker.position is not None:
            # Strategy wants out -> sell.
            broker.sell(price, reason="SIGNAL-EXIT")

    # --- FINAL REPORT -----------------------------------------------------
    last_price = data["Close"].iloc[-1]
    final_value = broker.equity(last_price)
    ret_pct = (final_value / broker.starting_cash - 1) * 100

    print("\n" + "=" * 56)
    print(" PAPER-TRADING REPLAY COMPLETE")
    print("=" * 56)
    print(f" Symbol            : {cfg['symbol']}")
    print(f" Strategy          : {cfg['strategy']}")
    print(f" Starting cash     : ${broker.starting_cash:,.2f}")
    print(f" Final value       : ${final_value:,.2f}   ({ret_pct:+.1f}%)")
    print(f" Trades logged to  : paper_trades.db")
    print("=" * 56)
    # --- COST REPORT: show the savings the two gates produced -------------
    report = decider.cost_report()
    naive_calls = cycles                       # if we'd called the API every cycle
    expensive = report["expensive_calls"]
    print(" API COST SAVINGS")
    print(f"   Decision cycles            : {cycles}")
    print(f"   Skipped by prefilter (free): {api_skipped}  "
          f"({api_skipped/cycles*100:.0f}% saved before any call)" if cycles else "")
    print(f"   Cheap-model triage calls   : {report['cheap_calls']}")
    print(f"   Expensive-model calls      : {expensive}  "
          f"({expensive/naive_calls*100:.0f}% of the naive every-cycle count)"
          if naive_calls else "")
    print("=" * 56)
    print(" Remember: this is ONE strategy over ONE period. Test many before")
    print(" trusting anything. Not financial advice.")
    print("=" * 56 + "\n")


if __name__ == "__main__":
    run()
