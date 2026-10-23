"""Helpers of the tests from test_audit13_rest.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/interview/test_interview_audit_findings.py, tests/items/test_items_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/privacy/test_privacy_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Audit Daten und Berechnungen 2026-10-12, package I (exports, choice models, elements, rest): E-3, E-4, E-5, E-6,
W-2, W-3, W-4, N-11, N-12, M-5, M-6, M-8, M-9, M-10, M-12.
"""
import json


import random


from datetime import timedelta


from types import SimpleNamespace


import numpy as np


from werkzeug.datastructures import MultiDict


from app.extensions import db


from app.items import get as kind_of


from app.models import Response, Survey, User, utcnow


from tests.conftest import add


class Fake(SimpleNamespace):
    """A stand-in for models.Item."""

    def __init__(self, type_, code, config, texts=None):
        super().__init__(type=type_, code=code, config=config, cfg=config, texts=texts or {"de": {}}, kind=kind_of(type_),
                         block=None, id=99)

    def t(self, lang=None):
        return (self.texts or {}).get(lang or "de") or {}

    def text(self, key, lang=None):
        return self.t(lang).get(key)
