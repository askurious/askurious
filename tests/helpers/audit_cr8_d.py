"""Helpers of the tests from test_cr8_d.py; the tests themselves are in tests/analysis/test_analysis_audit_findings.py, tests/live/test_live_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Code review 2026-10-07, package D "Schätzungen und Aufträge" (C1, C3-C8): start, state and rescue of the background
estimations in one module (app/jobs.py), a living estimation process is not rescued, cleanups stop running runs,
a missing marker is no error, the expected effect from an earlier survey without a flood of export tables, the PSM axis
without an endless loop, the conjoint HB input built once per table.
"""
import json


import os


import subprocess


import sys


import threading


import time


from datetime import datetime, timedelta, timezone


import pytest


from app import compute, estimate, jobs, maxdiff_hb


from app.extensions import db


from app.models import Survey


def _old(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _dead_pid():
    """The number of a process that has ended."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _stale_marker(paths, seconds=maxdiff_hb.STALE_HEARTBEAT + 5):
    then = _old(seconds)
    maxdiff_hb._write(paths[1], {"state": "running", "started": then, "beat": then, "progress": 0.5, "key": "k",
                                 "pool": {}})


def _job_file(app, paths, pid):
    maxdiff_hb._write(estimate.job_path(paths) + f".{pid}.running",
                      {"kind": "maxdiff_hb", "paths": list(paths), "inp": {"key": "k"}, "pool": {},
                       "upload": app.config["UPLOAD_FOLDER"]})


@pytest.fixture
def paths(app):
    folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", "uid1")
    os.makedirs(folder)
    return os.path.join(folder, "item_1.json"), os.path.join(folder, "item_1.run.json")


def _ticks_before(lo, hi, count=6):
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / count
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = -(-lo // step) * step
    ticks, t = [], first
    while t <= hi + 1e-9:
        ticks.append(round(t, 10) + 0.0)
        t += step
    return ticks
