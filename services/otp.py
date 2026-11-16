"""E-mail verification codes (OTP), stored and checked on the server.

Why this exists
---------------
The code used to be kept in Flask's session cookie. That cookie is signed but
NOT encrypted, so anyone could base64-decode their own cookie, read the code
and log in as any e-mail address without owning it. Now:

* only a keyed hash (HMAC-SHA256 with SECRET_KEY) of the code is stored, in the
  `otp_codes` table - the browser never receives the code or a hash of it;
* the wrong-attempt counter lives in the database, so replaying an old cookie
  cannot reset it;
* a code works once, expires after OTP_LIFETIME_SECONDS, and requesting a new
  code invalidates the previous one;
* resend cooldown and an hourly cap are also enforced from the database, so
  clearing cookies does not bypass them.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from flask import current_app

from models import OtpCode, db, utcnow

OTP_LIFETIME_SECONDS = 10 * 60
RESEND_COOLDOWN_SECONDS = 30
MAX_ATTEMPTS = 5
MAX_CODES_PER_HOUR = 6
PURPOSES = ("login", "registration", "contact")

# verify_code() results
OK, INVALID, EXPIRED, LOCKED = "ok", "invalid", "expired", "locked"


class OtpRateLimited(ValueError):
    """Raised with a user-safe message when a new code may not be sent yet."""


def _hash_code(email, purpose, code):
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"{email}|{purpose}|{code}".encode(), hashlib.sha256).hexdigest()


def _normalise(email):
    return (email or "").strip().lower()


def _latest_active(email, purpose):
    return db.session.execute(
        db.select(OtpCode)
        .where(OtpCode.email == email, OtpCode.purpose == purpose, OtpCode.consumed_at.is_(None))
        .order_by(OtpCode.id.desc()).limit(1)
    ).scalar_one_or_none()


def issue_code(email, purpose, send):
    """Create a code, e-mail it with `send(email, code)` and store its hash.

    The e-mail is sent first: if sending fails nothing usable is stored.
    Raises OtpRateLimited when asked too soon or too often.

    `send=None` stores a DECOY: a real-looking row whose code nobody ever
    receives. Callers use it for addresses that have no usable account, so the
    next page, the cooldown and the attempt lock behave identically and the
    response cannot reveal whether an account exists.
    """
    if purpose not in PURPOSES:
        raise ValueError("unknown OTP purpose")
    email = _normalise(email)
    if not email:
        raise ValueError("An email address is required")
    now = utcnow()

    latest = db.session.execute(
        db.select(OtpCode).where(OtpCode.email == email).order_by(OtpCode.id.desc()).limit(1)
    ).scalar_one_or_none()
    if latest and (now - latest.created_at).total_seconds() < RESEND_COOLDOWN_SECONDS:
        raise OtpRateLimited("Please wait before requesting another verification code.")
    recent = db.session.scalar(
        db.select(db.func.count(OtpCode.id)).where(OtpCode.email == email, OtpCode.created_at > now - timedelta(hours=1)))
    if recent >= MAX_CODES_PER_HOUR:
        raise OtpRateLimited("Too many verification codes were requested. Please try again later.")

    code = f"{secrets.randbelow(1_000_000):06d}"
    if send is not None:
        send(email, code)

    # A new code replaces any earlier unused one; old rows are cleaned up lazily.
    for old in db.session.execute(db.select(OtpCode).where(
            OtpCode.email == email, OtpCode.consumed_at.is_(None))).scalars():
        old.consumed_at = now
    db.session.execute(db.delete(OtpCode).where(OtpCode.created_at < now - timedelta(days=1)))
    db.session.add(OtpCode(email=email, purpose=purpose, code_hash=_hash_code(email, purpose, code),
                           created_at=now, expires_at=now + timedelta(seconds=OTP_LIFETIME_SECONDS)))
    db.session.commit()


def verify_code(email, purpose, submitted):
    """Check a submitted code. Returns (result, attempts_left).

    result is OK, INVALID, EXPIRED or LOCKED. A correct code is consumed so it
    can never be used twice. Unknown e-mails give the same answer as a wrong
    code, so this cannot be used to discover who has an account.
    """
    email = _normalise(email)
    row = _latest_active(email, purpose)
    if row is None:
        return INVALID, 0
    now = utcnow()
    if row.expires_at <= now:
        row.consumed_at = now
        db.session.commit()
        return EXPIRED, 0
    if row.attempts >= MAX_ATTEMPTS:
        return LOCKED, 0
    row.attempts += 1
    expected = row.code_hash
    actual = _hash_code(email, purpose, (submitted or "").strip())
    if hmac.compare_digest(expected, actual):
        row.consumed_at = now
        db.session.commit()
        return OK, MAX_ATTEMPTS - row.attempts
    db.session.commit()
    if row.attempts >= MAX_ATTEMPTS:
        row.consumed_at = now
        db.session.commit()
        return LOCKED, 0
    return INVALID, MAX_ATTEMPTS - row.attempts


def seconds_until_expiry(email, purpose):
    row = _latest_active(_normalise(email), purpose)
    if row is None:
        return 0
    return max(0, int((row.expires_at - utcnow()).total_seconds()))


def seconds_until_resend(email):
    latest = db.session.execute(
        db.select(OtpCode).where(OtpCode.email == _normalise(email)).order_by(OtpCode.id.desc()).limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return 0
    return max(0, int(RESEND_COOLDOWN_SECONDS - (utcnow() - latest.created_at).total_seconds()))
