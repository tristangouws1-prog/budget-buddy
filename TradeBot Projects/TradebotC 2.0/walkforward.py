"""
============================================================================
 walkforward.py  —  THE ANTI-OVERFITTING TEST
============================================================================
THE PROBLEM IT EXISTS TO CATCH:
A normal backtest TUNES a strategy's parameters and TESTS them on the SAME
stretch of history. That's like writing an exam after seeing the answer key:
of course the score looks great. The strategy has just MEMORISED the quirks of
that specific period (this is called "overfitting"), and it falls apart the
moment it meets data it hasn't seen. This is THE reason "amazing backtest,
loses money live" happens.

WHAT WALK-FORWARD DOES INSTEAD:
It never lets the strategy be tested on data it was tuned on. It slides a pair
of windows across history:

   |---- in-sample (tune here) ----|---- out-of-sample (test here) ----|
                                    ^ then slide both forward and repeat

   Window 1:  tune on Jan-Jun         test on Jul-Sep
   Window 2:  tune on Apr-Sep         test on Oct-Dec
   Window 3:  tune on Jul-Dec         test on Jan-Mar (next year)
   ... and so on.

For EACH window we:
  1. Search parameter combinations on the IN-SAMPLE slice and keep the best.
  2. Apply THOSE EXACT parameters to the OUT-OF-SAMPLE slice (unseen data).
  3. Record only the out-of-sample result.

Then we stitch all the out-of-sample results together. THAT stitched record is
the honest estimate of real-world performance, because at every step the
strategy was judged on data it had never touched during tuning.

HOW TO READ THE RESULT:
If the in-sample returns look fantastic but the out-of-sample returns are weak
or negative, that gap IS the overfitting, laid bare. A robust strategy shows
out-of-sample results that are at least in the same ballpark as in-sample. Most
simple strategies do NOT — and seeing that for yourself is the whole point.
============================================================================
"""

import numpy as np
import pandas as pd

# Reuse the strategy + simulation logic we already wrote and trust.
from backtester import moving_average_strategy, run_backtest


def _total_return(df, starting_cash=1000.0):
    """Helper: percentage return of an equity curve produced by run_backtest."""
    final = df["equity"].iloc[-1]
    return (final / starting_cash - 1) * 100


def optimize_on_slice(data_slice, param_grid, starting_cash=1000.0):
    """
    The 'tuning' step — run ONLY on in-sample data.

    Try every combination of parameters in `param_grid` and return the one with
    the best total return on this slice. (A simple grid search: exhaustive but
    easy to understand. The point isn't a clever optimiser — it's that whatever
    we pick here, we then judge on UNSEEN data.)

    param_grid example: {"short_window": [10, 20], "long_window": [50, 100]}
    """
    best_params = None
    best_return = -np.inf

    # Build every (short, long) combination from the grid.
    for short_w in param_grid["short_window"]:
        for long_w in param_grid["long_window"]:
            # A short window >= long window isn't a valid crossover setup; skip.
            if short_w >= long_w:
                continue
            signaled = moving_average_strategy(data_slice, short_w, long_w)
            result = run_backtest(signaled, starting_cash)
            ret = _total_return(result, starting_cash)
            if ret > best_return:
                best_return = ret
                best_params = {"short_window": short_w, "long_window": long_w}

    return best_params, best_return


def walk_forward(data, param_grid,
                 in_sample_len=180, out_sample_len=60,
                 starting_cash=1000.0):
    """
    Run the full walk-forward analysis over `data` (a DataFrame of candles).

    in_sample_len  : how many candles to TUNE on each window.
    out_sample_len : how many candles to TEST on (unseen) each window.

    Returns a dict with per-window records and the stitched out-of-sample
    summary. Prints a clear comparison so the overfitting gap is obvious.
    """
    windows = []
    start = 0
    n = len(data)

    # Slide the window pair across all available history.
    while start + in_sample_len + out_sample_len <= n:
        in_slice  = data.iloc[start : start + in_sample_len]
        out_slice = data.iloc[start + in_sample_len :
                              start + in_sample_len + out_sample_len]

        # 1) TUNE on in-sample only.
        best_params, in_return = optimize_on_slice(in_slice, param_grid, starting_cash)
        if best_params is None:
            start += out_sample_len
            continue

        # 2) APPLY those exact params to the unseen out-of-sample slice.
        out_signaled = moving_average_strategy(
            out_slice, best_params["short_window"], best_params["long_window"])
        out_result = run_backtest(out_signaled, starting_cash)
        out_return = _total_return(out_result, starting_cash)

        windows.append({
            "in_start": str(data.index[start].date()) if hasattr(data.index[start], "date") else start,
            "params": best_params,
            "in_sample_return": in_return,
            "out_sample_return": out_return,
        })

        # Slide forward by one out-of-sample length (non-overlapping tests).
        start += out_sample_len

    return _report(windows)


def _report(windows):
    """Print the window-by-window table and the honest stitched verdict."""
    if not windows:
        print("[walkforward] Not enough data for even one window. "
              "Use a longer date range or smaller window lengths.")
        return {"windows": [], "avg_in": None, "avg_out": None}

    print("\n" + "=" * 68)
    print(" WALK-FORWARD ANALYSIS  (out-of-sample = the honest number)")
    print("=" * 68)
    print(f" {'Window start':<14}{'Params':<22}{'In-sample':>12}{'Out-sample':>14}")
    print(" " + "-" * 66)
    for w in windows:
        p = f"{w['params']['short_window']}/{w['params']['long_window']}"
        print(f" {str(w['in_start']):<14}{p:<22}"
              f"{w['in_sample_return']:>10.1f}%{w['out_sample_return']:>13.1f}%")

    avg_in  = float(np.mean([w["in_sample_return"]  for w in windows]))
    avg_out = float(np.mean([w["out_sample_return"] for w in windows]))

    print(" " + "-" * 66)
    print(f" {'AVERAGE':<14}{'':<22}{avg_in:>10.1f}%{avg_out:>13.1f}%")
    print("=" * 68)

    # The honest interpretation.
    gap = avg_in - avg_out
    print(f" In-sample averaged {avg_in:+.1f}%, out-of-sample {avg_out:+.1f}%.")
    if avg_out <= 0:
        print(" VERDICT: out-of-sample is flat or negative. The in-sample gains")
        print("          were largely overfitting — this would likely lose live.")
    elif gap > abs(avg_out):
        print(" VERDICT: a big in/out gap. Some real signal, but heavily inflated")
        print("          by overfitting. Treat in-sample numbers with suspicion.")
    else:
        print(" VERDICT: out-of-sample holds up reasonably vs in-sample. That's")
        print("          the (rare) sign of a strategy that isn't just memorising.")
    print(" Reminder: this is still ONE asset. Repeat across many. Not advice.")
    print("=" * 68 + "\n")

    return {"windows": windows, "avg_in": avg_in, "avg_out": avg_out}


if __name__ == "__main__":
    # Demo on real data when run directly (needs internet for yfinance).
    from backtester import download_data
    data = download_data("BTC-USD", "2021-01-01", "2024-01-01")
    grid = {"short_window": [10, 20, 30], "long_window": [50, 100, 150]}
    walk_forward(data, grid, in_sample_len=180, out_sample_len=60)
