"""The diary app's other requests with a person's key (security audit 2026-10-13, AN-2, TN-11, AN-6): /study,
/offline/<module>, /leave and /erase had no brake per person (only /entry and /offline had one), and /offline/<module>
drew the whole participant page for every request; and it gave the page also to a person who left or ended."""
from datetime import UTC

import pytest

from app import limits
from app.extensions import db
from app.models import Enrolment, Survey
from tests.diary.test_diary import API, _active_from_today, _bearer, _enrol, _new


def test_study_and_offline_pages_are_braked_per_person(app, client):
    """AN-2/TN-11: past DIARY_SYNC_LIMIT within DIARY_PERSON_WINDOW the person's requests get 429."""
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    limits._diary_syncs.clear()
    app.config["DIARY_SYNC_LIMIT"] = 3
    answers = [phone.post(f"{API}/study", json={}, headers=_bearer(secret)).status_code,
               phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret)).status_code,
               phone.post(f"{API}/study", json={}, headers=_bearer(secret)).status_code,
               phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret)).status_code,
               phone.post(f"{API}/leave", json={}, headers=_bearer(secret)).status_code,
               phone.post(f"{API}/erase", json={}, headers=_bearer(secret)).status_code]
    assert answers == [200, 200, 200, 429, 429, 429]
    with app.app_context():
        assert db.session.scalar(db.select(Enrolment)).status == Enrolment.ACTIVE  # neither left nor erased
    other_phone, other = _enrol(app, code)
    assert other_phone.post(f"{API}/study", json={}, headers=_bearer(other)).status_code == 200  # per person
    limits._diary_syncs.clear()


def test_offline_pages_only_for_a_person_who_may_answer(app, client):
    """AN-6: a person who left (or a closed study) gets {"offline": false}, and offline_at is not written."""
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    assert phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret)).json["offline"] is True
    assert phone.post(f"{API}/leave", json={}, headers=_bearer(secret)).status_code == 200
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        person.settings = {k: v for k, v in (person.settings or {}).items() if k != "offline_at"}
        db.session.commit()
    kept = phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret)).json
    assert kept == {"offline": False}
    with app.app_context():
        assert "offline_at" not in (db.session.scalar(db.select(Enrolment)).settings or {})
    # a closed study likewise
    phone2, secret2 = _enrol(app, code)
    _active_from_today(app)
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.CLOSED
        db.session.commit()
    assert phone2.post(f"{API}/offline/m1", json={}, headers=_bearer(secret2)).json == {"offline": False}


@pytest.mark.parametrize("mode", ["online", "offline", "mixed"])
def test_parallel_self_entries_share_the_daily_cap_and_uuid(app, client, monkeypatch, mode):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from time import sleep

    from app import diary_api
    from app.models import Response
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.sampling = {**survey.sampling, "modules": [
            {**m, "trigger": {"t": "self", "max_per_day": 1}} for m in survey.sampling["modules"]]}
        db.session.commit()
    app.config["DIARY_PERSON_LIMIT"] = 0

    from app.models import utcnow
    from tests.diary.test_diary_integrity import _kept_page, _rating_field
    version = _kept_page(app, phone, secret)
    field = _rating_field(app)
    started = int(utcnow().replace(tzinfo=UTC).timestamp() * 1000) - 60000
    def body(i):
        return {"module": "m1", "uuid": f"00000000-0000-4000-8000-{i:012d}",
                "version": version, "started": started, "completed": started + 30000,
                "form": {field: ["4"]}}
    def endpoint(i):
        return "/offline" if mode == "offline" or (mode == "mixed" and i % 2) else "/entry"
    start = Barrier(4)
    original = diary_api._today_count
    def slow_count(*args):
        value = original(*args)
        sleep(0.04)
        return value
    monkeypatch.setattr(diary_api, "_today_count", slow_count)
    def attempt(i):
        start.wait(timeout=10)
        return app.test_client().post(API + endpoint(i), json=body(i), headers=_bearer(secret))
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(attempt, range(4)))
    if mode == "online":  # an open report is continued, not begun anew (Abgleich 2026-10-15, T07-N-1): one row
        assert [r.status_code for r in replies] == [200, 200, 200, 200]
    else:
        assert sorted(r.status_code for r in replies) == [200, 409, 409, 409]
    assert all(r.json["error"] == "daily_limit" for r in replies if r.status_code == 409)
    winner = next(i for i, r in enumerate(replies) if r.status_code == 200)
    replay = app.test_client().post(API + endpoint(winner), json=body(winner), headers=_bearer(secret))
    assert replay.status_code == 200  # each retry issues a fresh one-time token for the same row
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).where(Response.part == "m1")) == 1
