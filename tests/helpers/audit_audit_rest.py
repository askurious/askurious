"""Helpers of the tests from test_audit_rest.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/privacy/test_privacy_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Remaining fixes from the audit of 2026-10-01 (S5, S9, S12 to S15, S17, S18, D-2, D-6, D-7, D-9, D-12 and small ones).
"""
import re


from types import SimpleNamespace


import pytest


from werkzeug.datastructures import MultiDict


from app import accounts, export, loops, panel, results, runtime


from app.extensions import db


from app.items import get as kind_of


from app.items import psm, text


from app.models import Block, Item, Response, Survey


from tests.conftest import CONSENT_OK, add, fill_consent


E = "/surveys/1/editor"
