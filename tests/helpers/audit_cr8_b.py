"""Helpers of the tests from test_cr8_b.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/interview/test_interview_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/platform/test_platform_audit_findings.py, tests/privacy/test_privacy_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Code review 2026-10-07, package B: SQLite write locks and housekeeping (K2/D1, K3/D2, K4, E4, K8, T8/D6, K9).
"""
import os


import sqlite3


from datetime import timedelta


import pytest


from sqlalchemy import event


from app import lifecycle, migrations, retention


from app.extensions import db


from app.models import ContactEntry, OnceValue, Response, Survey, utcnow


from tests.conftest import CONSENT_OK, _config, add, fill_consent, fill_questions, needs_sqlite


class Statements:
    """The SQL the engine sends while the block runs."""

    def __init__(self):
        self.seen = []

    def __enter__(self):
        event.listen(db.engine, "before_cursor_execute", self._add)
        return self

    def __exit__(self, *exc):
        event.remove(db.engine, "before_cursor_execute", self._add)

    def _add(self, conn, cursor, statement, params, context, many):
        self.seen.append(" ".join(statement.split()))


def _taken(app, client, n=3):
    """Survey 1 started with n participations that hold an identifier from the link."""
    fill_consent(client)
    add(client, "text_input")
    fill_questions(app)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    for i in range(n):
        p = app.test_client()
        p.get("/s/1")
        p.post("/s/1", data=CONSENT_OK)
    with app.app_context():
        for r in db.session.scalars(db.select(Response).filter_by(survey_id=1)):
            r.params = {"pid": f"panel-{r.id}"}
        db.session.commit()
