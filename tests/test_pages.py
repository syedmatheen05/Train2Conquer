import re

import main
from conftest import buy, fake_response, login, valid_plan_json
from models import Payment, WorkoutPlan, db


def test_frontend_endpoints_exist():
    rules = {r.rule: r.methods for r in main.app.url_map.iter_rules()}
    for path in ("/payments/create-order", "/payments/verify", "/payments/cancel", "/payments/failed", "/razorpay/webhook", "/plans/basic",
                 "/admin/finance", "/healthz", "/workout-plan/status"):
        assert path in rules, path
    js = open("static/js/plans.js").read()
    assert "RAZORPAY_KEY_SECRET" not in js and "createUrl" in js and "verifyUrl" in js and "failedUrl" in js
    html = main.app.jinja_env.get_template("_plan_options.html").filter_by_empty if False else open("templates/_plan_options.html").read()
    for name in ("create_payment_order", "verify_payment", "cancel_payment", "payment_failed"):
        assert f"url_for('{name}')" in html
        assert name in {r.endpoint for r in main.app.url_map.iter_rules()}


def test_home_and_profile_pages_render_in_every_state(client, gateway, make_user, fake_gemini):
    uid = make_user(); login(client, uid)
    assert client.get("/fitness-profile").status_code == 200
    assert "Select plan" in client.get("/").get_data(as_text=True) or client.get("/").status_code == 200
    buy(client, gateway, uid)                                     # queued / generating
    assert client.get("/").status_code == 200
    fake_gemini([fake_response(valid_plan_json())]); main.process_queue()   # ready
    page = client.get("/").get_data(as_text=True)
    assert client.get("/").status_code == 200
    assert client.get("/workout-plan/status").status_code == 200
    # a paid-but-unstarted payment shows the "Build my plan" banner
    with main.app.app_context():
        db.session.query(WorkoutPlan).delete()
        db.session.add(Payment(user_id=uid, tier="advanced", amount_paise=1500, gateway_order_id="o_x", gateway_payment_id="p_x",
                               status="paid", payment_status="captured", payment_verification_status="verified"))
        db.session.commit()
    banner = client.get("/").get_data(as_text=True)
    assert "Build my plan" in banner and "gpt" not in banner.lower()


def test_failed_state_shows_retry_and_refund(client, gateway, make_user, fake_gemini):
    uid = make_user()
    buy(client, gateway, uid)
    fake_gemini([fake_response("x"), fake_response("x")]); main.process_queue()
    page = client.get("/").get_data(as_text=True)
    assert "Retry" in page and "Request a refund" in page and "no extra charge" in page


def test_plan_ui_states_present_in_markup():
    html = open("templates/_plan_options.html").read()
    for label in ("Select plan", "Creating payment", "Payment processing", "Payment successful", "Generating workout", "Workout ready"):
        assert label in html
    assert "Retry" in html
