"""Admin panel views.

Rules every view here follows:
  * admin access is checked server-side by `require_admin` (before_request);
  * every state change is a POST (CSRF-protected globally by CSRFProtect);
  * destructive actions need a typed confirmation that the SERVER checks;
  * every important change is written to the audit log in the same
    transaction as the change itself;
  * payments are never edited or deleted here (financial audit trail), and
    refunds go only through Razorpay's refund API.
"""
import json
import os
from datetime import date

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_fresh
from flask_sqlalchemy.pagination import SelectPagination
from sqlalchemy import update

import billing
import config
import costing
import finance
from exercises import EXERCISE_NAMES
from extensions import limiter
from forms import AdminTrainerForm, AdminUserForm
from models import (AdminAuditLog, AIUsage, FitnessProfile, Payment, PaymentEvent, Trainer, User,
                    WorkoutPlan, db, utcnow)
from services import accounts as account_service, audit, trainers as trainer_service
from services.admin_stats import PAYMENT_BUCKETS, PAYMENT_CONDITIONS, dashboard_stats, payment_bucket

from . import bp

PAGE_SIZE = 20
DELETE_WORD = "DELETE"
PLAN_TIERS = ("basic", "personalized", "advanced", "premium")
PLAN_STATUSES = ("generating", "ready", "failed")


# ------------------------------------------------------------------ access
@bp.before_request
def require_admin():
    """Runs before EVERY admin view. Anonymous -> login page; non-admin -> home."""
    if not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()
    if not current_user.is_admin:
        flash("You don't have access to the admin panel.", "danger")
        return redirect(url_for("home"))
    if not login_fresh():       # restored from a Remember Me cookie: confirm with a new e-mail code first
        return current_app.login_manager.needs_refresh()
    return None


# ----------------------------------------------------------------- helpers
class RowPagination(SelectPagination):
    """db.paginate() keeps only the first column of each row; list pages select
    several columns, so keep whole rows. Counting and limit/offset are inherited."""

    def _query_items(self):
        select = self._query_args["select"].limit(self.per_page).offset(self._query_offset)
        return list(self._query_args["session"].execute(select).all())


def _paginate_rows(query):
    return RowPagination(page=request.args.get("page", 1, type=int), per_page=PAGE_SIZE, max_per_page=None,
                         error_out=False, count=True, select=query, session=db.session)


def _paginate(query):
    return db.paginate(query, page=request.args.get("page", 1, type=int), per_page=PAGE_SIZE, error_out=False)


def _like(text):
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _count_active_admins():
    return db.session.scalar(db.select(db.func.count(User.id)).where(User.is_admin.is_(True), User.is_active.is_(True)))


def _email_taken(email, exclude_id=None):
    query = db.select(User.id).where(User.email == email)
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    return db.session.scalar(query) is not None


def _confirmed(expected):
    """The typed confirmation sent with a destructive POST, compared on the server."""
    return (request.form.get("confirm") or "").strip().lower() == expected.strip().lower()


def _parse_plan_days(plan_row):
    """(days, completed, error) for showing one saved plan. Never raises."""
    days, error = [], None
    try:
        completed = json.loads(plan_row.completed_days or "[]")
        completed = completed if isinstance(completed, list) else []
    except (TypeError, ValueError):
        completed = []
    if plan_row.status == "ready":
        try:
            parsed = json.loads(plan_row.plan)
            for key in sorted(parsed, key=lambda k: int(k.split("_")[1])):
                day = parsed[key]
                days.append({"number": int(key.split("_")[1]), "meta": day[0],
                             "exercises": [e for e in day[1:] if isinstance(e, dict)]})
        except (TypeError, ValueError, KeyError, IndexError, AttributeError):
            error = "This saved plan could not be read (invalid JSON)."
    return days, completed, error


# --------------------------------------------------------------- dashboard
def _env_info():
    """Shown to admins only: which mode the site is in and any money-settings warnings."""
    warnings = []
    problem = billing.config_problem()
    if problem:
        warnings.append("Payments are OFF: " + problem)
    if config.usd_to_inr() is None:
        warnings.append("OPENAI_USD_TO_INR is not set, so API costs in rupees are unknown.")
    for tier in billing.PAID_TIERS:
        if costing.pricing_for(config.plan_model(tier)) is None:
            warnings.append(f"No price is configured for the {tier} model, so its API cost is recorded as unknown.")
    if not os.environ.get("OPENAI_API_KEY"):
        warnings.append("OPENAI_API_KEY is not set: paid plans cannot be generated.")
    return {"environment": config.app_environment(), "razorpay_mode": config.razorpay_mode(),
            "generation_mode": config.generation_mode(), "warnings": warnings}


@bp.route("")
def dashboard():
    recent_actions = db.session.execute(
        db.select(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(5)).scalars().all()
    return render_template("admin/dashboard.html", stats=dashboard_stats(), fin=finance.overview({}),
                           env_info=_env_info(), recent_actions=recent_actions)


@bp.route("/finance")
def finance_overview():
    filters = finance.parse_filters(request.args)
    items, total = finance.transactions(filters)
    return render_template(
        "admin/finance.html", fin=finance.overview(filters), items=items, total=total, filters=filters,
        pages=max(1, -(-total // finance.PAGE_SIZE)), env_info=_env_info(), plan_choices=finance.PLAN_CHOICES,
        payment_status_choices=finance.PAYMENT_STATUS_CHOICES,
        generation_status_choices=finance.GENERATION_STATUS_CHOICES, raw=request.args)


# ------------------------------------------------------------------- users
@bp.route("/users")
def users():
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    role = request.args.get("role", "")
    # Outer-join the profile and latest plan status so the list needs one query, not one per row.
    latest_status = (db.select(WorkoutPlan.status).where(WorkoutPlan.user_id == User.id)
                     .order_by(WorkoutPlan.id.desc()).limit(1).scalar_subquery())
    query = (db.select(User, FitnessProfile.goal, FitnessProfile.experience, latest_status)
             .outerjoin(FitnessProfile, FitnessProfile.user_id == User.id).order_by(User.name, User.id))
    if search:
        query = query.where(db.or_(User.name.ilike(_like(search), escape="\\"), User.email.ilike(_like(search), escape="\\")))
    if status in ("active", "inactive"):
        query = query.where(User.is_active.is_(status == "active"))
    if role in ("admin", "user"):
        query = query.where(User.is_admin.is_(role == "admin"))
    page = _paginate_rows(query)
    return render_template("admin/users.html", rows=page.items, page=page, search=search, status=status, role=role)


@bp.route("/users/<int:user_id>")
def user_detail(user_id):
    user = db.get_or_404(User, user_id)
    profile = db.session.scalar(db.select(FitnessProfile).where(FitnessProfile.user_id == user.id))
    age, equipment = None, []
    if profile:
        today = date.today()
        age = today.year - profile.dob.year - ((today.month, today.day) < (profile.dob.month, profile.dob.day))
        equipment = [x.strip() for x in profile.equipment.split(",") if x.strip()]
    plan_row = db.session.scalar(db.select(WorkoutPlan).where(WorkoutPlan.user_id == user.id)
                                 .order_by(WorkoutPlan.id.desc()).limit(1))
    plan_days, completed_days, plan_error = _parse_plan_days(plan_row) if plan_row else ([], [], None)
    recent_plans = db.session.execute(
        db.select(WorkoutPlan.id, WorkoutPlan.week_number, WorkoutPlan.tier, WorkoutPlan.status, WorkoutPlan.created_at)
        .where(WorkoutPlan.user_id == user.id).order_by(WorkoutPlan.id.desc()).limit(10)).all()
    recent_payments = db.session.execute(
        db.select(Payment).where(Payment.user_id == user.id).order_by(Payment.id.desc()).limit(10)).scalars().all()
    plan_count = db.session.scalar(db.select(db.func.count(WorkoutPlan.id)).where(WorkoutPlan.user_id == user.id))
    return render_template(
        "admin/user_detail.html", user=user, profile=profile, age=age, equipment=equipment, plan=plan_row,
        plan_days=plan_days, completed_days=completed_days, plan_error=plan_error, exercise_names=EXERCISE_NAMES,
        recent_plans=recent_plans, recent_payments=recent_payments, plan_count=plan_count,
        payment_bucket=payment_bucket)


@bp.route("/users/new", methods=["GET", "POST"])
def user_new():
    form = AdminUserForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if _email_taken(email):
            form.email.errors.append("A user with this e-mail already exists.")
            return render_template("admin/user_form.html", form=form, mode="new", user=None)
        user = User(name=form.name.data.strip(), email=email, is_admin=bool(form.is_admin.data),
                    is_active=bool(form.is_active.data))
        db.session.add(user)
        db.session.flush()
        audit.record("user.create", "user", user.id, f"{user.email} admin={user.is_admin} active={user.is_active}")
        db.session.commit()
        flash(f'User "{user.name}" added.', "success")
        return redirect(url_for("admin.users"))
    if request.method == "GET":
        form.is_active.data = True
    return render_template("admin/user_form.html", form=form, mode="new", user=None)


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
def user_edit(user_id):
    user = db.get_or_404(User, user_id)
    form = AdminUserForm(obj=user)
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        wants_admin, wants_active = bool(form.is_admin.data), bool(form.is_active.data)
        problem = None
        if _email_taken(email, exclude_id=user.id):
            form.email.errors.append("A user with this e-mail already exists.")
            return render_template("admin/user_form.html", form=form, mode="edit", user=user)
        if user.id == current_user.id and not wants_active:
            problem = "You can't deactivate your own account."
        elif user.is_admin and user.is_active and (not wants_admin or not wants_active) and _count_active_admins() <= 1:
            problem = "You can't remove admin access from, or deactivate, the last active admin."
        if problem:
            flash(problem, "danger")
            return render_template("admin/user_form.html", form=form, mode="edit", user=user)
        changes = [f"{field}: {old} -> {new}" for field, old, new in (
            ("name", user.name, form.name.data.strip()), ("email", user.email, email),
            ("admin", user.is_admin, wants_admin), ("active", user.is_active, wants_active)) if old != new]
        user.name, user.email, user.is_admin, user.is_active = form.name.data.strip(), email, wants_admin, wants_active
        if changes:
            audit.record("user.update", "user", user.id, "; ".join(changes))
        db.session.commit()
        flash(f'User "{user.name}" updated.', "success")
        return redirect(url_for("admin.user_detail", user_id=user.id))
    return render_template("admin/user_form.html", form=form, mode="edit", user=user)


@bp.route("/users/<int:user_id>/status", methods=["POST"])
def user_set_status(user_id):
    """Suspend or re-activate an account (nothing is deleted)."""
    user = db.get_or_404(User, user_id)
    activate = request.form.get("action") == "activate"
    if not activate:
        if user.id == current_user.id:
            flash("You can't deactivate your own account.", "danger")
            return redirect(url_for("admin.user_detail", user_id=user.id))
        if user.is_admin and user.is_active and _count_active_admins() <= 1:
            flash("You can't deactivate the last active admin.", "danger")
            return redirect(url_for("admin.user_detail", user_id=user.id))
    user.is_active = activate
    audit.record("user.activate" if activate else "user.deactivate", "user", user.id, user.email)
    db.session.commit()
    flash(f'User "{user.name}" {"activated" if activate else "deactivated"}.', "success")
    return redirect(url_for("admin.user_detail", user_id=user.id))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
def user_delete(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("You can't delete your own account from the admin panel.", "danger")
        return redirect(url_for("admin.user_detail", user_id=user.id))
    if user.is_admin and user.is_active and _count_active_admins() <= 1:
        flash("You can't delete the last active admin.", "danger")
        return redirect(url_for("admin.user_detail", user_id=user.id))
    if not _confirmed(user.email):
        flash("Type the user's e-mail address exactly to confirm deletion.", "danger")
        return redirect(url_for("admin.user_detail", user_id=user.id))
    name, email, uid = user.name, user.email, user.id
    try:
        account_service.delete_user_account(uid)
        audit.record("user.delete", "user", uid, f"{email} (payments kept, detached from the account)")
        db.session.commit()
        flash(f'User "{name}" deleted. Their payment records were kept for the financial audit trail.', "info")
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Admin user delete failed: %s", type(exc).__name__)
        flash("We couldn't delete that user right now.", "danger")
    return redirect(url_for("admin.users"))


# ------------------------------------------------------------ workout plans
@bp.route("/plans")
def plans():
    search = request.args.get("q", "").strip()
    tier = request.args.get("tier", "")
    status = request.args.get("status", "")
    query = (db.select(WorkoutPlan.id, WorkoutPlan.week_number, WorkoutPlan.tier, WorkoutPlan.status,
                       WorkoutPlan.created_at, WorkoutPlan.payment_id, User.id.label("user_id"),
                       User.name.label("user_name"), User.email.label("user_email"))
             .join(User, User.id == WorkoutPlan.user_id).order_by(WorkoutPlan.id.desc()))
    if search:
        query = query.where(db.or_(User.name.ilike(_like(search), escape="\\"), User.email.ilike(_like(search), escape="\\")))
    if tier == "premium":
        query = query.where(WorkoutPlan.tier != "basic")
    elif tier in PLAN_TIERS:
        query = query.where(WorkoutPlan.tier == tier)
    if status in PLAN_STATUSES:
        query = query.where(WorkoutPlan.status == status)
    page = _paginate_rows(query)
    return render_template("admin/plans.html", rows=page.items, page=page, search=search, tier=tier, status=status,
                           tiers=PLAN_TIERS, statuses=PLAN_STATUSES)


@bp.route("/plans/<int:plan_id>")
def plan_detail(plan_id):
    plan = db.get_or_404(WorkoutPlan, plan_id)
    owner = db.session.get(User, plan.user_id)
    payment = db.session.get(Payment, plan.payment_id) if plan.payment_id else None
    usage = db.session.execute(db.select(AIUsage).where(AIUsage.workout_plan_id == plan.id)
                               .order_by(AIUsage.id)).scalars().all()
    plan_days, completed_days, plan_error = _parse_plan_days(plan)
    return render_template("admin/plan_detail.html", plan=plan, owner=owner, payment=payment, usage=usage,
                           plan_days=plan_days, completed_days=completed_days, plan_error=plan_error,
                           exercise_names=EXERCISE_NAMES, delete_word=DELETE_WORD)


@bp.route("/plans/<int:plan_id>/delete", methods=["POST"])
def plan_delete(plan_id):
    plan = db.get_or_404(WorkoutPlan, plan_id)
    if not _confirmed(DELETE_WORD):
        flash(f"Type {DELETE_WORD} to confirm deleting this plan.", "danger")
        return redirect(url_for("admin.plan_detail", plan_id=plan.id))
    owner_id, tier, week = plan.user_id, plan.tier, plan.week_number
    db.session.delete(plan)           # payment + AI-usage rows are untouched (they are financial records)
    audit.record("plan.delete", "plan", plan_id, f"user {owner_id}, tier {tier}, week {week}")
    db.session.commit()
    flash("Workout plan deleted. Any linked payment record was kept.", "info")
    return redirect(url_for("admin.plans"))


# ---------------------------------------------------------------- payments
@bp.route("/payments")
def payments():
    """READ-ONLY list. Payment rows are never edited or deleted from the admin panel."""
    search = request.args.get("q", "").strip()
    bucket = request.args.get("status", "")
    tier = request.args.get("tier", "")
    query = (db.select(Payment, User.name, User.email).outerjoin(User, User.id == Payment.user_id)
             .order_by(Payment.id.desc()))
    if search:
        like = _like(search)
        conditions = [Payment.gateway_order_id.ilike(like, escape="\\"), Payment.gateway_payment_id.ilike(like, escape="\\"),
                      User.name.ilike(like, escape="\\"), User.email.ilike(like, escape="\\")]
        if search.isdigit():
            conditions.append(Payment.id == int(search))
        query = query.where(db.or_(*conditions))
    if bucket in PAYMENT_CONDITIONS:
        query = query.where(PAYMENT_CONDITIONS[bucket])
    if tier in config.PAID_TIERS:
        query = query.where(Payment.tier == tier)
    page = _paginate_rows(query)
    return render_template("admin/payments.html", rows=page.items, page=page, search=search, status=bucket, tier=tier,
                           buckets=PAYMENT_BUCKETS, tiers=config.PAID_TIERS, payment_bucket=payment_bucket)


@bp.route("/payments/<int:payment_id>")
def payment_detail(payment_id):
    payment = db.get_or_404(Payment, payment_id)
    owner = db.session.get(User, payment.user_id) if payment.user_id else None
    plans = db.session.execute(db.select(WorkoutPlan.id, WorkoutPlan.week_number, WorkoutPlan.tier, WorkoutPlan.status,
                                         WorkoutPlan.created_at).where(WorkoutPlan.payment_id == payment.id)).all()
    usage = db.session.execute(db.select(AIUsage).where(AIUsage.payment_id == payment.id)
                               .order_by(AIUsage.id)).scalars().all()
    events = db.session.execute(db.select(PaymentEvent).where(PaymentEvent.gateway_order_id == payment.gateway_order_id)
                                .order_by(PaymentEvent.id)).scalars().all()
    return render_template("admin/payment_detail.html", payment=payment, owner=owner, plans=plans, usage=usage,
                           events=events, bucket=payment_bucket(payment))


@bp.route("/payments/<int:payment_id>/refund", methods=["POST"])
@limiter.limit("20 per hour")
def issue_refund(payment_id):
    """The ONLY place a refund is created: an admin approves a refund the customer requested.
    The money moves only through Razorpay's refund API; nothing is marked refunded unless
    Razorpay says so."""
    payment = db.session.get(Payment, payment_id)
    if not payment or payment.refund_status != "requested" or payment.status != "paid" or not payment.gateway_payment_id:
        flash("That refund can't be issued (not requested, or already handled).", "danger")
        return redirect(url_for("admin.finance_overview"))
    # Claim first so a double click can't send two refunds; also blocks plan generation meanwhile.
    claimed = db.session.execute(update(Payment).where(Payment.id == payment.id, Payment.refund_status == "requested")
                                 .values(refund_status="processing", status="refunded")).rowcount
    db.session.commit()
    if not claimed:
        flash("That refund is already being processed.", "info")
        return redirect(url_for("admin.finance_overview"))
    try:
        refund = billing.create_refund(payment.gateway_payment_id, payment.amount_paise,
                                       {"reason": "plan not delivered", "payment_row": str(payment.id)})
        payment.gateway_refund_id = str(refund.get("id") or "")[:64] or None
        if refund.get("status") == "processed":
            payment.refund_status, payment.refund_amount_paise, payment.refunded_at = "refunded", payment.amount_paise, utcnow()
            payment.payment_status = "refunded"
        audit.record("payment.refund", "payment", payment.id, f"{payment.amount_paise} paise, gateway status {refund.get('status')}")
        db.session.commit()
        flash("Refund sent to Razorpay.", "success")
    except billing.BillingError as exc:
        current_app.logger.error("Refund failed: %s", type(exc).__name__)
        db.session.execute(update(Payment).where(Payment.id == payment.id).values(refund_status="requested", status="paid"))
        audit.record("payment.refund_failed", "payment", payment.id, "gateway refused; state restored")
        db.session.commit()
        flash("Razorpay didn't accept the refund. Nothing was changed -- try again later.", "danger")
    return redirect(url_for("admin.finance_overview"))


# ---------------------------------------------------------------- trainers
def _trainer_coordinates(location):
    """(lat, lon) or None after flashing why. Used when a location is added/changed."""
    try:
        coordinates = trainer_service.get_coordinates(location)
    except trainer_service.GeocodingError:
        flash("The location service is not responding. Please try again in a moment.", "danger")
        return None
    if not coordinates:
        flash("Couldn't find that location. Try a more specific address.", "danger")
    return coordinates


@bp.route("/trainers")
def trainers():
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    gender = request.args.get("gender", "")
    query = db.select(Trainer).order_by(Trainer.name, Trainer.id)
    if search:
        query = query.where(db.or_(Trainer.name.ilike(_like(search), escape="\\"), Trainer.location.ilike(_like(search), escape="\\")))
    if status in ("active", "inactive"):
        query = query.where(Trainer.is_active.is_(status == "active"))
    if gender in ("male", "female", "other"):
        query = query.where(Trainer.gender == gender)
    page = _paginate(query)
    return render_template("admin/trainers.html", trainers=page.items, page=page, search=search, status=status, gender=gender)


@bp.route("/trainers/new", methods=["GET", "POST"])
def trainer_new():
    form = AdminTrainerForm()
    if form.validate_on_submit():
        coordinates = _trainer_coordinates(form.location.data.strip())
        if coordinates:
            trainer = Trainer(name=form.name.data.strip(), gender=form.gender.data, location=form.location.data.strip(),
                              latitude=coordinates[0], longitude=coordinates[1],
                              about=form.about.data.strip() if form.about.data else None,
                              contact_email=(form.contact_email.data or "").strip().lower() or None,
                              is_active=bool(form.is_active.data))
            db.session.add(trainer)
            db.session.flush()
            audit.record("trainer.create", "trainer", trainer.id, trainer.name)
            db.session.commit()
            flash(f'Trainer "{trainer.name}" added.', "success")
            return redirect(url_for("admin.trainers"))
    if request.method == "GET":
        form.is_active.data = True
    return render_template("admin/trainer_form.html", form=form, mode="new", trainer=None)


@bp.route("/trainers/<int:trainer_id>/edit", methods=["GET", "POST"])
def trainer_edit(trainer_id):
    trainer = db.get_or_404(Trainer, trainer_id)
    form = AdminTrainerForm(obj=trainer)
    if form.validate_on_submit():
        location = form.location.data.strip()
        coordinates = (trainer.latitude, trainer.longitude)
        if location != trainer.location:
            coordinates = _trainer_coordinates(location)
        if coordinates:
            trainer.name, trainer.gender, trainer.location = form.name.data.strip(), form.gender.data, location
            trainer.latitude, trainer.longitude = coordinates
            trainer.about = form.about.data.strip() if form.about.data else None
            trainer.contact_email = (form.contact_email.data or "").strip().lower() or None
            trainer.is_active = bool(form.is_active.data)
            audit.record("trainer.update", "trainer", trainer.id, trainer.name)
            db.session.commit()
            flash(f'Trainer "{trainer.name}" updated.', "success")
            return redirect(url_for("admin.trainers"))
    return render_template("admin/trainer_form.html", form=form, mode="edit", trainer=trainer)


@bp.route("/trainers/<int:trainer_id>/status", methods=["POST"])
def trainer_set_status(trainer_id):
    trainer = db.get_or_404(Trainer, trainer_id)
    activate = request.form.get("action") == "activate"
    trainer.is_active = activate
    audit.record("trainer.activate" if activate else "trainer.deactivate", "trainer", trainer.id, trainer.name)
    db.session.commit()
    flash(f'Trainer "{trainer.name}" {"activated" if activate else "deactivated"}.', "success")
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url)
                    else url_for("admin.trainers"))


@bp.route("/trainers/<int:trainer_id>/delete", methods=["POST"])
def trainer_delete(trainer_id):
    trainer = db.get_or_404(Trainer, trainer_id)
    if not _confirmed(DELETE_WORD):
        flash(f"Type {DELETE_WORD} to confirm deleting this trainer (or just deactivate them instead).", "danger")
        return redirect(url_for("admin.trainer_edit", trainer_id=trainer.id))
    name = trainer.name
    db.session.delete(trainer)
    audit.record("trainer.delete", "trainer", trainer_id, name)
    db.session.commit()
    flash(f'Trainer "{name}" deleted.', "info")
    return redirect(url_for("admin.trainers"))


# --------------------------------------------------------------- audit log
@bp.route("/audit-log")
def audit_log():
    search = request.args.get("q", "").strip()
    query = db.select(AdminAuditLog).order_by(AdminAuditLog.id.desc())
    if search:
        like = _like(search)
        query = query.where(db.or_(AdminAuditLog.action.ilike(like, escape="\\"), AdminAuditLog.admin_email.ilike(like, escape="\\"),
                                   AdminAuditLog.entity_type.ilike(like, escape="\\"), AdminAuditLog.details.ilike(like, escape="\\")))
    page = _paginate(query)
    return render_template("admin/audit_log.html", entries=page.items, page=page, search=search)
