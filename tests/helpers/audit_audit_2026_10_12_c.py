"""Helpers of the tests from test_audit_2026_10_12_c.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/diary/test_diary_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/privacy/test_privacy_audit_findings.py, tests/ui/test_ui_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Audit data and calculations 2026-10-12, package C (diary study): E-1, E-2, D-1 to D-7, D-10, D-12 to D-15 and
decision 10 (pause until the resume; a broken-off entry is always "abgebrochen").
"""
import os


from datetime import date, datetime, timedelta, timezone


from types import SimpleNamespace


from app import diary


from app.extensions import db


from app.models import Enrolment, Response, Survey, utcnow


from tests.diary.test_diary import API, _active_from_today, _bearer, _enrol, _entries_of, _new, _path, _prompt


def _activate(app, days_ago=0, length=14):
    with app.app_context():
        person = db.session.scalars(db.select(Enrolment).order_by(Enrolment.id.desc())).first()
        person.first_day = diary.local_today(person) - timedelta(days=days_ago)
        person.last_day = person.first_day + timedelta(days=length - 1)
        person.status = Enrolment.ACTIVE
        db.session.commit()
        return person.id


def _states(csv_text):
    lines = csv_text.splitlines()
    head = lines[0].lstrip("﻿").split(",")
    return [line.split(",")[head.index("abfrage")] for line in lines[1:]]


def _with_prompts(app, client, first_speeder=True):
    code = _new(app, client)
    _enrol(app, code)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        person = db.session.scalar(db.select(Enrolment))
        person.first_day = diary.local_today(person) - timedelta(days=2)
        person.last_day = person.first_day + timedelta(days=13)
        person.status = Enrolment.ACTIVE
        db.session.commit()
        prompts = list(diary.prompts(survey, person, until=utcnow() - timedelta(hours=3)))
        for n, (key, due) in enumerate(prompts):
            secs = 5 if n == 0 and first_speeder else 120
            db.session.add(Response(survey_id=survey.id, enrolment_id=person.id, status=Response.COMPLETED, answers={},
                                    pid=f"e-{n}", started_at=due, completed_at=due + timedelta(seconds=secs), due_at=due,
                                    source="real", occasion={"part": key, "due": due.isoformat(), "tz": person.tz}))
        db.session.commit()
        return prompts
