"""Remember Me: persistent login with Flask-Login's signed cookie (no database table).

Browser behaviour is simulated with Flask's test client, whose cookie jar follows the same
rules as a browser: cookies with no Max-Age/Expires ("session cookies") are lost when the
browser is closed; cookies with an expiry survive.
"""
import time

import pytest
from flask_login.utils import encode_cookie

import config
import main
from conftest import login
from models import User, db
from services import otp as otp_service

COOKIE = "remember_token"


def log_in_with_otp(client, sent_codes, email="u@example.com", remember=True):
    data = {"email": email}
    if remember:
        data["remember"] = "y"
    client.post("/login", data=data)
    return client.post("/verify-otp", data={"verification_code": sent_codes[-1][1]})


def close_browser(client):
    """Quit the browser completely: only cookies that have an expiry date survive."""
    for cookie in list(client._cookies.values()):
        if cookie.expires is None and cookie.max_age is None:
            client.delete_cookie(cookie.key, domain=cookie.domain, path=cookie.path)


def is_logged_in(client):
    return client.get("/fitness-profile").status_code == 200


def set_cookie_header(response, name):
    return next((h for h in response.headers.getlist("Set-Cookie") if h.startswith(name + "=")), None)


@pytest.fixture
def user(make_user):
    return make_user(email="u@example.com")


# TEST 1 -------------------------------------------------------------------------------
def test_without_remember_me_nothing_persistent_is_created(client, user, sent_codes):
    r = log_in_with_otp(client, sent_codes, remember=False)
    assert set_cookie_header(r, COOKIE) is None and client.get_cookie(COOKIE) is None
    session_cookie = client.get_cookie("session")
    assert session_cookie.expires is None and session_cookie.max_age is None     # dies with the browser
    assert is_logged_in(client)
    close_browser(client)
    assert not is_logged_in(client)                                              # existing behaviour preserved


# TESTS 2, 3, 4, 12 --------------------------------------------------------------------
def test_remember_me_survives_closing_the_browser(client, user, sent_codes):
    log_in_with_otp(client, sent_codes, remember=True)
    assert client.get_cookie(COOKIE) is not None
    close_browser(client)
    assert client.get_cookie("session") is None and client.get_cookie(COOKIE) is not None
    assert is_logged_in(client)                                                  # restored from the cookie
    assert client.get("/").status_code == 200                                    # TEST 3: refresh
    for url in ("/fitness-profile", "/", "/find-trainer?mode=online", "/contact", "/fitness-profile"):
        assert client.get(url).status_code in (200, 302)                          # TEST 4: navigation
    assert "/login" not in client.get("/find-trainer").headers.get("Location", "")
    assert "/register" not in client.get("/fitness-profile").headers.get("Location", "")
    close_browser(client)                                                        # TEST 12: and again
    assert is_logged_in(client)


def test_remember_cookie_security_attributes(client, user, sent_codes):
    r = log_in_with_otp(client, sent_codes)
    header = set_cookie_header(r, COOKIE)
    assert "HttpOnly" in header and "SameSite=Lax" in header and "Path=/" in header
    assert "Domain" not in header                                                # host-only
    from email.utils import parsedate_to_datetime
    from datetime import datetime, timezone
    expires = parsedate_to_datetime(header.split("Expires=")[1].split(";")[0])
    days = (expires - datetime.now(timezone.utc)).total_seconds() / 86400
    assert 29.9 < days <= 30.01                                                  # 30 days, fixed
    assert "Secure" not in header                                                # development over http
    assert "password" not in header.lower() and "u@example.com" not in header


def test_secure_flag_is_set_in_production_mode(client, user, sent_codes):
    main.app.config["REMEMBER_COOKIE_SECURE"] = True
    try:
        r = log_in_with_otp(client, sent_codes)
    finally:
        main.app.config["REMEMBER_COOKIE_SECURE"] = False
    assert "Secure" in set_cookie_header(r, COOKIE)


def test_remember_me_checkbox_is_unchecked_by_default(client):
    for path in ("/login", "/register"):
        page = client.get(path).get_data(as_text=True)
        assert 'name="remember"' in page and "Remember Me" in page
        assert 'name="remember"' in page and "checked" not in page.split('name="remember"')[1].split(">")[0]


def test_lifetime_is_configurable(monkeypatch):
    assert config.remember_me_days() == 30
    monkeypatch.setenv("REMEMBER_ME_DAYS", "7")
    assert config.remember_me_days() == 7
    monkeypatch.setenv("REMEMBER_ME_DAYS", "100000")
    assert config.remember_me_days() == 90                                       # never "forever"
    monkeypatch.setenv("REMEMBER_ME_DAYS", "junk")
    assert config.remember_me_days() == 30


# TEST 11 ------------------------------------------------------------------------------
def test_persistent_login_is_created_only_after_successful_verification(client, sent_codes, make_user):
    client.post("/register", data={"name": "New Person", "email": "new@example.com", "remember": "y"})
    assert client.get_cookie(COOKIE) is None                                     # nothing before the code is proven
    wrong = client.post("/verify-otp", data={"verification_code": "000000" if sent_codes[0][1] != "000000" else "111111"})
    assert set_cookie_header(wrong, COOKIE) is None and client.get_cookie(COOKIE) is None
    ok = client.post("/verify-otp", data={"verification_code": sent_codes[0][1]})
    assert set_cookie_header(ok, COOKIE) is not None
    close_browser(client)
    assert client.get("/fitness-profile").status_code == 200


def test_remember_choice_does_not_leak_to_a_later_login(client, user, sent_codes):
    log_in_with_otp(client, sent_codes, remember=True)
    client.post("/logout")
    with main.app.app_context():                       # skip the resend cooldown
        from models import OtpCode
        db.session.query(OtpCode).delete()
        db.session.commit()
    r = log_in_with_otp(client, sent_codes, remember=False)
    assert set_cookie_header(r, COOKIE) is None and client.get_cookie(COOKIE) is None


# TEST 5 -------------------------------------------------------------------------------
def test_logout_clears_the_cookie_and_requires_login_again(client, user, sent_codes):
    log_in_with_otp(client, sent_codes)
    r = client.post("/logout")
    header = set_cookie_header(r, COOKIE)
    assert header is not None and "Expires=Thu, 01 Jan 1970" in header           # told the browser to delete it
    assert client.get_cookie(COOKIE) is None
    assert not is_logged_in(client)
    close_browser(client)
    assert not is_logged_in(client)
    assert "/login" in client.get("/fitness-profile").headers["Location"]


# TEST 6 -------------------------------------------------------------------------------
def test_deactivated_or_deleted_account_loses_persistent_access(client, user, sent_codes):
    log_in_with_otp(client, sent_codes)
    close_browser(client)
    assert is_logged_in(client)
    with main.app.app_context():
        db.session.get(User, user).is_active = False
        db.session.commit()
    close_browser(client)
    assert not is_logged_in(client)
    with main.app.app_context():                                                  # re-activate: access returns
        db.session.get(User, user).is_active = True
        db.session.commit()
    assert is_logged_in(client)
    with main.app.app_context():
        from services.accounts import delete_user_account
        delete_user_account(user)
        db.session.commit()
    close_browser(client)
    assert not is_logged_in(client)


# TEST 7 -------------------------------------------------------------------------------
def test_there_are_no_passwords_so_account_events_are_what_matter(client, user, sent_codes):
    """Login is e-mail-code only (no password to change). Deactivation (above) and deleting the
    account take effect immediately; an admin changing the e-mail does NOT revoke an existing
    cookie (documented limitation: revocation per user would need a schema change)."""
    assert not hasattr(User, "password") and not hasattr(User, "password_hash")


# TEST 8 + admin safety ----------------------------------------------------------------
def test_normal_user_never_gets_admin_even_with_remember_me(client, user, sent_codes):
    log_in_with_otp(client, sent_codes)
    close_browser(client)
    r = client.get("/admin")
    assert r.status_code == 403 and b"Access denied" in r.data
    assert client.get("/fitness-profile").status_code == 200                        # NOT logged out by the denial


def test_admin_restored_from_cookie_opens_the_panel_without_a_second_login(client, make_user, sent_codes):
    make_user(email="boss@example.com", admin=True, profile=False)
    log_in_with_otp(client, sent_codes, email="boss@example.com")
    close_browser(client)                                                         # only the Remember Me cookie is left
    assert client.get("/admin").status_code == 200                                # no second login to LOOK at the panel
    assert client.get("/admin/users").status_code == 200


def test_admin_restored_from_cookie_must_confirm_before_changing_things(client, make_user, sent_codes):
    uid = make_user(email="boss@example.com", admin=True, profile=False)
    other = make_user(email="victim@example.com", profile=False)
    log_in_with_otp(client, sent_codes, email="boss@example.com")
    close_browser(client)
    r = client.post(f"/admin/users/{other}/delete", data={"confirm": "victim@example.com"},
                    headers={"Referer": "http://localhost/admin/users/%d" % other})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    with main.app.app_context():
        assert db.session.get(User, other) is not None                            # nothing deleted
    with client.session_transaction() as s:
        assert s["post_login_next"] == "/admin/users/%d" % other                   # comes back here after the code


def test_strict_setting_makes_every_admin_page_need_a_fresh_login(client, make_user, sent_codes, monkeypatch):
    monkeypatch.setenv("ADMIN_REQUIRE_FRESH_LOGIN", "always")
    make_user(email="boss@example.com", admin=True, profile=False)
    log_in_with_otp(client, sent_codes, email="boss@example.com")
    assert client.get("/admin").status_code == 200
    close_browser(client)
    r = client.get("/admin")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_guest_is_sent_to_login_and_lands_on_admin_after_verifying(client, make_user, sent_codes):
    make_user(email="boss@example.com", admin=True, profile=False)
    r = client.get("/admin/payments")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    client.get(r.headers["Location"])                                             # login page remembers where they were going
    client.post("/login", data={"email": "boss@example.com"})
    code = sent_codes[-1][1]
    done = client.post("/verify-otp", data={"verification_code": code})
    assert done.status_code == 302 and done.headers["Location"].endswith("/admin/payments")


def test_login_next_cannot_redirect_off_site(client, make_user, sent_codes):
    make_user(email="a@example.com", profile=False)
    for evil in ("https://evil.example/x", "//evil.example", "/\\evil.example", "javascript:alert(1)"):
        client.get("/login", query_string={"next": evil})
        client.post("/login", data={"email": "a@example.com"})
        done = client.post("/verify-otp", data={"verification_code": sent_codes[-1][1]})
        assert done.status_code == 302 and "evil" not in done.headers["Location"], evil
        client.post("/logout")
        import time; time.sleep(0)  # cooldown is per e-mail; clear it below
        with main.app.app_context():
            db.session.query(__import__("models").OtpCode).delete(); db.session.commit()


def test_admin_rights_come_from_the_database_not_the_cookie(client, make_user, sent_codes):
    uid = make_user(email="boss@example.com", admin=True, profile=False)
    log_in_with_otp(client, sent_codes, email="boss@example.com")
    with main.app.app_context():
        db.session.get(User, uid).is_admin = False
        db.session.commit()
    assert client.get("/admin").status_code == 403                                 # rights live in the database


def test_sensitive_actions_need_a_fresh_login(client, user, sent_codes):
    log_in_with_otp(client, sent_codes)
    close_browser(client)                                                         # now only the remember cookie
    r = client.post("/delete-account")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    with main.app.app_context():
        assert db.session.get(User, user) is not None                             # nothing was deleted
    assert client.post("/payments/1/refund-request").status_code == 302


# TEST 9 -------------------------------------------------------------------------------
def forged(user_id, issued_at, key=None):
    with main.app.app_context():
        return encode_cookie(f"{user_id}:{issued_at}", key=key)


def test_cookie_older_than_the_lifetime_is_rejected_by_the_server(client, user):
    client.set_cookie(COOKIE, forged(user, int(time.time()) - 31 * 86400))
    assert not is_logged_in(client)
    client.set_cookie(COOKIE, forged(user, int(time.time()) - 29 * 86400))
    assert is_logged_in(client)                                                   # still inside the 30 days


def test_cookie_from_the_future_is_rejected(client, user):
    client.set_cookie(COOKIE, forged(user, int(time.time()) + 86400))
    assert not is_logged_in(client)


def test_browser_side_expiry(client, user, sent_codes):
    log_in_with_otp(client, sent_codes)
    client.delete_cookie(COOKIE)                                                  # what the browser does at expiry
    close_browser(client)
    assert not is_logged_in(client)


# TEST 10 ------------------------------------------------------------------------------
def test_tampered_or_invalid_cookies_are_rejected(client, user, make_user, sent_codes):
    other = make_user(email="other@example.com", admin=True, profile=False)
    log_in_with_otp(client, sent_codes)
    good = client.get_cookie(COOKIE).value
    payload, signature = good.rsplit("|", 1)
    now = int(time.time())
    bad_values = [
        good[:-1] + ("0" if good[-1] != "0" else "1"),                           # altered signature
        f"{other}:{now}|{signature}",                                              # swap in another (admin) id
        f"{other}:{now}",                                                          # unsigned
        forged(other, now, key="a-different-secret-key-entirely-0000"),            # signed with the wrong key
        "garbage", "", "|", "1|2|3", f"abc:{now}|{signature}", f"{user}:abc|{signature}",
        "9" * 400,
    ]
    for value in bad_values:
        fresh = main.app.test_client()
        fresh.set_cookie(COOKIE, value)
        assert fresh.get("/fitness-profile").status_code == 302, value
        assert fresh.get("/admin").status_code == 302, value
    fresh = main.app.test_client()
    fresh.set_cookie(COOKIE, good)
    assert is_logged_in(fresh)                                                    # the genuine one still works


def test_legacy_session_format_still_works(client, user):
    login(client, user)                                                           # plain "<id>" sessions from before the change
    assert is_logged_in(client)


# Existing workflows -------------------------------------------------------------------
def test_workflows_work_with_a_remembered_session(client, user, sent_codes, make_trainer, gateway):
    make_trainer(name="Rani", gender="male")
    log_in_with_otp(client, sent_codes)
    close_browser(client)
    assert client.get("/").status_code == 200
    assert "Rani" in client.get("/find-trainer?mode=online").get_data(as_text=True)
    assert client.get("/workout-plan/status").status_code == 200
    order = client.post("/payments/create-order", json={"tier": "personalized"})      # fake gateway: no real call
    assert order.status_code == 200 and order.get_json()["ok"] is True


def test_no_remember_token_in_page_javascript_or_storage(client, user, sent_codes):
    log_in_with_otp(client, sent_codes)
    page = client.get("/").get_data(as_text=True)
    assert "localStorage" not in page and "sessionStorage" not in page and COOKIE not in page
