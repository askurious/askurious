"""Helpers of the tests from test_audit_2026_10_06_d.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/platform/test_platform_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Security audit 2026-10-06, package D "Konten": the right to create surveys of invited people (S11), blocking ends
the sessions (S13), guessing the current password on "Konto" (S14), the admin's "no" next to a group (S17), the
people search (S23), site admins without password (H1), the second factor at every admin right (H2), the address
in Slack links (H3) and the "back" of the study plan (H4).
"""
from types import SimpleNamespace


import pytest


from flask_login import login_user


from app import limits, notify, passwords, twofactor


from app.extensions import db


from app.models import SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember


from tests.conftest import add, allow


from tests.accounts.test_passwords import GOOD, MAIL, guest  # noqa: F401  (guest: fixture)


ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)


OTHER = "0000-0002-1825-0097"


THIRD = "0000-0000-0000-0124"


@pytest.fixture(autouse=True)
def _no_failures_from_other_tests(monkeypatch):
    limits._fallback.clear()  # the counts per sender and account are in each test's own database (audit 2026-10-13)


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _user(app, orcid):
    with app.app_context():
        return db.session.scalar(db.select(User).filter_by(orcid=orcid))


def _may_create(app, orcid):
    from app.auth import may_create_surveys

    with app.test_request_context():
        return may_create_surveys(db.session.scalar(db.select(User).filter_by(orcid=orcid)))
