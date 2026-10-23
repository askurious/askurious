"""Estimations in a process of their own at the lowest priority (app/estimate.py), with the thread as fallback."""
import glob
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from app import estimate, jobs, maxdiff_hb
from app.extensions import db
from app.models import Item
from tests.analysis.test_maxdiff_hb import with_dummies


def _paths(app, item_id=2):
    with app.app_context():
        return maxdiff_hb._paths(db.session.get(Item, item_id))


def _stored(app):
    with app.app_context():
        return maxdiff_hb.load(db.session.get(Item, 2))


def _wait(cond, seconds=180):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.25)
    return False


def _join(prefix):
    for t in threading.enumerate():
        if t.name.startswith(prefix):
            t.join(120)


def _silent(app, seconds=maxdiff_hb.STALE_HEARTBEAT + 60):
    path = _paths(app)[1]
    marker = maxdiff_hb._read(path)
    then = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    marker.update(beat=then, started=then)
    maxdiff_hb._write(path, marker)


def test_estimation_runs_in_a_process_of_its_own(app, client, survey):
    with_dummies(app, client, n=15)
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})          # here, HB_SYNC
    local = _stored(app)
    os.remove(_paths(app)[0])
    app.config.update(HB_SYNC=False, ESTIMATE_PROCESS=True)
    r = client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"},
                    headers={"X-Requested-With": "fetch"})
    assert r.get_json()["state"] == "started"
    assert not [t for t in threading.enumerate() if t.name == "hb-item-2"]                       # no thread here
    assert _wait(lambda: os.path.exists(_paths(app)[0]))
    assert _wait(lambda: client.get("/surveys/1/results/hb/2/status").get_json() == {"state": "done"}, 30)
    remote = _stored(app)
    assert remote["pids"] == local["pids"] and remote["util"] == local["util"] and remote["pool"] == local["pool"]
    assert not glob.glob(glob.escape(estimate.job_path(_paths(app))) + "*")                       # job file gone


def test_thread_when_no_process_starts(app, client, survey, monkeypatch):
    with_dummies(app, client, n=12)
    app.config.update(HB_SYNC=False, ESTIMATE_PROCESS=True)

    def refused(args):
        raise OSError("not allowed here")

    monkeypatch.setattr(estimate, "_spawn", refused)
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    _join("hb-item-2")
    assert _stored(app)["pids"] and not os.path.exists(estimate.job_path(_paths(app)))


def test_a_process_that_died_is_computed_again_in_a_thread(app, client, survey, monkeypatch):
    with_dummies(app, client, n=12)
    app.config.update(HB_SYNC=False, ESTIMATE_PROCESS=True)
    monkeypatch.setattr(estimate, "_spawn", lambda args: None)                                    # started, then killed
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    job = estimate.job_path(_paths(app))
    os.rename(job, job + ".4711.running")                                                          # it had taken the job
    monkeypatch.setattr(jobs, "pid_alive", lambda pid, holder=None: False)                        # ... and has ended
    assert client.get("/surveys/1/results/hb/2/status").get_json()["state"] == "running"         # still alive for now
    _silent(app)
    assert client.get("/surveys/1/results/hb/2/status").get_json()["state"] == "running"         # rescued, not aborted
    _join("hb-rescue")
    assert client.get("/surveys/1/results/hb/2/status").get_json() == {"state": "done"}
    assert _stored(app)["pids"] and not glob.glob(glob.escape(job) + "*")


def test_aborted_without_job_stays_aborted(app, client, survey):
    with_dummies(app, client, n=12)
    path = _paths(app)[1]
    now = datetime.now(timezone.utc).isoformat()
    maxdiff_hb._write(path, {"state": "running", "started": now, "beat": now, "progress": 0.2})
    _silent(app)
    assert client.get("/surveys/1/results/hb/2/status").get_json()["state"] == "aborted"


def test_probe_on_the_database_page(app, client):
    page = client.get("/admin/database").get_data(as_text=True)
    assert "Auf diesem Server: eigener Prozess" in page and page.index("Rechenknecht") < page.index("eigener Prozess") and "Ausgeschaltet (ESTIMATE_PROCESS=0" in page
    app.config.update(ESTIMATE_PROCESS=True, ESTIMATE_PROBE_SECONDS=1)
    r = client.post("/admin/database", data={"action": "probe"})
    assert r.status_code == 302 and r.location.endswith("#prozess")
    assert _wait(lambda: (estimate.probe_state(app) or {}).get("done"), 60)
    state = estimate.probe_state(app)
    assert state["pid"] != os.getpid() and state["lowered"]
    if hasattr(os, "nice"):
        assert state["nice"] == 19
    page = client.get("/admin/database").get_data(as_text=True)
    assert "bis zum Ende gelaufen" in page and "Ausgeschaltet" not in page

