"""HB and latent class estimations in a process of their own, at the lowest priority (Ralf 2026-10-06, thread
"Serverlast bei den Berechnungen").

An estimation keeps a processor core busy for 5 to 14 minutes at 2000 participations. As a thread in a Passenger
process it shared Python's GIL with the requests of that process (participant pages 45 to 60 % slower, audit
2026-10-06, P7) and died with the process (idle time, deploy). So start() of maxdiff_hb, conjoint_hb and segments
hands the model input to `python -m app.estimate <job file>`, started detached (its own session; on Windows its own
process group) and at the lowest priority (nice 19, Windows: idle class): the web process answers at once, and when
the cores are scarce the operating system serves the requests first. With idle cores the estimation is as fast
as before.

- The job file lies next to the marker (<marker>.job.json): kind, paths, input, data pool, the server's places
  (HB_PARALLEL) and upload folder. The process runs maxdiff_hb._estimate_and_store as the thread did: the same
  marker, result file, places (file locks over all processes) and progress. It needs no database and no Flask app.
- Fallback to the thread as before: ESTIMATE_PROCESS off, no Python found, the process does not start, or it died
  (killed by the host) before finishing: running() sees the aborted marker with the job file still there and
  computes once in a thread (rescue). A process whose number (<job>.<pid>.running) still lives is not rescued, and
  the run beats every maxdiff_hb.BEAT_EVERY seconds also between two progress steps (code review 2026-10-07, C1;
  app/jobs.py, settle).
- Administration > Datenbank checks whether the server lets such a process live (probe): a process that writes a
  sign of life every 10 seconds for PROBE_SECONDS, with its priority.
"""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

PROBE_SECONDS = 900   # how long the probe process lives (long enough to see a deploy or Passenger's idle end)
PROBE_BEAT = 10       # seconds between its signs of life
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def job_path(paths):
    return paths[1] + ".job.json"


def _python():
    """The interpreter of this process, if it is one (under some hosts sys.executable is the server binary)."""
    exe = sys.executable or ""
    return exe if os.path.basename(exe).lower().startswith("python") and os.path.isfile(exe) else None


def _spawn(args):
    """Start python -m app.estimate <args> detached, low priority, with this process's module path (vendor/)."""
    exe = _python()
    if exe is None:
        raise OSError("no Python interpreter for a process of its own")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    kw = {"cwd": ROOT, "env": env, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
          "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                               | subprocess.IDLE_PRIORITY_CLASS)
    else:
        kw["start_new_session"] = True  # not ended with the web process or its group
    return subprocess.Popen([exe, "-m", "app.estimate", *args], **kw)


def start(app, kind, paths, inp, pool):
    """Run the estimation in a process of its own: True if it was started, False if the caller should start the
    thread as before."""
    from app import maxdiff_hb

    if not app.config.get("ESTIMATE_PROCESS"):
        return False
    job = job_path(paths)
    maxdiff_hb._write(job, {"kind": kind, "paths": list(paths), "inp": inp, "pool": pool,
                            "upload": app.config["UPLOAD_FOLDER"], "places": app.config.get("HB_PARALLEL", 2)})
    try:
        _spawn([job])
    except OSError as e:
        app.logger.warning("Estimation process not started (%s): computed in a thread", e)
        _remove(job)
        return False
    return True


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def rescue(paths, marker):
    """An aborted run whose process left its job file: computed once more, in a thread of this process (the host
    may end processes of their own). Returns the new marker, or the given one when there is nothing to rescue.
    Called by jobs.settle only once no process that took the job lives any more."""
    from flask import current_app

    from app import compute, maxdiff_hb

    if not marker or marker.get("state") != "aborted":
        return marker
    import glob

    job = job_path(paths)
    taken = f"{job}.{os.getpid()}.{threading.get_ident()}.taken"
    for left in [job] + glob.glob(glob.escape(job) + ".*.running"):  # not started, or started and gone silent
        try:
            os.rename(left, taken)  # one request alone takes it
            break
        except OSError:
            continue
    else:
        return marker
    data = maxdiff_hb._read(taken)
    _remove(taken)
    if not data:
        return marker
    app = current_app._get_current_object()
    app.logger.warning("Estimation process ended before its result: computed in a thread")
    now = datetime.now(timezone.utc).isoformat()
    maxdiff_hb._write(paths[1], {"state": "running", "started": now, "beat": now, "progress": 0.0,
                                 "key": data["inp"]["key"], "pool": data["pool"]})
    threading.Thread(target=maxdiff_hb._estimate_and_store,
                     args=(app, tuple(data["paths"]), data["inp"], data["pool"], compute.runner(data["kind"])),
                     daemon=True, name="hb-rescue").start()
    return maxdiff_hb._read(paths[1])


def lower_priority():
    """The lowest scheduling priority for this process (nice 19; Windows: idle class); False where not possible."""
    try:
        if hasattr(os, "nice"):
            os.nice(19 - os.nice(0))
            return True
        if os.name == "nt":
            import ctypes

            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            return bool(k32.SetPriorityClass(k32.GetCurrentProcess(), 0x40))  # IDLE_PRIORITY_CLASS
    except OSError:
        pass
    return False


def priority():
    """The nice value of this process, None where there is none."""
    try:
        return os.nice(0) if hasattr(os, "nice") else None
    except OSError:
        return None


# --- the probe on Administration > Datenbank ---------------------------------------------------------------

def probe_path(app):
    return os.path.join(app.config["UPLOAD_FOLDER"], "hb", "_probe.json")


def start_probe(app):
    """Start the probe process; an error text, or None."""
    from app import maxdiff_hb

    path = probe_path(app)
    maxdiff_hb._write(path, {"asked": datetime.now(timezone.utc).isoformat()})
    try:
        _spawn(["--probe", path, str(app.config.get("ESTIMATE_PROBE_SECONDS", PROBE_SECONDS))])
    except OSError as e:
        _remove(path)
        return str(e)
    return None


def probe_state(app):
    """What the probe reported: None (never started), or {asked, started, beat, ends, nice, pid, alive, done, age}."""
    from app import maxdiff_hb

    data = maxdiff_hb._read(probe_path(app))
    if not data:
        return None
    now = datetime.now(timezone.utc)
    if data.get("beat"):
        age = (now - datetime.fromisoformat(data["beat"])).total_seconds()
        data["age"] = round(age)
        data["alive"] = not data.get("done") and age <= 3 * PROBE_BEAT
        data["minutes"] = round((datetime.fromisoformat(data["beat"]) - datetime.fromisoformat(data["started"]))
                                .total_seconds() / 60, 1)
    else:
        data["waiting"] = (now - datetime.fromisoformat(data["asked"])).total_seconds() < 30
    return data


def _probe(path, seconds):
    from app import maxdiff_hb

    lowered = lower_priority()
    data = maxdiff_hb._read(path) or {}
    data.update(started=datetime.now(timezone.utc).isoformat(), pid=os.getpid(), nice=priority(), lowered=lowered)
    end = time.monotonic() + seconds
    while True:
        data["beat"] = datetime.now(timezone.utc).isoformat()
        if time.monotonic() >= end:
            data["done"] = True
        maxdiff_hb._write(path, data)
        if data.get("done"):
            return
        time.sleep(min(PROBE_BEAT, max(0.1, end - time.monotonic())))


# --- the process itself --------------------------------------------------------------------------------------

class _App:
    """What maxdiff_hb._estimate_and_store needs of the app: the places and the log."""

    def __init__(self, data):
        self.config = {"UPLOAD_FOLDER": data["upload"], "HB_PARALLEL": data.get("places", 2)}
        self.logger = logging.getLogger("askurious.estimate")


def main(argv):
    logging.basicConfig(level=logging.INFO)
    if argv[:1] == ["--probe"]:
        return _probe(argv[1], float(argv[2]))
    lower_priority()
    from app import compute, maxdiff_hb

    job = argv[0]
    taken = f"{job}.{os.getpid()}.running"
    try:
        os.rename(job, taken)  # marks it as taken by this process (a rescue takes it only once it went silent)
    except OSError:
        return
    with open(taken, encoding="utf-8") as f:
        data = json.load(f)
    from app import jobs

    try:  # who took it beyond the number, which a new container may give to another process (audit 2026-10-10, W-2)
        maxdiff_hb._write(taken, {**data, "holder": jobs.identity()}, make=False)
    except OSError:
        pass
    try:
        maxdiff_hb._estimate_and_store(_App(data), tuple(data["paths"]), data["inp"], data["pool"],
                                       compute.runner(data["kind"]))
    finally:
        _remove(taken)


if __name__ == "__main__":
    main(sys.argv[1:])
