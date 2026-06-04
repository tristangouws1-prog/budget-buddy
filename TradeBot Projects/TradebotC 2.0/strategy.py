"""
============================================================================
 strategy.py  —  LAYER 2: THE STRATEGY LAYER
============================================================================
A strategy's ONLY job: look at the indicator columns and output a target
'signal' for each candle:
        1  = we WANT to be holding the asset (bullish / "long")
        0  = we WANT to be in cash (out of the market)

Notice what a strategy does NOT do:
  - It does not size positions     (that's the RISK layer)
  - It does not place orders        (that's the EXECUTION layer)
  - It does not know about fees      (that's the BACKTEST/EXECUTION layer)
Keeping it this narrow lets you swap strategies in and out like Lego bricks,
and test each one in isolation.

A WORD OF HONESTY: none of these strategies is a money-printer. They are
well-known, simple, public ideas. If a simple rule reliably beat the market,
it would have been arbitraged away long ago. Their value here is LEARNING —
run them through the backtester and watch how they really behave.

Each strategy is a function: takes a DataFrame with indicators, returns the
same DataFrame plus a 'signal' and a 'position' column.

The 'position' column is 'signal' shifted forward by one candle. This models
the unavoidable real-world delay: you see a candle CLOSE, decide, then can
only trade on the NEXT candle. Skipping this shift is "look-ahead bias" — the
classic beginner mistake that produces fake, too-good-to-be-true results.
============================================================================
"""

import numpy as np


def _finalize(df):
    """Shared helper: turn a raw 'signal' into a tradeable 'position'."""
    # .shift(1) = act on next candle (no peeking into the future).
    df["position"] = df["signal"].shift(1).fillna(0)
    return df


def ma_crossover(data):
    """
    TREND-FOLLOWING strategy.
    Be long when the fast moving average is above the slow one (uptrend),
    in cash otherwise. Profits from sustained trends; gets chopped up in
    sideways markets (lots of small losing trades).
    """
    df = data.copy()
    df["signal"] = np.where(df["sma_fast"] > df["sma_slow"], 1, 0)
    return _finalize(df)


def rsi_meanreversion(data, oversold=30, overbought=70):
    """
    MEAN-REVERSION strategy.
    Buy when RSI dips below `oversold` (betting the dip bounces back), and
    exit when RSI climbs above `overbought`. The opposite philosophy to
    trend-following: it assumes price snaps back to average. Works in
    range-bound markets, gets steamrolled in strong trends.
    """
    df = data.copy()
    # Build the signal candle-by-candle because today's position depends on
    # yesterday's (we hold until the exit condition triggers).
    signal = []
    holding = 0
    for rsi_value in df["rsi"]:
        if np.isnan(rsi_value):
            signal.append(0)                    # not enough data yet
        elif rsi_value < oversold:
            holding = 1                          # oversold -> enter
            signal.append(1)
        elif rsi_value > overbought:
            holding = 0                          # overbought -> exit
            signal.append(0)
        else:
            signal.append(holding)               # in between -> keep current
    df["signal"] = signal
    return _finalize(df)


def buy_and_hold(data):
    """
    The BENCHMARK. Buy on day one, never sell. Any 'clever' strategy that
    can't beat this simple approach (after fees!) is not worth running.
    Always compare against this.
    """
    df = data.copy()
    df["signal"] = 1
    return _finalize(df)


# A registry so other files can pick a strategy by name (e.g. from config).
STRATEGIES = {
    "ma_crossover": ma_crossover,
    "rsi_meanreversion": rsi_meanreversion,
    "buy_and_hold": buy_and_hold,
}
