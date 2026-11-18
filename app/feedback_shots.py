"""The image on a piece of feedback (the bug icon in the header; concept "Bildschirmfotos an Rückmeldungen", Ralf
2026-11-18 "wie empfohlen umsetzen", E1-E9).

One image per feedback, voluntary: taken of the page, chosen or pasted, marked (and covered) in the browser before it
is sent (static/feedback_shot.js, with the code of the comment images, static/comment_shot.js). Here it is read and
written anew exactly as an image on a comment (app/comment_shots.py), but without any metadata, not even rights:
instance/feedback-shots/<random>.png|jpg, outside what Askurious serves without login. Only admins of the platform see
it (/feedback/<id>/shot behind twofactor.admin_page()); it never goes to Slack (app/notify.py says "mit Bild").

It goes 30 days after the feedback was settled (done_at; "wieder öffnen" stops the period), at once with the account
that sent it (app/accounts.py), or with "Bild entfernen" on /feedback/. `run` (hourly) does the period and sweeps
files no feedback names after an hour. The feedback's text has no period (E6).
"""
import os
from datetime import timedelta

from flask import current_app

from app import comment_shots
from app.lang import de_en

KEEP_DAYS = 30
STALE_SECONDS = comment_shots.STALE_SECONDS


def root():
    return os.path.join(os.path.dirname(current_app.config["UPLOAD_FOLDER"]), "feedback-shots")


def path(name):
    """The file of an image, or None (also for a name this module does not write)."""
    if not name or not comment_shots.NAME_RE.match(name):
        return None
    full = os.path.join(root(), name)
    return full if os.path.isfile(full) else None


def used_bytes():
    folder = root()
    if not os.path.isdir(folder):
        return 0
    return sum(e.stat().st_size for e in os.scandir(folder) if e.is_file())


def store(upload):
    """Read the uploaded image and write it anew; (file name, width, height). ValueError with a message for the
    researcher when it is no image, too large, the folder is full (FEEDBACK_SHOTS_MAX_MB) or the server is busy."""
    comment_shots.check_sender()
    data = comment_shots.read_upload(upload)
    if used_bytes() >= current_app.config["FEEDBACK_SHOTS_MAX_MB"] * 1024 * 1024:
        raise ValueError(de_en("Gerade sind sehr viele Bilder zu Rückmeldungen gespeichert.",
                               "Very many images on feedback are stored right now."))
    payload, ext, width, height = comment_shots.redraw(data, keep_rights=False)
    return comment_shots.write_file(root(), payload, ext), width, height


def remove(name):
    where = path(name)
    if where:
        try:
            os.remove(where)
        except OSError:
            pass


def drop(item):
    """The image of `item` gone: file and columns (the caller commits)."""
    if item.shot:
        remove(item.shot)
    item.shot = None
    item.shot_w = item.shot_h = None


def run(now=None):
    """Hourly: images of feedback settled KEEP_DAYS ago, and files no feedback names (older than an hour). Returns the
    number removed. Never raises."""
    import time

    from app.extensions import db
    from app.models import Feedback, utcnow

    gone = 0
    now = now or utcnow()
    try:
        old = db.session.scalars(db.select(Feedback).where(
            Feedback.shot.is_not(None), Feedback.status == "done", Feedback.done_at.is_not(None),
            Feedback.done_at <= now - timedelta(days=KEEP_DAYS))).all()
        for item in old:
            drop(item)
            gone += 1
        db.session.commit()
        base = root()
        if os.path.isdir(base):
            named = set(db.session.scalars(db.select(Feedback.shot).where(Feedback.shot.is_not(None))))
            cutoff = time.time() - STALE_SECONDS
            for entry in os.scandir(base):
                if entry.is_file() and entry.name not in named and entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
                    gone += 1
    except OSError as e:
        current_app.logger.error("Feedback images not swept: %s", e)
    return gone
