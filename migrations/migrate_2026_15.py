"""
One-off migration for bill and income descriptions (November 2026).
Back up instance/budget.db first, then run this BEFORE reloading the web app.
The new code reads income.description, so the dashboard errors until it exists.
Safe to run twice, every step checks before changing anything.

    python migrations/migrate_2026_15.py

What it does
    - adds income.description, for notes like who pays it or its reference
    - payment.description has been there since the start, so it's only
      checked. Old descriptions saved before the box was taken off the
      form show up again under "Details" on the dashboard
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db


def columns(con, table):
    return [r[1] for r in con.exec_driver_sql(f'PRAGMA table_info("{table}")')]


with app.app_context():
    engine = db.engine

    #bill descriptions
    with engine.begin() as con:
        con.exec_driver_sql("BEGIN")
        if "description" in columns(con, "payment"):
            kept = con.exec_driver_sql(
                "SELECT COUNT(*) FROM payment WHERE description IS NOT NULL "
                "AND TRIM(description) != ''").scalar()
            print(f"payment.description already there, {kept} bill(s) already have one")
        else:
            con.exec_driver_sql("ALTER TABLE payment ADD COLUMN description VARCHAR(500)")
            print("added payment.description")

    #income descriptions
    with engine.begin() as con:
        con.exec_driver_sql("BEGIN")
        if "description" in columns(con, "income"):
            print("income.description already there")
        else:
            con.exec_driver_sql("ALTER TABLE income ADD COLUMN description VARCHAR(500)")
            print("added income.description")

    with engine.connect() as con:
        print("integrity check:", con.exec_driver_sql("PRAGMA integrity_check").scalar())
