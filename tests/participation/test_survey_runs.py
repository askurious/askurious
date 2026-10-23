"""Runs of a questionnaire ("Neuer Durchgang" on the page Datenerhebung; Philipp, 2026-10-13, lifecycle.new_run): the live
comparisons count only the current run, the data stay, the export names the run of every participation, the timeline
marks it."""
import csv
import io
from datetime import timedelta

from app import balance, export, lifecycle
from app.extensions import db
from app.models import Response, Survey, utcnow


def _running(app, answers=(0, 1, 2), ago=timedelta(hours=1)):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status, s.opened_at = Survey.RUNNING, utcnow() - timedelta(days=1)
        lifecycle._log(s, "start", at=utcnow() - timedelta(days=1))
        for n in answers:
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, started_at=utcnow() - ago,
                                    completed_at=utcnow() - ago, answers={"2": n}))
        db.session.commit()


def test_new_run_counts_anew_and_keeps_the_data(app, client, survey):
    app.config["BALANCE_SECONDS"] = 60
    _running(app)
    with app.app_context():
        assert [a for _, a in balance.answers(db.session.get(Survey, 1), "2")] == [0, 1, 2]
    page = client.get("/surveys/1/field").data.decode()
    assert "Durchgang 1" in page and "3 abgeschlossene Teilnahmen" in page and "Neuen Durchgang beginnen" in page
    page = client.post("/surveys/1/new-run", data={"name": " Vorlesung  2 "}, follow_redirects=True).data.decode()
    assert "„Vorlesung 2“ hat begonnen" in page
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [a for _, a in balance.answers(s, "2")] == []                   # the crowd starts from zero, at once
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, started_at=utcnow(),
                                completed_at=utcnow(), answers={"2": 5}))
        db.session.commit()
        balance.forget(1)
        assert [a for _, a in balance.answers(s, "2")] == [5]
        assert [r["name"] for r in lifecycle.runs(s)] == ["Durchgang 1", "Vorlesung 2"]
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 4    # nothing deleted
        assert export.has_runs(s)
    # an unnamed run is named by its number; the same name twice gets a number
    client.post("/surveys/1/new-run", data={"name": ""})
    client.post("/surveys/1/new-run", data={"name": "Vorlesung 2"})
    with app.app_context():
        assert [r["name"] for r in lifecycle.runs(db.session.get(Survey, 1))] == [
            "Durchgang 1", "Vorlesung 2", "Durchgang 3", "Vorlesung 2 (2)"]
    page = client.get("/surveys/1/field").data.decode()
    assert "Neuer Durchgang: Vorlesung 2" in page                             # the marker in the timeline
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert sorted(r["run"] for r in rows) == ["1", "1", "1", "2"]


def test_no_run_for_a_draft_and_no_column_without_one(app, client, survey):
    page = client.post("/surveys/1/new-run", follow_redirects=True).data.decode()
    assert "nur für Fragebögen, deren Datenerhebung begonnen hat" in page
    _running(app)
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert rows and "run" not in rows[0]
    with app.app_context():
        assert lifecycle.run_since(db.session.get(Survey, 1)) is None
