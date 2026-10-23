"""The Rechenknecht itself: a small web service on a second machine that runs the HB and latent class
estimations for the Askurious server (app/compute.py; rechenknecht-konzept.md; set-up in tools/rechenknecht/README.md).

    python -m app.compute_node      (behind a web server with HTTPS, e.g. nginx or Caddy; see the README)
    gunicorn --workers 1 --worker-class gthread --threads 8 --bind 127.0.0.1:8090 "app.compute_node:create()"
                                    (the same with a production server, as in deploy/docker/compose.yaml; one
                                    worker only, since the jobs live in its memory; audit 2026-10-18, CFG-07)

Settings from the environment:
    COMPUTE_SECRET     the shared key, the same as in the Askurious server's .env (required)
    COMPUTE_CALLBACK   the Askurious server's address, e.g. https://queryous.example.org/rechenknecht (required)
    COMPUTE_PARALLEL   estimations at the same time (default: processor cores minus one, at least 1)
    COMPUTE_BIND       address and port to listen on (default 127.0.0.1:8090; main() only, gunicorn takes --bind)
    COMPUTE_MAX_QUEUE  jobs waiting or computing at most (default 24); beyond it "busy" (503), and the server computes
                       the job itself (audit 2026-10-22, D-04: the queue took every signed job, all of them in memory)
    COMPUTE_SIGNATURE_V1  1 only for an Askurious server of an older version that signs with signature 1 alone
                          (app/compute.py); default 0 since 2026-10-22

It has no database and no files of the server. A job lives in memory only: the input while it is computed, the
result until it was delivered and at most KEEP seconds; a restart forgets everything (the server then computes
the job itself, compute.follow). Every request must be signed with COMPUTE_SECRET; a job for another version of
the estimation code is refused (409), and the server computes it itself.

Routes: POST /auftrag (a job: job, kind, version, inp), GET /auftrag/<job> (its state, with the result once done),
GET /stand (version, places, jobs)."""
import logging
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor

from flask import Flask, Response, abort, request

from app import compute

KEEP = 3600          # seconds a finished job stays to be asked for
PROGRESS_EVERY = 10  # seconds between two progress reports to the server
DELIVER_TRIES = 5
DELIVER_PAUSE = 20   # seconds between two tries to deliver a result

log = logging.getLogger("rechenknecht")


def _low():
    """Each worker process at the lowest priority: the machine's own services come first."""
    from app import estimate

    estimate.lower_priority()


def _work(kind, inp, job, callback, secret):
    """In a worker process: the estimation, with progress reports; {"result": ...} or {"error": ...}."""
    last = [0.0]

    def progress(fraction):
        now = time.monotonic()
        if callback and now - last[0] >= PROGRESS_EVERY:
            last[0] = now
            try:
                compute.send(callback, "/stand", {"job": job, "progress": round(float(fraction), 3)}, secret, timeout=5)
            except Exception:  # a lost report is no problem: the next one comes, and the server asks itself
                pass

    try:
        return {"result": compute.runner(kind)(inp, progress)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def create(secret=None, callback=None, parallel=None, inline=False):
    """The service. inline: compute in the request (tests), else in a pool of processes."""
    secret = secret or os.environ.get("COMPUTE_SECRET", "")
    callback = (callback if callback is not None else os.environ.get("COMPUTE_CALLBACK", "")).rstrip("/")
    parallel = parallel or int(os.environ.get("COMPUTE_PARALLEL") or max(1, (os.cpu_count() or 2) - 1))
    if not secret:
        raise SystemExit("COMPUTE_SECRET is missing")
    if not inline and not logging.getLogger().handlers:  # under gunicorn (compose.yaml) as under main()
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = Flask("askurious-rechenknecht")
    app.config["MAX_CONTENT_LENGTH"] = 128 * 2**20
    jobs, lock = {}, threading.Lock()
    pool = None if inline else ProcessPoolExecutor(max_workers=parallel, initializer=_low)

    def signed(obj, status=200):
        """A response signed for the request it answers (its method and nonce; audit 2026-10-18, CFG-07)."""
        body = compute.pack(obj)
        nonce = request.headers.get("X-Compute-Nonce", "")
        return Response(body, status=status, headers=compute.signed_headers(
            secret, request.path, body, request.method, compute.RESPONSE,
            reply_to=nonce if compute.NONCE.fullmatch(nonce) else ""))

    def check(body=b""):
        if not compute.verify(secret, request.headers, request.path, body, method=request.method):
            abort(403)

    def tidy():
        with lock:
            for job in [j for j, x in jobs.items() if x.get("finished") and time.time() - x["finished"] > KEEP]:
                del jobs[job]

    def deliver(job):
        x = jobs[job]
        payload = {"job": job, **({"result": x["result"]} if "result" in x else {"error": x.get("error")})}
        for n in range(DELIVER_TRIES):
            try:
                r = compute.send(callback, "/ergebnis", payload, secret, timeout=60)
                if r.status_code in (200, 404):  # 404: the server does not know it (any more); asking is over
                    break
            except Exception as e:
                log.warning("Delivering %s failed: %s", job, e)
            time.sleep(DELIVER_PAUSE)
        x.pop("inp", None)

    def finish(job, out):
        with lock:
            x = jobs.get(job)
            if x is None:
                return
            x.update(out, state="done" if "result" in out else "error", finished=time.time())
            x.pop("future", None)
        log.info("Job %s (%s) %s after %.0f s", job, x["kind"], x["state"], time.time() - x["created"])
        if callback:
            threading.Thread(target=deliver, args=(job,), daemon=True).start()

    @app.post("/auftrag")
    def take():
        body = request.get_data(cache=False)
        check(body)
        try:
            data = compute.unpack(body)
        except ValueError:
            abort(400)
        job, kind = str(data.get("job") or ""), data.get("kind")
        if not compute.JOB.match(job) or kind not in compute.KINDS or not isinstance(data.get("inp"), dict):
            abort(400)
        if data.get("version") != compute.version():
            return signed({"error": "version", "version": compute.version()}, 409)
        tidy()
        with lock:
            if job in jobs:
                return signed({"ok": True}, 202)
            waiting = sum(1 for x in jobs.values() if x.get("state") == "queued")
            if waiting >= int(os.environ.get("COMPUTE_MAX_QUEUE") or 24):
                return signed({"error": "busy"}, 503)
            jobs[job] = {"kind": kind, "state": "queued", "created": time.time()}
        log.info("Job %s (%s) taken", job, kind)
        if inline:
            finish(job, _work(kind, data["inp"], job, callback, secret))
        else:
            future = pool.submit(_work, kind, data["inp"], job, callback, secret)
            jobs[job]["future"] = future
            future.add_done_callback(lambda f, job=job: finish(
                job, f.result() if not f.exception() else {"error": f"{type(f.exception()).__name__}: {f.exception()}"}))
        return signed({"ok": True}, 202)

    @app.get("/auftrag/<job>")
    def state(job):
        check()
        tidy()
        x = jobs.get(job)
        if x is None:
            return signed({"state": "lost"}, 404)
        if x["state"] == "queued" and x.get("future") is not None and x["future"].running():
            return signed({"state": "running"})
        out = {"state": x["state"]}
        if x["state"] == "done":
            out["result"] = x["result"]
        elif x["state"] == "error":
            out["message"] = x.get("error")
        return signed(out)

    @app.get("/stand")
    def stand():
        check()
        tidy()
        with lock:
            busy = [x for x in jobs.values() if x["state"] == "queued"]
            running = sum(1 for x in busy if x.get("future") is not None and x["future"].running())
        return signed({"version": compute.version(), "parallel": parallel, "running": running,
                       "queued": len(busy) - running, "finished": len(jobs) - len(busy)})

    return app


def main():
    from werkzeug.serving import run_simple

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not os.environ.get("COMPUTE_CALLBACK"):
        raise SystemExit("COMPUTE_CALLBACK is missing (the Askurious server's address, ending in /rechenknecht)")
    host, _, port = (os.environ.get("COMPUTE_BIND") or "127.0.0.1:8090").rpartition(":")
    log.info("Rechenknecht for estimation code %s on %s:%s", compute.version(), host, port)
    run_simple(host or "127.0.0.1", int(port), create(), threaded=True)


if __name__ == "__main__":
    main()
