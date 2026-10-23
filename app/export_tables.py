"""Private, one-hour export-table cache shared by file formats/processes (SP13 PF-4)."""
import contextvars
import functools
import hashlib
import json
import os
from datetime import timedelta

from flask import current_app

_enabled = contextvars.ContextVar("export_file_table", default=False)


def file_format(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        token = _enabled.set(True)
        try:
            return function(*args, **kwargs)
        finally:
            _enabled.reset(token)
    return wrapped


def forget(survey):
    from app import report_jobs, results_store

    import glob

    folder = os.path.dirname(results_store._path(survey, name="export-table"))
    for path in glob.glob(os.path.join(folder, "export-table*.bin")):
        with report_jobs._claim(path + ".claim"):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def sweep():
    import glob
    import time
    from app import report_jobs

    for path in glob.glob(os.path.join(current_app.config["UPLOAD_FOLDER"], "hb", "*", "export-table*.bin")):
        with report_jobs._claim(path + ".claim"):
            try:
                if time.time() - os.stat(path).st_mtime >= 3600:
                    os.remove(path)
            except FileNotFoundError:
                pass


def table(survey, options, build):
    from app import export, quality, report_jobs, results_store
    from app.models import utcnow

    if not _enabled.get() or not survey.id:
        return build()
    pool = {"sources": options["sources"] or ["real"], "all": options["include_incomplete"],
            "drop": options["drop_speeders"], "speeder": options["speeder"], "speed_real": options["speed_real"],
            "weighted": True}
    def key():
        folder = os.path.dirname(results_store._path(survey))
        estimates = []
        for root, _, files in os.walk(folder):
            for name in files:
                if name.endswith(".bin") or name.endswith(".claim") or ".tmp" in name:
                    continue
                try:
                    stat = os.stat(os.path.join(root, name))
                    estimates.append((os.path.relpath(os.path.join(root, name), folder), stat.st_size, stat.st_mtime_ns))
                except FileNotFoundError:
                    pass
        basis = [results_store.fingerprint(survey, pool), quality.stamp(survey), options, sorted(estimates)]
        return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()
    path = results_store._path(survey, name="export-table")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with report_jobs._claim(path + ".claim"):
        wanted = key()
        found = results_store.load(survey, wanted, name="export-table")
        if found and found[1] and utcnow() - found[1] < timedelta(hours=1):
            return export.Table(found[0])
        data = build()
        # kept only if nothing changed while building (results_store.save checks after writing)
        if results_store.save(survey, wanted, list(data), name="export-table", basis=key) and os.path.isfile(path):
            os.chmod(path, 0o600)
        return data
