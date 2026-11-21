"""Admin panel: access control, management pages, safeguards, audit trail."""
import pytest

import main
from conftest import login
from models import AdminAuditLog, FitnessProfile, OtpCode, Payment, Trainer, User, WorkoutPlan, db
from services import trainers as trainer_service
from services.admin_stats import dashboard_stats, payment_bucket

ADMIN_GET_URLS = ["/admin", "/admin/users", "/admin/trainers", "/admin/plans", "/admin/payments",
                  "/admin/finance", "/admin/audit-log", "/admin/users/new", "/admin/trainers/new"]


@pytest.fixture
def admin(client, make_user):
    uid = make_user(name="Boss", email="boss@x.com", admin=True, profile=False)
    login(client, uid)
    return uid


def add_payment(user_id, **kw):
    with main.app.app_context():
        base = dict(user_id=user_id, tier="personalized", amount_paise=500, gateway_order_id=f"order_{kw.get('n', 1)}")
        base.update({k: v for k, v in kw.items() if k != "n"})
        p = Payment(**base)
        db.session.add(p)
        db.session.commit()
        return p.id


def audit_actions():
    with main.app.app_context():
        return [a.action for a in db.session.scalars(db.select(AdminAuditLog).order_by(AdminAuditLog.id))]


# ------------------------------------------------------------------ access
def test_every_admin_url_requires_admin(client, make_user):
    urls = ADMIN_GET_URLS + ["/admin/users/1", "/admin/plans/1", "/admin/payments/1"]
    for url in urls:
        assert client.get(url).status_code == 302 and "/login" in client.get(url).headers["Location"]   # anonymous
    login(client, make_user(email="plain@x.com"))
    for url in urls:
        assert client.get(url).status_code == 403                                                        # normal user: access denied
    for url in ("/admin/users/1/delete", "/admin/users/1/status", "/admin/plans/1/delete",
                "/admin/trainers/1/delete", "/admin/trainers/1/status", "/admin/payments/1/refund"):
        assert client.post(url).status_code == 403
    assert audit_actions() == []


def test_deactivated_admin_loses_access(client, admin):
    with main.app.app_context():
        u = db.session.get(User, admin)
        u.is_active = False
        db.session.commit()
    assert client.get("/admin").status_code in (302, 403)   # logged out (suspended account)


def test_every_admin_page_renders(client, admin, make_user, make_trainer):
    uid = make_user(email="u@x.com")
    tid = make_trainer()
    pid = add_payment(uid)
    with main.app.app_context():
        plan = WorkoutPlan(user_id=uid, plan="{}", status="ready")
        db.session.add(plan)
        db.session.commit()
        plan_id = plan.id
    for url in ADMIN_GET_URLS + [f"/admin/users/{uid}", f"/admin/users/{uid}/edit", f"/admin/trainers/{tid}/edit",
                                 f"/admin/plans/{plan_id}", f"/admin/payments/{pid}"]:
        assert client.get(url).status_code == 200, url
    assert client.get("/admin/users/99999").status_code == 404
    assert client.get("/admin/payments/99999").status_code == 404


def test_dashboard_statistics(client, admin, make_user, make_trainer):
    make_user(email="a@x.com"); make_trainer(); make_trainer(name="Off", active=False)
    other = make_user(email="b@x.com")
    with main.app.app_context():
        db.session.get(User, other).is_active = False
        db.session.commit()
    add_payment(admin, n=1, status="paid", payment_verification_status="verified")
    add_payment(admin, n=2, status="created", payment_status="failed")
    add_payment(admin, n=3, status="created")
    with main.app.app_context():
        s = dashboard_stats()
    assert (s["users_total"], s["users_active"], s["users_inactive"], s["admins"]) == (3, 2, 1, 1)
    assert (s["trainers_total"], s["trainers_active"]) == (2, 1)
    assert (s["payments_successful"], s["payments_failed"], s["payments_pending"]) == (1, 1, 1)
    assert client.get("/admin").status_code == 200


# ------------------------------------------------------------------- users
def test_user_search_and_filters(client, admin, make_user):
    make_user(name="Alice", email="alice@x.com"); off = make_user(name="Bob", email="bob@x.com")
    with main.app.app_context():
        db.session.get(User, off).is_active = False
        db.session.commit()
    page = client.get("/admin/users?q=alice").get_data(as_text=True)
    assert "alice@x.com" in page and "bob@x.com" not in page
    page = client.get("/admin/users?status=inactive").get_data(as_text=True)
    assert "bob@x.com" in page and "alice@x.com" not in page
    assert "Boss" in client.get("/admin/users?role=admin").get_data(as_text=True)
    assert "No users match" in client.get("/admin/users?q=%25").get_data(as_text=True)   # % is literal, not a wildcard


def test_deactivate_and_activate_user_with_audit(client, admin, make_user):
    uid = make_user(email="u@x.com")
    client.post(f"/admin/users/{uid}/status", data={"action": "deactivate"})
    with main.app.app_context():
        assert db.session.get(User, uid).is_active is False
    client.post(f"/admin/users/{uid}/status", data={"action": "activate"})
    with main.app.app_context():
        assert db.session.get(User, uid).is_active is True
    assert audit_actions() == ["user.deactivate", "user.activate"]


def test_admin_cannot_lock_everyone_out(client, admin, make_user):
    assert "own account" in client.post(f"/admin/users/{admin}/status", data={"action": "deactivate"},
                                        follow_redirects=True).get_data(as_text=True)
    other_admin = make_user(email="o@x.com", admin=True)
    with main.app.app_context():
        db.session.get(User, other_admin).is_active = False        # only one ACTIVE admin remains: me
        db.session.commit()
    r = client.post(f"/admin/users/{admin}/edit", data={"name": "Boss", "email": "boss@x.com", "is_active": "y"},
                    follow_redirects=True)
    assert "last active admin" in r.get_data(as_text=True)
    with main.app.app_context():
        assert db.session.get(User, admin).is_admin is True


def test_cannot_delete_self_or_without_confirmation(client, admin, make_user):
    assert client.post(f"/admin/users/{admin}/delete", data={"confirm": "boss@x.com"}).status_code == 302
    with main.app.app_context():
        assert db.session.get(User, admin) is not None
    uid = make_user(email="victim@x.com")
    client.post(f"/admin/users/{uid}/delete", data={"confirm": "wrong"})
    client.post(f"/admin/users/{uid}/delete")
    with main.app.app_context():
        assert db.session.get(User, uid) is not None
    assert audit_actions() == []


def test_delete_user_keeps_payments_and_removes_personal_data(client, admin, make_user):
    uid = make_user(email="victim@x.com")
    pid = add_payment(uid, status="used")
    with main.app.app_context():
        db.session.add(WorkoutPlan(user_id=uid, plan="{}"))
        db.session.add(OtpCode(email="victim@x.com", purpose="login", code_hash="x" * 64,
                               expires_at=main.utcnow()))
        db.session.commit()
    client.post(f"/admin/users/{uid}/delete", data={"confirm": "VICTIM@x.com"})
    with main.app.app_context():
        assert db.session.get(User, uid) is None
        assert db.session.scalar(db.select(db.func.count(FitnessProfile.id))) == 0
        assert db.session.scalar(db.select(db.func.count(WorkoutPlan.id))) == 0
        assert db.session.scalar(db.select(db.func.count(OtpCode.id))) == 0
        kept = db.session.get(Payment, pid)
        assert kept is not None and kept.user_id is None and kept.status == "used"
    assert audit_actions() == ["user.delete"]


def test_create_and_edit_user(client, admin):
    client.post("/admin/users/new", data={"name": "New", "email": "NEW@x.com", "is_active": "y"})
    with main.app.app_context():
        u = db.session.scalar(db.select(User).where(User.email == "new@x.com"))
        assert u and u.is_active and not u.is_admin
    dup = client.post("/admin/users/new", data={"name": "Dup", "email": "new@x.com", "is_active": "y"})
    assert "already exists" in dup.get_data(as_text=True)
    client.post(f"/admin/users/{u.id}/edit", data={"name": "Renamed", "email": "new@x.com"})   # box unticked = inactive
    with main.app.app_context():
        assert db.session.get(User, u.id).name == "Renamed" and db.session.get(User, u.id).is_active is False


# ------------------------------------------------------------------- plans
def test_plan_list_filters_detail_and_delete(client, admin, make_user):
    uid = make_user(email="u@x.com")
    pay = add_payment(uid, status="used")
    with main.app.app_context():
        free = WorkoutPlan(user_id=uid, plan="{}", tier="basic")
        paid = WorkoutPlan(user_id=uid, plan="{}", tier="personalized", payment_id=pay, status="failed")
        db.session.add_all([free, paid]); db.session.commit()
        free_id, paid_id = free.id, paid.id
    assert f"#{paid_id}" in client.get("/admin/plans?tier=premium").get_data(as_text=True)
    assert f"#{free_id}</td>" not in client.get("/admin/plans?tier=premium").get_data(as_text=True)
    assert f"#{paid_id}</td>" not in client.get("/admin/plans?status=ready").get_data(as_text=True)
    assert "u@x.com" in client.get("/admin/plans?q=u@x").get_data(as_text=True)
    client.post(f"/admin/plans/{paid_id}/delete", data={"confirm": "nope"})
    with main.app.app_context():
        assert db.session.get(WorkoutPlan, paid_id) is not None
    client.post(f"/admin/plans/{paid_id}/delete", data={"confirm": "DELETE"})
    with main.app.app_context():
        assert db.session.get(WorkoutPlan, paid_id) is None
        assert db.session.get(Payment, pay).status == "used"          # money record untouched
    assert audit_actions() == ["plan.delete"]


# ---------------------------------------------------------------- payments
def test_payments_search_filter_and_read_only(client, admin, make_user):
    uid = make_user(name="Payer", email="payer@x.com")
    ok = add_payment(uid, n=1, status="paid", payment_verification_status="verified", gateway_payment_id="pay_ABC")
    add_payment(uid, n=2, status="created", payment_status="failed")
    assert "order_1" in client.get("/admin/payments?status=successful").get_data(as_text=True)
    assert "order_2" not in client.get("/admin/payments?status=successful").get_data(as_text=True)
    assert "order_2" in client.get("/admin/payments?status=failed").get_data(as_text=True)
    assert "order_1" in client.get("/admin/payments?q=pay_ABC").get_data(as_text=True)
    assert "order_1" in client.get(f"/admin/payments?q={ok}").get_data(as_text=True)
    assert "order_1" in client.get("/admin/payments?q=payer@x").get_data(as_text=True)
    assert "pay_ABC" in client.get(f"/admin/payments/{ok}").get_data(as_text=True)
    # no way to edit or delete a payment from the admin panel
    rules = {r.rule for r in main.app.url_map.iter_rules() if r.rule.startswith("/admin/payments")}
    assert rules == {"/admin/payments", "/admin/payments/<int:payment_id>", "/admin/payments/<int:payment_id>/refund"}
    assert client.post(f"/admin/payments/{ok}", data={"amount_paise": 1}).status_code == 405
    assert client.delete(f"/admin/payments/{ok}").status_code == 405


def test_payment_bucket_matches_filter_precedence():
    p = Payment(status="refunded", refund_status="refunded", payment_verification_status="verified")
    assert payment_bucket(p) == "refunded"
    assert payment_bucket(Payment(status="used", payment_verification_status="verified", refund_status="none")) == "successful"
    assert payment_bucket(Payment(status="created", payment_status="created", payment_verification_status="pending", refund_status="none")) == "pending"


# ---------------------------------------------------------------- trainers
@pytest.fixture
def geocoded(monkeypatch):
    monkeypatch.setattr(trainer_service, "get_coordinates", lambda loc: (12.97, 77.59))


def test_trainer_create_edit_deactivate_delete(client, admin, geocoded):
    client.post("/admin/trainers/new", data={"name": "Tara", "gender": "female", "location": "Indiranagar",
                                             "about": "Yoga", "contact_email": "Tara@x.com", "is_active": "y"})
    with main.app.app_context():
        t = db.session.scalar(db.select(Trainer))
        assert t.contact_email == "tara@x.com" and t.is_active and t.latitude == 12.97
        tid = t.id
    client.post(f"/admin/trainers/{tid}/status", data={"action": "deactivate"})
    with main.app.app_context():
        assert db.session.get(Trainer, tid).is_active is False
    assert "No trainers match" in client.get("/admin/trainers?status=active").get_data(as_text=True)
    client.post(f"/admin/trainers/{tid}/delete", data={"confirm": "no"})
    with main.app.app_context():
        assert db.session.get(Trainer, tid) is not None
    client.post(f"/admin/trainers/{tid}/delete", data={"confirm": "DELETE"})
    with main.app.app_context():
        assert db.session.get(Trainer, tid) is None
    assert audit_actions() == ["trainer.create", "trainer.deactivate", "trainer.delete"]


def test_trainer_geocoding_failure_is_reported(client, admin, monkeypatch):
    def down(loc):
        raise trainer_service.GeocodingError
    monkeypatch.setattr(trainer_service, "get_coordinates", down)
    r = client.post("/admin/trainers/new", data={"name": "Tara Singh", "gender": "male", "location": "X", "is_active": "y"})
    assert r.status_code == 200 and "not responding" in r.get_data(as_text=True)
    with main.app.app_context():
        assert db.session.scalar(db.select(db.func.count(Trainer.id))) == 0


def test_audit_log_page_lists_actions(client, admin, make_user):
    uid = make_user(email="u@x.com")
    client.post(f"/admin/users/{uid}/status", data={"action": "deactivate"})
    page = client.get("/admin/audit-log?q=deactivate").get_data(as_text=True)
    assert "user.deactivate" in page and "boss@x.com" in page


def test_csrf_is_enforced_on_admin_posts(client, admin, make_user):
    uid = make_user(email="u@x.com")
    main.app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert client.post(f"/admin/users/{uid}/status", data={"action": "deactivate"}).status_code == 400
    finally:
        main.app.config["WTF_CSRF_ENABLED"] = False
    with main.app.app_context():
        assert db.session.get(User, uid).is_active is True
