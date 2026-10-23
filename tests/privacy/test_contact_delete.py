""""Alle Kontaktdaten löschen" on the data page (surveys.delete_contacts; code review 2026-10-15, L8: no direct test
before): only "Verwalten" deletes, and then every entry of the separated section of this survey, no other's."""
from app.extensions import db
from app.models import ContactEntry, SurveyMember

OTHER = "0000-0002-1825-0097"


def entries(app, survey_id):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count()).select_from(ContactEntry)
                                 .where(ContactEntry.survey_id == survey_id))


def test_only_admins_delete_the_contact_details_of_their_survey(app, client, survey):
    client.post("/surveys/new", data={"title": "Zweite", "languages": ["de"], "default_language": "de"})   # id 2
    with app.app_context():
        db.session.add_all([ContactEntry(survey_id=1, email="a@example.org"), ContactEntry(survey_id=1, comment="hi"),
                            ContactEntry(survey_id=2, email="b@example.org"),
                            SurveyMember(survey_id=1, orcid=OTHER, name="Zweite", role="editor", position=1)])
        db.session.commit()
    editor = app.test_client()
    editor.get(f"/auth/dev-login?orcid={OTHER}&name=Zweite")
    editor.post("/terms/", data={"accept": "1"})
    assert editor.post("/surveys/1/contact/delete").status_code == 302
    assert entries(app, 1) == 2                                            # "Bearbeiten" is not enough
    assert editor.post("/surveys/2/contact/delete").status_code == 404     # not a member
    r = client.post("/surveys/1/contact/delete")
    assert r.status_code == 302 and r.headers["Location"].endswith("#contact-data")
    assert entries(app, 1) == 0 and entries(app, 2) == 1
    assert "Alle Kontaktdaten dieser Befragung wurden gelöscht." in client.get("/surveys/1/data").data.decode()
