"""When the app may begin an entry of a module (app/diary_api.py, code review 2026-10-15, G3-G6): a prompt without an
expiry is over at the end of its day also online; a prompt belongs to the day it was due on, not to the day of the
answer; the brake per person also counts a repeated answer id; and the page kept for offline answers writes the
person's settings only under the person's lock again."""
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

from app import diary, diary_access, diary_api, limits
from app.extensions import db
from app.models import Enrolment, Response, Survey
from tests.diary.test_diary import API, _active_from_today, _bearer, _enrol, _new


def _late_prompt(app, expires):
    """A person whose last day was two days ago, with module m1 due at 23:30 (UTC) every day."""
    with app.app_context():
        survey = db.session.get(Survey, 1)
        person = db.session.scalar(db.select(Enrolment))
        person.tz, person.settings = "UTC", {}
        person.last_day = diary.local_today(person) - timedelta(days=2)
        person.first_day = person.last_day - timedelta(days=2)
        person.status = Enrolment.ACTIVE
        m1 = {**diary.module(survey, "m1"), "expires": expires, "days": [1, None],
              "trigger": {"t": "fixed", "times": ["23:30"], "weekdays": [1, 2, 3, 4, 5, 6, 7]}}
        survey.sampling = {**survey.sampling, "modules": [m1] + [m for m in diary.modules(survey) if m["key"] != "m1"]}
        db.session.commit()
        return datetime.combine(person.last_day, time(23, 30))


def _check(app, due, at):
    with app.test_request_context():
        survey = db.session.get(Survey, 1)
        person = db.session.scalar(db.select(Enrolment))
        millis = int(due.replace(tzinfo=timezone.utc).timestamp() * 1000)
        out = diary_api._check_module(survey, person, "finished", "m1", {"due": millis}, at)
        return out if isinstance(out, dict) else out.status_code


def test_a_prompt_of_the_last_day_answered_after_midnight_counts_for_its_day(app, client):
    """G5: the prompt of 23:30 on the last day, answered at 00:10 within its expiry, is not refused as "ended"."""
    code = _new(app, client)
    _enrol(app, code)
    due = _late_prompt(app, expires=60)
    checked = _check(app, due, due + timedelta(minutes=40))
    assert isinstance(checked, dict) and checked["occasion"]["day"] == 3
    assert _check(app, due, due + timedelta(minutes=70)) == 410
    assert _check(app, due + timedelta(days=1), due + timedelta(days=1, minutes=10)) == 409  # past the last day


def test_a_prompt_without_expiry_is_over_at_the_end_of_its_day_online(app, client):
    """G4: online as offline (prompt_over): without an expiry a prompt is taken until the end of its day only."""
    code = _new(app, client)
    _enrol(app, code)
    due = _late_prompt(app, expires=0) - timedelta(days=1)
    assert isinstance(_check(app, due, due + timedelta(minutes=20)), dict)
    assert _check(app, due, due + timedelta(minutes=40)) == 410  # 00:10 the next day
    assert _check(app, due, due + timedelta(days=2)) == 410


def test_an_entry_of_the_last_day_stays_open_on_the_next_day(app, client):
    """G5: the address of such an entry opens on the day after the period, not later."""
    code = _new(app, client)
    _enrol(app, code)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        person = db.session.scalar(db.select(Enrolment))
        person.status, person.tz = Enrolment.ACTIVE, "UTC"
        person.last_day = diary.local_today(person) - timedelta(days=1)
        person.first_day = person.last_day - timedelta(days=2)

        def entry(day):
            return SimpleNamespace(status=Response.ACTIVE, occasion={
                "part": "m1", "due": datetime.combine(day, time(23, 30)).isoformat()})
        assert diary.refresh(survey, person) == "finished"
        assert diary_access.allowed(survey, person, entry(person.last_day))
        assert not diary_access.allowed(survey, person, SimpleNamespace(status=Response.ACTIVE, occasion={"part": "m1"}))
        person.last_day -= timedelta(days=1)
        assert not diary_access.allowed(survey, person, entry(person.last_day))


def test_a_repeated_answer_id_is_braked_too(app, client):
    """G6: the same uuid again gives a new address each time; it counts against the brake per person."""
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.sampling = {**survey.sampling, "modules": [
            {**m, "trigger": {"t": "self", "max_per_day": 5}} for m in survey.sampling["modules"]]}
        db.session.commit()
    limits._diary_persons.clear()
    app.config["DIARY_PERSON_LIMIT"] = 3
    body = {"module": "m1", "uuid": "00000000-0000-4000-8000-000000000001"}
    answers = [phone.post(API + "/entry", json=body, headers=_bearer(secret)).status_code for _ in range(5)]
    assert answers == [200, 200, 200, 429, 429]
    limits._diary_persons.clear()


def test_the_offline_page_writes_under_the_lock_again_and_checks_the_secret(app, client, monkeypatch):
    """G3: the page is drawn in a transaction that is rolled back, which also ends the person's lock. The note
    "offline_at" is written only after the lock is taken again; a secret replaced meanwhile answers 401."""
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    calls = []
    original = diary_access.lock_person

    def lock_person(person):
        calls.append(person.id)
        if len(calls) == 2:  # another phone connected (rejoin) while the page was drawn
            db.session.execute(db.update(Enrolment).where(Enrolment.id == person.id).values(secret_hash="0" * 64))
        return original(person)
    monkeypatch.setattr(diary_access, "lock_person", lock_person)
    out = phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret))
    assert len(calls) == 2 and out.status_code == 401
    with app.app_context():
        assert "offline_at" not in (db.session.scalar(db.select(Enrolment)).settings or {})
