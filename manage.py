"""Database upgrade tool.

    python manage.py status                    show which upgrades are pending
    python manage.py migrate                   apply pending upgrades

Upgrades only ADD tables/columns and never delete user data - but ALWAYS take
a backup first (see README, "Back up before upgrading"). In production the
tool refuses to run until you confirm the backup:

    python manage.py migrate --i-have-a-backup
"""
import os
import shutil
import sys
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import config
import migrations
from database import build_database_config
from sqlalchemy import create_engine


def _engine():
    url, options = build_database_config()
    return create_engine(url, **options), url


def main(argv):
    command = argv[1] if len(argv) > 1 else "status"
    engine, url = _engine()
    pending = migrations.pending_migrations(engine)
    if command == "status":
        print("Environment:", config.app_environment())
        print("Pending upgrades:", ", ".join(f"{v:03d} {n}" for v, n in pending) or "none (database is up to date)")
        return 0
    if command != "migrate":
        print(__doc__)
        return 1
    if not pending:
        print("Database is already up to date.")
        return 0
    confirmed = "--i-have-a-backup" in argv or os.environ.get("MIGRATION_BACKUP_CONFIRMED") == "yes"
    if url.startswith("sqlite"):
        path = url.split("///", 1)[1]
        if os.path.exists(path):          # SQLite: we can back up for you
            copy = f"{path}.backup-{datetime.now():%Y%m%d-%H%M%S}"
            shutil.copy2(path, copy)
            print("Backup saved to", copy)
            confirmed = True
    if not confirmed:
        print("STOP: this would change your live database.\n"
              "1) Make a backup (README -> 'Back up before upgrading').\n"
              "2) Run again with:  python manage.py migrate --i-have-a-backup")
        return 2
    print("Applying upgrades...")
    migrations.run_migrations(engine)
    print("Done. Pending now:", migrations.pending_migrations(engine) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
