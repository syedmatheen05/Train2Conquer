import json

import main
from conftest import (buy, captured_event, login, sign, valid_plan_json, webhook_post)
from models import AIUsage, Payment, PaymentEvent, WorkoutPlan, db


def rows(model):
    with main.app.app_context():
        return db.session.query(model).all()


# ---- 2/3. orders: price is decided by the server ----
def test_personalized_order_uses_server_price(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    r = client.post("/payments/create-order", json={"tier": "personalized", "amount": 1, "price": 1})
    d = r.get_json()
    assert r.status_code == 200 and d["amount"] == 500 and d["currency"] == "INR"
    assert gateway.orders[0]["amount"] == 500
    assert "fake_key_secret" not in json.dumps(d)           # secret never sent to the browser
    assert d["key_id"] == "rzp_test_FAKEKEY"


def test_advanced_order_uses_server_price(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "advanced", "amount": 100}).get_json()
    assert d["amount"] == 1500 and gateway.orders[0]["amount"] == 1500


def test_unknown_tier_and_basic_rejected(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    assert client.post("/payments/create-order", json={"tier": "basic"}).status_code == 400
    assert client.post("/payments/create-order", json={"tier": "gold"}).status_code == 400
    assert gateway.orders == []


def test_order_requires_login(client, gateway):
    assert client.post("/payments/create-order", json={"tier": "advanced"}).status_code in (302, 401)


def test_double_click_reuses_open_order(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    a = client.post("/payments/create-order", json={"tier": "advanced"}).get_json()
    b = client.post("/payments/create-order", json={"tier": "advanced"}).get_json()
    assert a["order_id"] == b["order_id"] and len(gateway.orders) == 1


# ---- 10. successful payment ----
def test_successful_payment_verified_and_plan_queued(client, gateway, make_user):
    uid = make_user()
    _, verify = buy(client, gateway, uid)
    assert verify.get_json()["ok"] is True
    p = rows(Payment)[0]
    assert (p.status, p.payment_status, p.payment_verification_status) == ("paid", "captured", "verified")
    assert p.gateway_payment_id == "pay_1" and p.paid_at is not None and p.currency == "INR"
    assert (p.fee_paise, p.tax_paise, p.payment_method) == (12, 2, "upi")
    plan = rows(WorkoutPlan)[0]
    assert plan.status == "generating" and plan.generation_status == "queued" and plan.payment_id == p.id


def test_authorized_payment_is_captured_first(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "personalized"}).get_json()
    gateway.add_payment("pay_a", d["order_id"], 500, status="authorized")
    r = client.post("/payments/verify", json={"razorpay_order_id": d["order_id"], "razorpay_payment_id": "pay_a",
                                              "razorpay_signature": sign(d["order_id"], "pay_a")})
    assert r.get_json()["ok"] and gateway.captured_calls == ["pay_a"]


# ---- 5. invalid signature ----
def test_invalid_signature_rejected(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "personalized"}).get_json()
    gateway.add_payment("pay_x", d["order_id"], 500)
    r = client.post("/payments/verify", json={"razorpay_order_id": d["order_id"], "razorpay_payment_id": "pay_x",
                                              "razorpay_signature": "deadbeef"})
    assert r.status_code == 400
    p = rows(Payment)[0]
    assert p.status == "created" and p.payment_verification_status == "failed" and rows(WorkoutPlan) == []


# ---- 4. wrong amount / currency ----
def test_wrong_amount_rejected(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "advanced"}).get_json()
    gateway.add_payment("pay_w", d["order_id"], 100)           # Rs 1 instead of Rs 15
    r = client.post("/payments/verify", json={"razorpay_order_id": d["order_id"], "razorpay_payment_id": "pay_w",
                                              "razorpay_signature": sign(d["order_id"], "pay_w")})
    assert r.status_code == 400
    p = rows(Payment)[0]
    assert p.status == "created" and p.payment_verification_status == "mismatch" and rows(WorkoutPlan) == []


def test_wrong_currency_rejected(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "personalized"}).get_json()
    gateway.add_payment("pay_c", d["order_id"], 500, currency="USD")
    r = client.post("/payments/verify", json={"razorpay_order_id": d["order_id"], "razorpay_payment_id": "pay_c",
                                              "razorpay_signature": sign(d["order_id"], "pay_c")})
    assert r.status_code == 400 and rows(WorkoutPlan) == []


def test_other_users_order_cannot_be_verified(client, gateway, make_user):
    a = make_user(email="a@x.com"); b = make_user(email="b@x.com")
    login(client, a)
    d = client.post("/payments/create-order", json={"tier": "personalized"}).get_json()
    gateway.add_payment("pay_o", d["order_id"], 500)
    login(client, b)
    r = client.post("/payments/verify", json={"razorpay_order_id": d["order_id"], "razorpay_payment_id": "pay_o",
                                              "razorpay_signature": sign(d["order_id"], "pay_o")})
    assert r.status_code == 404 and rows(WorkoutPlan) == []


def test_pending_payment_does_not_unlock(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "personalized"}).get_json()
    gateway.add_payment("pay_p", d["order_id"], 500, status="created")
    r = client.post("/payments/verify", json={"razorpay_order_id": d["order_id"], "razorpay_payment_id": "pay_p",
                                              "razorpay_signature": sign(d["order_id"], "pay_p")})
    assert r.status_code == 409 and rows(WorkoutPlan) == [] and rows(Payment)[0].status == "created"


# ---- 6. duplicate callback ----
def test_duplicate_callback_makes_one_plan(client, gateway, make_user):
    uid = make_user()
    _, v1 = buy(client, gateway, uid)
    order_id = rows(Payment)[0].gateway_order_id
    v2 = client.post("/payments/verify", json={"razorpay_order_id": order_id, "razorpay_payment_id": "pay_1",
                                               "razorpay_signature": sign(order_id, "pay_1")})
    assert v1.get_json()["ok"] and v2.get_json()["ok"]
    assert len(rows(WorkoutPlan)) == 1 and rows(Payment)[0].attempts == 1


def test_same_payment_id_cannot_pay_second_order(client, gateway, make_user):
    uid = make_user()
    buy(client, gateway, uid)
    main_order = rows(Payment)[0]
    # finish first plan so a second order is allowed
    main.process_queue()
    with main.app.app_context():
        db.session.query(Payment).update({"status": "used"}); db.session.commit()
    login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "personalized"}).get_json()
    gateway.add_payment("pay_1", d["order_id"], 500)       # reuse the OLD payment id
    r = client.post("/payments/verify", json={"razorpay_order_id": d["order_id"], "razorpay_payment_id": "pay_1",
                                              "razorpay_signature": sign(d["order_id"], "pay_1")})
    assert r.status_code == 400
    assert main_order.gateway_order_id != d["order_id"]


# ---- 8/9. cancel and failure ----
def test_cancel_marks_cancelled_and_late_payment_still_counts(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "personalized"}).get_json()
    assert client.post("/payments/cancel", json={"razorpay_order_id": d["order_id"]}).get_json()["ok"]
    assert rows(Payment)[0].status == "cancelled" and rows(WorkoutPlan) == []
    # customer actually paid (UPI completed after closing the window): webhook must still unlock it
    r = webhook_post(client, captured_event(d["order_id"], "pay_late", 500), "evt_late")
    assert r.status_code == 200
    assert rows(Payment)[0].status == "paid" and len(rows(WorkoutPlan)) == 1


def test_payment_failed_callback(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "personalized"}).get_json()
    gateway.add_payment("pay_f", d["order_id"], 500, status="failed", error_code="BAD_REQUEST_ERROR")
    r = client.post("/payments/verify", json={"razorpay_order_id": d["order_id"], "razorpay_payment_id": "pay_f",
                                              "razorpay_signature": sign(d["order_id"], "pay_f")})
    assert r.status_code == 400
    p = rows(Payment)[0]
    assert p.status == "created" and p.payment_status == "failed" and rows(WorkoutPlan) == []


def test_payment_failed_webhook_then_success_on_same_order(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "personalized"}).get_json()
    ev = {"event": "payment.failed", "payload": {"payment": {"entity": {
        "id": "pay_bad", "order_id": d["order_id"], "amount": 500, "status": "failed", "error_code": "X"}}}}
    assert webhook_post(client, ev, "evt_f").status_code == 200
    assert rows(Payment)[0].payment_status == "failed" and rows(Payment)[0].status == "created"
    webhook_post(client, captured_event(d["order_id"], "pay_ok", 500), "evt_ok")
    p = rows(Payment)[0]
    assert p.status == "paid" and p.payment_status == "captured" and p.failure_code is None


# ---- 7. webhooks ----
def test_webhook_bad_signature_rejected(client, gateway, make_user):
    r = webhook_post(client, captured_event("order_1", "pay_1", 500), signature="nope")
    assert r.status_code == 400 and rows(PaymentEvent) == []


def test_webhook_signature_uses_webhook_secret_not_key_secret(client, gateway, make_user):
    r = webhook_post(client, captured_event("order_1", "pay_1", 500), secret=b"fake_key_secret")
    assert r.status_code == 400


def test_webhook_before_callback_then_callback(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "advanced"}).get_json()
    assert webhook_post(client, captured_event(d["order_id"], "pay_9", 1500), "evt_a").status_code == 200
    assert rows(Payment)[0].status == "paid" and len(rows(WorkoutPlan)) == 1   # browser never came back, plan still queued
    gateway.add_payment("pay_9", d["order_id"], 1500)
    r = client.post("/payments/verify", json={"razorpay_order_id": d["order_id"], "razorpay_payment_id": "pay_9",
                                              "razorpay_signature": sign(d["order_id"], "pay_9")})
    assert r.get_json()["ok"] and len(rows(WorkoutPlan)) == 1 and rows(Payment)[0].attempts == 1


def test_callback_before_webhook(client, gateway, make_user):
    uid = make_user()
    buy(client, gateway, uid)
    order_id = rows(Payment)[0].gateway_order_id
    assert webhook_post(client, captured_event(order_id, "pay_1", 500), "evt_b").status_code == 200
    assert len(rows(WorkoutPlan)) == 1 and rows(Payment)[0].attempts == 1


def test_duplicate_webhook_delivery_is_ignored(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "personalized"}).get_json()
    ev = captured_event(d["order_id"], "pay_d", 500)
    assert webhook_post(client, ev, "evt_same").status_code == 200
    assert webhook_post(client, ev, "evt_same").status_code == 200
    assert len(rows(PaymentEvent)) == 1 and len(rows(WorkoutPlan)) == 1


def test_captured_and_order_paid_events_make_one_plan(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "personalized"}).get_json()
    webhook_post(client, captured_event(d["order_id"], "pay_e", 500), "evt_1")
    webhook_post(client, captured_event(d["order_id"], "pay_e", 500, event="order.paid"), "evt_2")
    assert len(rows(WorkoutPlan)) == 1 and rows(Payment)[0].attempts == 1


def test_webhook_wrong_amount_never_unlocks(client, gateway, make_user):
    uid = make_user(); login(client, uid)
    d = client.post("/payments/create-order", json={"tier": "advanced"}).get_json()
    webhook_post(client, captured_event(d["order_id"], "pay_s", 500), "evt_w")
    p = rows(Payment)[0]
    assert p.status == "created" and p.payment_verification_status == "mismatch" and rows(WorkoutPlan) == []


def test_webhook_unknown_order_is_ok(client, gateway):
    assert webhook_post(client, captured_event("order_zzz", "pay_z", 500), "evt_z").status_code == 200


def test_webhook_processing_error_returns_500_so_razorpay_retries(client, gateway, make_user, monkeypatch):
    def boom(*a, **k): raise RuntimeError("db down")
    monkeypatch.setattr(main, "_handle_payment_event", boom)
    assert webhook_post(client, captured_event("o", "p", 500), "evt_err").status_code == 500
    assert rows(PaymentEvent) == []          # not recorded, so the retry is processed


def test_webhook_needs_no_csrf_but_other_posts_do(client, gateway, make_user):
    main.app.config["WTF_CSRF_ENABLED"] = True
    try:
        uid = make_user(); login(client, uid)
        assert client.post("/payments/create-order", json={"tier": "advanced"}).status_code == 400   # CSRF blocks
        assert webhook_post(client, captured_event("order_q", "pay_q", 500), "evt_csrf").status_code == 200
    finally:
        main.app.config["WTF_CSRF_ENABLED"] = False


# ---- refunds are explicit, never automatic ----
def test_refund_flow_is_manual_and_single_shot(client, gateway, make_user, fake_openai):
    uid = make_user(); admin = make_user(name="Admin", email="adm@x.com", admin=True, profile=False)
    buy(client, gateway, uid)
    pid = rows(Payment)[0].id
    login(client, uid)
    client.post(f"/payments/{pid}/refund-request")                            # plan still building: refused
    assert rows(Payment)[0].refund_status == "none"
    with main.app.app_context():                                              # generation fails for good
        db.session.query(WorkoutPlan).update({"status": "failed", "generation_status": "failed"}); db.session.commit()
    assert client.post(f"/payments/{pid}/refund-request").status_code == 302
    assert rows(Payment)[0].refund_status == "requested" and gateway.refunds == []
    # a normal user cannot approve
    assert client.post(f"/admin/payments/{pid}/refund").status_code in (302, 403, 404)
    assert gateway.refunds == []
    login(client, admin)
    client.post(f"/admin/payments/{pid}/refund")
    client.post(f"/admin/payments/{pid}/refund")                              # double click
    assert gateway.refunds == [("pay_1", 500)]
    p = rows(Payment)[0]
    assert p.refund_status == "refunded" and p.refund_amount_paise == 500 and p.status == "refunded"
    # a refunded payment can never generate a plan
    with main.app.app_context():
        assert main.start_paid_generation(db.session.get(Payment, pid))[0] == "invalid"


def test_refund_webhook_updates_record(client, gateway, make_user):
    uid = make_user()
    buy(client, gateway, uid)
    with main.app.app_context():
        db.session.query(Payment).update({"refund_status": "processing", "status": "refunded"}); db.session.commit()
    ev = {"event": "refund.processed", "payload": {
        "refund": {"entity": {"id": "rfnd_9", "payment_id": "pay_1", "amount": 500}},
        "payment": {"entity": {"id": "pay_1", "amount_refunded": 500}}}}
    assert webhook_post(client, ev, "evt_r").status_code == 200
    p = rows(Payment)[0]
    assert p.refund_status == "refunded" and p.refund_amount_paise == 500 and p.gateway_refund_id == "rfnd_9"


def test_refund_request_refused_when_plan_delivered(client, gateway, make_user, fake_openai):
    uid = make_user()
    buy(client, gateway, uid)
    fake_openai([__import__("conftest").fake_response(valid_plan_json())])
    main.process_queue()
    pid = rows(Payment)[0].id
    client.post(f"/payments/{pid}/refund-request")
    assert rows(Payment)[0].refund_status == "none" and rows(Payment)[0].status == "used"
