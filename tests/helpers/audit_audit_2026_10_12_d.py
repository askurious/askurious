"""Helpers of the tests from test_audit_2026_10_12_d.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/quotas/test_quotas_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Audit data and calculations 2026-10-12, package D (deletion, quotas, kept results): E-7 to E-11.
"""
import os


from tests.conftest import add


from app import erasure, lifecycle, quotas


from app.extensions import db


from app.models import Construct, ConstructCitation, Quota, QuotaCell, ResearchProject, Response, Survey, User
