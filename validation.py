"""Input rules shared by the forms (no Flask here, so they are easy to test).

  * person names   - registration, contact, trainer and admin forms
  * date of birth  - fitness profile minimum age

The browser scripts (static/js/validation.js) mirror these rules for instant
feedback, but this module is what actually protects the server: it runs for every
request, including ones that skip the web page entirely.
"""
import re
import unicodedata
from datetime import date

NAME_MIN_LETTERS = 2
NAME_MAX_LENGTH = 60
NAME_ERROR = "Please enter a valid name using letters only."
MIN_PROFILE_AGE = 15
AGE_ERROR = f"You must be at least {MIN_PROFILE_AGE} years old to create a fitness profile."
FUTURE_ERROR = "Date of birth can't be in the future."

# Besides letters (any language) and combining accents, a name may contain
# single spaces, hyphens (Abdul-Rahman), apostrophes (O'Connor) and full stops (St. John).
_ALLOWED_PUNCTUATION = " -'\u2019."


def normalise_name(value):
    """Trim and collapse repeated spaces. Non-strings pass through untouched."""
    if not isinstance(value, str):
        return value
    return re.sub(r"\s+", " ", value).strip()


def is_valid_name(value):
    """True for normal human names. Digits, symbols and punctuation-only input are rejected."""
    name = normalise_name(value)
    if not isinstance(name, str) or not name or len(name) > NAME_MAX_LENGTH:
        return False
    letters = 0
    for char in name:
        category = unicodedata.category(char)
        if category[0] == "L":
            letters += 1
        elif category[0] == "M" or char in _ALLOWED_PUNCTUATION:
            continue
        else:                                   # digits, @#$%, emoji, brackets, ...
            return False
    if letters < NAME_MIN_LETTERS:
        return False
    # punctuation must sit between letters/spaces sensibly: no leading hyphen/apostrophe, no "--" runs
    if name[0] in "-'\u2019." or re.search(r"[-'\u2019.]{2,}", name.replace("..", "")):
        return False
    return True


def today():
    """Today's date (one place to change in tests)."""
    return date.today()


def age_on(born, today):
    """Whole years completed on `today` (a birthday that hasn't happened yet this year doesn't count)."""
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def latest_allowed_dob(today=None):
    """The newest date of birth that is still old enough (used for the date picker's max)."""
    today = today or globals()["today"]()
    try:
        return today.replace(year=today.year - MIN_PROFILE_AGE)
    except ValueError:                          # today is 29 Feb and the target year has none
        return today.replace(year=today.year - MIN_PROFILE_AGE, day=28)


def dob_problem(born, today=None):
    """A user-safe message if this date of birth can't be used for a fitness profile, else None."""
    today = today or globals()["today"]()
    if not isinstance(born, date):
        return "Please enter a valid date of birth."
    if born > today:
        return FUTURE_ERROR
    if age_on(born, today) < MIN_PROFILE_AGE:
        return AGE_ERROR
    return None
