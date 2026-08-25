from flask import Flask, render_template, redirect, url_for, session, flash
from forms import Loginform, OTPform, Registerform,FitnessProfileform
from flask_bootstrap import Bootstrap5
import random, os, time, json, smtplib
from sqlalchemy.orm import Mapped, mapped_column, DeclarativeBase
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, LoginManager, login_user, logout_user,current_user, login_required
from sqlalchemy import Integer, String, Text, Date, ForeignKey
from functools import wraps
from dotenv import load_dotenv
from datetime import date, datetime
from ai import generate_fitness_plan
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy.pool import NullPool
# Creating flak object
app=Flask(__name__)
#that Flask-WTF needs a secret key to generate and verify the CSRF token.
app.config["SECRET_KEY"]="train2conquerbysyedmatheenandteam"
bootstrap=Bootstrap5(app)

login_manager=LoginManager()
login_manager.init_app(app)

load_dotenv()
class Base(DeclarativeBase):# Create a base class for all database models.A database model is a Python class that defines the structure of a database table. Each attribute in the class becomes a column in the table.
    pass


#database_url = os.environ.get("SUPABASE","sqlite:///train2conquer.db")
# Configure the database connection for SQLAlchemy.
# "SQLALCHEMY_DATABASE_URI" tells Flask which database to use.
# "sqlite:  ///" means use an SQLite database stored as a local file.
# "train2conquer.db" is the database file that will be created in the project's instance folder (or configured location).
#app.config["SQLALCHEMY_DATABASE_URI"]=database_url 
app.config["SQLALCHEMY_DATABASE_URI"]="sqlite:///train2conquer.db" 
#if database_url.startswith("postgresql://"):
#    db = SQLAlchemy(model_class=Base,engine_options={"poolclass": NullPool})
#else:
db = SQLAlchemy(model_class=Base) ## Create a SQLAlchemy object and use our Base class for all models.
#Connect SQLAlchemy to the Flask application.
db.init_app(app) # now SQLAlchemy knows which app to use.

#Create a User class that is a database table and has login features
#This class is called a model because it models (describes) the structure of the table. 
class User(UserMixin,db.Model): # UserMixin Adds ready-made login features for Flask-Login, such as is_authenticated, is_active, is_anonymous, get_id().db.Model tells SQLAlchemy that this class represents a database table.
    __tablename__="users" # specifies the name of the table in the database, Here, the table will be created as users.
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    name:Mapped[str]=mapped_column(String(100),nullable=False)
    email:Mapped[str]=mapped_column(String(200),unique=True,nullable=False)
class FitnessProfile(db.Model):
    __tablename__ = "profiles"
    id: Mapped[int] = mapped_column(Integer,primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"),nullable=False,unique=True)
    dob: Mapped[date] = mapped_column(Date,nullable=False)
    height: Mapped[int] = mapped_column(Integer,nullable=False)
    weight: Mapped[int] = mapped_column(Integer,nullable=False)
    gender: Mapped[str] = mapped_column(String(20),nullable=False)
    goal: Mapped[str] = mapped_column(String(50),nullable=False)
    experience: Mapped[str] = mapped_column(String(30),nullable=False)
    progression_score: Mapped[int] = mapped_column(Integer,nullable=False,default=1) 
    workout_days: Mapped[int] = mapped_column(Integer,nullable=False)
    equipment: Mapped[str] = mapped_column(Text,nullable=False)

class WorkoutPlan(db.Model):
    __tablename__ = "workout_plans"
    id: Mapped[int] = mapped_column(Integer,primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"),nullable=False,unique=True)
    created_at = db.Column(db.DateTime,default=datetime.utcnow)
    plan: Mapped[str] = mapped_column(Text,nullable=False)
    completed_days: Mapped[str] = mapped_column(Text, default="[]", nullable=False)


# Tell Flask that the following code belongs to this application.
with app.app_context():
    # Create all the tables in the database.
    # If the tables already exist, nothing happens.
    db.create_all()

@login_manager.user_loader #Tells Flask-Login: Use the function below whenever you need to load a logged-in user.
def load_user(user_id): #Flask-Login automatically passes the logged-in user's ID to this function
    return db.get_or_404(User,user_id) #Looks for the user with that ID in the User table. If found returns the User object else 404 error.

def logged_in_users_only(function):
    @wraps(function)
    def decorator_function(*args,**kwargs):
        if not current_user.is_authenticated:
           flash("Please login or register first.", "warning")
           return redirect(url_for('login'))    
        return function(*args,**kwargs)
    return decorator_function

def send_verification_code(email, otp):
    sender_email = os.environ.get("EMAIL")
    sender_password = os.environ.get("PASSWORD")
    message = MIMEMultipart()
    message["From"] = sender_email
    message["To"] = email
    message["Subject"] = "Train2Conquer Verification Code"
    body = f"""
    <html>
        <body>
            <h2>Train2Conquer</h2>
            <p>Your verification code is:</p>
            <h1>{otp}</h1>
            <p>Enter this code to verify your email.</p>
        </body>
    </html>
    """
    message.attach(MIMEText(body, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(
            sender_email, email,message.as_string()) 

def generate_otp(email):
        otp=random.randint(100000,999999)
        session["email"]=email
        session["otp"]=str(otp)
        send_verification_code(email,otp)
        session["otp_created"]=time.time()

def check_existing_user(email):
    return db.session.execute(db.select(User).where(User.email==email)).scalar()

def pop_session():
    session.pop("otp",None) # if otp does not exist it will return None, else KeyError.
    session.pop("otp_created", None)
    session.pop("name",None)
    session.pop("email",None)

@app.route("/login", methods = ["GET", "POST"])
def login():
    login_form = Loginform()
    if login_form.validate_on_submit():
        email = login_form.email.data
        if not check_existing_user(email):
            flash("You don't have an account. Please register first.", "warning")
            return redirect(url_for('register'))
        generate_otp(email)
        return redirect(url_for('verify_otp'))
    return render_template("login.html",form=login_form)

@app.route("/verify-otp",methods=["GET","POST"])
def verify_otp():
    verify_otp_form=OTPform()
    remaining=max(0,30-int(time.time()-session["otp_created"]))
    if verify_otp_form.validate_on_submit():
        if verify_otp_form.verification_code.data==session.get("otp"):
            flash("OTP verified successfully!", "success")
            user=check_existing_user(session.get("email"))
            if not user:
                user=User(name=session.get("name"),email=session.get("email"))
                db.session.add(user)
                db.session.commit()
                login_user(user)
                return redirect(url_for('fitness_profile'))
            login_user(user)
            pop_session()
            return redirect(url_for('home'))
        else:
           flash("Invalid OTP. Please try again.", "danger")
    return render_template("verify_otp.html",form=verify_otp_form,remaining=remaining)

@app.route("/resend-otp")
def resend_otp():
    email=session.get("email")
    generate_otp(email)
    return redirect(url_for('verify_otp'))

@app.route("/register",methods=["GET","POST"])
def register():
    register_form=Registerform()
    if register_form.validate_on_submit():
        email=register_form.email.data
        session["name"]=register_form.name.data
        if check_existing_user(email):
            flash("You Already have an account","warning")
            generate_otp(email)
            return redirect(url_for('verify_otp'))
        generate_otp(email)
        return redirect(url_for('verify_otp'))
    return render_template("register.html",form=register_form)

@app.route("/fitness-profile", methods=["GET", "POST"])
@login_required
def fitness_profile():
    profile = db.session.execute(db.select(FitnessProfile).where(FitnessProfile.user_id == current_user.id)).scalar_one_or_none()
    # Existing profile
    if profile:
        fitness_form = FitnessProfileform(obj=profile)
        if profile.equipment:
            fitness_form.equipment.data = profile.equipment.split(",")
    # New profile
    else:
        fitness_form = FitnessProfileform()
    if fitness_form.validate_on_submit():
        # UPDATE EXISTING PROFILE
        if profile:
            profile.dob = fitness_form.dob.data
            profile.height = fitness_form.height.data
            profile.weight = fitness_form.weight.data
            profile.gender = fitness_form.gender.data
            profile.goal = fitness_form.goal.data
            profile.experience = fitness_form.experience.data
            if profile.progression_score is None:
                profile.progression_score = 1
            if profile.experience=="intermediate":
                if profile.progression_score<8:
                    profile.progression_score+=7
            elif  profile.experience=="advanced":
                if profile.progression_score<15:
                    profile.progression_score+=7
            profile.workout_days = int(fitness_form.workout_days.data)
            profile.equipment = ",".join(fitness_form.equipment.data)
        # CREATE NEW PROFILE
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
                equipment=",".join(fitness_form.equipment.data))
            if profile.progression_score is None:
                profile.progression_score = 1
            if profile.experience=="intermediate":
                if profile.progression_score<8:
                    profile.progression_score+=7
            elif  profile.experience=="advanced":
                if profile.progression_score<15:
                    profile.progression_score+=7
            db.session.add(profile)
        # Save profile
        db.session.commit()
        flash("Fitness profile saved successfully!", "success")

        # PREPARE DATA FOR AI
        profile_data = {
            "dob": profile.dob.strftime("%d-%m-%Y"),
            "height": profile.height,
            "weight": profile.weight,
            "gender": profile.gender,
            "goal": profile.goal,
            "experience": profile.experience,

            "workout_days": profile.workout_days,
            "equipment": profile.equipment.split(",")
        }
        workout_plan = db.session.execute(db.select(WorkoutPlan).where(WorkoutPlan.user_id == current_user.id)).scalar_one_or_none()
        if workout_plan:
            previous_plan = workout_plan.plan
        else:
            previous_plan = None
        # GENERATE AI WORKOUT PLAN
        ai_result = generate_fitness_plan(profile=profile_data,previous_plan=previous_plan)
        if ai_result is None:
            flash("We couldn't generate your workout plan right now. Please try again.","danger")
            return redirect(url_for("fitness_profile"))
        

        # OVERWRITE EXISTING PLAN
        if workout_plan:
            workout_plan.plan = ai_result
            workout_plan.created_at = datetime.utcnow()
            # New plan = reset completed days
            workout_plan.completed_days = "[]"

        # CREATE FIRST WORKOUT PLAN
        else:
            workout_plan = WorkoutPlan(user_id=current_user.id,plan=ai_result,completed_days="[]")
            db.session.add(workout_plan)
        
        # Save workout plan
        db.session.commit()
        flash("Your workout plan has been updated!","success")
        return redirect(url_for("home"))
    return render_template( "fitness-profile.html", form=fitness_form)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    user_id = current_user.id
    # Delete fitness profile directly from database
    FitnessProfile.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    # Delete workout plan directly from database
    WorkoutPlan.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    # Delete user
    User.query.filter_by(id=user_id).delete(synchronize_session=False)
    db.session.commit()
    logout_user()
    return redirect(url_for("home"))

@app.route("/start-workout/<int:day>")
@login_required
def start_workout(day):
    workout_plan_db = (
        db.session.execute(db.select(WorkoutPlan).where(WorkoutPlan.user_id == current_user.id).order_by(WorkoutPlan.created_at.desc())).scalars().first())
    if not workout_plan_db:
        return "No workout found", 404
    plan = json.loads(workout_plan_db.plan)
    day_key = f"day_{day}"
    if day_key not in plan:
        return "Workout day not found", 404
    workouts = plan[day_key][1:]
    exercise_names = {

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
    "butterfly-stretch": "Butterfly Stretch",

    "childs-pose": "Child's Pose",

    "barbell-back-squat": "Barbell Back Squat",
    "barbell-bent-over-row": "Barbell Bent Over Row",
    "barbell-overhead-press": "Barbell Overhead Press",
    "barbell-skull-crushers": "Barbell Skull Crushers"

}
    for workout in workouts:
        workout["exercise"] = exercise_names.get(workout["exercise"],"")

    return render_template("workout.html",day_number=day,workouts=workouts)

@app.route("/complete-workout/<int:day>", methods=["POST"])
@login_required
def complete_workout(day):
    workout_plan = WorkoutPlan.query.filter_by(user_id=current_user.id).first()
    if not workout_plan:
        return redirect(url_for("home"))
    completed_days = json.loads(workout_plan.completed_days or "[]")
    if day not in completed_days:
        completed_days.append(day)
    workout_plan.completed_days = json.dumps(completed_days)
    db.session.commit()
    return redirect(url_for("home"))

@app.route("/load-next-week", methods=["POST"])
@login_required
def load_next_week():
    profile = db.session.execute(
        db.select(FitnessProfile).where(
            FitnessProfile.user_id == current_user.id
        )
    ).scalar_one_or_none()
    if not profile:
        flash("Please complete your fitness profile first.", "danger")
        return redirect(url_for("fitness_profile"))
    if profile.progression_score is None:
        profile.progression_score = 1
    profile.progression_score +=1 
    if profile.progression_score==15 :
        profile.experience="advanced"
    elif profile.progression_score==8:
            profile.experience="intermediate"
    profile_data = {
        "dob": profile.dob.strftime("%d-%m-%Y"),
        "height": profile.height,
        "weight": profile.weight,
        "gender": profile.gender,
        "goal": profile.goal,
        "experience": profile.experience,
        "progression_score": profile.progression_score,
        "workout_days": profile.workout_days,
        "equipment": profile.equipment.split(",")
    }
    workout_plan = db.session.execute(db.select(WorkoutPlan).where(WorkoutPlan.user_id == current_user.id)).scalar_one_or_none()
    if workout_plan:
        previous_plan = workout_plan.plan
    else:
        previous_plan = None
    # Generate a new workout plan
    ai_result = generate_fitness_plan(profile=profile_data,previous_plan=previous_plan)
    if ai_result is None:
        flash("We couldn't generate your next week's workout plan. Please try again.","danger")
        return redirect(url_for("home"))
    # Get existing workout plan
    # Replace old plan with new plan
    workout_plan.plan = ai_result
    workout_plan.created_at = datetime.utcnow()
    workout_plan.completed_days = "[]"
    db.session.commit()
    flash("Your next week's workout plan is ready!", "success")
    return redirect(url_for("home"))

@app.route("/")
def home():
    workout_plan = None
    workout_plan_data=None
    completed_days = []
    if current_user.is_authenticated:
        workout_plan = WorkoutPlan.query.filter_by(user_id=current_user.id).first()
        if workout_plan:
            # Convert database JSON string → Python dictionary
            workout_plan_data = json.loads(workout_plan.plan)
            # Get completed days from the WorkoutPlan itself
            completed_days = json.loads(workout_plan.completed_days or "[]")
        return render_template("home.html",workout_plan=workout_plan,workout_plan_data=workout_plan_data,completed_days=completed_days)
    return render_template("dashboard.html")
 
@app.route("/about")
def about():
    return render_template("about.html")

if __name__=="__main__":
    app.run(debug=False, port=5002)