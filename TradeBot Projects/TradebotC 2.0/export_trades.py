"""
============================================================================
 export_trades.py  —  bridge between the bot's database(s) and the dashboard
============================================================================
The bots log trades to SQLite files:
   - bot.py          -> paper_trades.db
   - multi_runner.py -> paper_trades_multi.db
The dashboard is just an HTML file and can't read SQLite directly, so this
script dumps whichever databases exist to JSON files the dashboard loads:
   - trades.json            (single-symbol run)
   - trades_multi.json      (watchlist run)

RUN:  python export_trades.py   (after running bot.py and/or multi_runner.py)
============================================================================
"""

import sqlite3
import json
import os


def export(db_path, json_path):
    """Dump one trades database to one JSON file, if the database exists."""
    if not os.path.exists(db_path):
        print(f"  (skip) {db_path} not found — run the matching bot first.")
        return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row          # read rows as dictionaries
    rows = conn.execute("SELECT * FROM trades ORDER BY id ASC").fetchall()
    trades = [dict(r) for r in rows]
    with open(json_path, "w") as f:
        json.dump(trades, f, indent=2)
    print(f"  Exported {len(trades)} trades -> {json_path}")
    conn.close()


if __name__ == "__main__":
    print("Exporting trade logs for the dashboard...")
    export("paper_trades.db", "trades.json")
    export("paper_trades_multi.db", "trades_multi.json")
    print("Done.")
