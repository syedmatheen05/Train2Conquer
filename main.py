from flask import Flask, render_template, redirect, url_for, session, flash
from forms import Loginform, OTPform, Registerform,FitnessProfileform
from flask_bootstrap import Bootstrap5
import random, os, time, resend, json, smtplib
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, LoginManager, login_user, logout_user,current_user, login_required
from sqlalchemy import Integer, String, Text, Date, ForeignKey
from functools import wraps
from dotenv import load_dotenv
from datetime import date, datetime
from ai import generate_fitness_plan
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Creating flak object
app=Flask(__name__)
#that Flask-WTF needs a secret key to generate and verify the CSRF token.
app.config["SECRET_KEY"]="train2conquerbysyedmatheenandteam"
bootstrap=Bootstrap5(app)

login_manager=LoginManager()
login_manager.init_app(app)

load_dotenv()
resend.api_key=os.environ.get("API_KEY")

class Base(DeclarativeBase):# Create a base class for all database models.A database model is a Python class that defines the structure of a database table. Each attribute in the class becomes a column in the table.
    pass

# Configure the database connection for SQLAlchemy.
# "SQLALCHEMY_DATABASE_URI" tells Flask which database to use.
# "sqlite:  ///" means use an SQLite database stored as a local file.
# "train2conquer.db" is the database file that will be created in the project's instance folder (or configured location).
app.config["SQLALCHEMY_DATABASE_URI"]=os.environ.get("DATABASE_URL","sqlite:///train2conquer.db") 
#app.config["SQLALCHEMY_DATABASE_URI"]="sqlite:///train2conquer.db" 
db=SQLAlchemy(model_class=Base) ## Create a SQLAlchemy object and use our Base class for all models.
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
    workout_days: Mapped[int] = mapped_column(Integer,nullable=False)
    equipment: Mapped[str] = mapped_column(Text,nullable=False)

class WorkoutPlan(db.Model):
    __tablename__ = "workout_plans"
    id: Mapped[int] = mapped_column(Integer,primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"),nullable=False)
    created_at = db.Column(db.DateTime,default=datetime.utcnow)
    plan: Mapped[str] = mapped_column(Text,nullable=False)

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

@app.route("/fitness-profile",methods=["GET","POST"])
@login_required
def fitness_profile():
    profile=db.session.execute(db.select(FitnessProfile).where(FitnessProfile.user_id==current_user.id)).scalar_one_or_none()
    if profile:
        fitness_form=FitnessProfileform(obj=profile)
        if profile.equipment:
            fitness_form.equipment.data = profile.equipment.split(",")
    else:
        fitness_form = FitnessProfileform()
    if fitness_form.validate_on_submit():
        if profile:
            # Update existing profile
            profile.dob = fitness_form.dob.data
            profile.height = fitness_form.height.data
            profile.weight = fitness_form.weight.data
            profile.gender = fitness_form.gender.data
            profile.goal = fitness_form.goal.data
            profile.experience = fitness_form.experience.data
            profile.workout_days = int(fitness_form.workout_days.data)
            profile.equipment = ",".join(fitness_form.equipment.data)
        else:
            #create new profile
            fitness_form=FitnessProfileform()
            profile=FitnessProfile(user_id=current_user.id,
                                       dob=fitness_form.dob.data,
                                       height=fitness_form.height.data,
                                       weight=fitness_form.weight.data, 
                                       gender=fitness_form.gender.data, 
                                       goal=fitness_form.goal.data, 
                                       experience=fitness_form.experience.data,
                                       workout_days=int(fitness_form.workout_days.data),
                                       equipment=",".join(fitness_form.equipment.data) )
            db.session.add(profile)
        db.session.commit()
        flash("Fitness profile saved successfully!", "success")
        profile_data = { "dob": profile.dob.strftime("%d-%m-%Y"), 
                    "height": profile.height, 
                    "weight": profile.weight, 
                    "gender": profile.gender,
                    "goal": profile.goal,
                    "experience": profile.experience, 
                    "workout_days": profile.workout_days,
                    "equipment": profile.equipment.split(",") }
        ai_result=generate_fitness_plan(profile=profile_data)
        if ai_result is None:
            flash("We couldn't generate your workout plan right now. Please try again.","danger")
            return redirect(url_for("fitness_profile"))
        workout_plan = WorkoutPlan(user_id=current_user.id,plan=ai_result)
        db.session.add(workout_plan)
        db.session.commit()
        return redirect(url_for('home'))
    return render_template("fitness-profile.html",form=fitness_form)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route("/delete-account",methods=["POST"])
@login_required
def delete_account():
    user_id = current_user.id
    fitness_profile = FitnessProfile.query.filter_by(user_id=user_id).first()
    if fitness_profile:
        db.session.delete(fitness_profile)
    workout_plans = WorkoutPlan.query.filter_by(user_id=user_id).all()
    for plan in workout_plans:
        db.session.delete(plan)
    user = db.session.get(User, user_id)
    if user:
        db.session.delete(user)
    db.session.commit()
    logout_user()
    return redirect(url_for('home'))

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
    "hindu-push-ups": "Hindu Push Ups",
    "military-push-ups": "Military Push Ups",
    "pike-push-ups": "Pike Push Ups",
    "incline-push-ups": "Incline Push Ups",
    "decline-push-ups": "Decline Push Ups",
    "burpees": "Burpees",
    "mountain-climbers": "Mountain Climbers",
    "diamond-push-ups": "Diamond Push Ups",
    "cobra-stretch": "Cobra Stretch",
    "sit-ups": "Sit Ups",
    "bicycle-crunches": "Bicycle Crunches",
    "v-up": "V Ups",
    "russian-twist": "Russian Twists",
    "butt-bridge": "Glute Bridge",
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
    "child-pose": "Child's Pose"
    }
    for workout in workouts:
        workout["exercise"] = exercise_names.get(workout["exercise"],"")

    return render_template("workout.html",day_number=day,workouts=workouts)

@app.route("/")
def home():
    if current_user.is_authenticated:
        workout_plans = db.session.execute(db.select(WorkoutPlan).where(WorkoutPlan.user_id == current_user.id).order_by(WorkoutPlan.created_at.desc())).scalars().all()
        for workout_plan in workout_plans:
            workout_plan.plan = json.loads(workout_plan.plan)
        return render_template("home.html",workout_plans=workout_plans)
    return render_template("dashboard.html")
 
@app.route("/about")
def about():
    return render_template("about.html")

if __name__=="__main__":
    app.run(debug=False, port=5002)