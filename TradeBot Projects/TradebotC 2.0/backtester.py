"""
============================================================================
 backtester.py  —  A learning-focused backtesting engine
============================================================================

WHAT IS A "BACKTEST"?
---------------------
A backtest takes a trading STRATEGY (a set of rules for when to buy and sell)
and runs it against REAL HISTORICAL PRICE DATA from the past. It pretends to
trade through that history and tells you: "If you had followed these rules,
how would your money have done?"

WHY THIS IS THE MOST IMPORTANT TOOL TO BUILD FIRST:
---------------------------------------------------
It is the honest mirror. Almost every strategy that "looks" like it should
print money turns out to lose money (or barely break even) once you include
two real-world costs:
   1. FEES      - the broker/exchange takes a cut on every trade.
   2. SLIPPAGE  - you rarely get the exact price you wanted; the market moves.
This engine includes BOTH, on purpose, so the numbers you see are realistic
instead of fantasy.

WORKS FOR ALL THREE ASSET CLASSES:
   - Stocks      e.g. "AAPL", "MSFT"
   - Crypto      e.g. "BTC-USD", "ETH-USD"
   - Forex       e.g. "EURUSD=X", "GBPUSD=X"
All of these symbols come from Yahoo Finance via the free `yfinance` library.

HOW TO READ THIS FILE:
Each section is heavily commented. Read top to bottom like a story.
============================================================================
"""

# --- IMPORTS: external code libraries we are borrowing ---------------------
import pandas as pd          # pandas = tables of data (think Excel in Python)
import numpy as np           # numpy = fast maths on lists of numbers
import yfinance as yf        # yfinance = downloads free historical price data


# ===========================================================================
# STEP 1: GET THE DATA
# ===========================================================================
def download_data(symbol, start, end, interval="1d"):
    """
    Download historical price 'candles' for one symbol.

    A 'candle' is one time-bucket of price info. A daily candle for AAPL
    tells you the Open, High, Low, Close price and Volume for that whole day.

    Parameters (the inputs to this function):
      symbol   : str  - what to trade, e.g. "BTC-USD"
      start    : str  - start date, e.g. "2022-01-01"
      end      : str  - end date,   e.g. "2024-01-01"
      interval : str  - candle size: "1d" (daily), "1h" (hourly), etc.

    Returns:
      A pandas DataFrame (a table) with columns: Open, High, Low, Close, Volume
      and one row per candle, indexed by date/time.
    """
    print(f"Downloading {symbol} data from {start} to {end}...")

    # yf.download does the heavy lifting of fetching from Yahoo Finance.
    # auto_adjust=True adjusts old prices for stock splits/dividends so the
    # numbers are comparable across time.
    data = yf.download(symbol, start=start, end=end,
                       interval=interval, auto_adjust=True, progress=False)

    # Sometimes yfinance returns columns as a "MultiIndex" (a 2-level header).
    # This line flattens it back to simple column names so the rest of our
    # code can just say data["Close"] without surprises.
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    # If we got nothing back (bad symbol, no internet, etc.) stop early with a
    # clear error message instead of crashing mysteriously later.
    if data.empty:
        raise ValueError(f"No data returned for '{symbol}'. Check the symbol/dates.")

    print(f"  Got {len(data)} candles.")
    return data


# ===========================================================================
# STEP 2: DEFINE A STRATEGY (the buy/sell rules)
# ===========================================================================
#
# A strategy's only job is to look at the price history and output a SIGNAL
# for each candle:
#       +1  = we want to be HOLDING the asset (we're "long")
#        0  = we want to be in CASH (out of the market)
#
# We will write one classic, simple, well-known strategy as our example:
# the "Moving Average Crossover".
#
# A "moving average" (MA) is just the average closing price over the last N
# candles. It smooths out the jiggle so you can see the underlying trend.
#   - A SHORT MA (e.g. 20 candles) reacts quickly to recent price.
#   - A LONG  MA (e.g. 50 candles) reacts slowly and shows the big trend.
#
# THE RULE:
#   When the short MA crosses ABOVE the long MA  -> trend turning up  -> BUY
#   When the short MA crosses BELOW the long MA  -> trend turning down -> SELL
#
# This is intentionally simple. It is NOT a money-printer (you'll see!).
# It's here so you can watch how a real strategy gets evaluated.
# ===========================================================================
def moving_average_strategy(data, short_window=20, long_window=50):
    """
    Add a 'signal' column to the data: +1 = hold asset, 0 = hold cash.
    """
    # .copy() makes our own copy so we don't accidentally modify the original.
    df = data.copy()

    # .rolling(window=N).mean() computes the average of the last N closes.
    df["ma_short"] = df["Close"].rolling(window=short_window).mean()
    df["ma_long"]  = df["Close"].rolling(window=long_window).mean()

    # np.where(condition, value_if_true, value_if_false):
    #   If short MA is above long MA -> signal 1 (be in the market)
    #   Otherwise                    -> signal 0 (be in cash)
    df["signal"] = np.where(df["ma_short"] > df["ma_long"], 1, 0)

    # IMPORTANT REALISM RULE: we can only ACT on a signal on the NEXT candle.
    # We see today's closing price, decide tonight, and trade tomorrow.
    # .shift(1) moves every signal down one row to model this delay.
    # Forgetting this is the #1 way beginners accidentally "cheat" and get
    # fake amazing results (it's called look-ahead bias).
    df["position"] = df["signal"].shift(1).fillna(0)

    return df


# ===========================================================================
# STEP 3: RUN THE BACKTEST (simulate the trades + apply real-world costs)
# ===========================================================================
def run_backtest(df, starting_cash=1000.0, fee_pct=0.001, slippage_pct=0.0005):
    """
    Walk through history candle-by-candle and track how the money grows/shrinks.

    Parameters:
      starting_cash : how much money we pretend to start with.
      fee_pct       : broker/exchange fee per trade. 0.001 = 0.1% (typical crypto).
      slippage_pct  : price you actually get vs. wanted. 0.0005 = 0.05%.

    Returns the table with an 'equity' column = our total money over time.
    """
    df = df.copy()

    # The percentage change in price from one candle to the next.
    # e.g. price 100 -> 102 gives a return of 0.02 (i.e. +2%).
    df["price_return"] = df["Close"].pct_change().fillna(0)

    # OUR return each candle = the asset's return, but ONLY when we were
    # holding it (position == 1). When in cash (position == 0), we earn 0.
    df["strategy_return"] = df["position"] * df["price_return"]

    # --- Now subtract the costs of trading ---------------------------------
    # A "trade" happens whenever our position CHANGES (0->1 buy, or 1->0 sell).
    # .diff() gives the change in position; .abs() makes it positive so both
    # buying and selling count as one trade event.
    df["trade"] = df["position"].diff().abs().fillna(0)

    # Each trade costs us fee + slippage, charged on the candle it happens.
    cost_per_trade = fee_pct + slippage_pct
    df["costs"] = df["trade"] * cost_per_trade

    # Net return = what we made from price moves MINUS what trading cost us.
    df["net_return"] = df["strategy_return"] - df["costs"]

    # --- Turn returns into an actual money balance over time ---------------
    # (1 + net_return) is the growth factor each candle. .cumprod() multiplies
    # them all together cumulatively, so we get a compounding equity curve.
    df["equity"] = starting_cash * (1 + df["net_return"]).cumprod()

    # For comparison: "buy and hold" = just buy on day 1 and never trade.
    # If our clever strategy can't beat this, the strategy isn't worth it!
    df["buy_hold"] = starting_cash * (1 + df["price_return"]).cumprod()

    return df


# ===========================================================================
# STEP 4: REPORT THE RESULTS (the honest scorecard)
# ===========================================================================
def summarize(df, starting_cash=1000.0):
    """
    Print the key performance numbers and return them as a dictionary.
    """
    final_equity   = df["equity"].iloc[-1]      # money at the end (strategy)
    final_buy_hold = df["buy_hold"].iloc[-1]    # money at the end (buy & hold)

    # Total return as a percentage: (end - start) / start * 100
    strat_return    = (final_equity   / starting_cash - 1) * 100
    buyhold_return  = (final_buy_hold / starting_cash - 1) * 100

    num_trades = int(df["trade"].sum())

    # --- WIN RATE: of the candles we held, how often did we make money? -----
    held = df[df["position"] == 1]
    wins = (held["price_return"] > 0).sum()
    win_rate = (wins / len(held) * 100) if len(held) > 0 else 0

    # --- MAX DRAWDOWN: the worst peak-to-bottom drop we suffered ------------
    # This matters HUGELY. A strategy that gains 50% but at one point dropped
    # 40% is terrifying to actually hold. .cummax() tracks the highest equity
    # seen so far; the drawdown is how far below that high we fell.
    running_peak = df["equity"].cummax()
    drawdown = (df["equity"] - running_peak) / running_peak
    max_drawdown = drawdown.min() * 100  # most negative value = worst drop

    # Print a clean report.
    print("\n" + "=" * 56)
    print(" BACKTEST RESULTS")
    print("=" * 56)
    print(f" Starting cash        : ${starting_cash:,.2f}")
    print(f" Final (strategy)     : ${final_equity:,.2f}   ({strat_return:+.1f}%)")
    print(f" Final (buy & hold)   : ${final_buy_hold:,.2f}   ({buyhold_return:+.1f}%)")
    print(f" Number of trades     : {num_trades}")
    print(f" Win rate (per candle): {win_rate:.1f}%")
    print(f" Max drawdown         : {max_drawdown:.1f}%  (worst drop suffered)")
    print("=" * 56)

    # The honest verdict.
    if strat_return > buyhold_return:
        print(" Verdict: strategy BEAT buy & hold over this period.")
    else:
        print(" Verdict: strategy LOST to simply buying and holding.")
    print("   (One winning period does NOT mean it works in general —")
    print("    test many symbols and many time ranges before trusting it.)")
    print("=" * 56 + "\n")

    return {
        "final_equity": final_equity,
        "strategy_return_pct": strat_return,
        "buyhold_return_pct": buyhold_return,
        "num_trades": num_trades,
        "win_rate_pct": win_rate,
        "max_drawdown_pct": max_drawdown,
    }


# ===========================================================================
# STEP 5: TIE IT ALL TOGETHER
# ===========================================================================
def backtest(symbol, start, end, interval="1d",
             short_window=20, long_window=50,
             starting_cash=1000.0, fee_pct=0.001, slippage_pct=0.0005):
    """
    The one function you call to run a full backtest end-to-end.
    It downloads data, applies the strategy, simulates, and prints results.
    """
    data    = download_data(symbol, start, end, interval)
    with_signals = moving_average_strategy(data, short_window, long_window)
    result  = run_backtest(with_signals, starting_cash, fee_pct, slippage_pct)
    summarize(result, starting_cash)
    return result


# This block only runs when you execute "python backtester.py" directly.
# It's a built-in demo so you can see the engine work immediately.
if __name__ == "__main__":
    # Try the same asset from the old bot: Bitcoin, over 2 years of daily data.
    backtest(
        symbol="BTC-USD",
        start="2022-01-01",
        end="2024-01-01",
        short_window=20,
        long_window=50,
        starting_cash=1000.0,
    )
