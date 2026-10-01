"""
Money is now stored as whole cents, not floating point.
Run once, after pulling. Safe to run twice.

    python migrations/migrate_2026_10.py

Floats can't hold decimal money exactly, which is why 208.35 // 41.67 came
out as 4 instead of 5. Every money column becomes an INTEGER of cents:
R41.67 is stored as 4167. Percentages stay as they were.

Each table is rebuilt rather than altered in place, because older SQLite
builds can't drop or retype a column.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa

from app import app, db

#table -> the columns whose rands become cents
MONEY = {
    "user":        ["budget_limit"],
    "payment":     ["amount", "amount_paid", "total_value", "current_balance",
                    "loan_insurance", "initiation_fee", "service_fee",
                    "carried_over"],
    "income":      ["amount"],
    "payment_log": ["amount_paid"],
}


def columns(con, table):
    return [r[1] for r in con.exec_driver_sql(f"PRAGMA table_info({table})")]


with app.app_context():
    inspector = sa.inspect(db.engine)
    present = inspector.get_table_names()

    with db.engine.begin() as con:
        con.exec_driver_sql("PRAGMA foreign_keys=OFF")

        todo = []
        for table, fields in MONEY.items():
            if table not in present:
                print(f"skip {table}: no such table yet")
                continue
            cols = columns(con, table)
            if all(f"{f}_cents" in cols for f in fields):
                print(f"skip {table}: already in cents")
                continue
            todo.append((table, fields, cols))

        for table, fields, old_cols in todo:
            #the rename takes the indexes with it, and their names would
            #then clash when the fresh table is created, so drop them first
            for (idx,) in con.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name=? AND sql IS NOT NULL", (table,)).fetchall():
                con.exec_driver_sql(f"DROP INDEX IF EXISTS {idx}")
            con.exec_driver_sql(f"ALTER TABLE {table} RENAME TO {table}_old")
            print(f"     {table} -> {table}_old")

    if todo:
        #rebuild the renamed tables from the models, now declaring *_cents
        db.create_all()

        with db.engine.begin() as con:
            con.exec_driver_sql("PRAGMA foreign_keys=OFF")
            for table, fields, old_cols in todo:
                new_cols = columns(con, table)
                picks = []
                for col in new_cols:
                    base = col[:-6] if col.endswith("_cents") else col
                    if col.endswith("_cents") and base in fields:
                        #rands -> cents, rounded so 41.67 lands on 4167 exactly
                        picks.append(f"CAST(ROUND(COALESCE({base}, 0) * 100) AS INTEGER)")
                    elif col in old_cols:
                        picks.append(col)
                    else:
                        picks.append("NULL")
                con.exec_driver_sql(
                    f"INSERT INTO {table} ({', '.join(new_cols)}) "
                    f"SELECT {', '.join(picks)} FROM {table}_old")
                moved = con.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar()
                con.exec_driver_sql(f"DROP TABLE {table}_old")
                print(f"ok   {table}: {moved} rows converted to cents")

    db.create_all()

    with db.engine.connect() as con:
        print("integrity check:", con.exec_driver_sql("PRAGMA integrity_check").scalar())
