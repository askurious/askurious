"""Viewers and the links per value; who enters publications (Abgleich 2026-10-15: B-2 and the side finding on roles
of surveys.publications)."""
from app.extensions import db
from app.models import SurveyMember
from tests.conftest import allow
from tests.dashboards.test_showcase import OTHER, _as


def _with(app, role):
    allow(app, OTHER)
    other = _as(app, OTHER, "Mitarbeit")
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, role=role))
        db.session.commit()
    return other


def test_viewers_see_counts_but_not_the_links_per_value(app, client, survey):
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna\nBert"})
    assert "w=" in client.get("/surveys/1/field").get_data(as_text=True)
    viewer = _with(app, "viewer")
    page = viewer.get("/surveys/1/field").get_data(as_text=True)
    assert "?w=" not in page and "&amp;w=" not in page and "value-links.csv" not in page
    assert viewer.get("/surveys/1/value-links.csv").status_code != 200
    assert viewer.get("/surveys/1/value-links/cards").status_code != 200
    assert viewer.get("/surveys/1/qr?name=w&value=x").status_code != 200
    assert viewer.get("/surveys/1/qr").status_code == 200  # the link for everyone stays
    with app.app_context():
        db.session.execute(db.update(SurveyMember).where(SurveyMember.orcid == OTHER).values(role="editor"))
        db.session.commit()
    assert viewer.get("/surveys/1/value-links.csv").status_code == 200


def test_edit_role_enters_publications(app, client, survey, monkeypatch):
    from app import citations

    monkeypatch.setattr(citations, "add_for_survey", lambda survey, doi, user: 1)
    editor = _with(app, "editor")
    r = editor.post("/surveys/1/publications", data={"doi": "10.1037/0022-3514.84.4.822"})
    assert r.status_code == 302 and r.location.endswith("/surveys/1/data#publications")  # before: only admins
