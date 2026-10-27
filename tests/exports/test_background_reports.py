"""PF-2: large reports leave the web process, retain permissions and return identical files."""
import html
import os
import re
import time
import pytest

from app.extensions import db
from app.models import Response, Survey, SurveyMember, utcnow
from tests.conftest import add, allow
from tests.dashboards.test_showcase import OTHER, _as

# how long a test waits for the separate process of a background report: starting a Python process and the app takes
# seconds on Windows, and under a full run with eight workers it took more than 45 (full suite 2026-10-28: 202 instead
# of 204 at the deadline); the conftest's TEST_TIMEOUT still ends a hanging test
JOB_WAIT = 180


def _answer(app):
    with app.app_context():
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED,
                                answers={"2": "1"}, completed_at=utcnow(), pid="person-one"))
        db.session.commit()


def _poll_url(response):
    return html.unescape(re.search(r'data-url="([^"]+)"', response.data.decode())[1])


def test_csv_is_created_by_a_separate_process_and_is_identical(app, client, survey, monkeypatch):
    from app import report_jobs

    add(client, "single_choice")
    _answer(app)
    expected = client.get("/surveys/1/export/data.csv").data
    app.config["REPORT_JOB_THRESHOLD"] = 1
    children = []
    spawn = report_jobs._spawn

    def track(payload):
        child = spawn(payload)
        children.append(child)
        return child

    monkeypatch.setattr(report_jobs, "_spawn", track)
    response = client.get("/surveys/1/export/data.csv")
    assert response.status_code == 202 and children[0].pid != os.getpid()
    url = _poll_url(response)
    deadline = time.monotonic() + JOB_WAIT
    while time.monotonic() < deadline:
        poll = client.get(url, headers={"X-Report-Poll": "1"})
        if poll.status_code != 202:
            break
        time.sleep(.1)
    assert poll.status_code == 204 and poll.data == b""
    ready = client.get(url)
    assert ready.status_code == 200 and ready.data == expected
    assert "attachment" in ready.headers["Content-Disposition"]
    children[0].wait(timeout=10)


def test_every_poll_rechecks_export_permission(app, client, survey, monkeypatch):
    from app import report_jobs

    add(client, "single_choice")
    _answer(app)
    allow(app, OTHER)
    other = _as(app, OTHER, "Editor")
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, role="editor"))
        db.session.commit()
    app.config["REPORT_JOB_THRESHOLD"] = 1
    monkeypatch.setattr(report_jobs, "_spawn", lambda payload: None)
    response = other.get("/surveys/1/export/data.csv")
    url = _poll_url(response)
    with app.app_context():
        db.session.scalar(db.select(SurveyMember).filter_by(survey_id=1, orcid=OTHER)).role = "viewer"
        db.session.commit()
    for headers in ({}, {"X-Report-Poll": "1"}):
        denied = other.get(url, headers=headers)
        assert denied.status_code == 302 and denied.headers["Location"].endswith("/dashboard/")
        assert "X-Report-Ready" not in denied.headers


@pytest.mark.parametrize("completed", [True, False])
def test_erasure_cancels_pending_reports_and_removes_artifacts(app, client, survey, monkeypatch, completed):
    from app import report_jobs

    add(client, "single_choice")
    _answer(app)
    if not completed:
        with app.app_context():
            db.session.scalar(db.select(Response).filter_by(survey_id=1)).status = Response.ACTIVE
            db.session.commit()
    app.config["REPORT_JOB_THRESHOLD"] = 1
    payloads = []
    monkeypatch.setattr(report_jobs, "_spawn", lambda payload: payloads.append(payload))
    response = client.get("/surveys/1/export/data.csv")
    assert response.status_code == 202
    base = payloads[0]["base"]
    with open(base + ".body", "wb") as out:
        out.write(b"private data")
    with app.app_context():
        from app import erasure

        erasure.erase(db.session.get(Survey, 1), [db.session.scalar(db.select(Response).filter_by(survey_id=1))], "request")
        db.session.commit()
    assert not os.path.exists(base + ".body") and not os.path.exists(base + ".run.json")
    assert client.get(_poll_url(response)).status_code == 410


@pytest.mark.parametrize("suffix", ["data.sav", "data.omv", "data.rds", "spss.zip"])
def test_binary_exports_are_delivered_after_the_background_job(app, client, survey, suffix):
    add(client, "single_choice")
    _answer(app)
    app.config["REPORT_JOB_THRESHOLD"] = 1
    first = client.get(f"/surveys/1/export/{suffix}")
    assert first.status_code == 202
    url = _poll_url(first)
    deadline = time.monotonic() + JOB_WAIT
    while time.monotonic() < deadline:
        poll = client.get(url, headers={"X-Report-Poll": "1"})
        if poll.status_code != 202:
            break
        time.sleep(.1)
    assert poll.status_code == 204
    result = client.get(url)
    assert result.status_code == 200 and result.data
    assert suffix.rsplit(".", 1)[1] in result.headers["Content-Disposition"]


def test_machine_csv_keeps_its_200_download_contract(app, client, survey):
    from tests.privacy.test_data_access import _create, _get

    add(client, "single_choice")
    _answer(app)
    _, token = _create(client)
    machine = app.test_client()
    expected = _get(machine, "/api/v1/surveys/1/data.csv", token).data
    app.config["REPORT_JOB_THRESHOLD"] = 1
    response = _get(machine, "/api/v1/surveys/1/data.csv", token)
    assert response.status_code == 200 and response.data == expected


def test_async_machine_poll_reuses_job_and_rechecks_token(app, client, survey, monkeypatch):
    from app import report_jobs
    from app.models import DataToken
    from tests.privacy.test_data_access import _create

    add(client, "single_choice")
    _answer(app)
    _, token = _create(client)
    app.config["REPORT_JOB_THRESHOLD"] = 1
    payloads = []
    monkeypatch.setattr(report_jobs, "_spawn", lambda payload: payloads.append(payload))
    machine = app.test_client()
    headers = {"Authorization": f"Bearer {token}", "Prefer": " respond-async "}
    first = machine.get("/api/v1/surveys/1/data.csv?sep=semicolon", headers=headers)
    assert first.status_code == 202
    assert first.headers["Preference-Applied"] == "respond-async"
    url = first.headers["Location"]
    assert "sep=semicolon" in url and token not in url
    assert machine.get(url, headers=headers).status_code == 202
    assert len(payloads) == 1
    base = payloads[0]["base"]
    with open(base + ".body", "wb") as out:
        out.write(b"completed report")
    with app.app_context():
        info = report_jobs._read(base + ".run.json")
        report_jobs._write(base + ".run.json", {**info, "state": "done"})
        report_jobs._write(base + ".meta.json", {"type": "text/csv", "status": 200})
    assert machine.get(url, headers=headers).data == b"completed report"
    assert machine.get(url, headers=headers).data == b"completed report"
    assert len(payloads) == 1
    with app.app_context():
        db.session.delete(db.session.scalar(db.select(DataToken)))
        db.session.commit()
    assert machine.get(url, headers=headers).status_code == 401


def test_machine_wait_slots_refuse_immediately_and_release_on_error(app, client, survey, monkeypatch):
    from app import heavy, report_jobs
    from app.models import DataToken
    from tests.privacy.test_data_access import _create, _get

    add(client, "single_choice")
    _answer(app)
    _, token = _create(client)
    app.config.update(REPORT_JOB_THRESHOLD=1, REPORT_API_WAITERS=1)
    machine = app.test_client()
    with app.app_context():
        slot = heavy._shared_slot(1, 0, namespace="report-api-wait")
    try:
        monkeypatch.setattr(report_jobs, "_spawn", lambda payload: pytest.fail("must not start rejected job"))
        assert _get(machine, "/api/v1/surveys/1/data.csv", token).status_code == 503
    finally:
        slot.close()
    import hashlib
    with app.app_context():
        token_id = db.session.scalar(db.select(DataToken.id))
        slot = heavy._shared_slot(1, 0, namespace="report-api-token-" + hashlib.sha256(str(token_id).encode()).hexdigest())
    try:
        assert _get(machine, "/api/v1/surveys/1/data.csv", token).status_code == 503
    finally:
        slot.close()
    monkeypatch.setattr(report_jobs, "_api_poll", lambda *args: (_ for _ in ()).throw(RuntimeError("failed poll")))
    with pytest.raises(RuntimeError, match="failed poll"):
        _get(machine, "/api/v1/surveys/1/data.csv", token)
    with app.app_context():
        slot = heavy._shared_slot(1, 0, namespace="report-api-wait")
        assert slot is not None
        slot.close()

        token_id = db.session.scalar(db.select(DataToken.id))
        import hashlib
        slot = heavy._shared_slot(1, 0, namespace="report-api-token-" + hashlib.sha256(str(token_id).encode()).hexdigest())
        assert slot is not None
        slot.close()


def test_machine_wait_slot_is_shared_with_another_process(app):
    import subprocess
    import sys
    from app import heavy

    program = """
import sys
from flask import Flask
from app import heavy
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = sys.argv[1]
with app.app_context():
    slot = heavy._shared_slot(1, 0, namespace='report-api-wait')
    print('acquired' if slot else 'full')
    if slot:
        slot.close()
"""
    with app.app_context():
        folder = app.config["UPLOAD_FOLDER"]
        slot = heavy._shared_slot(1, 0, namespace="report-api-wait")
        try:
            assert subprocess.check_output([sys.executable, "-c", program, folder], text=True).strip() == "full"
        finally:
            slot.close()
        assert subprocess.check_output([sys.executable, "-c", program, folder], text=True).strip() == "acquired"


def test_large_results_are_computed_outside_the_request(app, client, survey):
    add(client, "single_choice")
    _answer(app)
    app.config["REPORT_JOB_THRESHOLD"] = 1
    first = client.get("/surveys/1/results?go=1")
    assert first.status_code == 202
    url = _poll_url(first)
    deadline = time.monotonic() + JOB_WAIT
    while time.monotonic() < deadline:
        poll = client.get(url, headers={"X-Report-Poll": "1"})
        if poll.status_code != 202:
            break
        time.sleep(.1)
    assert poll.status_code == 204
    response = client.get(url)
    assert response.status_code == 200 and "Ergebnisse" in response.data.decode()


def test_estimation_status_fragments_keep_their_original_protocol(app, client, survey, monkeypatch):
    import importlib

    from app import report_jobs

    add(client, "single_choice")
    _answer(app)
    app.config["REPORT_JOB_THRESHOLD"] = 1
    module = importlib.import_module("app.surveys.results")
    monkeypatch.setattr(module, "_hb_part", lambda *args: "original fragment")
    monkeypatch.setattr(report_jobs, "_spawn", lambda payload: (_ for _ in ()).throw(AssertionError("unexpected report job")))
    response = client.get("/surveys/1/results?hb_part=2", headers={"X-Requested-With": "fetch"})
    assert response.status_code == 200 and response.data == b"original fragment"


def test_async_token_runs_one_report_at_a_time_and_results_count_against_the_room(app, client, survey, monkeypatch):
    """Abgleich 2026-10-15 NB-5: asynchronous calls (Prefer: respond-async) took no slot per token, so a token could
    start a full export with every changed query parameter; their results under hb/ counted against no room."""
    from app import report_jobs
    from tests.privacy.test_data_access import _create

    add(client, "single_choice")
    _answer(app)
    _, token = _create(client)
    app.config["REPORT_JOB_THRESHOLD"] = 1
    payloads = []
    monkeypatch.setattr(report_jobs, "_spawn", lambda payload: payloads.append(payload))
    machine = app.test_client()
    headers = {"Authorization": f"Bearer {token}", "Prefer": "respond-async"}
    assert machine.get("/api/v1/surveys/1/data.csv?x=1", headers=headers).status_code == 202
    assert machine.get("/api/v1/surveys/1/data.csv?x=2", headers=headers).status_code == 503
    assert len(payloads) == 1
    base = payloads[0]["base"]
    with open(base + ".body", "wb") as out:
        out.write(b"r" * (2 * 1024 * 1024))
    with app.app_context():
        report_jobs._write(base + ".run.json", {**report_jobs._read(base + ".run.json"), "state": "done"})
    app.config["SURVEY_STORAGE_MAX_MB"] = 1
    refused = machine.get("/api/v1/surveys/1/data.csv?x=3", headers=headers)
    assert refused.status_code == 507 and len(payloads) == 1
    app.config["SURVEY_STORAGE_MAX_MB"] = 0
    assert machine.get("/api/v1/surveys/1/data.csv?x=3", headers=headers).status_code == 202


def test_a_worker_that_died_turns_the_job_failed(app, client, survey, monkeypatch):
    """Code review 2026-10-15, E4: a report worker killed (memory, deploy) left its job "running" for an hour: the
    waiting page went on, and a data access token got 503. The poll looks whether the worker still lives."""
    import subprocess
    import sys

    from app import report_jobs

    add(client, "single_choice")
    _answer(app)
    app.config["REPORT_JOB_THRESHOLD"] = 1
    children = []

    def dies_at_once(payload):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=30)
        children.append(child)
        return child

    monkeypatch.setattr(report_jobs, "_spawn", dies_at_once)
    response = client.get("/surveys/1/export/data.csv")
    assert response.status_code == 202
    url = _poll_url(response)
    failed = client.get(url)
    assert failed.status_code == 503
    with app.app_context():
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", db.session.get(Survey, 1).uid)
    marker = next(os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".run.json"))
    assert report_jobs._read(marker)["state"] == "failed"
    # a worker that lives is left alone
    process = marker[:-len(".run.json")] + ".process.json"
    info = report_jobs._read(process)
    report_jobs._write(marker, {**report_jobs._read(marker), "state": "running"})
    report_jobs._write(process, {**info, "pid": os.getpid(), "identity": None})
    assert client.get(url).status_code == 202
