"""Roles on a live vote: who changes its title and language, who sees single earlier runs (Abgleich 2026-10-15,
AZ-62 and the live filter "durchgang" next to AZ-1)."""
from app.extensions import db
from app.models import Survey, SurveyMember
from tests.conftest import allow
from tests.dashboards.test_showcase import OTHER, _as
from tests.live.test_live import add, make, steer


def _member(app, sid, role):
    allow(app, OTHER, keep=True)
    other = _as(app, OTHER, "Mitarbeit")
    with app.app_context():
        db.session.add(SurveyMember(survey_id=sid, orcid=OTHER, role=role))
        db.session.commit()
    return other


def test_title_and_language_need_admin_the_look_stays_with_edit(app, client):
    """AZ-62: title and language are settings, as surveys.update_meta; "Bearbeiten" keeps the other parts."""
    code, sid, _block = make(app, client)
    editor = _member(app, sid, "editor")
    editor.post(f"/presenter/{code}/settings", data={"part": "titel", "title": "Übernommen"})
    with app.app_context():
        assert db.session.get(Survey, sid).title == "Ringvorlesung"  # refused (get_survey: back to the list)
    page = editor.get(f"/presenter/{code}/settings").get_data(as_text=True)
    assert "Titel und Sprache ändern nur Admins der Abstimmung." in page
    assert editor.post(f"/presenter/{code}/settings", data={"part": "teilnahme"}).status_code == 302
    client.post(f"/presenter/{code}/settings", data={"part": "titel", "title": "Neu", "language": "de"})
    with app.app_context():
        assert db.session.get(Survey, sid).title == "Neu"


def test_viewers_see_only_the_current_run_on_the_slides(app, client):
    """Live filter "durchgang": an earlier run alone or two side by side only from "Bearbeiten" up, as on the
    results page (AZ-1)."""
    code, sid, block = make(app, client)
    add(app, client, sid, block, "rating", question="Wie war es?")
    old = steer(client, code, action="new_run", name="Runde 2")["runs"][0]["id"]
    viewer = _member(app, sid, "viewer")
    assert viewer.get(f"/presenter/{code}/slides").status_code == 200
    assert viewer.get(f"/presenter/{code}/slides?durchgang={old}").status_code == 403
    assert viewer.get(f"/presenter/{code}/slides?vergleich={old}").status_code == 403
    assert 'name="durchgang"' not in viewer.get(f"/presenter/{code}/slides").get_data(as_text=True)
    assert client.get(f"/presenter/{code}/slides?durchgang={old}").status_code == 200
