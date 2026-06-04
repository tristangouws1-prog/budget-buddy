"""
============================================================================
 datafeed.py  —  LAYER 1: THE DATA LAYER
============================================================================
This module is the bot's senses. Its only job is to fetch price data and
compute indicators. It does NOT decide anything. Keeping data SEPARATE from
decisions is the single most important design habit — it means you can test
each piece on its own.

ONE SOURCE, THREE ASSET CLASSES:
We use yfinance (Yahoo Finance) for everything, because the same code can
fetch stocks, forex, AND crypto just by changing the symbol:
    Stocks : "AAPL", "MSFT", "TSLA"
    Crypto : "BTC-USD", "ETH-USD"
    Forex  : "EURUSD=X", "GBPUSD=X"

(For LIVE crypto trading you'd later swap in ccxt to talk to an exchange like
Binance, but for data + learning, one source keeps life simple.)

An INDICATOR is a number calculated from price that tries to summarise some
aspect of the market — momentum, trend, volatility — into something a rule
can act on. We compute a few classics below, each explained inline.
============================================================================
"""

import pandas as pd
import numpy as np
import yfinance as yf


def get_data(symbol, start, end, interval="1d"):
    """
    Fetch historical 'candles' (Open/High/Low/Close/Volume per time bucket).

    Returns a pandas DataFrame indexed by date, or raises ValueError if the
    download failed (bad symbol, no connection, etc.).
    """
    print(f"[datafeed] Fetching {symbol} ({start} -> {end}, {interval})...")
    data = yf.download(symbol, start=start, end=end,
                       interval=interval, auto_adjust=True, progress=False)

    # yfinance sometimes returns a 2-level column header; flatten it so the
    # rest of the code can simply use data["Close"].
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    if data.empty:
        raise ValueError(f"No data for '{symbol}'. Check the symbol and dates.")

    print(f"[datafeed]   got {len(data)} candles.")
    return data


# ---------------------------------------------------------------------------
# INDICATORS
# Each function takes a price Series (a column of numbers) and returns a new
# Series of the same length. We write them by hand (no library) so you can SEE
# exactly what each one is — there is no magic.
# ---------------------------------------------------------------------------

def sma(series, window):
    """
    Simple Moving Average: the plain average of the last `window` prices.
    Smooths out noise to reveal the underlying trend direction.
    """
    return series.rolling(window=window).mean()


def ema(series, window):
    """
    Exponential Moving Average: like SMA but weights RECENT prices more
    heavily, so it reacts faster to new moves.
    """
    return series.ewm(span=window, adjust=False).mean()


def rsi(series, window=14):
    """
    Relative Strength Index (0-100): a momentum gauge.
      - Above 70 is often called 'overbought' (price may have risen too fast).
      - Below 30 is often called 'oversold'  (price may have fallen too fast).
    It compares the size of recent gains to recent losses.
    """
    delta = series.diff()                       # price change each candle
    gain = delta.clip(lower=0)                  # keep only the up-moves
    loss = -delta.clip(upper=0)                 # keep only the down-moves (as +)
    avg_gain = gain.rolling(window).mean()      # average gain over the window
    avg_loss = loss.rolling(window).mean()      # average loss over the window
    rs = avg_gain / avg_loss                    # relative strength ratio
    return 100 - (100 / (1 + rs))               # squash into a 0-100 scale


def atr(high, low, close, window=14):
    """
    Average True Range: a VOLATILITY measure (how much price typically moves
    per candle, in price units). We use this later for position sizing and
    stop-losses — bigger volatility means we risk less and set wider stops.
    """
    # 'True range' = the largest of three measures of this candle's movement.
    prev_close = close.shift(1)
    range1 = high - low                         # today's high-to-low
    range2 = (high - prev_close).abs()          # gap up from yesterday's close
    range3 = (low - prev_close).abs()           # gap down from yesterday's close
    true_range = pd.concat([range1, range2, range3], axis=1).max(axis=1)
    return true_range.rolling(window).mean()


def add_indicators(data):
    """
    Take raw candles and bolt on a standard set of indicator columns.
    The strategy layer will read these columns to make decisions.
    """
    df = data.copy()
    df["sma_fast"] = sma(df["Close"], 20)       # fast trend line (20 candles)
    df["sma_slow"] = sma(df["Close"], 50)       # slow trend line (50 candles)
    df["ema_fast"] = ema(df["Close"], 12)
    df["ema_slow"] = ema(df["Close"], 26)
    df["rsi"]      = rsi(df["Close"], 14)
    df["atr"]      = atr(df["High"], df["Low"], df["Close"], 14)
    # A slower average of ATR, used by the pre-filter as the "normal" level of
    # volatility to compare against when detecting spikes.
    df["atr_baseline"] = df["atr"].rolling(50).mean()
    return df


# Self-test demo when run directly.
if __name__ == "__main__":
    d = get_data("AAPL", "2023-01-01", "2024-01-01")
    d = add_indicators(d)
    print(d[["Close", "sma_fast", "sma_slow", "rsi", "atr"]].tail())
