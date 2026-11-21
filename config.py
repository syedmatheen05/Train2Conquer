"""Central settings for Train2Conquer.

Everything that changes between your laptop and the live website is read
from environment variables HERE, in one place. Nothing in this file contains
a secret value, and nothing here ever prints one.

Two modes:
  * PRODUCTION  - the real website. Real Razorpay Live keys are required.
  * DEVELOPMENT - your computer. Razorpay TEST keys are allowed.
"""
import os

DEFAULT_SECRET_KEY = "change-this-in-production"


class ConfigError(Exception):
    """A setting is missing or wrong. The message is safe to show/log."""


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def app_environment():
    """'production' or 'development'.

    APP_ENV (or the older FLASK_ENV) decides. If neither is set, a hosting
    platform like Render or Vercel is treated as production, so a forgotten
    variable can never leave the live site in test mode.
    """
    explicit = (_env("APP_ENV") or _env("FLASK_ENV")).lower()
    if explicit in ("production", "prod"):
        return "production"
    if explicit in ("development", "dev", "local", "testing", "test"):
        return "development"
    if os.environ.get("RENDER") or os.environ.get("VERCEL"):
        return "production"
    return "development"


def is_production():
    return app_environment() == "production"


# ---------------------------------------------------------------- Razorpay
def razorpay_mode():
    """'live', 'test' or 'missing' - based only on the key id prefix."""
    key_id = _env("RAZORPAY_KEY_ID")
    if key_id.startswith("rzp_live_"):
        return "live"
    if key_id.startswith("rzp_test_"):
        return "test"
    return "missing" if not key_id else "invalid"


def razorpay_problem():
    """Returns None when payments may run, otherwise a short safe reason.

    Production: only rzp_live_ keys are accepted. rzp_test_ keys are rejected.
    Development: both live and test keys are accepted (test recommended).
    There is NEVER a silent fallback to test mode.
    """
    key_id = _env("RAZORPAY_KEY_ID")
    if not key_id or not _env("RAZORPAY_KEY_SECRET"):
        return "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are not set."
    mode = razorpay_mode()
    if mode == "invalid":
        return "RAZORPAY_KEY_ID must start with rzp_live_ (production) or rzp_test_ (development)."
    if is_production():
        if mode != "live":
            return "Production requires a Razorpay LIVE key (rzp_live_...). Test keys are rejected."
        if not _env("RAZORPAY_WEBHOOK_SECRET"):
            return "RAZORPAY_WEBHOOK_SECRET is not set (required in production)."
    return None


def payments_enabled():
    return razorpay_problem() is None


def razorpay_public_key():
    """Only the public key id may ever reach the browser."""
    return _env("RAZORPAY_KEY_ID")


# ------------------------------------------------------------------ Prices
CURRENCY = "INR"
PAID_TIERS = ("personalized", "advanced")
_DEFAULT_PRICE_PAISE = {"personalized": 500, "advanced": 1500}   # Rs 5 and Rs 15
_PRICE_ENV = {"personalized": "PERSONALIZED_PRICE_PAISE", "advanced": "ADVANCED_PRICE_PAISE"}


def price_paise(tier):
    """Price in paise (100 paise = Rs 1), decided on the server only."""
    if tier not in PAID_TIERS:
        raise ConfigError("unknown tier")
    raw = _env(_PRICE_ENV[tier])
    try:
        value = int(raw) if raw else _DEFAULT_PRICE_PAISE[tier]
    except ValueError:
        value = _DEFAULT_PRICE_PAISE[tier]
    return value if value >= 100 else _DEFAULT_PRICE_PAISE[tier]   # Razorpay minimum is Rs 1


# --------------------------------------------------------------- AI (Gemini only)
# One provider for every plan type (Basic, Personalized, Advanced): Google Gemini,
# through gemini_service.py. The key is GEMINI_API_KEY (as before).
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"


def gemini_model():
    """Model id (same one the Basic plan always used). Optional override: GEMINI_MODEL."""
    return _env("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


def int_env(name, default):
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def ai_timeout_seconds():
    return max(10, int_env("PAID_AI_TIMEOUT_SECONDS", 90))


def max_output_tokens():
    return int_env("PAID_AI_MAX_OUTPUT_TOKENS", 9000)


def max_ai_attempts():
    """Answers we may ask for per generation (first try + repairs after a bad answer). 1-4."""
    return max(1, min(4, int_env("PAID_AI_MAX_ATTEMPTS", 2)))


def gemini_max_tries():
    """Network-level tries per request for transient errors (rate limit, 5xx, timeout). 1-5."""
    return max(1, min(5, int_env("GEMINI_MAX_TRIES", 3)))


def daily_ai_request_limit():
    """Safety valve for the free Gemini quota: requests per UTC day across all plans."""
    return int_env("GEMINI_DAILY_MAX_REQUESTS", 500)


# ------------------------------------------------------------ App startup
def validate_startup():
    """Problems that must stop a PRODUCTION start. Returns a list of strings."""
    problems = []
    if is_production():
        secret = _env("SECRET_KEY")
        if not secret or secret == DEFAULT_SECRET_KEY or len(secret) < 24:
            problems.append("SECRET_KEY must be a long random value (24+ characters) in production.")
    return problems


def remember_me_days():
    """How long a "Remember Me" login lasts (REMEMBER_ME_DAYS, default 30, limited to 1-90).
    It is a fixed lifetime from the moment of login, never renewed by visiting."""
    return max(1, min(90, int_env("REMEMBER_ME_DAYS", 30)))


def generation_mode():
    """'inline' = web server runs jobs itself, 'worker' = separate worker.py."""
    mode = _env("GENERATION_MODE", "inline").lower()
    return mode if mode in ("inline", "worker") else "inline"


def admin_fresh_login_mode():
    """When must an admin confirm their e-mail again (a "fresh" login)?

    "writes" (default): only for admin actions that change something (POST: delete, refund, ...).
        Opening admin pages works for any logged-in admin, including one restored by Remember Me.
    "always": every admin page needs a fresh login (the strictest setting).
    Admin rights themselves are ALWAYS checked on the server, from the database, on every request.
    """
    return "always" if _env("ADMIN_REQUIRE_FRESH_LOGIN", "writes").lower() == "always" else "writes"
