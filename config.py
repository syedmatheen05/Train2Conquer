"""Central settings for Train2Conquer.

Everything that changes between your laptop and the live website is read
from environment variables HERE, in one place. Nothing in this file contains
a secret value, and nothing here ever prints one.

Two modes:
  * PRODUCTION  - the real website. Real Razorpay Live keys are required.
  * DEVELOPMENT - your computer. Razorpay TEST keys are allowed.
"""
import os
from decimal import Decimal, InvalidOperation

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


# --------------------------------------------------------------- AI models
MODEL_ENV = {"personalized": "PERSONALIZED_PLAN_MODEL", "advanced": "ADVANCED_PLAN_MODEL"}
MODEL_DEFAULTS = {"personalized": "gpt-6-luna", "advanced": "gpt-6.1-sol"}


def plan_model(tier):
    """Model id for a paid tier. Comes only from the environment (with the
    verified defaults). Never shown to customers."""
    name = MODEL_ENV.get(tier)
    if not name:
        return ""
    return _env(name) or MODEL_DEFAULTS[tier]


# ---------------------------------------------------------- Money settings
def _decimal_env(name, default=None):
    raw = _env(name)
    if not raw:
        return default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return default
    return value if value >= 0 else default


def usd_to_inr():
    """Configurable exchange rate. None means 'not set' (INR cost = unknown)."""
    rate = _decimal_env("OPENAI_USD_TO_INR")
    return rate if rate and rate > 0 else None


def daily_ai_limit_usd():
    return _decimal_env("OPENAI_DAILY_LIMIT_USD", Decimal("5.00"))


def per_generation_limit_usd():
    return _decimal_env("OPENAI_PER_GENERATION_LIMIT_USD", Decimal("0.50"))


def int_env(name, default):
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def max_output_tokens():
    return int_env("PAID_AI_MAX_OUTPUT_TOKENS", 9000)


def max_ai_attempts():
    """API calls allowed per generation job (first try + repairs). 1-4."""
    return max(1, min(4, int_env("PAID_AI_MAX_ATTEMPTS", 2)))


def daily_ai_request_limit():
    return int_env("OPENAI_DAILY_MAX_REQUESTS", 500)


# ------------------------------------------------------------ App startup
def validate_startup():
    """Problems that must stop a PRODUCTION start. Returns a list of strings."""
    problems = []
    if is_production():
        secret = _env("SECRET_KEY")
        if not secret or secret == DEFAULT_SECRET_KEY or len(secret) < 24:
            problems.append("SECRET_KEY must be a long random value (24+ characters) in production.")
    return problems


def generation_mode():
    """'inline' = web server runs jobs itself, 'worker' = separate worker.py."""
    mode = _env("GENERATION_MODE", "inline").lower()
    return mode if mode in ("inline", "worker") else "inline"
