"""All database tables for Train2Conquer.

Existing tables keep their names and columns. New columns are added by the
numbered steps in migrations.py (never by dropping or recreating tables).
"""
from datetime import date, datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


def utcnow():
    """Naive UTC 'now' (same style the app always used, without the deprecation warning)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=False)
    # Added by migration 007. False = the account is suspended: it cannot log in
    # and an open session is ended on the next request. Data is kept.
    is_active: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=True, server_default=db.true())
    created_at = db.Column(db.DateTime, nullable=True, default=utcnow)


class Trainer(UserMixin, db.Model):
    __tablename__ = "trainers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    gender: Mapped[str] = mapped_column(String(20), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    about: Mapped[str] = mapped_column(String(300), nullable=True)
    # Added by migration 007. Inactive trainers are hidden from "Find Trainers".
    is_active: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=True, server_default=db.true())
    # Optional (migration 007). If set, "Contact Trainer" messages are e-mailed to the trainer.
    contact_email: Mapped[str] = mapped_column(String(200), nullable=True)


class FitnessProfile(db.Model):
    __tablename__ = "profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)
    dob: Mapped[date] = mapped_column(Date, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    gender: Mapped[str] = mapped_column(String(20), nullable=False)
    goal: Mapped[str] = mapped_column(String(50), nullable=False)
    experience: Mapped[str] = mapped_column(String(30), nullable=False)
    progression_score: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    workout_days: Mapped[int] = mapped_column(Integer, nullable=False)
    equipment: Mapped[str] = mapped_column(Text, nullable=False)


class Payment(db.Model):
    """One row per paid-plan purchase. One verified payment = one plan.

    status            our own life-cycle: created -> paid -> used
                      (also cancelled, refunded)
    payment_status    what the gateway last told us: created / authorized /
                      captured / failed / refunded
    payment_verification_status
                      pending / verified / failed / mismatch
    refund_status     none / requested / processing / partial / refunded / failed
    Amounts are integer paise (100 paise = Rs 1). fee/tax are NULL until
    Razorpay reports them - NULL means "unknown", never zero.
    """
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable + kept when an account is deleted, so money records stay auditable.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    gateway_order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    gateway_payment_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # generation tries
    user_notes: Mapped[str] = mapped_column(Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    verified_at = db.Column(db.DateTime, nullable=True)
    # --- added by migration 003 ---
    payment_status: Mapped[str] = mapped_column(String(20), nullable=False, default="created", server_default="created")
    payment_verification_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    refund_status: Mapped[str] = mapped_column(String(20), nullable=False, default="none", server_default="none")
    paid_at = db.Column(db.DateTime, nullable=True)
    used_at = db.Column(db.DateTime, nullable=True)
    payment_method: Mapped[str] = mapped_column(String(20), nullable=True)
    failure_code: Mapped[str] = mapped_column(String(60), nullable=True)
    fee_paise: Mapped[int] = mapped_column(Integer, nullable=True)       # Razorpay fee, if reported
    tax_paise: Mapped[int] = mapped_column(Integer, nullable=True)       # GST on that fee, if reported
    refund_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    gateway_refund_id: Mapped[str] = mapped_column(String(64), nullable=True)
    refund_requested_at = db.Column(db.DateTime, nullable=True)
    refunded_at = db.Column(db.DateTime, nullable=True)


class PaymentEvent(db.Model):
    """Every webhook delivery we have processed (unique id = no double-processing)."""
    __tablename__ = "payment_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    gateway_order_id: Mapped[str] = mapped_column(String(64), nullable=True)
    gateway_payment_id: Mapped[str] = mapped_column(String(64), nullable=True)
    received_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class WorkoutPlan(db.Model):
    """One row per generated week (history is kept; nothing is overwritten).

    `status` is what the pages use: generating | ready | failed.
    `generation_status` is the finer job state: queued | running | succeeded | failed.
    A 'generating' row with locked_at NULL is waiting for a worker; once a
    worker claims it, locked_at/locked_by are set (a lease that can expire).
    """
    __tablename__ = "workout_plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    plan: Mapped[str] = mapped_column(Text, nullable=False)
    completed_days: Mapped[str] = mapped_column(Text, default="[]", server_default="[]", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ready", server_default="ready", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)      # short safe code only
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="basic", server_default="basic")
    week_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), unique=True, nullable=True)
    coach_notes: Mapped[str] = mapped_column(Text, nullable=True)
    ai_input_tokens: Mapped[int] = mapped_column(Integer, nullable=True)
    ai_output_tokens: Mapped[int] = mapped_column(Integer, nullable=True)
    # --- added by migration 004 ---
    ai_provider: Mapped[str] = mapped_column(String(20), nullable=True)
    ai_model: Mapped[str] = mapped_column(String(60), nullable=True)
    ai_cost_usd = db.Column(db.Numeric(14, 8), nullable=True)            # total of KNOWN attempt costs
    ai_cost_inr = db.Column(db.Numeric(14, 4), nullable=True)
    ai_cost_complete: Mapped[bool] = mapped_column(db.Boolean, nullable=True)   # False = some attempt's cost unknown
    generation_status: Mapped[str] = mapped_column(String(20), nullable=True)
    generation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_at = db.Column(db.DateTime, nullable=True)
    locked_by: Mapped[str] = mapped_column(String(60), nullable=True)


class AIUsage(db.Model):
    """One row per OpenAI request (every billable attempt is its own row).

    Tokens/cost are NULL when OpenAI never returned usage - that means the
    cost is UNKNOWN, not zero.
    """
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=True, index=True)
    workout_plan_id: Mapped[int] = mapped_column(Integer, nullable=True, index=True)
    payment_id: Mapped[int] = mapped_column(Integer, nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="openai")
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_id: Mapped[str] = mapped_column(String(100), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=True)
    cost_usd = db.Column(db.Numeric(14, 8), nullable=True)
    cost_inr = db.Column(db.Numeric(14, 4), nullable=True)
    usd_to_inr_rate = db.Column(db.Numeric(10, 4), nullable=True)
    # success | invalid_output | error
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_type: Mapped[str] = mapped_column(String(60), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)


class OtpCode(db.Model):
    """One row per e-mailed verification code (added by migration 007).

    Only a keyed hash of the code is stored (see services/otp.py) and the
    attempt counter lives here, on the server. The browser never receives the
    code or the counter, so it can neither read the code nor reset the counter.
    """
    __tablename__ = "otp_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)      # login | registration | contact
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    consumed_at = db.Column(db.DateTime, nullable=True)


class AdminAuditLog(db.Model):
    """Who did what in the admin panel (added by migration 007).

    admin_id has no foreign key on purpose: deleting an account must never
    delete or block the history of what that account did. The e-mail is copied
    in so the record stays readable. `details` never holds secrets.
    """
    __tablename__ = "admin_audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(Integer, nullable=True, index=True)
    admin_email: Mapped[str] = mapped_column(String(200), nullable=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=True)
    details: Mapped[str] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
