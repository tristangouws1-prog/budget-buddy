"""
============================================================================
 execution.py  —  LAYER 4: THE EXECUTION LAYER
============================================================================
This layer turns decisions into (pretend) trades. It is where the strategy
signal, the risk rules, and real-world costs all meet.

THE GOLDEN RULE OF THIS FILE: it runs in PAPER mode. No real money. It
simulates buying and selling with a fake cash balance so you can watch the
bot behave for a long time before risking a cent. Going live should be a
deliberate, scary, one-line decision you make only after weeks of good paper
results AND honest backtests — never a default.

It records every trade to a small SQLite database so the monitoring layer
(and you) can review exactly what happened and why.
============================================================================
"""

import sqlite3
from datetime import datetime, timezone


class PaperBroker:
    """A simulated broker holding fake cash and (at most) one position."""

    def __init__(self, starting_cash=1000.0,
                 fee_pct=0.001, slippage_pct=0.0005,
                 db_path="paper_trades.db"):
        self.cash = starting_cash            # uninvested cash
        self.starting_cash = starting_cash
        self.fee_pct = fee_pct               # cost per trade (0.1%)
        self.slippage_pct = slippage_pct     # price we get vs price we wanted

        # Current open position (None when we're flat / all in cash).
        self.position = None                 # will hold a dict when we own something

        self._init_db(db_path)

    def _init_db(self, db_path):
        """Create the trades log table if it doesn't exist yet."""
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                side TEXT,            -- 'BUY' or 'SELL'
                price REAL,
                units REAL,
                cash_after REAL,
                reason TEXT          -- why the trade happened (signal/stop/etc.)
            )
        """)
        self.conn.commit()

    def _log(self, symbol, side, price, units, reason):
        """Write one trade row to the database."""
        self.conn.execute(
            "INSERT INTO trades (timestamp, symbol, side, price, units, cash_after, reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), symbol, side, price, units, self.cash, reason)
        )
        self.conn.commit()

    def _fill_price(self, price, side):
        """
        Model slippage: when BUYING you tend to pay slightly MORE than the
        quoted price; when SELLING you get slightly LESS. This makes the
        simulation pessimistic-but-realistic instead of fantasy.
        """
        if side == "BUY":
            return price * (1 + self.slippage_pct)
        else:
            return price * (1 - self.slippage_pct)

    def buy(self, symbol, price, cash_to_deploy, stop, target, reason=""):
        """Open a position, deducting fees. Refuses if we already hold one."""
        if self.position is not None:
            return  # already in a trade; this simple bot holds one at a time

        fill = self._fill_price(price, "BUY")
        fee = cash_to_deploy * self.fee_pct
        spend = cash_to_deploy + fee
        if spend > self.cash:
            return  # not enough cash; skip

        units = cash_to_deploy / fill
        self.cash -= spend
        self.position = {
            "symbol": symbol, "units": units, "entry": fill,
            "stop": stop, "target": target,
        }
        self._log(symbol, "BUY", fill, units, reason)
        print(f"[exec] BUY  {units:.6f} {symbol} @ {fill:.2f}  ({reason})")

    def sell(self, price, reason=""):
        """Close the open position, deducting fees."""
        if self.position is None:
            return  # nothing to sell

        fill = self._fill_price(price, "SELL")
        proceeds = self.position["units"] * fill
        fee = proceeds * self.fee_pct
        self.cash += proceeds - fee
        self._log(self.position["symbol"], "SELL", fill,
                  self.position["units"], reason)
        print(f"[exec] SELL {self.position['units']:.6f} "
              f"{self.position['symbol']} @ {fill:.2f}  ({reason})")
        self.position = None

    def check_stops(self, current_price):
        """
        Before acting on any new signal, see if the open position has hit its
        stop-loss (cut the loss) or take-profit (lock the gain). This is the
        risk layer's rules being ENFORCED, candle by candle.
        """
        if self.position is None:
            return
        if current_price <= self.position["stop"]:
            self.sell(current_price, reason="STOP-LOSS")
        elif current_price >= self.position["target"]:
            self.sell(current_price, reason="TAKE-PROFIT")

    def equity(self, current_price):
        """Total account value = cash + current worth of any open position."""
        held = (self.position["units"] * current_price) if self.position else 0
        return self.cash + held
