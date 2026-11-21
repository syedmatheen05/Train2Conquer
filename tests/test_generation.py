"""Plan generation: ALL three plan types use Gemini (gemini_service), nothing else.

Covers the free Basic plan, paid Personalized/Advanced plans (only after a verified
payment), Gemini error handling, retries, and the money/job-safety guarantees.
"""
import json
import pathlib
import re
from decimal import Decimal

import pytest

import gemini_service
import main
from conftest import ApiError, FakeGemini, buy, fake_response, login, valid_plan_json
from models import AIUsage, Payment, WorkoutPlan, db


def rows(model):
    with main.app.app_context():
        return db.session.query(model).all()


def paid_job(client, gateway, make_user, tier="personalized"):
    uid = make_user()
    buy(client, gateway, uid, tier=tier)
    return uid


def free_job(client, make_user):
    uid = make_user()
    login(client, uid)
    client.post("/plans/basic")
    return uid


# ---- 1. all three plan types use Gemini ----
def test_basic_plan_uses_gemini(client, make_user, fake_gemini):
    ai = fake_gemini([fake_response(valid_plan_json())])
    free_job(client, make_user)
    main.process_queue()
    plan = rows(WorkoutPlan)[0]
    assert plan.status == "ready" and plan.tier == "basic" and plan.ai_provider == "gemini" and len(ai.calls) == 1
    assert ai.calls[0]["model"] == "gemini-3.1-flash-lite"


def test_personalized_plan_uses_gemini_after_payment(client, gateway, make_user, fake_gemini):
    paid_job(client, gateway, make_user, "personalized")
    ai = fake_gemini([fake_response(valid_plan_json(), tokens_in=1000, tokens_out=2000, cached=200, reasoning=300)])
    main.process_queue()
    plan, usage = rows(WorkoutPlan)[0], rows(AIUsage)[0]
    assert plan.status == "ready" and plan.ai_provider == "gemini" and rows(Payment)[0].status == "used"
    assert (usage.provider, usage.input_tokens, usage.output_tokens, usage.cached_input_tokens, usage.reasoning_tokens) == \
        ("gemini", 1000, 2000, 200, 300)
    assert usage.cost_usd == Decimal(0) and usage.cost_inr == Decimal(0)       # free tier: exactly zero, not unknown
    assert "PERSONALIZED PLAN RULES" in ai.calls[0]["instructions"]


def test_advanced_plan_uses_gemini_and_keeps_coach_notes(client, gateway, make_user, fake_gemini):
    paid_job(client, gateway, make_user, "advanced")
    wrapped = json.dumps({"plan": json.loads(valid_plan_json()), "coach_notes": "  Nice work. We stepped up the volume.  "})
    ai = fake_gemini([fake_response(wrapped)])
    main.process_queue()
    plan = rows(WorkoutPlan)[0]
    assert plan.status == "ready" and plan.tier == "advanced" and plan.ai_provider == "gemini"
    assert plan.coach_notes == "Nice work. We stepped up the volume."
    assert "ADVANCED PLAN RULES" in ai.calls[0]["instructions"] and '"HISTORY"' in ai.calls[0]["input"]


def test_plan_types_keep_their_own_prompts_and_history(client, gateway, make_user, fake_gemini):
    free_ai = fake_gemini([fake_response(valid_plan_json())])
    free_job(client, make_user); main.process_queue()
    p = free_ai.calls[0]
    assert "BASIC PLAN RULES" in p["instructions"] and '"PREVIOUS_PLAN"' in p["input"] and "USER_NOTES" not in p["input"]
    assert "PERSONALIZED PLAN RULES" not in p["instructions"] and "ADVANCED PLAN RULES" not in p["instructions"]


def test_model_can_be_set_from_environment(client, gateway, make_user, fake_gemini, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-custom")
    paid_job(client, gateway, make_user)
    ai = fake_gemini([fake_response(valid_plan_json())])
    main.process_queue()
    assert ai.calls[0]["model"] == "gemini-custom" and rows(AIUsage)[0].model == "gemini-custom"


def test_no_openai_anywhere_in_the_application():
    root = pathlib.Path(main.__file__).parent
    offenders = []
    for path in list(root.glob("*.py")) + list((root / "templates").rglob("*.html")) + list((root / "static").rglob("*.js")) \
            + [root / "requirements.txt", root / ".env.example", root / "render.yaml.example"]:
        if path.name == "migrations.py":          # one historical data label for old rows, documented there
            continue
        if re.search(r"openai", path.read_text(errors="ignore"), re.I):
            offenders.append(path.name)
    assert offenders == []


# ---- 2. malformed / incomplete answers ----
def test_invalid_json_two_attempts_both_recorded(client, gateway, make_user, fake_gemini):
    paid_job(client, gateway, make_user)
    ai = fake_gemini([fake_response("this is not json", 1000, 500), fake_response('{"day_1": []}', 1200, 600)])
    main.process_queue()
    plan, usage = rows(WorkoutPlan)[0], rows(AIUsage)
    assert plan.status == "failed" and plan.error_message == "invalid_output" and rows(Payment)[0].status == "paid"
    assert len(ai.calls) == 2 and [u.status for u in usage] == ["invalid_output", "invalid_output"]
    assert "rejected" in ai.calls[1]["input"]


def test_repair_attempt_can_succeed(client, gateway, make_user, fake_gemini):
    paid_job(client, gateway, make_user)
    ai = fake_gemini([fake_response("oops"), fake_response(valid_plan_json())])
    main.process_queue()
    assert rows(WorkoutPlan)[0].status == "ready" and len(ai.calls) == 2


def test_attempt_limit_is_configurable(client, gateway, make_user, fake_gemini, monkeypatch):
    monkeypatch.setenv("PAID_AI_MAX_ATTEMPTS", "1")
    paid_job(client, gateway, make_user)
    ai = fake_gemini([fake_response("bad"), fake_response(valid_plan_json())])
    main.process_queue()
    assert len(ai.calls) == 1 and rows(WorkoutPlan)[0].status == "failed"


def test_answer_wrapped_in_code_fence_or_text_is_still_read(client, make_user, fake_gemini):
    fake_gemini([fake_response("```json\n" + valid_plan_json() + "\n```")])
    free_job(client, make_user); main.process_queue()
    assert rows(WorkoutPlan)[0].status == "ready"


def test_extract_json_object_handles_messy_answers():
    assert gemini_service.extract_json_object('Sure! {"a": 1} Hope that helps')["a"] == 1
    for bad in ("", "   ", None, "[1, 2]", "no json", '{"a": '):
        with pytest.raises(ValueError):
            gemini_service.extract_json_object(bad)


def test_incomplete_plan_is_rejected(client, make_user, fake_gemini):
    plan = json.loads(valid_plan_json()); del plan["day_7"]
    fake_gemini([fake_response(json.dumps(plan)), fake_response(json.dumps(plan))])
    free_job(client, make_user); main.process_queue()
    assert rows(WorkoutPlan)[0].status == "failed" and rows(WorkoutPlan)[0].error_message == "invalid_output"


# ---- 3. Gemini errors: transient retried (bounded), permanent not ----
def test_rate_limit_is_retried_then_succeeds(client, make_user, fake_gemini):
    ai = fake_gemini([ApiError(429), ApiError(503), fake_response(valid_plan_json())])
    free_job(client, make_user); main.process_queue()
    assert rows(WorkoutPlan)[0].status == "ready" and len(ai.calls) == 3


def test_retries_are_bounded_and_end_cleanly(client, make_user, fake_gemini):
    ai = fake_gemini([ApiError(429)] * 10)
    free_job(client, make_user); main.process_queue()
    plan = rows(WorkoutPlan)[0]
    assert len(ai.calls) == 3 and plan.status == "failed" and plan.error_message == "rate_limit"   # GEMINI_MAX_TRIES default


def test_permanent_errors_are_not_retried(client, make_user, fake_gemini):
    ai = fake_gemini([ApiError(403), fake_response(valid_plan_json())])
    free_job(client, make_user); main.process_queue()
    assert len(ai.calls) == 1 and rows(WorkoutPlan)[0].error_message == "config"


def test_timeout_is_reported_as_timeout(client, make_user, fake_gemini):
    class ReadTimeout(Exception): pass
    fake_gemini([ReadTimeout("t")] * 3)
    free_job(client, make_user); main.process_queue()
    assert rows(WorkoutPlan)[0].status == "failed" and rows(WorkoutPlan)[0].error_message == "timeout"


def test_empty_answer_is_retried_then_fails_safely(client, make_user, fake_gemini):
    ai = fake_gemini([fake_response(""), fake_response(None), fake_response("")])
    free_job(client, make_user); main.process_queue()
    assert len(ai.calls) == 3 and rows(WorkoutPlan)[0].error_message == "invalid_output"


def test_missing_api_key_is_a_config_error(client, make_user, monkeypatch):
    monkeypatch.undo()                 # drop the autouse fake client so the real key check runs
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(gemini_service.GeminiError) as caught:
        gemini_service._client()
    assert caught.value.code == "config"


def test_logs_never_contain_the_key_or_prompt(client, make_user, fake_gemini, capsys):
    fake_gemini([ApiError(500, "boom with secret-key-123 and user data")] * 3)
    free_job(client, make_user); main.process_queue()
    out = capsys.readouterr()
    assert "secret-key-123" not in out.out + out.err and "boom" not in out.out + out.err and "USER_PROFILE" not in out.out + out.err


# ---- 4. retry without paying again ----
def test_retry_after_failure_reuses_payment(client, gateway, make_user, fake_gemini):
    uid = paid_job(client, gateway, make_user)
    fake_gemini([fake_response("bad"), fake_response("bad")])
    main.process_queue()
    plan_id = rows(WorkoutPlan)[0].id
    login(client, uid)
    assert client.post(f"/plans/{plan_id}/retry").status_code == 302
    assert rows(WorkoutPlan)[0].status == "generating" and rows(Payment)[0].attempts == 2
    fake_gemini([fake_response(valid_plan_json())])
    main.process_queue()
    assert rows(WorkoutPlan)[0].status == "ready" and len(rows(Payment)) == 1 and rows(Payment)[0].status == "used"
    assert len(rows(AIUsage)) == 3 and len(gateway.orders) == 1        # no new order, all 3 requests recorded


def test_retries_are_capped(client, gateway, make_user, fake_gemini):
    uid = paid_job(client, gateway, make_user)
    login(client, uid)
    for _ in range(main.MAX_GENERATION_ATTEMPTS):
        fake_gemini([fake_response("bad"), fake_response("bad")]); main.process_queue()
        client.post(f"/plans/{rows(WorkoutPlan)[0].id}/retry")
    assert rows(Payment)[0].attempts == main.MAX_GENERATION_ATTEMPTS
    assert rows(WorkoutPlan)[0].status in ("failed", "generating")


# ---- 5. payment gate ----
def test_paid_plan_never_generated_without_verified_payment(client, make_user, fake_gemini):
    uid = make_user()
    with main.app.app_context():
        p = Payment(user_id=uid, tier="advanced", amount_paise=1500, gateway_order_id="order_fake", status="created")
        db.session.add(p); db.session.flush()
        db.session.add(WorkoutPlan(user_id=uid, plan="{}", status="generating", generation_status="queued",
                                   tier="advanced", payment_id=p.id)); db.session.commit()
    ai = fake_gemini([fake_response(valid_plan_json())])
    main.process_queue()
    assert ai.calls == [] and rows(WorkoutPlan)[0].status == "failed"


def test_prices_are_unchanged():
    assert main.billing.price_paise("personalized") == 500 and main.billing.price_paise("advanced") == 1500


# ---- 6. safety limits and job safety ----
def test_daily_request_limit_blocks_the_call(client, gateway, make_user, fake_gemini, monkeypatch):
    monkeypatch.setenv("GEMINI_DAILY_MAX_REQUESTS", "1")
    paid_job(client, gateway, make_user)
    fake_gemini([fake_response("bad", 1, 1), fake_response("bad", 1, 1)])
    main.process_queue()                       # first request is allowed (and rejected as bad); the repair is blocked
    plan = rows(WorkoutPlan)[0]
    assert plan.status == "failed" and plan.error_message == "budget" and len(rows(AIUsage)) == 1


def test_duplicate_runners_cannot_double_generate(client, gateway, make_user, fake_gemini):
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


def test_users_never_see_model_or_provider_names(client, gateway, make_user, fake_gemini):
    uid = paid_job(client, gateway, make_user)
    fake_gemini([fake_response("bad"), fake_response("bad")]); main.process_queue()
    login(client, uid)
    html = client.get("/").get_data(as_text=True).lower()
    for word in ("gpt", "openai", "gemini", "flash-lite"):
        assert word not in html
    assert "retry" in html and "refund" in html
