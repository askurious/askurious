""""Start planen": a draft or paused survey starts by itself at a set date and time (lifecycle.plan_start,
lifecycle.start_due_surveys), by the same path as the button: the same checks, the pre-registration, the status log.
Before it participants see only when it begins (no title, as the page of a draft, S19)."""
from datetime import timedelta

from app import lifecycle, prereg
from app.extensions import db
from app.models import Survey, utcnow
from app.timeutil import to_local
from tests.editor.test_editor_fieldwork import _join
from tests.accounts.test_group_access import _other
from tests.participation.test_participate import build, uid


def _day(days):
    return (to_local(utcnow()).date() + timedelta(days=days)).isoformat()


def _plan(client, days=3, time="08:00", **extra):
    data = {"start_date": _day(days), "start_time": time, "use_end": "1", "field_end": _day(days + 30), **extra}
    return client.post("/surveys/1/start/plan", data=data, follow_redirects=True).data.decode()


def _survey(app):
    s = db.session.get(Survey, 1)
    db.session.refresh(s)
    return s


def _due_now(app):
    """The planned moment has come: start_at a minute ago."""
    with app.app_context():
        s = _survey(app)
        s.start_at = utcnow() - timedelta(minutes=1)
        db.session.commit()


def test_plan_shows_everywhere_and_the_housekeeping_starts_it(app, client, survey):
    build(client)
    page = _plan(client)
    assert "Der Start ist geplant" in page and "Start geplant:" in page and "08:00" in page
    with app.app_context():
        s = _survey(app)
        assert s.status == "draft" and to_local(s.start_at).strftime("%H:%M") == "08:00"
        assert s.start_plan["field_end"] and s.start_plan["lang"] == "de" and s.start_plan["by"]
    # the cards of "Meine Befragungen" and the header of the survey name it
    assert "badge-pending" in client.get("/dashboard/", follow_redirects=True).data.decode()
    assert "badge-pending" in client.get("/surveys/1/editor/").data.decode()
    # participants: only the start, in a language of the survey, no title (S19)
    p = app.test_client()
    html = p.get(f"/s/{uid(app)}", headers={"Accept-Language": "en"}).data.decode()
    assert "This survey begins on" in html and "at 08:00" in html and "Test" not in html
    html = p.get(f"/s/{uid(app)}", headers={"Accept-Language": "de"}).data.decode()
    assert "Diese Befragung beginnt am" in html and "um 08:00 Uhr" in html
    # nothing before the moment
    with app.test_request_context():
        assert lifecycle.start_due_surveys() == 0
    _due_now(app)
    with app.test_request_context():
        assert lifecycle.start_due_surveys() == 1
        s = _survey(app)
        assert s.status == "running" and s.start_at is None and s.start_plan is None
        assert s.field_end is not None and s.opened_at is not None
        assert [e["event"] for e in s.status_log] == ["start"]
        assert prereg.listing(s)  # frozen as by the button
    assert "Einwilligung" in p.get(f"/s/{uid(app)}").data.decode()


def test_the_link_starts_it_when_the_moment_has_come(app, client, survey):
    """Whoever opens the link at the moment itself does not wait for the next round of the housekeeping."""
    build(client)
    _plan(client)
    _due_now(app)
    html = app.test_client().get(f"/s/{uid(app)}").data.decode()
    assert "beginnt am" not in html and "Einwilligung" in html
    with app.app_context():
        assert _survey(app).status == "running"


def test_refused_with_errors_in_the_design_check(app, client, survey):
    page = _plan(client)  # no question, no complete consent
    assert "Der Fragebogen ist noch nicht startklar" in page
    with app.app_context():
        assert _survey(app).start_at is None


def test_refused_in_the_past_or_with_the_end_before_it(app, client, survey):
    build(client)
    assert "Der geplante Start muss in der Zukunft liegen" in _plan(client, days=-1)
    assert "Das Feldende muss nach dem geplanten Start liegen" in _plan(client, days=10, field_end=_day(5))
    assert "Bitte Datum und Uhrzeit des Starts wählen" in _plan(client, time="")
    assert "höchstens ein Jahr im Voraus" in _plan(client, days=400)
    with app.app_context():
        assert _survey(app).start_at is None


def test_a_failed_check_at_the_moment_keeps_a_note(app, client, survey):
    build(client)
    _plan(client)
    with app.app_context():  # something changed since: the end of fieldwork the plan goes with is over
        s = _survey(app)
        s.start_plan = {**s.start_plan, "field_end": (utcnow() - timedelta(days=1)).isoformat()}
        db.session.commit()
    _due_now(app)
    with app.test_request_context():
        assert lifecycle.start_due_surveys() == 0
        s = _survey(app)
        assert s.status == "draft" and s.start_at is None and "Feldende" in s.start_plan["failed"]
        assert not prereg.listing(s)
    page = client.get("/surveys/1/field").data.decode()
    assert "wurde nicht ausgeführt" in page and "Hinweis entfernen" in page
    assert "badge-error" in client.get("/dashboard/", follow_redirects=True).data.decode()
    # the link shows the page of a draft again, not a start in the past
    assert "derzeit nicht geöffnet" in app.test_client().get(f"/s/{uid(app)}").data.decode()
    client.post("/surveys/1/start/cancel")
    with app.app_context():
        assert _survey(app).start_plan is None
    assert "wurde nicht ausgeführt" not in client.get("/surveys/1/field").data.decode()


def test_cancel_and_a_start_by_hand_end_the_plan(app, client, survey):
    build(client)
    _plan(client)
    page = client.post("/surveys/1/start/cancel", follow_redirects=True).data.decode()
    assert "Der geplante Start ist aufgehoben" in page
    with app.app_context():
        assert _survey(app).start_at is None and _survey(app).start_plan is None
    _plan(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": _day(20)})
    with app.app_context():
        s = _survey(app)
        assert s.status == "running" and s.start_at is None and s.start_plan is None


def test_a_paused_survey_resumes_at_the_planned_moment(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={})
    client.post("/surveys/1/close", data={"pause": "1"})
    assert "Fortsetzung geplant" in _plan(client, days=2, time="09:30")
    html = app.test_client().get(f"/s/{uid(app)}").data.decode()
    assert "Diese Befragung wird am" in html and "09:30 Uhr fortgesetzt" in html
    _due_now(app)
    with app.test_request_context():
        assert lifecycle.start_due_surveys() == 1
        assert _survey(app).status == "running"


def test_roles_who_may_start_may_plan(app, client, survey):
    build(client)
    _join(app, "viewer")
    viewer = _other(app)
    viewer.post("/surveys/1/start/plan", data={"start_date": _day(3), "start_time": "08:00"})
    with app.app_context():
        assert _survey(app).start_at is None
    assert "Start planen" not in viewer.get("/surveys/1/field").data.decode()
    with app.app_context():
        s = _survey(app)
        s.members[-1].role = "editor"
        db.session.commit()
    _plan(viewer)
    with app.app_context():
        assert _survey(app).start_at is not None
    viewer.post("/surveys/1/start/cancel")
    with app.app_context():
        assert _survey(app).start_at is None


def test_a_copy_does_not_take_the_plan(app, client, survey):
    build(client)
    _plan(client)
    client.post("/surveys/1/duplicate", data={"title": "Kopie", "with_people": "1"})
    with app.app_context():
        copy = db.session.scalar(db.select(Survey).filter_by(title="Kopie"))
        assert copy is not None and copy.start_at is None and copy.start_plan is None


def test_archiving_cancels_the_plan_and_an_archived_survey_never_starts(app, client, survey):
    """Code review 2026-10-15, H4: an archived survey started at its planned moment and collected real data. Now
    archiving cancels the plan, the housekeeping skips archived surveys, and none can be planned."""
    build(client)
    _plan(client)
    client.post("/surveys/1/archive", data={"archived": "1"})
    with app.app_context():
        s = _survey(app)
        assert s.archived and s.start_at is None and s.start_plan is None
        # one archived before this fix, with its plan still there
        s.start_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    with app.test_request_context():
        assert lifecycle.start_due_surveys() == 0
        assert _survey(app).status == "draft"
    with app.app_context():
        _survey(app).start_at = None
        db.session.commit()
    assert "Stellen Sie sie zuerst wieder her" in _plan(client)
    with app.app_context():
        assert _survey(app).start_at is None


def test_a_start_cut_off_hard_is_taken_up_again_after_its_lease(app, client, survey, monkeypatch):
    """Gesamtaudit 2026-10-18, FUNC-03: the claim emptied start_at before the work; a process ending during the
    start (a worker killed at its limit, a deploy) left a draft with no plan and no note. Now the claim is a lease:
    once it runs out, a later round starts the survey, counting the cut-off as a try."""
    build(client)
    _plan(client)
    _due_now(app)

    class WorkerKilled(BaseException):  # not an Exception: nothing in the process catches it
        pass

    def killed(*a, **k):
        raise WorkerKilled()

    now = utcnow()
    monkeypatch.setattr(lifecycle, "start_committed", killed)
    with app.test_request_context():
        try:
            lifecycle.start_due_surveys(now=now)
        except WorkerKilled:
            pass
        db.session.rollback()
    monkeypatch.undo()
    with app.test_request_context():
        assert lifecycle.start_due_surveys(now=now + timedelta(minutes=1)) == 0  # the lease still holds
        s = _survey(app)
        assert s.status == Survey.DRAFT and s.start_at is not None, "the planned start is gone without a note"
        assert lifecycle.start_due_surveys(now=now + lifecycle.START_LEASE + timedelta(minutes=1)) == 1
        s = _survey(app)
        assert s.status == Survey.RUNNING and s.start_at is None and s.start_plan is None


def test_a_start_cut_off_again_and_again_ends_with_a_note(app, client, survey, monkeypatch):
    """FUNC-03: every cut-off counts; after START_TRIES the researchers see a note instead of a silent retry."""
    build(client)
    _plan(client)
    _due_now(app)

    class WorkerKilled(BaseException):
        pass

    def killed(*a, **k):
        raise WorkerKilled()

    monkeypatch.setattr(lifecycle, "start_committed", killed)
    now = utcnow()
    with app.test_request_context():
        for n in range(lifecycle.START_TRIES + 1):
            try:
                lifecycle.start_due_surveys(now=now + n * (lifecycle.START_LEASE + timedelta(minutes=1)))
            except WorkerKilled:
                pass
            db.session.rollback()
        s = _survey(app)
        assert s.status == Survey.DRAFT and s.start_at is None and lifecycle.planned_failure(s) is not None
