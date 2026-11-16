"""Razorpay helpers for Train2Conquer paid plans.

This file has NO database code. It only talks to Razorpay and reads the
settings in config.py, so main.py stays in charge of saving anything.
Prices always come from the server, never from the browser. The key SECRET
is only ever used inside this file and is never returned, logged or sent to
the browser.
"""
import razorpay

import config

PAID_TIERS = config.PAID_TIERS
CURRENCY = config.CURRENCY


class BillingError(Exception):
    """Raised for any payment-gateway problem. The message is safe to log."""


def price_paise(tier):
    """Price in paise (100 paise = Rs 1) for a paid tier, from the server."""
    try:
        return config.price_paise(tier)
    except config.ConfigError as exc:
        raise BillingError(str(exc))


def public_key_id():
    return config.razorpay_public_key()


def is_configured():
    """True only when the keys are valid for the current environment
    (production = live keys only)."""
    return config.payments_enabled()


def config_problem():
    return config.razorpay_problem()


def _client():
    problem = config.razorpay_problem()
    if problem:
        raise BillingError("payment gateway is not configured: " + problem)
    import os
    client = razorpay.Client(auth=(os.environ["RAZORPAY_KEY_ID"].strip(), os.environ["RAZORPAY_KEY_SECRET"].strip()))
    return client


def create_order(amount_paise, receipt, notes=None):
    try:
        return _client().order.create({
            "amount": int(amount_paise),
            "currency": CURRENCY,
            "receipt": receipt,
            "notes": notes or {},
        })
    except BillingError:
        raise
    except Exception as exc:  # network errors, bad keys, etc. (message never includes secrets)
        raise BillingError(f"create_order failed: {type(exc).__name__}")


def verify_payment_signature(order_id, payment_id, signature):
    """True only if Razorpay's signature for this order+payment is valid."""
    try:
        _client().utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
        return True
    except razorpay.errors.SignatureVerificationError:
        return False
    except BillingError:
        raise
    except Exception as exc:
        raise BillingError(f"signature check failed: {type(exc).__name__}")


def fetch_payment(payment_id):
    """Ask Razorpay directly about a payment (never trust the browser)."""
    try:
        return _client().payment.fetch(payment_id)
    except BillingError:
        raise
    except Exception as exc:
        raise BillingError(f"fetch_payment failed: {type(exc).__name__}")


def capture_payment(payment_id, amount_paise):
    try:
        return _client().payment.capture(payment_id, int(amount_paise), {"currency": CURRENCY})
    except BillingError:
        raise
    except Exception as exc:
        raise BillingError(f"capture failed: {type(exc).__name__}")


def create_refund(payment_id, amount_paise, notes=None):
    """Full/partial refund of a captured payment. Only called from the explicit
    admin refund action in main.py - never automatically."""
    try:
        return _client().payment.refund(payment_id, {
            "amount": int(amount_paise), "speed": "normal", "notes": notes or {},
        })
    except BillingError:
        raise
    except Exception as exc:
        raise BillingError(f"refund failed: {type(exc).__name__}")


def verify_webhook(raw_body, signature):
    """True only if the webhook really came from Razorpay.

    Checks an HMAC-SHA256 of the RAW request bytes using the webhook secret
    (not the key secret), exactly as Razorpay documents it.
    """
    import hashlib
    import hmac
    import os
    secret = (os.environ.get("RAZORPAY_WEBHOOK_SECRET") or "").strip()
    if not secret or not signature or not isinstance(raw_body, (bytes, bytearray)):
        return False
    expected = hmac.new(secret.encode("utf-8"), bytes(raw_body), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(signature).strip())
