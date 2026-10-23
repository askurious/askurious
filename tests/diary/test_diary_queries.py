"""Diary admissions stay bounded with a long history; exact time-zone and retry semantics survive."""
from datetime import date, datetime, timedelta
from statistics import median
from time import perf_counter

import pytest
from sqlalchemy import event

from app import diary, diary_api
from app.extensions import db
from app.models import Enrolment, Response, Survey
from tests.diary.test_diary import API, _active_from_today, _bearer, _enrol, _new


def test_study_and_admission_with_five_thousand_old_entries(app, client, monkeypatch):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        survey = db.session.get(Survey, person.survey_id)
        survey.sampling = {**survey.sampling, "modules": [
            {**m, "trigger": {"t": "self", "max_per_day": 2}} for m in diary.modules(survey)]}
        at = datetime(2020, 1, 1)
        db.session.execute(db.insert(Response), [
            {"survey_id": survey.id, "enrolment_id": person.id, "pid": f"history-{i}", "part": "m1",
             "occasion": {"part": "m1", "due": None}, "started_at": at + timedelta(minutes=i),
             "status": Response.COMPLETED, "answers": {"large": "x" * 1000}}
            for i in range(5000)])
        db.session.commit()
        today = diary.local_today(person)
        old, new = [], []
        for _ in range(5):
            db.session.expunge_all()
            person = db.session.scalar(db.select(Enrolment))
            began = perf_counter()
            diary_api._today_count(person, "m1", today, diary._entries(person, light=True))
            old.append((perf_counter() - began) * 1000)
            began = perf_counter()
            diary_api._today_count(person, "m1", today)
            new.append((perf_counter() - began) * 1000)
        print(f"5000 entries, median of 5 daily-count reads: history={median(old):.2f} ms; targeted={median(new):.2f} ms")
        statements = []
        def capture(connection, cursor, statement, parameters, context, many):
            statements.append((statement, parameters))
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            diary_api._today_count(person, "m1", today)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        statement, parameters = statements[-1]
        if db.engine.dialect.name == "sqlite":
            plan = db.session.connection().exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
            ranges = [str(row) for row in plan if "ix_response_diary_started" in str(row)]
            assert len(ranges) == 2 and all("started_at>" in row for row in ranges)
            print("Daily range plan:", plan)

    def no_history(*args, **kwargs):
        raise AssertionError("request loaded the full history")

    monkeypatch.setattr(diary, "_entries", no_history)
    loaded = []
    def observed(session, instance):
        if isinstance(instance, Response):
            loaded.append(instance.id)

    event.listen(db.session.session_factory, "loaded_as_persistent", observed)
    try:
        assert phone.post(API + "/study", json={}, headers=_bearer(secret)).status_code == 200
        assert phone.post(API + "/entry", json={"module": "m1"}, headers=_bearer(secret)).status_code == 200
        assert len(loaded) < 5
    finally:
        event.remove(db.session.session_factory, "loaded_as_persistent", observed)
    with app.app_context():
        last = db.session.scalar(db.select(Response).order_by(Response.id.desc()).limit(1))
        assert last.occasion["n"] == 5001


@pytest.mark.parametrize("tz", ["Europe/Berlin", "Pacific/Kiritimati", "America/Los_Angeles"])
@pytest.mark.parametrize("factor", [1, 144])
def test_daily_query_matches_full_history_across_dst_travel_and_fast_clock(app, client, tz, factor):
    code = _new(app, client)
    _enrol(app, code)
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        person.tz = tz
        t0 = datetime(2026, 3, 27)
        person.settings = {"clock": {"t0": t0.isoformat(), "factor": factor},
                           "tz": [{"from": None, "tz": "Europe/Berlin"},
                                  {"from": "2026-03-29", "tz": tz}]}
        for hour in range(144):
            at = t0 + timedelta(hours=hour)
            row = Response(survey_id=person.survey_id, enrolment_id=person.id, pid=f"day-{hour}",
                           started_at=t0 + (at - t0) / factor,
                           occasion={"part": "m1", "due": at.isoformat() if hour % 3 == 0 else None})
            db.session.add(row)
        db.session.commit()
        # Simulate an older participant container writing occasion without the part column.
        db.session.execute(db.update(Response).where(Response.pid == "day-55").values(part=None))
        db.session.commit()
        entries = diary._entries(person, light=True)
        for day in (date(2026, 3, 28), date(2026, 3, 29), date(2026, 3, 30)):
            assert diary_api._today_count(person, "m1", day) == diary_api._today_count(person, "m1", day, entries)


def test_targeted_readers_preserve_legacy_parts_and_completed_prompt_contract(app, client):
    code = _new(app, client)
    _enrol(app, code)
    _active_from_today(app)
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        survey = db.session.get(Survey, person.survey_id)
        due = datetime(2026, 3, 29, 7)
        row = Response(survey_id=survey.id, enrolment_id=person.id, pid="legacy-prompt",
                       status=Response.COMPLETED, occasion={"part": "m1", "due": due.isoformat()})
        db.session.add(row)
        db.session.commit()
        db.session.execute(db.update(Response).where(Response.id == row.id).values(part=None))
        db.session.commit()
        entries = diary._entries(person, light=True)
        assert diary.study_json(survey, person, "de") == diary.study_json(survey, person, "de", entries)
        assert diary_api._same_prompt("m1", due, person=person).id == row.id
        assert diary.entry_part(person).id == diary.entry_part(person, entries).id
