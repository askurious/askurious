"""Large reports run in a separate process; authenticated polling/downloads keep their original route.

No cookie, key or database credential is written to the job file. The child receives its configuration/session
through stdin. Artifacts are private, short-lived, re-authorised on every read and cancelled on erasure.
"""
import functools
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager, nullcontext
from urllib.parse import urlencode

from flask import abort, current_app, g, has_app_context, render_template, request, send_file, session
from flask_login import current_user

from app.extensions import db
from app.models import Response

TTL = 3600
JOB = re.compile(r"^[a-f0-9]{32}$")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def forget(survey):
    """Cancel report workers and remove every cached response containing the erased data."""
    forget_uid(survey.uid)


def _stop(info):
    """Terminate only our positively identified Linux child, using a pidfd to avoid PID reuse."""
    from app import jobs

    if not info or not hasattr(os, "pidfd_open"):
        return
    pid, identity = info.get("pid"), info.get("identity") or {}
    if not pid or identity.get("start") is None:
        return
    try:
        fd = os.pidfd_open(pid)
        try:
            if jobs.identity(pid) != identity:
                return
            with open(f"/proc/{pid}/cmdline", "rb") as command:
                if b"app.report_jobs" not in command.read().split(b"\0"):
                    return
            import signal

            signal.pidfd_send_signal(fd, signal.SIGTERM)
        finally:
            os.close(fd)
    except (OSError, ProcessLookupError):
        pass  # already ended; the missing marker still prevents a late result


def forget_uid(uid):
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "hb", uid)
    if os.path.isdir(folder):
        bases = {match.group(0) for name in os.listdir(folder)
                 if (match := re.match(r"report-[a-f0-9]{64}(?=\.)", name))}
        for name in bases:
            base = os.path.join(folder, name)
            # Publication and erasure use the same lock: a finishing worker cannot revive an erased report.
            with _claim(base + ".claim"):
                _remove(base + ".run.json")
                _stop(_read(base + ".process.json"))
                for file in os.listdir(folder):
                    if file.startswith(name + ".") and not file.endswith(".claim"):
                        try:
                            _remove(os.path.join(folder, file))
                        except PermissionError:
                            if not file.endswith(".tmp"):
                                raise
                            # Windows cannot unlink an open file; the cancelled worker removes its temp file.


def sweep():
    root = os.path.join(current_app.config["UPLOAD_FOLDER"], "hb")
    if os.path.isdir(root):
        for name in os.listdir(root):
            folder = os.path.join(root, name)
            if os.path.isdir(folder):
                _sweep(folder)


def _sweep(folder):
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if name.startswith("report-") and not name.endswith(".claim") and time.time() - os.path.getmtime(path) > TTL:
                _remove(path)
        except FileNotFoundError:
            pass


def _failed(base, job):
    with _claim(base + ".claim"):
        marker = base + ".run.json"
        if (_read(marker) or {}).get("job") == job:
            _write(marker, {"job": job, "state": "failed", "started": time.time()})


@contextmanager
def _claim(path):
    from app.storage_room import writing

    # Create the coordination byte under accounting, then release admission before
    # waiting for the claim: thumbnail users take claim -> admission, never the reverse.
    with ExitStack() as stack:
        with writing(None, 0, check=False) if has_app_context() else nullcontext():
            file = stack.enter_context(open(path, "a+b"))
            if os.name == "nt":
                if os.path.getsize(path) == 0:
                    file.write(b"0")
                    file.flush()
        if os.name == "nt":
            import msvcrt

            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        yield


def _spawn(payload):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    options = {"cwd": root, "stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.IDLE_PRIORITY_CLASS
    else:
        options["start_new_session"] = True
        place = g.get("_heavy_place")
        if place is not None:
            payload["slot_fd"] = place.file.fileno()
            options["pass_fds"] = (place.file.fileno(),)
    child = subprocess.Popen([sys.executable, "-m", "app.report_jobs"], **options)
    try:
        child.stdin.write(json.dumps(payload).encode("utf-8"))
        child.stdin.close()
    except (OSError, BrokenPipeError):
        child.terminate()
        raise
    threading.Thread(target=child.wait, daemon=True, name="report-reaper").start()
    return child


def background(view=None, *, count=None, ready=None):
    """From REPORT_JOB_THRESHOLD participations on the view runs in a process of its own, a waiting page polls it.
    count(survey): the participations that decide it where they are not the survey's own (the merged waves: those of
    every wave, app/waves.py). ready(survey, **view_args): True when the view can answer without computing anew (kept
    results, the small codebook): then it answers in the request, whatever the size (performance audit 2026-10-18,
    PERF-03: a process of its own took 3 to 4 s for results kept already, 0.3 s in the request). In a process that
    also serves participants (SIDE not "researcher", app/hosts.py) a computation estimated at more than about a second
    (participations times elements from REPORT_JOB_COST on) leaves the process below the threshold too, so that it
    does not slow down taking part (PERF-06)."""
    if view is None:
        return lambda v: background(v, count=count, ready=ready)

    @functools.wraps(view)
    def wrapped(survey_id, *args, **kwargs):
        if g.get("_report_worker"):
            return view(survey_id, *args, **kwargs)
        if request.endpoint == "surveys.results" and request.headers.get("X-Requested-With") == "fetch" \
                and any(request.args.get(name) for name in ("hb_part", "lc_part", "omega_part", "sim_part")):
            # These endpoints already use background estimation/status protocols; their callers
            # expect a fragment/JSON, not a second waiting page.
            return view(survey_id, *args, **kwargs)
        from app.surveys.base import get_survey

        survey = get_survey(survey_id)  # checks current permissions on every poll/download
        if "kind" in kwargs and kwargs["kind"] not in ("data", "codebook"):
            abort(404)
        n = count(survey) if count else \
            db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=survey.id))
        if not request.args.get("_report_job"):
            if not offload(survey, n):
                return view(survey_id, *args, **kwargs)
            if ready is not None and ready(survey, **kwargs):
                return view(survey_id, *args, **kwargs)
        if request.endpoint == "surveys.results" and not survey.can(current_user, "editor") \
                and (request.args.get("nur_var") or request.args.get("durchgang")):
            abort(403)
        return _response(survey)
    return wrapped


def serves_participants():
    """This process also answers participants: one address for everything, or the participants' container
    (app/hosts.py). Only a researchers' container (SIDE=researcher) does not."""
    from app import hosts

    return current_app.config.get("SIDE") != hosts.RESEARCHER


def offload(survey, n):
    """Whether a report over `n` participations of the survey leaves the web process (see background). REPORT_JOB_
    THRESHOLD 0 keeps every report in the request; REPORT_JOB_COST 0 switches the estimate off."""
    threshold = int(current_app.config.get("REPORT_JOB_THRESHOLD", 5000))
    if threshold <= 0:
        return False
    if n >= threshold:
        return True
    cost = int((current_app.config.get("REPORT_JOB_COST", 50_000) if serves_participants()
                else current_app.config.get("REPORT_JOB_COST_RESEARCHER", 300_000)) or 0)
    if cost <= 0 or not n:
        return False
    from app.models import Block, Item

    items = db.session.scalar(db.select(db.func.count(Item.id)).join(Block, Block.id == Item.block_id)
                              .where(Block.survey_id == survey.id)) or 0
    return n * max(items, 1) >= cost


def api_response(survey, token_id):
    """Machine clients keep their existing 200/download contract while the computation runs separately."""
    if g.get("_report_worker"):
        return None
    count = db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=survey.id))
    if not request.args.get("_report_job") and not offload(survey, count):
        return None
    from app import heavy

    # All workers/containers share these slots; a token cannot occupy the whole pool.
    # Acquire without waiting: requests queued for admission would themselves consume threads.
    asynchronous = "respond-async" in {part.strip().lower() for part in request.headers.get("Prefer", "").split(",")}
    with ExitStack() as slots:
        if not asynchronous:
            token_key = hashlib.sha256(str(token_id).encode()).hexdigest()
            for namespace, count in (("report-api-token-" + token_key, 1),
                                     ("report-api-wait", min(8, max(1, int(current_app.config.get("REPORT_API_WAITERS", 4)))))):
                try:
                    slot = heavy._shared_slot(count, 0, namespace=namespace)
                except OSError:
                    current_app.logger.exception("Report API slots unavailable")
                    return heavy.busy()
                if slot is None:
                    return heavy.busy()
                slots.callback(slot.close)
        return _api_poll(survey, token_id, asynchronous)


def _api_poll(survey, token_id, asynchronous):
    deadline = time.monotonic() + min(30, max(0, float(current_app.config.get("REPORT_JOB_WAIT", 10))))
    job = None
    while True:
        response = current_app.make_response(_response(survey, principal=f"api:{token_id}", machine=True, job_override=job))
        if response.status_code != 202:
            return response
        job = response.headers["X-Report-Job"]
        if asynchronous:
            pairs = [(k, v) for k, values in request.args.lists() if k != "_report_job" for v in values]
            response.headers["Location"] = request.path + "?" + urlencode(pairs + [("_report_job", job)])
            response.headers["Preference-Applied"] = "respond-async"
            return response
        response.close()
        if time.monotonic() >= deadline:
            from app import heavy

            return heavy.busy()
        time.sleep(min(.5, max(0, deadline - time.monotonic())))


def _response(survey, principal=None, machine=False, job_override=None):
    # One active job per user and exact view; neither another user nor a changed URL can retrieve it.
    pairs = [(k, v) for k, values in request.args.lists() if k != "_report_job" for v in values]
    uri = request.path + ("?" + urlencode(pairs) if pairs else "")
    from app.lang import current

    principal = current_user.id if principal is None else principal
    key = hashlib.sha256(json.dumps([principal, uri, request.host, current()]).encode()).hexdigest()
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "hb", survey.uid)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    _sweep(folder)
    base = os.path.join(folder, "report-" + key)
    with _claim(base + ".claim"):
        return _response_locked(survey, pairs, uri, base, machine, job_override, str(principal))


def _token_running(folder, principal):
    """Whether a report of this data access token is still being computed in the survey's folder: a token runs one
    job at a time, also asynchronously (Prefer: respond-async), which took no slot per token before; a script could
    start a full export with every changed query parameter (audit 2026-10-15, NB-5)."""
    for name in os.listdir(folder):
        if name.startswith("report-") and name.endswith(".run.json"):
            info = _read(os.path.join(folder, name)) or {}
            if info.get("state") == "running" and info.get("principal") == principal \
                    and time.time() - float(info.get("started") or 0) < TTL \
                    and not _worker_dead(os.path.join(folder, name[:-len(".run.json")]), info):
                return True
    return False


def _results_bytes(folder):
    """Bytes of the finished reports kept in the survey's folder (report-*.body, kept for TTL)."""
    total = 0
    for name in os.listdir(folder):
        if name.startswith("report-") and (name.endswith(".body") or name.endswith(".body.tmp")):
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


def _worker_dead(base, info):
    """The worker of this running job has ended without a result (killed for memory, a deploy, a restart): the
    page then said "running" for an hour, and a data access token got 503 that long (code review 2026-10-15, E4).
    Only a worker this host started is judged; one of another container cannot be seen from here."""
    import socket

    from app import jobs

    process = _read(base + ".process.json")
    if not process or process.get("job", info.get("job")) != info.get("job"):
        return False
    holder = process.get("identity") or {}
    if holder.get("host") not in (None, socket.gethostname()):
        return False
    return not jobs.pid_alive(process.get("pid"), holder)


def _response_locked(survey, pairs, uri, base, machine=False, job_override=None, principal=None):
    marker = base + ".run.json"
    info = _read(marker)
    if info and info.get("state") == "running" and _worker_dead(base, info):
        info = {**info, "state": "failed"}
        _write(marker, info)
    job = job_override or request.args.get("_report_job")
    if job:
        if not JOB.fullmatch(job) or info is None or info.get("job") != job:
            abort(410)
        if info["state"] == "failed":
            if machine:
                from app import heavy

                return heavy.busy()
            return render_template("surveys/report_failed.html", survey=survey, retry=uri), 503
        meta = _read(base + ".meta.json")
        if meta and info["state"] == "done" and os.path.isfile(base + ".body"):
            if not machine and request.headers.get("X-Report-Poll") == "1":
                return "", 204, {"X-Report-Ready": "1", "Cache-Control": "no-store"}
            response = send_file(base + ".body", mimetype=meta["type"], conditional=False)
            response.status_code = meta["status"]
            if meta.get("attachment"):
                response.headers["Content-Disposition"] = meta["attachment"]
            if meta.get("location"):
                response.headers["Location"] = meta["location"]
            response.headers.update({"Cache-Control": "no-store", "X-Report-Ready": "1"})
            return response
    elif not info or info["state"] != "running":
        from app import heavy, media

        folder = os.path.dirname(base)
        if machine and _token_running(folder, principal):
            return heavy.busy()
        try:
            # the reports kept for an hour count against the survey's and the account's room, like its files
            # (audit 2026-10-15, NB-5): a new one starts only while they fit
            media.check_room(survey, _results_bytes(folder))
        except media.StorageFull as e:
            return str(e), 507, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}
        if not heavy.hold(wait=0):
            return heavy.busy()
        info = {"job": secrets.token_hex(16), "state": "running", "started": time.time(), "principal": principal}
        # Claim atomically: simultaneous clicks must not start duplicate workers.
        if os.path.exists(marker):
            _remove(marker)
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            info = _read(marker)
            if info is None:
                return render_template("surveys/report_failed.html", survey=survey, retry=uri), 503
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(info, f)
            config = {}
            for name, value in current_app.config.items():
                try:
                    json.dumps(value)
                except (TypeError, ValueError):
                    continue
                config[name] = value
            payload = {"config": config, "base": base, "job": info["job"], "uri": uri,
                       "origin": request.host_url, "session": dict(session),
                       "headers": {name: request.headers[name] for name in ("User-Agent", "Accept-Language", "Authorization", "X-Requested-With")
                                   if name in request.headers}, "remote": request.remote_addr}
            try:
                child = _spawn(payload)
                if child is not None:
                    from app import jobs

                    _write(base + ".process.json", {"pid": child.pid, "identity": jobs.identity(child.pid),
                                                    "job": info["job"]})
                if os.name == "nt":
                    # Linux passes the locked descriptor to the child. Windows workers acquire their own
                    # slot once started; release this reservation before a machine client waits for them.
                    heavy.release()
            except OSError:
                _write(marker, {**info, "state": "failed"})
                return render_template("surveys/report_failed.html", survey=survey, retry=uri), 503
    url = uri + ("&" if pairs else "?") + "_report_job=" + info["job"]
    if machine or request.headers.get("X-Report-Poll") == "1":
        return "", 202, {"Retry-After": "1", "Cache-Control": "no-store", "X-Report-Job": info["job"]}
    return render_template("surveys/report_wait.html", survey=survey, poll=url,
                           download=request.endpoint != "surveys.results"), 202


def run(payload):
    from app import create_app, heavy
    from config import Config

    base, job = payload["base"], payload["job"]
    marker = base + ".run.json"

    try:
        app = create_app(type("ReportConfig", (Config,), payload["config"]))
    except Exception:
        _failed(base, job)
        return
    app.config["RESULTS_WAIT_PAGE"] = False

    def alive():
        info = _read(marker)
        return info is not None and info.get("job") == job

    try:
        if not alive():
            return
        with app.test_request_context(payload["uri"], base_url=payload["origin"],
                                      headers=payload["headers"],
                                      environ_base={"REMOTE_ADDR": payload["remote"]}):
            session.update(payload["session"])
            g._report_worker = True
            if payload.get("slot_fd") is not None:
                g._heavy_place = heavy._Place(None, os.fdopen(payload["slot_fd"], "a+b"))
            elif not heavy.hold():
                raise RuntimeError("Report capacity exhausted")
            response = app.full_dispatch_request()
            if not 200 <= response.status_code < 400:
                raise RuntimeError("Report request failed")
            tmp = base + ".body.tmp"
            fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(fd, "wb") as out:
                    for chunk in response.response:
                        if not alive():
                            return
                        out.write(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
                with _claim(base + ".claim"):
                    if not alive():
                        return
                    os.replace(tmp, base + ".body")
                    _write(base + ".meta.json", {"status": response.status_code, "type": response.mimetype,
                                                  "attachment": response.headers.get("Content-Disposition"),
                                                  "location": response.headers.get("Location")})
                    _write(marker, {"job": job, "state": "done", "started": time.time()})
            finally:
                response.close()
                _remove(tmp)
            from app import quality

            quality.save_kept()  # the next report of the survey starts from these (audit 2026-10-22, G-01)
    except Exception:
        app.logger.exception("Background report failed")
        _failed(base, job)


def limit_memory(megabytes):
    """The address space of a report process at most this many MB (REPORT_JOB_MEMORY_MB; Linux, 0 = no limit): an
    export of a very large survey then ends as a failed report ("try again") instead of pressing on the memory of
    the whole container (performance audit 2026-10-18, PERF-09: about 30 KB per participation). True once set."""
    try:
        megabytes = int(megabytes or 0)
    except (TypeError, ValueError):
        return False
    if megabytes <= 0 or os.name == "nt":
        return False
    import resource

    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    wanted = megabytes * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (wanted if hard == resource.RLIM_INFINITY else min(wanted, hard), hard))
    return True


if __name__ == "__main__":
    if os.name != "nt":
        os.nice(10)
    job = json.load(sys.stdin)
    limit_memory((job.get("config") or {}).get("REPORT_JOB_MEMORY_MB"))
    run(job)
