"""One-off migration for the August 2026 batch (#50, #53, buddy house).
Safe to run twice - every step checks before changing anything.

    python migrate_2026_08.py

What it does:
  - reminder.payment_id  (new nullable column, #53)
  - payment.is_archived  (new column, default 0, #50)
  - buddy table rebuild: drops the one-buddy-per-user UNIQUE rule and adds
    is_active (the house needs several buddies; SQLite can't drop a
    constraint in place, so the table is copied and swapped)
  - db.create_all() for any tables that don't exist yet
"""
import sqlalchemy as sa

from app import app, db

with app.app_context():
    engine = db.engine
    inspector = sa.inspect(engine)
    tables = inspector.get_table_names()

    with engine.begin() as con:
        # ---- reminder.payment_id (#53) ----
        if "reminder" in tables:
            cols = [c["name"] for c in inspector.get_columns("reminder")]
            if "payment_id" not in cols:
                con.exec_driver_sql("ALTER TABLE reminder ADD COLUMN payment_id INTEGER")
                print("added reminder.payment_id")
            else:
                print("reminder.payment_id already there")

        # ---- payment.is_archived (#50) ----
        if "payment" in tables:
            cols = [c["name"] for c in inspector.get_columns("payment")]
            if "is_archived" not in cols:
                con.exec_driver_sql(
                    "ALTER TABLE payment ADD COLUMN is_archived BOOLEAN DEFAULT 0")
                print("added payment.is_archived")
            else:
                print("payment.is_archived already there")

        # ---- buddy: drop UNIQUE(user_id), add is_active ----
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
                #every existing buddy was that user's only one, so it stays active
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

    #any brand-new tables (first deploy of the buddy feature creates them all)
    db.create_all()
    print("create_all done")

    with engine.connect() as con:
        ok = con.exec_driver_sql("PRAGMA integrity_check").scalar()
    print("integrity check:", ok)
