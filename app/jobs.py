"""Start, state and rescue of the background estimations in one place (code review 2026-10-07, C7): HB of MaxDiff
(maxdiff_hb) and conjoint (conjoint_hb), latent classes (segments), the SEM bootstrap (sem_model) and the omega
bootstrap of the results page (results.omega_part_state). Each caller only gives the kind, the two paths (result,
marker), the input and the data pool.

- start: claim the marker (one run at a time), then the Rechenknecht (app/compute.py), else a process of its own at
  low priority (app/estimate.py), else a thread here; background=False computes in the request (HB_SYNC, tests).
- read: the marker, brought up to date from the Rechenknecht when the job runs there (compute.follow).
- settle: a running marker without a sign of life for maxdiff_hb.STALE_HEARTBEAT seconds is aborted, unless the
  estimation process named in its job file still lives (C1); an aborted run whose process left its job file is
  computed once more in a thread (estimate.rescue).
- forget: the job files of a survey's folder (the job files of the process, the markers, the partial file of a latent
  class run and the Rechenknecht's index), removed by the cleanups (erasure, retention, deleting the survey): a run
  that finds its marker gone stops and writes nothing (C3).
"""
import glob
import os
import threading
from datetime import datetime

# the kinds a person starts by a button; the omega bootstrap starts by itself with the results page and is not counted
COUNTED_KINDS = {"maxdiff_hb", "conjoint_hb", "cva_hb", "paired_hb", "segments", "sem", "sem_power", "panel"}


class EstimateLimit(Exception):
    """This account started ESTIMATE_LIMIT estimations within ESTIMATE_WINDOW (limits.allow_estimate; audit
    2026-10-22, D-04). create_app answers it with 429 and a message."""


DEAD_AFTER = 3600  # seconds without a sign of life after which a living process number no longer counts (the number
#                    may have gone to another process meanwhile; the estimation itself beats every BEAT_EVERY seconds)


def start(app, kind, paths, inp, pool, background=True, name="hb"):
    """Claim the marker and start the estimation: True if started (or computed, background=False), False if one runs
    already."""
    from app import compute, estimate, maxdiff_hb

    user_id = _starter(kind)
    if user_id is not None:
        from app import limits

        if not limits.allow_estimate(user_id, count=False):
            raise EstimateLimit()
    if not maxdiff_hb.claim(paths, inp, pool):  # one run at a time, also for two clicks at once (W-9)
        return False
    if user_id is not None:
        from app import limits
        from app.extensions import db

        limits.allow_estimate(user_id)
        db.session.commit()  # the count stands even when the request ends without a commit of its own (a fetch)
    run = compute.runner(kind)
    if not background:
        maxdiff_hb._estimate_and_store(app, paths, inp, pool, run)
        return True
    if compute.submit(app, kind, paths, inp, pool):  # on the Rechenknecht, if one is set up
        return True
    if estimate.start(app, kind, paths, inp, pool):  # a process of its own at low priority
        return True
    threading.Thread(target=maxdiff_hb._estimate_and_store, args=(app, paths, inp, pool, run), daemon=True,
                     name=name).start()
    return True


def _starter(kind):
    """The account that starts an estimation of a counted kind by a button, else None (no request, the omega
    bootstrap, the tests' synchronous runs)."""
    if kind not in COUNTED_KINDS:
        return None
    from flask import has_request_context
    from flask_login import current_user

    if not has_request_context() or not current_user.is_authenticated:
        return None
    return current_user.id


def read(paths):
    """The marker (None if there is none), followed on the Rechenknecht when the job runs there."""
    from app import maxdiff_hb

    marker = maxdiff_hb._read(paths[1])
    if marker and marker.get("remote"):
        from app import compute

        marker = compute.follow(paths, marker)
    return marker


def silence(marker):
    """Seconds since the last sign of life of a marker."""
    from app import maxdiff_hb

    return (maxdiff_hb._now() - datetime.fromisoformat(marker.get("beat") or marker["started"])).total_seconds()


def stale(paths, marker):
    """Is this running marker silent for longer than STALE_HEARTBEAT, with no living estimation process behind it?"""
    from app import maxdiff_hb

    if not marker or marker.get("state") != "running":
        return False
    quiet = silence(marker)
    return quiet > maxdiff_hb.STALE_HEARTBEAT and not (quiet <= DEAD_AFTER and holder_alive(paths))


def settle(paths, marker):
    """A running marker that went stale: aborted, and once more computed in a thread if its process left the job
    (estimate.rescue). Returns the marker to show (None if the rescue's run is already through)."""
    if not stale(paths, marker):
        return marker
    from app import estimate

    return estimate.rescue(paths, {**marker, "state": "aborted"})


def marker(paths):
    """read and settle: the marker of a running, failed or aborted estimation, None if there is none."""
    m = read(paths)
    return settle(paths, m) if m else None


# --- is the estimation process still there? ----------------------------------------------------------------------

def running_files(paths):
    """The job files taken by an estimation process (estimate.main: <job>.<pid>.running) with their process numbers."""
    from app import estimate

    job = estimate.job_path(paths)
    out = []
    for path in glob.glob(glob.escape(job) + ".*.running"):
        pid = path[len(job) + 1:-len(".running")]
        if pid.isdigit():
            out.append((path, int(pid)))
    return out


def holder_alive(paths):
    """Does an estimation process that took this job still live? The process writes who it is into its job file
    (identity): a number that lives but belongs to another process (started at another time, on another host or
    since another boot, e.g. after the container was started anew) counts as dead (audit 2026-10-10, W-2)."""
    from app import maxdiff_hb

    return any(pid_alive(pid, (maxdiff_hb._read(path) or {}).get("holder")) for path, pid in running_files(paths))


def identity(pid=None):
    """Who a process is beyond its number: the host name, the boot of the kernel and the start time of the process
    (Linux; elsewhere None, then the number has to do)."""
    import socket

    pid = pid or os.getpid()
    return {"host": socket.gethostname(), "boot": _read_text("/proc/sys/kernel/random/boot_id"),
            "start": _start_time(pid)}


def _read_text(path):
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _start_time(pid):
    """Field 22 of /proc/<pid>/stat: the start of the process in clock ticks after the boot (None without /proc)."""
    stat = _read_text(f"/proc/{pid}/stat")
    try:
        return int(stat.rsplit(")", 1)[1].split()[19])
    except (AttributeError, IndexError, ValueError):
        return None


def pid_alive(pid, holder=None):
    """Does the process with this number live (Linux, macOS, Windows)? A zombie (ended, not yet reaped by the web
    process that started it) counts as dead, and so does a number that lives but is not the process holder names
    (identity: another host, another boot or another start time; W-2)."""
    if not pid or pid <= 0:
        return False
    if holder:
        now = identity(pid)
        if any(holder.get(k) is not None and now[k] is not None and holder[k] != now[k] for k in ("host", "boot", "start")):
            return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # it lives, under another user
        pass
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True  # no /proc (macOS): the signal test has to do


def _pid_alive_windows(pid):
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: it exists
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


# --- the cleanups ---------------------------------------------------------------------------------------------

JOB_ENDINGS = (".run.json", ".job.json", ".running", ".taken", ".part.json", ".lock")


# the files in hb/<uid>/ an estimation computed from the answers: HB and latent classes (item_<id>.json, .lc.json),
# the bootstrap of a structural equation model (sem.json) and of omega (omega-<job>.json), with their job files; the
# power simulation of the plan (sem_power.json) holds no answers but stops with every other kind of job; the models over
# the waves of a panel (panel.json) and the estimate of the results page's model (sem_fit.json) are computed from the
# answers
BOOTSTRAP_FILES = ("sem.", "sem_fit.", "omega-", "sem_power.", "panel.")
ANSWER_FILES = ("item_",) + BOOTSTRAP_FILES


def is_job_file(name):
    """A file of a run in hb/<uid>/: marker, job file of the process (also taken), partial file, start lock."""
    return name.endswith(JOB_ENDINGS) or ".job.json." in name


def forget(folder, prefix="item_"):
    """Stop the runs of this folder (hb/<uid>/) whose file names start with prefix: their markers, job files and partial
    files go, and the Rechenknecht's index of their jobs (hb/_remote/, compute._folder). A run that finds its marker
    gone stops and writes no result (maxdiff_hb._estimate_and_store, compute.deliver)."""
    from app import maxdiff_hb

    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.startswith(prefix) and is_job_file(name):
                _remove(os.path.join(folder, name))
    remote = os.path.join(os.path.dirname(os.path.abspath(folder)), "_remote")
    if os.path.isdir(remote):
        inside = os.path.normcase(os.path.abspath(folder)) + os.sep
        for name in os.listdir(remote):
            path = os.path.join(remote, name)
            index = maxdiff_hb._read(path) if name.endswith(".json") else None
            target = (index or {}).get("paths") or [""]
            if os.path.normcase(os.path.abspath(target[0])).startswith(inside) \
                    and os.path.basename(target[0]).startswith(prefix):
                _remove(path)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
