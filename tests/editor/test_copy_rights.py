"""Taking the questionnaire out of a survey needs the same role everywhere (access.may_copy_out; audit 2026-10-18,
AUTHZ-05): duplicating asked for "Bearbeiten", while the survey file (.json, .zip), the multi-export of "Meine
Befragungen" and the take-over into a diary study let "Ansehen" copy it as well."""
from app import access, diary
from app.extensions import db
from app.models import Survey, SurveyMember, User
from tests.conftest import allow

OTHER = "0000-0002-1825-0097"


def _member(app, client, role):
    allow(app, OTHER)
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "viewer"})
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(orcid=OTHER))
        db.session.scalar(db.select(SurveyMember).filter_by(survey_id=1, user_id=uid)).role = role
        db.session.commit()
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    return other


def _set_role(app, role):
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(orcid=OTHER))
        db.session.scalar(db.select(SurveyMember).filter_by(survey_id=1, user_id=uid)).role = role
        db.session.commit()


def test_view_takes_nothing_out_edit_does(app, client, survey):
    other = _member(app, client, "viewer")
    json_file, zip_file = other.get("/surveys/1/export/survey.json"), other.get("/surveys/1/export/survey.zip")
    assert json_file.status_code == 302 and zip_file.status_code == 302           # refused with "Dafür fehlen die Rechte"
    assert other.get("/surveys/1/duplicate").status_code == 302
    assert other.post("/diary/1/take-over", data={"source": "1", "part": "entry"}).status_code == 302
    page = other.get("/dashboard/export").data.decode()
    assert "Noch keine Befragungen" in page                                        # not offered in the multi-export
    assert "Als JSON-Datei herunterladen" not in other.get("/surveys/1/data").data.decode()
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        found = db.session.get(Survey, 1)
        assert not access.may_copy_out(user, found) and diary.take_over_sources(Survey(id=0), user) == []

    _set_role(app, "editor")
    assert other.get("/surveys/1/export/survey.json").status_code == 200
    assert other.get("/surveys/1/export/survey.zip").status_code == 200
    assert other.get("/surveys/1/duplicate").status_code == 200
    assert other.post("/diary/1/take-over", data={"source": "1", "part": "entry"}).status_code != 302  # on to the target
    assert "Als JSON-Datei herunterladen" in other.get("/surveys/1/data").data.decode()
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        assert [s.id for s in diary.take_over_sources(Survey(id=0), user)] == [1]


def test_opening_the_editor_or_the_flow_as_viewer_changes_nothing(app, client, survey):
    """Audit 2026-10-18, AUTHZ-06: a GET of editor.index and flow_page.index put the flow in order and emptied the
    trash of entries whose days were over, with a commit, also for "Ansehen". Now only for who may edit; the page of
    a viewer shows the flow in order without storing it."""
    from datetime import timedelta

    from app.models import TrashedItem, utcnow
    from tests.conftest import add

    add(client, "info")
    other = _member(app, client, "viewer")
    with app.app_context():
        found = db.session.get(Survey, 1)
        found.flow = {"t": "root", "id": "root", "children": []}  # out of order: normalize puts the blocks back in
        found.trash.append(TrashedItem(data={"type": "info", "texts": {}}, deleted_at=utcnow() - timedelta(days=30)))
        db.session.commit()
    for path in ("/surveys/1/editor/", "/surveys/1/flow/"):
        assert other.get(path).status_code == 200
        with app.app_context():
            found = db.session.get(Survey, 1)
            assert found.flow == {"t": "root", "id": "root", "children": []} and len(found.trash) == 1
    assert client.get("/surveys/1/editor/").status_code == 200                  # an editor's visit does the upkeep
    with app.app_context():
        found = db.session.get(Survey, 1)
        assert found.flow["children"] and not found.trash
