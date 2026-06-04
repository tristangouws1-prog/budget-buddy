"""
============================================================================
 prefilter.py  —  THE API COST GATE
============================================================================
THE PROBLEM IT SOLVES:
The old bot called the (paid) Claude API every single cycle — e.g. every 15
minutes, around the clock. Most of those calls were wasted: if RSI is sitting
quietly at 50 and no moving average is anywhere near crossing, there is simply
nothing to decide. You paid for an answer of "do nothing", which you could
have worked out for free.

THE IDEA:
Put a cheap, deterministic gate in front of the expensive call. Every cycle we
run a few lines of plain Python (cost: nothing) that ask one question:

        "Has anything INTERESTING actually happened since last time?"

Only when the answer is YES do we spend money on an API call. The rest of the
time we skip it. In practice this removes the large majority of calls, because
markets spend most of their time NOT doing anything decision-worthy.

WHAT COUNTS AS "INTERESTING"?
We define a handful of trigger conditions. If ANY one fires, we call the API.
None of these involve predicting the future — they just detect that the
SITUATION changed enough to be worth a closer look:

  1. A moving-average crossover just happened (or is about to).
  2. RSI entered an extreme zone (oversold/overbought) it wasn't in before.
  3. A volatility spike (ATR jumped) — the market regime may be shifting.
  4. We currently HOLD a position (we must always re-check stops/targets).
  5. A 'heartbeat' — force one call every N cycles no matter what, so the bot
     never goes completely blind for too long.

This is a classic, sound engineering pattern: a cheap filter guarding an
expensive resource. Same reason a smoke alarm doesn't phone the fire brigade
every second — it watches cheaply and only calls when something trips.
============================================================================
"""


class PreFilter:
    def __init__(self,
                 rsi_oversold=30,
                 rsi_overbought=70,
                 ma_near_pct=0.005,      # "about to cross" = within 0.5%
                 atr_spike_mult=1.5,     # ATR jumping 1.5x its recent norm
                 heartbeat_cycles=96):   # force a call ~once/day on 15-min candles
        self.rsi_oversold     = rsi_oversold
        self.rsi_overbought   = rsi_overbought
        self.ma_near_pct      = ma_near_pct
        self.atr_spike_mult   = atr_spike_mult
        self.heartbeat_cycles = heartbeat_cycles

        # MEMORY: the gate has to remember the PREVIOUS state to detect CHANGE.
        # "RSI is below 30" isn't interesting on its own — "RSI just CROSSED
        # below 30 having been above it" is. So we store last cycle's readings.
        self._prev_ma_diff = None    # sign of (fast MA - slow MA) last cycle
        self._prev_rsi_zone = None   # which RSI zone we were in last cycle
        self._cycles_since_call = 0  # for the heartbeat

    def _rsi_zone(self, rsi):
        """Bucket RSI into a zone label so we can detect zone CHANGES."""
        if rsi <= self.rsi_oversold:
            return "oversold"
        if rsi >= self.rsi_overbought:
            return "overbought"
        return "neutral"

    def should_call_api(self, row, holding_position):
        """
        The gate. Returns (decision, reasons):
          decision : True  -> worth an API call this cycle
                     False -> skip, save the money
          reasons  : list of human-readable strings explaining WHY (great for
                     logging and for the dashboard).

        `row` is one candle's indicator data (a dict-like with sma_fast,
        sma_slow, rsi, atr, etc.). `holding_position` is True if we currently
        own the asset.
        """
        reasons = []
        self._cycles_since_call += 1

        # Pull the numbers we need out of this candle.
        ma_fast = row["sma_fast"]
        ma_slow = row["sma_slow"]
        rsi_val = row["rsi"]
        atr_val = row["atr"]

        # If indicators aren't ready yet (early candles = NaN), don't call.
        # NaN is the only value that is not equal to itself — handy check.
        if ma_fast != ma_fast or ma_slow != ma_slow or rsi_val != rsi_val:
            return False, ["indicators not ready"]

        # --- TRIGGER 1: moving-average crossover (happened or imminent) -----
        ma_diff = ma_fast - ma_slow
        ma_sign = 1 if ma_diff >= 0 else -1
        if self._prev_ma_diff is not None and ma_sign != self._prev_ma_diff:
            reasons.append("MA crossover occurred")
        else:
            # Not crossed yet — but are they ALMOST touching? If the gap is
            # tiny relative to price, a cross is likely soon, so wake up.
            gap_pct = abs(ma_diff) / ma_slow if ma_slow else 1
            if gap_pct < self.ma_near_pct:
                reasons.append("MAs nearly touching (cross imminent)")
        self._prev_ma_diff = ma_sign

        # --- TRIGGER 2: RSI changed zone (e.g. just became oversold) --------
        zone = self._rsi_zone(rsi_val)
        if self._prev_rsi_zone is not None and zone != self._prev_rsi_zone \
           and zone != "neutral":
            reasons.append(f"RSI entered {zone}")
        self._prev_rsi_zone = zone

        # --- TRIGGER 3: volatility spike ------------------------------------
        # If this candle's ATR is much bigger than 'atr_baseline' carried on
        # the row, the market just got jumpy — regime may be changing.
        baseline = row.get("atr_baseline", None) if hasattr(row, "get") else None
        if baseline and baseline == baseline and atr_val > baseline * self.atr_spike_mult:
            reasons.append("volatility spike")

        # --- TRIGGER 4: we hold a position ----------------------------------
        # Open trades must always be re-evaluated (stops/targets/exit), so an
        # open position is itself a reason to look closely.
        if holding_position:
            reasons.append("position open (must re-check)")

        # --- TRIGGER 5: heartbeat -------------------------------------------
        # Even in dead-quiet markets, force one call occasionally so the bot
        # never goes fully blind. Resets the counter when it fires.
        if self._cycles_since_call >= self.heartbeat_cycles:
            reasons.append("heartbeat")

        decision = len(reasons) > 0
        if decision:
            self._cycles_since_call = 0   # we're making a call; reset heartbeat
        return decision, reasons
