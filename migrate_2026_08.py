"""
One-off migration for the August 2026 batch.
Safe to run twice, every step checks before changing anything.

    python migrate_2026_08.py

What it does
    - adds reminder.payment_id, so paying a bill can tick off its reminders
    - adds payment.is_archived, for finished once-off bills
    - rebuilds the buddy table to allow several buddies per user and add
      is_active (SQLite can't drop a UNIQUE rule, so the table is copied)
    - creates any tables that don't exist yet
"""
import sqlalchemy as sa

from app import app, db

with app.app_context():
    engine = db.engine
    inspector = sa.inspect(engine)
    tables = inspector.get_table_names()

    with engine.begin() as con:
        #paying a bill ticks off its reminders
        if "reminder" in tables:
            cols = [c["name"] for c in inspector.get_columns("reminder")]
            if "payment_id" not in cols:
                con.exec_driver_sql("ALTER TABLE reminder ADD COLUMN payment_id INTEGER")
                print("added reminder.payment_id")
            else:
                print("reminder.payment_id already there")

        #finished once-off bills get tucked away
        if "payment" in tables:
            cols = [c["name"] for c in inspector.get_columns("payment")]
            if "is_archived" not in cols:
                con.exec_driver_sql(
                    "ALTER TABLE payment ADD COLUMN is_archived BOOLEAN DEFAULT 0")
                print("added payment.is_archived")
            else:
                print("payment.is_archived already there")

        #buddy: drop the one-per-user rule and add is_active
        if "buddy" in tables:
            cols = [c["name"] for c in inspector.get_columns("buddy")]
            if "is_active" not in cols:
                con.exec_driver_sql("""
                    CREATE TABLE buddy_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        name VARCHAR(50) NOT NULL,
                        species VARCHAR(30) NOT NULL,
                        stage VARCHAR(10) NOT NULL,
                        xp INTEGER NOT NULL,
                        coins INTEGER NOT NULL,
                        created_at DATETIME,
                        is_active BOOLEAN,
                        user_id INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES user (id)
                    )""")
                #every existing buddy was the only one, so it stays active
                con.exec_driver_sql("""
                    INSERT INTO buddy_new
                        (id, name, species, stage, xp, coins, created_at, is_active, user_id)
                    SELECT id, name, species, stage, xp, coins, created_at, 1, user_id
                    FROM buddy""")
                con.exec_driver_sql("DROP TABLE buddy")
                con.exec_driver_sql("ALTER TABLE buddy_new RENAME TO buddy")
                print("rebuilt buddy table: multiple buddies allowed, is_active added")
            else:
                print("buddy.is_active already there")

    #any new tables, the first buddy deploy creates them all
    db.create_all()
    print("create_all done")

    with engine.connect() as con:
        ok = con.exec_driver_sql("PRAGMA integrity_check").scalar()
    print("integrity check:", ok)
