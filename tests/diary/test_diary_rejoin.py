"""Joining a diary study: one shared code, or personal codes that connect a new phone to the same participation
again once after the researchers released the code (diary.join_settings, diary.rejoin; Philipp, 2026-10-13). Since
the Abgleich 2026-10-15 (NB-1, Ralf's decision 2a) only after a release, valid for diary.RELEASE_TIME."""
from datetime import timedelta

from app import diary
from app.extensions import db
from app.models import DiaryInvite, Enrolment, Survey, utcnow
from tests.diary.test_diary import API, _bearer, _new


def _personal(client, rejoin=None):
    client.post("/diary/1/schedule", data={"period_days": "14", "m1_trigger": "fixed", "m1_name": "Moment",
                                           "m1_times": "09:00", "m1_weekdays": ["1"], "join": "personal",
                                           **({"rejoin": rejoin} if rejoin else {})})


def _codes(app, code):
    with app.app_context():
        return [i["c"] for i in diary.invites(db.session.get(Survey, 1))]


def test_a_new_diary_survey_asks_how_people_join_and_makes_the_codes(app, client):
    from tests.conftest import experiments_on

    experiments_on(client, "diary")
    page = client.get("/diary/new").get_data(as_text=True)
    assert 'name="join" value="shared"' in page and 'name="join" value="personal"' in page
    assert 'name="rejoin"' not in page  # "at any time" is no choice any more (NB-1)
    client.post("/diary/new", data={"title": "Codes", "language": "de", "join": "personal", "persons": "7",
                                    "rejoin": "auto"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey))
        assert diary.invite_only(survey) and diary.rejoin_mode(survey) == "release"
        assert survey.sampling["rejoin"] == "release"
        assert len(diary.invites(survey)) == 7
    client.post("/diary/new", data={"title": "Shared", "language": "de", "join": "shared"})
    with app.app_context():
        shared = db.session.scalars(db.select(Survey).order_by(Survey.id)).all()[-1]
        assert not diary.invite_only(shared) and diary.rejoin_mode(shared) == "release" and diary.invites(shared) == []


def test_a_used_code_of_a_study_set_to_auto_no_longer_connects_without_a_release(app, client):
    """Abgleich 2026-10-15, NB-1: a study made with "auto" (2026-10-13 to 2026-10-15) behaves like "release": whoever
    sees a printed card cannot take the participation over, lock the phone out and delete the answers."""
    code = _new(app, client)
    _personal(client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.sampling = {**survey.sampling, "rejoin": "auto"}
        db.session.commit()
    client.post("/diary/1/invites", data={"n": "1"})
    card = f"{code}-{_codes(app, code)[0]}"
    owner, stranger = app.test_client(), app.test_client()
    first = owner.post(API + "/enrol", json={"code": card}).json
    assert stranger.get(API + f"/join/{card}").json["error"] == "used"
    assert stranger.post(API + "/enrol", json={"code": card}).json["error"] == "used"
    assert stranger.post(API + "/erase", json={}).status_code == 401  # no way to the person's answers
    assert owner.post(API + "/study", json={}, headers=_bearer(first["secret"])).status_code == 200


def test_after_a_release_a_personal_code_connects_a_new_phone_to_the_same_participation(app, client):
    code = _new(app, client)
    _personal(client)
    client.post("/diary/1/invites", data={"n": "1"})
    c = _codes(app, code)[0]
    card = f"{code}-{c}"
    old_phone, new_phone = app.test_client(), app.test_client()
    first = old_phone.post(API + "/enrol", json={"code": card}).json
    assert "rejoined" not in first
    client.post(f"/diary/1/invites/{c}/release")
    assert new_phone.get(API + f"/join/{card}").json["rejoin"] is True
    again = new_phone.post(API + "/enrol", json={"code": card}).json
    assert again["rejoined"] is True and again["status"] == "pending" and again["url"]  # entry part still open
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Enrolment.id))) == 1  # the same person, no new one
        person = db.session.scalar(db.select(Enrolment))
        assert person.settings["rejoined_at"]
        assert db.session.scalar(db.select(DiaryInvite.enrolment_id)) == person.id
    assert old_phone.post(API + "/study", json={}, headers=_bearer(first["secret"])).status_code == 401
    assert new_phone.post(API + "/study", json={}, headers=_bearer(again["secret"])).status_code == 200
    page = client.get("/surveys/1/field").get_data(as_text=True)
    assert "zuletzt neu verbunden" in page  # the researchers see that a phone connected again
    # the shared code does not join with personal codes
    assert new_phone.post(API + "/enrol", json={"code": code}).json["error"] == "closed"


def test_after_release_a_code_connects_once(app, client):
    code = _new(app, client)
    _personal(client, "release")
    client.post("/diary/1/invites", data={"n": "1"})
    c = _codes(app, code)[0]
    card = f"{code}-{c}"
    phone = app.test_client()
    assert phone.post(API + "/enrol", json={"code": card}).status_code == 200
    refused = phone.get(API + f"/join/{card}")
    assert refused.status_code == 409 and refused.json["error"] == "used"
    page = client.get("/surveys/1/field").get_data(as_text=True)
    assert f"/diary/1/invites/{c}/release" in page
    client.post(f"/diary/1/invites/{c}/release")
    with app.app_context():
        assert diary.invites(db.session.get(Survey, 1))[0]["released"] is True
    assert "freigegeben bis" in client.get("/surveys/1/field").get_data(as_text=True)
    assert phone.post(API + "/enrol", json={"code": card}).json["rejoined"] is True
    assert phone.post(API + "/enrol", json={"code": card}).json["error"] == "used"  # the release is used up
    client.post(f"/diary/1/invites/{c}/release")
    client.post(f"/diary/1/invites/{c}/release", data={"on": "0"})  # taken back
    assert phone.post(API + "/enrol", json={"code": card}).json["error"] == "used"
    assert client.post("/diary/1/invites/zzzzzzzz/release").status_code == 404


def test_a_release_holds_only_for_a_short_time(app, client):
    """Abgleich 2026-10-15, NB-1: a release forgotten by the researchers does not leave the code open for good."""
    code = _new(app, client)
    _personal(client)
    client.post("/diary/1/invites", data={"n": "1"})
    c = _codes(app, code)[0]
    card = f"{code}-{c}"
    phone = app.test_client()
    phone.post(API + "/enrol", json={"code": card})
    client.post(f"/diary/1/invites/{c}/release")
    with app.app_context():
        db.session.execute(db.update(DiaryInvite).values(
            released_at=utcnow() - diary.RELEASE_TIME - timedelta(minutes=1)))
        db.session.commit()
        assert diary.invites(db.session.get(Survey, 1))[0]["released"] is False
    assert phone.get(API + f"/join/{card}").json["error"] == "used"
    assert phone.post(API + "/enrol", json={"code": card}).json["error"] == "used"


def test_a_person_who_left_or_a_code_from_before_does_not_connect_again(app, client):
    code = _new(app, client)
    _personal(client)
    client.post("/diary/1/invites", data={"n": "2"})
    left, before = _codes(app, code)
    phone = app.test_client()
    secret = phone.post(API + "/enrol", json={"code": f"{code}-{left}"}).json["secret"]
    phone.post(API + "/leave", json={}, headers=_bearer(secret))
    client.post(f"/diary/1/invites/{left}/release")
    assert phone.post(API + "/enrol", json={"code": f"{code}-{left}"}).json["error"] == "used"
    with app.app_context():  # used before 2026-10-13: nobody known
        db.session.execute(db.update(DiaryInvite).where(DiaryInvite.code == before).values(used_at=db.func.now()))
        db.session.commit()
    client.post(f"/diary/1/invites/{before}/release")
    assert phone.post(API + "/enrol", json={"code": f"{code}-{before}"}).json["error"] == "used"


def test_a_viewer_cannot_release_a_code(app, client):
    from app.surveys.base import EDIT_ENDPOINTS

    assert "diary.release" in EDIT_ENDPOINTS
