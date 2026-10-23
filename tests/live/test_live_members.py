"""Live votes have co-owners like surveys: invited by ORCID iD in their settings (surveys/_members.html)."""
from app.extensions import db
from app.models import Survey


def test_co_owners_are_invited_in_the_settings(app, client):
    client.post("/presenter/new", data={"title": "Gemeinsam"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(title="Gemeinsam"))
        code, sid = survey.code, survey.id
    page = client.get(f"/presenter/{code}/settings").data.decode()
    assert 'id="members"' in page and "Beteiligte" in page and f'action="/presenter/{code}/settings#members"' in page
    r = client.post(f"/surveys/{sid}/members", data={"orcid": "0000-0002-1825-0097", "name": "Josiah Carberry",
                                                   "role": "editor", "next": f"/presenter/{code}/settings#members"})
    assert r.headers["Location"].endswith(f"/presenter/{code}/settings#members")   # back to the live vote's settings
    assert "Josiah Carberry" in client.get(f"/presenter/{code}/settings").data.decode()
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Josiah")
    with app.app_context():
        assert any(s.title == "Gemeinsam" for s in __import__("app.live", fromlist=["mine"]).mine(
            db.session.scalar(db.select(__import__("app.models", fromlist=["User"]).User).filter_by(orcid="0000-0002-1825-0097"))))


def test_roles_are_told_in_the_words_of_a_live_vote(app, client):
    """V89: no "Fragebogen, Blöcke, Ablauf" on a live vote, and no second sentence about the roles above."""
    client.post("/presenter/new", data={"title": "Rollen"})
    with app.app_context():
        code = db.session.scalar(db.select(Survey).filter_by(title="Rollen")).code
    page = client.get(f"/presenter/{code}/settings").data.decode()
    assert "Fragen bearbeiten und präsentieren" in page and "Fragebogen, Blöcke, Ablauf" not in page
    assert "bearbeitet und präsentiert" not in page
