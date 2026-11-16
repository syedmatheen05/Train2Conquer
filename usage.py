"""Recording what every OpenAI request cost.

Each API attempt becomes its own `ai_usage` row, saved the moment the answer
arrives - BEFORE the app checks whether the JSON was any good - so a rejected
answer or a crash afterwards can never lose a charge. If OpenAI never sent
usage numbers back, tokens and cost stay NULL (= unknown), never 0.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func

import config
import costing
from models import AIUsage, WorkoutPlan, db


def record_attempt(*, user_id, plan_id, payment_id, model, tier, attempt_number, status,
                   error_type=None, request_id=None, input_tokens=None, output_tokens=None,
                   cached_input_tokens=None, reasoning_tokens=None):
    """Save one API attempt and commit immediately. Never raises."""
    cost_usd = costing.compute_cost_usd(model, input_tokens, output_tokens, cached_input_tokens or 0)
    rate = config.usd_to_inr()
    row = AIUsage(
        user_id=user_id, workout_plan_id=plan_id, payment_id=payment_id, provider="openai",
        model=model, tier=tier, attempt_number=attempt_number, request_id=(request_id or None),
        input_tokens=input_tokens, output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens, reasoning_tokens=reasoning_tokens,
        cost_usd=cost_usd, cost_inr=costing.usd_to_inr(cost_usd, rate), usd_to_inr_rate=rate,
        status=status, error_type=(error_type or None),
    )
    for _ in range(2):                      # one quiet retry: this row is money
        try:
            db.session.add(row)
            db.session.commit()
            return row
        except Exception as exc:
            db.session.rollback()
            print("AI USAGE SAVE ERROR:", type(exc).__name__)
    return None


def _day_start():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def today_totals():
    """(known_cost_usd, request_count, unknown_cost_requests) since 00:00 UTC."""
    since = _day_start()
    cost, count, unknown = db.session.execute(
        db.select(
            func.coalesce(func.sum(AIUsage.cost_usd), 0),
            func.count(AIUsage.id),
            func.coalesce(func.sum(db.case((AIUsage.cost_usd.is_(None), 1), else_=0)), 0),
        ).where(AIUsage.created_at >= since)).one()
    return Decimal(str(cost or 0)), int(count or 0), int(unknown or 0)


def daily_limit_reached():
    """True when today's OpenAI spending (or request count) hit the configured limit."""
    cost, count, _unknown = today_totals()
    limit = config.daily_ai_limit_usd()
    if limit is not None and cost >= limit:
        return True
    return count >= config.daily_ai_request_limit()


def plan_totals(plan_id):
    """Sum of all attempts for one plan: dict with tokens, known cost, attempts, completeness."""
    rows = db.session.execute(db.select(AIUsage).where(AIUsage.workout_plan_id == plan_id)).scalars().all()
    known_usd = sum((r.cost_usd for r in rows if r.cost_usd is not None), Decimal(0))
    known_inr = sum((r.cost_inr for r in rows if r.cost_inr is not None), Decimal(0))
    return {
        "attempts": len(rows),
        "input_tokens": sum(r.input_tokens or 0 for r in rows),
        "output_tokens": sum(r.output_tokens or 0 for r in rows),
        "cost_usd": Decimal(str(known_usd)),
        "cost_inr": Decimal(str(known_inr)),
        "complete": bool(rows) and all(r.cost_usd is not None and (r.cost_inr is not None) for r in rows),
        "has_rows": bool(rows),
        "model": rows[-1].model if rows else None,
    }


def refresh_plan_totals(plan_id):
    """Copy the per-attempt totals onto the workout plan row (no commit)."""
    plan = db.session.get(WorkoutPlan, plan_id)
    if plan is None:
        return
    totals = plan_totals(plan_id)
    if not totals["has_rows"]:
        return
    plan.ai_provider = "openai"
    plan.ai_model = totals["model"]
    plan.ai_input_tokens = totals["input_tokens"]
    plan.ai_output_tokens = totals["output_tokens"]
    plan.ai_cost_usd = totals["cost_usd"]
    plan.ai_cost_inr = totals["cost_inr"]
    plan.ai_cost_complete = totals["complete"]
    plan.generation_attempts = totals["attempts"]


def mark_attempt(row, status, error_type=None):
    """Change the status of an attempt that was already saved. Never raises."""
    if row is None:
        return
    try:
        row.status = status
        row.error_type = error_type
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print("AI USAGE UPDATE ERROR:", type(exc).__name__)
