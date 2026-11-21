"""Numbers for the admin Financial Overview.

Read-only. Everything is calculated from the database rows the app itself
saved (payments, ai_usage, workout_plans). Nothing here is a bank balance:
the "estimated" figures are calculations, not Razorpay settlements.

Money: payment amounts are integer paise. API costs are Decimal dollars/rupees.
Unknown values are reported as unknown - never silently counted as zero.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from models import AIUsage, Payment, User, WorkoutPlan, db

PLAN_CHOICES = ("personalized", "advanced")
PAYMENT_STATUS_CHOICES = ("created", "paid", "used", "cancelled", "refunded")
GENERATION_STATUS_CHOICES = ("queued", "running", "succeeded", "failed")
PAGE_SIZE = 25


def _d(value):
    return Decimal(str(value)) if value is not None else Decimal(0)


def parse_filters(args):
    """Turns request.args into a clean, validated filter dict."""
    def parse_day(name):
        raw = (args.get(name) or "").strip()
        try:
            return datetime.strptime(raw, "%Y-%m-%d") if raw else None
        except ValueError:
            return None
    date_from, date_to = parse_day("from"), parse_day("to")
    plan = args.get("plan") if args.get("plan") in PLAN_CHOICES else None
    pay = args.get("payment_status") if args.get("payment_status") in PAYMENT_STATUS_CHOICES else None
    gen = args.get("generation_status") if args.get("generation_status") in GENERATION_STATUS_CHOICES else None
    try:
        page = max(1, int(args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    return {"from": date_from, "to": date_to, "plan": plan, "payment_status": pay,
            "generation_status": gen, "page": page}


def _payment_conditions(f):
    conds = []
    if f.get("from"):
        conds.append(Payment.created_at >= f["from"])
    if f.get("to"):
        conds.append(Payment.created_at < f["to"] + timedelta(days=1))
    if f.get("plan"):
        conds.append(Payment.tier == f["plan"])
    if f.get("payment_status"):
        conds.append(Payment.status == f["payment_status"])
    return conds


def _captured():
    """Payments where money was really received (later refunds still count as gross)."""
    return Payment.payment_verification_status == "verified"


def overview(filters=None):
    """All headline numbers as one dict. Honours the date range and plan filters."""
    f = filters or {}
    conds = _payment_conditions(f)

    # --- customer payments (gross) ---
    rows = db.session.execute(
        db.select(Payment.tier, func.count(Payment.id), func.coalesce(func.sum(Payment.amount_paise), 0))
        .where(_captured(), *conds).group_by(Payment.tier)).all()
    sales = {t: {"count": int(c), "paise": int(s)} for t, c, s in rows}
    gross_paise = sum(v["paise"] for v in sales.values())
    gross_count = sum(v["count"] for v in sales.values())

    # --- refunds and fees (only what Razorpay/our records actually state) ---
    refund_paise, fee_paise, tax_paise, fee_known, fee_unknown = db.session.execute(
        db.select(
            func.coalesce(func.sum(Payment.refund_amount_paise), 0),
            func.coalesce(func.sum(Payment.fee_paise), 0),
            func.coalesce(func.sum(Payment.tax_paise), 0),
            func.coalesce(func.sum(db.case((Payment.fee_paise.is_not(None), 1), else_=0)), 0),
            func.coalesce(func.sum(db.case((Payment.fee_paise.is_(None), 1), else_=0)), 0),
        ).where(_captured(), *conds)).one()
    refund_requests = db.session.execute(
        db.select(func.count(Payment.id)).where(Payment.refund_status.in_(("requested", "processing")), *conds)).scalar_one()

    # --- AI usage (every Gemini request; free tier = 0) ---
    uconds = []
    if f.get("from"):
        uconds.append(AIUsage.created_at >= f["from"])
    if f.get("to"):
        uconds.append(AIUsage.created_at < f["to"] + timedelta(days=1))
    if f.get("plan"):
        uconds.append(AIUsage.tier == f["plan"])
    usage_rows = db.session.execute(
        db.select(
            AIUsage.model, AIUsage.tier,
            func.count(AIUsage.id),
            func.coalesce(func.sum(AIUsage.input_tokens), 0),
            func.coalesce(func.sum(AIUsage.output_tokens), 0),
            func.coalesce(func.sum(AIUsage.cost_usd), 0),
            func.coalesce(func.sum(AIUsage.cost_inr), 0),
            func.coalesce(func.sum(db.case((db.or_(AIUsage.cost_usd.is_(None), AIUsage.cost_inr.is_(None)), 1), else_=0)), 0),
            func.coalesce(func.sum(db.case((AIUsage.status != "success", 1), else_=0)), 0),
        ).where(*uconds).group_by(AIUsage.model, AIUsage.tier)).all()
    by_model, by_tier = {}, {}
    total_usd = total_inr = Decimal(0)
    unknown_attempts = requests_total = 0
    for model, tier, n, tin, tout, usd, inr, unk, bad in usage_rows:
        m = by_model.setdefault(model, {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                                         "usd": Decimal(0), "inr": Decimal(0), "unknown": 0, "rejected": 0})
        m["requests"] += int(n); m["input_tokens"] += int(tin); m["output_tokens"] += int(tout)
        m["usd"] += _d(usd); m["inr"] += _d(inr); m["unknown"] += int(unk); m["rejected"] += int(bad)
        t = by_tier.setdefault(tier or "unknown", {"usd": Decimal(0), "inr": Decimal(0), "requests": 0})
        t["usd"] += _d(usd); t["inr"] += _d(inr); t["requests"] += int(n)
        total_usd += _d(usd); total_inr += _d(inr)
        unknown_attempts += int(unk); requests_total += int(n)

    # --- generations (one paid plan job = one generation) ---
    pconds = []
    if f.get("from"):
        pconds.append(Payment.created_at >= f["from"])
    if f.get("to"):
        pconds.append(Payment.created_at < f["to"] + timedelta(days=1))
    if f.get("plan"):
        pconds.append(Payment.tier == f["plan"])
    plans = db.session.execute(
        db.select(WorkoutPlan.id, WorkoutPlan.tier, WorkoutPlan.generation_status, WorkoutPlan.status,
                  WorkoutPlan.ai_cost_usd, WorkoutPlan.ai_cost_complete, WorkoutPlan.created_at)
        .join(Payment, Payment.id == WorkoutPlan.payment_id).where(*pconds)).all()
    generations = len(plans)
    failed = sum(1 for p in plans if (p.generation_status or p.status) == "failed" or p.status == "failed")
    complete = [p for p in plans if p.ai_cost_usd is not None and p.ai_cost_complete]
    avg_cost = (sum((_d(p.ai_cost_usd) for p in complete), Decimal(0)) / len(complete)) if complete else None
    top = max(plans, key=lambda p: _d(p.ai_cost_usd), default=None)
    highest = ({"plan_id": top.id, "tier": top.tier, "usd": _d(top.ai_cost_usd), "when": top.created_at}
               if top is not None and top.ai_cost_usd is not None else None)

    # --- derived (ESTIMATES, not bank balances) ---
    inr_complete = unknown_attempts == 0
    gross_inr = Decimal(gross_paise) / 100
    refunds_inr = Decimal(int(refund_paise)) / 100
    fee_inr = Decimal(int(fee_paise)) / 100
    tax_inr = Decimal(int(tax_paise)) / 100
    remaining = gross_inr - refunds_inr - total_inr
    net_estimate = remaining - fee_inr - tax_inr
    return {
        "gross_paise": gross_paise, "gross_count": gross_count, "sales": sales,
        "personalized": sales.get("personalized", {"count": 0, "paise": 0}),
        "advanced": sales.get("advanced", {"count": 0, "paise": 0}),
        "ai_usd": total_usd, "ai_inr": total_inr, "ai_unknown_attempts": unknown_attempts,
        "ai_requests": requests_total, "by_tier": by_tier, "by_model": by_model,
        "fee_inr": fee_inr, "tax_inr": tax_inr, "fee_reported": int(fee_known), "fee_not_reported": int(fee_unknown),
        "refund_inr": refunds_inr, "refund_requests_open": int(refund_requests),
        "remaining_before_other_expenses": remaining, "net_estimate": net_estimate,
        "costs_complete": inr_complete and int(fee_unknown) == 0,
        "generations": generations, "failed_generations": failed,
        "avg_cost_usd": avg_cost, "highest": highest,
    }


def transactions(filters):
    """One page of payment rows for the table, plus the total row count."""
    f = filters
    conds = _payment_conditions(f)
    gen_filter = []
    if f.get("generation_status"):
        gen_filter.append(WorkoutPlan.generation_status == f["generation_status"])
    base = (db.select(Payment, User.name, User.email, WorkoutPlan)
            .outerjoin(User, User.id == Payment.user_id)
            .outerjoin(WorkoutPlan, WorkoutPlan.payment_id == Payment.id)
            .where(*conds, *gen_filter))
    total = db.session.execute(
        db.select(func.count()).select_from(base.order_by(None).subquery())).scalar_one()
    rows = db.session.execute(
        base.order_by(Payment.id.desc()).limit(PAGE_SIZE).offset((f["page"] - 1) * PAGE_SIZE)).all()
    items = []
    for payment, name, email, plan in rows:
        items.append({
            "payment": payment,
            "customer": name or "(deleted account)",
            "email": email,
            "plan": plan,
            "model": plan.ai_model if plan else None,
            "input_tokens": plan.ai_input_tokens if plan else None,
            "output_tokens": plan.ai_output_tokens if plan else None,
            "cost_usd": plan.ai_cost_usd if plan else None,
            "cost_inr": plan.ai_cost_inr if plan else None,
            "cost_complete": plan.ai_cost_complete if plan else None,
            "generation": (plan.generation_status or plan.status) if plan else "not started",
        })
    return items, int(total)
