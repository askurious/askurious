"""Helpers of the tests from test_audit_small2.py; the tests themselves are in tests/analysis/test_analysis_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/items/test_items_audit_findings.py, tests/quotas/test_quotas_audit_findings.py, tests/ui/test_ui_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Small fixes from the audit of 2026-10-01 (S4, S6, S7, S8, S10, S11, D-1, D-3, D-4).
"""
from types import SimpleNamespace


import pytest


from app import design, export, psm_analysis, results


from app.extensions import db


from app.items import get as kind_of


from app.items import postal_code, psm, text


from app.models import Item, Survey


from tests.conftest import add


def _errors(app):
    with app.app_context():
        return [f.message for f in design.check(db.session.get(Survey, 1)) if f.severity == design.ERROR]
