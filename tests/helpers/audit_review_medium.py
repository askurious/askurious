"""Helpers of the tests from test_review_medium.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/dashboards/test_dashboards_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/platform/test_platform_audit_findings.py, tests/quotas/test_quotas_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

The medium findings of the code review of 2026-10-02 (code-review-2026-10-02.md, M5 to M16).
"""
import io


import json


from datetime import timedelta


import pytest


from sqlalchemy.orm.exc import StaleDataError


from werkzeug.datastructures import MultiDict


from app import boards, conditions, design, export, flow, runtime


from app.extensions import db


from app.models import Board, Item, Response, Survey, SurveyGroup, SurveyMember, WorkGroup, WorkGroupMember, utcnow


from tests.dashboards import test_boards


from tests.participation import test_participate


from tests.quotas import test_quotas


from tests.conftest import CONSENT_OK, add, fill_consent


from tests.exports.test_transfer import _upload as _read_in


E = "/surveys/1/editor"


ME = "0000-0002-1825-0097"      # the signed-in researcher of the fixtures (conftest)


OTHER = "0000-0000-0000-0124"


THIRD = "0000-0000-0000-0239"


study = test_boards.study


def _as(app, orcid, name):
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    other.post("/terms/", data={"accept": "1"})
    return other


def _member(app, role, orcid=OTHER, name="Vera"):
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=orcid, name=name, role=role, position=1))
        db.session.commit()
    return _as(app, orcid, name)


def _errors(app):
    with app.app_context():
        return [f.message for f in design.check(db.session.get(Survey, 1)) if f.severity == "error"]


def _answer(app, answers, n=1, **more):
    with app.app_context():
        now = utcnow()
        for _ in range(n):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers=answers,
                                    started_at=now - timedelta(minutes=5), completed_at=now, **more))
        db.session.commit()


def _group(app, client):
    with app.app_context():
        me = db.session.scalar(db.select(SurveyMember.orcid).filter_by(survey_id=1))
    client.post("/groups/new", data={"name": "Lehrstuhl"})
    with app.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id))
    client.post(f"/groups/{gid}/members", data={"orcid": OTHER, "name": "Kollegin", "role": "lead"})
    client.post("/surveys/1/access-groups", data={"group": gid, "role": "editor"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(SurveyGroup.id))) == 1
        mid = db.session.scalar(db.select(WorkGroupMember.id).filter_by(group_id=gid, orcid=me))
    return gid, mid


def _numbers(page):
    import re

    return re.findall(r'data-n="item:2">(\d+)<', page) or re.findall(r"(\d+) Antworten", page)


def _overlap(app, form_of):
    """Two requests of one participation that overlap: B has read the participation, A runs and stores, B goes on
    with what it read and is refused; nothing of B is kept."""
    b = app.app_context()
    b.push()
    survey_b = db.session.get(Survey, 1)
    rb = db.session.scalar(db.select(Response).filter_by(source="real"))
    _ = (rb.position, rb.quota, rb.answers, rb.layout, rb.status)
    form_b = form_of(rb)
    a = app.app_context()
    a.push()
    ra = db.session.scalar(db.select(Response).filter_by(source="real"))
    runtime.step(db.session.get(Survey, 1), ra, form_of(ra), "de", "POST", params={})
    db.session.commit()
    a.pop()
    try:
        with pytest.raises(StaleDataError):  # B stores second: refused, at its first write or at the latest at its commit
            runtime.step(survey_b, rb, form_b, "de", "POST", params={})
            db.session.commit()
    finally:
        db.session.rollback()
        b.pop()


def _loop_around_block_2(app):
    with app.app_context():
        s = db.session.get(Survey, 1)
        rest = [n for n in flow.tree(s)["children"] if not (n["t"] == "block" and n["block"] in (1, 2))]
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1},
            {"t": "loop", "id": "l1", "name": "S", "code": "loop1", "columns": [{"id": "c1", "name": "x", "hidden": False}],
             "rows": [{"id": "r1", "values": {"c1": {"de": "a"}}}, {"id": "r2", "values": {"c1": {"de": "b"}}}],
             "mode": "all", "k": 1, "order": "fixed", "balanced": True, "children": [{"t": "block", "block": 2}]}] + rest}
        flow.normalize(s)
        db.session.commit()


def _with_parameter(app, client, tree):
    fill_consent(client)
    add(client, "text_input")                                       # 2 in block 2
    client.post(E + "/items/2", data={"code": "age", "validation": "integer", "question": "Alter"})
    client.post(E + "/blocks")                                       # block 3
    add(client, "info", block=3)                                     # 3
    client.post(E + "/items/3", data={"code": "intro", "question": "Hinweis für das Panel"})
    client.post("/surveys/1/panel", data=MultiDict([("param_name", "grp"), ("param_mode", "redirect"),
                                                    ("param_required", "0"), ("param_allowed", "")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": tree}
        flow.normalize(s)
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING
        return f"/s/{s.code}"


def _file(client):
    return json.loads(client.get("/surveys/1/export/survey.json?files=0").data)


def _upload(client, data, title="Aus Datei"):
    return _read_in(client, data, title=title)
