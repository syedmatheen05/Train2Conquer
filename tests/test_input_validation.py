"""Registration names (requirement 7) and fitness-profile minimum age (requirement 10)."""
from datetime import date

import pytest

import main
import validation
from conftest import login, valid_plan_json
from models import FitnessProfile, User, db

VALID_NAMES = ["Matheen", "Syed Matheen", "John Smith", "Abdul-Rahman", "O'Connor", "Mary Jane", "José", "St. John", "李雷"]
INVALID_NAMES = ["123456", "9876543210", "123 John", "999 Matheen", "123@456", "@#$%", "", "   ", "A", "-", "John3", "Mary_Jane", "x" * 61]


@pytest.mark.parametrize("name", VALID_NAMES)
def test_valid_names_are_accepted(name):
    assert validation.is_valid_name(name)


@pytest.mark.parametrize("name", INVALID_NAMES)
def test_invalid_names_are_rejected(name):
    assert not validation.is_valid_name(name)


def test_names_are_trimmed():
    assert validation.normalise_name("  Syed   Matheen ") == "Syed Matheen"


# ---- registration: the SERVER rejects bad names even when the browser is bypassed ----
@pytest.mark.parametrize("name", INVALID_NAMES)
def test_registration_rejects_bad_names_on_the_server(client, sent_codes, name):
    r = client.post("/register", data={"name": name, "email": "new@example.com"})
    assert r.status_code == 200 and sent_codes == []                       # form shown again, no code e-mailed
    assert validation.NAME_ERROR.encode() in r.data or b"Please enter your name" in r.data
    with main.app.app_context():
        assert db.session.query(User).count() == 0


@pytest.mark.parametrize("name", ["Syed Matheen", "Abdul-Rahman", "O'Connor"])
def test_registration_accepts_real_names(client, sent_codes, name):
    r = client.post("/register", data={"name": name, "email": "new@example.com"})
    assert r.status_code == 302 and len(sent_codes) == 1


def test_registration_stores_a_trimmed_name(client, sent_codes):
    client.post("/register", data={"name": "  Syed    Matheen  ", "email": "new@example.com"})
    client.post("/verify-otp", data={"verification_code": sent_codes[-1][1]})
    with main.app.app_context():
        assert db.session.query(User).one().name == "Syed Matheen"


def test_existing_users_with_old_style_names_can_still_log_in(client, make_user, sent_codes):
    make_user(name="12345", email="old@example.com", profile=False)
    assert client.post("/login", data={"email": "old@example.com"}).status_code == 302 and len(sent_codes) == 1


def test_register_page_shows_the_hint_and_the_script(client):
    html = client.get("/register").get_data(as_text=True)
    assert 'data-validate="person-name"' in html and "validation.js" in html and "Letters, spaces, hyphens" in html


# ---- admin forms: new names validated, an unchanged legacy name does not block other edits ----
def test_admin_cannot_create_a_numeric_name(client, make_user):
    login(client, make_user(email="boss@example.com", admin=True, profile=False))
    r = client.post("/admin/users/new", data={"name": "12345", "email": "x@example.com", "is_active": "y"})
    assert r.status_code == 200
    with main.app.app_context():
        assert db.session.query(User).filter_by(email="x@example.com").count() == 0


def test_admin_can_edit_other_fields_of_a_legacy_name(client, make_user):
    login(client, make_user(email="boss@example.com", admin=True, profile=False))
    legacy = make_user(name="12345", email="legacy@example.com", profile=False)
    r = client.post(f"/admin/users/{legacy}/edit", data={"name": "12345", "email": "legacy2@example.com", "is_active": "y"})
    assert r.status_code == 302


# ---- fitness profile: minimum age 15 ----
@pytest.fixture
def today_is_20_nov_2026(monkeypatch):
    monkeypatch.setattr(validation, "today", lambda: date(2026, 11, 20))


PROFILE = {"height": "170", "weight": "70", "gender": "male", "goal": "muscle", "experience": "beginner",
           "workout_days": "3", "equipment": ["none"]}


def save(client, dob):
    return client.post("/fitness-profile", data={**PROFILE, "dob": dob})


@pytest.mark.parametrize("dob,ok", [
    ("2011-11-20", True),      # exactly 15 today
    ("2011-11-19", True),      # 15 and one day
    ("2011-11-21", False),     # 14 years 11 months 30 days
    ("2012-02-29", False),     # leap-day birthday, still 14
    ("2012-11-20", False),     # 14
    ("2010-01-01", True),      # 16
    ("2000-05-05", True),      # 26
    ("2026-11-21", False),     # tomorrow = future
    ("2099-01-01", False),     # far future
])
def test_minimum_age_boundaries(client, make_user, today_is_20_nov_2026, dob, ok):
    uid = make_user(profile=False)
    login(client, uid)
    r = save(client, dob)
    assert (r.status_code == 302) is ok
    with main.app.app_context():
        assert (db.session.query(FitnessProfile).count() == 1) is ok
    if not ok:
        assert b"at least 15 years old" in r.data or b"in the future" in r.data


def test_invalid_date_formats_are_rejected(client, make_user):
    login(client, make_user(profile=False))
    for bad in ("not-a-date", "2000-13-40", "01/01/2000", ""):
        assert save(client, bad).status_code == 200


def test_underage_edit_cannot_overwrite_a_valid_profile(client, make_user, today_is_20_nov_2026):
    uid = make_user(profile=True)                               # dob 2000-01-01
    login(client, uid)
    assert save(client, "2015-01-01").status_code == 200
    with main.app.app_context():
        assert db.session.query(FitnessProfile).one().dob == date(2000, 1, 1)


def test_existing_profile_with_old_enough_dob_can_still_be_edited(client, make_user):
    login(client, make_user(profile=True))
    assert save(client, "2000-01-01").status_code == 302


def test_profile_with_a_stored_underage_dob_loads_without_crashing(client, make_user):
    uid = make_user(profile=True)
    with main.app.app_context():
        db.session.query(FitnessProfile).update({"dob": date(2020, 1, 1)}); db.session.commit()
    login(client, uid)
    assert client.get("/fitness-profile").status_code == 200 and client.get("/").status_code == 200


def test_date_picker_has_a_max_and_script_hooks(client, make_user):
    login(client, make_user(profile=False))
    html = client.get("/fitness-profile").get_data(as_text=True)
    assert 'data-validate="min-age"' in html and 'data-min-age="15"' in html and "max=" in html and "validation.js" in html
