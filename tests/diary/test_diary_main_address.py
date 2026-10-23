"""Diary studies on the main address (config DIARY_ON_MAIN_ADDRESS, app/hosts.py): with a participants' address of its
own, the app's API, the join link, the entry addresses and the questionnaire pages of a diary study stay on the main
address; a questionnaire's pages still move to the participants' address. 2026-10-12: the participants' containers
and their nginx did not carry the diary code yet, and only the researchers' side could be updated."""
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK
from tests.diary.test_diary import API, _finish, _new, _path

ASK = "http://ask.localhost"
MAIN = "http://localhost:5060"


def test_a_diary_study_runs_on_the_main_address(app, client):
    code = _new(app, client)
    app.config.update(PARTICIPANT_BASE_URL=ASK)
    phone = app.test_client()
    info = phone.get(f"{API}/join/{code}")
    assert info.status_code == 200 and info.json["code"] == code  # answered here, not moved
    joined = phone.post(API + "/enrol", json={"code": code, "lang": "de"})
    assert joined.status_code == 200
    url = joined.json["url"]
    assert url.startswith(f"{MAIN}/s/{code}/e/")  # the entry address carries the main address
    went = phone.get(_path(url))
    assert went.status_code == 302 and not went.headers["Location"].startswith(ASK)
    assert phone.get(f"/s/{code}").status_code == 200  # the page of the entry: no move to the participants' address
    entry = _finish(app, phone, code, first={**CONSENT_OK, "i1_c_age": "1"})
    assert entry.status == Response.COMPLETED
    page = client.get("/surveys/1/field").get_data(as_text=True)
    assert f"{MAIN}/j/{code}" in page and f"{ASK}/j/" not in page  # the join link of the QR code


def test_a_questionnaire_still_moves_to_the_participants_address(app, client):
    _new(app, client)
    client.post("/surveys/new", data={"title": "Fragebogen"})
    with app.app_context():
        survey = db.session.scalars(db.select(Survey).where(Survey.sampling.is_(None))).first()
        survey.status = Survey.RUNNING
        db.session.commit()
        code = survey.code
    app.config.update(PARTICIPANT_BASE_URL=ASK)
    moved = app.test_client().get(f"/s/{code}")
    assert moved.status_code == 301 and moved.headers["Location"] == f"{ASK}/s/{code}"


def test_switched_off_the_diary_moves_too(app, client):
    code = _new(app, client)
    app.config.update(PARTICIPANT_BASE_URL=ASK, DIARY_ON_MAIN_ADDRESS=False)
    moved = app.test_client().get(f"{API}/join/{code}")
    assert moved.status_code == 301 and moved.headers["Location"].startswith(f"{ASK}{API}/join/")
