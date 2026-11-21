"""Failed / cancelled / pending payments: Retry, Try another payment method, refunds.

Nothing here talks to Razorpay: the `gateway` fixture fakes it (signature checks stay real).
"""
import main
from conftest import buy, login, sign
from models import Payment, WorkoutPlan, db


def order(client, **extra):
    return client.post("/payments/create-order", json={"tier": "personalized", **extra}).get_json()


def payments():
    with main.app.app_context():
        return db.session.query(Payment).order_by(Payment.id).all()


def plans():
    with main.app.app_context():
        return db.session.query(WorkoutPlan).all()


def start(client, make_user):
    uid = make_user()
    login(client, uid)
    return uid


def test_failed_attempt_is_confirmed_with_razorpay_and_unlocks_nothing(client, gateway, make_user):
    start(client, make_user)
    first = order(client)
    gateway.add_payment("pay_f1", first["order_id"], first["amount"], status="failed", error_code="BAD_REQUEST_ERROR")
    gateway.payments["pay_f1"]["method"] = "card"
    r = client.post("/payments/failed", json={"razorpay_order_id": first["order_id"]}).get_json()
    assert r["ok"] and r["state"] == "failed" and r["reference"] == first["order_id"] and r["method"] == "card"
    p = payments()[0]
    assert p.status == "created" and p.payment_status == "failed" and p.failure_code == "BAD_REQUEST_ERROR"
    assert plans() == []                                       # never a plan without a verified payment


def test_retry_reuses_the_same_order_even_after_the_window_was_closed(client, gateway, make_user):
    start(client, make_user)
    first = order(client)
    client.post("/payments/cancel", json={"razorpay_order_id": first["order_id"]})
    assert payments()[0].status == "cancelled"
    again = order(client, retry=True, order_id=first["order_id"])
    assert again["ok"] and again["order_id"] == first["order_id"] and len(gateway.orders) == 1
    assert payments()[0].status == "created"


def test_try_another_method_makes_a_fresh_order_and_retires_the_old_one(client, gateway, make_user):
    start(client, make_user)
    first = order(client)
    gateway.add_payment("pay_f1", first["order_id"], first["amount"], status="failed")
    fresh = order(client, fresh=True)
    assert fresh["ok"] and fresh["order_id"] != first["order_id"] and len(gateway.orders) == 2
    old, new = payments()
    assert old.status == "cancelled" and old.failure_code == "failed" and new.status == "created"
    assert plans() == []


def test_fresh_order_never_abandons_a_payment_that_actually_succeeded(client, gateway, make_user):
    uid = start(client, make_user)
    first = order(client)
    gateway.add_payment("pay_ok", first["order_id"], first["amount"], status="captured")    # browser missed it
    r = order(client, fresh=True)
    assert r["ok"] is False and r["state"] == "already_paid"
    assert len(gateway.orders) == 1                                       # no second order, no second charge
    p = payments()[0]
    assert p.status == "paid" and p.payment_verification_status == "verified" and p.gateway_payment_id == "pay_ok"
    assert len(plans()) == 1


def test_fresh_order_waits_while_an_earlier_payment_is_pending(client, gateway, make_user):
    start(client, make_user)
    first = order(client)
    gateway.add_payment("pay_p", first["order_id"], first["amount"], status="authorized")
    r = client.post("/payments/create-order", json={"tier": "personalized", "fresh": True})
    assert r.status_code == 409 and r.get_json()["state"] == "pending" and len(gateway.orders) == 1


def test_gateway_outage_changes_nothing_and_assumes_nothing(client, gateway, make_user):
    start(client, make_user)
    first = order(client)
    gateway.order_lookup_down = True
    r = client.post("/payments/create-order", json={"tier": "personalized", "fresh": True})
    assert r.status_code == 502 and len(gateway.orders) == 1 and payments()[0].status == "created"
    s = client.post("/payments/failed", json={"razorpay_order_id": first["order_id"]}).get_json()
    assert s["state"] == "unknown" and plans() == []


def test_failed_report_that_is_really_a_success_starts_the_plan_once(client, gateway, make_user):
    start(client, make_user)
    first = order(client)
    gateway.add_payment("pay_ok", first["order_id"], first["amount"], status="captured")
    for _ in range(3):                                                    # repeated reports are harmless
        r = client.post("/payments/failed", json={"razorpay_order_id": first["order_id"]}).get_json()
        assert r["state"] == "paid"
    assert len(plans()) == 1 and payments()[0].status == "paid"


def test_closing_the_window_without_paying_is_cancelled_not_failed(client, gateway, make_user):
    start(client, make_user)
    first = order(client)
    r = client.post("/payments/failed", json={"razorpay_order_id": first["order_id"], "reason": "cancelled"}).get_json()
    assert r["state"] == "cancelled" and payments()[0].status == "cancelled" and plans() == []


def test_wrong_amount_on_the_gateway_is_never_treated_as_paid(client, gateway, make_user):
    start(client, make_user)
    first = order(client)
    gateway.add_payment("pay_x", first["order_id"], 1, status="captured")
    r = client.post("/payments/failed", json={"razorpay_order_id": first["order_id"]}).get_json()
    assert r["state"] == "pending" and payments()[0].status == "created" and plans() == []


def test_someone_elses_order_cannot_be_checked(client, gateway, make_user):
    a = make_user(email="a@example.com"); b = make_user(email="b@example.com")
    login(client, a)
    first = order(client)
    login(client, b)
    assert client.post("/payments/failed", json={"razorpay_order_id": first["order_id"]}).status_code == 404


def test_retry_with_another_method_then_success_gives_exactly_one_plan(client, gateway, make_user):
    uid = start(client, make_user)
    first = order(client)
    gateway.add_payment("pay_f1", first["order_id"], first["amount"], status="failed")
    fresh = order(client, fresh=True)
    gateway.add_payment("pay_ok", fresh["order_id"], fresh["amount"])
    v = client.post("/payments/verify", json={"razorpay_order_id": fresh["order_id"], "razorpay_payment_id": "pay_ok",
                                              "razorpay_signature": sign(fresh["order_id"], "pay_ok")})
    assert v.get_json()["ok"] and len(plans()) == 1
    states = [p.status for p in payments()]
    assert states == ["cancelled", "paid"] and payments()[0].gateway_payment_id is None


def test_a_late_capture_on_the_retired_order_is_still_recorded_not_lost(client, gateway, make_user):
    start(client, make_user)
    first = order(client)
    order(client, fresh=True)                                             # first is retired
    from conftest import captured_event, webhook_post
    gateway.add_payment("pay_late", first["order_id"], first["amount"])
    assert webhook_post(client, captured_event(first["order_id"], "pay_late", first["amount"])).status_code == 200
    assert payments()[0].status == "paid"                                  # visible to admin, refundable


# ----------------------------------------------------------------- refunds
def refund_ready(client, gateway, make_user, fake_gemini):
    uid = make_user()
    buy(client, gateway, uid)                                              # paid, plan queued (worker mode)
    with main.app.app_context():
        db.session.query(WorkoutPlan).delete(); db.session.commit()       # plan never built
    return uid


def test_plan_failure_alone_never_refunds(client, gateway, make_user, fake_gemini):
    uid = refund_ready(client, gateway, make_user, fake_gemini)
    assert gateway.refunds == [] and payments()[0].refund_status == "none"


def test_refund_request_is_single_shot_and_admin_checks_the_gateway(client, gateway, make_user, fake_gemini):
    uid = refund_ready(client, gateway, make_user, fake_gemini)
    pid = payments()[0].id
    login(client, uid)
    for _ in range(2):
        client.post(f"/payments/{pid}/refund-request")
    assert payments()[0].refund_status == "requested"
    admin = make_user(email="boss@example.com", admin=True, profile=False)
    login(client, admin)
    gateway.payments["pay_1"]["amount_refunded"] = payments()[0].amount_paise    # already returned on Razorpay
    client.post(f"/admin/payments/{pid}/refund")
    p = payments()[0]
    assert gateway.refunds == [] and p.refund_status == "refunded" and p.status == "refunded"
    client.post(f"/admin/payments/{pid}/refund")                                  # second click: still no refund
    assert gateway.refunds == []


def test_admin_refund_blocked_when_gateway_does_not_show_captured(client, gateway, make_user, fake_gemini):
    uid = refund_ready(client, gateway, make_user, fake_gemini)
    pid = payments()[0].id
    login(client, uid); client.post(f"/payments/{pid}/refund-request")
    login(client, make_user(email="boss@example.com", admin=True, profile=False))
    gateway.payments["pay_1"]["status"] = "failed"
    client.post(f"/admin/payments/{pid}/refund")
    assert gateway.refunds == [] and payments()[0].refund_status == "requested"
