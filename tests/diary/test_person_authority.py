"""Audit 2026-10-13 AZ-31: irreversible individual endings require survey admin rights."""
from app.extensions import db
from app.models import Enrolment, SurveyMember
from tests.conftest import allow
from tests.diary.test_diary import _new, _enrol


def test_editor_cannot_end_a_person_but_admin_can(app, client):
    code = _new(app, client)
    _enrol(app, code)
    other = "0000-0002-1825-0097"
    allow(app, other, keep=True)
    editor = app.test_client()
    editor.get(f"/auth/dev-login?orcid={other}&name=Editor")
    editor.post("/terms/", data={"accept": "1"})
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=other, role="editor", position=1))
        person = db.session.scalar(db.select(Enrolment))
        pid, before = person.id, person.status
        db.session.commit()
    assert editor.post(f"/diary/1/person/{pid}/end").status_code == 403
    with app.app_context():
        assert db.session.get(Enrolment, pid).status == before
    assert client.post(f"/diary/1/person/{pid}/end").status_code == 302
    with app.app_context():
        assert db.session.get(Enrolment, pid).status == Enrolment.ENDED
