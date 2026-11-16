"""Flask extensions created once and attached to the app in main.py.

Keeping them here lets blueprints import them without importing main.py
(which would be a circular import).
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import current_user


def _limit_key():
    """Rate limits are per logged-in user (falls back to the visitor's address)."""
    if current_user and current_user.is_authenticated:
        return f"user:{current_user.id}"
    return get_remote_address()


limiter = Limiter(_limit_key, default_limits=[])
