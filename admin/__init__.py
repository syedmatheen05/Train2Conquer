"""Admin panel Blueprint (everything under /admin).

Access is enforced in ONE place, `require_admin` in routes.py, which runs
before every view in this blueprint - a route added later cannot forget it.
"""
from flask import Blueprint

bp = Blueprint("admin", __name__, url_prefix="/admin")

from . import routes  # noqa: E402,F401  (registers the routes on `bp`)
