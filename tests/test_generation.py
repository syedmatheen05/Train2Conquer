from decimal import Decimal

import pytest

import main
from conftest import FakeOpenAI, buy, fake_response, login, valid_plan_json
from models import AIUsage, Payment, WorkoutPlan, db
import costing


def rows(model):
    with main.app.app_context():
        return db.session.query(model).all()


def paid_job(client, gateway, make_user, tier="personalized"):
    uid = make_user()
    buy(client, gateway, uid, tier=tier)
    return uid


# ---- 1. free plan: Gemini only, OpenAI never touched ----
def test_basic_plan_uses_gemini_and_never_openai(client, make_user, monkeypatch):
    uid = make_user(); login(client, uid)
    monkeypatch.setattr(main, "generate_fitness_plan", lambda profile, previous_plan=None: valid_plan_json())
    assert client.post("/plans/basic").status_code == 302
    main.process_queue()                       # the autouse fixture makes any OpenAI client creation explode
    plan = rows(WorkoutPlan)[0]
    assert plan.status == "ready" and plan.ai_provider == "gemini" and plan.payment_id is None
    assert rows(AIUsage) == [] and rows(Payment) == []


def test_basic_plan_failure_is_retryable_and_free(client, make_user, monkeypatch):
    uid = make_user(); login(client, uid)
    monkeypatch.setattr(main, "generate_fitness_plan", lambda profile, previous_plan=None: "not json")
    client.post("/plans/basic"); main.process_queue()
    plan = rows(WorkoutPlan)[0]
    assert plan.status == "failed" and plan.error_message == "invalid_output"


# ---- 11/16/17. successful OpenAI generation, tokens and cost ----
def test_personalized_generation_records_tokens_and_exact_cost(client, gateway, make_user, fake_openai):
    paid_job(client, gateway, make_user)
    ai = fake_openai([fake_response(valid_plan_json(), tokens_in=1000, tokens_out=2000, cached=200, reasoning=300)])
    main.process_queue()
    plan, pay = rows(WorkoutPlan)[0], rows(Payment)[0]
    assert plan.status == "ready" and plan.generation_status == "succeeded" and pay.status == "used" and pay.used_at
    u = rows(AIUsage)
    assert len(u) == 1
    row = u[0]
    # luna: fresh 800*0.10 + cached 200*0.01 + 2000*0.50 per million = 0.001082
    assert (row.model, row.status, row.input_tokens, row.output_tokens, row.cached_input_tokens,
            row.reasoning_tokens) == ("gpt-6-luna", "success", 1000, 2000, 200, 300)
    assert row.cost_usd == Decimal("0.001082") and row.cost_inr == Decimal("0.0974") and row.request_id == "req_fake"
    assert (plan.ai_input_tokens, plan.ai_output_tokens, plan.ai_model, plan.ai_cost_complete) == (1000, 2000, "gpt-6-luna", True)
    assert plan.ai_cost_usd == Decimal("0.001082") and plan.generation_attempts == 1
    assert ai.calls[0]["max_output_tokens"] == 9000 and ai.calls[0]["model"] == "gpt-6-luna"


def test_advanced_uses_advanced_model_and_coach_notes(client, gateway, make_user, fake_openai):
    paid_job(client, gateway, make_user, tier="advanced")
    import json
    wrapped = json.dumps({"plan": json.loads(valid_plan_json()), "coach_notes": "Nice work."})
    ai = fake_openai([fake_response(wrapped, tokens_in=10000, tokens_out=5000)])
    main.process_queue()
    plan = rows(WorkoutPlan)[0]
    assert ai.calls[0]["model"] == "gpt-6.1-sol" and plan.coach_notes == "Nice work."
    assert rows(AIUsage)[0].cost_usd == Decimal("0.070000")


def test_models_come_from_environment(client, gateway, make_user, fake_openai, monkeypatch):
    monkeypatch.setenv("PERSONALIZED_PLAN_MODEL", "custom-model")
    paid_job(client, gateway, make_user)
    ai = fake_openai([fake_response(valid_plan_json())])
    main.process_queue()
    row = rows(AIUsage)[0]
    assert ai.calls[0]["model"] == "custom-model" and row.cost_usd is None and row.cost_inr is None  # unknown, not 0
    assert rows(WorkoutPlan)[0].ai_cost_complete is False


# ---- 12/15. invalid JSON: every billable attempt counted ----
def test_invalid_json_two_attempts_both_billed(client, gateway, make_user, fake_openai):
    paid_job(client, gateway, make_user)
    ai = fake_openai([fake_response("this is not json", 1000, 500), fake_response('{"day_1": []}', 1200, 600)])
    main.process_queue()
    plan, pay = rows(WorkoutPlan)[0], rows(Payment)[0]
    assert len(ai.calls) == 2                                   # attempt limit respected, no loop
    assert plan.status == "failed" and plan.error_message == "invalid_output" and pay.status == "paid"
    u = rows(AIUsage)
    assert [r.status for r in u] == ["invalid_output", "invalid_output"] and [r.attempt_number for r in u] == [1, 2]
    expected = costing.compute_cost_usd("gpt-6-luna", 1000, 500) + costing.compute_cost_usd("gpt-6-luna", 1200, 600)
    assert plan.ai_cost_usd == expected and plan.generation_attempts == 2 and plan.ai_cost_complete is True
    assert plan.ai_input_tokens == 2200 and plan.ai_output_tokens == 1100


def test_repair_attempt_can_succeed(client, gateway, make_user, fake_openai):
    paid_job(client, gateway, make_user)
    ai = fake_openai([fake_response("oops"), fake_response(valid_plan_json())])
    main.process_queue()
    assert rows(WorkoutPlan)[0].status == "ready" and len(rows(AIUsage)) == 2
    assert "rejected" in ai.calls[1]["input"]


def test_attempt_limit_is_configurable(client, gateway, make_user, fake_openai, monkeypatch):
    monkeypatch.setenv("PAID_AI_MAX_ATTEMPTS", "1")
    paid_job(client, gateway, make_user)
    ai = fake_openai([fake_response("bad"), fake_response(valid_plan_json())])
    main.process_queue()
    assert len(ai.calls) == 1 and rows(WorkoutPlan)[0].status == "failed"


# ---- 13. timeout / error before usage: cost UNKNOWN and no blind retry ----
def test_timeout_records_unknown_cost_and_stops(client, gateway, make_user, fake_openai):
    class APITimeoutError(Exception): pass
    paid_job(client, gateway, make_user)
    ai = fake_openai([APITimeoutError("t"), fake_response(valid_plan_json())])
    main.process_queue()
    plan = rows(WorkoutPlan)[0]
    assert len(ai.calls) == 1 and plan.status == "failed" and plan.error_message == "timeout"
    row = rows(AIUsage)[0]
    assert row.status == "error" and row.input_tokens is None and row.cost_usd is None and row.error_type == "APITimeoutError"
    assert plan.ai_cost_complete is False


def test_sdk_hidden_retries_are_disabled(monkeypatch):
    from conftest import ORIGINAL_CLIENT_FACTORY
    seen = {}
    class Capture:
        def __init__(self, **kw): seen.update(kw)
    import openai
    monkeypatch.setattr(openai, "OpenAI", Capture)
    ORIGINAL_CLIENT_FACTORY("personalized")
    assert seen["max_retries"] == 0 and seen["api_key"] == "sk-fake"


# ---- 14. retry without paying again ----
def test_retry_after_failure_reuses_payment(client, gateway, make_user, fake_openai):
    uid = paid_job(client, gateway, make_user)
    fake_openai([fake_response("bad"), fake_response("bad")])
    main.process_queue()
    plan_id = rows(WorkoutPlan)[0].id
    login(client, uid)
    assert client.post(f"/plans/{plan_id}/retry").status_code == 302
    assert rows(WorkoutPlan)[0].status == "generating" and rows(Payment)[0].attempts == 2
    fake_openai([fake_response(valid_plan_json())])
    main.process_queue()
    assert rows(WorkoutPlan)[0].status == "ready" and len(rows(Payment)) == 1 and rows(Payment)[0].status == "used"
    assert len(rows(AIUsage)) == 3 and len(gateway.orders) == 1        # no new order, all 3 attempts recorded


def test_retries_are_capped(client, gateway, make_user, fake_openai):
    uid = paid_job(client, gateway, make_user)
    login(client, uid)
    for _ in range(main.MAX_GENERATION_ATTEMPTS):
        fake_openai([fake_response("bad"), fake_response("bad")]); main.process_queue()
        pid = rows(WorkoutPlan)[0].id
        client.post(f"/plans/{pid}/retry")
    assert rows(Payment)[0].attempts == main.MAX_GENERATION_ATTEMPTS
    assert rows(WorkoutPlan)[0].status == "failed" or rows(WorkoutPlan)[0].status == "generating"


def test_paid_plan_never_generated_without_verified_payment(client, make_user, fake_openai):
    uid = make_user()
    with main.app.app_context():
        p = Payment(user_id=uid, tier="advanced", amount_paise=1500, gateway_order_id="order_fake", status="created")
        db.session.add(p); db.session.flush()
        db.session.add(WorkoutPlan(user_id=uid, plan="{}", status="generating", generation_status="queued",
                                   tier="advanced", payment_id=p.id)); db.session.commit()
    ai = fake_openai([fake_response(valid_plan_json())])
    main.process_queue()
    assert ai.calls == [] and rows(WorkoutPlan)[0].status == "failed"


# ---- spending limits ----
def test_daily_limit_blocks_openai_call(client, gateway, make_user, fake_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_DAILY_LIMIT_USD", "0.001")
    uid = paid_job(client, gateway, make_user)
    with main.app.app_context():
        db.session.add(AIUsage(user_id=uid, model="gpt-6-luna", status="success", cost_usd=Decimal("0.01"), attempt_number=1))
        db.session.commit()
    ai = fake_openai([fake_response(valid_plan_json())])
    main.process_queue()
    plan = rows(WorkoutPlan)[0]
    assert ai.calls == [] and plan.status == "failed" and plan.error_message == "budget" and rows(Payment)[0].status == "paid"


def test_per_generation_limit_stops_repairs(client, gateway, make_user, fake_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_PER_GENERATION_LIMIT_USD", "0.0001")
    paid_job(client, gateway, make_user)
    ai = fake_openai([fake_response("bad", 1000, 2000), fake_response(valid_plan_json())])
    main.process_queue()
    assert len(ai.calls) == 1 and rows(WorkoutPlan)[0].status == "failed"


def test_duplicate_runners_cannot_double_generate(client, gateway, make_user, fake_openai):
    paid_job(client, gateway, make_user)
    pid = rows(WorkoutPlan)[0].id
    with main.app.app_context():
        first, second = main.claim_job(pid), main.claim_job(pid)
    assert first and second is None


def test_stale_running_job_becomes_retryable(client, gateway, make_user):
    from datetime import timedelta
    from models import utcnow
    paid_job(client, gateway, make_user)
    pid = rows(WorkoutPlan)[0].id
    with main.app.app_context():
        main.claim_job(pid)
        db.session.query(WorkoutPlan).update({"locked_at": utcnow() - timedelta(hours=1)}); db.session.commit()
        main.expire_stale_generations()
    plan = rows(WorkoutPlan)[0]
    assert plan.status == "failed" and plan.error_message == "stale" and rows(Payment)[0].status == "paid"


def test_users_never_see_model_or_provider_names(client, gateway, make_user, fake_openai):
    uid = paid_job(client, gateway, make_user)
    fake_openai([fake_response("bad"), fake_response("bad")]); main.process_queue()
    login(client, uid)
    html = client.get("/").get_data(as_text=True).lower()
    for word in ("gpt", "openai", "gemini", "luna", "sol "):
        assert word not in html
    assert "retry" in html and "refund" in html
