"""Helpers of the tests from test_audit_2026_10_12_h.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Audit Daten und Berechnungen 2026-10-12, package H (SEM rest and R script): SEM-3 to SEM-7, SEM-9 to SEM-11,
decisions 8 and 9. Independently computed reference values on the same data, for PLS from an independent
implementation of Wold's algorithm (mode A, factor scheme). Decision 9 (planning by groups): test_plan_moderation.
"""
import math


import random


import numpy as np


from werkzeug.datastructures import MultiDict


from app import sem, sem_model, study_plan


from app.extensions import db


from app.models import Response, Survey


from tests.conftest import add


from tests.analysis.test_sem_survey import E, _answers, _scale


def _products(X, at, pairs):
    centred = X - X.mean(axis=0)
    out = []
    for a, b in pairs:
        v = centred[:, at[a]] * centred[:, at[b]]
        out.append(v - v.mean())
    return np.column_stack([X] + out)


def _fit(XP, pcols, spec):
    ordered = sem.columns(spec)
    res = sem.fit_ml(XP[:, [pcols.index(c) for c in ordered]], ordered, spec)
    return {"fit": sem_model._plain(res), "spec": spec}


def _groups_survey(app, client):
    for number, code, k in ((2, "tb", 4), (3, "einst", 3), (4, "absicht", 3)):
        _scale(client, number, code, k)
    add(client, "single_choice")
    client.post(f"{E}/items/5", data=MultiDict({"code": "grp", "question": "grp?", "option_id": ["a", "b"],
                                                "option_code": ["1", "2"], "option_label": ["G1", "G2"]}))
    with app.app_context():
        s = db.session.get(Survey, 1)
        rng = random.Random(4)
        for answers in _answers(rng, 300):
            answers["5"] = rng.choice(["1", "2"])
            if rng.random() < 0.05:
                answers["3"] = {k: v for k, v in answers["3"].items() if k != "s1"}
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers=answers))
        db.session.commit()
