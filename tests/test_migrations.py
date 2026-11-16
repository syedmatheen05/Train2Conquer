"""Upgrading an OLD database (before payments/accounting existed) must keep every row."""
import os
import sqlite3
import subprocess
import sys

from conftest import BASE


def _old_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, email VARCHAR(200) NOT NULL UNIQUE, is_admin BOOLEAN NOT NULL DEFAULT 0);
    CREATE TABLE profiles (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id), dob DATE NOT NULL, height INTEGER NOT NULL, weight INTEGER NOT NULL, gender VARCHAR(20) NOT NULL, goal VARCHAR(50) NOT NULL, experience VARCHAR(30) NOT NULL, progression_score INTEGER NOT NULL, workout_days INTEGER NOT NULL, equipment TEXT NOT NULL);
    CREATE TABLE workout_plans (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id), created_at DATETIME NOT NULL, plan TEXT NOT NULL, completed_days TEXT NOT NULL DEFAULT '[]', status VARCHAR(20) NOT NULL DEFAULT 'ready', error_message TEXT);
    CREATE TABLE payments (id INTEGER PRIMARY KEY, user_id INTEGER, tier VARCHAR(20) NOT NULL, amount_paise INTEGER NOT NULL, currency VARCHAR(3) NOT NULL, gateway_order_id VARCHAR(64) NOT NULL UNIQUE, gateway_payment_id VARCHAR(64) UNIQUE, status VARCHAR(20) NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, user_notes TEXT, created_at DATETIME NOT NULL, verified_at DATETIME);
    INSERT INTO users VALUES (1,'Old User','old@example.com',0);
    INSERT INTO profiles VALUES (1,1,'2000-01-01',170,70,'male','muscle_gain','beginner',1,3,'none');
    INSERT INTO workout_plans VALUES (1,1,'2026-01-01 00:00:00','{"old":"plan"}','[1,2]','ready',NULL);
    INSERT INTO payments VALUES (1,1,'personalized',500,'INR','order_old','pay_old','used',1,'knee','2026-01-01 00:00:00','2026-01-01 00:01:00');
    INSERT INTO payments VALUES (2,1,'advanced',1500,'INR','order_old2',NULL,'created',0,NULL,'2026-01-02 00:00:00',NULL);
    """)
    con.commit(); con.close()


def _manage(args, db_path, extra=None):
    env = dict(os.environ, SUPABASE=f"sqlite:///{db_path}", APP_ENV="development")
    env.update(extra or {})
    return subprocess.run([sys.executable, "manage.py", *args], cwd=BASE, env=env, capture_output=True, text=True, timeout=60)


def test_old_database_upgrades_without_losing_data(tmp_path):
    path = str(tmp_path / "old.db"); _old_db(path)
    assert "001" in _manage(["status"], path).stdout
    r = _manage(["migrate"], path)
    assert r.returncode == 0 and "Backup saved" in r.stdout            # SQLite is backed up automatically
    assert any(f.startswith("old.db.backup-") for f in os.listdir(tmp_path))
    con = sqlite3.connect(path)
    assert con.execute("select plan, completed_days, tier, week_number, generation_status, ai_provider from workout_plans").fetchone() == ('{"old":"plan"}', "[1,2]", "basic", 1, "succeeded", "gemini")
    used = con.execute("select status, payment_status, payment_verification_status, paid_at, used_at, refund_status from payments where id=1").fetchone()
    assert used[:3] == ("used", "captured", "verified") and used[3] == "2026-01-01 00:01:00" and used[5] == "none"
    assert con.execute("select payment_status, payment_verification_status from payments where id=2").fetchone() == ("created", "pending")
    assert con.execute("select count(*) from users").fetchone()[0] == 1
    # many plans per user now allowed; new tables exist
    con.execute("insert into workout_plans (user_id, created_at, plan) values (1, '2026-02-01', '{}')")
    assert {"ai_usage", "payment_events", "schema_migrations"} <= {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    con.close()
    again = _manage(["migrate"], path)
    assert "already up to date" in again.stdout                          # safe to run twice


def test_postgres_migration_refuses_without_backup_confirmation(monkeypatch, capsys):
    import manage, migrations
    ran = []
    monkeypatch.setattr(manage, "_engine", lambda: (None, "postgresql://u:p@host/db"))
    monkeypatch.setattr(migrations, "pending_migrations", lambda e: [(1, "x")])
    monkeypatch.setattr(migrations, "run_migrations", lambda e, log=print: ran.append(1))
    monkeypatch.delenv("MIGRATION_BACKUP_CONFIRMED", raising=False)
    assert manage.main(["manage.py", "migrate"]) == 2 and ran == []
    assert "STOP" in capsys.readouterr().out
    assert manage.main(["manage.py", "migrate", "--i-have-a-backup"]) == 0 and ran == [1]
