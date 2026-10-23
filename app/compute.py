"""Rechenknecht: the HB and latent class estimations on a second machine (Ralf 2026-10-06, rechenknecht-konzept.md,
variant V1; prepared, off until the server's .env names one).

The estimations are pure functions: run(inp, progress) takes plain JSON and gives plain JSON (maxdiff_hb, conjoint_hb,
segments). They keep one processor core busy for 5 to 14 minutes at 2000 participations (measured 2026-10-06), while
everything else of the app takes seconds. With COMPUTE_URL and COMPUTE_SECRET in the .env, start() of these
modules sends the model input to the Rechenknecht (app/compute_node.py, `python -m app.compute_node` on the other
machine) instead of starting a thread here:

- The input goes out pseudonymised: the participation numbers become 1, 2, 3 ... (pseudonym); the real ones stay in
  an index file here (uploads/hb/_remote/<job>.json) and come back into the result (restore). The other machine has
  no database, no SECRET_KEY and sees no participation number.
- Every message in both directions is gzip-compressed JSON signed with HMAC-SHA256 (COMPUTE_SECRET, a key of its
  own); older than MAX_AGE seconds or wrongly signed: refused. Signature 2 (X-Compute-Signature-2, audit 2026-10-18,
  CFG-07) covers the direction (request or response, each with a key of its own derived by HKDF), the method, time,
  a random nonce, the path and the body; a response also names the nonce of its request, so it answers only that
  one request, and a request whose nonce was seen within MAX_AGE is refused (replay). Signature 1 (time, path and
  body, X-Compute-Signature) is sent and accepted only with COMPUTE_SIGNATURE_V1=1, for an outside Rechenknecht of
  an older version; off by default since 2026-10-22 (audit E-02; BETRIEB.md).
- The Rechenknecht reports progress and the result to /rechenknecht/stand and /rechenknecht/ergebnis (bp). The
  result is written exactly as a thread here writes it (maxdiff_hb._estimate_and_store): page, export and
  convergence check see no difference.
- As a net, the status requests of the results page ask the Rechenknecht when nothing was heard for QUIET seconds
  (follow, from maxdiff_hb.running and segments.running) and fetch a finished result.
- Fallback: not reachable, a different code version (version: the estimation code's content) or the job lost there
  (restart): the estimation runs here as before, in a thread with the server's places (HB_PARALLEL). Without
  COMPUTE_URL nothing changes.
- A result is kept only while the marker still names its job (a cleanup removes marker and index, app/jobs.py).
"""
import gzip
import hashlib
import hmac
import importlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, jsonify, request

from app.extensions import csrf

bp = Blueprint("compute", __name__, url_prefix="/rechenknecht")

# kind -> module with run(inp, progress); the kind travels with the job, the module is imported on both machines
KINDS = {"maxdiff_hb": "app.maxdiff_hb", "conjoint_hb": "app.conjoint_hb", "paired_hb": "app.paired_hb", "cva_hb": "app.cva_hb", "segments": "app.segments",
         "sem": "app.sem_job",  # the bootstrap of a structural equation model (app/sem_model.py)
         "omega": "app.omega_job",
         "sem_power": "app.sem_power_job",  # the power of a planned SEM by simulation (SEM extensions 2026-10-17)
         "panel": "app.panel_job",
         "sem_fit": "app.sem_fit_job"}  # the estimate of the results page's model (thread "Rechenknecht")  # the models over the waves of a panel (Ralf 2026-10-17, thread "Rechenknecht")
# the files the estimations compute with: their content is the version both machines must share (the app's
# results_store.program() uses file times, which differ between two checkouts)
CODE = ("hb.py", "hb_convergence.py", "maxdiff_hb.py", "maxdiff_analysis.py", "conjoint_hb.py", "choice_model.py",
        "segments.py", "latent_class.py", "lc_regression.py", "units.py", "compute.py", "compute_node.py", "sem.py", "sem_job.py",
        "omega_job.py", "intervals.py", "scales.py", "paired_hb.py", "paired_analysis.py", "cva_hb.py", "sem_means.py",
        "sem_ordinal.py", "sem_power_job.py", "sem_table.py", "panel_tables.py", "panel_power.py", "panel_models.py",
        "panel_job.py", "sem_model.py", "sem_fit_job.py")
MAX_AGE = 300        # seconds a signed message stays valid (clocks of both machines may differ a little)
SEND_TIMEOUT = 15    # seconds for sending a job
QUIET = 60           # seconds without a sign from the Rechenknecht before the status request asks it
ASK_EVERY = 30       # seconds between two such questions
GIVE_UP = 240        # seconds without any sign: computed here (below maxdiff_hb.STALE_HEARTBEAT)
JOB = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

_version = None


def version():
    """A hash of the content of the estimation code, the same on every machine with the same code, and of the numpy
    and Python versions: the random streams of default_rng and the linear algebra may differ between them, so the same
    input with the same seed could give another result unnoticed (audit 2026-10-06, W-5)."""
    global _version
    if _version is None:
        import sys

        import numpy

        root = os.path.dirname(os.path.abspath(__file__))
        h = hashlib.sha256()
        h.update(f"numpy {numpy.__version__}\0python {sys.version_info[0]}.{sys.version_info[1]}\0".encode())
        for name in CODE:
            with open(os.path.join(root, name), "rb") as f:
                h.update(name.encode() + b"\0" + f.read() + b"\0")
        _version = h.hexdigest()[:16]
    return _version


def url_ok(url):
    """May the pseudonymised data go to this address: https, or plain http only inside the own machine or network
    (localhost, a Docker service name without a dot, a private or loopback IP address). The data are signed but not
    encrypted (audit 2026-10-13, IN-6)."""
    import ipaddress
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url or "")
        host = parts.hostname or ""
    except ValueError:
        return False
    if parts.scheme == "https":
        return bool(host)
    if parts.scheme != "http" or not host:
        return False
    if host == "localhost" or host.endswith(".localhost") or "." not in host and ":" not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def enabled(app=None):
    """COMPUTE_URL and COMPUTE_SECRET set and the address allowed (url_ok); else this server computes itself."""
    config = (app or current_app).config
    return bool(config.get("COMPUTE_URL") and config.get("COMPUTE_SECRET") and url_ok(config.get("COMPUTE_URL")))


def runner(kind):
    return importlib.import_module(KINDS[kind]).run


# --- messages ------------------------------------------------------------------------------------------------

def pack(obj):
    return gzip.compress(json.dumps(obj, separators=(",", ":")).encode(), compresslevel=5)


def unpack(body, limit=512 * 2**20):
    """The JSON of a packed body; ValueError if it is none (or unpacks to more than limit bytes)."""
    try:
        with gzip.GzipFile(fileobj=__import__("io").BytesIO(body)) as f:
            raw = f.read(limit + 1)
    except (OSError, EOFError) as e:
        raise ValueError("not gzip") from e
    if len(raw) > limit:
        raise ValueError("too large")
    return json.loads(raw)


def sign(secret, ts, path, body):
    """Signature 1: path is the route below the service's own address (/auftrag, /stand, /ergebnis), so a folder in
    front of either service (a proxy, an app below a path) does not matter."""
    msg = f"{ts}\n{path}\n".encode() + body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


REQUEST, RESPONSE = "request", "response"
NONCE = re.compile(r"[0-9a-f]{32}")
_seen, _seen_lock = {}, threading.Lock()  # nonces of requests within MAX_AGE (per process): a replay is refused


def sign2(secret, direction, method, ts, nonce, path, body, reply_to=""):
    """Signature 2 (CFG-07): direction (REQUEST or RESPONSE, each with a key of its own), method, time, nonce, the
    nonce of the request a response answers, path, body."""
    from app import subkeys

    head = "\n".join(("askurious-compute-2", direction, method.upper(), ts, nonce, reply_to or "", path))
    key = subkeys.derive(secret, f"compute-{direction}")
    return hmac.new(key, head.encode() + b"\n" + body, hashlib.sha256).hexdigest()


def v1_allowed():
    """Is signature 1 still sent and taken (COMPUTE_SIGNATURE_V1, default off since 2026-10-22, audit E-02)? The app
    reads its config, the Rechenknecht its environment."""
    try:
        value = current_app.config.get("COMPUTE_SIGNATURE_V1")
    except RuntimeError:  # outside an app
        value = None
    if value is None:
        value = os.environ.get("COMPUTE_SIGNATURE_V1", "0").strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def signed_headers(secret, path, body, method="POST", direction=REQUEST, reply_to=""):
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    headers = {"X-Compute-Time": ts, "X-Compute-Nonce": nonce, "Content-Type": "application/gzip",
               "X-Compute-Signature-2": sign2(secret, direction, method, ts, nonce, path, body, reply_to)}
    if reply_to:
        headers["X-Compute-Reply-To"] = reply_to
    if v1_allowed():  # for a Rechenknecht (or server) of an older version
        headers["X-Compute-Signature"] = sign(secret, ts, path, body)
    return headers


def _fresh(nonce, ts):
    """True the first time this nonce of a request comes within MAX_AGE; a second time it is a replay."""
    now = time.time()
    with _seen_lock:
        for old in [n for n, until in _seen.items() if until < now]:
            del _seen[old]
        if nonce in _seen:
            return False
        _seen[nonce] = int(ts) + MAX_AGE + 1
    return True


def verify(secret, headers, path, body, method="POST", direction=REQUEST, reply_to=None):
    """True if the message is signed with secret and not older (or newer) than MAX_AGE seconds. Signature 2 when the
    message carries it (a request: its nonce not seen before; a response: to the request with nonce reply_to),
    else signature 1 while v1_allowed()."""
    ts = headers.get("X-Compute-Time", "")
    # ASCII digits only and a comparison of bytes: "²" or "ä" in a header gave a 500 (audit 2026-10-09, K2 = B6)
    if not secret or not (ts.isascii() and ts.isdigit() and len(ts) <= 16):
        return False
    if abs(time.time() - int(ts)) > MAX_AGE:
        return False
    given2 = headers.get("X-Compute-Signature-2", "")
    if given2:
        nonce = headers.get("X-Compute-Nonce", "")
        if not NONCE.fullmatch(nonce) or (direction == RESPONSE and not reply_to):
            return False
        expected = sign2(secret, direction, method, ts, nonce, path, body, reply_to or "")
        if not hmac.compare_digest(expected.encode(), given2.encode("utf-8", "replace")):
            return False
        return direction == RESPONSE or _fresh(nonce, ts)
    given = headers.get("X-Compute-Signature", "")
    if not given or not v1_allowed():
        return False
    return hmac.compare_digest(sign(secret, ts, path, body).encode(), given.encode("utf-8", "replace"))


def verify_response(secret, response, path, method="GET"):
    """verify() for the answer to send() or ask(): signed for the request it answers (CFG-07)."""
    return verify(secret, response.headers, path, response.content, method=method, direction=RESPONSE,
                  reply_to=getattr(response, "compute_nonce", None))


def send(base, path, obj, secret, timeout=SEND_TIMEOUT):
    """POST obj, packed and signed, to base + path; the response (requests, with the request's nonce as
    compute_nonce), exceptions pass through."""
    import requests

    body = pack(obj)
    headers = signed_headers(secret, path, body, "POST")
    r = requests.post(base.rstrip("/") + path, data=body, headers=headers, timeout=timeout)
    r.compute_nonce = headers["X-Compute-Nonce"]
    return r


def ask(base, path, secret, timeout=10):
    import requests

    headers = signed_headers(secret, path, b"", "GET")
    r = requests.get(base.rstrip("/") + path, headers=headers, timeout=timeout)
    r.compute_nonce = headers["X-Compute-Nonce"]
    return r


# --- pseudonyms ----------------------------------------------------------------------------------------------

def pseudonym(inp):
    """(the input to send, the real participation numbers): persons numbered 1..n, without the path of the partial
    file of a latent class run (segments: a file of this server)."""
    out = {k: v for k, v in inp.items() if k not in ("persons", "partial")}
    if "persons" not in inp:  # the bootstraps (SEM, omega) carry numbers only, no participation numbers
        return out, []
    real = [p["pid"] for p in inp["persons"]]
    out["persons"] = [{**p, "pid": n + 1} for n, p in enumerate(inp["persons"])]
    return out, real


def restore(result, real):
    if "pids" in result:
        result["pids"] = [real[int(p) - 1] for p in result["pids"]]
    return result


# --- this server: send, take back, ask ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _folder(app):
    return os.path.join(app.config["UPLOAD_FOLDER"], "hb", "_remote")


def _index(app, job):
    return os.path.join(_folder(app), f"{job}.json")


def submit(app, kind, paths, inp, pool):
    """Send the estimation to the Rechenknecht: True if it took it (the marker then names the job), False if it is
    not set up, not reachable or has another code version: then the caller computes here as before."""
    from app import maxdiff_hb

    if not enabled(app):
        return False
    job = secrets.token_urlsafe(18)
    sent, real = pseudonym(inp)
    index = _index(app, job)
    maxdiff_hb._write(index, {"kind": kind, "paths": list(paths), "pool": pool, "real": real, "inp": inp,
                              "created": _now().isoformat()})
    try:
        r = send(app.config["COMPUTE_URL"], "/auftrag", {"job": job, "kind": kind, "version": version(), "inp": sent},
                 app.config["COMPUTE_SECRET"])
        ok = r.status_code == 202
        if not ok:
            app.logger.warning("Rechenknecht refused the job (%s): %s", r.status_code, r.text[:200])
    except Exception as e:  # requests' errors and anything else: compute here
        app.logger.warning("Rechenknecht not reachable: %s", e)
        ok = False
    if not ok:
        _remove(index)
        return False
    now = _now().isoformat()
    maxdiff_hb._write(paths[1], {"state": "running", "started": now, "beat": now, "progress": 0.0, "key": inp["key"],
                                 "pool": pool, "remote": job})
    return True


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _take(app, job):
    """The index of the job, taken by this caller alone (a callback and a status request may come at once), or None."""
    from app import maxdiff_hb

    index = _index(app, job)
    taken = f"{index}.{os.getpid()}.{threading.get_ident()}.taken"
    try:
        os.rename(index, taken)
    except OSError:
        return None
    data = maxdiff_hb._read(taken)
    _remove(taken)
    return data


def deliver(app, job, result=None, error=None):
    """Store the result (or the error) of a job as a thread here would; False for a job this server does not know
    (or one delivered already) and for a result that does not belong to its input."""
    from app import maxdiff_hb

    index = maxdiff_hb._read(_index(app, job))
    if not index:
        return False
    if result is not None and (not isinstance(result, dict) or result.get("key") != index["inp"]["key"]):
        return False
    index = _take(app, job)
    if not index:
        return False
    result_path, marker_path = index["paths"]
    marker = maxdiff_hb._read(marker_path) or {}
    if marker.get("remote") != job:  # stopped by a cleanup (erasure, retention, survey deleted) or another run took
        return True                  # over: nothing is written (code review 2026-10-07, C3)
    try:
        if result is not None:
            result = restore(result, index["real"])
            result.update(pool=index["pool"], created=_now().isoformat())
            maxdiff_hb._write(result_path, result, make=False)  # not into a folder removed meanwhile
            _remove(marker_path)
        else:
            app.logger.error("HB estimation on the Rechenknecht failed: %s", error)
            maxdiff_hb._write(marker_path, {**marker, "state": "error", "message": str(error)[:500]}, make=False)
    except FileNotFoundError:
        pass
    return True


def _local(app, index):
    """Compute a taken job here, as start() does without a Rechenknecht."""
    from app import maxdiff_hb

    threading.Thread(target=maxdiff_hb._estimate_and_store,
                     args=(app, tuple(index["paths"]), index["inp"], index["pool"], runner(index["kind"])),
                     daemon=True, name="hb-local").start()


def follow(paths, marker):
    """The marker of a job on the Rechenknecht, brought up to date when nothing was heard for QUIET seconds: a
    finished result fetched (then None), a lost or unreachable job computed here. Called from running()."""
    from app import maxdiff_hb

    app = current_app._get_current_object()
    job = marker.get("remote")
    if marker.get("state") != "running" or not job or not JOB.match(job):
        return marker
    now = _now()
    quiet = (now - datetime.fromisoformat(marker.get("beat") or marker["started"])).total_seconds()
    asked = marker.get("asked")
    if quiet < QUIET or (asked and (now - datetime.fromisoformat(asked)).total_seconds() < ASK_EVERY):
        return marker
    state = None
    if enabled(app):
        try:
            r = ask(app.config["COMPUTE_URL"], f"/auftrag/{job}", app.config["COMPUTE_SECRET"])
            if r.status_code == 404:
                state = {"state": "lost"}
            elif r.status_code == 200 and verify_response(app.config["COMPUTE_SECRET"], r, f"/auftrag/{job}"):
                state = unpack(r.content)
        except Exception as e:
            app.logger.warning("Rechenknecht not reachable: %s", e)
    if state and state.get("state") == "done":
        deliver(app, job, result=state.get("result"))
        return maxdiff_hb._read(paths[1])
    if state and state.get("state") == "error":
        deliver(app, job, error=state.get("message"))
        return maxdiff_hb._read(paths[1])
    if state and state.get("state") in ("queued", "running"):
        marker.update(beat=now.isoformat(), asked=now.isoformat())
        maxdiff_hb._write(paths[1], marker)
        return marker
    if (state and state.get("state") == "lost") or quiet >= GIVE_UP:
        index = _take(app, job)
        if index:
            app.logger.warning("Rechenknecht lost job %s or went silent: computed here", job)
            marker.pop("remote", None)
            marker.update(beat=now.isoformat(), progress=0.0)
            maxdiff_hb._write(paths[1], marker)
            _local(app, index)
        return maxdiff_hb._read(paths[1])
    marker["asked"] = now.isoformat()
    maxdiff_hb._write(paths[1], marker)
    return marker


def healthy(app=None):
    """Bounded, signed readiness probe without exposing jobs, addresses or errors to the status page."""
    app = app or current_app
    if not enabled(app):
        return False
    try:
        secret = app.config["COMPUTE_SECRET"]
        r = ask(app.config["COMPUTE_URL"], "/stand", secret, timeout=2)
        if r.status_code != 200 or not verify_response(secret, r, "/stand"):
            return False
        data = unpack(r.content, limit=64 * 1024)
        return isinstance(data, dict) and data.get("version") == version()
    except Exception:
        return False


def status(app=None):
    """For Administration > Datenbank: {"configured": False} or what the Rechenknecht says about itself."""
    app = app or current_app
    if not enabled(app):
        return {"configured": False}
    out = {"configured": True, "url": app.config["COMPUTE_URL"], "version": version()}
    try:
        r = ask(app.config["COMPUTE_URL"], "/stand", app.config["COMPUTE_SECRET"], timeout=5)
        if r.status_code != 200 or not verify_response(app.config["COMPUTE_SECRET"], r, "/stand"):
            out["error"] = f"HTTP {r.status_code}"
        else:
            out.update(reachable=True, node=unpack(r.content))
    except Exception as e:
        out["error"] = type(e).__name__
    folder = _folder(app)
    out["waiting"] = len([n for n in os.listdir(folder) if n.endswith(".json")]) if os.path.isdir(folder) else 0
    return out


# --- what the Rechenknecht calls -------------------------------------------------------------------------------

_SIGNATURE = re.compile(r"[0-9a-f]{64}")


def _signed_headers_fit(headers):
    """What can be checked before the body is read: a Rechenknecht is set up, the time of the signature is fresh and
    the signature has the form of one. Wherever /rechenknecht/ is reachable from outside (an outside Rechenknecht
    calls it over the internet; on the Docker server nginx answers 404 since the Abgleich 2026-10-15, as its own
    Rechenknecht calls researcher:8000 directly), the body may be 64 MB: without this the app read all of it before it
    found the request unsigned (audit 2026-10-13, DP-12). The signature over the body itself follows in verify."""
    ts = headers.get("X-Compute-Time", "")
    given = headers.get("X-Compute-Signature-2") or headers.get("X-Compute-Signature", "")
    if not enabled() or not (len(ts) <= 16 and ts.isascii() and ts.isdigit()) or not _SIGNATURE.fullmatch(given):
        return False
    return abs(time.time() - int(ts)) <= MAX_AGE


def _message():
    """The JSON of a signed message from the Rechenknecht, else 403/400."""
    secret = current_app.config.get("COMPUTE_SECRET")
    if not _signed_headers_fit(request.headers):
        abort(403)  # the body stays unread
    body = request.get_data(cache=False)
    if not enabled() or not verify(secret, request.headers, request.path[len(bp.url_prefix):], body,
                                   method=request.method):
        abort(403)
    try:
        data = unpack(body)
    except ValueError:
        abort(400)
    if not isinstance(data, dict) or not JOB.match(str(data.get("job") or "")):
        abort(400)
    return data


@bp.route("/stand", methods=["POST"])
@csrf.exempt
def progress():
    from app import maxdiff_hb

    data = _message()
    index = maxdiff_hb._read(_index(current_app, data["job"]))
    if not index:
        return jsonify(ok=False), 404
    marker = maxdiff_hb._read(index["paths"][1]) or {}
    if marker.get("remote") == data["job"] and marker.get("state") == "running":
        try:
            fraction = min(1.0, max(0.0, float(data.get("progress") or 0)))
        except (TypeError, ValueError):
            fraction = 0.0
        marker.update(progress=round(fraction, 3), beat=_now().isoformat())
        maxdiff_hb._write(index["paths"][1], marker)
    return jsonify(ok=True)


@bp.route("/ergebnis", methods=["POST"])
@csrf.exempt
def result():
    data = _message()
    ok = deliver(current_app._get_current_object(), data["job"], result=data.get("result"),
                 error=None if "result" in data else (data.get("error") or "?"))
    return jsonify(ok=ok), 200 if ok else 404
