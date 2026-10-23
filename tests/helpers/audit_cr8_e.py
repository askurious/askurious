"""Helpers of the tests from test_cr8_e.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/interview/test_interview_audit_findings.py, tests/items/test_items_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/platform/test_platform_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Code review 2026-10-07, package E: computing time of results and exports.

R3  a cold results page with hypotheses reads the data pool once (not again for the hypotheses and the model)
R4  export.rows prepares per element what does not depend on the answer (items.base.export_plan); the table is the
    same to the byte as without it
R5  "Markierte einbeziehen" and the group comparisons keep their results in files of their own
T4  the balance counts are read only when a draw asks for them, only the parts of the layouts they need, and a
    decline takes its counts away instead of dropping the table
T5  the marked participations of the live comparison are kept as long as what they rest on is the same
D5  the pre-launch check reads the AI images of the survey in one query
I10 the word counts of the transcript search are kept per transcript; the transcripts are read once per request
I14 the construct page reads constructs.usage once, with block and survey
F3  only the settings page draws the survey title anew after a save
F4  a POST of the editor and flow scripts (X-Navigate) gets {"location": ...} instead of the redirect
F9  map scripts once per results page; participant.js not on the researchers' pages, richtext.js only where needed
"""
import os


import random


import re


from contextlib import nullcontext


from sqlalchemy import event


from app.extensions import db


from app.models import Item, Response, Survey


from tests.conftest import CONSENT_OK, add, fill_consent


def _hypothesis(app):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.study_plan = {"hypotheses": [{"id": "h1", "text": "Nutzen wirkt", "kind": "causal", "iv": "bed", "dv": "TA"}]}
        db.session.commit()


def _count_rows(monkeypatch):
    from app import export

    calls = []
    real = export.rows
    monkeypatch.setattr(export, "rows", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    return calls


def _every_type(app, client, n=40):
    from app import dummy
    from tests.exports.test_transfer import _everything

    _everything(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        dummy.generate(s, n, seed=3)
        db.session.execute(db.update(Response).values(source="real"))
        db.session.commit()


def _running(app, client):
    from tests.participation.test_participate import build, uid

    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    return f"/s/{uid(app)}"


def _asset_selects(app, client, url):
    statements = []

    def note(conn, cursor, statement, *args):
        statements.append(" ".join(statement.split()).lower())

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", note)
    try:
        assert client.get(url).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", note)
    return [q for q in statements if q.startswith("select") and " from asset" in q]


def _last_item(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.max(Item.id)))
