"""Helpers of the tests from test_review2_reste.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/live/test_live_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/platform/test_platform_audit_findings.py, tests/ui/test_ui_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Second code review 2026-10-03, package 7 (the rest): no inline scripts and handlers, so the CSP allows scripts
only as files (U-6); dead live CSS gone (D-8); the preview tools in the survey's language (U-8); comment names
go with the account by its id (K-8).
"""
import glob


import os


import re


from app import accounts


from app.extensions import db


from app.models import PreviewComment, Survey, User


from tests.conftest import add


from tests.editor.test_undo import colleague


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


E = "/surveys/1/editor"


OTHER = "0000-0002-1825-0097"    # tests.editor.test_undo.colleague, "Kollegin"


QUOTA_STATE = "/surveys/1/flow/quotas/state"


HANDLER = re.compile(r"<[a-zA-Z][^>]*\son[a-z]+\s*=", re.S)


SCRIPT = re.compile(r"<script\b([^>]*)>", re.I)


def _comment(c, text):
    r = c.post(f"{E}/preview/comment", data={"block_id": "2", "text": text})
    assert r.status_code == 200
    return r.json["id"]
