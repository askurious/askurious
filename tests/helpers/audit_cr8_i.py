"""Helpers of the tests from test_cr8_i.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/items/test_items_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/platform/test_platform_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Code review 2026-10-07, package I "Kleinere Fehler": R6, E3, E5, E6, E12, K5, K6, K7, T6, G11.
"""
import os


import pytest


from app import linked


from app.extensions import db


from app.models import Item, ResearchProject, Response, Survey, TrashedItem, User


from tests.conftest import add, fill_questions


ME = "0000-0000-0000-001X"


OTHER = "0000-0002-1825-0097"


def _linked_question(app, client):
    add(client, "single_choice")  # 2
    client.post("/surveys/1/editor/items/2", data={"code": "q", "question": "Q?", "answer_mode": "optional",
                                                  "option_id": ["a", "b"], "option_code": ["1", "2"],
                                                  "option_label": ["A", "B"]})
    client.post("/surveys/1/editor/items/2/link", data={"name": "Q"})
    with app.app_context():
        key = linked.key_of(db.session.get(Item, 2))
    assert key
    return key


TYPES = ("rating", "slider", "iat", "psm", "acbc", "assign", "map_marker", "likert", "semantic")
