"""Safe, numbered database upgrades for Train2Conquer.

How it works
------------
* Each step has a number and runs at most once. The numbers that already ran
  are remembered in a small table called `schema_migrations`.
* Every step only ADDS things (tables, columns, indexes) and fills in values.
  Nothing here deletes a user's data. Each step also checks first, so running
  it twice is harmless.
* Run it with:   python manage.py migrate      (see README - back up first!)
"""
from sqlalchemy import inspect, text

from models import AdminAuditLog, Base, OtpCode, WorkoutPlan  # importing models registers every table on Base
from models import utcnow

ADVISORY_LOCK_ID = 727274   # stops two servers migrating at the same moment (Postgres)


def _columns(conn, table):
    insp = inspect(conn)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _add_columns(conn, table, columns):
    """columns: {name: 'SQL TYPE ... DEFAULT ...'}. Adds only the missing ones."""
    existing = _columns(conn, table)
    if not existing:
        return
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


# ---------------------------------------------------------------- steps
def m001_create_missing_tables(conn):
    """Creates any table that does not exist yet. Existing tables are untouched."""
    Base.metadata.create_all(conn)


def m002_legacy_fixes(conn):
    """The upgrades the app used to do silently on every start."""
    _add_columns(conn, "users", {"is_admin": "BOOLEAN NOT NULL DEFAULT FALSE"})
    _add_columns(conn, "workout_plans", {
        "completed_days": "TEXT NOT NULL DEFAULT '[]'",
        "status": "VARCHAR(20) NOT NULL DEFAULT 'ready'",
        "error_message": "TEXT",
        "tier": "VARCHAR(20) NOT NULL DEFAULT 'basic'",
        "week_number": "INTEGER NOT NULL DEFAULT 1",
        "payment_id": "INTEGER REFERENCES payments(id)",
        "coach_notes": "TEXT",
        "ai_input_tokens": "INTEGER",
        "ai_output_tokens": "INTEGER",
    })
    if "workout_plans" not in inspect(conn).get_table_names():
        return
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_workout_plans_payment_id ON workout_plans (payment_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_workout_plans_user_id ON workout_plans (user_id)"))
    # Very old databases said "one plan per user" (UNIQUE on user_id). Remove that rule.
    insp = inspect(conn)
    one_per_user = [u for u in insp.get_unique_constraints("workout_plans") if u["column_names"] == ["user_id"]]
    one_per_user_idx = [i for i in insp.get_indexes("workout_plans") if i.get("unique") and i["column_names"] == ["user_id"]]
    dialect = conn.dialect.name
    if dialect == "postgresql":
        for constraint in one_per_user:
            conn.execute(text(f'ALTER TABLE workout_plans DROP CONSTRAINT "{constraint["name"]}"'))
        for index in one_per_user_idx:
            if not any(c["name"] == index["name"] for c in one_per_user):
                conn.execute(text(f'DROP INDEX IF EXISTS "{index["name"]}"'))
    elif (one_per_user or one_per_user_idx) and dialect == "sqlite":
        # SQLite cannot drop a constraint: copy every row into a fresh table (no rows lost).
        old_cols = _columns(conn, "workout_plans")
        conn.execute(text("ALTER TABLE workout_plans RENAME TO workout_plans_old"))
        for stale in ("uq_workout_plans_payment_id", "ix_workout_plans_user_id"):
            conn.execute(text(f"DROP INDEX IF EXISTS {stale}"))
        WorkoutPlan.__table__.create(conn)
        shared = [c.name for c in WorkoutPlan.__table__.columns if c.name in old_cols]
        names = ", ".join(shared)
        conn.execute(text(f"INSERT INTO workout_plans ({names}) SELECT {names} FROM workout_plans_old"))
        conn.execute(text("DROP TABLE workout_plans_old"))


def m003_payment_columns(conn):
    _add_columns(conn, "payments", {
        "payment_status": "VARCHAR(20) NOT NULL DEFAULT 'created'",
        "payment_verification_status": "VARCHAR(20) NOT NULL DEFAULT 'pending'",
        "refund_status": "VARCHAR(20) NOT NULL DEFAULT 'none'",
        "paid_at": "TIMESTAMP",
        "used_at": "TIMESTAMP",
        "payment_method": "VARCHAR(20)",
        "failure_code": "VARCHAR(60)",
        "fee_paise": "INTEGER",
        "tax_paise": "INTEGER",
        "refund_amount_paise": "INTEGER NOT NULL DEFAULT 0",
        "gateway_refund_id": "VARCHAR(64)",
        "refund_requested_at": "TIMESTAMP",
        "refunded_at": "TIMESTAMP",
    })


def m004_workout_plan_columns(conn):
    _add_columns(conn, "workout_plans", {
        "ai_provider": "VARCHAR(20)",
        "ai_model": "VARCHAR(60)",
        "ai_cost_usd": "NUMERIC(14, 8)",
        "ai_cost_inr": "NUMERIC(14, 4)",
        "ai_cost_complete": "BOOLEAN",
        "generation_status": "VARCHAR(20)",
        "generation_attempts": "INTEGER NOT NULL DEFAULT 0",
        "locked_at": "TIMESTAMP",
        "locked_by": "VARCHAR(60)",
    })


def m005_backfill(conn):
    """Fill the new columns for rows that existed before this upgrade."""
    conn.execute(text("""UPDATE payments SET payment_status = 'captured', payment_verification_status = 'verified'
                         WHERE status IN ('paid', 'used') AND payment_status = 'created'"""))
    conn.execute(text("UPDATE payments SET payment_status = 'refunded' WHERE status = 'refunded' AND payment_status = 'created'"))
    conn.execute(text("UPDATE payments SET paid_at = verified_at WHERE status IN ('paid', 'used') AND paid_at IS NULL"))
    conn.execute(text("""UPDATE payments SET used_at =
                         (SELECT created_at FROM workout_plans WHERE workout_plans.payment_id = payments.id)
                         WHERE status = 'used' AND used_at IS NULL"""))
    conn.execute(text("""UPDATE workout_plans SET generation_status = CASE status
                         WHEN 'ready' THEN 'succeeded' WHEN 'failed' THEN 'failed' ELSE 'running' END
                         WHERE generation_status IS NULL"""))
    conn.execute(text("UPDATE workout_plans SET ai_provider = 'gemini' WHERE ai_provider IS NULL AND tier = 'basic'"))
    # Historical label only: paid plans built before the Gemini-only switch really were made with OpenAI.
    conn.execute(text("UPDATE workout_plans SET ai_provider = 'openai' WHERE ai_provider IS NULL AND tier <> 'basic'"))


def m006_indexes(conn):
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_workout_plans_generation ON workout_plans (status, locked_at)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_payments_status ON payments (status)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_payments_created_at ON payments (created_at)"))


def m007_accounts_otp_audit(conn):
    """Account/trainer suspension flags, server-side OTP storage, admin audit log.

    Add-only: two new columns with safe defaults (everyone stays active) and
    two new tables. No existing column or row is changed or removed.
    """
    _add_columns(conn, "users", {"is_active": "BOOLEAN NOT NULL DEFAULT TRUE", "created_at": "TIMESTAMP"})
    _add_columns(conn, "trainers", {"is_active": "BOOLEAN NOT NULL DEFAULT TRUE", "contact_email": "VARCHAR(200)"})
    OtpCode.__table__.create(conn, checkfirst=True)
    AdminAuditLog.__table__.create(conn, checkfirst=True)


def m008_plan_replacement(conn):
    """Adds is_archived + profile_hash, then gives every existing plan the fingerprint of its
    user's CURRENT profile, so weekly progression carries on until the user actually edits it."""
    import plan_cycle
    _add_columns(conn, "workout_plans", {
        "is_archived": "BOOLEAN NOT NULL DEFAULT FALSE",
        "profile_hash": "VARCHAR(64)",
    })
    if "profiles" not in inspect(conn).get_table_names():
        return
    table = "profiles"
    from types import SimpleNamespace
    from datetime import date as _date
    for row in conn.execute(text(f"SELECT user_id, dob, height, weight, gender, goal, experience, workout_days, equipment FROM {table}")).fetchall():
        dob = row.dob if isinstance(row.dob, _date) else (_date.fromisoformat(str(row.dob)[:10]) if row.dob else None)
        fp = plan_cycle.fingerprint(SimpleNamespace(dob=dob, height=row.height, weight=row.weight, gender=row.gender, goal=row.goal,
                                                    experience=row.experience, workout_days=row.workout_days, equipment=row.equipment))
        conn.execute(text("UPDATE workout_plans SET profile_hash = :h WHERE user_id = :u AND profile_hash IS NULL"), {"h": fp, "u": row.user_id})


MIGRATIONS = [
    (1, "create_missing_tables", m001_create_missing_tables),
    (2, "legacy_fixes", m002_legacy_fixes),
    (3, "payment_columns", m003_payment_columns),
    (4, "workout_plan_columns", m004_workout_plan_columns),
    (5, "backfill_new_columns", m005_backfill),
    (6, "indexes", m006_indexes),
    (7, "accounts_otp_audit", m007_accounts_otp_audit),
    (8, "plan_replacement", m008_plan_replacement),
]
LATEST_VERSION = MIGRATIONS[-1][0]

_VERSION_TABLE = """CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL, applied_at TIMESTAMP NOT NULL)"""


def applied_versions(engine):
    with engine.connect() as conn:
        if "schema_migrations" not in inspect(conn).get_table_names():
            return set()
        return {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}


def pending_migrations(engine):
    done = applied_versions(engine)
    return [(v, n) for v, n, _ in MIGRATIONS if v not in done]


def schema_is_current(engine):
    return not pending_migrations(engine)


def run_migrations(engine, log=print):
    """Applies every missing step, in order. Returns the list of steps applied."""
    applied = []
    lock_conn = None
    if engine.dialect.name == "postgresql":
        lock_conn = engine.connect()
        lock_conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": ADVISORY_LOCK_ID})
    try:
        with engine.begin() as conn:
            conn.execute(text(_VERSION_TABLE))
        for version, name, func in MIGRATIONS:
            if version in applied_versions(engine):
                continue
            with engine.begin() as conn:          # one step = one transaction (rolled back if it fails)
                func(conn)
                conn.execute(text("INSERT INTO schema_migrations (version, name, applied_at) VALUES (:v, :n, :t)"),
                             {"v": version, "n": name, "t": utcnow()})
            log(f"  applied {version:03d} {name}")
            applied.append((version, name))
    finally:
        if lock_conn is not None:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": ADVISORY_LOCK_ID})
            lock_conn.close()
    return applied
