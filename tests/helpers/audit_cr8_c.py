"""Helpers of the tests from test_cr8_c.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/interview/test_interview_audit_findings.py, tests/live/test_live_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/platform/test_platform_audit_findings.py, tests/privacy/test_privacy_audit_findings.py, tests/quotas/test_quotas_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Code review 2026-10-07, package C: counters and changes at the same moment (D3, D4, T2, K11, T7, R7, F8, F13, G7,
I7). A change "at the same moment" is made by another connection while the request is between reading and writing
(a hook on a function the request calls there); on SQLite that connection may write as long as the request has not
begun to write itself.
"""
import json


import pathlib


import pytest


from sqlalchemy import event, text


from app import ai, quotas


from app.extensions import db


from app.models import Block, ContactEntry, Item, LiveQuestion, PreviewComment, ResearchProject, SplitSession, Survey


from tests.conftest import CONSENT_OK, PG_URL, add, experiments_on


STATIC = pathlib.Path(__file__).resolve().parents[2] / "app" / "static"


needs_pg = pytest.mark.skipif(not PG_URL, reason="about PostgreSQL's xmin (TEST_DATABASE_URL)")


def _other(app, statement, **params):
    """Another request (another process) writes and commits."""
    with app.app_context(), db.engine.begin() as con:
        con.execute(text(statement), params)


def _entries(app):
    from app import contact

    with app.app_context():
        return contact.count(db.session.get(Survey, 1))


TURN = {"reply": "Gut.", "canvas": {k: None for k in ("frage", "design", "stichprobe", "hypothesen", "konstrukte", "analyse")},
        "focus": "", "name": None}


def _fake(monkeypatch, answer):
    def fake(instructions, prompt, purpose="text", item_type=None, schema=None):
        return answer(purpose) if callable(answer) else answer

    monkeypatch.setattr(ai, "generate_text", fake)


def _new_project(app, client, monkeypatch):
    experiments_on(client, "ainstein")
    _fake(monkeypatch, json.dumps({**TURN, "canvas": {**TURN["canvas"], "frage": "Vorschlag der KI"}}))
    client.post("/ainstein/new", data={"name": "P", "idea": "eine Idee mit genug Woertern fuer den Assistenten zum Testen hier"})
    with app.app_context():
        return db.session.scalar(db.select(ResearchProject.code))
