"""
============================================================================
 test_bot.py  —  AUTOMATED SAFETY TESTS
============================================================================
WHY THIS FILE EXISTS:
The bot now has many moving parts. When you tweak one (say, change how
position sizing works), it's easy to silently break another. These tests are
a safety net: they encode the RULES THAT MUST ALWAYS HOLD, and they fail
loudly the moment a change violates one. Run them after every edit.

HOW TO RUN:
    pytest test_bot.py -v
    (or just `pytest` to run everything)

Each test checks ONE invariant and has a comment saying what could go wrong if
it failed. They focus on the safety-critical stuff — the rules that, if broken,
would cost real money: position sizing, stops, the daily kill-switch, the
prefilter's logic, and the look-ahead-bias guard.

The tests use small hand-made data so the expected answers are obvious — no
network, no randomness, fast to run.
============================================================================
"""

import numpy as np
import pandas as pd
import pytest

from risk import RiskManager
from execution import PaperBroker
from prefilter import PreFilter
import strategy as strat
import datafeed


# ===========================================================================
# RISK LAYER — the rules that keep the account alive.
# ===========================================================================

def test_position_size_never_exceeds_cap():
    """
    INVARIANT: a single position must never exceed max_position_pct of the
    account. If this broke, one trade could put the whole account at risk —
    exactly the concentration that blows accounts up.
    """
    risk = RiskManager(risk_per_trade=0.50,      # deliberately huge...
                       max_position_pct=0.25,    # ...but the cap is 25%
                       stop_loss_pct=0.03)
    account = 1000.0
    sizing = risk.position_size(account, price=100.0)
    # Even though risk_per_trade is absurdly high, the cap must clamp it.
    assert sizing["cash_to_deploy"] <= account * 0.25 + 1e-9


def test_position_size_respects_risk_per_trade():
    """
    INVARIANT: with a sane config, the cash deployed should match the 1% rule:
    risk_amount / stop_distance. If this broke, your real risk per trade would
    silently differ from what you configured.
    """
    risk = RiskManager(risk_per_trade=0.01, max_position_pct=0.99, stop_loss_pct=0.03)
    account = 1000.0
    sizing = risk.position_size(account, price=50.0)
    expected = (account * 0.01) / 0.03      # $10 risk / 3% stop = $333.33
    assert sizing["cash_to_deploy"] == pytest.approx(expected, rel=1e-6)


def test_position_size_never_exceeds_account():
    """
    INVARIANT: you can never deploy more cash than you actually have, no matter
    what the formula spits out. Spending money you don't have = a bug that would
    desync the simulation (or, live, get orders rejected).
    """
    risk = RiskManager(risk_per_trade=0.99, max_position_pct=10.0, stop_loss_pct=0.01)
    account = 1000.0
    sizing = risk.position_size(account, price=100.0)
    assert sizing["cash_to_deploy"] <= account + 1e-9


def test_stop_and_target_prices_are_correct():
    """
    INVARIANT: stop sits below entry by stop_loss_pct, target sits above by
    take_profit_pct. If these inverted, you'd cut winners and ride losers — the
    exact opposite of the intended risk control.
    """
    risk = RiskManager(stop_loss_pct=0.03, take_profit_pct=0.06)
    entry = 100.0
    assert risk.stop_loss_price(entry) == pytest.approx(97.0)
    assert risk.take_profit_price(entry) == pytest.approx(106.0)
    # Sanity: stop must be below target, always.
    assert risk.stop_loss_price(entry) < risk.take_profit_price(entry)


def test_daily_limit_triggers_at_threshold():
    """
    INVARIANT: the kill-switch fires once losses reach max_daily_loss_pct, and
    NOT before. A broken kill-switch is how a bad day becomes a catastrophic one.
    """
    risk = RiskManager(max_daily_loss_pct=0.05)
    # 4% loss -> should NOT halt yet.
    assert risk.check_daily_limit(day_start_value=1000, current_value=960) is False
    # 5% loss -> should halt.
    assert risk.check_daily_limit(day_start_value=1000, current_value=950) is True
    # 6% loss -> should halt.
    assert risk.check_daily_limit(day_start_value=1000, current_value=940) is True


# ===========================================================================
# EXECUTION LAYER — fills, fees, stops.
# ===========================================================================

def test_buy_then_sell_deducts_fees(tmp_path):
    """
    INVARIANT: a round trip at the SAME price must LOSE money, because fees and
    slippage both cost something. If a flat round-trip broke even or profited,
    costs aren't being applied — and your backtests would be fantasy.
    """
    db = str(tmp_path / "t.db")        # temp db so tests don't touch real data
    broker = PaperBroker(starting_cash=1000.0, fee_pct=0.001,
                         slippage_pct=0.0005, db_path=db)
    broker.buy("X", price=100.0, cash_to_deploy=500.0,
               stop=97.0, target=106.0, reason="t")
    broker.sell(price=100.0, reason="t")     # sell at the same price
    assert broker.position is None           # position closed
    assert broker.equity(100.0) < 1000.0     # ...and we're down by the costs


def test_check_stops_sells_at_stop_loss(tmp_path):
    """
    INVARIANT: when price falls to/below the stop, the position must be closed.
    If stops didn't fire, losers would run unbounded.
    """
    db = str(tmp_path / "t.db")
    broker = PaperBroker(starting_cash=1000.0, db_path=db)
    broker.buy("X", price=100.0, cash_to_deploy=500.0,
               stop=97.0, target=106.0, reason="t")
    broker.check_stops(current_price=96.0)   # below the 97 stop
    assert broker.position is None           # must have sold


def test_check_stops_sells_at_take_profit(tmp_path):
    """INVARIANT: at/above the target, the winner must be locked in."""
    db = str(tmp_path / "t.db")
    broker = PaperBroker(starting_cash=1000.0, db_path=db)
    broker.buy("X", price=100.0, cash_to_deploy=500.0,
               stop=97.0, target=106.0, reason="t")
    broker.check_stops(current_price=107.0)  # above the 106 target
    assert broker.position is None


def test_cannot_hold_two_positions_at_once(tmp_path):
    """
    INVARIANT: this simple broker holds ONE position at a time; a second buy
    while already in a trade must be ignored. If it weren't, cash accounting
    and risk caps would both break.
    """
    db = str(tmp_path / "t.db")
    broker = PaperBroker(starting_cash=1000.0, db_path=db)
    broker.buy("X", price=100.0, cash_to_deploy=300.0, stop=97, target=106, reason="t")
    first = broker.position
    broker.buy("Y", price=50.0, cash_to_deploy=300.0, stop=48, target=53, reason="t")
    # The second buy should NOT have replaced the first.
    assert broker.position is first


# ===========================================================================
# STRATEGY LAYER — and the critical look-ahead-bias guard.
# ===========================================================================

def _toy_frame(closes):
    """Build a minimal candle frame from a list of closing prices."""
    closes = np.array(closes, dtype=float)
    idx = pd.date_range("2022-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Open": closes, "High": closes * 1.01,
                         "Low": closes * 0.99, "Close": closes,
                         "Volume": 1000}, index=idx)


def test_no_lookahead_position_is_shifted():
    """
    INVARIANT (the big one): the 'position' we act on must be the signal from
    the PREVIOUS candle, never the current one. Acting on the current candle's
    signal means trading on information you wouldn't have had in real time —
    'look-ahead bias' — which fabricates impossibly good results.

    We check that position[t] equals signal[t-1].
    """
    df = _toy_frame(list(range(1, 80)))      # steadily rising prices
    out = strat.ma_crossover(datafeed.add_indicators(df))
    # position must equal signal shifted forward by one row.
    shifted = out["signal"].shift(1).fillna(0)
    assert (out["position"] == shifted).all()


def test_strategy_registry_has_expected_entries():
    """INVARIANT: the strategy registry exposes the names config.py relies on."""
    for name in ["ma_crossover", "rsi_meanreversion", "buy_and_hold"]:
        assert name in strat.STRATEGIES
        assert callable(strat.STRATEGIES[name])


def test_buy_and_hold_is_always_invested():
    """
    INVARIANT: buy_and_hold's signal is always 1 (always in the market). It's
    our benchmark; if it weren't fully invested, comparisons against it would
    be meaningless.
    """
    df = _toy_frame(list(range(1, 30)))
    out = strat.buy_and_hold(df)
    assert (out["signal"] == 1).all()


# ===========================================================================
# PREFILTER — the free cost gate.
# ===========================================================================

def test_prefilter_skips_when_indicators_not_ready():
    """
    INVARIANT: with NaN indicators (early candles), the gate must NOT call the
    API. Calling on garbage data wastes money and risks bad decisions.
    """
    gate = PreFilter()
    row = {"sma_fast": float("nan"), "sma_slow": float("nan"),
           "rsi": float("nan"), "atr": float("nan")}
    decision, reasons = gate.should_call_api(row, holding_position=False)
    assert decision is False


def test_prefilter_always_calls_when_holding():
    """
    INVARIANT: if we hold a position, the gate must always say 'look' — open
    trades need their stops/targets re-checked every cycle. Skipping them could
    let a stop go unhonoured.
    """
    gate = PreFilter()
    row = {"sma_fast": 100.0, "sma_slow": 101.0, "rsi": 50.0,
           "atr": 1.0, "atr_baseline": 1.0}
    decision, reasons = gate.should_call_api(row, holding_position=True)
    assert decision is True
    assert any("position open" in r for r in reasons)


def test_prefilter_detects_rsi_zone_change():
    """
    INVARIANT: moving from neutral into an extreme RSI zone is an 'interesting'
    event that should wake the gate. If it didn't, the bot could sleep through
    the very setups the strategy cares about.
    """
    gate = PreFilter()
    base = {"sma_fast": 100.0, "sma_slow": 100.5, "atr": 1.0, "atr_baseline": 1.0}
    # First call: neutral RSI establishes the baseline zone.
    gate.should_call_api({**base, "rsi": 50.0}, holding_position=False)
    # Second call: RSI drops into oversold — should be flagged.
    decision, reasons = gate.should_call_api({**base, "rsi": 25.0}, holding_position=False)
    assert decision is True
    assert any("oversold" in r for r in reasons)


if __name__ == "__main__":
    # Allow `python test_bot.py` as a convenience; pytest is the normal path.
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
