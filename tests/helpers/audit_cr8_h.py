"""Helpers of the tests from test_cr8_h.py; the tests themselves are in tests/diary/test_diary_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/interview/test_interview_audit_findings.py, tests/live/test_live_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/platform/test_platform_audit_findings.py, tests/ui/test_ui_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Code review 2026-10-07, package H (AI calls and the external signature): I1 no SDK retries and limits below the
proxy, I2 the question line of an AI scale per language, I3 answers of unexpected types, I5 NaN/Infinity in JSON,
I6 raw line breaks in translation, live vote and scale answers, I8 version 2 of the signed return values, I9 BibTeX
names of organisations.
"""
from datetime import date, datetime


from urllib.parse import quote


import pytest


from app import ai, external


from app.extensions import db


from app.models import Response, Survey


def _fake(monkeypatch, answer):
    def fake(instructions, prompt, purpose="text", item_type=None, schema=None):
        return answer(purpose) if callable(answer) else answer

    monkeypatch.setattr(ai, "generate_text", fake)


NODE = {"values": [{"name": "a", "kind": "text"}, {"name": "b", "kind": "text"}]}


def _return_flow(app, client):
    from tests.editor.test_external import build, leave, start, to_the_section
    from tests.platform.test_security_keys import _key, _sign_on, _sig, _token

    nid = build(app, client)
    _sign_on(client, nid)
    key = _key(app)
    uid = start(app, client)

    def go(query):
        p = to_the_section(app, uid)
        _, back = leave(p, uid)
        assert p.get(back + query(_token(back))).status_code == 302
        with app.app_context():
            last = db.session.scalars(db.select(Response).filter_by(source="real").order_by(Response.id.desc())).first()
            return last.external[nid]

    return key, go, _sig
