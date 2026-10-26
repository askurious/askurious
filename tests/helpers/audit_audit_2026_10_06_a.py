"""Helpers of the tests from test_audit_2026_10_06_a.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/interview/test_interview_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/privacy/test_privacy_audit_findings.py, tests/quotas/test_quotas_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Security audit 2026-10-06, package A "Rollen und Objekte": S1, S2, S9, S10, S12, S22, H5 and the side finding on
the short text question (items/text.py dropped `construct` on saving).
"""
import json


import os


import re


from werkzeug.datastructures import MultiDict


from app import flow, linked, undo


from app.extensions import db


from app.models import (AccessLog, Board, ConstructShare, EditStep, Item, Response, Survey, SurveyMember, User,
                        utcnow)


from tests.conftest import add, allow, experiments_on


from tests.editor.test_linked import _linked_question, choice


from tests.exports.test_transfer import _upload


OTHER = "0000-0002-1825-0097"


THIRD = "0000-0000-0000-0124"


D = "/surveys/1/dashboards"


def _person(app, orcid, name, may_create=False):
    if may_create:
        allow(app, orcid, keep=True)
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    c.post("/terms/", data={"accept": "1"})
    return c


def _member(app, survey_id, orcid, role):
    with app.app_context():
        s = db.session.get(Survey, survey_id)
        u = db.session.scalar(db.select(User).filter_by(orcid=orcid))
        s.members.append(SurveyMember(orcid=orcid, user_id=u.id if u else None, name=orcid, role=role,
                                      position=len(s.members)))
        db.session.commit()


def _own_survey(app, client, title):
    """A new draft of `client`; returns (id, its content block)."""
    client.post("/surveys/new", data={"title": title, "languages": ["de", "en"], "default_language": "de"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title=title))
        return s.id, next(b.id for b in s.blocks if not any(i.type == "consent" for i in b.items))


def _insert(app, client, sid, block, key):
    client.post(f"/surveys/{sid}/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        return db.session.scalar(db.select(Item.id).filter(Item.block_id == block).order_by(Item.id.desc()))


# routes under /export that hand out answers: "Ansehen" is refused (the arguments make the URL; get_survey comes first)
RAW = {
    "surveys.export_csv": {"kind": "data"},
    "surveys.export_sav": {}, "surveys.export_omv": {}, "surveys.export_rds": {}, "surveys.export_spss": {},
    "surveys.export_conjoint": {"item_id": 2}, "surveys.export_vignettes": {"nid": "x"}, "surveys.export_loop_long": {"nid": "x"},
    "surveys.export_voice": {}, "surveys.export_contacts": {},
    "surveys.snapshot_file": {"name": "a", "fname": "data.csv"},
    "surveys.seal_chain": {},
    "surveys.export_sem_scores": {},  # the PLS construct scores per participation (SEM extensions 2026-10-17)  # no answers, but when cases ended and went: as the data (app/seal.py)
    "coding.guide": {},  # the coding guide: its anchor examples may quote what someone typed (app/coding.py)
}


RAW_MORE = [("surveys.snapshot_file", {"name": "a", "fname": "long_q.csv"})]


# routes under /export that hand out answers but take other arguments than survey_id; their refusal to "Ansehen" is
# tested where they live (tests/interview/test_transcript_export.py)
RAW_ELSEWHERE = {"interview_transcribe.export"}


# routes under /export without answers, open to "Ansehen" (the questionnaire, its documentation, comments of test
# runs, the pre-registration without answers, the guide of an interview)
OPEN = {"surveys.export_data_flow", "surveys.export_file", "surveys.export_zip", "surveys.export_loop_table", "surveys.export_comments", "surveys.export_comments_zip",
        "surveys.export_constructs", "surveys.export_docx", "surveys.export_study_material", "surveys.export_references", "surveys.export_methods",
        "surveys.prereg_file", "dashboard.export", "interview.export", "interview.export_docx", "interview.export_qdc",
        "surveys.export_sem_r",  # the model of the study plan as an R script, no answers
        "surveys.export_results_pdf",  # the results report: aggregates as on the results page, no answers or free texts
        "surveys.prereg_stamp", "surveys.seal_checker",  # the timestamp of the pre-registration, the checker
        "surveys.seal_check"}  # a POST that checks the seal chain: for the editor role (EDIT_ENDPOINTS)


def _editor(app):
    _person(app, OTHER, "Editor")
    _member(app, 1, OTHER, "editor")
    return _person(app, OTHER, "Editor")
