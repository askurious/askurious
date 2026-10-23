"""Helpers of the tests from test_audit_2026_10_06_b.py; the tests themselves are in tests/editor/test_editor_audit_findings.py, tests/live/test_live_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/platform/test_platform_audit_findings.py, tests/privacy/test_privacy_audit_findings.py, tests/quotas/test_quotas_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Package B of the security and performance audit of 2026-10-06: limits for the public pages.

S4  a sender is an IPv6 /64 (an IPv4 address written as IPv6 is the IPv4 address)
S5  the caps per survey and per live run report instead of refusing; a narrow limit per sender and survey (run)
S18 deleting "here" is limited per sender (kept results: every real deletion drops them since 2026-10-12, E-8)
S19 wrong codes count on every public code address; a draft shows a neutral page
S20 a note when the running places of a quota cell rise by leaps
S21 live quiz in blocks: the answer time runs from the start of the block
"""
import os


import re


import time


import pytest


from app import limits


from app.extensions import db


from app.models import Quota, QuotaCell, Response, Survey, utcnow


from tests.conftest import CONSENT_OK, add, fill_consent


@pytest.fixture(autouse=True)
def _fresh_limits():
    # wrong codes and deletion codes count in each test's own database since the audit 2026-10-13 (TN-2)
    for store in (limits._asked, limits._load_fallback):
        store.clear()
    yield


def _running(app, client):
    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert survey.status == Survey.RUNNING
        return survey.code


def _start(app, code, addr=None):
    p = app.test_client()
    env = {"REMOTE_ADDR": addr} if addr else {}
    p.get(f"/s/{code}", environ_base=env)
    return p, p.post(f"/s/{code}", data=CONSENT_OK, environ_base=env)


def _kept_files(app):
    with app.app_context():
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", db.session.get(Survey, 1).uid)
    os.makedirs(folder, exist_ok=True)
    for name in ("results.bin", "board-1.json"):
        with open(os.path.join(folder, name), "w") as f:
            f.write("x")
    return folder
