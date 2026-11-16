import os

# On Windows, security policies (Smart App Control / App Control for Business)
# can block unsigned compiled extension (.pyd) files. SQLAlchemy's optional
# Cython speed-ups are such files, so skip them there and use its built-in
# pure-Python code instead. Must be set BEFORE sqlalchemy is imported.
if os.name == "nt":
    os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")

import hashlib, html, json, re, requests, secrets, smtplib, socket, threading, time
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from math import radians, sin, cos, sqrt, atan2
from dotenv import load_dotenv

load_dotenv()   # must run before config/billing read the environment

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_bootstrap import Bootstrap5
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_sqlalchemy.pagination import SelectPagination
from flask_wtf import CSRFProtect
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from werkzeug.middleware.proxy_fix import ProxyFix
from forms import ContactForm, ContactOTPForm, FitnessProfileform, Loginform, OTPform, Registerform, TrainerSearchForm, TrainerForm, AdminTrainerForm, AdminUserForm
from ai import generate_fitness_plan
import ai_paid, billing, config, costing, finance, migrations, usage
from database import build_database_config
from models import FitnessProfile, Payment, PaymentEvent, Trainer, User, WorkoutPlan, db, utcnow

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
app.config["REMEMBER_COOKIE_SECURE"] = IS_PRODUCTION
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
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
login_manager.init_app(app)


def _limit_key():
    """Rate limits are per logged-in user (falls back to the visitor's address)."""
    if current_user and current_user.is_authenticated:
        return f"user:{current_user.id}"
    return get_remote_address()


limiter = Limiter(_limit_key, app=app, default_limits=[],
                  storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"))


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
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
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
ADMIN_PAGE_SIZE = 20

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def admin_required(function):
    """Server-side admin gate. Stacks under @login_required on every
    /admin/... route. Never relies on hiding a button in the template --
    unauthorized users get redirected here regardless of what the
    frontend shows them."""
    @wraps(function)
    def decorator_function(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
            # Not an admin: no admin-only data is ever exposed to this
            # response. Authenticated non-admins get a safe redirect back
            # home; anyone unauthenticated goes through the normal login
            # flow via login_required (which always runs first).
            flash("You don't have access to the admin panel.", "danger")
            return redirect(url_for("home"))
        return function(*args, **kwargs)
    return decorator_function

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
OTP_EXPIRY_SECONDS = 10 * 60
OTP_RESEND_COOLDOWN = 30
def send_verification_code(email, otp):
    sender_email = os.environ.get("EMAIL")
    sender_password = os.environ.get("PASSWORD")
    if not sender_email or not sender_password:
        raise RuntimeError("EMAIL and PASSWORD environment variables are required")
    mail = MIMEMultipart()
    mail["From"] = sender_email
    mail["To"] = email
    mail["Subject"] = "Train2Conquer Verification Code"
    body = f"""
    <html>
        <body style="font-family:Arial,sans-serif;background:#08090C;color:#F5F5F3;padding:24px">
            <h2>Train2Conquer</h2>
            <p>Your verification code is:</p>
            <h1 style="letter-spacing:8px">{otp}</h1>
            <p>This code expires in 10 minutes.</p>
            <p>If you did not request this code, you can safely ignore this email.</p>
        </body>
    </html>
    """
    mail.attach(MIMEText(body, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, [email], mail.as_string())

def send_contact_info(email, name, contact_message):
    """Send a verified contact message to the site's owner/admin inbox."""
    sender_email = os.environ.get("EMAIL")
    sender_password = os.environ.get("PASSWORD")
    if not sender_email or not sender_password:
        raise RuntimeError("EMAIL and PASSWORD environment variables are required")
    safe_email = html.escape(email or "")
    safe_name = html.escape(name or "")
    safe_message = html.escape(contact_message or "").replace("\n", "<br>")
    mail = MIMEMultipart()
    mail["From"] = sender_email
    mail["To"] = sender_email
    mail["Reply-To"] = email
    mail["Subject"] = "Train2Conquer Contact Message"
    body = f"""
    <html>
        <body style="font-family:Arial,sans-serif">
            <h2>Train2Conquer Contact Message</h2>
            <p><strong>From:</strong> {safe_name}</p>
            <p><strong>Email:</strong> {safe_email}</p>
            <hr>
            <p>{safe_message}</p>
        </body>
    </html>
    """
    mail.attach(MIMEText(body, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, [sender_email], mail.as_string())


def generate_otp(email, purpose="login"):
    """Generate an OTP after enforcing the server-side resend cooldown."""
    if not email:
        raise ValueError("An email address is required")
    created = session.get("otp_created")
    if created and time.time() - float(created) < OTP_RESEND_COOLDOWN:
        raise ValueError("Please wait before requesting another verification code.")
    otp = f"{secrets.randbelow(1_000_000):06d}"
    # Send first. If email fails, don't create a fake active OTP session.
    send_verification_code(email, otp)
    session["email"] = email.strip().lower()
    session["otp"] = otp
    session["otp_created"] = time.time()
    session["otp_purpose"] = purpose
    session["otp_attempts"] = 0

def otp_remaining():
    """Server-side OTP lifetime. Kept separate from the resend countdown."""
    created = session.get("otp_created")
    if not created:
        return 0
    elapsed = max(0, int(time.time() - float(created)))
    return max(0, OTP_EXPIRY_SECONDS - elapsed)

def otp_resend_remaining():
    """Seconds until another OTP may be requested (always max 30 seconds)."""
    created = session.get("otp_created")
    if not created:
        return 0
    elapsed = max(0, int(time.time() - float(created)))
    return max(0, OTP_RESEND_COOLDOWN - elapsed)

def otp_is_expired():
    return otp_remaining() <= 0

def check_existing_user(email):
    if not email:
        return None
    return db.session.execute(
        db.select(User).where(User.email == email.strip().lower())).scalar_one_or_none()

def clear_otp_session():
    for key in ("otp","otp_created","otp_purpose","otp_attempts","name","email","message",):
        session.pop(key, None)

def get_coordinates(location):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": f"{location}, Bengaluru, Karnataka, India","format": "json","limit": 1}
    headers = {"User-Agent": "Train2Conquer/1.0"}
    response = requests.get(url, params=params, headers=headers, timeout=10)
    data = response.json()
    if not data:
        return None
    latitude = float(data[0]["lat"])
    longitude = float(data[0]["lon"])
    return latitude, longitude

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in km
    lat1 = radians(lat1)
    lon1 = radians(lon1)
    lat2 = radians(lat2)
    lon2 = radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (sin(dlat / 2) ** 2+cos(lat1)* cos(lat2)* sin(dlon / 2) ** 2)
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

def find_nearby_trainers(location,gender,radius=5):
    coordinates = get_coordinates(location)
    if not coordinates:
        return []
    user_lat, user_lon = coordinates
    # Bounding-box pre-filter: instead of loading every trainer of this
    # gender and Haversine-checking each one in Python (O(T)), first ask
    # the database for only the trainers whose lat/lon fall inside a
    # square box around the search point (O(1)-ish via the WHERE clause,
    # no extra index needed for this table size). Haversine still runs
    # on the (much smaller) remaining candidate set to get the exact
    # circular 5 km radius, since a square box slightly over-includes
    # the corners. 1 degree latitude ~= 111 km everywhere; 1 degree
    # longitude shrinks with cos(latitude), so it's computed per-search.
    lat_delta = radius / 111.0
    lon_delta = radius / (111.0 * max(cos(radians(user_lat)), 0.01))
    candidates = Trainer.query.filter(
        Trainer.gender == gender,
        Trainer.latitude.between(user_lat - lat_delta, user_lat + lat_delta),
        Trainer.longitude.between(user_lon - lon_delta, user_lon + lon_delta),
    ).all()
    nearby = []
    for trainer in candidates:
        distance = calculate_distance(user_lat,user_lon,trainer.latitude,trainer.longitude)
        if distance <= radius:
            nearby.append(trainer)
    return nearby

# LOGIN / REGISTRATION
@app.route("/login", methods=["GET", "POST"])
def login():
    login_form = Loginform()
    if login_form.validate_on_submit():
        email = login_form.email.data.strip().lower()
        if not check_existing_user(email):
            flash("You don't have an account. Please register first.", "warning")
            return redirect(url_for("register"))
        try:
            generate_otp(email, purpose="login")
        except Exception as exc:
            print("LOGIN OTP ERROR:", exc)
            flash("We couldn't send the verification code. Please try again.", "danger")
            return redirect(url_for("login"))
        return redirect(url_for("verify_otp"))
    return render_template("login.html", form=login_form)


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    email = session.get("email")
    purpose = session.get("otp_purpose")
    if not email or purpose not in {"login", "registration"} or not session.get("otp"):
        flash("Your verification session has expired. Please request a new code.", "warning")
        return redirect(url_for("login"))
    verify_otp_form = OTPform()
    remaining = otp_remaining()
    resend_remaining = otp_resend_remaining()
    if verify_otp_form.validate_on_submit():
        if otp_is_expired():
            flash("That verification code has expired. Please request a new one.", "warning")
        elif verify_otp_form.verification_code.data.strip() != session.get("otp"):
            attempts = int(session.get("otp_attempts", 0)) + 1
            session["otp_attempts"] = attempts
            if attempts >= 5:
                clear_otp_session()
                flash("Too many incorrect attempts. Please request a new code.", "danger")
                return redirect(url_for("login"))
            flash(f"Invalid OTP. {5 - attempts} attempts remaining.", "danger")
        else:
            user = check_existing_user(email)
            if not user:
                name = session.get("name")
                if not name:
                    flash("Registration information is missing. Please register again.", "danger")
                    clear_otp_session()
                    return redirect(url_for("register"))
                user = User(name=name.strip(), email=email.strip().lower())
                db.session.add(user)
                db.session.commit()
            login_user(user)
            was_registration = purpose == "registration"
            clear_otp_session()
            flash("Email verified successfully!", "success")
            if was_registration and not FitnessProfile.query.filter_by(user_id=user.id).first():
                return redirect(url_for("fitness_profile"))
            return redirect(url_for("home"))
    return render_template("verify_otp.html",form=verify_otp_form,remaining=remaining,resend_remaining=resend_remaining,email=email,)


@app.route("/resend-otp", methods=["POST"])
def resend_otp():
    email = session.get("email")
    purpose = session.get("otp_purpose")
    if not email or purpose not in {"login", "registration", "contact"}:
        flash("No verification request was found. Please start again.", "warning")
        return redirect(url_for("login"))
    try:
        generate_otp(email, purpose=purpose)
        flash("A new verification code has been sent.", "info")
    except ValueError as exc:
        flash(str(exc), "warning")
    except Exception as exc:
        print("RESEND OTP ERROR:", exc)
        flash("We couldn't send a new code right now. Please try again.", "danger")
    if purpose == "contact":
        return redirect(url_for("verify_contact"))
    return redirect(url_for("verify_otp"))


@app.route("/register", methods=["GET", "POST"])
def register():
    register_form = Registerform()
    if register_form.validate_on_submit():
        email = register_form.email.data.strip().lower()
        name = register_form.name.data.strip()
        existing = check_existing_user(email)
        session["name"] = name
        if existing:
            flash("An account already exists. We'll send you a login code.", "info")
            purpose = "login"
        else:
            purpose = "registration"
        try:
            generate_otp(email, purpose=purpose)
        except Exception as exc:
            print("REGISTER OTP ERROR:", exc)
            flash("We couldn't send the verification code. Please try again.", "danger")
            return redirect(url_for("register"))
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
        "equipment": [x for x in profile.equipment.split(",") if x],
    }


def validate_and_serialize_plan(ai_result, expected_workout_days):
    """Validate a raw Gemini JSON string and return the serialized plan.
    Pulled out of run_generation so the validation rules
    (used to check the AI response) live in exactly one place. Raises
    ValueError on anything invalid -- callers decide how to surface that.
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
    return db.session.execute(db.select(WorkoutPlan).where(WorkoutPlan.user_id == user_id).order_by(WorkoutPlan.id.desc())).scalars().first()


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


def next_week_number(user_id):
    count = db.session.execute(db.select(db.func.count(WorkoutPlan.id)).where(
        WorkoutPlan.user_id == user_id, WorkoutPlan.status.in_(("ready", "generating")))).scalar_one()
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


def build_history(user_id, tier):
    """What each tier is allowed to 'remember' about the user's past weeks.
    Basic: just last week's raw plan. Personalized: last week + completion.
    Advanced: up to 4 weeks (newest in full detail)."""
    limit = 4 if tier == "advanced" else 1
    rows = db.session.execute(db.select(WorkoutPlan).where(
        WorkoutPlan.user_id == user_id, WorkoutPlan.status == "ready").order_by(WorkoutPlan.id.desc()).limit(limit)).scalars().all()
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
        raise ai_paid.PlanGenerationError("budget", "daily OpenAI limit reached")


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
                raise ai_paid.PlanGenerationError("config", "profile missing")
            profile_data, expected_days = profile_to_dict(profile), int(profile.workout_days)
            history = build_history(user_id, tier)
            payment = db.session.get(Payment, plan.payment_id) if plan.payment_id else None
            if tier != "basic" and not (payment and payment.status in ("paid", "used")
                                        and payment.payment_verification_status == "verified"):
                raise ai_paid.PlanGenerationError("config", "no verified payment")   # never generate unpaid
            coach_notes = None
            if tier == "basic":
                ai_result = generate_fitness_plan(profile=profile_data, previous_plan=history)   # Gemini only
                if not ai_result:
                    raise ai_paid.PlanGenerationError("provider_error")
                serialized = validate_and_serialize_plan(ai_result, expected_days)
            else:
                def record(**fields):
                    return usage.record_attempt(user_id=user_id, plan_id=plan_id, payment_id=payment.id, **fields)
                result = ai_paid.generate_paid_plan(
                    tier, profile_data, history, plan.week_number, payment.user_notes,
                    validator=lambda raw: validate_and_serialize_plan(raw, expected_days),
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
            if tier == "basic":
                plan.ai_provider = "gemini"
            else:
                usage.refresh_plan_totals(plan_id)
                payment.status, payment.used_at = "used", utcnow()
            if plan.week_number > 1:
                advance_progression(user_id)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            code = getattr(exc, "code", None)
            if code not in SAFE_ERROR_CODES:
                code = "invalid_output" if isinstance(exc, ValueError) else "provider_error"
            print("PLAN GENERATION ERROR:", tier, code, type(exc).__name__)   # server log only
            try:
                plan = db.session.get(WorkoutPlan, plan_id)
                if plan and plan.status == "generating" and plan.locked_by == token:
                    plan.status, plan.generation_status, plan.error_message = "failed", "failed", code
                    plan.locked_at = plan.locked_by = None
                    if tier != "basic":
                        usage.refresh_plan_totals(plan_id)
                    db.session.commit()
            except Exception as inner_exc:
                print("PLAN GENERATION ERROR (could not record failure):", type(inner_exc).__name__)
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
                       ai_provider="openai" if payment else "gemini")
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
        if profile.equipment:
            fitness_form.equipment.data = profile.equipment.split(",")
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
            profile.equipment = ",".join(fitness_form.equipment.data)
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
                equipment=",".join(fitness_form.equipment.data),
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
        print("PAYMENTS DISABLED:", billing.config_problem())   # reason goes to the log, never to the customer
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
    payment = db.session.execute(db.select(Payment).where(
        Payment.user_id == current_user.id, Payment.tier == tier, Payment.status == "created",
        Payment.created_at >= utcnow() - timedelta(minutes=30)).order_by(Payment.id.desc())).scalars().first()
    if payment:  # double click / reopened checkout: reuse the same open order
        payment.user_notes = notes
        db.session.commit()
    else:
        if recent_orders >= 8:
            return _json_error("Too many attempts. Please wait a few minutes and try again.", 429)
        amount = billing.price_paise(tier)  # price is decided here, never by the browser
        try:
            order = billing.create_order(amount, f"t2c_{current_user.id}_{secrets.token_hex(6)}",
                                         {"user_id": str(current_user.id), "tier": tier})
        except billing.BillingError as exc:
            print("PAYMENT ORDER ERROR:", exc)
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
                print("PAYMENT MISMATCH for order", order_id)
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
        print("PAYMENT VERIFY ERROR:", exc)
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
        print("WEBHOOK ERROR:", type(exc).__name__)
        return "", 500
    if paid_payment is not None:     # browser may have closed: build the plan anyway
        try:
            db.session.refresh(paid_payment)
            start_paid_generation(paid_payment)
        except Exception as exc:
            db.session.rollback()
            print("WEBHOOK START ERROR:", type(exc).__name__)   # customer still has the "Build my plan" button
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
@login_required
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


@app.route("/admin/payments/<int:payment_id>/refund", methods=["POST"])
@login_required
@admin_required
@limiter.limit("20 per hour")
def admin_issue_refund(payment_id):
    """The ONLY place a refund is created: an admin presses the button for a requested refund."""
    payment = db.session.get(Payment, payment_id)
    if not payment or payment.refund_status != "requested" or payment.status != "paid" or not payment.gateway_payment_id:
        flash("That refund can't be issued (not requested, or already handled).", "danger")
        return redirect(url_for("admin_finance"))
    # Claim first so a double click can't send two refunds; also blocks plan generation meanwhile.
    claimed = db.session.execute(update(Payment).where(Payment.id == payment.id, Payment.refund_status == "requested").values(
        refund_status="processing", status="refunded")).rowcount
    db.session.commit()
    if not claimed:
        flash("That refund is already being processed.", "info")
        return redirect(url_for("admin_finance"))
    try:
        refund = billing.create_refund(payment.gateway_payment_id, payment.amount_paise,
                                       {"reason": "plan not delivered", "payment_row": str(payment.id)})
        payment.gateway_refund_id = str(refund.get("id") or "")[:64] or None
        if refund.get("status") == "processed":
            payment.refund_status, payment.refund_amount_paise, payment.refunded_at = "refunded", payment.amount_paise, utcnow()
            payment.payment_status = "refunded"
        db.session.commit()
        flash("Refund sent to Razorpay.", "success")
    except billing.BillingError as exc:
        print("REFUND ERROR:", exc)
        db.session.execute(update(Payment).where(Payment.id == payment.id).values(refund_status="requested", status="paid"))
        db.session.commit()
        flash("Razorpay didn't accept the refund. Nothing was changed -- try again later.", "danger")
    return redirect(url_for("admin_finance"))


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
@login_required
def delete_account():
    user_id = current_user.id
    try:
        FitnessProfile.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        WorkoutPlan.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        # Keep payment records for auditing, just detach them from the account.
        Payment.query.filter_by(user_id=user_id).update({"user_id": None}, synchronize_session=False)
        User.query.filter_by(id=user_id).delete(synchronize_session=False)
        db.session.commit()
        logout_user()
        clear_otp_session()
        flash("Your account has been deleted.", "info")
    except Exception as exc:
        db.session.rollback()
        print("DELETE ACCOUNT ERROR:", exc)
        flash("We couldn't delete your account right now.", "danger")
    return redirect(url_for("home"))


# WORKOUT
EXERCISE_VIDEOS = {
    "jumping-jacks": "/static/assets/videos/jumping_jacks.mp4",
    "push-ups": "https://res.cloudinary.com/mj0fhq0h/video/upload/v1788422205/Push-ups.mp4",
    "pull-ups":"https://res.cloudinary.com/mj0fhq0h/video/upload/v1788687647/Create_a_professional_exercise_1.mp4"
}


EXERCISE_NAMES = {
    "jumping-jacks": "Jumping Jacks",
    "high-knees": "High Knees",
    "push-ups": "Push Ups",
    "pull-ups": "Pull Ups",
    "side-lunges": "Side Lunges",
    "hindu-push-ups": "Hindu Push Ups",
    "military-push-ups": "Military Push Ups",
    "calf-raises": "Calf Raises",
    "single-leg-calf-raises": "Single Leg Calf Raises",
    "lying-leg-raises": "Lying Leg Raises",
    "reverse-crunches": "Reverse Crunches",
    "pike-push-ups": "Pike Push Ups",
    "incline-push-ups": "Incline Push Ups",
    "decline-push-ups": "Decline Push Ups",
    "superman": "Superman",
    "bird-dog": "Bird Dog",
    "bulgarian-split-squats": "Bulgarian Split Squats",
    "burpees": "Burpees",
    "mountain-climbers": "Mountain Climbers",
    "diamond-push-ups": "Diamond Push Ups",
    "cobra-stretch": "Cobra Stretch",
    "dumbbell-front-raises": "Dumbbell Front Raises",
    "dumbbell-rear-delt-fly": "Dumbbell Rear Delt Fly",
    "sit-ups": "Sit Ups",
    "bicycle-crunches": "Bicycle Crunches",
    "v-up": "V Ups",
    "russian-twist": "Russian Twists",
    "butt-bridge": "Glute Bridge",
    "dumbbell-lateral-raises": "Dumbbell Lateral Raises",
    "plank": "Plank",
    "skipping": "Skipping",
    "skipping-without-rope": "Skipping Without Rope",
    "alternating-hooks": "Alternating Hooks",
    "dumbell-bicep-curls": "Dumbbell Bicep Curls",
    "tricep-kickbacks": "Tricep Kickbacks",
    "tricep-overhead-single-arm-dumbell-extension-left": "Tricep Overhead Dumbbell Extension Left",
    "tricep-overhead-single-arm-dumbell-extension-right": "Tricep Overhead Dumbbell Extension Right",
    "squats": "Squats",
    "lunges-with-dumbells": "Lunges With Dumbbells",
    "lunges-with-bagpack": "Lunges With Backpack",
    "jumping-squats": "Jumping Squats",
    "wall-sit": "Wall Sit",
    "mike-tyson-push-ups": "Mike Tyson Push Ups",
    "cat-cow-pose": "Cat Cow Pose",
    "floor-y-raises": "Floor Y Raises",
    "reverse-snow-angels": "Reverse Snow Angels",
    "child-pose": "Child's Pose",
    "explosive-push-ups": "Explosive Push Ups",
    "flutter-kicks": "Flutter Kicks",
    "bear-crawl": "Bear Crawl",
    "tuck-jumps": "Tuck Jumps",
    "shadow-boxing": "Shadow Boxing",
    "downward-dog": "Downward Dog",
    "worlds-greatest-stretch": "World's Greatest Stretch",
    "pigeon-pose": "Pigeon Pose",
    "seated-forward-fold": "Seated Forward Fold",
    "hamstring-stretch-left": "Hamstring Stretch Left",
    "hamstring-stretch-right": "Hamstring Stretch Right",
    "quad-stretch-left": "Quad Stretch Left",
    "quad-stretch-right": "Quad Stretch Right",
    "hip-flexor-stretch-left": "Hip Flexor Stretch Left",
    "hip-flexor-stretch-right": "Hip Flexor Stretch Right",
    "butterfly-stretch": "Butterfly Stretch",
    "childs-pose": "Child's Pose",
    "barbell-back-squat": "Barbell Back Squat",
    "barbell-bent-over-row": "Barbell Bent Over Row",
    "barbell-overhead-press": "Barbell Overhead Press",
    "barbell-skull-crushers": "Barbell Skull Crushers"
}


def load_current_plan():
    """The user's newest READY week (the one they are training on)."""
    return db.session.execute(db.select(WorkoutPlan).where(
        WorkoutPlan.user_id == current_user.id, WorkoutPlan.status == "ready").order_by(WorkoutPlan.id.desc())).scalars().first()

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
        print("WORKOUT JSON ERROR:", exc)
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
        if old and old.user_id == current_user.id and old.status == "ready":
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
            print("HOME PLAN JSON ERROR:", exc)
            flash("Your saved workout plan is invalid. Please generate a new one.", "danger")
            workout_plan_data = None
    history = db.session.execute(db.select(WorkoutPlan.id, WorkoutPlan.week_number, WorkoutPlan.tier).where(
        WorkoutPlan.user_id == current_user.id, WorkoutPlan.status == "ready").order_by(WorkoutPlan.id.desc())).all()
    # Basic ("free") for the next week unlocks after all 7 days are done, as before;
    # a brand-new user with no plan yet can always start one.
    free_unlocked = shown is None or (len(completed_days) >= 7 and not viewing_old)
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


@app.route("/contact", methods=["GET", "POST"])
def contact():
    form = ContactForm()
    if current_user.is_authenticated:
        if form.validate_on_submit():
            message = form.message.data.strip()
            try:
                send_contact_info(email=current_user.email, name=current_user.name, contact_message=message)
                flash("Your message has been sent successfully!", "success")
                return redirect(url_for("contact"))
            except Exception as exc:
                print("CONTACT EMAIL ERROR:", exc)
                flash("We couldn't send your message right now. Please try again.", "danger")
        return render_template("contact.html", form=form)

    if form.validate_on_submit():
        name = form.name.data.strip()
        email = form.email.data.strip().lower()
        message = form.message.data.strip()
        session["name"] = name
        session["email"] = email
        session["message"] = message
        try:
            generate_otp(email, purpose="contact")
        except Exception as exc:
            print("CONTACT OTP ERROR:", exc)
            flash("We couldn't send the verification code. Please try again.", "danger")
            return redirect(url_for("contact"))
        flash("A verification code has been sent to your email.", "info")
        return redirect(url_for("verify_contact"))
    return render_template("contact.html", form=form)


@app.route("/verify-contact", methods=["GET", "POST"])
def verify_contact():
    if session.get("otp_purpose") != "contact":
        flash("No contact verification request found.", "warning")
        return redirect(url_for("contact"))

    email = session.get("email")
    if not email or not session.get("otp"):
        flash("Your verification session has expired. Please try again.", "warning")
        return redirect(url_for("contact"))

    form = ContactOTPForm()
    # The resend countdown must use the 30-second resend cooldown, NOT
    # the 10-minute OTP lifetime (otp_remaining()). These are different
    # concepts: otp_remaining() is only used server-side to decide
    # whether the submitted code has expired.
    resend_remaining = otp_resend_remaining()
    if form.validate_on_submit():
        if otp_is_expired():
            flash("That verification code has expired. Please request a new one.", "warning")
        elif form.verification_code.data.strip() != session.get("otp"):
            attempts = int(session.get("otp_attempts", 0)) + 1
            session["otp_attempts"] = attempts
            if attempts >= 5:
                clear_otp_session()
                flash("Too many incorrect attempts. Please start the contact request again.", "danger")
                return redirect(url_for("contact"))
            flash(f"Invalid OTP. {5 - attempts} attempts remaining.", "danger")
        else:
            try:
                send_contact_info(email=email,name=session.get("name", ""),contact_message=session.get("message", ""),)
                flash("Your message has been sent successfully!", "success")
                clear_otp_session()
                return redirect(url_for("home"))
            except Exception as exc:
                print("CONTACT DELIVERY ERROR:", exc)
                flash("We verified your email, but couldn't send the message. Please try again.", "danger")
    return render_template("verify_contact.html",form=form,resend_remaining=resend_remaining,email=email,)

@app.route("/find-trainer", methods=["GET", "POST"])
def find_trainer():
    # 1. USER MUST BE LOGGED I
    if not current_user.is_authenticated:
        flash("Please log in or register to find a personal trainer.","warning")
        return redirect(url_for("register"))
    # 2. CHECK FITNESS PROFIL
    profile = FitnessProfile.query.filter_by(user_id=current_user.id).first()
    form = TrainerSearchForm()
    # 3. IF PROFILE EXISTS
    #    DON'T ASK GENDE
    if profile:
        # Use gender from existing profile
        user_gender = profile.gender
        # Remove gender field from the form visually
        del form.gender
    # 4. IF PROFILE DOESN'T EXIST
    #    USER MUST ENTER GENDE
    else:
        user_gender = None

    # 5. FORM SUBMISSIO
    if form.validate_on_submit():
        # Profile already contains gender
        if profile:
            gender = profile.gender
        # No profile → gender came from form
        else:
            gender = form.gender.data
        location = form.location.data.strip()
        mode = form.mode.data
        # FIND TRAINERS
        trainers = Trainer.query.filter_by(gender=gender).all()
        # ONLINE
        if mode == "online":
            # No distance checking
            pass
        # OFFLINE
        elif mode == "offline":
            trainers = find_nearby_trainers(location=location,gender=gender)
        # RESULTS
        return render_template("trainer_results.html",trainers=trainers,mode=mode,location=location)
    return render_template("find_trainer.html",form=form,profile=profile)

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
            print("Trainer email error:", e)
            flash("Something went wrong while sending your application. Please try again.","danger")
    return render_template("become_trainer.html",form=form,profile=profile)
        
# ADMIN
# Every /admin/... route is protected by BOTH @login_required and
# @admin_required (stacked), so authorization is enforced server-side on
# every request -- never by hiding a link/button in a template. A signed-in
# non-admin hitting any of these gets redirected with a flash message; an
# unauthenticated visitor gets sent through the normal login flow first.
@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    # One round trip instead of three: with NullPool every query on a
    # cold connection is expensive, so batch the counts into one SELECT.
    user_count, admin_count, trainer_count = db.session.execute(
        db.select(
            db.select(db.func.count(User.id)).scalar_subquery(),
            db.select(db.func.count(User.id)).where(User.is_admin.is_(True)).scalar_subquery(),
            db.select(db.func.count(Trainer.id)).scalar_subquery(),
        )
    ).one()
    return render_template(
        "admin/dashboard.html",
        trainer_count=trainer_count,
        user_count=user_count,
        admin_count=admin_count,
        fin=finance.overview({}),
        env_info=_env_info(),
    )


def _env_info():
    """Shown to admins only: which mode the site is in and any money-settings warnings."""
    warnings = []
    problem = billing.config_problem()
    if problem:
        warnings.append("Payments are OFF: " + problem)
    if config.usd_to_inr() is None:
        warnings.append("OPENAI_USD_TO_INR is not set, so API costs in rupees are unknown.")
    for tier in billing.PAID_TIERS:
        if costing.pricing_for(config.plan_model(tier)) is None:
            warnings.append(f"No price is configured for the {tier} model, so its API cost is recorded as unknown.")
    if not os.environ.get("OPENAI_API_KEY"):
        warnings.append("OPENAI_API_KEY is not set: paid plans cannot be generated.")
    return {"environment": config.app_environment(), "razorpay_mode": config.razorpay_mode(),
            "generation_mode": config.generation_mode(), "warnings": warnings}


@app.route("/admin/finance")
@login_required
@admin_required
def admin_finance():
    filters = finance.parse_filters(request.args)
    items, total = finance.transactions(filters)
    pages = max(1, -(-total // finance.PAGE_SIZE))
    return render_template(
        "admin/finance.html", fin=finance.overview(filters), items=items, total=total, filters=filters,
        pages=pages, env_info=_env_info(), plan_choices=finance.PLAN_CHOICES,
        payment_status_choices=finance.PAYMENT_STATUS_CHOICES, generation_status_choices=finance.GENERATION_STATUS_CHOICES,
        raw=request.args)


@app.route("/admin/trainers")
@login_required
@admin_required
def admin_trainers():
    search = request.args.get("q", "").strip()
    query = db.select(Trainer).order_by(Trainer.name)
    if search:
        like = f"%{search}%"
        query = query.where(db.or_(Trainer.name.ilike(like), Trainer.location.ilike(like)))
    page = db.paginate(query, page=request.args.get("page", 1, type=int),
                       per_page=ADMIN_PAGE_SIZE, error_out=False)
    return render_template("admin/trainers.html", trainers=page.items, page=page, search=search)


@app.route("/admin/trainers/new", methods=["GET", "POST"])
@login_required
@admin_required
def admin_trainer_new():
    form = AdminTrainerForm()
    if form.validate_on_submit():
        coordinates = get_coordinates(form.location.data.strip())
        if not coordinates:
            flash("Couldn't find that location. Try a more specific address.", "danger")
            return render_template("admin/trainer_form.html", form=form, mode="new", trainer=None)
        latitude, longitude = coordinates
        trainer = Trainer(
            name=form.name.data.strip(),
            gender=form.gender.data,
            location=form.location.data.strip(),
            latitude=latitude,
            longitude=longitude,
            about=form.about.data.strip() if form.about.data else None,
        )
        db.session.add(trainer)
        db.session.commit()
        flash(f"Trainer \"{trainer.name}\" added.", "success")
        return redirect(url_for("admin_trainers"))
    return render_template("admin/trainer_form.html", form=form, mode="new", trainer=None)


@app.route("/admin/trainers/<int:trainer_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def admin_trainer_edit(trainer_id):
    trainer = db.session.get(Trainer, trainer_id)
    if not trainer:
        return "Trainer not found", 404
    form = AdminTrainerForm(obj=trainer)
    if form.validate_on_submit():
        location_changed = form.location.data.strip() != trainer.location
        if location_changed:
            coordinates = get_coordinates(form.location.data.strip())
            if not coordinates:
                flash("Couldn't find that location. Try a more specific address.", "danger")
                return render_template("admin/trainer_form.html", form=form, mode="edit", trainer=trainer)
            trainer.latitude, trainer.longitude = coordinates
        trainer.name = form.name.data.strip()
        trainer.gender = form.gender.data
        trainer.location = form.location.data.strip()
        trainer.about = form.about.data.strip() if form.about.data else None
        db.session.commit()
        flash(f"Trainer \"{trainer.name}\" updated.", "success")
        return redirect(url_for("admin_trainers"))
    return render_template("admin/trainer_form.html", form=form, mode="edit", trainer=trainer)


@app.route("/admin/trainers/<int:trainer_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_trainer_delete(trainer_id):
    # POST-only (never a GET link), CSRF-protected by the global
    # CSRFProtect, and the confirmation step lives in the template
    # (admin/trainers.html) as a JS confirm dialog before the form submits.
    trainer = db.session.get(Trainer, trainer_id)
    if not trainer:
        return "Trainer not found", 404
    name = trainer.name
    db.session.delete(trainer)
    db.session.commit()
    flash(f"Trainer \"{name}\" deleted.", "info")
    return redirect(url_for("admin_trainers"))



# ADMIN: USER CRUD
class RowPagination(SelectPagination):
    """db.paginate() keeps only the first column of each row (it calls
    .scalars()). The admin user list selects several columns, so keep the
    full rows instead; counting and limit/offset are inherited unchanged."""

    def _query_items(self):
        select = self._query_args["select"].limit(self.per_page).offset(self._query_offset)
        return list(self._query_args["session"].execute(select).all())



def count_admins():
    return db.session.scalar(
        db.select(db.func.count(User.id)).where(User.is_admin.is_(True)))


def email_taken(email, exclude_id=None):
    query = db.select(User.id).where(User.email == email)
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    return db.session.scalar(query) is not None


@app.route("/admin/users")
@login_required
@admin_required
def admin_users():
    search = request.args.get("q", "").strip()
    # One query: outer-join the profile and plan so the list can show goal /
    # experience / plan status without an extra query per row. The heavy
    # plan JSON column is NOT selected here -- only on the detail page.
    latest_status = (db.select(WorkoutPlan.status).where(WorkoutPlan.user_id == User.id)
                     .order_by(WorkoutPlan.id.desc()).limit(1).scalar_subquery())
    query = (
        db.select(User, FitnessProfile.goal, FitnessProfile.experience, latest_status)
        .outerjoin(FitnessProfile, FitnessProfile.user_id == User.id)
        .order_by(User.name)
    )
    if search:
        like = f"%{search}%"
        query = query.where(db.or_(User.name.ilike(like), User.email.ilike(like)))
    page = RowPagination(page=request.args.get("page", 1, type=int), per_page=ADMIN_PAGE_SIZE,
                         max_per_page=None, error_out=False, count=True,
                         select=query, session=db.session)
    return render_template("admin/users.html", rows=page.items, page=page, search=search)


@app.route("/admin/users/<int:user_id>")
@login_required
@admin_required
def admin_user_detail(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return "User not found", 404
    profile = db.session.execute(
        db.select(FitnessProfile).where(FitnessProfile.user_id == user.id)).scalar_one_or_none()
    plan_row = latest_plan_row(user.id)

    age = None
    equipment = []
    if profile:
        today = date.today()
        age = today.year - profile.dob.year - ((today.month, today.day) < (profile.dob.month, profile.dob.day))
        equipment = [x.strip() for x in profile.equipment.split(",") if x.strip()]

    plan_days = []
    completed_days = []
    plan_error = None
    if plan_row:
        try:
            completed = json.loads(plan_row.completed_days or "[]")
            completed_days = completed if isinstance(completed, list) else []
        except (TypeError, ValueError):
            completed_days = []
        if plan_row.status == "ready":
            try:
                parsed = json.loads(plan_row.plan)
                for key in sorted(parsed, key=lambda k: int(k.split("_")[1])):
                    day = parsed[key]
                    plan_days.append({
                        "number": int(key.split("_")[1]),
                        "meta": day[0],
                        "exercises": [e for e in day[1:] if isinstance(e, dict)],
                    })
            except (TypeError, ValueError, KeyError, IndexError):
                plan_error = "This saved plan could not be read (invalid JSON)."
    return render_template(
        "admin/user_detail.html", user=user, profile=profile, age=age, equipment=equipment,
        plan=plan_row, plan_days=plan_days, completed_days=completed_days,
        plan_error=plan_error, exercise_names=EXERCISE_NAMES,
    )


@app.route("/admin/users/new", methods=["GET", "POST"])
@login_required
@admin_required
def admin_user_new():
    form = AdminUserForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if email_taken(email):
            form.email.errors.append("A user with this e-mail already exists.")
            return render_template("admin/user_form.html", form=form, mode="new", user=None)
        user = User(name=form.name.data.strip(), email=email, is_admin=bool(form.is_admin.data))
        db.session.add(user)
        db.session.commit()
        flash(f"User \"{user.name}\" added.", "success")
        return redirect(url_for("admin_users"))
    return render_template("admin/user_form.html", form=form, mode="new", user=None)


@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def admin_user_edit(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return "User not found", 404
    form = AdminUserForm(obj=user)
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        wants_admin = bool(form.is_admin.data)
        if email_taken(email, exclude_id=user.id):
            form.email.errors.append("A user with this e-mail already exists.")
            return render_template("admin/user_form.html", form=form, mode="edit", user=user)
        # Guard rail: never let the panel lock everyone out. Removing admin
        # rights from the last admin (including yourself) is refused.
        if user.is_admin and not wants_admin and count_admins() <= 1:
            flash("You can't remove admin access from the last remaining admin.", "danger")
            return render_template("admin/user_form.html", form=form, mode="edit", user=user)
        user.name = form.name.data.strip()
        user.email = email
        user.is_admin = wants_admin
        db.session.commit()
        flash(f"User \"{user.name}\" updated.", "success")
        return redirect(url_for("admin_users"))
    return render_template("admin/user_form.html", form=form, mode="edit", user=user)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_user_delete(user_id):
    # POST-only + CSRF-protected, same as the trainer delete. Extra rules:
    # an admin can't delete their own account here (use "Delete account"
    # on the profile page), and the last admin can never be deleted.
    user = db.session.get(User, user_id)
    if not user:
        return "User not found", 404
    if user.id == current_user.id:
        flash("You can't delete your own account from the admin panel.", "danger")
        return redirect(url_for("admin_users"))
    if user.is_admin and count_admins() <= 1:
        flash("You can't delete the last remaining admin.", "danger")
        return redirect(url_for("admin_users"))
    name = user.name
    try:
        # Child rows first -- profiles/workout_plans have FKs to users.id.
        FitnessProfile.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        WorkoutPlan.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        Payment.query.filter_by(user_id=user.id).update({"user_id": None}, synchronize_session=False)
        User.query.filter_by(id=user.id).delete(synchronize_session=False)
        db.session.commit()
        flash(f"User \"{name}\" deleted.", "info")
    except Exception as exc:
        db.session.rollback()
        print("ADMIN DELETE USER ERROR:", exc)
        flash("We couldn't delete that user right now.", "danger")
    return redirect(url_for("admin_users"))


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


if __name__ == "__main__":
    app.run(debug=False, port=5002)
