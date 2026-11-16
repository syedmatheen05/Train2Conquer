"""Trainer discovery (online + offline) and the pre-filled contact message."""
import pytest

import main
from conftest import login
from services import trainers as trainer_service

NEAR = dict(lat=12.9716, lon=77.5946)           # the 'search point'
CLOSE = dict(lat=12.9800, lon=77.6000)          # ~1 km away
FAR = dict(lat=13.1986, lon=77.7066)            # ~28 km away (Bengaluru airport side)


@pytest.fixture
def geocode(monkeypatch):
    calls = []
    def fake(location):
        calls.append(location)
        return (NEAR["lat"], NEAR["lon"])
    monkeypatch.setattr(trainer_service, "get_coordinates", fake)
    return calls


@pytest.fixture
def no_geocoding(monkeypatch):
    def boom(location):
        raise AssertionError("Online mode must not geocode")
    monkeypatch.setattr(trainer_service, "get_coordinates", boom)


def signed_in(client, make_user, gender="female", profile=True):
    uid = make_user(profile=profile)
    if profile:
        with main.app.app_context():
            main.db.session.query(main.FitnessProfile).filter_by(user_id=uid).update({"gender": gender})
            main.db.session.commit()
    login(client, uid)
    return uid


# ------------------------------------------------------------ ONLINE MODE
def test_online_search_without_location_shows_trainers_everywhere(client, make_user, make_trainer, no_geocoding):
    """Regression: online + empty location used to bounce back to the same page."""
    make_trainer(name="Near Nina", **CLOSE)
    make_trainer(name="Far Farah", location="Devanahalli", **FAR)
    signed_in(client, make_user)
    r = client.post("/find-trainer", data={"mode": "online", "location": ""})
    assert r.status_code == 302 and "mode=online" in r.headers["Location"]
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Near Nina" in page and "Far Farah" in page            # no radius applied


def test_online_works_for_users_without_a_profile_and_uses_form_gender(client, make_user, make_trainer, no_geocoding):
    make_trainer(name="Wanted Wendy", gender="female")
    make_trainer(name="Other Omar", gender="male")
    signed_in(client, make_user, profile=False)
    r = client.post("/find-trainer", data={"mode": "online", "gender": "female"})
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Wanted Wendy" in page and "Other Omar" not in page


def test_profile_gender_cannot_be_overridden_from_the_url(client, make_user, make_trainer, no_geocoding):
    make_trainer(name="Wanted Wendy", gender="female")
    make_trainer(name="Other Omar", gender="male")
    signed_in(client, make_user, gender="female")
    page = client.get("/find-trainer?mode=online&gender=male").get_data(as_text=True)
    assert "Wanted Wendy" in page and "Other Omar" not in page


def test_inactive_trainers_are_hidden(client, make_user, make_trainer, no_geocoding):
    make_trainer(name="Active Anu")
    make_trainer(name="Suspended Sam", active=False)
    signed_in(client, make_user)
    page = client.get("/find-trainer?mode=online").get_data(as_text=True)
    assert "Active Anu" in page and "Suspended Sam" not in page


def test_online_results_are_paginated(client, make_user, make_trainer, no_geocoding):
    for i in range(trainer_service.ONLINE_PAGE_SIZE + 3):
        make_trainer(name=f"Trainer {i:02d}")
    signed_in(client, make_user)
    first = client.get("/find-trainer?mode=online").get_data(as_text=True)
    second = client.get("/find-trainer?mode=online&page=2").get_data(as_text=True)
    assert "Trainer 00" in first and "Trainer 14" not in first and "Next" in first
    assert "Trainer 14" in second and "Trainer 00" not in second and "Previous" in second


def test_online_empty_result_is_friendly(client, make_user, no_geocoding):
    signed_in(client, make_user)
    r = client.get("/find-trainer?mode=online")
    assert r.status_code == 200 and "No Trainer Found" in r.get_data(as_text=True)


# ----------------------------------------------------------- OFFLINE MODE
def test_offline_keeps_the_5km_radius(client, make_user, make_trainer, geocode):
    make_trainer(name="Close Chitra", **CLOSE)
    make_trainer(name="Far Farah", location="Devanahalli", **FAR)
    signed_in(client, make_user)
    r = client.post("/find-trainer", data={"mode": "offline", "location": "Indiranagar"})
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Close Chitra" in page and "Far Farah" not in page
    assert geocode == ["Indiranagar"]


def test_offline_requires_a_location(client, make_user, geocode):
    signed_in(client, make_user)
    r = client.post("/find-trainer", data={"mode": "offline", "location": ""})
    assert r.status_code == 200 and "enter your location" in r.get_data(as_text=True)
    assert geocode == []


def test_offline_geocoder_failure_is_handled(client, make_user, monkeypatch):
    def down(location):
        raise trainer_service.GeocodingError
    monkeypatch.setattr(trainer_service, "get_coordinates", down)
    signed_in(client, make_user)
    r = client.get("/find-trainer?mode=offline&location=Indiranagar")
    assert r.status_code == 200 and "look up that location" in r.get_data(as_text=True)


def test_anonymous_user_is_sent_to_register(client):
    r = client.get("/find-trainer")
    assert r.status_code == 302 and "/register" in r.headers["Location"]


def test_invalid_mode_is_rejected(client, make_user):
    signed_in(client, make_user)
    assert client.get("/find-trainer?mode=teleport").status_code == 200   # form re-shown, no crash


# --------------------------------------------------------- CONTACT MESSAGE
def test_message_builder_offline_and_online():
    offline = trainer_service.build_contact_message("Priya Sharma", "Koramangala", online=False)
    online = trainer_service.build_contact_message("Priya Sharma", "Koramangala", online=True)
    assert offline.startswith("Hello Priya Sharma,")
    assert "I found your profile through Koramangala" in offline
    assert "Koramangala" not in online and "found your profile through" not in online
    assert "Please let me know how we can get started." in online and online.endswith("Thank you!")


def test_message_builder_strips_control_characters():
    msg = trainer_service.build_contact_message("Evil\r\nBcc: x@y.z", "Loc\nation", online=False)
    assert "\r" not in msg and "Hello Evil Bcc: x@y.z," in msg


def test_contact_link_carries_trainer_id_and_mode(client, make_user, make_trainer, no_geocoding):
    tid = make_trainer(name="Link Lata")
    signed_in(client, make_user)
    page = client.get("/find-trainer?mode=online").get_data(as_text=True)
    assert f"/contact?trainer={tid}&amp;mode=online" in page or f"/contact?trainer={tid}&mode=online" in page


def test_contact_page_prefills_offline_message_with_place(client, make_user, make_trainer):
    tid = make_trainer(name="Priya Sharma", location="Koramangala")
    signed_in(client, make_user)
    page = client.get(f"/contact?trainer={tid}&mode=offline").get_data(as_text=True)
    assert "Hello Priya Sharma," in page and "I found your profile through Koramangala" in page
    assert f'value="{tid}"' in page                                   # hidden trainer id kept


def test_contact_page_prefills_online_message_without_place(client, make_user, make_trainer):
    tid = make_trainer(name="Priya Sharma", location="Koramangala")
    signed_in(client, make_user)
    page = client.get(f"/contact?trainer={tid}&mode=online").get_data(as_text=True)
    assert "Hello Priya Sharma," in page and "Koramangala" not in page


def test_prefill_is_html_escaped(client, make_user, make_trainer):
    tid = make_trainer(name="<script>alert(1)</script>")
    signed_in(client, make_user)
    page = client.get(f"/contact?trainer={tid}&mode=online").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page


def test_unknown_or_inactive_trainer_gives_blank_form_and_notice(client, make_user, make_trainer):
    gone = make_trainer(name="Gone Gita", active=False)
    signed_in(client, make_user)
    for bad in (gone, 99999):
        page = client.get(f"/contact?trainer={bad}&mode=online").get_data(as_text=True)
        assert "no longer available" in page and "Hello Gone Gita" not in page
    assert client.get("/contact?trainer=abc&mode=online").status_code == 200


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_contact_info", lambda **kw: sent.append(kw))
    return sent


def test_edited_message_goes_to_the_right_trainer(client, make_user, make_trainer, outbox):
    make_trainer(name="Decoy Dev")
    tid = make_trainer(name="Right Rani")
    signed_in(client, make_user)
    r = client.post("/contact", data={"message": "My own edited words for you.", "trainer_id": str(tid),
                                      "contact_mode": "online", "name": "x", "email": "x@x.com"})
    assert r.status_code == 302 and len(outbox) == 1
    assert outbox[0]["trainer"].id == tid and outbox[0]["contact_message"] == "My own edited words for you."


def test_posted_trainer_id_is_validated(client, make_user, make_trainer, outbox):
    inactive = make_trainer(name="Off Olga", active=False)
    signed_in(client, make_user)
    for bad in (str(inactive), "424242"):
        r = client.post("/contact", data={"message": "Hello there trainer!", "trainer_id": bad})
        assert r.status_code == 200 and "no longer available" in r.get_data(as_text=True)
    r = client.post("/contact", data={"message": "Hello there trainer!", "trainer_id": "1; DROP TABLE trainers"})
    assert r.status_code == 200
    assert outbox == []                                               # nothing was sent for any bad id


def test_message_is_validated_on_the_server(client, make_user, make_trainer, outbox):
    tid = make_trainer()
    signed_in(client, make_user)
    assert client.post("/contact", data={"message": "short", "trainer_id": str(tid)}).status_code == 200
    assert client.post("/contact", data={"message": "x" * 1300, "trainer_id": str(tid)}).status_code == 200
    assert outbox == []


def test_general_contact_still_works_without_trainer(client, make_user, outbox):
    signed_in(client, make_user)
    r = client.post("/contact", data={"message": "A general question for the team."})
    assert r.status_code == 302 and outbox[0]["trainer"] is None


def test_guest_contact_keeps_trainer_through_otp(client, make_trainer, sent_codes, outbox):
    tid = make_trainer(name="Guest Gauri")
    client.post("/contact", data={"name": "Guest", "email": "g@example.com",
                                  "message": "Hello Gauri, I would like to train.", "trainer_id": str(tid)})
    client.post("/verify-contact", data={"verification_code": sent_codes[0][1]})
    assert outbox and outbox[0]["trainer"].id == tid
