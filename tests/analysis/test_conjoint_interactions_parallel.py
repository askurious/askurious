"""The confirmation of the interaction search with its HB runs side by side in processes of their own (WW_PARALLEL,
Ralf 2026-11-24): the same result as one after another, the number of processes, errors and cancelling."""
import multiprocessing

import pytest

from app import conjoint_hb
from app import conjoint_interactions as ww
from tests.analysis.test_conjoint_interactions import _inp, simulate
from tests.helpers import ww_child

SMALL = {"burn": 100, "draws": 200, "thin": 2, "seed": 3}


def _without_time(res):
    return {k: v for k, v in res.items() if k != "seconds"}


def test_two_processes_give_exactly_the_result_of_one(monkeypatch):
    coding, people, _ = simulate(0.8, persons=120, tasks=6, seed=3)
    inp = {**_inp(coding, people), "settings": SMALL}
    monkeypatch.setenv("WW_PARALLEL", "1")
    one = ww.run(inp)
    assert one["flagged"] >= 1                                     # the confirmation ran
    seen = []
    monkeypatch.setenv("WW_PARALLEL", "2")
    monkeypatch.setattr(ww, "_load", lambda: None)                 # a busy test machine must not lower it to 1
    two = ww.run(inp, seen.append)
    assert _without_time(two) == _without_time(one)
    assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) > 5   # the bar moves on and ends full


def test_processes_follow_setting_runs_and_free_cores(monkeypatch):
    assert ww.processes(9, wanted=4, cpus=8, limit=8, load=0.5) == 4
    assert ww.processes(9, wanted=4, cpus=8, limit=8, load=6.2) == 2      # 8 - 6.2 = 1.8 free, plus its own core
    assert ww.processes(9, wanted=4, cpus=8, limit=8, load=20.0) == 1     # busy machine: at least one
    assert ww.processes(9, wanted=4, cpus=27, limit=3, load=1.0) == 3     # container quota
    assert ww.processes(9, wanted=4, cpus=8, limit=8, load=None) == 4     # no load average (Windows)
    assert ww.processes(2, wanted=4, cpus=8, limit=8, load=0.0) == 2      # at most the runs
    assert ww.processes(9, wanted=1, cpus=8, limit=8, load=0.0) == 1
    monkeypatch.setenv("WW_PARALLEL", "2")
    assert ww.parallel_setting() == 2
    monkeypatch.setenv("WW_PARALLEL", "0")
    assert ww.parallel_setting() == 1
    monkeypatch.setenv("WW_PARALLEL", "viele")
    assert ww.parallel_setting() == ww.PARALLEL == 4
    monkeypatch.delenv("WW_PARALLEL")
    assert ww.parallel_setting() == 4


def test_runtime_hint_follows_the_processes():
    assert ww.runtime_minutes(64, 3, 1) == (4, 4)                 # 367 persons: 12 min for 3 divisions and 2 pairs
    assert ww.runtime_minutes(64, 3, 4) == (1, 1)
    assert ww.runtime_minutes(62.5, 12, 4) == (6, 6)              # every task once, 12 tasks: measured 17:23 min
    assert ww.runtime_minutes(None, 3, 4) is None


def _confirm(people, coding, **extra):
    return ww.confirm(people, coding, [(0, 1)], SMALL, conjoint_hb._prior(coding), extra, 3, workers=2)


@pytest.mark.parametrize("broken, message", [(ww_child.fail, "Confirmation run .* failed: ValueError"),
                                             (ww_child.die, "Confirmation run .* ended without a result")])
def test_a_broken_run_ends_the_job_with_an_error(broken, message):
    coding, people, _ = simulate(0.5, persons=40, tasks=5, seed=4)
    with pytest.raises(RuntimeError, match=message):
        _confirm(people, coding, person_stat=broken)
    assert not [p for p in multiprocessing.active_children() if p.name.startswith("confirmation-run")]


def test_a_cancelled_job_ends_the_processes():
    coding, people, _ = simulate(0.5, persons=40, tasks=5, seed=4)

    class Cancelled(Exception):
        pass

    def progress(fraction):
        raise Cancelled()

    with pytest.raises(Cancelled):
        ww.confirm(people, coding, [(0, 1)], SMALL, conjoint_hb._prior(coding), {}, 3, progress=progress, workers=2)
    assert not [p for p in multiprocessing.active_children() if p.name.startswith("confirmation-run")]
