"""The omega bootstrap of the results page as a job of its own (app/omega_job.py, results.omega_part_state): the part
answers 202 with the progress while it runs, then the same intervals as drawn in the request; in the estimation
process, on the Rechenknecht or in a thread."""
import threading
import time

from app import compute, compute_node, omega_job, results, results_store
from app.extensions import db
from app.models import Survey
from tests.helpers.audit_audit_2026_10_06_f import FETCH, _as_the_script_does, _body, _likert, _quiet, _scale_rows

PART = "/surveys/1/results?omega_part=1"


def _join():
    for t in threading.enumerate():
        if t.name.startswith("omega-"):
            t.join(60)


def _until_done(client, seconds=120):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        r = client.get(PART, headers=FETCH)
        if r.status_code != 202:
            return r
        time.sleep(0.25)
    raise AssertionError("still running")


def test_part_waits_for_the_job_and_brings_the_same_intervals(app, client, survey):
    _likert(app, client, _scale_rows())
    _quiet(client)
    first = client.get("/surveys/1/results").data.decode()
    app.config["OMEGA_BACKGROUND"] = True
    r = client.get(PART, headers=FETCH)
    assert r.status_code == 202 and r.get_json()["state"] == "running" and r.headers["Cache-Control"] == "no-store"
    _join()
    part = client.get(PART, headers=FETCH)
    assert part.status_code == 200
    again = client.get("/surveys/1/results").data.decode()
    assert _body(_as_the_script_does(first, part.data.decode())) == _body(again)
    with app.app_context():
        s = db.session.get(Survey, 1)
        inline = results.compute(s, ("real",), False, True, omega_ci=True)
        scale = next(e for e in inline["items"] if e["item"].code == "TA")["scale"]
    assert list(scale["omega_ci"]) in (list(c) for c in [_kept(app)["2:"]["ci"]])
    assert client.get(PART, headers=FETCH).status_code == 200                       # kept: no new job
    assert not [t for t in threading.enumerate() if t.name.startswith("omega-")]


def _kept(app):
    from app.surveys import stored_pool

    with app.app_context():
        s = db.session.get(Survey, 1)
        f = {**stored_pool(s, drop=True), "drop": True, "speed_real": True, "weighted": False}
        return results_store.load(s, results_store.fingerprint(s, f), {"weighted": False}, name="omega")[0]


def test_the_job_runs_in_the_estimation_process(app, client, survey):
    _likert(app, client, _scale_rows())
    _quiet(client)
    client.get("/surveys/1/results")
    app.config.update(OMEGA_BACKGROUND=True, ESTIMATE_PROCESS=True)
    assert client.get(PART, headers=FETCH).status_code == 202
    assert not [t for t in threading.enumerate() if t.name.startswith("omega-")]                 # not here
    assert _until_done(client).status_code == 200 and _kept(app)["2:"]["ci"]


def test_the_job_on_the_rechenknecht_and_its_input_without_ids(app, client, survey, monkeypatch):
    import requests

    from tests.analysis.test_compute import NODE, SECRET, _Answer

    node = compute_node.create(secret=SECRET, callback="", inline=True)
    sent = []

    def post(url, data=None, headers=None, timeout=None):
        sent.append(compute.unpack(data))
        return _Answer(node.test_client().post(url[len(NODE):], data=data, headers=headers))

    def get(url, headers=None, timeout=None):
        return _Answer(node.test_client().get(url[len(NODE):], headers=headers))

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    _likert(app, client, _scale_rows())
    _quiet(client)
    client.get("/surveys/1/results")
    app.config.update(OMEGA_BACKGROUND=True, COMPUTE_URL=NODE, COMPUTE_SECRET=SECRET)
    assert client.get(PART, headers=FETCH).status_code == 202
    job = sent[-1]
    assert job["kind"] == "omega" and set(job["inp"]) == {"key", "scales"}
    assert set(job["inp"]["scales"]["2:"]) == {"matrix", "persons", "pooled"} and job["inp"]["scales"]["2:"]["persons"] is None
    monkeypatch.setattr(compute, "QUIET", 0)
    assert client.get(PART, headers=FETCH).status_code == 200 and _kept(app)["2:"]["ci"]


def test_run_equals_the_bootstrap_in_compute():
    rows = _scale_rows(n=50, k=4, seed=5)
    from app import intervals

    direct = intervals.omega_boot(rows)
    out = omega_job.run({"key": "k", "scales": {"7:": {"matrix": rows, "persons": None, "pooled": False}}})
    assert out == {"key": "k", "found": {"7:": {"ci": direct[0], "failed": direct[1], "pooled": False}}}


def test_admin_heading(client):
    page = client.get("/admin/database").get_data(as_text=True)
    assert "Schätzungen und Bootstraps" in page and "Omega-Intervalle der Ergebnisseite" in page


def test_scale_table_takes_the_intervals_from_the_job(app, client, survey):
    """Code review 2026-10-15, A4: the Word table of the scales drew the omega bootstrap in the request, without a
    place for heavy evaluations; now it is limited like the methods section and waits for the job of the page."""
    from app import heavy

    _likert(app, client, _scale_rows())
    _quiet(client)
    client.get("/surveys/1/results")
    app.config["OMEGA_BACKGROUND"] = True
    r = client.get("/surveys/1/results/scales.docx?lang=de")
    assert r.status_code == 302 and r.headers["Location"].endswith("/surveys/1/results")      # the job runs
    assert "werden gerade berechnet" in client.get("/surveys/1/results").get_data(as_text=True)
    _join()
    r = client.get("/surveys/1/results/scales.docx?lang=de")
    assert r.status_code == 200 and r.headers["Content-Type"].endswith("wordprocessingml.document")
    assert _kept(app)["2:"]["ci"]
    app.config.update(HEAVY_PARALLEL=1, HEAVY_WAIT=0)
    held = heavy._semaphore(1)
    assert held.acquire(timeout=1)
    try:
        assert client.get("/surveys/1/results/scales.docx?lang=de").status_code == 503
    finally:
        held.release()
