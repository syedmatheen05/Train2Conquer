"""Queries behind the admin dashboard and the payment filters.

One definition of "successful / failed / pending / ..." is used both for the
dashboard counters and for the payment list filter, so they can never disagree.
"""
from sqlalchemy import and_, case, func, not_, or_

from models import FitnessProfile, Payment, Trainer, User, WorkoutPlan, db

PAYMENT_BUCKETS = ("successful", "failed", "pending", "cancelled", "refunded")
_PAID_STATUSES = ("paid", "used")
_BAD_VERIFICATION = ("failed", "mismatch")

_refunded = or_(Payment.status == "refunded", Payment.refund_status == "refunded")
_successful = and_(Payment.payment_verification_status == "verified",
                   Payment.status.in_(_PAID_STATUSES), not_(_refunded))
_cancelled = and_(Payment.status == "cancelled", not_(_refunded))
_failed_signal = or_(Payment.payment_status == "failed", Payment.payment_verification_status.in_(_BAD_VERIFICATION))
_failed = and_(_failed_signal, Payment.status.notin_(_PAID_STATUSES + ("refunded", "cancelled")))
_pending = and_(Payment.status == "created", not_(_failed_signal))

PAYMENT_CONDITIONS = {
    "successful": _successful, "failed": _failed, "pending": _pending,
    "cancelled": _cancelled, "refunded": _refunded,
}


def payment_bucket(payment):
    """Python twin of PAYMENT_CONDITIONS for showing a badge on one payment row."""
    if payment.status == "refunded" or payment.refund_status == "refunded":
        return "refunded"
    if payment.payment_verification_status == "verified" and payment.status in _PAID_STATUSES:
        return "successful"
    if payment.status == "cancelled":
        return "cancelled"
    if payment.payment_status == "failed" or payment.payment_verification_status in _BAD_VERIFICATION:
        return "failed"
    return "pending"


def _count_if(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def dashboard_stats():
    """All dashboard counters in four small queries."""
    users = db.session.execute(db.select(
        func.count(User.id), _count_if(User.is_active.is_(True)), _count_if(User.is_admin.is_(True)))).one()
    trainers = db.session.execute(db.select(func.count(Trainer.id), _count_if(Trainer.is_active.is_(True)))).one()
    plans = db.session.execute(db.select(
        func.count(WorkoutPlan.id), _count_if(WorkoutPlan.tier != "basic"),
        _count_if(WorkoutPlan.status == "failed"), _count_if(WorkoutPlan.status == "generating"))).one()
    payments = db.session.execute(db.select(
        func.count(Payment.id), *[_count_if(cond) for cond in PAYMENT_CONDITIONS.values()],
        _count_if(Payment.refund_status == "requested"))).one()
    profiles = db.session.scalar(db.select(func.count(FitnessProfile.id)))
    return {
        "users_total": users[0], "users_active": users[1], "users_inactive": users[0] - users[1], "admins": users[2],
        "trainers_total": trainers[0], "trainers_active": trainers[1], "trainers_inactive": trainers[0] - trainers[1],
        "plans_total": plans[0], "plans_premium": plans[1], "plans_free": plans[0] - plans[1],
        "plans_failed": plans[2], "plans_generating": plans[3],
        "payments_total": payments[0], "payments_successful": payments[1], "payments_failed": payments[2],
        "payments_pending": payments[3], "payments_cancelled": payments[4], "payments_refunded": payments[5],
        "refunds_requested": payments[6],
        "profiles_total": profiles,
    }
