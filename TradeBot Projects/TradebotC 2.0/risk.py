"""
============================================================================
 risk.py  —  LAYER 3: THE RISK LAYER
============================================================================
This is the layer that keeps you in the game. A mediocre strategy with good
risk control survives a losing streak; a brilliant strategy with no risk
control blows up the first time it's wrong several times in a row (and it
WILL be wrong several times in a row — that's normal).

The whole philosophy here is one sentence:
    "Decide how much you're willing to LOSE before you think about what you
     might gain."

This class is a set of rules that the execution layer must obey. It answers
three questions:
  1. HOW MUCH should I buy?            -> position_size()
  2. WHEN am I forced to sell a loser? -> stop-loss / take-profit (checked in execution)
  3. WHEN must the whole bot STOP?     -> check_daily_limit()
============================================================================
"""


class RiskManager:
    def __init__(self,
                 risk_per_trade=0.01,     # risk at most 1% of account per trade
                 max_position_pct=0.25,   # never put >25% of account in one position
                 stop_loss_pct=0.03,      # sell if a position falls 3%
                 take_profit_pct=0.06,    # take profit if it rises 6%
                 max_daily_loss_pct=0.05):# halt the bot if we lose 5% in a day
        # Storing the rules as attributes so every other part of the bot can
        # read them. These defaults are deliberately conservative.
        self.risk_per_trade     = risk_per_trade
        self.max_position_pct   = max_position_pct
        self.stop_loss_pct      = stop_loss_pct
        self.take_profit_pct    = take_profit_pct
        self.max_daily_loss_pct = max_daily_loss_pct

    def position_size(self, account_value, price, atr_value=None):
        """
        Work out how much money (and how many units) to put into a trade.

        We use the '1% rule': risk only `risk_per_trade` of the account on the
        distance to our stop-loss. If the trade goes wrong and hits the stop,
        we lose roughly that 1% — no more. This is how you survive being wrong
        many times: each individual mistake is small.

        Returns a dict with the cash to deploy and the number of units to buy.
        """
        # The dollar amount we're willing to lose if the stop-loss triggers.
        dollars_at_risk = account_value * self.risk_per_trade

        # How far (in %) is our stop from entry? That's our per-unit risk.
        stop_distance_pct = self.stop_loss_pct

        # Position value such that a `stop_distance_pct` drop loses exactly
        # `dollars_at_risk`.  e.g. risk $10, stop 3% -> position = $10/0.03 = $333
        position_value = dollars_at_risk / stop_distance_pct

        # SAFETY CAP: never let one position exceed max_position_pct of account,
        # no matter what the formula says. Concentration is how accounts die.
        cap = account_value * self.max_position_pct
        position_value = min(position_value, cap)

        # Can't deploy more cash than we actually have.
        position_value = min(position_value, account_value)

        units = position_value / price
        return {
            "cash_to_deploy": position_value,
            "units": units,
            "dollars_at_risk": dollars_at_risk,
        }

    def stop_loss_price(self, entry_price):
        """The price at which a losing position must be sold."""
        return entry_price * (1 - self.stop_loss_pct)

    def take_profit_price(self, entry_price):
        """The price at which we lock in a winning position."""
        return entry_price * (1 + self.take_profit_pct)

    def check_daily_limit(self, day_start_value, current_value):
        """
        Circuit breaker. If today's losses exceed max_daily_loss_pct, return
        True meaning 'STOP TRADING for today'. This stops a bad day (or a bug,
        or a flash crash) from turning into a catastrophic one. Every serious
        trading system has a kill switch like this.
        """
        loss_pct = (day_start_value - current_value) / day_start_value
        if loss_pct >= self.max_daily_loss_pct:
            print(f"[risk] DAILY LOSS LIMIT HIT ({loss_pct:.1%}). Halting for today.")
            return True
        return False
