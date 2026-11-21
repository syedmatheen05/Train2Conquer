from flask_wtf import FlaskForm

import equipment as equipment_module
import validation
from wtforms import BooleanField, EmailField, HiddenField, StringField, SubmitField, SelectField, SelectMultipleField,DateField, IntegerField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional, Regexp, ValidationError
from wtforms.widgets import ListWidget, CheckboxInput
def person_name(form, field):
    """Names must look like names (no digits/symbols). Server-side, whatever the browser did."""
    if not validation.is_valid_name(field.data):
        raise ValidationError(validation.NAME_ERROR)


def unchanged_name_ok(form, field):
    """Admin edits: an existing record's name may stay as it is; only NEW names must be valid."""
    if validation.normalise_name(field.data) == validation.normalise_name(field.object_data):
        return
    person_name(form, field)


NAME_KW = {"data-validate": "person-name", "maxlength": validation.NAME_MAX_LENGTH}
NAME_HINT = "Letters, spaces, hyphens and apostrophes only."


class Loginform(FlaskForm):
    email=EmailField("E-mail",validators=[DataRequired(),Email()], render_kw={"autocomplete": "email"})
    remember=BooleanField("Remember Me", default=False)   # unchecked unless the user ticks it
    submit=SubmitField("verify")

class OTPform(FlaskForm):
    verification_code=StringField("Verification code",
                                      render_kw={"autocomplete": "one-time-code", "inputmode": "numeric", "maxlength": 6},
                                      validators=[DataRequired(),
                                                  Length(min=6,max=6, message="Verification code must be exactly 6 digits."),
                                                      Regexp(r"^\d{6}$", message="Verification code must contain exactly 6 digits.")])
    submit=SubmitField("Verify & Continue")

class Registerform(FlaskForm):
    name=StringField("Full Name", filters=[validation.normalise_name], validators=[DataRequired(message="Please enter your name."), person_name],
                     description=NAME_HINT, render_kw={"autocomplete": "name", **NAME_KW})
    email=EmailField("E-mail",validators=[DataRequired(),Email()], render_kw={"autocomplete": "email"})
    remember=BooleanField("Remember Me", default=False)
    submit=SubmitField("verify")

class FitnessProfileform(FlaskForm):
    dob=DateField("Date of Birth", format="%Y-%m-%d", validators=[DataRequired(message="Please enter your date of birth.")],
                  description=f"You must be at least {validation.MIN_PROFILE_AGE} years old.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The picker can't select a date that is too recent (or in the future). The server re-checks below.
        self.dob.render_kw = {"max": validation.latest_allowed_dob().isoformat(), "min": "1900-01-01",
                              "data-validate": "min-age", "data-min-age": str(validation.MIN_PROFILE_AGE)}

    def validate_dob(self, field):
        problem = validation.dob_problem(field.data)
        if problem:
            raise ValidationError(problem)
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
    equipment = SelectMultipleField("Equipment Available", choices=equipment_module.CHOICES,
                                    description="Pick \"No equipment\" or \"Gym\" on their own, or tick any mix of individual items.",
                                    option_widget=CheckboxInput(),
                                    widget=ListWidget(prefix_label=False))

    def validate_equipment(self, field):
        """Server-side rule (the browser script is only a convenience): None/Gym stand alone."""
        problem = equipment_module.validate_selection(field.data)
        if problem:
            raise ValidationError(problem)

    submit = SubmitField("Save Profile")

class ContactForm(FlaskForm):
    name = StringField(
        "Name",
        filters=[validation.normalise_name],
        render_kw={"autocomplete": "name", **NAME_KW},
        validators=[
            DataRequired(),
            Length(min=2, max=100),
            person_name,
        ]
    )

    email = StringField(
        "Email",
        render_kw={"autocomplete": "email"},
        validators=[
            DataRequired(),
            Email()
        ]
    )

    message = TextAreaField(
        "Message",
        render_kw={"maxlength": 1200},
        validators=[
            DataRequired(),
            Length(min=10, max=1200)
        ]
    )

    # Set when the user came from "Contact Trainer". Both are only HINTS: the
    # server re-loads the trainer from the database and ignores anything invalid.
    trainer_id = HiddenField(validators=[Optional(), Regexp(r"^\d{1,9}$")])
    contact_mode = HiddenField(validators=[Optional(), Regexp(r"^(online|offline)$")])

    submit = SubmitField("Verify Email")

class ContactOTPForm(FlaskForm):
    verification_code=StringField("Verification code",
                                      render_kw={"autocomplete": "one-time-code", "inputmode": "numeric", "maxlength": 6},
                                          validators=[DataRequired(),
                                                      Length(min=6,max=6, message="Verification code must be exactly 6 digits."),
                                                      Regexp(r"^\d{6}$", message="Verification code must contain exactly 6 digits.")])
    submit=SubmitField("Verify")


class TrainerSearchForm(FlaskForm):
    gender = SelectField(
        "Gender",
        choices=[("male", "Male"), ("female", "Female"), ("other", "Other")],
        validators=[DataRequired()],
    )

    # Optional here: online training needs no location. Offline requires it (see validate()).
    location = StringField("Location", validators=[Optional(), Length(max=100)])

    mode = SelectField(
        "Training Mode",
        choices=[("online", "Online"), ("offline", "Offline")],
        validators=[DataRequired()],
    )

    submit = SubmitField("Find Trainer")

    def validate(self, extra_validators=None):
        valid = super().validate(extra_validators)
        if valid and self.mode.data == "offline" and not (self.location.data or "").strip():
            self.location.errors = list(self.location.errors) + ["Please enter your location to find nearby trainers."]
            return False
        return valid

class TrainerForm(FlaskForm):

    about = TextAreaField(
        "About Yourself",
        validators=[
            DataRequired(message="Please tell us something about yourself."),
            Length(min=20, max=1000)
        ],
        render_kw={
            "placeholder": "Tell users about yourself, your experience, training style, etc.",
            "rows": 6
        }
    )

    location = StringField(
        "Location",
        validators=[
            DataRequired(message="Please enter your location."),
            Length(max=100)
        ],
        render_kw={
            "placeholder": "e.g. Bengaluru, Karnataka"
        }
    )

    gender = SelectField(
        "Gender",
        choices=[
            ("", "Select Gender"),
            ("Male", "Male"),
            ("Female", "Female"),
            ("Other", "Other")
        ],
        validators=[
            DataRequired(message="Please select your gender.")
        ]
    )
    phone_number = StringField(
        "Phone Number",
        validators=[
            DataRequired(message="Please enter your phone number."),
            Regexp(
                r"^\d{10}$",
                message="Phone number must contain exactly 10 digits."
            )
        ],
        render_kw={
            "placeholder": "10-digit phone number",
            "maxlength": "10",
            "inputmode": "numeric"
        }
    )

    submit = SubmitField("Become a Trainer")


class AdminTrainerForm(FlaskForm):
    """Used by the admin panel to add/edit rows in the Trainer table.

    Distinct from TrainerForm (the public "become a trainer" application,
    which only emails the site owner and never touches the Trainer table).
    Gender choices match the lowercase values already used elsewhere
    (FitnessProfileform, TrainerSearchForm) so gender filtering stays
    consistent across the app.
    """

    name = StringField(
        "Full Name",
        filters=[validation.normalise_name],
        validators=[DataRequired(), Length(max=100), unchanged_name_ok],
        render_kw={"placeholder": "e.g. Priya Sharma", **NAME_KW},
    )

    gender = SelectField(
        "Gender",
        choices=[("male", "Male"), ("female", "Female"), ("other", "Other")],
        validators=[DataRequired()],
    )

    location = StringField(
        "Location",
        validators=[DataRequired(), Length(max=200)],
        render_kw={"placeholder": "e.g. Koramangala, Bengaluru"},
    )

    about = TextAreaField(
        "About",
        validators=[Length(max=300)],
        render_kw={"placeholder": "Short bio shown to users (optional)", "rows": 4},
    )

    contact_email = EmailField(
        "Trainer's e-mail (optional)",
        validators=[Optional(), Email(), Length(max=200)],
        render_kw={"placeholder": "If set, 'Contact Trainer' messages are e-mailed to this address", "autocomplete": "off"},
    )

    is_active = BooleanField("Active (shown in Find Trainers)")

    submit = SubmitField("Save Trainer")


class AdminUserForm(FlaskForm):
    """Used by the admin panel to add/edit rows in the User table.

    Accounts are passwordless (email OTP login), so there is no password
    field: an admin only manages name, email and the admin flag. Email is
    normalised to lowercase in the route, matching how login/register
    look users up.
    """

    name = StringField(
        "Full Name",
        filters=[validation.normalise_name],
        validators=[DataRequired(), Length(max=100), unchanged_name_ok],
        render_kw={"placeholder": "e.g. Syed Matheen", "autocomplete": "off", **NAME_KW},
    )

    email = EmailField(
        "E-mail",
        validators=[DataRequired(), Email(), Length(max=200)],
        render_kw={"placeholder": "name@example.com", "autocomplete": "off"},
    )

    is_admin = BooleanField("Admin access")
    is_active = BooleanField("Account active (can log in)")

    submit = SubmitField("Save User")
