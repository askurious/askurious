"""What the page "Qualitätssicherung" computes from the participations is kept on the server for every process
(quality.page, results_store file "quality"); from REPORT_JOB_THRESHOLD participations on its first computation
runs in a report process (performance audit 2026-10-18, PERF rest: cold 4.6 s at 5000 and 8.4 s at 10 000
participations in every process, because the page was kept per process only)."""
import os
from datetime import timedelta

import pytest

from app import quality, report_jobs, results_store
from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.conftest import add

POOL = {"sources": ["real"], "all": False}


def _answer(app, answers, minutes=5):
    with app.app_context():
        now = utcnow()
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED,
                                answers=answers, started_at=now - timedelta(minutes=minutes), completed_at=now))
        db.session.commit()


@pytest.fixture
def study(app, client, survey):
    add(client, "single_choice")
    client.post("/surveys/1/pool", data={"source": "real"})
    for minutes in (5, 6, 7):
        _answer(app, {"2": "1"}, minutes)
    return client


@pytest.fixture
def computed(monkeypatch):
    """How often the page's parts were computed from the participations."""
    calls = []
    summary = quality.summary
    monkeypatch.setattr(quality, "summary", lambda *a, **k: calls.append(1) or summary(*a, **k))
    return calls


def _page(app):
    with app.test_request_context():
        return quality.page(db.session.get(Survey, 1), POOL)


def _other_process():
    """What a second server process sees: nothing kept in its own memory."""
    quality._PAGE.clear()
    quality._KEPT.clear()


def test_another_process_reads_the_kept_page_instead_of_computing(app, study, computed):
    first = _page(app)
    assert len(computed) == 1
    _other_process()
    assert _page(app) == first and len(computed) == 1                 # from the file, not computed
    with app.app_context():
        assert os.path.exists(results_store._path(db.session.get(Survey, 1), name="quality"))


def test_a_changed_rule_threshold_or_participation_computes_at_once(app, study, computed):
    _page(app)
    with app.app_context():
        db.session.get(Survey, 1).speeder_pct = 60                       # the researchers change the threshold
        db.session.commit()
    _other_process()
    _page(app)
    assert len(computed) == 2
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.quality_rules = {**quality.rules(survey), "min": 1}       # and the marking rule
        db.session.commit()
    _other_process()
    _page(app)
    assert len(computed) == 3
    with app.app_context():                                             # a participation changed by bulk SQL
        db.session.execute(db.update(Response).where(Response.id == 1).values(answers={"2": "2"}))
        db.session.commit()
    _other_process()
    _page(app)
    assert len(computed) == 4
    _other_process()
    _page(app)
    assert len(computed) == 4                                           # and nothing changed: kept again


def test_a_deletion_while_computing_keeps_nothing(app, study, monkeypatch):
    summary = quality.summary

    def deleting(survey, *a, **k):
        found = summary(survey, *a, **k)
        db.session.execute(db.delete(Response).where(Response.id == 1))
        db.session.commit()
        return found

    monkeypatch.setattr(quality, "summary", deleting)
    _page(app)
    with app.app_context():
        assert not os.path.exists(results_store._path(db.session.get(Survey, 1), name="quality"))


def test_erasure_removes_the_kept_page(app, study):
    _page(app)
    assert "quality.bin" in results_store.KEPT
    with app.app_context():
        survey = db.session.get(Survey, 1)
        results_store.forget_files(survey.uid)
        assert not os.path.exists(results_store._path(survey, name="quality"))


def test_from_the_threshold_the_first_computation_runs_in_a_report_process(app, study, monkeypatch, computed):
    started = []
    monkeypatch.setattr(report_jobs, "_spawn", lambda payload: started.append(payload["uri"]))
    app.config["REPORT_JOB_THRESHOLD"] = 1
    waiting = study.get("/surveys/1/quality")
    assert waiting.status_code == 202 and "data-report-wait" in waiting.data.decode()
    assert started == ["/surveys/1/quality"] and not computed            # nothing computed in the request
    saved = study.post("/surveys/1/quality", data={"speeder": "40"})    # a save is never sent away
    assert saved.status_code == 302 and len(started) == 1
    _page(app)                                                          # what the report process does
    _other_process()
    shown = study.get("/surveys/1/quality")
    assert shown.status_code == 200 and len(computed) == 1 and len(started) == 1   # kept: in the request
