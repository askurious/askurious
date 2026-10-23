"""Robustness and visibility of diary studies (audit 2026-10-11, M4, M9, N1, N2, N3, N6, N9, N10; decision 6a): valid
secrets and codes pass whatever other phones behind the address sent, the daily limit as 409, a short survey without
questions, JSON bodies that are no object or nested too deeply, the join page, test codes that can be made anew and
are shown only to "Bearbeiten" and never on an example survey, and the deletion request that names what goes."""
import pytest

from app import design, diary
from app.extensions import db
from app.models import Response, Survey, SurveyMember
from app.request_values import safe_loads
from tests.conftest import allow, experiments_on
from tests.diary.test_diary import API, _active_from_today, _bearer, _enrol, _entries_of, _new, _path
from tests.dashboards.test_showcase import OTHER, _as

DEEP = "[" * 5000 + "]" * 5000


def test_valid_codes_pass_while_wrong_ones_are_counted(app, client):
    code = _new(app, client)
    app.config["CODE_LIMIT"] = 3
    where = {"REMOTE_ADDR": "198.51.100.4"}
    other = app.test_client()
    wrong = [other.get(f"{API}/join/zzzzzz", environ_base=where).status_code for _ in range(5)]
    assert wrong == [404, 404, 404, 429, 429]
    assert other.get("/j/zzzzzz", environ_base=where).status_code == 429  # the join page counts the same way
    phone = app.test_client()
    assert phone.get(f"{API}/join/{code}", environ_base=where).status_code == 200
    assert phone.get(f"/j/{code}", environ_base=where).status_code == 200
    assert phone.post(f"{API}/enrol", json={"code": code}, environ_base=where).status_code == 200


def test_the_join_page_shows_no_title_of_a_draft_or_an_ended_study(app, client):
    code = _new(app, client, running=False)
    assert "Lernen im Alltag" not in app.test_client().get(f"/j/{code}").get_data(as_text=True)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.status = Survey.RUNNING
        db.session.commit()
    assert "Lernen im Alltag" in app.test_client().get(f"/j/{code}").get_data(as_text=True)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.status = Survey.CLOSED
        db.session.commit()
    page = app.test_client().get(f"/j/{code}").get_data(as_text=True)
    assert "Lernen im Alltag" not in page and "keine neuen Teilnehmenden" in page


def test_bodies_that_are_no_object_or_too_deep_answer_400(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    for body in ("[1, 2]", "7", '"x"', "true", DEEP):
        assert phone.post(f"{API}/enrol", data=body, content_type="application/json").status_code == 400
        for path in ("/study", "/entry", "/offline"):
            out = phone.post(API + path, data=body, content_type="application/json", headers=_bearer(secret))
            assert out.status_code == 400, (path, body[:10])
    with pytest.raises(ValueError):
        safe_loads(DEEP)
    with pytest.raises(ValueError):
        safe_loads("[" * 70 + "]" * 70)  # deeper than MAX_DEPTH: refused before any code walks it
    assert safe_loads('{"a": [1, {"b": "x\\u0000"}]}') == {"a": [1, {"b": "x"}]}


def test_form_fields_with_deep_json_do_not_break_the_pages(app, client, survey):
    from werkzeug.datastructures import MultiDict

    from app import conditions, interview_ai, study_plan

    assert conditions.from_form(MultiDict({"item": "", "raw": DEEP}), "", {}) is None  # no RecursionError
    with app.test_request_context():
        assert interview_ai._state(MultiDict({"data": DEEP}))["questions"] == []
        found = study_plan.from_form(db.session.get(Survey, 1), MultiDict({"plan_layout": DEEP}))
        assert found["layout"] == study_plan.plan_of(db.session.get(Survey, 1))["layout"]  # the stored drawing stays


def test_a_short_survey_without_questions_is_found_and_ends_at_once(app, client):
    code = _new(app, client, running=False)
    client.post("/diary/1/schedule/module")  # "+ Kurzbefragung hinzufügen": m2 without a block
    with app.app_context():
        survey = db.session.get(Survey, 1)
        errors = design.blocking(design.check(survey, start=True))
        assert any("Kurzbefragung „Meldung“ hat noch keine Fragen" in f.message for f in errors)
    # a test person in the draft opens an entry of it: it ends at once instead of a 500
    phone = app.test_client()
    test = phone.post(f"{API}/enrol", json={"code": _test_code(app)}).json
    assert test.get("secret")
    with app.app_context():
        from app.models import Enrolment

        person = db.session.scalar(db.select(Enrolment))
        person.status, person.first_day = Enrolment.ACTIVE, diary.local_today(person)
        person.last_day = person.first_day
        db.session.commit()
    url = phone.post(API + "/entry", json={"module": "m2"}, headers=_bearer(test["secret"])).json["url"]
    phone.get(_path(url))
    assert phone.get(f"/s/{code}").status_code < 500
    with app.app_context():
        entry = db.session.scalar(db.select(Response).filter_by(part="m2"))
        assert entry.status == Response.COMPLETED


def _test_code(app, fast=False):
    with app.app_context():
        return diary.test_code(db.session.get(Survey, 1), fast=fast)


def test_test_codes_are_made_anew_and_the_old_ones_lead_nowhere(app, client):
    code = _new(app, client, running=False)
    old, old_fast = _test_code(app), _test_code(app, fast=True)
    assert app.test_client().get(f"{API}/join/{old}").status_code == 200
    assert client.post("/diary/1/test-codes").status_code == 302
    new, new_fast = _test_code(app), _test_code(app, fast=True)
    assert new != old and new_fast != old_fast and new.startswith(code + "-")
    phone = app.test_client()
    assert phone.get(f"{API}/join/{old}").status_code == 404
    assert phone.get(f"{API}/join/{old_fast}").status_code == 404
    assert phone.get(f"{API}/join/{new}").status_code == 200
    page = client.get("/surveys/1/field").get_data(as_text=True)  # joining is on "Datenerhebung" (2026-10-12)
    assert new in page and old not in page and "Testcodes neu erzeugen" in page
    # the schedule saved afterwards keeps the new codes
    client.post("/diary/1/schedule", data={"period_days": "14", "m1_trigger": "fixed", "m1_name": "Moment",
                                           "m1_times": "09:00", "m1_weekdays": ["1"]})
    assert _test_code(app) == new


def test_test_codes_only_for_editors_and_never_on_an_example(app, client):
    _new(app, client)
    allow(app, OTHER, keep=True)
    other = _as(app, OTHER, "Kollegin")
    experiments_on(other, "diary", orcid=OTHER)
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, name="Kollegin", role="viewer", position=1))
        db.session.commit()
    test = _test_code(app)
    page = other.get("/surveys/1/field")
    assert page.status_code == 200 and test not in page.get_data(as_text=True)
    assert other.post("/diary/1/test-codes").status_code in (302, 403, 404)
    assert _test_code(app) == test  # "Ansehen" cannot make new ones
    assert test in client.get("/surveys/1/field").get_data(as_text=True)
    # an example survey: no test code for anyone, and the persons only for its members
    _enrol(app, db_code(app))
    with app.app_context():
        db.session.execute(db.delete(SurveyMember).where(SurveyMember.orcid == OTHER))
        survey = db.session.get(Survey, 1)
        survey.showcase = True
        db.session.commit()
        pid = db.session.scalar(db.select(__import__("app.models", fromlist=["Enrolment"]).Enrolment.pid))
    own = client.get("/surveys/1/field").get_data(as_text=True)  # the persons on "Datenerhebung" since T5 (2026-10-17)
    assert pid in own and test not in own
    seen = other.get("/surveys/1/field")
    assert seen.status_code == 200
    text = seen.get_data(as_text=True)
    assert test not in text and pid not in text
    assert test not in other.get("/surveys/1/field").get_data(as_text=True)


def db_code(app):
    with app.app_context():
        return db.session.get(Survey, 1).code


def test_the_deletion_request_names_every_entry_of_the_person(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    _entries_of(app, phone, secret, code, 2)
    with app.app_context():
        pid = db.session.scalar(db.select(Response.pid).filter_by(part="m1"))
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 3
    found = client.post("/surveys/1/erasure", data={"term": pid}).get_data(as_text=True)
    assert "Gefunden: 3 Teilnahmen" in found and 'name="confirm" value="3"' in found
    assert "alle Einträge der Person" in found
    done = client.post("/surveys/1/erasure", data={"term": pid, "confirm": "3"}, follow_redirects=True).get_data(as_text=True)
    assert "3 Teilnahmen endgültig gelöscht" in done
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
