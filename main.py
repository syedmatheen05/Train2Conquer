from flask import Flask, render_template, redirect, url_for,session
from forms import Loginform, OTPform
from flask_bootstrap import Bootstrap5
from email.message import EmailMessage #i t as an empty email that I fill in before sending.Imagine writing an email manually
import random
import smtplib
# Creating flak object
app=Flask(__name__)
#that Flask-WTF needs a secret key to generate and verify the CSRF token.
app.config["SECRET_KEY"]="train2conquerbysyedmatheenandteam"
bootstrap=Bootstrap5(app)

my_email="syedmatheen2910@gmail.com"
password="lsjkwzwzgpvhtsno"

def send_verification_code(reciever_email,verification_code):
    with smtplib.SMTP("smtp.gmail.com") as connection:
        connection.starttls()
        connection.login(user=my_email, password=password)
        connection.sendmail(from_addr=my_email,to_addrs=reciever_email,
                            msg=f"Subject:Train2Conquer Verification Code\n\n Hello Your Train2Conquer verification code is:{verification_code}\nThis OTP is valid for 5 minutes.\nDo not share this code with anyone.\nRegards,\nTrain2Conquer Team")


@app.route("/login", methods = ["GET", "POST"])
def login():
    login_form = Loginform()
    if login_form.validate_on_submit():
        email = login_form.email.data
        otp=random.randint(100000,999999)
        session["email"]=email
        session["otp"]=str(otp)
        send_verification_code(email,otp)
        return redirect(url_for('verify_otp'))
    return render_template("login.html",form=login_form)


@app.route("/verify-otp",methods=["GET","POST"])
def verify_otp():
    verify_otp_form=OTPform()
    if verify_otp_form.validate_on_submit():
        if verify_otp_form.verification_code.data==session.get("otp"):
            return render_template("index.html")
    return render_template("verify_otp.html",form=verify_otp_form)

@app.route("/resend-otp")
def resend_otp():
    email=session.get("email")
    otp = random.randint(100000, 999999)
    session["otp"]=str(otp)
    send_verification_code(email,otp)
    return redirect(url_for('verify_otp'))


@app.route("/register")
def register():
    return render_template("register.html")




@app.route("/")
def home():
    return render_template("header.html")



if __name__=="__main__":
    app.run(debug=True)