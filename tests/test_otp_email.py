"""OTP e-mail: layout, plain-text fallback, and that the code never leaks into logs or the subject."""
import logging

import main
from conftest import login
from services import emails


def parts(msg):
    out = {}
    for p in msg.walk():
        out[p.get_content_type()] = p
    return out


def body(part):
    return part.get_payload(decode=True).decode()


def test_message_has_text_html_and_inline_logo():
    msg = emails.build_otp_message("sender@gmail.com", "a@b.com", "123456", 600)
    p = parts(msg)
    assert {"text/plain", "text/html", "image/png"} <= set(p)
    assert p["image/png"]["Content-ID"] == "<t2c-mark>" and "cid:t2c-mark" in body(p["text/html"])
    html, text = body(p["text/html"]), body(p["text/plain"])
    for content in (html, text):
        assert "123456" in content and "10 minutes" in content
        assert "Never share your verification code with anyone" in content
    assert "Verify Your Email Address" in html
    assert "<script" not in html and "background-image" not in html
    assert len(msg.as_string()) < 100_000                      # Gmail clips larger messages


def test_code_is_not_in_the_subject_or_headers():
    msg = emails.build_otp_message("sender@gmail.com", "a@b.com", "654321", 600)
    assert "654321" not in msg["Subject"] and all("654321" not in str(v) for k, v in msg.items())


def test_recipient_is_escaped_in_html():
    html = emails.otp_html("111111", 10, '"><script>alert(1)</script>')
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


def test_expiry_text_follows_the_real_lifetime():
    assert main.OTP_EXPIRY_SECONDS == 600
    assert "5 minutes" in emails.otp_text("111111", 5)


def test_failed_send_logs_no_code(client, make_user, monkeypatch, caplog):
    make_user(email="u@example.com")

    def boom(email, code):
        raise ConnectionError("smtp down")

    monkeypatch.setattr(main, "send_verification_code", boom)
    caplog.set_level(logging.DEBUG)
    r = client.post("/login", data={"email": "u@example.com"})
    assert r.status_code == 302
    assert "ConnectionError" in caplog.text
    import re
    assert not re.search(r"\b\d{6}\b", caplog.text)


def test_sender_uses_the_builder_and_never_prints_the_code(monkeypatch, capsys):
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, u, p): pass
        def sendmail(self, frm, to, raw): sent.append((frm, to, raw))

    monkeypatch.setenv("EMAIL", "me@gmail.com"); monkeypatch.setenv("PASSWORD", "app-pass")
    monkeypatch.setattr(main.smtplib, "SMTP", FakeSMTP)
    main.send_verification_code("user@example.com", "987654")
    import email as email_lib
    parsed = email_lib.message_from_string(sent[0][2])
    assert sent[0][1] == ["user@example.com"]
    assert "987654" in body(parts(parsed)["text/html"])
    out = capsys.readouterr()
    assert "987654" not in out.out + out.err
