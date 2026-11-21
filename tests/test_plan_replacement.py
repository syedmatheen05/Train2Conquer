"""Editing the fitness profile and generating a plan REPLACES the active plan (requirement 8)."""
import json
from datetime import date

import pytest

import main
import plan_cycle
from conftest import buy, fake_response, login, valid_plan_json
from models import AIUsage, FitnessProfile, Payment, WorkoutPlan, db

FORM = {"dob": "2000-01-01", "height": "170", "weight": "70", "gender": "male", "goal": "muscle",
        "experience": "beginner", "workout_days": "3", "equipment": ["none"]}


@pytest.fixture(autouse=True)
def roomy_daily_cap(monkeypatch):
    monkeypatch.setattr(main, "MAX_BASIC_PER_DAY", 20)


def plans():
    with main.app.app_context():
        return db.session.query(WorkoutPlan).order_by(WorkoutPlan.id).all()


def active_ready():
    return [p for p in plans() if p.status == "ready" and not p.is_archived]


def edit_profile(client, **changes):
    r = client.post("/fitness-profile", data={**FORM, **changes})
    assert r.status_code == 302, r.get_data(as_text=True)[:300]


def new_free_plan(client, fake_gemini, answers=None):
    ai = fake_gemini(answers or [fake_response(valid_plan_json())])
    client.post("/plans/basic")
    main.process_queue()
    return ai


def setup_user_with_plan(client, make_user, fake_gemini):
    uid = make_user()
    login(client, uid)
    edit_profile(client)                       # saves the baseline profile
    new_free_plan(client, fake_gemini)
    return uid


def test_first_plan_records_the_profile_fingerprint(client, make_user, fake_gemini):
    setup_user_with_plan(client, make_user, fake_gemini)
    (plan,) = plans()
    assert plan.week_number == 1 and plan.status == "ready" and not plan.is_archived and plan.profile_hash


def test_editing_the_profile_replaces_the_plan_instead_of_adding_week_2(client, make_user, fake_gemini):
    uid = setup_user_with_plan(client, make_user, fake_gemini)
    old_id = plans()[0].id
    edit_profile(client, weight="82", goal="lose_weight", workout_days="4")
    ai = new_free_plan(client, fake_gemini, [fake_response(valid_plan_json(4))])
    rows = plans()
    new = rows[-1]
    assert [r.week_number for r in rows] == [1, 1]                       # NOT week 2
    assert rows[0].id == old_id and rows[0].is_archived and not new.is_archived and new.status == "ready"
    assert len(active_ready()) == 1 and active_ready()[0].id == new.id   # exactly one active plan
    sent = json.loads(ai.calls[0]["input"])
    assert sent["PREVIOUS_PLAN"] is None                                 # the old profile's plan is NOT fed to Gemini
    assert sent["USER_PROFILE"]["weight"] == 82 and sent["USER_PROFILE"]["workout_days"] == 4


def test_the_old_plan_is_kept_as_history_not_deleted(client, make_user, fake_gemini):
    setup_user_with_plan(client, make_user, fake_gemini)
    old = plans()[0]
    edit_profile(client, weight="90")
    new_free_plan(client, fake_gemini)
    with main.app.app_context():
        kept = db.session.get(WorkoutPlan, old.id)
        assert kept is not None and kept.plan == old.plan and kept.is_archived


def test_a_failed_generation_never_touches_the_existing_plan(client, make_user, fake_gemini):
    setup_user_with_plan(client, make_user, fake_gemini)
    old = plans()[0]
    edit_profile(client, weight="95")
    new_free_plan(client, fake_gemini, [fake_response("not json"), fake_response("still not json")])
    rows = plans()
    assert rows[-1].status == "failed" and rows[-1].error_message == "invalid_output"
    assert rows[0].id == old.id and not rows[0].is_archived and rows[0].status == "ready"
    assert [p.id for p in active_ready()] == [old.id]
    html = client.get("/").get_data(as_text=True)
    assert "still available" in html and "Retry" in html or "Try again" in html     # clear message + a way to retry


def test_gemini_outage_keeps_the_old_plan_and_a_retry_then_replaces_it(client, make_user, fake_gemini):
    from conftest import ApiError
    setup_user_with_plan(client, make_user, fake_gemini)
    old_id = plans()[0].id
    edit_profile(client, weight="77")
    new_free_plan(client, fake_gemini, [ApiError(503)] * 3)
    assert plans()[-1].status == "failed" and [p.id for p in active_ready()] == [old_id]
    failed_id = plans()[-1].id
    fake_gemini([fake_response(valid_plan_json())])
    assert client.post(f"/plans/{failed_id}/retry").status_code == 302
    main.process_queue()
    rows = plans()
    assert rows[-1].status == "ready" and rows[-1].week_number == 1 and rows[0].is_archived
    assert len(active_ready()) == 1


def test_unchanged_profile_still_continues_as_next_week(client, make_user, fake_gemini):
    setup_user_with_plan(client, make_user, fake_gemini)
    with main.app.app_context():
        db.session.query(WorkoutPlan).update({"completed_days": json.dumps(list(range(1, 8)))}); db.session.commit()
    edit_profile(client)                                                  # same values re-saved: not an edit
    ai = new_free_plan(client, fake_gemini)
    rows = plans()
    assert [r.week_number for r in rows] == [1, 2] and not any(r.is_archived for r in rows)
    assert json.loads(ai.calls[0]["input"])["PREVIOUS_PLAN"]             # week 2 DOES see week 1


def test_editing_and_changing_back_is_not_a_change(client, make_user, fake_gemini):
    setup_user_with_plan(client, make_user, fake_gemini)
    edit_profile(client, weight="99"); edit_profile(client, weight="70")
    with main.app.app_context():
        assert main.starts_new_cycle(plans()[0].user_id) is False


@pytest.mark.parametrize("change", [{"weight": "71"}, {"height": "180"}, {"goal": "lose_weight"}, {"experience": "advanced"},
                                    {"workout_days": "5"}, {"equipment": ["gym"]}, {"equipment": ["dumbbells", "bench"]},
                                    {"dob": "1999-05-05"}, {"gender": "female"}])
def test_every_relevant_field_triggers_a_replacement(client, make_user, fake_gemini, change):
    uid = setup_user_with_plan(client, make_user, fake_gemini)
    edit_profile(client, **change)
    with main.app.app_context():
        assert main.starts_new_cycle(uid) is True
    days = int(change.get("workout_days", 3))
    new_free_plan(client, fake_gemini, [fake_response(valid_plan_json(days, equipment_values=change.get("equipment", ["none"])))])
    assert [r.week_number for r in plans()] == [1, 1] and plans()[0].is_archived and len(active_ready()) == 1


def test_editing_several_times_keeps_exactly_one_active_plan(client, make_user, fake_gemini):
    setup_user_with_plan(client, make_user, fake_gemini)
    for weight in ("75", "80", "85"):
        edit_profile(client, weight=weight)
        new_free_plan(client, fake_gemini)
    rows = plans()
    assert len(rows) == 4 and [r.is_archived for r in rows] == [True, True, True, False] and len(active_ready()) == 1


def test_profile_edited_while_generating_makes_the_next_plan_a_replacement(client, make_user, fake_gemini, monkeypatch):
    uid = make_user(); login(client, uid); edit_profile(client)
    client.post("/plans/basic")
    ai = fake_gemini([fake_response(valid_plan_json())])
    real = ai.models.generate_content

    def edit_during_generation(model, contents, config=None):
        with main.app.app_context():
            db.session.query(FitnessProfile).update({"weight": 120}); db.session.commit()
        return real(model, contents, config)

    ai.models.generate_content = edit_during_generation
    main.process_queue()
    assert plans()[0].status == "ready"
    with main.app.app_context():
        assert main.starts_new_cycle(uid) is True                       # the in-flight edit is not lost


def test_home_never_shows_an_archived_plan(client, make_user, fake_gemini):
    uid = setup_user_with_plan(client, make_user, fake_gemini)
    old_id = plans()[0].id
    edit_profile(client, weight="88")
    ai = new_free_plan(client, fake_gemini)
    new_id = plans()[-1].id
    html = client.get(f"/?plan={old_id}").get_data(as_text=True)         # trying to open the archived plan by id
    with main.app.app_context():
        assert main.current_plan_row(uid).id == new_id
    assert f"plan={old_id}" not in html


def test_notice_appears_after_an_edit_and_free_plan_unlocks_mid_week(client, make_user, fake_gemini):
    setup_user_with_plan(client, make_user, fake_gemini)
    quiet = client.get("/").get_data(as_text=True)
    assert "will <strong>replace</strong>" not in quiet
    edit_profile(client, weight="66")
    loud = client.get("/").get_data(as_text=True)
    assert "will <strong>replace</strong>" in loud and "Generate Basic Plan" in loud.replace("GENERATE", "Generate") or "Basic" in loud
    client.post("/plans/basic")                                          # no 7 completed days needed after an edit
    assert plans()[-1].status == "generating"


# ---- paid plans ----
def test_personalized_plan_replaces_after_profile_edit(client, gateway, make_user, fake_gemini):
    uid = setup_user_with_plan(client, make_user, fake_gemini)
    edit_profile(client, goal="lose_weight", equipment=["dumbbells"])
    buy(client, gateway, uid, "personalized")
    ai = fake_gemini([fake_response(valid_plan_json(equipment_values=["dumbbells"]))])
    main.process_queue()
    rows = plans()
    assert rows[-1].tier == "personalized" and rows[-1].week_number == 1 and rows[0].is_archived and len(active_ready()) == 1
    sent = json.loads(ai.calls[0]["input"])
    assert sent["LAST_WEEK"] is None and sent["USER_PROFILE"]["equipment"] == ["dumbbells"] and sent["USER_PROFILE"]["goal"] == "lose_weight"
    with main.app.app_context():
        assert db.session.query(Payment).one().status == "used"


def test_advanced_plan_replaces_with_empty_history(client, gateway, make_user, fake_gemini):
    uid = setup_user_with_plan(client, make_user, fake_gemini)
    edit_profile(client, experience="advanced", workout_days="5")
    buy(client, gateway, uid, "advanced")
    wrapped = json.dumps({"plan": json.loads(valid_plan_json(5)), "coach_notes": "Fresh start for your new goals."})
    ai = fake_gemini([fake_response(wrapped)])
    main.process_queue()
    rows = plans()
    assert rows[-1].tier == "advanced" and rows[-1].week_number == 1 and rows[0].is_archived
    assert json.loads(ai.calls[0]["input"])["HISTORY"] == [] and rows[-1].coach_notes == "Fresh start for your new goals."


def test_failed_paid_replacement_keeps_the_old_plan_and_the_payment(client, gateway, make_user, fake_gemini):
    uid = setup_user_with_plan(client, make_user, fake_gemini)
    old_id = plans()[0].id
    edit_profile(client, weight="101")
    buy(client, gateway, uid, "personalized")
    fake_gemini([fake_response("bad"), fake_response("bad")])
    main.process_queue()
    assert plans()[-1].status == "failed" and [p.id for p in active_ready()] == [old_id]
    with main.app.app_context():
        assert db.session.query(Payment).one().status == "paid"          # not consumed: retry costs nothing


# ---- legacy data / migration ----
def test_plans_without_a_fingerprint_keep_the_old_weekly_behaviour(client, make_user, fake_gemini):
    uid = setup_user_with_plan(client, make_user, fake_gemini)
    with main.app.app_context():
        db.session.query(WorkoutPlan).update({"profile_hash": None}); db.session.commit()
    edit_profile(client, weight="64")
    with main.app.app_context():
        assert main.starts_new_cycle(uid) is False


def test_migration_gives_existing_plans_the_current_profile_fingerprint(client, make_user, fake_gemini):
    import migrations
    uid = setup_user_with_plan(client, make_user, fake_gemini)
    with main.app.app_context():
        db.session.query(WorkoutPlan).update({"profile_hash": None}); db.session.commit()
        with db.engine.begin() as conn:
            migrations.m008_plan_replacement(conn)
            migrations.m008_plan_replacement(conn)                         # safe to run twice
        expected = plan_cycle.fingerprint(db.session.query(FitnessProfile).one())
        assert db.session.query(WorkoutPlan).one().profile_hash == expected
        assert main.starts_new_cycle(uid) is False
    edit_profile(client, weight="59")
    with main.app.app_context():
        assert main.starts_new_cycle(uid) is True


def test_fingerprint_ignores_automatic_progression_score(make_user):
    uid = make_user()
    with main.app.app_context():
        profile = db.session.query(FitnessProfile).one()
        before = plan_cycle.fingerprint(profile)
        profile.progression_score += 5
        assert plan_cycle.fingerprint(profile) == before
