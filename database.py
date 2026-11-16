"""Database connection settings (moved out of main.py, logic unchanged).

Reads the SUPABASE environment variable (any Postgres URL, or sqlite:///x.db
for local testing) and returns what Flask-SQLAlchemy needs.
"""
import os

from sqlalchemy.pool import NullPool

_PG_PREFIXES = ("postgres://", "postgresql://", "postgresql+psycopg://", "postgresql+pg8000://")
_LIBPQ_ONLY_PARAMS = {
    "sslmode", "sslrootcert", "sslcert", "sslkey", "channel_binding",
    "gssencmode", "target_session_attrs", "connect_timeout", "options",
}


def build_database_config():
    """Returns (database_uri, engine_options)."""
    database_url = os.environ.get("SUPABASE")
    if not database_url:
        raise RuntimeError("SUPABASE environment variable is not set")
    engine_options = {}
    database_url = database_url.strip()

    if database_url.startswith(_PG_PREFIXES):
        # psycopg (v3) is used when it loads; otherwise pure-Python pg8000.
        try:
            import psycopg  # noqa: F401
            driver = "psycopg"
        except ImportError:
            driver = "pg8000"
        rest = database_url.split("://", 1)[1]
        database_url = f"postgresql+{driver}://{rest}"

        if driver == "pg8000":
            import ssl
            from sqlalchemy.engine import make_url
            url = make_url(database_url)
            query = dict(url.query)
            sslmode = str(query.get("sslmode", "require")).lower()
            for key in list(query):          # pg8000 rejects libpq-only options
                if key in _LIBPQ_ONLY_PARAMS:
                    query.pop(key)
            url = url.set(query=query)
            database_url = url.render_as_string(hide_password=False)
            is_local = (not url.host) or url.host in ("localhost", "127.0.0.1", "::1") or "unix_sock" in query
            if sslmode != "disable" and not is_local:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                engine_options["connect_args"] = {"ssl_context": ctx}

    if database_url.startswith("postgresql"):
        engine_options["poolclass"] = NullPool
    return database_url, engine_options
