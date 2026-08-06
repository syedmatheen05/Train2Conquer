from flask import Flask, render_template, redirect, url_for, session, flash
from forms import Loginform, OTPform, Registerform
from flask_bootstrap import Bootstrap5
import random, smtplib, os, time
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, login_manager, login_user, logout_user
from sqlalchemy import Integer, String, Text

# Creating flak object
app=Flask(__name__)
#that Flask-WTF needs a secret key to generate and verify the CSRF token.
app.config["SECRET_KEY"]="train2conquerbysyedmatheenandteam"
bootstrap=Bootstrap5(app)

class Base(DeclarativeBase):# Create a base class for all database models.A database model is a Python class that defines the structure of a database table. Each attribute in the class becomes a column in the table.
    pass

# Configure the database connection for SQLAlchemy.
# "SQLALCHEMY_DATABASE_URI" tells Flask which database to use.
# "sqlite:///" means use an SQLite database stored as a local file.
# "train2conquer.db" is the database file that will be created in the project's instance folder (or configured location).
app.config["SQLALCHEMY_DATABASE_URI"]="sqlite:///train2conquer.db" 
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


my_email=os.environ.get("EMAIL")
password=os.environ.get("PASSWORD")


def send_verification_code(receiver_email,verification_code):
    with smtplib.SMTP("smtp.gmail.com",port=587) as connection:
        connection.starttls()
        connection.login(user=my_email, password=password)
        connection.sendmail(from_addr=my_email,to_addrs=receiver_email,
                            msg=f"Subject:Train2Conquer Verification Code\n\n Hello Your Train2Conquer verification code is:{verification_code}\nThis OTP is valid for 5 minutes.\nDo not share this code with anyone.\nRegards,\nTrain2Conquer Team")

def generate_otp(email):
        otp=random.randint(100000,999999)
        session["email"]=email
        session["otp"]=str(otp)
        send_verification_code(email,otp)
        session["otp_created"]=time.time()

@app.route("/login", methods = ["GET", "POST"])
def login():
    login_form = Loginform()
    if login_form.validate_on_submit():
        email = login_form.email.data
        existing_user=db.session.execute(db.select(User).where(User.email==email)).scalar()
        if not existing_user:
            flash("You don't have an account. Please register first.", "warning")
            return redirect(url_for('register'))
        generate_otp(email)
        return redirect(url_for('verify_otp'))
    return render_template("login.html",form=login_form)


@app.route("/verify-otp",methods=["GET","POST"])
def verify_otp():
    verify_otp_form=OTPform()
    remaining=max(0,60-int(time.time()-session["otp_created"]))
    if verify_otp_form.validate_on_submit():
        if verify_otp_form.verification_code.data==session.get("otp"):
            flash("OTP verified successfully!", "success")
            session.pop("otp",None) # if otp does not exist it will return None, else KeyError.
            session.pop("otp_created", None)
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
        existing_user=db.session.execute(db.select(User).where(User.email==email)).scalar()
        if existing_user:
            flash("You Already have an account","warning")
            return redirect(url_for('login'))
        new_user=User(name=register_form.name.data,email=email)
        db.session.add(new_user)
        db.session.commit()
        generate_otp(email)
        return redirect(url_for('verify_otp'))
    return render_template("register.html",form=register_form)



@app.route("/")
def home():
    return render_template("header.html")


if __name__=="__main__":
    app.run(debug=True)