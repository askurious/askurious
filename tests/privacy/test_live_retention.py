"""Retention of a live vote (app/retention.py, ended; concept "Befragungstypen im Vergleich" 2026-10-17, T14, variant A):
a live vote is never closed, so its periods count from its last activity, and the period for the answers is set in
its settings, group "Datenschutz und Aufbewahrung"."""
from datetime import timedelta

from app import retention
from app.extensions import db
from app.models import LiveQuestion, Response, Survey, utcnow
from tests.live.test_live import make


def _vote(app, sid, days_ago, source="real"):
    with app.app_context():
        when = utcnow() - timedelta(days=days_ago)
        survey = db.session.get(Survey, sid)
        db.session.add(Response(survey_id=sid, language=survey.default_language, source=source, started_at=when,
                                completed_at=when, status=Response.COMPLETED))
        db.session.commit()


def test_the_settings_show_and_save_the_period_from_the_last_participation(app, client):
    code, sid, block = make(app, client, "Frist")
    page = client.get(f"/presenter/{code}/settings").data.decode()
    assert "Datenschutz und Aufbewahrung" in page and "nach der letzten Teilnahme" in page
    client.post(f"/presenter/{code}/settings", data={"part": "datenschutz", "retention_shown": "1",
                                                      "retention_personal": "6", "retention_answers": "12"})
    with app.app_context():
        assert retention.of(db.session.get(Survey, sid)) == {"personal": 6, "answers": 12}
    assert "1 Jahr nach der letzten Teilnahme" in client.get(f"/presenter/{code}/settings").data.decode()


def test_the_answers_go_a_period_after_the_last_activity(app, client):
    code, sid, block = make(app, client, "Alt")
    with app.app_context():
        survey = db.session.get(Survey, sid)
        survey.retention = {"personal": 6, "answers": 12}
        db.session.commit()
        assert retention.ended(survey) is None          # nothing collected yet: no clock
    _vote(app, sid, days_ago=400)
    with app.app_context():
        db.session.add(LiveQuestion(survey_id=sid, text="Wann ist die Klausur?", created_at=utcnow() - timedelta(days=380)))
        db.session.commit()
        survey = db.session.get(Survey, sid)
        assert survey.status == Survey.DRAFT and retention.ended(survey) is not None
        assert retention.run() == 1
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 0
        assert db.session.scalar(db.select(db.func.count(LiveQuestion.id)).filter_by(survey_id=sid)) == 0


def test_a_recent_participation_keeps_everything(app, client):
    code, sid, block = make(app, client, "Neu")
    with app.app_context():
        survey = db.session.get(Survey, sid)
        survey.retention = {"personal": 6, "answers": 12}
        db.session.commit()
    _vote(app, sid, days_ago=400)
    _vote(app, sid, days_ago=3)                         # the clock starts anew with every activity
    with app.app_context():
        assert retention.run() == 0
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 2

