"""Helpers of the tests from test_audit_2026_10_12_a.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/dashboards/test_dashboards_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/participation/test_participation_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Audit Daten und Berechnungen 2026-10-12, package A (crashes and wrong figures): S-1, S-3, W-1, M-1, SEM-1, SEM-2,
SEM-8. The repro tests of the audit, turned round: each checks the right behaviour.
"""
import random


from datetime import timedelta


from werkzeug.datastructures import MultiDict


from app import boards, export, loops, quality, results, sem_model, study_plan


from app import vignette_positions as vp


from app.extensions import db


from app.models import Block, Item, Response, Survey, User, utcnow


from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions, fill_table


from tests.analysis.test_sem_survey import _answers, _scale


E = "/surveys/1/editor"


def _likert(client, item_id, code, n=6):
    data = [("code", code), ("question", "Wie sehr?"), ("points", "5"), ("labeling", "full"), ("level", "metric"),
            ("dont_know", "1")]
    for k in range(n):
        data += [("stmt_id", f"s{k}"), ("stmt_code", ""), ("stmt_text", f"Aussage {k}")]
    client.post(f"{E}/items/{item_id}", data=MultiDict(data))


def _subscales(app, item_id, names):
    with app.app_context():
        item = db.session.get(Item, item_id)
        cfg = dict(item.config)
        cfg["statements"] = [{**st, "subscale": name} for st, name in zip(cfg["statements"], names)]
        item.config = cfg
        db.session.commit()
