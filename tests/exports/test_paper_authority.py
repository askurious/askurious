"""Audit 2026-10-13 AZ-30: editors may discard unused paper, not recorded answers."""
from app.extensions import db
from app.models import PaperSheet, SurveyMember, AccessLog
from tests.conftest import allow
from tests.exports.test_paper import _setup, _run, _sheets


def test_editor_cannot_discard_a_scanned_sheet(app, client, survey):
    _setup(app, client)
    _run(client, n=1)
    sid = _sheets(app)[0]["id"]
    other = "0000-0002-1825-0097"
    allow(app, other, keep=True)
    editor = app.test_client()
    editor.get(f"/auth/dev-login?orcid={other}&name=Editor")
    editor.post("/terms/", data={"accept": "1"})
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=other, role="editor", position=1))
        sheet = db.session.get(PaperSheet, sid)
        sheet.state, sheet.values = "scanned", {"1": "answer"}
        db.session.commit()
    assert editor.post(f"/surveys/1/paper/sheets/{sid}/discard").status_code == 403
    with app.app_context():
        sheet = db.session.get(PaperSheet, sid)
        assert sheet.state == "scanned" and sheet.values == {"1": "answer"}
    assert client.post(f"/surveys/1/paper/sheets/{sid}/discard").status_code == 302
    with app.app_context():
        assert db.session.get(PaperSheet, sid).state == "discarded"
        assert db.session.scalar(db.select(AccessLog).where(AccessLog.action == "Papierbogen verworfen"))


def test_only_admins_clear_the_destroy_note(app, client, survey):
    """Code review 2026-10-15, J4: "Papier vernichten" is a deletion duty; an editor cannot click it away."""
    _setup(app, client)
    _run(client, n=1)
    sid = _sheets(app)[0]["id"]
    other = "0000-0002-1825-0097"
    allow(app, other, keep=True)
    editor = app.test_client()
    editor.get(f"/auth/dev-login?orcid={other}&name=Editor")
    editor.post("/terms/", data={"accept": "1"})
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=other, role="editor", position=1))
        db.session.get(PaperSheet, sid).destroy = True
        db.session.commit()
    assert "/destroyed" not in editor.get("/surveys/1/field").get_data(as_text=True)
    editor.post(f"/surveys/1/paper/sheets/{sid}/destroyed")
    with app.app_context():
        assert db.session.get(PaperSheet, sid).destroy
    assert "/destroyed" in client.get("/surveys/1/field").get_data(as_text=True)
    client.post(f"/surveys/1/paper/sheets/{sid}/destroyed")
    with app.app_context():
        assert not db.session.get(PaperSheet, sid).destroy
