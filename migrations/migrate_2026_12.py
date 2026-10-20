"""
One-off migration for the mid-October 2026 batch.
Back up instance/budget.db first, and reload the web app afterwards.
Safe to run twice, every step checks before changing anything.

    python migrations/migrate_2026_12.py

What it does
    - once-off bills can't be weekly any more. Any saved as weekly become
      monthly, and the carry-over the month-end gave them is cleared, since
      a once-off never carries over
    - prints each bill it changes: its due day was a weekday (1-7) and now
      reads as a day of the month, so check it and whether it's paid
    - rebuilds the payment table so a deleted bill's id is never handed out
      again. SQLite reused the newest id, so a new bill picked up the old
      one's payments, reminders and xp. The next id also skips past any id
      the history, reminders or xp still point at
    - lists any accounts whose emails differ only in capitals, which now
      count as the same address, to sort out by hand
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa
from sqlalchemy.schema import CreateTable

from app import app, db, Payment

with app.app_context():
    stuck = Payment.query.filter_by(bill_type="once_off", frequency="weekly").all()
    for p in stuck:
        carried = p.carried_over_cents or 0
        p.frequency = "monthly"
        p.carried_over_cents = 0
        print(f"'{p.name}' (id {p.id}, user {p.user_id}): now monthly, due day "
              f"{p.due_day}, cleared {carried / 100:.2f} carried over")
    db.session.commit()
    db.session.remove()
    print(f"fixed {len(stuck)} once-off bill(s)")

    engine = db.engine
    tables = sa.inspect(engine).get_table_names()
    with engine.begin() as con:
        ddl = con.exec_driver_sql("SELECT sql FROM sqlite_master "
                                  "WHERE type='table' AND name='payment'").scalar() or ""
        if "AUTOINCREMENT" in ddl.upper():
            print("payment ids are already never reused")
        else:
            #swap in
            new = str(CreateTable(Payment.__table__).compile(engine)).strip()
            assert new.startswith("CREATE TABLE payment ("), new[:40]
            con.exec_driver_sql(new.replace("CREATE TABLE payment (",
                                            "CREATE TABLE payment_new (", 1))
            old_cols = [r[1] for r in con.exec_driver_sql("PRAGMA table_info(payment)")]
            cols = ", ".join(r[1] for r in con.exec_driver_sql("PRAGMA table_info(payment_new)")
                             if r[1] in old_cols)
            con.exec_driver_sql(f"INSERT INTO payment_new ({cols}) SELECT {cols} FROM payment")
            moved = con.exec_driver_sql("SELECT COUNT(*) FROM payment_new").scalar()
            con.exec_driver_sql("DROP TABLE payment")
            con.exec_driver_sql("ALTER TABLE payment_new RENAME TO payment")
            for index in Payment.__table__.indexes:
                index.create(bind=con, checkfirst=True)
            print(f"payment rebuilt, {moved} bills kept, ids never reused from now on")

        #highest referenced
        top = con.exec_driver_sql("SELECT COALESCE(MAX(id), 0) FROM payment").scalar()
        for table in ("payment_log", "reminder"):
            if table in tables:
                top = max(top, con.exec_driver_sql(
                    f"SELECT COALESCE(MAX(payment_id), 0) FROM {table}").scalar())
        if "xp_event" in tables:
            for (key,) in con.exec_driver_sql("SELECT ref_key FROM xp_event"):
                found = re.match(r"(?:bill|pay|loanweek|balance|carry):(\d+)", key or "")
                if found:
                    top = max(top, int(found.group(1)))
        seq = con.exec_driver_sql("SELECT seq FROM sqlite_sequence "
                                  "WHERE name='payment'").scalar() or 0
        if top > seq:
            con.exec_driver_sql("DELETE FROM sqlite_sequence WHERE name='payment'")
            con.exec_driver_sql("INSERT INTO sqlite_sequence (name, seq) VALUES ('payment', ?)",
                                (top,))
        print(f"the next bill id will be above {max(top, seq)}")

        for email, count in con.exec_driver_sql(
                'SELECT LOWER(email), COUNT(*) FROM "user" GROUP BY LOWER(email) '
                "HAVING COUNT(*) > 1"):
            print(f"note: {count} accounts use {email} with different capitals")

    with engine.connect() as con:
        print("integrity check:", con.exec_driver_sql("PRAGMA integrity_check").scalar())
