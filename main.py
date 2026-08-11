from flask import Flask, render_template, redirect, url_for, session, flash
from forms import Loginform, OTPform, Registerform,FitnessProfileform
from flask_bootstrap import Bootstrap5
import random, os, time, resend
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, LoginManager, login_user, logout_user,current_user, login_required
from sqlalchemy import Integer, String, Text
from functools import wraps
from dotenv import load_dotenv

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
# "sqlite:///" means use an SQLite database stored as a local file.
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


def send_verification_code(receiver_email, verification_code):
    params = {
        "from": "onboarding@resend.dev",
        "to": [receiver_email],
        "subject": "Train2Conquer Verification Code",
        "html": f"""
        <h2>Train2Conquer</h2>
        <p>Hello,</p>
        <p>Your Train2Conquer verification code is:</p>
        <h1>{verification_code}</h1>
        <p>This OTP is valid for 5 minutes.</p>
        <p>Do not share this code with anyone.</p>
        <p>Regards,<br>
        Train2Conquer Team</p>"""}
    resend.Emails.send(params)


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
                return redirect(url_for('fitness_form'))
            login_user(user)
            pop_session()
            return render_template("home.html")
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
def fitness_form():
    fitness_form=FitnessProfileform()
    return render_template("fitness-profile.html",form=fitness_form)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route("/")
def home():
    return render_template("dashboard.html")

if __name__=="__main__":
    app.run(debug=False)