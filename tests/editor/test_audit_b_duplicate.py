"""Audit 2026-10-05, L-12: a duplicate takes the contact address of the consent or of a live vote's privacy page
only for an admin of the original, and says so otherwise."""
from app import duplicate as dup
from app.extensions import db
from app.models import Survey, SurveyMember, User, UserLogin
from tests.conftest import fill_consent
from tests.editor.test_duplicate import ME, OTHER, _as
from tests.conftest import CREATE_ALL


def _editor(app, client, sid=1):
    client.post(f"/surveys/{sid}/members", data={"orcid": OTHER, "name": "Kollegin", "role": "editor"})
    _as(app.test_client(), OTHER, "Kollegin")
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        db.session.add(UserLogin(user_id=user.id, kinds=CREATE_ALL))
        db.session.commit()


def test_consent_contact_only_for_admins_of_the_original(app, client, survey):
    fill_consent(client)
    _editor(app, client)
    with app.app_context():
        source = db.session.get(Survey, 1)
        source.consent_item.config = {**source.consent_item.config, "contact_email": "leitung@example.org"}
        db.session.commit()
        mine, notes = dup.create(source, db.session.scalar(db.select(User).filter_by(orcid=ME)), "Welle 2")
        assert mine.consent_item.config["contact_email"] == "leitung@example.org"
        assert not any("Kontaktadresse" in n for n in notes)
        theirs, notes = dup.create(db.session.get(Survey, 1), db.session.scalar(db.select(User).filter_by(orcid=OTHER)), "Welle 3")
        assert theirs.consent_item.config["contact_email"] == ""
        assert any("Kontaktadresse der Einwilligung wurde nicht übernommen" in n for n in notes)
        assert "leitung@example.org" not in repr(theirs.consent_item.texts)


def test_live_vote_contact_only_for_admins_of_the_original(app, client):
    from tests.live.test_live import make

    code, sid, block = make(app, client, "Vortrag")
    _editor(app, client, sid)
    with app.app_context():
        source = db.session.get(Survey, sid)
        source.live = {**source.live, "contact_email": "leitung@example.org"}
        db.session.commit()
        theirs, notes = dup.create(source, db.session.scalar(db.select(User).filter_by(orcid=OTHER)), "Vortrag 2")
        assert theirs.live["contact_email"] == "" and any("Datenschutzseite" in n for n in notes)
        assert db.session.scalar(db.select(SurveyMember.role).filter_by(survey_id=theirs.id, orcid=OTHER)) == "admin"
        mine, notes = dup.create(db.session.get(Survey, sid), db.session.scalar(db.select(User).filter_by(orcid=ME)), "Vortrag 3")
        assert mine.live["contact_email"] == "leitung@example.org"
