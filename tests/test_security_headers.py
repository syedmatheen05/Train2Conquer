import main
from conftest import login


def test_security_headers_on_public_pages(client):
    h = client.get("/about").headers
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in h["Content-Security-Policy"] and "object-src 'none'" in h["Content-Security-Policy"]
    assert "Permissions-Policy" in h and "Strict-Transport-Security" not in h        # HSTS only in production


def test_private_pages_are_not_cacheable(client, make_user):
    assert client.get("/about").headers.get("Cache-Control") != "no-store"
    login(client, make_user(admin=True, profile=False))
    assert client.get("/admin").headers["Cache-Control"] == "no-store"
    assert client.get("/fitness-profile").headers["Cache-Control"] == "no-store"


def test_no_secrets_in_templates_or_static_js():
    import glob
    blob = "".join(open(f, errors="ignore").read() for f in glob.glob("templates/**/*.html", recursive=True) + glob.glob("static/js/*.js"))
    import re
    for needle in ("RAZORPAY_KEY_SECRET", "GEMINI_API_KEY", "SECRET_KEY"):
        assert needle not in blob, needle
    assert not re.search(r"sk-[A-Za-z0-9_-]{20,}|rzp_(live|test)_[A-Za-z0-9]{6,}|AIza[0-9A-Za-z_-]{20,}", blob)
