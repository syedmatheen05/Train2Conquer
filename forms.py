from flask_wtf import FlaskForm
from wtforms import EmailField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length

class Loginform(FlaskForm):
    email=EmailField("E-mail",validators=[DataRequired(),Email()])
    submit=SubmitField("verify")

class OTPform(FlaskForm):
    verification_code=StringField("Verification code",
                                      validators=[DataRequired(),
                                                  Length(min=6,max=6,
                                                         message="Verification code must be exactly 6 digits.")])
    submit=SubmitField("Login")

class Registerform(FlaskForm):
    name=StringField("Full Name",validators=[DataRequired()])
    email=EmailField("E-mail",validators=[DataRequired(),Email()])
    submit=SubmitField("verify")

