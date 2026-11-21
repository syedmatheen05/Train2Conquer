"""Safe 'go back to where you were headed' handling (prevents open redirects)."""
from urllib.parse import urlsplit

SESSION_KEY = "post_login_next"


def safe_next(raw, host=None):
    """Returns a same-site path (with query) or None. Anything else is ignored."""
    if not isinstance(raw, str) or not raw or len(raw) > 500:
        return None
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        if host and parts.netloc == host and parts.scheme in ("http", "https"):
            raw = parts.path + (("?" + parts.query) if parts.query else "")
        else:
            return None
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw or any(ord(c) < 32 for c in raw):
        return None
    return raw
