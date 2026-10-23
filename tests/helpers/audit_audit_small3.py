"""Helpers of the tests from test_audit_small3.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/items/test_items_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/platform/test_platform_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Small things from the audit of 2026-10-01 (package 7): patterns that keep a server busy, a content policy,
escaping in two places, a limit for feedback.
"""
import re


import time


import pytest


from werkzeug.datastructures import MultiDict


from app.extensions import db


from app.models import Feedback, Survey, User


from tests.conftest import CONSENT_OK, add, fill_consent


E = "/surveys/1/editor"
