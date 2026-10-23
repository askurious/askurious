"""Helpers of the tests from test_audit_2026_10_06_g.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/participation/test_participation_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Audit 2026-10-06, package G: performance of the database and of taking part. P3 (index for the counts of
participations), P5 (once-only IDs in a table of their own with a UNIQUE index), P8 (start page without a query per
card, counts once per request), P4 (piping works a value out only when a text asks for it), P7 (a limit of
estimations for the whole server), P11 (the signature once per step, the README counts in the database).
"""
import os


import subprocess


import sys


import time


from alembic import command


from sqlalchemy import event, inspect


from app import maxdiff_hb, migrations, panel, piping, runtime


from app.extensions import db


from app.models import OnceValue, Response, Survey, once_hash


from tests.conftest import CONSENT_OK, needs_sqlite


from tests.participation.test_panel_once import USED, once_survey


def _statements(app):
    """A list that collects every SQL statement sent while it is attached."""
    seen = []

    def note(conn, cursor, statement, *args):
        seen.append(" ".join(statement.split()))
    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", note)
    return seen, lambda: event.remove(engine, "before_cursor_execute", note)


def _rows(app):
    with app.app_context():
        return sorted((r.name, r.value_hash, r.once) for r in db.session.scalars(db.select(OnceValue)))
