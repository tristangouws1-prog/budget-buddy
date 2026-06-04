# Multi-Asset Paper Trading Bot

A learning-focused, **paper-trading-only** bot for stocks, forex, and crypto.
Heavily commented for beginners. **No real money is ever traded by this build.**

## Files

| Group | File | Job |
|-------|------|-----|
| 1. Data | `datafeed.py` | Fetch prices (stocks/forex/crypto) + compute indicators |
| 2. Strategy | `strategy.py` | Turn data into BUY/SELL/HOLD signals |
| 3. Risk | `risk.py` | Position sizing, stop-loss, take-profit, daily kill-switch |
| 4. Execution | `execution.py` | Simulate trades, apply fees + slippage, log to SQLite |
| 5. Monitoring | `dashboard.html` | Visualise trades + the combined watchlist |
| Orchestrator | `bot.py` | Runs the layers together over one symbol |
| Cost gate | `prefilter.py` | Free Python check: only call the API when something changed |
| Cost gate | `model_tier.py` | Cheap-model triage, escalate to expensive model only when needed; prompt caching |
| Watchlist | `multi_runner.py` | Runs the whole pipeline across many symbols at once |
| Settings | `config.py` | Every tunable knob in one place |
| Backtester | `backtester.py` | Standalone honest strategy tester |
| Walk-forward | `walkforward.py` | Out-of-sample test that exposes overfitting |
| Tests | `test_bot.py` | Pytest suite covering safety-critical invariants |
| Helper | `export_trades.py` | Dumps the trade database to JSON for the dashboard |

## Setup

```
pip install yfinance pandas numpy ccxt anthropic
```

## Run it

Single symbol:
```
python bot.py              # paper-trades over history, writes paper_trades.db
python export_trades.py    # dumps the trades to trades.json
# then open dashboard.html in your browser
```

Whole watchlist:
```
python multi_runner.py     # runs the pipeline across several symbols
```

## Try different assets

Edit `symbol` in `config.py` (or the `watchlist` in `multi_runner.py`):
- Stocks: `"AAPL"`, `"MSFT"`, `"TSLA"`
- Crypto: `"BTC-USD"`, `"ETH-USD"`
- Forex:  `"EURUSD=X"`, `"GBPUSD=X"`

And try different strategies: `"ma_crossover"`, `"rsi_meanreversion"`, `"buy_and_hold"`.

## The cost architecture (how the API bill is kept low)

Three gates stack, cheapest first, so the expensive model only sees the few
genuinely hard decisions:

1. **`prefilter.py`** — free Python. Skips the API entirely unless an indicator
   actually changed state (a crossover, an RSI zone change, a volatility spike,
   an open position to re-check, or a periodic heartbeat). Removes ~80% of calls.
2. **`model_tier.py`** — a cheap model triages what gets through; only unsure or
   significant cases escalate to the expensive model. Cuts expensive calls further.
3. **Prompt caching** (inside `model_tier.py`) — the fixed instruction block is
   cached and reused across calls, so you stop paying to reprocess it every time.
   Reduces cost *per* call (the other two reduce the *number* of calls).

Across a 4-symbol watchlist these compounded to expensive-model calls being
roughly **6% of the naive "call every symbol every cycle" count**.

## Testing & validation

Run the safety tests after any change:
```
pytest test_bot.py -v
```
These check the rules that must always hold: position size never exceeds the
cap, stops/targets fire correctly, the daily kill-switch triggers at the right
threshold, the prefilter skips/wakes correctly, and — most important — that
there's no look-ahead bias (you only ever act on the previous candle's signal).

Check a strategy for overfitting with walk-forward analysis:
```
python walkforward.py
```
It tunes parameters on each in-sample window, then tests those exact parameters
on the *next, unseen* window. If in-sample returns look great but out-of-sample
returns are weak, that gap is overfitting — the reason most strategies that
backtest beautifully fail live.

## The honest part

No strategy here (or anywhere) reliably prints money — markets are too
efficient for simple public rules to beat consistently. The cost work above
makes the bot **cheaper to run, not better at predicting.** The real value of
this project is **learning how trading systems are structured and why most
strategies fail once you include fees, slippage, and bad luck.** Always compare
against buy-and-hold, test across many symbols and time periods, and keep
`live_trading = False`.

This is educational software, not financial advice.
