"""Progress of a long request, for the page that waits for it.

The results page is computed in one request. While it runs, it notes how far it is in a small file;
the waiting page asks for that note now and then (surveys.results_progress) and moves its bar. A file,
because the question may be answered by another process than the one that computes (as the state of
the HB estimation, app/maxdiff_hb.py). The note holds a share and a short text, never data.

The token comes from the waiting page (a cookie); the file name also carries the user, so nobody
reads the progress of someone else.
"""
import json
import os
import re
import time

TOKEN = re.compile(r"^[A-Za-z0-9]{8,40}\Z")
EVERY = 0.2        # seconds between two notes: writing must not slow the work down
MAX_AGE = 600      # seconds after which a note left behind (aborted request) is removed


def _path(token, user_id):
    from flask import current_app

    if not token or not TOKEN.match(token):
        return None
    return os.path.join(current_app.config["UPLOAD_FOLDER"], "progress", f"{user_id}_{token}.json")


def reporter(token, user_id):
    """A function report(share, text) for the work (share 0..1), or None without a valid token."""
    path = _path(token, user_id)
    if path is None:
        return None
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, exist_ok=True)
        names = os.listdir(folder)
    except OSError:
        return None
    for name in names:  # notes of requests that never finished
        old = os.path.join(folder, name)
        try:
            if time.time() - os.path.getmtime(old) > MAX_AGE:
                os.remove(old)
        except OSError:
            pass  # another request removed it in the same moment: no reason for this one to go without a bar
    last = [0.0]

    def report(share, text):
        now = time.monotonic()
        if now - last[0] < EVERY:
            return
        last[0] = now
        tmp = f"{path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"share": round(max(0.0, min(share, 1.0)), 3), "text": text}, f)
            os.replace(tmp, path)  # readers never see half a file
        except OSError:
            pass  # the bar then just waits; the work goes on

    return report


def read(token, user_id):
    """The latest note ({"share", "text"}), or {} if there is none (not started yet, or finished)."""
    path = _path(token, user_id)
    if path is None:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def clear(token, user_id):
    path = _path(token, user_id)
    if path is None:
        return
    try:
        os.remove(path)
    except OSError:
        pass
