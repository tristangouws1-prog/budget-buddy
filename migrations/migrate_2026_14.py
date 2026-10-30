"""
One-off migration for categories and savings goals (late October 2026).
Back up instance/budget.db first, then run this BEFORE reloading the web app.
The new code reads payment.category, income.category and the goal table, so the
dashboard errors until they exist.
Safe to run twice, every step checks before changing anything.

    python migrations/migrate_2026_14.py

What it does
    - adds payment.category, so a bill can be filed under Groceries, Pets, Car
      and so on, and the Categories page can total each group
    - existing loans start in "Loans" and credit accounts in "Store Credit".
      Every other bill reads as "Other" until it's edited
    - adds income.category, so the Charts page can split income too
    - adds the goal table, for savings goals
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db, Goal


def columns(con, table):
    return [r[1] for r in con.exec_driver_sql(f'PRAGMA table_info("{table}")')]


def tables(con):
    return [r[0] for r in con.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")]


with app.app_context():
    engine = db.engine

    #bill categories
    with engine.begin() as con:
        #atomic step
        con.exec_driver_sql("BEGIN")
        if "category" in columns(con, "payment"):
            print("payment.category already there")
        else:
            con.exec_driver_sql("ALTER TABLE payment ADD COLUMN category VARCHAR(30)")
            loans = con.exec_driver_sql(
                "UPDATE payment SET category = 'Loans' WHERE bill_type = 'loan'").rowcount
            credit = con.exec_driver_sql(
                "UPDATE payment SET category = 'Store Credit' "
                "WHERE bill_type = 'credit'").rowcount
            print(f"added payment.category, {loans} loan(s) filed under Loans and "
                  f"{credit} credit account(s) under Store Credit")

    #income categories
    with engine.begin() as con:
        con.exec_driver_sql("BEGIN")
        if "category" in columns(con, "income"):
            print("income.category already there")
        else:
            con.exec_driver_sql("ALTER TABLE income ADD COLUMN category VARCHAR(30)")
            print("added income.category")

    #goal table
    with engine.begin() as con:
        if "goal" in tables(con):
            print("goal table already there")
        else:
            Goal.__table__.create(con)
            print("added the goal table")

    with engine.connect() as con:
        print("integrity check:", con.exec_driver_sql("PRAGMA integrity_check").scalar())
