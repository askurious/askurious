"""At most a few heavy evaluations at once across all processes of a container (PF-2, audit 2026-10-13).

The exports of the data (CSV, SPSS, R, jamovi), the method section and results computed anew read every
participation of the pool and build the file in memory: 100 to 450 MB each with 5000 participations. A researcher
process runs 8 threads; three such downloads at the same moment would hold well over a gigabyte. So a view marked
with @limited waits for a free place (HEAVY_PARALLEL places per container, 2 by default) for up to HEAVY_WAIT
seconds and only then answers 503 "please try again in a moment". HEAVY_PARALLEL = 0 switches the limit off.
A request takes at most one place and gives it back when it ends (teardown, also after an error).

Fair per account (audit 2026-10-18, BIZ-08): one account holds at most one of the places at a time. Its second heavy
request waits behind its own first one (an OS file lock per account, shared by all processes of the container) and
never takes a second place, so the other places stay free for other researchers while it waits.
"""
import functools
import hashlib
import os
import socket
import threading
import time

from flask import Response, current_app, g, request

from app.lang import de_en

_places = {}  # {number of places: semaphore}, per process
_lock = threading.Lock()


class _Place:
    def __init__(self, semaphore, file, account=None):
        self.semaphore, self.file, self.account = semaphore, file, account

    def release(self):
        self.file.close()  # the OS releases the lock, also if the process dies
        if self.account is not None:
            self.account.close()
        if self.semaphore is not None:
            self.semaphore.release()


def _account():
    """The signed-in account of this request, None for anonymous ones (public dashboards, plain test apps)."""
    try:
        from flask_login import current_user

        if current_user and current_user.is_authenticated:
            return current_user.get_id()
    except (AttributeError, RuntimeError):  # no login manager on this app
        return None
    return None


def _shared_slot(n, wait, namespace=None):
    """OS file locks limit all web/child processes of this container together (PF-2)."""
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "hb", "_heavy")
    os.makedirs(folder, exist_ok=True)
    deadline = time.monotonic() + wait
    while True:
        for i in range(n):
            path = os.path.join(folder, f"{namespace or socket.gethostname()}-{i}.lock")
            file = open(path, "a+b")
            try:
                if os.name == "nt":
                    import msvcrt

                    if os.path.getsize(path) == 0:
                        file.write(b"0")
                        file.flush()
                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return file
            except (OSError, BlockingIOError):
                file.close()
        if time.monotonic() >= deadline:
            return None
        time.sleep(min(.05, max(0, deadline - time.monotonic())))


def _semaphore(n):
    with _lock:
        if n not in _places:
            _places[n] = threading.BoundedSemaphore(n)
        return _places[n]


def busy():
    """The answer when no place became free in time."""
    text = de_en("Gerade laufen schon mehrere große Auswertungen auf dem Server. Bitte versuchen Sie es in einer "
                 "Minute noch einmal.",
                 "Several large evaluations are running on the server right now. Please try again in a minute.")
    return Response(text, status=503, mimetype="text/plain; charset=utf-8", headers={"Retry-After": "30"})


def hold(wait=None, account=None):
    """Take a place for a heavy evaluation for the rest of the request (released when the request ends, also on an
    error: register). True once one is free (or none is needed), False if none became free within HEAVY_WAIT
    seconds; then the caller answers busy(). account: whose request it is where no one is signed in (an access
    token's owner); by default the signed-in person. One account holds at most one place at a time (BIZ-08)."""
    n = int(current_app.config.get("HEAVY_PARALLEL", 2) or 0)
    if n <= 0 or g.get("_heavy_place") is not None:
        return True
    wait = max(0.0, float(current_app.config.get("HEAVY_WAIT", 30) if wait is None else wait))
    started = time.monotonic()
    account = _account() if account is None else account
    mine = None
    if account is not None and n > 1:  # taken before a place, so waiting for it blocks no other account
        name = hashlib.sha256(f"heavy-account-{account}".encode()).hexdigest()[:24]
        try:
            mine = _shared_slot(1, wait, namespace=f"{socket.gethostname()}-account-{name}")
        except OSError:
            current_app.logger.exception("Shared heavy-evaluation slots unavailable")
            return False
        if mine is None:
            current_app.logger.warning("Heavy evaluation refused, the account's own one still runs: %s",
                                       request.endpoint)
            return False
    semaphore = _semaphore(n)
    if not semaphore.acquire(timeout=max(0, wait - (time.monotonic() - started))):
        if mine is not None:
            mine.close()
        current_app.logger.warning("Heavy evaluation refused after waiting: %s", request.endpoint)
        return False
    try:
        file = _shared_slot(n, max(0, wait - (time.monotonic() - started)))
    except OSError:
        file = None
        current_app.logger.exception("Shared heavy-evaluation slots unavailable")
    if file is None:
        semaphore.release()
        if mine is not None:
            mine.close()
        return False
    g._heavy_place = _Place(semaphore, file, mine)
    return True


def release(exc=None):
    semaphore = g.pop("_heavy_place", None)
    if semaphore is not None:
        semaphore.release()


def register(app):
    app.teardown_request(release)


def limited(view):
    """Run the view in one of the places for heavy evaluations (see the module text)."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        return view(*args, **kwargs) if hold() else busy()

    return wrapper
