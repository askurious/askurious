"""Audit „Daten und Berechnungen“ 2026-10-10, package G (choice models): HB and segment results coded before the dual
response fix are no longer current (W-1), a process number that a new process took is no sign of life (W-2), the
part-worth power of a price marked as numbers is the power of its linear coefficient (W-3), the ESS wording (W-4)."""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app import estimate, jobs, maxdiff_hb
from app.extensions import db
from app.models import Item


# --- W-1: results of the old coding are estimated anew ----------------------------------------------------------------

def test_old_format_and_coding_are_not_current(app, client, survey, monkeypatch):
    from app import conjoint_hb, export, segments
    from tests.analysis.test_cbc_results import setup
    from tests.analysis.test_conjoint_hb import simulate_segments

    assert conjoint_hb.FORMAT == 7 and segments.FORMAT == 2 and conjoint_hb.CODING == 2
    setup(client, tasks=6, versions=2)
    simulate_segments(app, 60)
    with app.app_context():
        item = db.session.get(Item, 2)
        table = list(export.rows(item.survey, with_hb=False))
        now = conjoint_hb.model_input(item, table)
        with monkeypatch.context() as m:
            m.setattr(conjoint_hb, "FORMAT", 5)
            old = conjoint_hb.model_input(item, table)
        with monkeypatch.context() as m:
            m.setattr(conjoint_hb, "CODING", 3)  # a later change of the coding changes the key on its own
            assert conjoint_hb.model_input(item, table)["key"] != now["key"]
        assert old["key"] != now["key"] and old["data_key"] != now["data_key"]
        # a result stored before: FORMAT 5 with the key of then
        path = maxdiff_hb._paths(item)[0]
        maxdiff_hb._write(path, {"format": 5, "type": "conjoint", "key": old["key"], "data_key": old["data_key"],
                                 "settings": None, "pids": [], "mu": []})
        assert conjoint_hb.current(item) is None and conjoint_hb._stored(item) is None
        started = []
        monkeypatch.setattr(jobs, "start", lambda *a, **k: started.append(a) or True)
        assert conjoint_hb.start(item, table, {}) == "started" and started   # not "passt schon"
        # the latent classes: their own FORMAT is part of their key, a stored FORMAT 1 is not read
        seg = segments.model_input(item, table)
        with monkeypatch.context() as m:
            m.setattr(segments, "FORMAT", 1)
            assert segments.model_input(item, table)["key"] != seg["key"]
        maxdiff_hb._write(segments._paths(item)[0], {"format": 1, "type": "conjoint", "key": seg["key"]})
        assert segments._stored(item) is None


# --- W-2: who holds a job beyond the process number --------------------------------------------------------------------

def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_pid_alive_compares_the_identity(monkeypatch):
    me = jobs.identity()
    assert jobs.pid_alive(os.getpid(), me) and jobs.pid_alive(os.getpid())
    assert not jobs.pid_alive(os.getpid(), {**me, "host": me["host"] + "-other"})      # another container
    assert not jobs.pid_alive(os.getpid(), {**me, "boot": "another-boot"}) or me["boot"] is None
    assert not jobs.pid_alive(_dead_pid(), me)
    # the number went to another process: started at another time (Linux: field 22 of /proc/<pid>/stat)
    monkeypatch.setattr(jobs, "_start_time", lambda pid: 1000)
    assert not jobs.pid_alive(os.getpid(), {**me, "start": 999})
    assert jobs.pid_alive(os.getpid(), {**me, "start": 1000})
    assert jobs.pid_alive(os.getpid(), {"host": None, "boot": None, "start": None})    # unknown: the number has to do


@pytest.fixture
def paths(app):
    folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", "uid1")
    os.makedirs(folder)
    return os.path.join(folder, "item_1.json"), os.path.join(folder, "item_1.run.json")


def test_estimation_process_writes_its_identity_and_a_stranger_is_rescued(app, paths, monkeypatch):
    seen = []
    job = estimate.job_path(paths)
    maxdiff_hb._write(job, {"kind": "maxdiff_hb", "paths": list(paths), "inp": {"key": "k"}, "pool": {},
                            "upload": app.config["UPLOAD_FOLDER"]})
    monkeypatch.setattr(estimate, "lower_priority", lambda: None)
    monkeypatch.setattr(maxdiff_hb, "_estimate_and_store",
                        lambda *a, **k: seen.append(maxdiff_hb._read(f"{job}.{os.getpid()}.running")))
    estimate.main([job])
    assert seen and seen[0]["holder"] == jobs.identity() and seen[0]["inp"] == {"key": "k"}
    # a job file of this very number, but written by a process on another host: it counts as dead
    taken = f"{job}.{os.getpid()}.running"
    maxdiff_hb._write(taken, {"kind": "maxdiff_hb", "paths": list(paths), "inp": {"key": "k"}, "pool": {},
                              "holder": {**jobs.identity(), "host": "old-container"}})
    assert not jobs.holder_alive(paths)
    maxdiff_hb._write(taken, {"kind": "maxdiff_hb", "paths": list(paths), "inp": {"key": "k"}, "pool": {},
                              "holder": jobs.identity()})
    assert jobs.holder_alive(paths)
    then = (datetime.now(timezone.utc) - timedelta(seconds=maxdiff_hb.STALE_HEARTBEAT + 5)).isoformat()
    maxdiff_hb._write(paths[1], {"state": "running", "started": then, "beat": then, "progress": 0.5, "key": "k",
                                 "pool": {}})
    assert not jobs.stale(paths, maxdiff_hb._read(paths[1]))


# --- W-3: the price as a number has one coefficient ------------------------------------------------------------------

class _PriceKind:
    def __init__(self, levels, pa, numbers):
        self.levels, self.pa, self.numbers = levels, pa, numbers

    def design_current(self, item):
        return True

    def level_counts(self, item):
        return self.levels

    def attributes(self, item, lang=None):
        return [{"label": f"M{i}", "level_labels": [f"s{j}" for j in range(n)]} for i, n in enumerate(self.levels)]

    def price_attribute(self, item):
        return self.pa

    def level_numbers(self, item, attr):
        return self.numbers


def test_power_of_a_linear_price_is_its_coefficient():
    from app import power_choice
    from app.conjoint_design import _encode, generate

    levels = [3, 3, 4]
    numbers = [10.0, 20.0, 30.0, 60.0]
    design, _eff = generate(levels, 8, 2, 2, seed=1)
    item = SimpleNamespace(kind=_PriceKind(levels, 2, numbers), config={"design": {"versions": design},
                                                                         "alternatives": 2})
    cp = power_choice.conjoint(item)
    # M0 and M1 per level, the price one row: its coefficient at the level farthest from the mean (60 against 30)
    assert cp["labels"] == ["M0: s0", "M0: s1", "M0: s2", "M1: s0", "M1: s1", "M1: s2", "M2: s3 (linear)"]
    assert cp["price"] == "s3"
    mean, scale = np.mean(numbers), np.std(numbers)
    info = np.zeros((5, 5))
    for version in design:
        for task in version:
            task = np.asarray(task)
            X = np.hstack([_encode(task[:, :2], levels[:2]), ((np.array(numbers)[task[:, 2]] - mean) / scale)[:, None]])
            d = X - X.mean(0)
            info += d.T @ d / 2
    cov = np.linalg.inv(info / len(design))
    assert cp["var"][-1] == pytest.approx(((60 - mean) / scale) ** 2 * cov[4, 4])
    assert cp["var"][0] == pytest.approx(cov[0, 0])
    # without the price mark: a part-worth per level as before
    item.kind.pa = None
    plain = power_choice.conjoint(item)
    assert len(plain["var"]) == 10 and plain["price"] is None


def test_samplesize_page_says_the_price_is_linear(app, client, survey):
    from tests.analysis.test_cbc_results import setup

    setup(client, price_attr=True, tasks=10, versions=4)
    html = client.get("/sample-size/?survey=1").data.decode()
    assert "data-ss-choice-data" in html and "data-ss-choice-price" in html
    assert "Das Modell schätzt für den Preis einen Koeffizienten" in html and "(150 €)" in html


# --- W-4: the ESS is an equivalent number ---------------------------------------------------------------------------

def test_ess_wording(app):
    from app import hb_convergence as hc

    with app.test_request_context("/"):
        note = hc.warning({"diag_ok": False, "ess_min": 230, "kept": 1500, "rhat_max": 1.0, "geweke_ok": True})
    assert "die 1.500 Ziehungen tragen nur so viel Information wie 230 unabhängige Ziehungen, nötig sind" in note
    assert "voneinander unabhängig" not in note
