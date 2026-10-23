"""Helpers of the tests from test_audit_2026_10_06_e.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/participation/test_participation_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Security audit 2026-10-06, package E: researcher texts of the delay discounting stay inside their data block (S3),
no-store for downloads of signed-in people and for every participant page (S15), and the gaps of the access log
(S16).
"""
import json


import os


import re


from app import audit


from app.extensions import db


from app.models import AccessLog, Item, Survey


from tests.conftest import CONSENT_OK


from tests.analysis.test_discounting import _dd


from tests.accounts.test_groups import _add, _group, _member_id


from tests.accounts.test_passwords import GOOD, _create, _sign_in, guest  # noqa: F401  (fixture)


from tests.exports.test_prereg import _start


OTHER = "0000-0002-1825-0097"


ESCAPE = "</script><form action=https://evil.example/x method=post>"


def _log(app):
    with app.app_context():
        return [(r.action, r.detail) for r in db.session.scalars(db.select(AccessLog).order_by(AccessLog.id))]
