import html
import json
import os
import secrets
import smtplib
import time
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, session, url_for
from flask_bootstrap import Bootstrap5
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import Date, ForeignKey, Integer, String, Text, inspect, text, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from forms import (
    ContactForm,
    ContactOTPForm,
    FitnessProfileform,
    Loginform,
    OTPform,
    Registerform,
    TrainerSearchForm,
    TrainerForm
)
from ai import generate_fitness_plan

load_dotenv()

# =========================================================
# APP CONFIGURATION
# =========================================================

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-in-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 7

bootstrap = Bootstrap5(app)
csrf = CSRFProtect(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "warning"
login_manager.init_app(app)


class Base(DeclarativeBase):
    pass


# Supabase/Postgres URLs may arrive as postgres:// or postgresql://.
database_url = os.environ.get("SUPABASE")
if not database_url:
    raise RuntimeError("SUPABASE environment variable is not set")

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url

if database_url.startswith("postgresql"):
    db = SQLAlchemy(model_class=Base, engine_options={"poolclass": NullPool})
else:
    db = SQLAlchemy(model_class=Base)

db.init_app(app)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


# =========================================================
# DATABASE MODELS
# =========================================================

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)

class Trainer(UserMixin, db.Model):
    __tablename__ = "trainers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )
    gender: Mapped[str] = mapped_column(
        String(20),
        nullable=False
    )
    location: Mapped[str] = mapped_column(
        String(200),
        nullable=False
    )
    latitude: Mapped[float] = mapped_column(
        Float,
        nullable=False
    )
    longitude: Mapped[float] = mapped_column(
        Float,
        nullable=False
    )
    about: Mapped[str] = mapped_column(
        String(300),
        nullable=True
    )
    phone_number: Mapped[int]= mapped_column(Integer)

class FitnessProfile(db.Model):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, unique=True
    )
    dob: Mapped[date] = mapped_column(Date, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    gender: Mapped[str] = mapped_column(String(20), nullable=False)
    goal: Mapped[str] = mapped_column(String(50), nullable=False)
    experience: Mapped[str] = mapped_column(String(30), nullable=False)
    progression_score: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    workout_days: Mapped[int] = mapped_column(Integer, nullable=False)
    equipment: Mapped[str] = mapped_column(Text, nullable=False)


class WorkoutPlan(db.Model):
    __tablename__ = "workout_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, unique=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    plan: Mapped[str] = mapped_column(Text, nullable=False)
    completed_days: Mapped[str] = mapped_column(
        Text, default="[]", nullable=False
    )


# =========================================================
# STARTUP DATABASE CHECK / LIGHTWEIGHT MIGRATION
# =========================================================

def ensure_database_schema():
    """Create missing tables and add the completed_days column to old databases."""
    with app.app_context():
        db.create_all()
        inspector = inspect(db.engine)
        if "workout_plans" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("workout_plans")}
            if "completed_days" not in columns:
                db.session.execute(
                    text(
                        "ALTER TABLE workout_plans "
                        "ADD COLUMN completed_days TEXT NOT NULL DEFAULT '[]'"
                    )
                )
                db.session.commit()


try:
    ensure_database_schema()
except Exception as exc:
    # Do not hide the original error; make it visible in deployment logs.
    print("DATABASE STARTUP ERROR:", exc)


# =========================================================
# AUTH HELPERS
# =========================================================

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def logged_in_users_only(function):
    """Backward-compatible custom decorator; prefer @login_required."""
    @wraps(function)
    def decorator_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please login or register first.", "warning")
            return redirect(url_for("login"))
        return function(*args, **kwargs)

    return decorator_function

def send_trainer_application(email,name,about,location,gender,phone_number):
    """Send trainer application to the site owner/admin."""
    sender_email = os.environ.get("EMAIL")
    sender_password = os.environ.get("PASSWORD")

    if not sender_email or not sender_password:
        raise RuntimeError(
            "EMAIL and PASSWORD environment variables are required"
        )

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

            <p>
                <strong>Name:</strong><br>
                {safe_name}
            </p>

            <p>
                <strong>Email:</strong><br>
                {safe_email}
            </p>

            <p>
                <strong>Location:</strong><br>
                {safe_location}
            </p>

            <p>
                <strong>Gender:</strong><br>
                {safe_gender}
            </p>
            <p>
                <strong>Phone Number: </strong><br>
                            {safe_phone_number}
                        </p>

            <p>
                <strong>About Yourself:</strong><br>
                {safe_about}
            </p>

            <hr>

            <p style="color: #777;">
                This application was submitted through Train2Conquer.
            </p>

        </body>
    </html>
    """

    mail.attach(MIMEText(body, "html"))

    with smtplib.SMTP(
        "smtp.gmail.com",
        587,
        timeout=20
    ) as server:

        server.starttls()

        server.login(
            sender_email,
            sender_password
        )

        server.sendmail(
            sender_email,
            [sender_email],
            mail.as_string()
        )

# =========================================================
# EMAIL / OTP
# =========================================================

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
        db.select(User).where(User.email == email.strip().lower())
    ).scalar_one_or_none()


def clear_otp_session():
    for key in (
        "otp",
        "otp_created",
        "otp_purpose",
        "otp_attempts",
        "name",
        "email",
        "message",
    ):
        session.pop(key, None)

def calculate_distance(user_location,trainer_location):
    return 4
# =========================================================
# LOGIN / REGISTRATION
# =========================================================

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

    return render_template(
        "verify_otp.html",
        form=verify_otp_form,
        remaining=remaining,
        resend_remaining=resend_remaining,
        email=email,
    )


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


# =========================================================
# FITNESS PROFILE / AI PLAN
# =========================================================

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


def generate_and_store_plan(profile, workout_plan):
    previous_plan = workout_plan.plan if workout_plan else None
    ai_result = generate_fitness_plan(
        profile=profile_to_dict(profile),
        previous_plan=previous_plan,
    )

    if not ai_result:
        return False

    # Validate the AI response before it ever reaches the database.
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

    expected_workout_days = int(profile.workout_days)
    if workout_day_count != expected_workout_days:
        raise ValueError(
            f"AI generated {workout_day_count} main workout days; expected {expected_workout_days}"
        )

    serialized = json.dumps(parsed)

    if workout_plan:
        workout_plan.plan = serialized
        workout_plan.created_at = datetime.utcnow()
        workout_plan.completed_days = "[]"
    else:
        workout_plan = WorkoutPlan(
            user_id=current_user.id,
            plan=serialized,
            completed_days="[]",
        )
        db.session.add(workout_plan)

    db.session.commit()
    return True


@app.route("/fitness-profile", methods=["GET", "POST"])
@login_required
def fitness_profile():
    profile = db.session.execute(
        db.select(FitnessProfile).where(FitnessProfile.user_id == current_user.id)
    ).scalar_one_or_none()

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
        flash("Fitness profile saved successfully!", "success")

        workout_plan = db.session.execute(
            db.select(WorkoutPlan).where(WorkoutPlan.user_id == current_user.id)
        ).scalar_one_or_none()

        try:
            if not generate_and_store_plan(profile, workout_plan):
                flash("We couldn't generate your workout plan right now. Please try again.", "danger")
                return redirect(url_for("fitness_profile"))
        except Exception as exc:
            db.session.rollback()
            print("AI PLAN ERROR:", exc)
            flash("Your profile was saved, but we couldn't generate the workout plan. Please try again.", "danger")
            return redirect(url_for("fitness_profile"))

        flash("Your workout plan has been updated!", "success")
        return redirect(url_for("home"))

    return render_template("fitness-profile.html", form=fitness_form)


# =========================================================
# ACCOUNT
# =========================================================

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


# =========================================================
# WORKOUT
# =========================================================

EXERCISE_VIDEOS = {
    "jumping-jacks": "/static/assets/videos/jumping_jacks.mp4",
    "push-ups": "/static/assets/videos/pushups.mp4",
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
    return db.session.execute(
        db.select(WorkoutPlan)
        .where(WorkoutPlan.user_id == current_user.id)
        .order_by(WorkoutPlan.created_at.desc())
    ).scalars().first()


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

    workout_plan = WorkoutPlan.query.filter_by(user_id=current_user.id).first()
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
    profile = db.session.execute(
        db.select(FitnessProfile).where(FitnessProfile.user_id == current_user.id)
    ).scalar_one_or_none()

    if not profile:
        flash("Please complete your fitness profile first.", "danger")
        return redirect(url_for("fitness_profile"))

    profile.progression_score = (profile.progression_score or 1) + 1
    if profile.progression_score >= 15:
        profile.experience = "advanced"
    elif profile.progression_score >= 8:
        profile.experience = "intermediate"

    workout_plan = db.session.execute(
        db.select(WorkoutPlan).where(WorkoutPlan.user_id == current_user.id)
    ).scalar_one_or_none()

    try:
        if not generate_and_store_plan(profile, workout_plan):
            flash("We couldn't generate your next week's workout plan. Please try again.", "danger")
            return redirect(url_for("home"))
    except Exception as exc:
        db.session.rollback()
        print("NEXT WEEK AI ERROR:", exc)
        flash("We couldn't generate your next week's workout plan. Please try again.", "danger")
        return redirect(url_for("home"))

    flash("Your next week's workout plan is ready!", "success")
    return redirect(url_for("home"))


# =========================================================
# PAGES
# =========================================================

@app.route("/")
def home():
    workout_plan = None
    workout_plan_data = None
    completed_days = []

    if current_user.is_authenticated:
        workout_plan = WorkoutPlan.query.filter_by(user_id=current_user.id).first()
        if workout_plan:
            try:
                workout_plan_data = json.loads(workout_plan.plan)
                completed_days = json.loads(workout_plan.completed_days or "[]")
                if not isinstance(completed_days, list):
                    completed_days = []
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                print("HOME PLAN JSON ERROR:", exc)
                flash("Your saved workout plan is invalid. Please update your profile.", "danger")
                workout_plan_data = None
                completed_days = []

        return render_template(
            "home.html",
            workout_plan=workout_plan,
            workout_plan_data=workout_plan_data,
            completed_days=completed_days,
        )

    return render_template("dashboard.html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    form = ContactForm()

    if current_user.is_authenticated:
        if form.validate_on_submit():
            message = form.message.data.strip()
            try:
                send_contact_info(
                    email=current_user.email,
                    name=current_user.name,
                    contact_message=message,
                )
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
    remaining = otp_remaining()

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
                send_contact_info(
                    email=email,
                    name=session.get("name", ""),
                    contact_message=session.get("message", ""),
                )
                flash("Your message has been sent successfully!", "success")
                clear_otp_session()
                return redirect(url_for("home"))
            except Exception as exc:
                print("CONTACT DELIVERY ERROR:", exc)
                flash("We verified your email, but couldn't send the message. Please try again.", "danger")

    return render_template(
        "verify_contact.html",
        form=form,
        remaining=remaining,
        email=email,
    )

@app.route("/find-trainer", methods=["GET", "POST"])
def find_trainer():

    # --------------------------------------------------
    # 1. USER MUST BE LOGGED IN
    # --------------------------------------------------

    if not current_user.is_authenticated:
        flash(
            "Please log in or register to find a personal trainer.",
            "warning"
        )
        return redirect(url_for("register"))

    # --------------------------------------------------
    # 2. CHECK FITNESS PROFILE
    # --------------------------------------------------

    profile = FitnessProfile.query.filter_by(
        user_id=current_user.id
    ).first()

    form = TrainerSearchForm()

    # --------------------------------------------------
    # 3. IF PROFILE EXISTS
    #    DON'T ASK GENDER
    # --------------------------------------------------

    if profile:

        # Use gender from existing profile
        user_gender = profile.gender

        # Remove gender field from the form visually
        del form.gender

    # --------------------------------------------------
    # 4. IF PROFILE DOESN'T EXIST
    #    USER MUST ENTER GENDER
    # --------------------------------------------------

    else:

        user_gender = None

    # --------------------------------------------------
    # 5. FORM SUBMISSION
    # --------------------------------------------------

    if form.validate_on_submit():

        # Profile already contains gender
        if profile:
            gender = profile.gender

        # No profile → gender came from form
        else:
            gender = form.gender.data

        location = form.location.data.strip()
        mode = form.mode.data

        # --------------------------------------------------
        # FIND TRAINERS
        # --------------------------------------------------

        trainers = Trainer.query.filter_by(
            gender=gender
        ).all()

        # --------------------------------------------------
        # ONLINE
        # --------------------------------------------------

        if mode == "online":

            # No distance checking
            pass

        # --------------------------------------------------
        # OFFLINE
        # --------------------------------------------------

        elif mode == "offline":

            nearby_trainers = []

            for trainer in trainers:

                distance = calculate_distance(
                    location,
                    trainer.location
                )

                if distance <= 5:
                    nearby_trainers.append(trainer)

            trainers = nearby_trainers

        # --------------------------------------------------
        # RESULTS
        # --------------------------------------------------

        return render_template(
            "trainer_results.html",
            trainers=trainers,
            mode=mode,
            location=location
        )

    return render_template(
        "find_trainer.html",
        form=form,
        profile=profile
    )

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
    return render_template(
        "error.html",
        code=404,
        title="Page not found",
        message="The page you requested does not exist or may have moved.",
    ), 404


@app.errorhandler(500)
def internal_server_error(error):
    db.session.rollback()
    return render_template(
        "error.html",
        code=500,
        title="Something went wrong",
        message="Train2Conquer hit an unexpected error. Please try again in a moment.",
    ), 500


@app.context_processor
def inject_template_globals():
    return {"current_year": datetime.now().year}


if __name__ == "__main__":
    app.run(debug=False, port=5002)