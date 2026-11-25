import os

# On Windows, security policies (Smart App Control / App Control for Business)
# can block unsigned compiled extension (.pyd) files. SQLAlchemy's optional
# Cython speed-ups are such files, so skip them there and use its built-in
# pure-Python code instead. Must be set BEFORE sqlalchemy is imported.
if os.name == "nt":
    os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")

import hashlib, html, json, re, secrets, smtplib, socket, threading, time
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()   # must run before config/billing read the environment

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for, Response
from flask_bootstrap import Bootstrap5
from extensions import limiter
from flask_login import LoginManager, current_user, fresh_login_required, login_required, login_user, logout_user
from flask_wtf import CSRFProtect
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from werkzeug.middleware.proxy_fix import ProxyFix
from forms import ContactForm, ContactOTPForm, FitnessProfileform, Loginform, OTPform, Registerform, TrainerSearchForm, TrainerForm
from exercises import EXERCISE_NAMES, EXERCISE_VIDEOS
from services import accounts as account_service, emails as email_templates, otp as otp_service, redirects, trainers as trainer_service
import gemini_service  # noqa: F401  (tests patch main.gemini_service)
import plan_cycle, plan_generator, billing, config, equipment as equipment_service, migrations, usage
from database import build_database_config
from models import FitnessProfile, Payment, PaymentEvent, User, WorkoutPlan, db, utcnow

# APP CONFIGURATION
IS_PRODUCTION = config.is_production()
_startup_problems = config.validate_startup()
if _startup_problems:
    raise RuntimeError("Configuration error: " + " ".join(_startup_problems))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", config.DEFAULT_SECRET_KEY)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION          # cookies only over https in production
# "Remember Me": Flask-Login's own signed cookie (no database table). HttpOnly so scripts can't
# read it, Secure over https in production, SameSite=Lax, host-only (no domain), fixed lifetime.
app.config["REMEMBER_COOKIE_SECURE"] = IS_PRODUCTION
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_PATH"] = "/"
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=config.remember_me_days())
app.config["REMEMBER_COOKIE_REFRESH_EACH_REQUEST"] = False   # no sliding window: expires N days after login
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 7
app.config["WTF_CSRF_TIME_LIMIT"] = 60 * 60 * 8
if IS_PRODUCTION:
    # Render/Vercel sit behind a proxy: trust its forwarded address/https headers.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=config.int_env("TRUSTED_PROXY_COUNT", 1), x_proto=1, x_host=1)

# Static files are cached for a week (filenames are not hash-versioned).
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 7

bootstrap = Bootstrap5(app)
csrf = CSRFProtect(app)

login_manager = LoginManager()
login_manager.login_view = "login" # type: ignore
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "warning"
# A session restored from the Remember Me cookie is "not fresh": sensitive actions ask for a new e-mail code.
login_manager.refresh_view = "login" # type: ignore
login_manager.needs_refresh_message = "For your security, please verify your e-mail again to continue."
login_manager.needs_refresh_message_category = "warning"
login_manager.init_app(app)


app.config["RATELIMIT_STORAGE_URI"] = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
limiter.init_app(app)

from admin import bp as admin_blueprint  # noqa: E402  (needs `app` extensions set up first)
app.register_blueprint(admin_blueprint)


# Lightweight response compression using only the standard library (no
# Flask-Compress dependency). Gzips text-based responses (HTML/CSS/JS/
# JSON/SVG) above a small size threshold, only when the browser says it
# accepts gzip, and only if the response isn't already encoded. This is
# the same real saving Flask-Compress would give for this app's actual
# traffic (server-rendered HTML pages + the CSS/JS this project already
# ships), without adding a dependency for it.
import gzip as _gzip
COMPRESSIBLE_MIMETYPES = {
    "text/html", "text/css", "text/javascript", "application/javascript",
    "application/json", "image/svg+xml",
}
MIN_COMPRESS_BYTES = 500

@app.after_request
def compress_response(response):
    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    already_encoded = "Content-Encoding" in response.headers
    mimetype_ok = response.mimetype in COMPRESSIBLE_MIMETYPES
    if not accepts_gzip or already_encoded or not mimetype_ok:
        return response
    if response.direct_passthrough:
        # Static files (send_from_directory) default to direct_passthrough
        # for efficient streaming + Range-request support. This app never
        # actually serves anything that needs Range requests (no local
        # video/large downloadable files -- exercise videos are external
        # URLs), so it's safe to disable passthrough here and buffer the
        # file in memory to compress it; ETag/Last-Modified conditional
        # caching (304 responses) is untouched since those headers are
        # already set before this hook runs.
        response.direct_passthrough = False
    data = response.get_data()
    if len(data) < MIN_COMPRESS_BYTES:
        return response
    compressed = _gzip.compress(data, compresslevel=6)
    response.set_data(compressed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(compressed))
    response.headers.setdefault("Vary", "Accept-Encoding")
    return response


# DATABASE
database_url, _engine_options = build_database_config()
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = _engine_options
db.init_app(app)

@app.after_request
def add_security_headers(response):
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(self)")
    # Only the directives that cannot break the page or Razorpay Checkout (inline scripts and
    # third-party scripts are still allowed; a stricter script-src would need a nonce rollout).
    headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'; base-uri 'self'; object-src 'none'")
    if IS_PRODUCTION:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    # Private pages must not be kept by the browser/proxies (e.g. Back button after logout).
    if response.mimetype == "text/html" and (current_user.is_authenticated or request.path.startswith("/admin")):
        headers["Cache-Control"] = "no-store"
    return response


# STARTUP: DATABASE SCHEMA + SAFETY CHECKS
SCHEMA_OK = True


def initialise_schema():
    """Development (or AUTO_MIGRATE=1): apply pending upgrades automatically.
    Production default: do NOT change the live database on start-up. Run
    `python manage.py migrate` (after a backup) instead; until then the site
    answers 503 rather than running against a half-upgraded database."""
    global SCHEMA_OK
    with app.app_context():
        try:
            pending = migrations.pending_migrations(db.engine)
            if pending:
                if (not IS_PRODUCTION) or os.environ.get("AUTO_MIGRATE") == "1":
                    migrations.run_migrations(db.engine)
                else:
                    SCHEMA_OK = False
                    print("DATABASE NEEDS UPGRADE: run `python manage.py migrate` (back up first). Site is paused until then.")
                    return
            # Optional bootstrap: promote ADMIN_EMAIL (never creates or demotes anyone).
            admin_email = os.environ.get("ADMIN_EMAIL")
            if admin_email:
                admin_user = db.session.execute(
                    db.select(User).where(User.email == admin_email.strip().lower())).scalar_one_or_none()
                if admin_user and not admin_user.is_admin:
                    admin_user.is_admin = True
                    db.session.commit()
        except Exception as exc:
            SCHEMA_OK = False
            print("DATABASE STARTUP ERROR:", type(exc).__name__)


initialise_schema()
print("Train2Conquer starting | mode: %s | razorpay key: %s | payments: %s | generation: %s" % (
    config.app_environment().upper(), config.razorpay_mode(),
    "ENABLED" if billing.is_configured() else "DISABLED (%s)" % billing.config_problem(),
    config.generation_mode()))


@app.before_request
def schema_gate():
    if not SCHEMA_OK and request.endpoint != "static" and request.path != "/healthz":
        return "Train2Conquer is being upgraded. Please try again in a few minutes.", 503


@app.route("/healthz")
@limiter.exempt
def healthz():
    """For hosting health checks. Contains no secrets."""
    return jsonify(status="ok" if SCHEMA_OK else "needs_migration", environment=config.app_environment(),
                   payments_enabled=billing.is_configured())

# AUTH HELPERS
SESSION_CLOCK_SKEW_SECONDS = 300


def _parse_session_id(raw):
    """Split "<id>:<login time>" into (user_id, login_time). Old-format sessions that are
    just "<id>" (from before this change) give (id, None). Garbage gives (None, None)."""
    user_part, _, issued_part = str(raw).partition(":")
    if not user_part.isdigit() or (issued_part and not issued_part.isdigit()):
        return None, None
    return int(user_part), (int(issued_part) if issued_part else None)


@login_manager.user_loader
def load_user(session_id):
    """Called on every request that carries a login session or Remember Me cookie.

    The browser's claim is never trusted on its own: the account is re-read from the
    database each time, so deleted or deactivated users, and any change of admin rights,
    take effect immediately. The cookie also expires on the server after REMEMBER_ME_DAYS.
    """
    user_id, issued_at = _parse_session_id(session_id)
    if user_id is None:
        return None
    if issued_at is not None:
        age = time.time() - issued_at
        if age > config.remember_me_days() * 86400 or age < -SESSION_CLOCK_SKEW_SECONDS:
            return None
    user = db.session.get(User, user_id)
    return user if user is not None and user.is_active else None


def send_trainer_application(email,name,about,location,gender,phone_number):
    """Send trainer application to the site owner/admin."""
    sender_email = os.environ.get("EMAIL")
    sender_password = os.environ.get("PASSWORD")
    if not sender_email or not sender_password:
        raise RuntimeError("EMAIL and PASSWORD environment variables are required")
    safe_email = html.escape(email or "")
    safe_name = html.escape(name or "")
    safe_about = html.escape(about or "").replace("\n", "<br>")
    safe_location = html.escape(location or "")
    safe_gender = html.escape(gender or "")
    safe_phone_number=html.escape(phone_number or "")

    mail = MIMEMultipart()

    mail["From"] = sender_email
    mail["To"] = sender_email
    mail["Reply-To"] = email
    mail["Subject"] = "Train2Conquer - Trainer Application"
    body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6;">
            <h2>🏋️ New Trainer Application</h2>
            <hr>
            <p><strong>Name:</strong><br>{safe_name}</p>
            <p><strong>Email:</strong><br>{safe_email}</p>
            <p><strong>Location:</strong><br>{safe_location}</p>
            <p><strong>Gender:</strong><br>{safe_gender}</p>
            <p><strong>Phone Number: </strong><br>{safe_phone_number}</p>
            <p><strong>About Yourself:</strong><br>{safe_about}</p>
            <hr>
            <p style="color: #777;"> This application was submitted through Train2Conquer.</p>
        </body>
    </html>
    """
    mail.attach(MIMEText(body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587,timeout=20) as server:
        server.starttls()
        server.login(sender_email,sender_password)
        server.sendmail(sender_email,[sender_email],mail.as_string())

# EMAIL / OTP
OTP_EXPIRY_SECONDS = otp_service.OTP_LIFETIME_SECONDS   # one source of truth: the e-mail text follows the real lifetime
OTP_RESEND_COOLDOWN = otp_service.RESEND_COOLDOWN_SECONDS
def send_verification_code(email, otp):
    """E-mail a verification code (same Gmail SMTP account as before).

    The message layout lives in services/emails.py. The code is never logged:
    callers log only the exception TYPE if sending fails.
    """
    sender_email = os.environ.get("EMAIL")
    sender_password = os.environ.get("PASSWORD")
    if not sender_email or not sender_password:
        raise RuntimeError("EMAIL and PASSWORD environment variables are required")
    message = email_templates.build_otp_message(sender_email, email, otp, OTP_EXPIRY_SECONDS)
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, [email], message.as_string())

def send_contact_info(email, name, contact_message, trainer=None):
    """E-mail a verified contact message to the site inbox.

    If `trainer` is given, the message is about that trainer: the trainer's name
    and id are added to the mail and, when the trainer has a contact e-mail on
    file, the mail is sent to the trainer with the site inbox in copy.
    """
    sender_email = os.environ.get("EMAIL")
    sender_password = os.environ.get("PASSWORD")
    if not sender_email or not sender_password:
        raise RuntimeError("EMAIL and PASSWORD environment variables are required")
    safe_email = html.escape(email or "")
    safe_name = html.escape(name or "")
    safe_message = html.escape(contact_message or "").replace("\n", "<br>")
    trainer_line = ""
    recipients = [sender_email]
    mail = MIMEMultipart()
    mail["From"] = sender_email
    mail["Reply-To"] = email
    mail["Subject"] = "Train2Conquer Contact Message"
    if trainer is not None:
        trainer_line = f"<p><strong>For trainer:</strong> {html.escape(trainer.name)} (ID {int(trainer.id)})</p>"
        mail["Subject"] = "Train2Conquer Trainer Enquiry"
        if trainer.contact_email:
            recipients = [trainer.contact_email, sender_email]
            mail["Cc"] = sender_email
    mail["To"] = recipients[0]
    body = f"""
    <html>
        <body style="font-family:Arial,sans-serif">
            <h2>{mail["Subject"]}</h2>
            <p><strong>From:</strong> {safe_name}</p>
            <p><strong>Email:</strong> {safe_email}</p>
            {trainer_line}
            <hr>
            <p>{safe_message}</p>
        </body>
    </html>
    """
    mail.attach(MIMEText(body, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipients, mail.as_string())

def generate_otp(email, purpose="login", deliver=True):
    """Start a verification: issue a code (see services/otp.py) and remember in
    the session only which e-mail/purpose it belongs to - never the code.

    deliver=False stores a decoy code that is never e-mailed. It is used for
    e-mail addresses with no usable account so that the page looks and behaves
    exactly the same (no account enumeration).
    """
    otp_service.issue_code(email, purpose, send_verification_code if deliver else None)
    session["email"] = email.strip().lower()
    session["otp_purpose"] = purpose


def check_existing_user(email):
    if not email:
        return None
    return db.session.execute(
        db.select(User).where(User.email == email.strip().lower())).scalar_one_or_none()


def clear_otp_session():
    for key in ("otp_purpose", "name", "email", "message", "trainer_id", "contact_mode", "remember_me", redirects.SESSION_KEY):
        session.pop(key, None)


def _start_login_code(email, purpose):
    """Issue a login/registration code, or a decoy if the account can't log in.
    Returns False (after flashing why) only if the e-mail could not be sent."""
    existing = check_existing_user(email)
    usable = existing is None if purpose == "registration" else (existing is not None and existing.is_active)
    try:
        generate_otp(email, purpose=purpose, deliver=usable)
    except otp_service.OtpRateLimited as exc:
        flash(str(exc), "warning")
        return False
    except Exception as exc:
        app.logger.error("Could not send verification code: %s", type(exc).__name__)
        flash("We couldn't send the verification code. Please try again.", "danger")
        return False
    return True


CODE_SENT_MESSAGE = "If this e-mail can be used, we've sent a 6-digit verification code to it."


# LOGIN / REGISTRATION
def _remember_destination():
    """?next=/admin/... (set by Flask-Login) is kept so verification can send the user there."""
    if request.method == "GET":
        destination = redirects.safe_next(request.args.get("next"), request.host)
        if destination:
            session[redirects.SESSION_KEY] = destination


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute;30 per hour", methods=["POST"])
def login():
    _remember_destination()
    login_form = Loginform()
    if login_form.validate_on_submit():
        email = login_form.email.data.strip().lower()
        session["remember_me"] = bool(login_form.remember.data)
        if not _start_login_code(email, "login"):
            return redirect(url_for("login"))
        flash(CODE_SENT_MESSAGE, "info")
        return redirect(url_for("verify_otp"))
    return render_template("login.html", form=login_form)


@app.route("/verify-otp", methods=["GET", "POST"])
@limiter.limit("20 per minute;100 per hour", methods=["POST"])
def verify_otp():
    email = session.get("email")
    purpose = session.get("otp_purpose")
    if not email or purpose not in {"login", "registration"}:
        flash("Your verification session has expired. Please request a new code.", "warning")
        return redirect(url_for("login"))
    verify_otp_form = OTPform()
    if verify_otp_form.validate_on_submit():
        result, _attempts_left = otp_service.verify_code(email, purpose, verify_otp_form.verification_code.data)
        if result == otp_service.EXPIRED:
            flash("That verification code has expired. Please request a new one.", "warning")
        elif result == otp_service.LOCKED:
            clear_otp_session()
            flash("Too many incorrect attempts. Please request a new code.", "danger")
            return redirect(url_for("login"))
        elif result != otp_service.OK:
            flash("That code isn't correct. Please check it and try again.", "danger")
        else:
            user = check_existing_user(email)
            if not user:
                name = session.get("name")
                if purpose != "registration" or not name:
                    flash("Registration information is missing. Please register again.", "danger")
                    clear_otp_session()
                    return redirect(url_for("register"))
                user = User(name=name.strip(), email=email)
                db.session.add(user)
                try:
                    db.session.commit()
                except IntegrityError:          # two requests registered the same e-mail
                    db.session.rollback()
                    user = check_existing_user(email)
            # The persistent cookie is only ever created here, AFTER the e-mail code was verified.
            if user is None or not login_user(user, remember=bool(session.get("remember_me"))):  # refuses suspended accounts
                clear_otp_session()
                flash("We couldn't sign you in. Please contact support.", "danger")
                return redirect(url_for("login"))
            was_registration = purpose == "registration"
            destination = redirects.safe_next(session.pop(redirects.SESSION_KEY, None), request.host)
            clear_otp_session()
            flash("Email verified successfully!", "success")
            if was_registration and not FitnessProfile.query.filter_by(user_id=user.id).first():
                return redirect(url_for("fitness_profile"))
            return redirect(destination or url_for("home"))
    return render_template("verify_otp.html", form=verify_otp_form,
                           remaining=otp_service.seconds_until_expiry(email, purpose),
                           resend_remaining=otp_service.seconds_until_resend(email), email=email)


@app.route("/resend-otp", methods=["POST"])
@limiter.limit("5 per minute;20 per hour")
def resend_otp():
    email = session.get("email")
    purpose = session.get("otp_purpose")
    if not email or purpose not in {"login", "registration", "contact"}:
        flash("No verification request was found. Please start again.", "warning")
        return redirect(url_for("login"))
    if purpose == "contact":
        sent = generate_otp_or_flash(email, purpose, deliver=True)
    else:
        sent = _start_login_code(email, purpose)
    if sent:
        flash("A new verification code has been sent.", "info")
    return redirect(url_for("verify_contact" if purpose == "contact" else "verify_otp"))


def generate_otp_or_flash(email, purpose, deliver=True):
    """generate_otp() that turns failures into flash messages. True if a code was issued."""
    try:
        generate_otp(email, purpose=purpose, deliver=deliver)
        return True
    except otp_service.OtpRateLimited as exc:
        flash(str(exc), "warning")
    except Exception as exc:
        app.logger.error("Could not send verification code: %s", type(exc).__name__)
        flash("We couldn't send the verification code. Please try again.", "danger")
    return False


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute;30 per hour", methods=["POST"])
def register():
    _remember_destination()
    register_form = Registerform()
    if register_form.validate_on_submit():
        email = register_form.email.data.strip().lower()
        session["name"] = register_form.name.data.strip()
        session["remember_me"] = bool(register_form.remember.data)
        # Existing accounts get a login code; the page says the same thing either way.
        purpose = "login" if check_existing_user(email) else "registration"
        if not _start_login_code(email, purpose):
            return redirect(url_for("register"))
        flash(CODE_SENT_MESSAGE, "info")
        return redirect(url_for("verify_otp"))
    return render_template("register.html", form=register_form)


# FITNESS PROFILE / AI PLAN
def profile_to_dict(profile):
    return {
        "dob": profile.dob.strftime("%d-%m-%Y"),
        "height": profile.height,
        "weight": profile.weight,
        "gender": profile.gender,
        "goal": profile.goal,
        "experience": profile.experience,
        "progression_score": profile.progression_score,
        "workout_days": profile.workout_days,
        "equipment": equipment_service.parse_stored(profile.equipment),   # blank (legacy) = ["none"]
    }


def validate_and_serialize_plan(ai_result, expected_workout_days, equipment=None):
    """Validate a raw AI JSON string and return the serialized plan.
    Pulled out of run_generation so the validation rules
    (used to check the AI response) live in exactly one place. Raises
    ValueError on anything invalid -- callers decide how to surface that.
    `equipment` (the user's saved list) makes the plan fail if it uses an
    exercise that needs equipment the user does not have.
    """
    parsed = json.loads(ai_result)
    if not isinstance(parsed, dict):
        raise ValueError("AI returned an invalid workout plan")
    required_days = {f"day_{i}" for i in range(1, 8)}
    if set(parsed.keys()) != required_days:
        raise ValueError("AI workout plan must contain exactly seven days")
    allowed_images = {
        "chest.jpg", "arms2.jpg", "back2.jpg", "mobility.jpg", "arms.jpg",
        "legs.jpg", "abs.jpg", "yoga.jpg", "shoulders.jpg", "back.jpg", "cardio.jpg"
    }
    workout_day_count = 0
    for day_key in sorted(required_days, key=lambda value: int(value.split("_")[1])):
        day = parsed[day_key]
        if not isinstance(day, list) or not day or not isinstance(day[0], list) or len(day[0]) != 3:
            raise ValueError(f"{day_key} is missing valid metadata")
        if not all(isinstance(value, str) and value.strip() for value in day[0]):
            raise ValueError(f"{day_key} has invalid metadata")
        if day[0][2] not in allowed_images:
            raise ValueError(f"{day_key} uses an unavailable image")
        exercises = day[1:]
        if len(exercises) >= 12:
            workout_day_count += 1
        if len(exercises) > 25:
            raise ValueError(f"{day_key} contains too many exercises")
        for exercise in exercises:
            if not isinstance(exercise, dict):
                raise ValueError(f"{day_key} contains an invalid exercise")
            if exercise.get("exercise") not in EXERCISE_NAMES:
                raise ValueError(f"Unknown exercise key in {day_key}: {exercise.get('exercise')}")
            if "reps" not in exercise or "seconds" not in exercise or "rest" not in exercise:
                raise ValueError(f"{day_key} contains an incomplete exercise")
            try:
                int(exercise["seconds"])
                int(exercise["rest"])
            except (TypeError, ValueError):
                raise ValueError(f"{day_key} contains invalid timer values")
    if workout_day_count != expected_workout_days:
        raise ValueError(f"AI generated {workout_day_count} main workout days; expected {expected_workout_days}")
    if equipment is not None:
        unavailable = equipment_service.disallowed_exercises(parsed, equipment)
        if unavailable:
            raise ValueError("Plan uses equipment the user does not have: " + ", ".join(unavailable))
    return json.dumps(parsed)


# PLAN GENERATION (Basic / Personalized / Advanced)
# Every generated week is its own WorkoutPlan row, so old weeks stay viewable.
# Users only ever see the plan names below -- never provider or model names.
#
# HOW A JOB WORKS (the database is the queue):
#   1. A row is saved with status "generating", generation_status "queued".
#   2. A runner "claims" it (atomic UPDATE sets locked_at/locked_by = a lease).
#      - GENERATION_MODE=inline : a thread in the web server runs it.
#      - GENERATION_MODE=worker : `python worker.py` runs it (recommended).
#   3. On success the row becomes "ready"; on failure "failed" (customer can
#      retry; paid retries reuse the same payment, no new charge).
#   A runner that dies leaves an expiring lease; the row is then marked
#   failed("stale") so the customer can retry. Nothing is lost.
TIER_LABELS = {"basic": "Basic Plan", "personalized": "Personalized Plan", "advanced": "Advanced Plan"}

MAX_BASIC_PER_DAY = config.int_env("MAX_BASIC_PLANS_PER_DAY", 3)
MAX_PAID_PER_DAY = config.int_env("MAX_PAID_PLANS_PER_DAY", 10)
MAX_GENERATION_ATTEMPTS = config.int_env("MAX_GENERATION_ATTEMPTS", 3)      # job starts per payment
GENERATION_LEASE_SECONDS = config.int_env("GENERATION_LEASE_SECONDS", 600)   # running job may take this long
QUEUE_WAIT_SECONDS = config.int_env("GENERATION_QUEUE_WAIT_SECONDS", 900)    # queued job may wait this long
SAFE_ERROR_CODES = {"config", "timeout", "rate_limit", "invalid_output", "provider_error", "stale", "budget"}
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


def get_profile(user_id):
    return db.session.execute(db.select(FitnessProfile).where(FitnessProfile.user_id == user_id)).scalar_one_or_none()


def latest_plan_row(user_id):
    """Newest plan row of any status (ready / generating / failed)."""
    return db.session.execute(db.select(WorkoutPlan).where(
        WorkoutPlan.user_id == user_id, WorkoutPlan.is_archived.is_(False)).order_by(WorkoutPlan.id.desc())).scalars().first()


def expire_stale_generations(user_id=None):
    """Fail jobs that can never finish: a runner died (lease expired) or nobody
    picked the job up in time. Customers can then retry (paid: no new charge).
    In inline mode, also start jobs whose thread was lost (e.g. server restart)."""
    now = utcnow()
    scope = [WorkoutPlan.user_id == user_id] if user_id else []
    db.session.execute(update(WorkoutPlan).where(
        WorkoutPlan.status == "generating", WorkoutPlan.locked_at.is_not(None),
        WorkoutPlan.locked_at < now - timedelta(seconds=GENERATION_LEASE_SECONDS), *scope,
    ).values(status="failed", generation_status="failed", error_message="stale", locked_at=None, locked_by=None))
    db.session.execute(update(WorkoutPlan).where(
        WorkoutPlan.status == "generating", WorkoutPlan.locked_at.is_(None),
        WorkoutPlan.created_at < now - timedelta(seconds=QUEUE_WAIT_SECONDS), *scope,
    ).values(status="failed", generation_status="failed", error_message="stale"))
    db.session.commit()
    if config.generation_mode() == "inline":
        waiting = db.session.execute(db.select(WorkoutPlan.id).where(
            WorkoutPlan.status == "generating", WorkoutPlan.locked_at.is_(None),
            WorkoutPlan.created_at < now - timedelta(seconds=20), *scope)).scalars().all()
        for plan_id in waiting:
            _start_thread(plan_id)


def active_generation(user_id):
    return db.session.execute(db.select(WorkoutPlan.id).where(WorkoutPlan.user_id == user_id, WorkoutPlan.status == "generating")).first() is not None


def current_plan_row(user_id):
    """The user's active plan: their newest READY week that is not archived."""
    return db.session.execute(db.select(WorkoutPlan).where(
        WorkoutPlan.user_id == user_id, WorkoutPlan.status == "ready", WorkoutPlan.is_archived.is_(False)
    ).order_by(WorkoutPlan.id.desc())).scalars().first()


def starts_new_cycle(user_id):
    """True when the profile has been edited since the active plan was built, so the next plan
    must REPLACE it (week 1, fresh) instead of continuing as next week. A plan with no recorded
    fingerprint (should not happen after migration 008) keeps the old weekly behaviour."""
    current = current_plan_row(user_id)
    if current is None or not current.profile_hash:
        return False
    return current.profile_hash != plan_cycle.fingerprint(get_profile(user_id))


def next_week_number(user_id):
    """Week 1 when this plan replaces one built for an older profile; otherwise the next week of the cycle."""
    if starts_new_cycle(user_id):
        return 1
    count = db.session.execute(db.select(db.func.count(WorkoutPlan.id)).where(
        WorkoutPlan.user_id == user_id, WorkoutPlan.is_archived.is_(False),
        WorkoutPlan.status.in_(("ready", "generating")))).scalar_one()
    return count + 1


def count_recent_plans(user_id, paid):
    since = utcnow() - timedelta(hours=24)
    tier_filter = WorkoutPlan.tier != "basic" if paid else WorkoutPlan.tier == "basic"
    return db.session.execute(db.select(db.func.count(WorkoutPlan.id)).where(
        WorkoutPlan.user_id == user_id, tier_filter, WorkoutPlan.status != "failed", WorkoutPlan.created_at >= since)).scalar_one()


def clean_notes(raw):
    if not isinstance(raw, str):
        return None
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", raw).strip()[:300]
    return cleaned or None


def _completed_list(plan_row):
    try:
        value = json.loads(plan_row.completed_days or "[]")
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _compact_week(plan_row, detail):
    """Small summary of one old week (keeps the paid prompt cheap)."""
    data = json.loads(plan_row.plan)
    days = []
    for key in sorted(data, key=lambda k: int(k.split("_")[1])):
        day = data[key]
        exercises = [e for e in day[1:] if isinstance(e, dict)]
        if detail:
            items = [[e["exercise"], e.get("reps"), e.get("seconds"), e.get("rest")] for e in exercises]
        else:
            items = [e["exercise"] for e in exercises]
        days.append({"title": day[0][0], "exercises": items})
    return {"week": plan_row.week_number, "tier": plan_row.tier,
            "completion_rate": round(min(len(_completed_list(plan_row)), 7) / 7, 2), "days": days}


def build_history(user_id, tier, fresh=False):
    """What each tier is allowed to 'remember' about the user's past weeks.
    Basic: just last week's raw plan. Personalized: last week + completion.
    Advanced: up to 4 weeks (newest in full detail)."""
    limit = 4 if tier == "advanced" else 1
    if fresh:      # a replacement plan starts from the updated profile; the old profile's weeks must not leak in
        return [] if tier == "advanced" else None
    rows = db.session.execute(db.select(WorkoutPlan).where(
        WorkoutPlan.user_id == user_id, WorkoutPlan.status == "ready", WorkoutPlan.is_archived.is_(False)
    ).order_by(WorkoutPlan.id.desc()).limit(limit)).scalars().all()
    if tier == "basic":
        return rows[0].plan if rows else None
    try:
        weeks = [_compact_week(row, detail=(i == 0)) for i, row in enumerate(rows)]
    except (TypeError, ValueError, KeyError, IndexError):
        weeks = []
    if tier == "personalized":
        return weeks[0] if weeks else None
    return weeks


def advance_progression(user_id):
    profile = get_profile(user_id)
    if not profile:
        return
    profile.progression_score = (profile.progression_score or 1) + 1
    if profile.progression_score >= 15:
        profile.experience = "advanced"
    elif profile.progression_score >= 8:
        profile.experience = "intermediate"


def claim_job(plan_id):
    """Atomically take a queued job. Returns a run token, or None if someone else has it."""
    token = f"{WORKER_ID}-{secrets.token_hex(4)}"
    claimed = db.session.execute(update(WorkoutPlan).where(
        WorkoutPlan.id == plan_id, WorkoutPlan.status == "generating", WorkoutPlan.locked_at.is_(None),
    ).values(generation_status="running", locked_at=utcnow(), locked_by=token)).rowcount
    db.session.commit()
    return token if claimed == 1 else None


def _budget_guard():
    if usage.daily_limit_reached():
        raise plan_generator.PlanGenerationError("budget", "daily Gemini request limit reached")


def run_generation(plan_id):
    """Runs ONE queued job to the end. Safe to call from a thread or a worker;
    if another runner already claimed the job this does nothing."""
    with app.app_context():
        token = claim_job(plan_id)
        if not token:
            return
        plan = db.session.get(WorkoutPlan, plan_id)
        user_id, tier = plan.user_id, plan.tier
        try:
            profile = get_profile(user_id)
            if not profile:
                raise plan_generator.PlanGenerationError("config", "profile missing")
            profile_data, expected_days = profile_to_dict(profile), int(profile.workout_days)
            history = build_history(user_id, tier, fresh=(plan.week_number == 1))
            start_hash = plan_cycle.fingerprint(profile)       # the profile this plan is built from
            payment = db.session.get(Payment, plan.payment_id) if plan.payment_id else None
            if tier != "basic" and not (payment and payment.status in ("paid", "used")
                                        and payment.payment_verification_status == "verified"):
                raise plan_generator.PlanGenerationError("config", "no verified payment")   # never generate unpaid
            def record(**fields):          # every Gemini request is saved, even failed ones
                return usage.record_attempt(user_id=user_id, plan_id=plan_id, payment_id=payment.id if payment else None, **fields)
            result = plan_generator.generate_plan(
                tier, profile_data, history, plan.week_number, payment.user_notes if payment else None,
                validator=lambda raw: validate_and_serialize_plan(raw, expected_days, profile_data["equipment"]),
                record=record, mark=usage.mark_attempt, guard=_budget_guard)
            serialized, coach_notes = result["plan_json"], result["coach_notes"]
            plan = db.session.get(WorkoutPlan, plan_id)
            if plan.locked_by != token or plan.status != "generating":
                return   # lease was lost; another runner owns this row now
            plan.plan = serialized
            plan.created_at = utcnow()
            plan.completed_days = "[]"
            plan.status, plan.generation_status = "ready", "succeeded"
            plan.error_message, plan.coach_notes = None, coach_notes
            plan.locked_at = plan.locked_by = None
            usage.refresh_plan_totals(plan_id)
            plan.ai_provider = "gemini"
            if payment:
                payment.status, payment.used_at = "used", utcnow()
            before = plan_cycle.fingerprint(get_profile(user_id))
            if plan.week_number > 1:
                advance_progression(user_id)
            # If the user edited their profile WHILE this plan was being built, keep the old fingerprint so
            # the next plan is treated as a replacement. Otherwise record the (progression-adjusted) profile.
            plan.profile_hash = plan_cycle.fingerprint(get_profile(user_id)) if before == start_hash else start_hash
            if plan.week_number == 1:
                # A replacement: archive the older plans in the SAME transaction that saves this one.
                # If anything above failed we never get here, so the old plan is left untouched.
                db.session.execute(update(WorkoutPlan).where(
                    WorkoutPlan.user_id == user_id, WorkoutPlan.id != plan.id, WorkoutPlan.is_archived.is_(False)
                ).values(is_archived=True))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            code = getattr(exc, "code", None)
            if code not in SAFE_ERROR_CODES:
                code = "invalid_output" if isinstance(exc, ValueError) else "provider_error"
            app.logger.error("PLAN GENERATION ERROR: %s %s %s", tier, code, type(exc).__name__)   # server log only
            try:
                plan = db.session.get(WorkoutPlan, plan_id)
                if plan and plan.status == "generating" and plan.locked_by == token:
                    plan.status, plan.generation_status, plan.error_message = "failed", "failed", code
                    plan.locked_at = plan.locked_by = None
                    usage.refresh_plan_totals(plan_id)
                    db.session.commit()
            except Exception as inner_exc:
                app.logger.error("PLAN GENERATION ERROR (could not record failure): %s", type(inner_exc).__name__)
                db.session.rollback()
        finally:
            db.session.remove()


def process_queue(limit=5):
    """Used by worker.py: run waiting jobs, one after another. Returns how many were tried."""
    with app.app_context():
        expire_stale_generations()
        ids = db.session.execute(db.select(WorkoutPlan.id).where(
            WorkoutPlan.status == "generating", WorkoutPlan.locked_at.is_(None)).order_by(WorkoutPlan.id).limit(limit)).scalars().all()
    for plan_id in ids:
        run_generation(plan_id)
    return len(ids)


def _start_thread(plan_id):
    threading.Thread(target=run_generation, args=(plan_id,), daemon=True).start()


def create_plan_row(user_id, tier, payment=None):
    """Creates the queued row. Returns None if a concurrent request won
    (same payment already used, or another generation already running)."""
    plan = WorkoutPlan(user_id=user_id, plan=json.dumps({}), completed_days="[]", status="generating",
                       generation_status="queued", tier=tier, week_number=next_week_number(user_id),
                       payment_id=payment.id if payment else None,
                       ai_provider="gemini")
    db.session.add(plan)
    try:
        db.session.commit()
    except IntegrityError:  # unique(payment_id): this payment already has its plan row
        db.session.rollback()
        return None
    first_running = db.session.execute(db.select(db.func.min(WorkoutPlan.id)).where(
        WorkoutPlan.user_id == user_id, WorkoutPlan.status == "generating")).scalar_one()
    if first_running != plan.id:  # two clicks raced: keep only the first
        db.session.delete(plan)
        db.session.commit()
        return None
    return plan


def launch_generation(plan):
    """Hand the queued row to a runner. In worker mode nothing else is needed:
    worker.py finds it. In inline mode a thread in this server runs it."""
    if config.generation_mode() == "inline":
        _start_thread(plan.id)


def start_paid_generation(payment):
    """Turns ONE verified payment into ONE plan. Safe to call again (retry,
    double click, replayed callback). Returns (outcome, plan_row_or_None)."""
    user_id = payment.user_id
    profile = get_profile(user_id) if user_id else None
    if (payment.status not in ("paid", "used") or payment.payment_verification_status != "verified"
            or payment.refund_status in ("processing", "refunded") or not profile):
        return "invalid", None
    expire_stale_generations(user_id)
    existing = db.session.execute(db.select(WorkoutPlan).where(WorkoutPlan.payment_id == payment.id)).scalar_one_or_none()
    if existing and existing.status in ("ready", "generating"):
        return "exists", existing
    if payment.attempts >= MAX_GENERATION_ATTEMPTS:
        return "exhausted", existing
    if active_generation(user_id):
        return "busy", existing
    if existing:  # retry the same row: no new payment needed
        claimed = db.session.execute(update(WorkoutPlan).where(
            WorkoutPlan.id == existing.id, WorkoutPlan.status == "failed").values(
            status="generating", generation_status="queued", error_message=None, created_at=utcnow(),
            locked_at=None, locked_by=None, week_number=next_week_number(user_id))).rowcount
        if not claimed:
            db.session.rollback()
            return "exists", existing
        payment.attempts += 1
        db.session.commit()
        plan = db.session.get(WorkoutPlan, existing.id)
    else:
        plan = create_plan_row(user_id, payment.tier, payment)
        if not plan:
            return "exists", None
        payment.attempts += 1
        db.session.commit()
    launch_generation(plan)
    return "started", plan


def flash_paid_outcome(outcome):
    messages = {
        "started": ("Payment confirmed. Your plan is being built -- this can take up to a minute.", "success"),
        "exists": ("This paid plan has already been started. You will not be charged again.", "info"),
        "busy": ("Another plan is still being built. Your payment is safe -- start your paid plan once it finishes.", "info"),
        "exhausted": ("We couldn't finish your paid plan after several tries. You will not be charged again -- you can request a refund below or contact us.", "danger"),
        "invalid": ("We couldn't start that plan. Please complete your fitness profile first.", "danger"),
    }
    text_, category = messages[outcome]
    if outcome == "started" and current_user.is_authenticated and starts_new_cycle(current_user.id):
        text_ = "Payment confirmed. We're building a fresh plan from your updated profile to replace your current one -- this can take up to a minute."
    flash(text_, category)


def start_basic_generation():
    profile = get_profile(current_user.id)
    if not profile:
        flash("Please complete your fitness profile first.", "danger")
        return redirect(url_for("fitness_profile"))
    expire_stale_generations(current_user.id)
    if active_generation(current_user.id):
        flash("Your workout plan is already being generated. Please wait.", "info")
        return redirect(url_for("home"))
    if count_recent_plans(current_user.id, paid=False) >= MAX_BASIC_PER_DAY:
        flash("You've reached today's limit for Basic plans. Please try again tomorrow, or choose a paid plan.", "warning")
        return redirect(url_for("home"))
    plan = create_plan_row(current_user.id, "basic")
    if plan:
        launch_generation(plan)
        if starts_new_cycle(current_user.id):
            flash("Your profile changed, so we're building a fresh plan to replace your current one -- this can take up to a minute. Your current plan stays until the new one is ready.", "info")
        else:
            flash("Your Basic plan is being generated -- this can take up to a minute.", "info")
    return redirect(url_for("home"))


def plan_options_context(user_id, free_unlocked=True):
    """Everything the shared _plan_options.html partial needs."""
    expire_stale_generations(user_id)
    paid_payments = db.session.execute(db.select(Payment).where(
        Payment.user_id == user_id, Payment.status == "paid").order_by(Payment.id)).scalars().all()
    pending_payment, failed_plan = None, None
    for payment in paid_payments:
        plan = db.session.execute(db.select(WorkoutPlan).where(WorkoutPlan.payment_id == payment.id)).scalar_one_or_none()
        if plan is None:
            pending_payment = payment
        elif plan.status == "failed":
            failed_plan = plan
    if not failed_plan:
        newest = latest_plan_row(user_id)
        if newest and newest.status == "failed":
            failed_plan = newest
    retry_exhausted, refund_payment = False, None
    if failed_plan and failed_plan.payment_id:
        payment = db.session.get(Payment, failed_plan.payment_id)
        retry_exhausted = bool(payment and payment.attempts >= MAX_GENERATION_ATTEMPTS)
        if payment and payment.status == "paid":
            refund_payment = payment      # may show "Request a refund"
    return {
        "free_unlocked": free_unlocked,
        "has_current_plan": current_plan_row(user_id) is not None,
        "will_replace": starts_new_cycle(user_id),      # profile edited since the active plan was built
        "pending_payment": pending_payment,
        "failed_plan": failed_plan,
        "retry_exhausted": retry_exhausted,
        "refund_payment": refund_payment,
        "payments_ready": billing.is_configured(),
    }


@app.route("/fitness-profile", methods=["GET", "POST"])
@login_required
def fitness_profile():
    profile = get_profile(current_user.id)
    if profile:
        fitness_form = FitnessProfileform(obj=profile)
        if request.method == "GET":     # a failed POST keeps what the user just ticked
            fitness_form.equipment.data = equipment_service.parse_stored(profile.equipment)
    else:
        fitness_form = FitnessProfileform()
    if fitness_form.validate_on_submit():
        if fitness_form.dob.data > date.today():
            flash("Date of birth cannot be in the future.", "danger")
            return render_template("fitness-profile.html", form=fitness_form)
        if profile:
            profile.dob = fitness_form.dob.data
            profile.height = fitness_form.height.data
            profile.weight = fitness_form.weight.data
            profile.gender = fitness_form.gender.data
            profile.goal = fitness_form.goal.data
            profile.experience = fitness_form.experience.data
            profile.workout_days = int(fitness_form.workout_days.data)
            profile.equipment = equipment_service.serialise(fitness_form.equipment.data)
        else:
            profile = FitnessProfile(
                user_id=current_user.id,
                dob=fitness_form.dob.data,
                height=fitness_form.height.data,
                weight=fitness_form.weight.data,
                gender=fitness_form.gender.data,
                goal=fitness_form.goal.data,
                experience=fitness_form.experience.data,
                workout_days=int(fitness_form.workout_days.data),
                equipment=equipment_service.serialise(fitness_form.equipment.data),
                progression_score=1,
            )
            db.session.add(profile)
        # Experience should set a sensible minimum progression instead of repeatedly adding 7.
        target_score = {"beginner": 1, "intermediate": 8, "advanced": 15}[profile.experience]
        profile.progression_score = max(profile.progression_score or 1, target_score)
        db.session.commit()
        # Saving the profile no longer starts a plan by itself: the user now
        # picks Basic / Personalized / Advanced on the next screen.
        flash("Fitness profile saved! Now choose how you want your plan built.", "success")
        return redirect(url_for("fitness_profile"))
    options = plan_options_context(current_user.id) if profile else {}
    return render_template("fitness-profile.html", form=fitness_form, has_profile=bool(profile), options_context="profile", **options)


# PAYMENTS (Razorpay)
def _json_error(message, status, **extra):
    return jsonify(ok=False, message=message, **extra), status


@app.errorhandler(429)
def too_many_requests(error):
    if request.path.startswith("/payments") or request.is_json:
        return _json_error("Too many requests. Please wait a moment and try again.", 429)
    return render_template("error.html", code=429, title="Slow down a little",
                           message="Too many requests. Please wait a moment and try again."), 429


@app.route("/payments/create-order", methods=["POST"])
@login_required
@limiter.limit("10 per minute;40 per hour")
def create_payment_order():
    data = request.get_json(silent=True) or {}
    tier = data.get("tier")
    if tier not in billing.PAID_TIERS:
        return _json_error("Please choose a valid plan.", 400)
    if not billing.is_configured():
        app.logger.warning("PAYMENTS DISABLED: %s", billing.config_problem())   # reason goes to the log, never to the customer
        return _json_error("Paid plans aren't available right now. Please try again later.", 503)
    if not get_profile(current_user.id):
        return _json_error("Please save your fitness profile first.", 400)
    expire_stale_generations(current_user.id)
    if active_generation(current_user.id):
        return _json_error("Your current plan is still being built. Please wait for it to finish.", 409)
    unfinished = db.session.execute(db.select(Payment.id).where(
        Payment.user_id == current_user.id, Payment.status == "paid")).first()
    if unfinished:
        return _json_error("You already have a paid plan that isn't finished. Use the button on this page to finish it -- you won't be charged again.", 409)
    since = utcnow() - timedelta(hours=24)
    paid_today = db.session.execute(db.select(db.func.count(Payment.id)).where(
        Payment.user_id == current_user.id, Payment.status.in_(("paid", "used")), Payment.verified_at >= since)).scalar_one()
    if paid_today >= MAX_PAID_PER_DAY:
        return _json_error("You've reached today's limit for paid plans. Please try again tomorrow.", 429)
    recent_orders = db.session.execute(db.select(db.func.count(Payment.id)).where(
        Payment.user_id == current_user.id, Payment.created_at >= utcnow() - timedelta(minutes=10))).scalar_one()
    notes = clean_notes(data.get("notes"))
    mode = "fresh" if data.get("fresh") else ("retry" if data.get("retry") else "normal")
    open_orders = _open_orders(current_user.id, tier)
    if mode != "normal" and open_orders:
        # Before retrying or replacing an order, ask Razorpay what really happened on it:
        # a payment may have gone through even though the browser reported a failure.
        try:
            states = {order.id: reconcile_order(order) for order in open_orders}
        except billing.BillingError as exc:
            app.logger.error("PAYMENT RECONCILE ERROR: %s", type(exc).__name__)
            return _json_error("We couldn't check your earlier payment right now. Please try again in a minute.", 502)
        if "paid" in states.values():
            paid = next(o for o in open_orders if states[o.id] == "paid")
            outcome, _plan = start_paid_generation(paid)
            flash_paid_outcome(outcome)
            return jsonify(ok=False, state="already_paid", redirect=url_for("home"),
                           message="Your earlier payment actually went through. You won't be charged again.")
        if "pending" in states.values():
            return _json_error("Your earlier payment is still being processed. Please wait a few minutes before paying again.", 409, state="pending")
    payment = None
    if mode == "fresh":
        for old in open_orders:      # retire the old attempt; a late capture on it is still honoured by webhook/verify
            old.status, old.failure_code = "cancelled", old.failure_code or "replaced"
        db.session.commit()
    elif mode == "retry":
        payment = next((o for o in open_orders if o.gateway_order_id == data.get("order_id")), None)
        if payment:
            if payment.status == "cancelled":
                payment.status = "created"      # same order, a further attempt
            payment.user_notes = notes
            db.session.commit()
    else:
        payment = db.session.execute(db.select(Payment).where(
            Payment.user_id == current_user.id, Payment.tier == tier, Payment.status == "created",
            Payment.created_at >= utcnow() - timedelta(minutes=30)).order_by(Payment.id.desc())).scalars().first()
        if payment:  # double click / reopened checkout: reuse the same open order
            payment.user_notes = notes
            db.session.commit()
    if payment is None:
        if recent_orders >= 8:
            return _json_error("Too many attempts. Please wait a few minutes and try again.", 429)
        amount = billing.price_paise(tier)  # price is decided here, never by the browser
        try:
            order = billing.create_order(amount, f"t2c_{current_user.id}_{secrets.token_hex(6)}",
                                         {"user_id": str(current_user.id), "tier": tier})
        except billing.BillingError as exc:
            app.logger.error("PAYMENT ORDER ERROR: %s", type(exc).__name__)
            return _json_error("We couldn't start the payment. Please try again.", 502)
        payment = Payment(user_id=current_user.id, tier=tier, amount_paise=amount, currency=billing.CURRENCY,
                          gateway_order_id=order["id"], status="created", user_notes=notes)
        db.session.add(payment)
        db.session.commit()
    return jsonify(ok=True, key_id=billing.public_key_id(), order_id=payment.gateway_order_id,
                   amount=payment.amount_paise, currency=payment.currency, label=TIER_LABELS[tier],
                   name=current_user.name, email=current_user.email)


def _apply_gateway_details(payment, details):
    """Copy what Razorpay says about a payment onto our row (fee/tax stay NULL if not reported)."""
    if not isinstance(details, dict):
        return
    method = details.get("method")
    if isinstance(method, str):
        payment.payment_method = method[:20]
    if isinstance(details.get("fee"), int):
        payment.fee_paise = details["fee"]
    if isinstance(details.get("tax"), int):
        payment.tax_paise = details["tax"]


def mark_payment_paid(payment_id, gateway_payment_id, details=None, commit=True):
    """Atomic created/cancelled -> paid. Only one caller can win, so replays are harmless.
    A payment captured after the customer closed the window still counts."""
    now = utcnow()
    result = db.session.execute(update(Payment).where(
        Payment.id == payment_id, Payment.status.in_(("created", "cancelled"))).values(
        status="paid", gateway_payment_id=gateway_payment_id, verified_at=now, paid_at=now,
        payment_status="captured", payment_verification_status="verified", failure_code=None))
    won = result.rowcount == 1
    if won and details:
        payment = db.session.get(Payment, payment_id)
        db.session.refresh(payment)
        _apply_gateway_details(payment, details)
    if commit:
        db.session.commit()
    return won


def _open_orders(user_id, tier):
    """Orders of this customer and plan that were started recently and never completed."""
    return db.session.execute(db.select(Payment).where(
        Payment.user_id == user_id, Payment.tier == tier, Payment.status.in_(("created", "cancelled")),
        Payment.created_at >= utcnow() - timedelta(minutes=30)).order_by(Payment.id.desc())).scalars().all()


SAFE_METHODS = ("card", "netbanking", "wallet", "upi", "emi")


def reconcile_order(payment):
    """Ask Razorpay what really happened on this order (the browser is never believed).

    Returns "paid" (money captured - our row is now paid), "pending" (authorized or still
    processing), "failed" (every attempt failed) or "open" (no attempt yet).
    Idempotent: calling it again changes nothing. Raises billing.BillingError if Razorpay
    can't be reached - callers must then NOT assume anything about the payment.
    """
    if payment.status in ("paid", "used"):
        return "paid"
    attempts = billing.fetch_order_payments(payment.gateway_order_id)
    for item in attempts:
        if (item.get("status") == "captured" and item.get("order_id") == payment.gateway_order_id
                and item.get("amount") == payment.amount_paise and item.get("currency") == payment.currency
                and isinstance(item.get("id"), str)):
            try:
                mark_payment_paid(payment.id, item["id"], item)
            except IntegrityError:
                db.session.rollback()
                return "pending"
            db.session.refresh(payment)
            return "paid" if payment.status in ("paid", "used") else "pending"
    if any(item.get("status") in ("authorized", "captured") for item in attempts):
        return "pending"
    failed = [item for item in attempts if item.get("status") == "failed"]
    if failed:
        last = failed[-1]
        if payment.status in ("created", "cancelled"):
            payment.payment_status = "failed"
            payment.failure_code = str(last.get("error_code") or "failed")[:60]
            method = last.get("method")
            payment.payment_method = method[:20] if method in SAFE_METHODS else payment.payment_method
            db.session.commit()
        return "failed"
    return "open"


@app.route("/payments/failed", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def payment_failed():
    """The checkout window reported a failure or was closed. We check the real status with Razorpay
    and tell the page what it may offer: nothing is unlocked and nothing is refunded here."""
    data = request.get_json(silent=True) or {}
    order_id = data.get("razorpay_order_id")
    if not isinstance(order_id, str) or not 0 < len(order_id) < 100:
        return _json_error("We couldn't check that payment.", 400)
    payment = db.session.execute(db.select(Payment).where(
        Payment.gateway_order_id == order_id, Payment.user_id == current_user.id)).scalar_one_or_none()
    if not payment:
        return _json_error("We couldn't check that payment.", 404)
    cancelled_by_user = data.get("reason") == "cancelled"
    try:
        state = reconcile_order(payment)
    except billing.BillingError as exc:
        app.logger.error("PAYMENT STATUS ERROR: %s", type(exc).__name__)
        return jsonify(ok=True, state="unknown", reference=payment.gateway_order_id, tier=payment.tier,
                       message="We couldn't check your payment status right now. If money was taken, your plan will appear automatically.")
    if state == "paid":
        outcome, _plan = start_paid_generation(payment)
        flash_paid_outcome(outcome)
        return jsonify(ok=True, state="paid", redirect=url_for("home"))
    if state in ("open", "failed") and payment.status == "created" and cancelled_by_user:
        payment.status = "cancelled"
        db.session.commit()
    return jsonify(ok=True, state="cancelled" if (cancelled_by_user and state == "open") else state,
                   reference=payment.gateway_order_id, tier=payment.tier,
                   method=payment.payment_method if payment.payment_method in SAFE_METHODS else None)


@app.route("/payments/verify", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def verify_payment():
    data = request.get_json(silent=True) or {}
    order_id = data.get("razorpay_order_id")
    pay_id = data.get("razorpay_payment_id")
    signature = data.get("razorpay_signature")
    if not all(isinstance(v, str) and 0 < len(v) < 100 for v in (order_id, pay_id, signature)):
        return _json_error("We couldn't verify your payment.", 400)
    payment = db.session.execute(db.select(Payment).where(
        Payment.gateway_order_id == order_id, Payment.user_id == current_user.id)).scalar_one_or_none()
    if not payment:
        return _json_error("We couldn't verify your payment.", 404)
    if payment.gateway_payment_id and payment.gateway_payment_id != pay_id:
        return _json_error("We couldn't verify your payment.", 400)
    try:
        if not billing.verify_payment_signature(order_id, pay_id, signature):
            if payment.status == "created":
                payment.payment_verification_status = "failed"
                db.session.commit()
            return _json_error("We couldn't verify your payment.", 400)
        if payment.status in ("created", "cancelled"):
            details = billing.fetch_payment(pay_id)  # ask Razorpay, don't trust the browser
            if (details.get("order_id") != order_id or details.get("amount") != payment.amount_paise
                    or details.get("currency") != payment.currency):
                app.logger.error("PAYMENT MISMATCH for order %s", order_id)
                payment.payment_verification_status = "mismatch"
                db.session.commit()
                return _json_error("We couldn't verify your payment.", 400)
            if details.get("status") == "authorized":
                billing.capture_payment(pay_id, payment.amount_paise)
                details = billing.fetch_payment(pay_id)
            gateway_status = details.get("status")
            if gateway_status == "failed":
                payment.payment_status, payment.failure_code = "failed", str(details.get("error_code") or "failed")[:60]
                db.session.commit()
                return _json_error("Your payment didn't go through. You have not been charged -- please try again.", 400)
            if gateway_status != "captured":
                if isinstance(gateway_status, str):
                    payment.payment_status = gateway_status[:20]
                    db.session.commit()
                return _json_error("Your payment is still processing. Please wait a moment -- if it goes through, your plan will start automatically.", 409)
            try:
                mark_payment_paid(payment.id, pay_id, details)
            except IntegrityError:  # this gateway payment id is already attached to another order
                db.session.rollback()
                return _json_error("We couldn't verify your payment.", 400)
            db.session.refresh(payment)
    except billing.BillingError as exc:
        app.logger.error("PAYMENT VERIFY ERROR: %s", type(exc).__name__)
        return _json_error("We couldn't confirm your payment right now. Please refresh in a minute -- if it went through, your plan will be waiting.", 502)
    if payment.status not in ("paid", "used"):
        return _json_error("We couldn't verify your payment.", 400)
    outcome, _plan = start_paid_generation(payment)
    flash_paid_outcome(outcome)
    return jsonify(ok=True, state="generating", redirect=url_for("home"))


@app.route("/payments/cancel", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def cancel_payment():
    data = request.get_json(silent=True) or {}
    order_id = data.get("razorpay_order_id")
    if isinstance(order_id, str):
        # If the customer actually paid, a later webhook/verify still marks it paid.
        db.session.execute(update(Payment).where(
            Payment.gateway_order_id == order_id, Payment.user_id == current_user.id, Payment.status == "created",
        ).values(status="cancelled"))
        db.session.commit()
    return jsonify(ok=True)


def _handle_payment_event(event_type, event):
    """Applies one verified webhook event. Returns a Payment that just became paid (or None).
    Does not commit: the caller commits the event record and the changes together."""
    payload = event.get("payload") or {}
    if event_type in ("payment.captured", "order.paid", "payment.failed", "payment.authorized"):
        entity = (payload.get("payment") or {}).get("entity") or {}
        payment = db.session.execute(db.select(Payment).where(
            Payment.gateway_order_id == entity.get("order_id"))).scalar_one_or_none()
        if not payment:
            return None   # an order that isn't ours
        if event_type in ("payment.captured", "order.paid"):
            if (entity.get("status") == "captured" and entity.get("amount") == payment.amount_paise
                    and entity.get("currency") == payment.currency and isinstance(entity.get("id"), str)):
                if mark_payment_paid(payment.id, entity["id"], entity, commit=False):
                    return payment
            elif entity.get("amount") != payment.amount_paise and payment.status == "created":
                payment.payment_verification_status = "mismatch"   # wrong amount: never unlock
        elif event_type == "payment.failed" and payment.status == "created":
            payment.payment_status = "failed"
            payment.failure_code = str(entity.get("error_code") or "failed")[:60]
        return None
    if event_type in ("refund.processed", "refund.created", "refund.failed"):
        refund = (payload.get("refund") or {}).get("entity") or {}
        pay_entity = (payload.get("payment") or {}).get("entity") or {}
        payment_id = refund.get("payment_id") or pay_entity.get("id")
        payment = db.session.execute(db.select(Payment).where(Payment.gateway_payment_id == payment_id)).scalar_one_or_none()
        if not payment:
            return None
        if event_type == "refund.processed":
            refunded = pay_entity.get("amount_refunded")
            if not isinstance(refunded, int):
                refunded = max(payment.refund_amount_paise, int(refund.get("amount") or 0))
            payment.refund_amount_paise = refunded
            payment.refund_status = "refunded" if refunded >= payment.amount_paise else "partial"
            payment.gateway_refund_id = payment.gateway_refund_id or refund.get("id")
            payment.refunded_at = utcnow()
            if payment.refund_status == "refunded":
                payment.status, payment.payment_status = "refunded", "refunded"
        elif event_type == "refund.failed" and payment.refund_status == "processing":
            payment.refund_status = "failed"
    return None


@app.route("/razorpay/webhook", methods=["POST"])
@csrf.exempt  # Razorpay can't send our CSRF token; the signature below is the protection
@limiter.exempt
def razorpay_webhook():
    """Razorpay tells us about payments even if the customer's browser closed.
    Signature is checked on the RAW body. Each delivery is recorded by its event
    id, so a repeated delivery does nothing. If our own processing fails we
    answer 500 so Razorpay delivers it again."""
    raw = request.get_data()
    if not billing.verify_webhook(raw, request.headers.get("X-Razorpay-Signature", "")):
        return "", 400
    try:
        event = json.loads(raw)
        event_type = str(event.get("event") or "")[:60]
    except (ValueError, AttributeError):
        return "", 400
    event_id = (request.headers.get("X-Razorpay-Event-Id") or "").strip()[:100] or "sha256:" + hashlib.sha256(raw).hexdigest()
    entity = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    paid_payment = None
    try:
        db.session.add(PaymentEvent(event_id=event_id, event_type=event_type,
                                    gateway_order_id=entity.get("order_id"), gateway_payment_id=entity.get("id")))
        db.session.flush()           # a repeated event id fails here (unique) => nothing is applied twice
        paid_payment = _handle_payment_event(event_type, event)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "", 200               # duplicate delivery: already handled
    except Exception as exc:
        db.session.rollback()
        app.logger.error("WEBHOOK ERROR: %s", type(exc).__name__)
        return "", 500
    if paid_payment is not None:     # browser may have closed: build the plan anyway
        try:
            db.session.refresh(paid_payment)
            start_paid_generation(paid_payment)
        except Exception as exc:
            db.session.rollback()
            app.logger.error("WEBHOOK START ERROR: %s", type(exc).__name__)   # customer still has the "Build my plan" button
    return "", 200


@app.route("/payments/<int:payment_id>/start", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def start_paid_plan(payment_id):
    """Claim a plan you already paid for (e.g. you closed the tab after paying)."""
    payment = db.session.get(Payment, payment_id)
    if not payment or payment.user_id != current_user.id or payment.status != "paid":
        return "Not found", 404
    outcome, _plan = start_paid_generation(payment)
    flash_paid_outcome(outcome)
    return redirect(url_for("home"))


@app.route("/plans/<int:plan_id>/retry", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def retry_plan(plan_id):
    """Retry a plan that failed. Paid plans reuse the same payment (no new charge)."""
    plan = db.session.get(WorkoutPlan, plan_id)
    if not plan or plan.user_id != current_user.id or plan.status != "failed":
        return "Not found", 404
    if plan.payment_id:
        payment = db.session.get(Payment, plan.payment_id)
        outcome, _plan = start_paid_generation(payment)
        flash_paid_outcome(outcome)
        return redirect(url_for("home"))
    profile = get_profile(current_user.id)
    expire_stale_generations(current_user.id)
    if not profile or active_generation(current_user.id):
        flash("Please wait for your current plan to finish.", "info")
        return redirect(url_for("home"))
    claimed = db.session.execute(update(WorkoutPlan).where(WorkoutPlan.id == plan.id, WorkoutPlan.status == "failed").values(
        status="generating", generation_status="queued", error_message=None, created_at=utcnow(),
        locked_at=None, locked_by=None, week_number=next_week_number(current_user.id))).rowcount
    db.session.commit()
    if claimed:
        launch_generation(db.session.get(WorkoutPlan, plan.id))
        flash("Trying again -- your plan is being generated.", "info")
    return redirect(url_for("home"))


@app.route("/payments/<int:payment_id>/refund-request", methods=["POST"])
@fresh_login_required
@limiter.limit("5 per hour")
def request_refund(payment_id):
    """Customer asks for a refund (only when their paid plan never got built).
    This does NOT refund anything: an admin reviews and approves each request."""
    payment = db.session.get(Payment, payment_id)
    if not payment or payment.user_id != current_user.id or payment.status != "paid":
        return "Not found", 404
    done = db.session.execute(db.select(WorkoutPlan.id).where(
        WorkoutPlan.payment_id == payment.id, WorkoutPlan.status.in_(("ready", "generating")))).first()
    if done:
        flash("Your plan is already built or being built, so a refund isn't needed.", "info")
        return redirect(url_for("home"))
    claimed = db.session.execute(update(Payment).where(Payment.id == payment.id, Payment.refund_status == "none").values(
        refund_status="requested", refund_requested_at=utcnow())).rowcount
    db.session.commit()
    flash("Refund requested. We'll review it and email you." if claimed else "Your refund request is already with us.", "info")
    return redirect(url_for("home"))


@app.route("/plans/basic", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def generate_basic_plan():
    return start_basic_generation()


# ACCOUNT
@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    clear_otp_session()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))

@app.route("/delete-account", methods=["POST"])
@fresh_login_required
def delete_account():
    user_id = current_user.id
    try:
        account_service.delete_user_account(user_id)
        db.session.commit()
        logout_user()
        clear_otp_session()
        flash("Your account has been deleted.", "info")
    except Exception as exc:
        db.session.rollback()
        app.logger.error("Account deletion failed: %s", type(exc).__name__)
        flash("We couldn't delete your account right now.", "danger")
    return redirect(url_for("home"))


# WORKOUT
def load_current_plan():
    """The user's newest READY week (the one they are training on)."""
    return current_plan_row(current_user.id)

@app.route("/start-workout/<int:day>")
@login_required
def start_workout(day):
    if day < 1 or day > 7:
        return "Workout day not found", 404
    workout_plan_db = load_current_plan()
    if not workout_plan_db:
        return "No workout found", 404
    try:
        plan = json.loads(workout_plan_db.plan)
        day_key = f"day_{day}"
        if day_key not in plan or not isinstance(plan[day_key], list):
            return "Workout day not found", 404
        workouts = []
        for item in plan[day_key][1:]:
            if not isinstance(item, dict) or "exercise" not in item:
                continue
            item = dict(item)
            exercise_key = str(item["exercise"])
            item["video"] = EXERCISE_VIDEOS.get(exercise_key)
            item["exercise"] = EXERCISE_NAMES.get(exercise_key, exercise_key)
            workouts.append(item)
        if not workouts:
            return "This workout has no exercises", 404
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        app.logger.error("WORKOUT JSON ERROR: %s", type(exc).__name__)
        return "Workout data is invalid", 500
    return render_template("workout.html", day_number=day, workouts=workouts)


@app.route("/complete-workout/<int:day>", methods=["POST"])
@login_required
def complete_workout(day):
    if day < 1 or day > 7:
        return "Workout day not found", 404
    workout_plan = load_current_plan()
    if not workout_plan:
        return redirect(url_for("home"))
    try:
        plan = json.loads(workout_plan.plan)
        if f"day_{day}" not in plan:
            return "Workout day not found", 404
        completed_days = json.loads(workout_plan.completed_days or "[]")
        if not isinstance(completed_days, list):
            completed_days = []
    except (TypeError, ValueError, json.JSONDecodeError):
        flash("Your workout progress could not be read.", "danger")
        return redirect(url_for("home"))
    if day not in completed_days:
        completed_days.append(day)
        completed_days.sort()
    workout_plan.completed_days = json.dumps(completed_days)
    db.session.commit()
    flash(f"Day {day} completed! Great work.", "success")
    return redirect(url_for("home"))

@app.route("/load-next-week", methods=["POST"])
@login_required
def load_next_week():
    """Free 'Basic Plan' for the next week (kept at its old URL)."""
    return start_basic_generation()


# PAGES
@app.route("/")
def home():
    if not current_user.is_authenticated:
        return render_template("dashboard.html")
    expire_stale_generations(current_user.id)
    latest = latest_plan_row(current_user.id)
    current = load_current_plan()
    shown = current
    viewing_old = False
    requested = request.args.get("plan", type=int)
    if requested:
        old = db.session.get(WorkoutPlan, requested)
        if old and old.user_id == current_user.id and old.status == "ready" and not old.is_archived:
            shown = old
            viewing_old = current is not None and old.id != current.id
    generating = latest is not None and latest.status == "generating"
    workout_plan_data = None
    completed_days = []
    if shown and not generating:
        try:
            workout_plan_data = json.loads(shown.plan)
            completed_days = _completed_list(shown)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            app.logger.error("HOME PLAN JSON ERROR: %s", type(exc).__name__)
            flash("Your saved workout plan is invalid. Please generate a new one.", "danger")
            workout_plan_data = None
    history = db.session.execute(db.select(WorkoutPlan.id, WorkoutPlan.week_number, WorkoutPlan.tier).where(
        WorkoutPlan.user_id == current_user.id, WorkoutPlan.status == "ready", WorkoutPlan.is_archived.is_(False)
    ).order_by(WorkoutPlan.id.desc())).all()
    # Basic ("free") for the next week unlocks after all 7 days are done, as before;
    # a brand-new user with no plan yet can always start one.
    # After a profile edit the user may always ask for a replacement plan, even mid-week.
    free_unlocked = shown is None or starts_new_cycle(current_user.id) or (len(completed_days) >= 7 and not viewing_old)
    options = plan_options_context(current_user.id, free_unlocked=free_unlocked)
    return render_template(
        "home.html", workout_plan=(latest if generating else shown), workout_plan_data=workout_plan_data,
        completed_days=completed_days, viewing_old=viewing_old, plan_history=history,
        has_profile=get_profile(current_user.id) is not None, options_context="home", **options)


@app.route("/workout-plan/status")
@login_required
def workout_plan_status():
    """Polled by home.html while a plan is generating in the background."""
    expire_stale_generations(current_user.id)
    latest = latest_plan_row(current_user.id)
    if not latest:
        return {"status": "none"}
    return {"status": latest.status}


def _trainer_from_request():
    """Trainer chosen via 'Contact Trainer' (?trainer=ID&mode=...). Loaded from the
    database; None if missing/unknown/inactive. Query values are only hints."""
    return trainer_service.get_contactable_trainer(request.args.get("trainer", type=int))


@app.route("/contact", methods=["GET", "POST"])
@limiter.limit("10 per minute;30 per hour", methods=["POST"])
def contact():
    form = ContactForm()
    if current_user.is_authenticated:
        del form.name, form.email      # identity comes from the account, not from the browser
    trainer = None
    if request.method == "GET":
        if request.args.get("trainer"):
            trainer = _trainer_from_request()
            if trainer is None:
                flash("That trainer is no longer available. You can still send us a general message.", "warning")
        if trainer is not None:
            mode = request.args.get("mode") if request.args.get("mode") in trainer_service.MODES else "offline"
            form.trainer_id.data = str(trainer.id)
            form.contact_mode.data = mode
            form.message.data = trainer_service.build_contact_message(
                trainer.name, trainer.location, online=(mode == "online"))
    elif form.trainer_id.data:
        # POST: never trust the id from the page - look the trainer up again.
        raw_id = form.trainer_id.data.strip()
        trainer = trainer_service.get_contactable_trainer(int(raw_id)) if raw_id.isdigit() and len(raw_id) < 10 else None
        if trainer is None:
            flash("That trainer is no longer available. Please go back and choose another trainer.", "danger")
            return render_template("contact.html", form=form, trainer=None)

    if current_user.is_authenticated:
        if form.validate_on_submit():
            message = form.message.data.strip()
            try:
                send_contact_info(email=current_user.email, name=current_user.name, contact_message=message, trainer=trainer)
                flash("Your message has been sent successfully!", "success")
                return redirect(url_for("contact"))
            except Exception as exc:
                app.logger.error("Contact e-mail failed: %s", type(exc).__name__)
                flash("We couldn't send your message right now. Please try again.", "danger")
        return render_template("contact.html", form=form, trainer=trainer)

    if form.validate_on_submit():
        session["name"] = form.name.data.strip()
        session["message"] = form.message.data.strip()
        session["trainer_id"] = trainer.id if trainer else None
        email = form.email.data.strip().lower()
        if not generate_otp_or_flash(email, "contact"):
            return redirect(url_for("contact"))
        flash("A verification code has been sent to your email.", "info")
        return redirect(url_for("verify_contact"))
    return render_template("contact.html", form=form, trainer=trainer)


@app.route("/verify-contact", methods=["GET", "POST"])
@limiter.limit("20 per minute;100 per hour", methods=["POST"])
def verify_contact():
    email = session.get("email")
    if session.get("otp_purpose") != "contact" or not email:
        flash("No contact verification request found.", "warning")
        return redirect(url_for("contact"))
    form = ContactOTPForm()
    if form.validate_on_submit():
        result, _attempts_left = otp_service.verify_code(email, "contact", form.verification_code.data)
        if result == otp_service.EXPIRED:
            flash("That verification code has expired. Please request a new one.", "warning")
        elif result == otp_service.LOCKED:
            clear_otp_session()
            flash("Too many incorrect attempts. Please start the contact request again.", "danger")
            return redirect(url_for("contact"))
        elif result != otp_service.OK:
            flash("That code isn't correct. Please check it and try again.", "danger")
        else:
            trainer = trainer_service.get_contactable_trainer(session.get("trainer_id"))
            try:
                send_contact_info(email=email, name=session.get("name", ""),
                                  contact_message=session.get("message", ""), trainer=trainer)
                flash("Your message has been sent successfully!", "success")
                clear_otp_session()
                return redirect(url_for("home"))
            except Exception as exc:
                app.logger.error("Contact delivery failed: %s", type(exc).__name__)
                flash("We verified your email, but couldn't send the message. Please try again.", "danger")
    return render_template("verify_contact.html", form=form,
                           resend_remaining=otp_service.seconds_until_resend(email), email=email)


@app.route("/find-trainer", methods=["GET", "POST"])
def find_trainer():
    """Search form (POST) -> redirect to the results (GET, so pages can be linked/paged)."""
    if not current_user.is_authenticated:
        flash("Please log in or register to find a personal trainer.", "warning")
        return redirect(url_for("register"))
    profile = db.session.scalar(db.select(FitnessProfile).where(FitnessProfile.user_id == current_user.id))
    searching = request.method == "GET" and request.args.get("mode") is not None
    form = TrainerSearchForm(formdata=request.args, meta={"csrf": False}) if searching else TrainerSearchForm()
    if profile:
        del form.gender           # gender comes from the saved profile, never from the browser

    if request.method == "POST" and form.validate_on_submit():
        params = {"mode": form.mode.data}
        if form.mode.data == "offline":
            params["location"] = form.location.data.strip()
        if not profile:
            params["gender"] = form.gender.data
        return redirect(url_for("find_trainer", **params))

    if searching and form.validate():
        gender = profile.gender if profile else form.gender.data
        mode = form.mode.data
        location = (form.location.data or "").strip()
        if mode == "online":
            # Online: location and distance are ignored; no coordinates needed.
            page = trainer_service.find_online_trainers(gender, page=request.args.get("page", 1, type=int))
            return render_template("trainer_results.html", trainers=page.items, page=page, mode=mode, location="")
        try:
            trainers = trainer_service.find_nearby_trainers(location, gender)
        except trainer_service.GeocodingError:
            flash("We couldn't look up that location right now. Please try again, or choose online training.", "danger")
            return render_template("find_trainer.html", form=form, profile=profile)
        return render_template("trainer_results.html", trainers=trainers, page=None, mode=mode, location=location)
    return render_template("find_trainer.html", form=form, profile=profile)


@app.route("/become-trainer", methods=["GET", "POST"])
@login_required
def become_trainer():
    form = TrainerForm()
    # Check whether user already has a fitness profile
    profile = db.session.scalar(db.select(FitnessProfile).where(FitnessProfile.user_id == current_user.id))
    # If profile exists, don't require gender from this form
    if profile:
        form.gender.validators = []
        # Remove gender from validation
        form.gender.data = profile.gender
    else:
        gender = None
    if form.validate_on_submit():
        # If there is no profile, get gender from form
        if not profile:
            gender = form.gender.data
        try:
            send_trainer_application(email=current_user.email,
                                 name=current_user.name,
                                 about=form.about.data,
                                 location=form.location.data,
                                 gender=gender,
                                 phone_number=form.phone_number.data)
            flash("Your trainer application has been sent successfully!","success")
            return redirect(url_for('home'))
        except Exception as e:
            app.logger.error("Trainer application e-mail failed: %s", type(e).__name__)
            flash("Something went wrong while sending your application. Please try again.","danger")
    return render_template("become_trainer.html",form=form,profile=profile)
        
@app.route("/nutrition")
def nutrition():
    return render_template("nutrition.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.errorhandler(404)
def page_not_found(error):
    return render_template("error.html",code=404,title="Page not found",
        message="The page you requested does not exist or may have moved.",), 404

@app.errorhandler(500)
def internal_server_error(error):
    db.session.rollback()
    return render_template("error.html",code=500,title="Something went wrong",
        message="Train2Conquer hit an unexpected error. Please try again in a moment.",), 500
 
@app.context_processor
def inject_template_globals():
    def rupees(paise):
        return f"\u20b9{paise // 100}" if paise % 100 == 0 else f"\u20b9{paise / 100:.2f}"
    return {
        "current_year": datetime.now().year,
        "price_labels": {t: rupees(billing.price_paise(t)) for t in billing.PAID_TIERS},
        "tier_labels": TIER_LABELS,
    }


@app.template_filter("inr")
def inr_filter(value):
    """Rupees from a Decimal/number of RUPEES. None -> 'unknown'."""
    if value is None:
        return "unknown"
    return "\u20b9{:,.2f}".format(Decimal(str(value)))


@app.template_filter("inr_paise")
def inr_paise_filter(paise):
    return "unknown" if paise is None else "\u20b9{:,.2f}".format(Decimal(int(paise)) / 100)


@app.template_filter("usd")
def usd_filter(value):
    return "unknown" if value is None else "${:,.4f}".format(Decimal(str(value)))


@app.route('/sitemap.xml')
def sitemap():
    pages = [
        "https://www.train2conquer.in/",
        # Add other public pages here
    ]

    xml = '<?xml version="1.0" encoding="UTF-8"?>'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'

    for page in pages:
        xml += f'<url><loc>{page}</loc></url>'

    xml += '</urlset>'

    return Response(xml, mimetype='application/xml')


if __name__ == "__main__":
    app.run(debug=False, port=5002)
