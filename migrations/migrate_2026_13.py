"""
One-off migration for the growing house batch (B7, late October 2026).
Back up instance/budget.db first, then run this BEFORE reloading the web app.
The new code reads user.coins, so every page errors until the column exists.
Safe to run twice, every step checks before changing anything.

    python migrations/migrate_2026_13.py

What it does
    - adds user.coins, one purse shared by all of an account's buddies (#75).
      Any coins still on a buddy move into its owner's purse, so nothing is
      lost and nothing is counted twice
    - adds user.bill_sort, so the dashboard remembers its sort (#76)
    - adds buddy.in_room (#77). Up to four buddies per account that aren't
      out front move into the room, oldest first: the same ones it showed before
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db, ROOM_SPOTS


def columns(con, table):
    return [r[1] for r in con.exec_driver_sql(f'PRAGMA table_info("{table}")')]


with app.app_context():
    engine = db.engine

    #shared purse
    with engine.begin() as con:
        #all or nothing
        con.exec_driver_sql("BEGIN")
        if "coins" in columns(con, "user"):
            print("user.coins already there")
        else:
            con.exec_driver_sql(
                'ALTER TABLE "user" ADD COLUMN coins INTEGER NOT NULL DEFAULT 0')
            print("added user.coins")
        owned = 'user_id IN (SELECT id FROM "user")'
        moved = con.exec_driver_sql(
            f"SELECT COALESCE(SUM(coins), 0) FROM buddy WHERE {owned}").scalar()
        con.exec_driver_sql(
            'UPDATE "user" SET coins = coins + (SELECT COALESCE(SUM(coins), 0) '
            'FROM buddy WHERE buddy.user_id = "user".id)')
        con.exec_driver_sql(f"UPDATE buddy SET coins = 0 WHERE coins != 0 AND {owned}")
        print(f"moved {moved} coins from the buddies into their owners' purses")

    #remembered sort
    with engine.begin() as con:
        if "bill_sort" in columns(con, "user"):
            print("user.bill_sort already there")
        else:
            con.exec_driver_sql('ALTER TABLE "user" ADD COLUMN bill_sort VARCHAR(10)')
            print("added user.bill_sort")

    #room spots
    with engine.begin() as con:
        con.exec_driver_sql("BEGIN")
        if "in_room" in columns(con, "buddy"):
            print("buddy.in_room already there")
        else:
            con.exec_driver_sql(
                "ALTER TABLE buddy ADD COLUMN in_room BOOLEAN NOT NULL DEFAULT 0")
            placed = {}
            for buddy_id, user_id in con.exec_driver_sql(
                    "SELECT id, user_id FROM buddy WHERE NOT COALESCE(is_active, 0) "
                    "ORDER BY user_id, created_at, id").fetchall():
                if placed.get(user_id, 0) < ROOM_SPOTS:
                    con.exec_driver_sql("UPDATE buddy SET in_room = 1 WHERE id = ?",
                                        (buddy_id,))
                    placed[user_id] = placed.get(user_id, 0) + 1
            print(f"added buddy.in_room, {sum(placed.values())} housemate(s) moved "
                  f"into {len(placed)} room(s)")

    with engine.connect() as con:
        print("integrity check:", con.exec_driver_sql("PRAGMA integrity_check").scalar())
