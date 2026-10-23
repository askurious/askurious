"""Whoever may edit a survey also runs its fieldwork (Ralf, 2026-10-06, concept rechte-bearbeiten-konzept.md):
"Bearbeiten", in person or through a group, starts, pauses, resumes, closes and opens a new questionnaire version.
Archiving and deleting stay with "Admin"; duplicating stays bound to the right to create surveys."""
from app.extensions import db
from app.models import Survey, SurveyMember, User, UserLogin
from tests.accounts.test_group_access import OTHER, _group, _other, _share
from tests.participation.test_participate import build


def _ready(client):
    """A survey that may start: a question and a complete consent (as the admin)."""
    build(client)                     # with a complete consent


def _join(app, role):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role=role, position=1))
        db.session.commit()


def _state(app):
    with app.app_context():
        s = db.session.get(Survey, 1)
        return s.status, s.close_reason, s.archived, s.qversion or 1


def _run_through(app, other):
    """Start, pause, resume, close and open a new version, each as `other`."""
    other.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    assert _state(app)[0] == "running"
    other.post("/surveys/1/close", data={"pause": "1"})
    assert _state(app)[:2] == ("closed", "paused")
    other.post("/surveys/1/start", data={})
    assert _state(app)[0] == "running"
    other.post("/surveys/1/close", data={})
    assert _state(app)[:2] == ("closed", "manual")
    other.post("/surveys/1/reopen", data={"confirm": "1"})
    assert _state(app)[0] == "draft" and _state(app)[3] == 2


def test_a_personal_editor_runs_the_fieldwork_but_does_not_archive(app, client, survey):
    _ready(client)
    _join(app, "editor")
    other = _other(app)
    page = other.get("/surveys/1/field").data.decode()
    assert "Befragung starten" in page and "/surveys/1/archive" not in page
    _run_through(app, other)
    other.post("/surveys/1/archive", data={"archived": "1"})
    assert _state(app)[2] is False


def test_a_group_with_editor_runs_the_fieldwork(app, client, survey):
    _ready(client)
    _share(client, _group(client), "editor")
    _run_through(app, _other(app))


def test_a_viewer_neither_starts_nor_stops(app, client, survey):
    _ready(client)
    _join(app, "viewer")
    other = _other(app)
    assert "Befragung starten" not in other.get("/surveys/1/field").data.decode()
    r = other.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"}, follow_redirects=True).data.decode()
    assert "nötig ist „Bearbeiten“" in r and _state(app)[0] == "draft"
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})   # the admin starts
    other.post("/surveys/1/close", data={"pause": "1"})
    assert _state(app)[0] == "running"


def test_the_paused_card_offers_resuming_to_editors(app, client, survey):
    _ready(client)
    _join(app, "editor")
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    client.post("/surveys/1/close", data={"pause": "1"})
    assert "Datenerhebung fortsetzen" in _other(app).get("/dashboard/").data.decode()


def test_without_the_right_to_create_the_duplicate_button_is_greyed(app, client, survey):
    _join(app, "editor")
    other = _other(app)
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(orcid=OTHER))
        row = db.session.get(UserLogin, uid)
        if row is None:
            db.session.add(UserLogin(user_id=uid, kinds=None))
        else:
            row.kinds = None
        db.session.commit()
    card = other.get("/dashboard/").data.decode()
    assert "/surveys/1/duplicate" not in card and "Ihr Konto darf keine eigenen Befragungen anlegen" in card
    assert "nicht möglich: Ihr Konto darf keine eigenen Befragungen anlegen" in other.get("/surveys/1").data.decode()
    r = other.post("/surveys/1/duplicate", data={"title": "Kopie"}, follow_redirects=True).data.decode()
    assert "Ihr Konto darf keine eigenen Befragungen anlegen" in r
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 1


def test_an_archived_survey_is_restored_before_it_opens_for_editing(app, client, survey):
    """Gesamtaudit 2026-10-18, FUNC-07: "Fragebogen ändern" of an archived survey made a draft in the archive, with a
    backup and a new questionnaire version, which then could not start ("archiviert"). Reopening and closing a pause
    by hand are refused like a start until the survey is restored; the page offers neither."""
    _ready(client)
    client.post("/surveys/1/start")
    client.post("/surveys/1/close")
    client.post("/surveys/1/archive", data={"archived": "1"})
    page = client.get("/surveys/1/field").data.decode()
    assert "/surveys/1/reopen" not in page
    r = client.post("/surveys/1/reopen", data={"confirm": "1"}, follow_redirects=True)
    assert "archiviert" in r.data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.CLOSED and (s.qversion or 1) == 1 and s.archived
    client.post("/surveys/1/archive", data={"archived": "0"})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.DRAFT and s.qversion == 2


def test_an_archived_pause_is_closed_only_by_its_field_end(app, client, survey):
    """FUNC-07: by hand only after restoring; the housekeeping still ends it at its field end (retention begins)."""
    from datetime import datetime

    from app import lifecycle

    _ready(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-31"})
    client.post("/surveys/1/close", data={"pause": "1"})
    client.post("/surveys/1/archive", data={"archived": "1"})
    client.post("/surveys/1/close")
    with app.app_context():
        assert db.session.get(Survey, 1).close_reason == "paused"
    with app.test_request_context():
        lifecycle._close_due(datetime(2031, 2, 1))
        db.session.commit()
        assert db.session.get(Survey, 1).close_reason == "time"
