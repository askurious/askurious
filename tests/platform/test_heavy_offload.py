"""CPU-heavy evaluations leave a process that also serves participants (performance audit 2026-10-18, PERF-06): below
REPORT_JOB_THRESHOLD a cold result was computed in the request thread (6 s at 5000) and slowed every other thread of
the process, taking part included (p95 of the participants' POSTs 163 -> 611 ms). Now from participations x elements
>= REPORT_JOB_COST such a computation runs in the report process, except in a researchers' container."""
import pytest

from app import report_jobs
from app.extensions import db
from app.models import Response, utcnow
from tests.conftest import add


@pytest.fixture
def spawned(app, monkeypatch):
    started = []
    monkeypatch.setattr(report_jobs, "_spawn", lambda payload: started.append(payload["uri"]) or None)
    return started


def _survey(app, client, n=20):
    for kind in ("single_choice", "text_input"):
        add(client, kind)
    with app.app_context():
        for k in range(n):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers={"2": "1"},
                                    completed_at=utcnow(), pid=f"h{k}"))
        db.session.commit()


def test_an_expensive_computation_leaves_the_participants_process(app, client, survey, spawned, monkeypatch):
    _survey(app, client)  # 20 participations x 3 elements (consent, single choice, text) = 60
    app.config["REPORT_JOB_COST"] = 50
    assert client.get("/surveys/1/results?go=1").status_code == 202
    assert client.get("/surveys/1/export/data.csv").status_code == 202
    assert spawned == ["/surveys/1/results?go=1", "/surveys/1/export/data.csv"]
    monkeypatch.setattr(report_jobs, "serves_participants", lambda: False)  # a researchers' container
    assert client.get("/surveys/1/results?go=1").status_code == 200
    assert len(spawned) == 2
    # there from REPORT_JOB_COST_RESEARCHER on (audit 2026-10-22, G-06: a long computation held back the other
    # threads of its gunicorn worker)
    from app.models import Survey

    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not report_jobs.offload(s, 20)                    # 20 x 3 below REPORT_JOB_COST_RESEARCHER (0: off)
        app.config["REPORT_JOB_COST_RESEARCHER"] = 50
        assert report_jobs.offload(s, 20)                        # 60 >= 50
        assert not report_jobs.offload(s, 10)                    # 30 < 50


def test_a_cheap_one_and_a_switched_off_estimate_stay_in_the_request(app, client, survey, spawned):
    _survey(app, client)
    app.config["REPORT_JOB_COST"] = 61
    assert client.get("/surveys/1/results?go=1").status_code == 200
    app.config.update(REPORT_JOB_COST=50, REPORT_JOB_THRESHOLD=0)  # no report processes at all
    assert client.get("/surveys/1/export/data.csv").status_code == 200
    assert spawned == []


def test_which_processes_serve_participants(app):
    with app.app_context():
        for side, serves in (("", True), ("participant", True), ("researcher", False)):
            app.config["SIDE"] = side
            assert report_jobs.serves_participants() is serves
        app.config["SIDE"] = ""


def test_a_report_process_starts_from_the_quality_values_of_the_one_before(app, client, survey, monkeypatch):
    """Audit 2026-10-22, G-01: every report process started cold and computed every participation's quality values
    anew (4.2 s instead of 2.2 s at 10 000). It reads the file the report before wrote; a deleted participation takes
    the file along."""
    from flask import g

    from app import quality, results_store
    from app.models import Survey

    _survey(app, client)
    calls = []
    real = quality.values
    monkeypatch.setattr(quality, "values", lambda *a, **k: calls.append(1) or real(*a, **k))

    def report():
        quality._KEPT.clear()
        quality._DISK_LOADED.clear()
        with app.test_request_context("/surveys/1/results"):
            g._report_worker = True
            s = db.session.get(Survey, 1)
            ctx = quality.context(s, __import__("app.export", fromlist=["_items"])._items(s))
            for r in db.session.scalars(db.select(Response).filter_by(survey_id=1)):
                quality.request_values(s, r, ctx)
            quality.save_kept()

    report()
    first = len(calls)
    assert first == 20
    report()
    assert len(calls) == first                                   # all 20 from the file of the report before
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert results_store.load(s, quality.DISK_KEY, name=quality.DISK_NAME) is not None
        from app import erasure

        erasure.erase(s, [db.session.scalar(db.select(Response).filter_by(survey_id=1))], "code")
        assert results_store.load(s, quality.DISK_KEY, name=quality.DISK_NAME) is None   # gone with the deletion
