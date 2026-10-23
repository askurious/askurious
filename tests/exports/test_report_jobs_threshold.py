"""What can be answered without computing anew stays in the request also from REPORT_JOB_THRESHOLD participations on
(performance audit 2026-10-18, PERF-03): results kept already, the codebook and a kept count of the export page took
a process of their own, 3 to 4 s instead of 0.1 to 0.3 s. A computation still goes to the report process."""
import pytest

from app.extensions import db
from app.models import Response, utcnow
from tests.conftest import add


@pytest.fixture
def spawned(app, monkeypatch):
    from app import report_jobs

    started = []
    monkeypatch.setattr(report_jobs, "_spawn", lambda payload: started.append(payload["uri"]) or None)
    return started


def _participations(app, n=20, start=0):
    with app.app_context():
        for k in range(start, start + n):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers={"2": "1"},
                                    completed_at=utcnow(), pid=f"t{k}"))
        db.session.commit()


def test_kept_results_and_the_codebook_answer_in_the_request(app, client, survey, spawned):
    add(client, "single_choice")
    _participations(app)
    assert client.get("/surveys/1/results").status_code == 200  # computed and kept (threshold 5000)
    app.config["REPORT_JOB_THRESHOLD"] = 10
    page = client.get("/surveys/1/results")
    assert page.status_code == 200 and "Ergebnisse" in page.data.decode()
    assert client.get("/surveys/1/export/codebook.csv").status_code == 200
    assert spawned == []
    # what is not kept still leaves the process: the data file, another data pool
    assert client.get("/surveys/1/export/data.csv").status_code == 202
    assert client.get("/surveys/1/results?source=real&source=test").status_code == 202
    assert len(spawned) == 2


def test_new_participations_compute_in_the_report_process(app, client, survey, spawned):
    add(client, "single_choice")
    _participations(app)
    client.get("/surveys/1/results")
    _participations(app, 1, start=20)
    app.config["REPORT_JOB_THRESHOLD"] = 10
    assert client.get("/surveys/1/results").status_code == 202 and spawned == ["/surveys/1/results"]


def test_a_kept_count_of_the_export_page_answers_in_the_request(app, client, survey, spawned):
    add(client, "single_choice")
    _participations(app)
    app.config["REPORT_JOB_THRESHOLD"] = 10
    first = client.get("/surveys/1/data-counts?source=real", headers={"X-Report-Poll": "1"})
    assert first.status_code == 202 and first.headers["X-Report-Job"] and len(spawned) == 1  # the page polls (app.js)
    app.config["REPORT_JOB_THRESHOLD"] = 0
    assert client.get("/surveys/1/data-counts?source=real").json["total"] == 20  # as the report process keeps it
    app.config["REPORT_JOB_THRESHOLD"] = 10
    again = client.get("/surveys/1/data-counts?source=real", headers={"X-Report-Poll": "1"})
    assert again.status_code == 200 and again.json["total"] == 20 and len(spawned) == 1
