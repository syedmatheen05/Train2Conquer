"""Shared test setup. Nothing here talks to Razorpay, OpenAI or Gemini:
all network calls are replaced by fakes, and the database is a throw-away
SQLite file. Fake keys only."""
import hashlib
import hmac
import json
import os
import sys
import tempfile
from types import SimpleNamespace

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(tempfile.mkdtemp(), "t2c_test.db")
os.environ.update(
    SUPABASE="sqlite:///" + DB_FILE, SECRET_KEY="test-secret-key-for-tests-only-123456",
    APP_ENV="development", GENERATION_MODE="worker", GEMINI_API_KEY="fake", OPENAI_API_KEY="sk-fake",
    OPENAI_USD_TO_INR="90", RAZORPAY_KEY_ID="rzp_test_FAKEKEY", RAZORPAY_KEY_SECRET="fake_key_secret",
    RAZORPAY_WEBHOOK_SECRET="fake_webhook_secret",
)
for _name in ("PERSONALIZED_PLAN_MODEL", "ADVANCED_PLAN_MODEL", "OPENAI_DAILY_LIMIT_USD",
              "OPENAI_PER_GENERATION_LIMIT_USD", "PAID_AI_MAX_ATTEMPTS", "MODEL_PRICING_JSON"):
    os.environ.pop(_name, None)
sys.path.insert(0, BASE)

import pytest  # noqa: E402

import main  # noqa: E402
import ai as ai_module  # noqa: E402
from models import (AdminAuditLog, AIUsage, FitnessProfile, OtpCode, Payment, PaymentEvent, Trainer,  # noqa: E402
                    User, WorkoutPlan, db)

main.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
ORIGINAL_CLIENT_FACTORY = main.ai_paid._client_and_model
main.limiter.enabled = False   # re-enabled only in the rate-limit test


# ------------------------------------------------------------------ helpers
def valid_plan_json(workout_days=3):
    keys = list(ai_module.exercise_keys)
    days = {}
    for i in range(1, 8):
        count = 12 if i <= workout_days else 2
        day = [["Day %d" % i, "Focus", "chest.jpg"]]
        for n in range(count):
            day.append({"exercise": keys[n % len(keys)], "reps": 10, "seconds": 30, "rest": 20})
        days["day_%d" % i] = day
    return json.dumps(days)


def fake_response(text, tokens_in=1000, tokens_out=2000, cached=0, reasoning=0, with_usage=True):
    usage = SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out,
                            input_tokens_details=SimpleNamespace(cached_tokens=cached),
                            output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning)) if with_usage else None
    return SimpleNamespace(output_text=text, usage=usage, id="resp_fake", _request_id="req_fake")


class FakeOpenAI:
    """Stands in for the OpenAI client. `script` items are responses or exceptions."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake_openai(monkeypatch):
    def install(script, model="gpt-6-luna"):
        client = FakeOpenAI(script)
        monkeypatch.setattr(main.ai_paid, "_client_and_model", lambda tier: (client, main.config.plan_model(tier)))
        return client
    return install


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    with main.app.app_context():
        for model in (AIUsage, PaymentEvent, WorkoutPlan, Payment, FitnessProfile, User, Trainer, OtpCode, AdminAuditLog):
            db.session.query(model).delete()
        db.session.commit()
    for name in ("OPENAI_DAILY_LIMIT_USD", "OPENAI_PER_GENERATION_LIMIT_USD", "PAID_AI_MAX_ATTEMPTS",
                 "OPENAI_DAILY_MAX_REQUESTS"):
        monkeypatch.delenv(name, raising=False)
    # Basic plans must never touch OpenAI: make any accidental call explode.
    def boom(tier):
        raise AssertionError("OpenAI client was created")
    monkeypatch.setattr(main.ai_paid, "_client_and_model", boom)
    # No real network: Razorpay calls are replaced per test via the `gateway` fixture.
    yield


@pytest.fixture
def client():
    return main.app.test_client()


@pytest.fixture
def make_user():
    def make(name="Test User", email="t@example.com", admin=False, profile=True, days=3):
        with main.app.app_context():
            user = User(name=name, email=email, is_admin=admin)
            db.session.add(user)
            db.session.flush()
            if profile:
                from datetime import date
                db.session.add(FitnessProfile(user_id=user.id, dob=date(2000, 1, 1), height=170, weight=70,
                                              gender="male", goal="muscle_gain", experience="beginner",
                                              progression_score=1, workout_days=days, equipment="none"))
            db.session.commit()
            return user.id
    return make


def _session_identifier(client):
    """The IP+browser fingerprint Flask-Login stores at a real login (so the test session counts as fresh)."""
    from flask_login.utils import _create_identifier
    from werkzeug.test import EnvironBuilder
    builder = EnvironBuilder(environ_base=client.environ_base)
    with main.app.request_context(builder.get_environ()):
        return _create_identifier()


def login(client, user_id):
    """Logs a user in like a real, fresh login (without the OTP e-mail step)."""
    with client.session_transaction() as s:
        s["_user_id"] = str(user_id)
        s["_fresh"] = True
        s["_id"] = _session_identifier(client)


class Gateway:
    """Fake Razorpay network layer (signature checks stay REAL)."""
    def __init__(self):
        self.orders, self.payments, self.refunds, self.captured_calls = [], {}, [], []
        self.fail_orders = False

    def create_order(self, amount, receipt, notes=None):
        if self.fail_orders:
            raise main.billing.BillingError("down")
        order = {"id": "order_%d" % (len(self.orders) + 1), "amount": amount}
        self.orders.append(order)
        return order

    def fetch_payment(self, pay_id):
        return dict(self.payments[pay_id])

    def capture_payment(self, pay_id, amount):
        self.captured_calls.append(pay_id)
        self.payments[pay_id]["status"] = "captured"
        return {}

    def create_refund(self, payment_id, amount, notes=None):
        self.refunds.append((payment_id, amount))
        return {"id": "rfnd_1", "status": "processed"}

    def add_payment(self, pay_id, order_id, amount, status="captured", currency="INR", **extra):
        self.payments[pay_id] = dict(id=pay_id, order_id=order_id, amount=amount, currency=currency,
                                     status=status, method="upi", fee=12, tax=2, **extra)


@pytest.fixture
def gateway(monkeypatch):
    gw = Gateway()
    monkeypatch.setattr(main.billing, "create_order", gw.create_order)
    monkeypatch.setattr(main.billing, "fetch_payment", gw.fetch_payment)
    monkeypatch.setattr(main.billing, "capture_payment", gw.capture_payment)
    monkeypatch.setattr(main.billing, "create_refund", gw.create_refund)
    return gw


def sign(order_id, pay_id):
    return hmac.new(b"fake_key_secret", f"{order_id}|{pay_id}".encode(), hashlib.sha256).hexdigest()


def webhook_post(client, event, event_id="evt_1", secret=b"fake_webhook_secret", signature=None):
    body = json.dumps(event).encode()
    sig = signature if signature is not None else hmac.new(secret, body, hashlib.sha256).hexdigest()
    return client.post("/razorpay/webhook", data=body, content_type="application/json",
                       headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id})


def captured_event(order_id, pay_id, amount, event="payment.captured"):
    entity = {"id": pay_id, "order_id": order_id, "amount": amount, "currency": "INR", "status": "captured",
              "method": "upi", "fee": 12, "tax": 2}
    return {"event": event, "payload": {"payment": {"entity": entity}}}


def buy(client, gateway, user_id, tier="personalized", pay_id="pay_1", notes=""):
    """Order -> (fake) checkout -> verify callback. Returns (order_response, verify_response)."""
    login(client, user_id)
    order = client.post("/payments/create-order", json={"tier": tier, "notes": notes})
    data = order.get_json()
    if not data.get("ok"):
        return order, None
    gateway.add_payment(pay_id, data["order_id"], data["amount"])
    verify = client.post("/payments/verify", json={
        "razorpay_order_id": data["order_id"], "razorpay_payment_id": pay_id,
        "razorpay_signature": sign(data["order_id"], pay_id)})
    return order, verify


@pytest.fixture
def sent_codes(monkeypatch):
    """Replaces the real e-mail sender. Returns the list of (email, code) 'sent'."""
    sent = []
    monkeypatch.setattr(main, "send_verification_code", lambda email, code: sent.append((email, code)))
    return sent


@pytest.fixture
def make_trainer():
    def make(name="Priya Sharma", gender="female", location="Koramangala", lat=12.9352, lon=77.6245,
             active=True, about="Strength coach", contact_email=None):
        with main.app.app_context():
            trainer = Trainer(name=name, gender=gender, location=location, latitude=lat, longitude=lon,
                              about=about, is_active=active, contact_email=contact_email)
            db.session.add(trainer)
            db.session.commit()
            return trainer.id
    return make
