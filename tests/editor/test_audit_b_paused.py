"""Audit 2026-10-05, L-10: a paused survey whose field end has passed counts as closed (from its field end on, its
retention periods begin), and a paused survey can be closed from the pause."""
from datetime import timedelta

from app import lifecycle, retention
from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.privacy.test_erasure import build, take


def _paused(app, client):
    uid = build(app, client)
    take(app.test_client(), uid, "x")
    client.post("/surveys/1/close", data={"pause": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert (s.status, s.close_reason) == (Survey.CLOSED, "paused") and retention.due(s)["personal"] is None


def test_paused_past_its_field_end_closes_and_its_periods_run(app, client, survey):
    _paused(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        end = utcnow() - timedelta(days=4000)
        s.closed_at, s.field_end = end - timedelta(days=1), end
        s.retention = {"personal": 1, "answers": 12}
        db.session.commit()
        assert lifecycle.close_due_surveys() == 1
        s = db.session.get(Survey, 1)
        assert (s.close_reason, s.closed_at) == ("time", end)
        assert s.status_log[-1]["event"] == "close" and s.status_log[-1]["reason"] == "time"
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0  # the period "answers" was long over


def test_paused_before_its_field_end_stays_paused(app, client, survey):
    _paused(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.field_end = utcnow() + timedelta(days=3)
        db.session.commit()
        assert lifecycle.close_due_surveys() == 0
        assert db.session.get(Survey, 1).close_reason == "paused"


def test_close_from_the_pause(app, client, survey):
    _paused(app, client)
    page = client.get("/surveys/1/field").data.decode()
    assert "Aus der Pause schließen" in page
    r = client.post("/surveys/1/close", follow_redirects=True).data.decode()
    assert "geschlossen" in r
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.close_reason == "manual" and retention.due(s)["personal"] is not None
        try:  # a closed survey is not paused afterwards, and closed once only
            lifecycle.close(s, "paused")
        except lifecycle.LifecycleError:
            pass
        else:
            raise AssertionError("closed survey paused")
    assert "Aus der Pause schließen" not in client.get("/surveys/1/field").data.decode()
