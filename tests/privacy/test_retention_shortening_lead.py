"""A shorter period "Alle Teilnahmen löschen" that would delete at once (the fieldwork ended longer ago than the new
period) is asked for first and deletes 30 days later at the earliest (app/retention.py, from_form and ask_shortening):
before, choosing "1 Jahr" by mistake on a survey closed two years ago deleted every participation at the next round,
against the hint "warnt 30 Tage vorher" (business logic audit 2026-11-11). Ralf 2026-11-12: question and lead."""
from datetime import timedelta

from app import retention
from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.privacy.test_retention import build, close, periods


def _real(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=1, source="real"))


def test_shortening_into_the_past_is_asked_and_waits_thirty_days(app, client, survey):
    build(app, client, personal=6, answers=120, take=3)
    close(app, client, days_ago=730)
    form = {**periods(6, 12), "part": "datenschutz", "autosave": "1"}
    body = client.post("/surveys/1/meta", data=form).get_json()
    assert body["status"] == "ask" and body["field"] == "shorten" and "3 Teilnahmen" in body["note"], body
    with app.app_context():
        assert retention.of(db.session.get(Survey, 1))["answers"] == 120          # asked only: nothing saved yet
    client.post("/surveys/1/meta", data={**form, "asked": "shorten"})              # the yes
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert retention.of(s)["answers"] == 12
        assert retention.due(s)["answers"] >= utcnow() + timedelta(days=29)
        retention.run()
    assert _real(app) == 3                                                         # nothing went yet
    with app.app_context():
        retention.run(now=utcnow() + timedelta(days=31))
    assert _real(app) == 0                                                         # after the lead it does


def test_a_shorter_period_that_still_ends_later_is_saved_without_a_question(app, client, survey):
    build(app, client, personal=6, answers=120, take=1)
    close(app, client, days_ago=10)
    with app.app_context():
        assert retention.shortening_soon(db.session.get(Survey, 1), periods(6, 60)) is None
    client.post("/surveys/1/meta", data={**periods(6, 60), "part": "datenschutz", "autosave": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert retention.of(s)["answers"] == 60 and "not_before" not in (s.retention or {})
