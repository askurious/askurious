"""Helpers of the tests from test_review_fixes.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/dashboards/test_dashboards_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/participation/test_participation_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

What the code review of 2026-10-02 found and what holds since (code-review-2026-10-02.md, S1 to S7).
"""
from datetime import timedelta


from werkzeug.datastructures import MultiDict


from app import boards, design, export, flow, loops, runtime


from app.extensions import db


from app.models import Block, Board, ContactEntry, Response, SplitSession, Survey, utcnow


from tests.dashboards import test_boards


from tests.editor import test_flow


from tests.editor import test_loops


from tests.quotas import test_split


from tests.conftest import CONSENT_OK, add, fill_consent, split_node


E = "/surveys/1/editor"


study = test_boards.study  # the fixture of the dashboard tests


def _start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING
        return f"/s/{s.code}"


def _state(app):
    with app.app_context():
        r = db.session.scalars(db.select(Response).filter_by(is_preview=False).order_by(Response.id)).first()
        return {"status": r.status, "position": r.position, "conditions": dict(r.layout["conditions"]),
                "loops": dict(r.layout["loops"]), "sig": r.layout["sig"], "language": r.language, "qversion": r.qversion}


def _consent_and_two_blocks(client):
    fill_consent(client)
    add(client, "text_input")                    # 2 in block 2
    client.post(E + "/blocks")                   # block 3
    add(client, "text_input", block=3)           # 3


def _errors(app):
    with app.app_context():
        return [f.message for f in design.check(db.session.get(Survey, 1)) if f.severity == "error"]


def _split_board(study):
    add(study, "single_choice")                                     # 7: the groups
    study.post(E + "/items/7", data=MultiDict([
        ("code", "g"), ("question", "Gruppe?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Alt"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Jung")]))
    test_boards._board(study, "empty")
    study.post(f"{test_boards.D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("view-item:2", "bars"),
                                                     ("n-item:2", "1"), ("by-item:2", "item:7")]))


def _split(app):
    with app.app_context():
        split = boards.build(db.session.get(Board, 1))["tiles"][0]["split"]
        return {g["label"]: dict(zip(split["cats"], shares)) for g, shares in zip(split["groups"], split["shares"])}


OTHER = "0000-0002-1825-0097"


def _member(app, role):
    from app.models import SurveyMember

    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, name="Vera", role=role, position=1))
        db.session.commit()
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Vera")
    other.post("/terms/", data={"accept": "1"})
    return other
