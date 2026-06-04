"""
============================================================================
 config.py  —  ALL YOUR SETTINGS IN ONE PLACE
============================================================================
Keeping every knob here (instead of scattered through the code) means you can
tune the bot without touching the logic. This is good practice on every
project, not just trading bots.
============================================================================
"""

CONFIG = {
    # --- What to trade -----------------------------------------------------
    # Works for stocks ("AAPL"), crypto ("BTC-USD"), or forex ("EURUSD=X").
    "symbol": "BTC-USD",
    "start":  "2022-01-01",
    "end":    "2024-01-01",
    "interval": "1d",          # "1d" daily, "1h" hourly, etc.

    # --- Which strategy ----------------------------------------------------
    # Options: "ma_crossover", "rsi_meanreversion", "buy_and_hold"
    "strategy": "ma_crossover",

    # --- Starting money & trading costs ------------------------------------
    "starting_cash": 1000.0,
    "fee_pct":       0.001,    # 0.1% per trade
    "slippage_pct":  0.0005,   # 0.05% price slippage

    # --- Risk rules (see risk.py for what each means) ----------------------
    "risk_per_trade":     0.01,   # risk 1% of account per trade
    "max_position_pct":   0.25,   # max 25% of account in one position
    "stop_loss_pct":      0.03,   # cut losers at -3%
    "take_profit_pct":    0.06,   # take winners at +6%
    "max_daily_loss_pct": 0.05,   # halt for the day after -5%

    # --- SAFETY ------------------------------------------------------------
    # Live trading is intentionally NOT wired up. This bot only paper-trades.
    "live_trading": False,        # leave False. Going live needs real broker
                                  # API keys and a lot more safety work.
}
