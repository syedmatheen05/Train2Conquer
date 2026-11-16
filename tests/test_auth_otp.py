"""Login / registration / OTP security.

Regression: the OTP used to be stored in the session cookie, which the browser
can read, so anyone could log in as any e-mail address.
"""
import base64
import json
import zlib
from datetime import timedelta

import main
from models import OtpCode, User, db, utcnow
from services import otp as otp_service


def decode_cookie(client):
    """What a visitor can read from their own session cookie (it is signed, not encrypted)."""
    cookie = client.get_cookie("session")
    if cookie is None:
        return {}
    value = cookie.value
    compressed = value.startswith(".")
    payload = value.split(".")[1 if compressed else 0]
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    return json.loads(zlib.decompress(raw) if compressed else raw)


def request_code(client, email="a@example.com"):
    return client.post("/login", data={"email": email})


def test_code_is_never_readable_from_the_cookie(client, make_user, sent_codes):
    make_user(email="a@example.com")
    request_code(client)
    code = sent_codes[0][1]
    visible = decode_cookie(client)
    assert visible.get("email") == "a@example.com"            # proves the decoder really reads the cookie
    assert code not in json.dumps(visible) and "otp" not in visible
    with main.app.app_context():                           # stored hashed, never in clear text
        row = db.session.scalar(db.select(OtpCode))
        assert row.code_hash != code and len(row.code_hash) == 64


def test_correct_code_logs_in_and_works_only_once(client, make_user, sent_codes):
    make_user(email="a@example.com")
    request_code(client)
    code = sent_codes[0][1]
    r = client.post("/verify-otp", data={"verification_code": code})
    assert r.status_code == 302 and client.get("/fitness-profile").status_code == 200
    client.post("/logout")
    # the same code can't be reused, even by someone holding the session
    with client.session_transaction() as s:
        s["email"], s["otp_purpose"] = "a@example.com", "login"
    client.post("/verify-otp", data={"verification_code": code})
    assert client.get("/fitness-profile").status_code == 302       # still logged out


def test_wrong_codes_lock_the_code_and_cookie_replay_cannot_reset_it(client, make_user, sent_codes):
    make_user(email="a@example.com")
    request_code(client)
    code = sent_codes[0][1]
    wrong = "000000" if code != "000000" else "111111"
    saved = client.get_cookie("session").value                      # attacker saves the cookie...
    for _ in range(5):
        client.post("/verify-otp", data={"verification_code": wrong})
        client.set_cookie("session", saved)                          # ...and replays it every time
    r = client.post("/verify-otp", data={"verification_code": code})  # even the right code is now refused
    assert client.get("/fitness-profile").status_code == 302
    with main.app.app_context():
        assert db.session.scalar(db.select(OtpCode.attempts)) >= otp_service.MAX_ATTEMPTS


def test_expired_code_is_refused(client, make_user, sent_codes):
    make_user(email="a@example.com")
    request_code(client)
    with main.app.app_context():
        row = db.session.scalar(db.select(OtpCode))
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    client.post("/verify-otp", data={"verification_code": sent_codes[0][1]})
    assert client.get("/fitness-profile").status_code == 302


def test_code_for_one_purpose_cannot_be_used_for_another(client, sent_codes):
    client.post("/contact", data={"name": "Guest", "email": "g@example.com", "message": "Hello there, testing."})
    code = sent_codes[0][1]
    with client.session_transaction() as s:
        s["email"], s["otp_purpose"] = "g@example.com", "login"      # try to log in with a contact code
    client.post("/verify-otp", data={"verification_code": code})
    assert client.get("/fitness-profile").status_code == 302


def test_unknown_email_looks_exactly_like_a_real_one_and_sends_nothing(client, make_user, sent_codes):
    make_user(email="real@example.com")
    known = client.post("/login", data={"email": "real@example.com"}, follow_redirects=True)
    other = main.app.test_client()
    unknown = other.post("/login", data={"email": "nobody@example.com"}, follow_redirects=True)
    assert known.status_code == unknown.status_code == 200
    assert known.request.path == unknown.request.path == "/verify-otp"
    assert "don't have an account" not in unknown.get_data(as_text=True)
    assert [e for e, _ in sent_codes] == ["real@example.com"]          # nothing was mailed to the stranger
    # a decoy cannot be guessed into a login either
    r = other.post("/verify-otp", data={"verification_code": "123456"})
    assert other.get("/fitness-profile").status_code == 302


def test_registering_an_existing_email_does_not_reveal_it(client, make_user, sent_codes):
    make_user(email="real@example.com")
    r = client.post("/register", data={"name": "X", "email": "real@example.com"}, follow_redirects=True)
    assert "already exists" not in r.get_data(as_text=True) and r.request.path == "/verify-otp"


def test_registration_creates_account_after_verification(client, sent_codes):
    client.post("/register", data={"name": "New Person", "email": "new@example.com"})
    r = client.post("/verify-otp", data={"verification_code": sent_codes[0][1]})
    assert r.status_code == 302 and "fitness-profile" in r.headers["Location"]
    with main.app.app_context():
        assert db.session.scalar(db.select(User).where(User.email == "new@example.com")).name == "New Person"


def test_resend_cooldown_cannot_be_bypassed_by_clearing_cookies(client, make_user, sent_codes):
    make_user(email="a@example.com")
    request_code(client)
    fresh = main.app.test_client()
    fresh.post("/login", data={"email": "a@example.com"})
    assert len(sent_codes) == 1                                       # second request refused by the database


def test_hourly_code_cap(make_user, sent_codes):
    make_user(email="a@example.com")
    with main.app.app_context():
        for _ in range(otp_service.MAX_CODES_PER_HOUR):
            db.session.add(OtpCode(email="a@example.com", purpose="login", code_hash="x" * 64,
                                   created_at=utcnow() - timedelta(minutes=5),
                                   expires_at=utcnow() + timedelta(minutes=5), consumed_at=utcnow()))
        db.session.commit()
        try:
            otp_service.issue_code("a@example.com", "login", lambda e, c: None)
            raised = False
        except otp_service.OtpRateLimited:
            raised = True
    assert raised


def test_suspended_user_cannot_log_in_and_is_logged_out_immediately(client, make_user, sent_codes):
    uid = make_user(email="a@example.com")
    from conftest import login
    login(client, uid)
    assert client.get("/fitness-profile").status_code == 200
    with main.app.app_context():
        db.session.get(User, uid).is_active = False
        db.session.commit()
    assert client.get("/fitness-profile").status_code == 302          # session ended by the suspension
    fresh = main.app.test_client()
    fresh.post("/login", data={"email": "a@example.com"})
    assert sent_codes == []                                           # no code is even e-mailed
