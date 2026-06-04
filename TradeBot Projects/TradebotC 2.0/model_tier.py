"""
============================================================================
 model_tier.py  —  MODEL TIERING (spend big only when it's worth it)
============================================================================
THE IDEA, IN ONE LINE:
Not every decision deserves the most expensive model. Use a CHEAP, FAST model
for routine triage, and only ESCALATE to the expensive model when the cheap
one says "this is genuinely ambiguous / important."

WHERE THIS SITS IN THE PIPELINE:
   prefilter  ->  is anything interesting happening at all?  (free Python)
        |  yes
        v
   model_tier ->  CHEAP model: quick read of the situation       (Haiku)
        |  only if the cheap model is unsure / sees something big
        v
                  EXPENSIVE model: careful reasoning             (a bigger model)

So we now have THREE gates, each cheaper than the next, filtering down to the
few moments that truly justify top-tier spend:
   1. prefilter   = $0        (plain Python)
   2. cheap model = ~cheap    (fast triage)
   3. expensive   = $$$       (rare, only when escalated)

THE COST MATH (why this works):
Say the prefilter already cut you to 18% of cycles. Of those, maybe only ~20%
are truly ambiguous enough to need the big model. The rest are handled by the
cheap model at a fraction of the price. Stacked on top of the prefilter, your
spend on the expensive model can drop by ~95%+ versus calling it every cycle.

IMPORTANT HONESTY NOTE:
This module is about COST, not accuracy. A cheap model triaging and a big
model reasoning does NOT give you a crystal ball — it just spends money
smartly. The deterministic Python logic in strategy.py is still free and, for
pure indicator math, often just as good. The models earn their keep mainly
when you feed them messier inputs (news, sentiment, multi-signal conflicts).

NOTE ON RUNNING THIS:
The actual API call is wrapped in `_call_api()` and is left as a clearly
marked stub by default, so this code runs and is testable WITHOUT spending
money or needing keys. Flip `live_api=True` and fill in the call to go live.
============================================================================
"""

import json


# Model names are kept here so you change them in one place. Use whatever the
# current cheap/expensive models are when you actually wire this up.
CHEAP_MODEL     = "claude-haiku-4-5"     # fast, inexpensive — for triage
EXPENSIVE_MODEL = "claude-opus-4-8"      # slow, pricey — for hard calls


class TieredDecider:
    def __init__(self, live_api=False, escalate_confidence=0.75):
        # live_api=False keeps everything as a safe, free stub you can test.
        self.live_api = live_api
        # If the cheap model's confidence is BELOW this, we escalate to the
        # expensive model. High-confidence cheap answers are trusted as-is.
        self.escalate_confidence = escalate_confidence

        # Bookkeeping so you can SEE where the money would go.
        self.cheap_calls = 0
        self.expensive_calls = 0

    # ----------------------------------------------------------------------
    # PROMPTS — split into a STATIC part (cacheable) and a DYNAMIC part.
    #
    # Prompt caching only works on a fixed PREFIX: the leading chunk of the
    # prompt must be byte-for-byte identical on every call so the API can
    # reuse its cached processing of it. So we keep all the unchanging
    # instructions in `_system_*` (sent as a cached system block) and put ONLY
    # the per-cycle indicator data in the user message. Big stable block +
    # tiny changing tail = ideal caching shape.
    # ----------------------------------------------------------------------

    # STATIC: the cheap model's instructions. Identical on every call -> cached.
    _SYSTEM_TRIAGE = (
        "You are a fast trading triage assistant. Given indicator data, reply "
        "ONLY with JSON: {\"action\":\"BUY|SELL|HOLD\",\"confidence\":0.0-1.0,"
        "\"needs_deep_look\":true|false}. Set needs_deep_look=true if signals "
        "conflict or the move looks significant. No prose, JSON only."
    )

    # STATIC: the expensive model's instructions. Identical on every call.
    _SYSTEM_DEEP = (
        "You are a careful trading analyst. Weigh the indicator data, note "
        "conflicts and risks, and reply ONLY with JSON: {\"action\":"
        "\"BUY|SELL|HOLD\",\"confidence\":0.0-1.0,\"reasoning\":\"...\"}. "
        "No prose outside the JSON."
    )

    def _user_payload(self, context):
        """DYNAMIC: just this cycle's data. Small, changes every call, NOT cached."""
        return f"Indicators: {json.dumps(context)}"

    # ----------------------------------------------------------------------
    # The API call itself — STUBBED by default so this is free to run/test.
    # ----------------------------------------------------------------------
    def _call_api(self, model, system_block, user_payload):
        """
        Make one model call and return a parsed dict.

        `system_block` is the STATIC, cacheable instructions.
        `user_payload` is the DYNAMIC, per-cycle data.

        DEFAULT: returns a deterministic fake answer so the whole pipeline
        runs with zero cost and no API key. Set live_api=True and use the live
        path below to actually call the API (with prompt caching enabled).
        """
        if not self.live_api:
            # --- STUB: pretend response so the demo runs free. --------------
            if model == CHEAP_MODEL:
                pseudo = (hash(user_payload) % 100) / 100.0
                confidence = 0.85 if pseudo > 0.25 else 0.55
                return {"action": "HOLD", "confidence": confidence,
                        "needs_deep_look": confidence < self.escalate_confidence}
            else:
                return {"action": "HOLD", "confidence": 0.9, "reasoning": "stub"}

        # --- LIVE PATH with PROMPT CACHING --------------------------------
        # The key detail is `cache_control` on the system block. It marks the
        # END of a cacheable prefix: everything up to and including it is
        # stored and reused on later calls (within the cache window, ~5 min,
        # refreshed on each hit). The user message is NOT cached because it
        # changes every call. First call = small write premium; every call
        # after = big discount on the cached portion.
        import anthropic
        client = anthropic.Anthropic()        # reads ANTHROPIC_API_KEY from env
        resp = client.messages.create(
            model=model,
            max_tokens=300,
            system=[
                {
                    "type": "text",
                    "text": system_block,
                    "cache_control": {"type": "ephemeral"},   # <-- cache this prefix
                }
            ],
            messages=[{"role": "user", "content": user_payload}],
        )
        # resp.usage shows cache_creation_input_tokens (the write, first time)
        # and cache_read_input_tokens (the cheap reads, every time after).
        text = resp.content[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Defensive: if the model adds stray text, fail safe to HOLD.
            return {"action": "HOLD", "confidence": 0.0, "reasoning": "parse error"}

    # ----------------------------------------------------------------------
    # The main entry point the bot calls.
    # ----------------------------------------------------------------------
    def decide(self, context):
        """
        Run cheap triage first; escalate to the expensive model only if the
        cheap model is unsure OR explicitly flags it needs a deeper look.

        Returns the final decision dict plus which tier produced it.
        """
        payload = self._user_payload(context)

        # 1) Always start cheap. Static instructions are cached across calls.
        self.cheap_calls += 1
        triage = self._call_api(CHEAP_MODEL, self._SYSTEM_TRIAGE, payload)

        unsure = triage.get("confidence", 0) < self.escalate_confidence
        flagged = triage.get("needs_deep_look", False)

        # 2) Escalate only when warranted.
        if unsure or flagged:
            self.expensive_calls += 1
            deep = self._call_api(EXPENSIVE_MODEL, self._SYSTEM_DEEP, payload)
            deep["tier"] = "expensive"
            return deep

        # 3) Otherwise trust the cheap answer — most of the time we stop here.
        triage["tier"] = "cheap"
        return triage

    def cost_report(self):
        """A quick summary of where calls went, for logging/dashboard."""
        total = self.cheap_calls + self.expensive_calls
        return {
            "cheap_calls": self.cheap_calls,
            "expensive_calls": self.expensive_calls,
            "expensive_share_pct": (self.expensive_calls / total * 100) if total else 0,
        }
