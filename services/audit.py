"""Admin audit trail: one row per important admin action."""
from flask import request
from flask_login import current_user

from models import AdminAuditLog, db


def record(action, entity_type, entity_id=None, details=None):
    """Add an audit row to the CURRENT transaction (the caller commits).

    Adding it to the same transaction as the change means the log and the
    change succeed or fail together. Never pass secrets in `details`.
    """
    db.session.add(AdminAuditLog(
        admin_id=getattr(current_user, "id", None),
        admin_email=getattr(current_user, "email", None),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=(details or "")[:500] or None,
        ip_address=(request.remote_addr or "")[:45] or None,
    ))
