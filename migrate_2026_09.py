"""
One-off migration for the September 2026 security batch.
Safe to run twice.

    python migrate_2026_09.py

Adds the indexes every query filters by. New databases get them from
the models; this catches databases made before then.
"""
import sqlalchemy as sa

from app import app, db

INDEXES = {
    "ix_payment_user_id":      ("payment", "user_id"),
    "ix_income_user_id":       ("income", "user_id"),
    "ix_reminder_user_id":     ("reminder", "user_id"),
    "ix_reminder_payment_id":  ("reminder", "payment_id"),
    "ix_payment_log_user_id":  ("payment_log", "user_id"),
    "ix_payment_log_payment_id": ("payment_log", "payment_id"),
    "ix_buddy_user_id":        ("buddy", "user_id"),
    "ix_xp_event_user_id":     ("xp_event", "user_id"),
    "ix_owned_cosmetic_user_id": ("owned_cosmetic", "user_id"),
}

with app.app_context():
    inspector = sa.inspect(db.engine)
    tables = inspector.get_table_names()
    with db.engine.begin() as con:
        for name, (table, column) in INDEXES.items():
            if table not in tables:
                print(f"skip {name}: no {table} table yet (create_all will handle it)")
                continue
            con.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")
            print(f"ok   {name}")
    with db.engine.connect() as con:
        print("integrity check:", con.exec_driver_sql("PRAGMA integrity_check").scalar())
