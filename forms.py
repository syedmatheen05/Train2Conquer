from flask_wtf import FlaskForm
from wtforms import EmailField, StringField, SubmitField, SelectField, SelectMultipleField,DateField, IntegerField
from wtforms.validators import DataRequired, Email, Length, NumberRange
from wtforms.widgets import ListWidget, CheckboxInput
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

class FitnessProfileform(FlaskForm):
    dob=DateField("Date of Birth",format="%d-%m-%Y",validators=[DataRequired()])
    height=IntegerField("Height (cm)", validators=[DataRequired(),NumberRange(min=100,max=250)])
    weight=IntegerField("Weight (kg)",validators=[DataRequired(),NumberRange(20,200)])
    gender=SelectField("Gender",choices=[("male","Male"),("female","Female"),("other","Other")],validators=[DataRequired()])
    goal=SelectField("Fitness Goal",  choices=[
            ("muscle", "Build Muscle"),
            ("fat_muscle", "Lose Fat and Build Muscle"),
            ("lose_weight", "Lose Weight"),
            ("gain_weight", "Gain Weight"),
            ("strength", "Get Stronger"),
            ("endurance", "Improve Endurance"),
            ("fitness", "Improve Fitness"),
            ("maintain", "Maintain Weight")],validators=[DataRequired()])
    experience = SelectField("Fitness Level",choices=[("beginner", "Beginner"),
                                                      ("intermediate", "Intermediate"),
                                                      ("advanced", "Advanced")],
                                            validators=[DataRequired()])
    workout_days = SelectField("Workout Days Per Week",choices=[("2", "2 days"),
                                                                ("3", "3 days"),
                                                                ("4", "4 days"),
                                                                ("5", "5 days"),
                                                                ("6", "6 days"),
                                                                ("7", "Every day")],
                                                        validators=[DataRequired()])
    equipment = SelectMultipleField("Equipment Available",choices=[("none", "No equipment"),
                                                                   ("dumbbells", "Dumbbells"),
                                                                   ("bands", "Resistance bands"),
                                                                   ("pullup", "Pull-up bar"),
                                                                   ("bench", "Bench"),
                                                                   ("kettlebell", "Kettlebell"),
                                                                   ("barbell", "Barbell")],
                                                        option_widget=CheckboxInput(),
                                                        widget=ListWidget(prefix_label=False))
    submit = SubmitField("Save Profile")

    

