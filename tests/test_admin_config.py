import os
import subprocess
import sys
from decimal import Decimal

import pytest

import main
import config
import costing
from conftest import BASE, buy, fake_response, login, valid_plan_json
from models import AIUsage, Payment, WorkoutPlan, db


def rows(model):
    with main.app.app_context():
        return db.session.query(model).all()


# ---- 18/19. admin dashboard + access control ----
def test_admin_pages_require_admin(client, make_user):
    for url in ("/admin", "/admin/finance"):
        assert client.get(url).status_code in (302, 401)           # anonymous
    uid = make_user(email="u@x.com"); login(client, uid)
    for url in ("/admin", "/admin/finance"):
        r = client.get(url)
        assert r.status_code in (302, 403, 404) and b"Financial Overview" not in r.data   # logged-in non-admin
    assert client.post("/admin/payments/1/refund").status_code in (302, 403, 404)


def test_admin_financial_dashboard_numbers(client, gateway, make_user, fake_openai):
    admin = make_user(name="Boss", email="boss@x.com", admin=True, profile=False)
    u1 = make_user(name="Alice", email="a@x.com"); u2 = make_user(name="Bob", email="b@x.com")
    buy(client, gateway, u1, "personalized", "pay_1"); fake_openai([fake_response(valid_plan_json(), 1000, 2000)]); main.process_queue()
    buy(client, gateway, u2, "advanced", "pay_2"); fake_openai([fake_response("bad", 10000, 5000), fake_response("bad", 10000, 5000)]); main.process_queue()
    login(client, admin)
    with main.app.app_context():
        f = main.finance.overview({})
    assert f["gross_paise"] == 2000 and f["personalized"] == {"count": 1, "paise": 500} and f["advanced"] == {"count": 1, "paise": 1500}
    luna = costing.compute_cost_usd("gpt-6-luna", 1000, 2000)
    sol = costing.compute_cost_usd("gpt-6.1-sol", 10000, 5000) * 2
    assert f["openai_usd"] == luna + sol and f["openai_requests"] == 3
    assert f["openai_inr"] == costing.usd_to_inr(luna) + costing.usd_to_inr(costing.compute_cost_usd("gpt-6.1-sol", 10000, 5000)) * 2
    assert f["generations"] == 2 and f["failed_generations"] == 1
    assert set(f["by_model"]) == {"gpt-6-luna", "gpt-6.1-sol"}
    assert f["fee_inr"] == Decimal("0.24") and f["refund_inr"] == 0
    assert f["net_estimate"] == f["remaining_before_other_expenses"] - Decimal("0.24") - Decimal("0.04")
    assert f["highest"]["tier"] == "advanced"
    page = client.get("/admin").get_data(as_text=True)
    assert "Financial Overview" in page and "DEVELOPMENT" in page and "not</strong> your bank balance" in page
    report = client.get("/admin/finance").get_data(as_text=True)
    assert "Alice" in report and "Bob" in report and "gpt-6-luna" in report
    # private fitness data is never in the finance pages
    assert "muscle_gain" not in report and "equipment" not in report.lower()


def test_admin_filters(client, gateway, make_user, fake_openai):
    admin = make_user(name="Boss", email="boss@x.com", admin=True, profile=False)
    u1 = make_user(name="Alice", email="a@x.com"); u2 = make_user(name="Bob", email="b@x.com")
    buy(client, gateway, u1, "personalized", "pay_1"); fake_openai([fake_response(valid_plan_json())]); main.process_queue()
    buy(client, gateway, u2, "advanced", "pay_2"); fake_openai([fake_response("x"), fake_response("x")]); main.process_queue()
    login(client, admin)
    only_adv = client.get("/admin/finance?plan=advanced").get_data(as_text=True)
    assert "Bob" in only_adv and "Alice" not in only_adv
    failed = client.get("/admin/finance?generation_status=failed").get_data(as_text=True)
    assert "Bob" in failed and "Alice" not in failed
    used = client.get("/admin/finance?payment_status=used").get_data(as_text=True)
    assert "Alice" in used and "Bob" not in used
    assert client.get("/admin/finance?from=2999-01-01").get_data(as_text=True).count("No transactions match") == 1
    assert client.get("/admin/finance?from=garbage&plan=hack").status_code == 200


def test_unknown_costs_are_flagged_not_zero(client, gateway, make_user, fake_openai, monkeypatch):
    monkeypatch.delenv("OPENAI_USD_TO_INR")
    admin = make_user(name="Boss", email="boss@x.com", admin=True, profile=False)
    u1 = make_user(name="Alice", email="a@x.com")
    buy(client, gateway, u1); fake_openai([fake_response(valid_plan_json())]); main.process_queue()
    with main.app.app_context():
        f = main.finance.overview({})
    assert f["openai_unknown_attempts"] == 1 and f["costs_complete"] is False
    login(client, admin)
    assert "OPENAI_USD_TO_INR is not set" in client.get("/admin").get_data(as_text=True)


# ---- 17. cost maths ----
def test_cost_maths():
    assert costing.compute_cost_usd("gpt-6-luna", 100_000, 100_000) == Decimal("0.060000")
    assert costing.compute_cost_usd("gpt-6.1-sol", 200_000, 100_000, 100_000) == Decimal("1.210000")
    assert costing.compute_cost_usd("nope", 1, 1) is None
    assert costing.compute_cost_usd("gpt-6-luna", None, 5) is None
    assert costing.compute_cost_usd("gpt-6-luna", 300_000, 1) is None      # long-context rates not configured
    assert isinstance(costing.compute_cost_usd("gpt-6-luna", 3, 3), Decimal)
    assert costing.usd_to_inr(Decimal("1.00"), Decimal("90")) == Decimal("90.0000")
    assert costing.usd_to_inr(None) is None


def test_pricing_override_from_environment(monkeypatch):
    monkeypatch.setenv("MODEL_PRICING_JSON", '{"my-model": {"input": "1", "cached_input": "0.5", "output": "2"}}')
    assert costing.compute_cost_usd("my-model", 100_000, 100_000) == Decimal("0.300000")


# ---- 20. production strictness ----
def test_production_rejects_test_keys(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert "LIVE" in config.razorpay_problem() and not config.payments_enabled()


def test_production_requires_live_key_secret_and_webhook(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_abc")
    assert config.razorpay_problem() is None and config.razorpay_mode() == "live"
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET")
    assert "WEBHOOK" in config.razorpay_problem()
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "x"); monkeypatch.delenv("RAZORPAY_KEY_SECRET")
    assert config.razorpay_problem()
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "x"); monkeypatch.setenv("RAZORPAY_KEY_ID", "garbage")
    assert config.razorpay_problem()


def test_development_allows_test_keys_but_never_silent_fallback(monkeypatch):
    assert config.razorpay_problem() is None
    monkeypatch.delenv("RAZORPAY_KEY_ID")
    assert config.razorpay_problem() and not config.payments_enabled()   # missing = error, not test mode


def test_production_order_creation_refused_with_test_key(client, gateway, make_user, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    uid = make_user(); login(client, uid)
    r = client.post("/payments/create-order", json={"tier": "advanced"})
    assert r.status_code == 503 and gateway.orders == []
    assert "rzp_" not in r.get_data(as_text=True) and "LIVE" not in r.get_data(as_text=True)   # no config details to customers
    page = client.get("/").get_data(as_text=True)
    assert "Unavailable right now" in page or "profile" in page.lower()


def test_production_webhook_and_verify_blocked_with_test_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    import billing
    assert billing.verify_webhook(b"{}", "abc") is False
    with pytest.raises(billing.BillingError):
        billing.fetch_payment("pay_1")


def _run(code, env):
    full = dict(os.environ); full.update(env)
    return subprocess.run([sys.executable, "-c", code], cwd=BASE, env=full, capture_output=True, text=True, timeout=60)


def test_production_startup_rejects_default_secret_key():
    r = _run("import main", {"APP_ENV": "production", "SECRET_KEY": "change-this-in-production"})
    assert r.returncode != 0 and "SECRET_KEY" in r.stderr


def test_production_cookies_secure_and_payments_off_with_test_key(tmp_path):
    env = {"APP_ENV": "production", "SECRET_KEY": "x" * 40, "SUPABASE": f"sqlite:///{tmp_path}/p.db", "AUTO_MIGRATE": "1"}
    r = _run("import main;print(main.app.config['SESSION_COOKIE_SECURE'], main.billing.is_configured())", env)
    assert r.returncode == 0 and r.stdout.strip().endswith("True False")
    assert "PRODUCTION" in r.stdout


def test_production_blocks_site_until_migrated(tmp_path):
    env = {"APP_ENV": "production", "SECRET_KEY": "x" * 40, "SUPABASE": f"sqlite:///{tmp_path}/q.db", "AUTO_MIGRATE": "0"}
    r = _run("import main;c=main.app.test_client();print(c.get('/').status_code, c.get('/healthz').status_code)", env)
    assert r.stdout.strip().endswith("503 200")


def test_env_example_has_no_real_secrets():
    text = open(os.path.join(BASE, ".env.example")).read()
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if "SECRET" in key or "KEY" in key or "URL" in key or key == "SUPABASE":
                assert value.strip() == "" or "your" in value.lower() or "paste" in value.lower() or "example" in value.lower() or "change" in value.lower(), line


def test_no_secret_in_frontend_files():
    for folder in ("templates", "static/js"):
        for root, _d, files in os.walk(os.path.join(BASE, folder)):
            for f in files:
                body = open(os.path.join(root, f), errors="ignore").read()
                assert "RAZORPAY_KEY_SECRET" not in body and "OPENAI_API_KEY" not in body and not __import__("re").search(r"sk-[A-Za-z0-9_-]{20,}", body)


def test_healthz(client):
    assert client.get("/healthz").get_json()["status"] == "ok"


def test_rate_limiter_on_payment_endpoints(client, gateway, make_user):
    main.limiter.enabled = True
    try:
        uid = make_user(); login(client, uid)
        codes = [client.post("/payments/create-order", json={"tier": "advanced"}).status_code for _ in range(12)]
        assert 429 in codes
    finally:
        main.limiter.enabled = False
        main.limiter.reset()
