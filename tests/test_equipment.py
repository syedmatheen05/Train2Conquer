"""Equipment selection: No equipment / Gym / individual items.

Covers the form rules, saving and reloading, editing an existing profile, and
what the AI is told and allowed to produce for each configuration.
"""
import json
import re

import pytest

import ai as ai_module
import equipment
import main
import plan_generator
from conftest import fake_response, login, valid_plan_json
from models import FitnessProfile, WorkoutPlan, db

PROFILE_FORM = {"dob": "2000-01-01", "height": "170", "weight": "70", "gender": "male", "goal": "muscle",
                "experience": "beginner", "workout_days": "3"}


def save_profile(client, equipment_values):
    return client.post("/fitness-profile", data={**PROFILE_FORM, "equipment": equipment_values})


def stored_equipment(user_id):
    with main.app.app_context():
        return db.session.scalar(db.select(FitnessProfile.equipment).where(FitnessProfile.user_id == user_id))


def checked_boxes(html):
    """Values of the equipment checkboxes that are ticked in a rendered page."""
    boxes = re.findall(r'<input[^>]*name="equipment"[^>]*>', html)
    return sorted(re.search(r'value="([^"]+)"', b).group(1) for b in boxes if " checked" in b)


# ------------------------------------------------------------- pure rules
@pytest.mark.parametrize("values", [["none"], ["gym"], ["dumbbells"], ["dumbbells", "bands", "bench"],
                                     ["pullup", "barbell", "kettlebell"]])
def test_valid_combinations_are_accepted(values):
    assert equipment.validate_selection(values) is None


@pytest.mark.parametrize("values", [["none", "dumbbells"], ["gym", "barbell"], ["none", "gym"],
                                     ["none", "gym", "bands"], []])
def test_invalid_combinations_are_rejected(values):
    assert equipment.validate_selection(values)


def test_legacy_blank_value_means_no_equipment():
    assert equipment.parse_stored("") == ["none"] and equipment.parse_stored(None) == ["none"]
    assert equipment.parse_stored("dumbbells, bench") == ["dumbbells", "bench"]    # old data with spaces
    assert equipment.parse_stored("dumbbells,notreal") == ["dumbbells"]            # unknown words dropped


def test_every_tagged_exercise_exists_in_the_catalogue():
    assert set(equipment.EXERCISE_EQUIPMENT) <= set(ai_module.exercise_keys)


def test_exercise_availability_per_mode():
    keys = ai_module.exercise_keys
    none_keys = set(equipment.allowed_exercise_keys(keys, ["none"]))
    gym_keys = set(equipment.allowed_exercise_keys(keys, ["gym"]))
    assert "push-ups" in none_keys and "squats" in none_keys
    assert not none_keys & set(equipment.EXERCISE_EQUIPMENT)           # nothing equipment-based
    assert gym_keys == set(keys)                                       # the whole catalogue
    custom = set(equipment.allowed_exercise_keys(keys, ["dumbbells", "pullup"]))
    assert "dumbell-bicep-curls" in custom and "pull-ups" in custom
    assert "barbell-back-squat" not in custom and "skipping" not in custom
    assert none_keys < custom < gym_keys


# ------------------------------------------------------- form + database
def test_gym_is_a_choice_on_the_form(client, make_user):
    login(client, make_user(profile=False))
    html = client.get("/fitness-profile").get_data(as_text=True)
    assert 'value="gym"' in html and 'value="none"' in html and 'value="dumbbells"' in html


@pytest.mark.parametrize("sent,stored", [(["gym"], "gym"), (["none"], "none"),
                                         (["dumbbells", "bench"], "dumbbells,bench")])
def test_selection_is_saved_and_reloaded(client, make_user, sent, stored):
    uid = make_user(profile=False)
    login(client, uid)
    assert save_profile(client, sent).status_code == 302
    assert stored_equipment(uid) == stored
    assert checked_boxes(client.get("/fitness-profile").get_data(as_text=True)) == sorted(stored.split(","))


@pytest.mark.parametrize("sent", [["none", "dumbbells"], ["gym", "barbell"], ["none", "gym"], []])
def test_server_rejects_conflicting_or_empty_selection(client, make_user, sent):
    uid = make_user(profile=False)
    login(client, uid)
    r = save_profile(client, sent)
    assert r.status_code == 200                                   # form shown again with the error
    assert stored_equipment(uid) is None                          # nothing was saved


def test_unknown_equipment_value_is_rejected(client, make_user):
    uid = make_user(profile=False)
    login(client, uid)
    assert save_profile(client, ["hovercraft"]).status_code == 200 and stored_equipment(uid) is None


def test_editing_an_existing_profile_really_changes_the_equipment(client, make_user):
    """Regression: the old code overwrote the submitted equipment with the saved one on every POST."""
    uid = make_user(profile=True)                                 # saved as "none"
    login(client, uid)
    assert stored_equipment(uid) == "none"
    assert save_profile(client, ["dumbbells", "bands"]).status_code == 302
    assert stored_equipment(uid) == "dumbbells,bands"
    assert save_profile(client, ["gym"]).status_code == 302
    assert stored_equipment(uid) == "gym"
    assert save_profile(client, ["none"]).status_code == 302
    assert stored_equipment(uid) == "none"


def test_a_failed_edit_keeps_what_the_user_just_ticked(client, make_user):
    uid = make_user(profile=True)
    login(client, uid)
    r = client.post("/fitness-profile", data={**PROFILE_FORM, "height": "5", "equipment": ["dumbbells", "bench"]})
    assert r.status_code == 200 and checked_boxes(r.get_data(as_text=True)) == ["bench", "dumbbells"]
    assert stored_equipment(uid) == "none"                        # invalid form: nothing saved


def test_old_profile_with_blank_equipment_still_loads_and_generates(client, make_user):
    uid = make_user(profile=True)
    with main.app.app_context():
        db.session.query(FitnessProfile).update({"equipment": ""})
        db.session.commit()
    login(client, uid)
    assert client.get("/fitness-profile").status_code == 200
    with main.app.app_context():
        assert main.profile_to_dict(main.get_profile(uid))["equipment"] == ["none"]


# --------------------------------------------------------- AI integration
def profile_with(values):
    return {"dob": "01-01-2000", "height": 170, "weight": 70, "gender": "male", "goal": "muscle",
            "experience": "beginner", "progression_score": 1, "workout_days": 3, "equipment": values}


def basic_call(fake_gemini, values):
    """Runs the real generator for a Basic plan; returns (system instructions, exercise keys sent to Gemini)."""
    ai = fake_gemini([fake_response(valid_plan_json())])
    plan_generator.generate_plan("basic", profile_with(values), None, 1, None,
                                 validator=lambda raw: raw, record=lambda **fields: None)
    call = ai.calls[0]
    return call["instructions"], json.loads(call["input"])["AVAILABLE_EXERCISE_KEYS"]


def test_no_equipment_prompt_is_bodyweight_only(fake_gemini):
    rules, keys = basic_call(fake_gemini, ["none"])
    assert "bodyweight exercises ONLY" in rules
    assert "push-ups" in keys and "dumbell-bicep-curls" not in keys
    assert "barbell-back-squat" not in keys and "pull-ups" not in keys and "skipping" not in keys


def test_gym_prompt_assumes_a_standard_gym_and_offers_everything(fake_gemini):
    rules, keys = basic_call(fake_gemini, ["gym"])
    assert "STANDARD GYM" in rules
    assert "barbell-back-squat" in keys and "dumbell-bicep-curls" in keys and "pull-ups" in keys


def test_individual_equipment_prompt_lists_only_what_was_selected(fake_gemini):
    rules, keys = basic_call(fake_gemini, ["dumbbells", "bench"])
    assert "ONLY this equipment: dumbbells, bench" in rules
    assert "dumbell-bicep-curls" in keys and "barbell-back-squat" not in keys and "pull-ups" not in keys


def test_the_old_pull_up_exception_is_gone():
    assert "even when the user selected" not in ai_module.main_prompt


def test_paid_prompt_and_key_list_follow_the_equipment(client, gateway, make_user, fake_gemini):
    from conftest import buy
    uid = make_user()
    with main.app.app_context():
        db.session.query(FitnessProfile).update({"equipment": "barbell"})
        db.session.commit()
    buy(client, gateway, uid)
    ai = fake_gemini([fake_response(valid_plan_json(equipment_values=["barbell"]))])
    main.process_queue()
    sent = json.loads(ai.calls[0]["input"])
    assert sent["USER_PROFILE"]["equipment"] == ["barbell"]
    assert "barbell-back-squat" in sent["AVAILABLE_EXERCISE_KEYS"]
    assert "dumbell-bicep-curls" not in sent["AVAILABLE_EXERCISE_KEYS"]
    assert "ONLY this equipment: barbell" in ai.calls[0]["instructions"]


# ------------------------------------------- server-side enforcement of the plan
def plan_with_exercise(key, workout_days=3):
    plan = json.loads(valid_plan_json(workout_days))
    plan["day_1"][1]["exercise"] = key
    return json.dumps(plan)


def test_plan_using_unavailable_equipment_is_rejected():
    with pytest.raises(ValueError, match="equipment"):
        main.validate_and_serialize_plan(plan_with_exercise("barbell-back-squat"), 3, ["none"])
    with pytest.raises(ValueError, match="equipment"):
        main.validate_and_serialize_plan(plan_with_exercise("pull-ups"), 3, ["dumbbells"])
    with pytest.raises(ValueError, match="equipment"):
        main.validate_and_serialize_plan(plan_with_exercise("skipping"), 3, ["dumbbells", "barbell"])   # needs a rope


def test_plan_using_available_equipment_is_accepted():
    assert main.validate_and_serialize_plan(plan_with_exercise("barbell-back-squat"), 3, ["gym"])
    assert main.validate_and_serialize_plan(plan_with_exercise("dumbell-bicep-curls"), 3, ["dumbbells", "bench"])
    assert main.validate_and_serialize_plan(plan_with_exercise("push-ups"), 3, ["none"])        # bodyweight is always fine


def test_free_plan_with_forbidden_equipment_fails_and_is_not_saved_as_ready(client, make_user, fake_gemini):
    uid = make_user()                                             # equipment "none"
    login(client, uid)
    fake_gemini([fake_response(plan_with_exercise("barbell-back-squat"))] * 2)
    client.post("/plans/basic")
    main.process_queue()
    with main.app.app_context():
        plan = db.session.query(WorkoutPlan).one()
        assert plan.status == "failed" and plan.error_message == "invalid_output"


def test_paid_plan_with_forbidden_equipment_is_retried_with_the_reason(client, gateway, make_user, fake_gemini):
    from conftest import buy
    buy(client, gateway, make_user())
    ai = fake_gemini([fake_response(plan_with_exercise("barbell-back-squat")),     # rejected...
                      fake_response(valid_plan_json())])                           # ...repaired
    main.process_queue()
    assert len(ai.calls) == 2 and "equipment" in ai.calls[1]["input"]
    with main.app.app_context():
        assert db.session.query(WorkoutPlan).one().status == "ready"
