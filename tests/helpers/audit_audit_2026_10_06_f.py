"""Helpers of the tests from test_audit_2026_10_06_f.py; the tests themselves are in tests/analysis/test_analysis_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/ui/test_ui_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Audit 2026-10-06 (security and performance), package F: performance of the evaluation pages.

P1   the omega bootstrap is no part of results.compute(): the results page shows "wird berechnet" and fetches the
     intervals as a part of their own (omega_part), kept under the fingerprint; the same numbers as before; the Word
     table of the scales and the pooled constructs draw them when they are not kept yet
P2   the quality values of a participation are computed once per request (export, method section, HB checks)
P6   the stored HB result and the table of a data pool are read once per request; the scenario the market simulator
     opens with is kept with the results
P9   PSM curves and their table show at most 300 prices; the price points come from all of them
P10  while another request computes the first state of a dashboard, the page says so instead of computing it again
"""
import os


import re


from datetime import datetime, timedelta


import numpy as np


import pytest


from werkzeug.datastructures import MultiDict


from app import boards, export, intervals, maxdiff_hb, psm_analysis, quality, results, results_store, simulator


from app.extensions import db


from app.models import Board, Item, Response, Survey


from app.units import number


from tests.conftest import add


FETCH = {"X-Requested-With": "fetch"}


WAIT = "[wird berechnet …]"


def _likert(app, client, rows, code="TA"):
    """A metric Likert item (2) with one statement per column of rows, one completed participation per row."""
    form = [("code", code), ("points", "5"), ("labeling", "full"), ("level", "metric")]
    for j in range(len(rows[0])):
        form += [("stmt_id", f"s{j + 1}"), ("stmt_code", ""), ("stmt_text", f"Aussage {j + 1}")]
    add(client, "likert")
    client.post("/surveys/1/editor/items/2", data=MultiDict(form))
    client.post("/surveys/1/pool", data={"source": "real"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for i, row in enumerate(rows):
            s.responses.append(Response(source="real", status=Response.COMPLETED, started_at=t0,
                                        completed_at=t0 + timedelta(seconds=300 + i),
                                        answers={"2": {f"s{j + 1}": v for j, v in enumerate(row)}}))
        db.session.commit()


def _scale_rows(n=60, k=4, seed=3, weak=False):
    rng = np.random.default_rng(seed)
    f = rng.normal(size=n)
    rows = [[int(min(5, max(1, round(3 + x + rng.normal(scale=.8))))) for _ in range(k)] for x in f]
    if weak:  # a statement that runs against the others: many draws without an omega
        rows = [r[:-1] + [int(rng.integers(1, 6))] for r in rows]
    return rows


@pytest.fixture
def draws(monkeypatch):
    """How often the omega bootstrap ran."""
    calls = []
    boot = intervals.omega_boot
    monkeypatch.setattr(intervals, "omega_boot", lambda *a, **k: calls.append(1) or boot(*a, **k))
    return calls


def _as_the_script_does(page, part):
    """static/omega.js: every placeholder replaced by the template of the same slot."""
    found = dict(re.findall(r'<template data-omega-for="([^"]*)">(.*?)</template>', part, re.S))
    return re.sub(r'<span (?:class="res-ci res-wait" )?data-omega-slot="([^"]*)"[^>]*>[^<]*</span>',
                  lambda m: found[m.group(1).replace("&#34;", '"')], page)


def _quiet(client):
    """Show the messages of the set-up on another page, so they are not part of the compared ones."""
    client.get("/surveys/1")


def _body(html):
    """The page without the line that says when it was computed and without the CSRF tokens."""
    html = re.sub(r'<p class="muted small res-state-more".*?</p>', "", html, flags=re.S)  # when it was computed (S6)
    return re.sub(r'(csrf[-_]token"? (?:content|value)=")[^"]*', r"", html)


def _pool(app):
    from app.surveys import stored_pool

    with app.app_context():
        return stored_pool(db.session.get(Survey, 1), drop=True)
