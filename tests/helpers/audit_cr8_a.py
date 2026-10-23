"""Helpers of the tests from test_cr8_a.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/dashboards/test_dashboards_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/live/test_live_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/platform/test_platform_audit_findings.py, tests/privacy/test_privacy_audit_findings.py, tests/quotas/test_quotas_audit_findings.py, tests/ui/test_ui_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Code review 2026-10-07, package A "Falsche oder verlorene Daten": regression tests for T1, E1, E2, E8, E9, E10,
E11, F21, G1, K1, R1 and R2 (code-review-2026-10-07.md).
"""
import random


from app import flow, loops, vignettes


from app.extensions import db


from app.models import Survey


from tests.conftest import CONSENT_OK, _db_template, app, client, survey  # noqa: F401


def _loops(app, sid):
    with app.app_context():
        s = db.session.get(Survey, sid)
        return [n for n, _, _ in flow.walk(flow.tree(s)) if n["t"] == "loop"]


def _attention_run(app, client, cond):
    from app.models import Response
    from tests.analysis.test_quality import exclusion_survey

    uid = exclusion_survey(app, client, cond=cond)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "3", "i2_s2": "1"})  # the attention check failed
    p.get(f"/s/{uid}", follow_redirects=True)
    with app.app_context():
        return db.session.scalar(db.select(Response.status).filter_by(source="real"))


def _vignette_survey(app, client):
    from tests.analysis.test_vignettes import FORM, _add, _node

    _add(client)
    node = _node(app)
    page = f"/surveys/1/flow/vignettes/{node['id']}"
    client.post(page, data=FORM)
    client.post(page, data={"action": "generate"})
    return page


def _interview(app, client):
    from app import interview
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, sid = make(app, client)
    rid = start(app, client, code)
    with app.app_context():
        survey = db.session.get(Survey, sid)
        age = next(i for i in interview.shown_form(survey, "short").items if i.type == "text_input")
        post = next(i for i in interview.shown_form(survey, "post").items
                    if i.kind.collects_answer and i.type in ("text_input", "text_area"))
        return code, rid, age.id, post.id


OPTS = {"option_id": ["a", "b"], "option_code": ["1", "2"], "option_label": ["A", "B"], "answer_mode": "optional", "code": "q"}


def _question(app, iid):
    from app.models import Item

    with app.app_context():
        return (db.session.get(Item, iid).texts or {}).get("de", {}).get("question")


def _linked_in_two(app, client):
    from app import linked
    from app.models import Item
    from tests.conftest import add

    add(client, "single_choice")  # 2 in X
    client.post("/surveys/1/editor/items/2", data={**OPTS, "question": "Alte Fassung?"})
    client.post("/surveys/1/editor/items/2/link", data={"name": "Q"})
    with app.app_context():
        key = linked.key_of(db.session.get(Item, 2))
    client.post("/surveys/new", data={"title": "Y", "languages": ["de", "en"], "default_language": "de"})
    with app.app_context():
        s2 = db.session.get(Survey, 2)
        bid = [b.id for b in s2.blocks if not any(i.type == "consent" for i in b.items)][0]
    client.post(f"/surveys/2/editor/blocks/{bid}/items", data={"type": "construct:" + key})
    with app.app_context():
        y = max(i.id for i in db.session.scalars(db.select(Item)))
    client.post("/surveys/1/editor/items/2/delete")  # X: into the bin
    client.post(f"/surveys/2/editor/items/{y}", data={**OPTS, "question": "Neue Fassung?"})  # Y: new wording
    assert _question(app, y) == "Neue Fassung?"
    return y
