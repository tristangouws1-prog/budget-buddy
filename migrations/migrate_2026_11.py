"""
One-off migration for the October 2026 batch.
Safe to run twice, every step checks before changing anything.

    python migrations/migrate_2026_11.py

What it does
    - adds user.tag and numbers every account per name, David#0001,
      David#0002, oldest account first (#49)
    - makes username + tag unique, so no two accounts share a handle
    - creates the new job_run and personal_reminder tables
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa

from app import app, db

with app.app_context():
    engine = db.engine
    inspector = sa.inspect(engine)
    tables = inspector.get_table_names()

    with engine.begin() as con:
        if "user" in tables:
            cols = [c["name"] for c in inspector.get_columns("user")]
            if "tag" not in cols:
                con.exec_driver_sql('ALTER TABLE "user" ADD COLUMN tag INTEGER')
                print("added user.tag")
            else:
                print("user.tag already there")

            #untagged accounts, after any tags already given
            rows = con.exec_driver_sql(
                'SELECT id, username, tag FROM "user" ORDER BY id').fetchall()
            top = {}
            for _, name, tag in rows:
                if tag is not None:
                    top[name] = max(top.get(name, 0), tag)
            given = 0
            for uid, name, tag in rows:
                if tag is None:
                    top[name] = top.get(name, 0) + 1
                    con.exec_driver_sql('UPDATE "user" SET tag = ? WHERE id = ?',
                                        (top[name], uid))
                    given += 1
            print(f"tagged {given} account(s)")

            con.exec_driver_sql('CREATE UNIQUE INDEX IF NOT EXISTS '
                                'uq_user_username_tag ON "user" (username, tag)')
            print("username + tag is unique")

    #job_run, personal_reminder
    db.create_all()
    print("create_all done")

    with engine.connect() as con:
        ok = con.exec_driver_sql("PRAGMA integrity_check").scalar()
    print("integrity check:", ok)
