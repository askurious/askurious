"""Replay-safe live device state, isolated by survey/run and expiring after seven days.

The device id of a phone is issued by the server (issue): a random part and its HMAC with the server's key. It is
kept in the signed session cookie and, for a phone that loses its cookie, in localStorage (live.js); an id the
phone sends in the form is only taken when its signature fits. A script can no longer name a new device with every
request: each one needs a page of the server first (audit 2026-10-18, BIZ-02). Votes and Q&A upvotes stay once per
device and are limited per sender's network and question (allow_sender, counted by the database for all processes).
"""
import base64
import hashlib
import hmac
import secrets

from flask import abort, current_app, g, request, session

from app import browser_receipts
from app.extensions import db
from app.models import BrowserReceipt, utcnow


def marker(survey, st, lock=False):
    from app.live.audience import _device

    token = _device(survey)
    if not token:
        if request.method == "POST":
            abort(400)
        return None
    key = (survey.id, st["run"]["id"])
    cached = g.get("live_receipt")
    if cached and cached[0] == key and (cached[2] or not lock):
        return cached[1]
    scope = "live:" + str(st["run"]["id"])
    if lock:
        known = db.session.get(BrowserReceipt, (survey.id, scope, browser_receipts.digest(token)))
        if known is None:
            # a device id this run has not seen: a new row only within the limit of new devices per sender and run,
            # so a script that names a new id with every request cannot fill the table (audit 2026-10-15, NB-7)
            from app import limits

            if not limits.allow_live_run("device", survey, st["run"]["id"]):
                abort(429)
        row = browser_receipts.receipt(survey, scope, token, lock=True)
        if not row.data:
            # Import legacy cookie state once during the transition; all subsequent reads use the server.
            prefix = f"{survey.id}:{st['run']['id']}"
            row.data = {"votes": session.get("live-votes:" + prefix) or [],
                        "asked": session.get("live-asked:" + prefix) or 0,
                        "unclear": session.get("live-unclear:" + prefix) or {},
                        "sent": session.get("live-sent:" + prefix) or {},
                        "reaction": session.get(f"live-react:{survey.id}") or 0}
    else:
        row = db.session.get(BrowserReceipt, (survey.id, scope, browser_receipts.digest(token)))
        if row is None or row.expires_at <= utcnow():
            return None
    g.live_receipt = (key, row, lock)
    return row


def read(survey, st, name, default=None):
    row = marker(survey, st)
    return (row.data or {}).get(name, default) if row is not None else default


def write(survey, st, name, value):
    row = marker(survey, st, lock=True)
    row.data = {**(row.data or {}), name: value}


RANDOM_CHARS, SIGNATURE_CHARS = 24, 22  # 18 random bytes and 16 bytes of the HMAC, Base64 for addresses
SENDER_WINDOW = 3600  # seconds: a vote is over within this; rate_hit keeps its rows at least as long


def _mac(rand):
    secret = str(current_app.config.get("SECRET_KEY") or "").encode()
    digest = hmac.new(secret, f"live-device|{rand}".encode(), hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def issue():
    """A new device id: random, signed by the server (BIZ-02)."""
    rand = secrets.token_urlsafe(18)
    return rand + _mac(rand)


def signed(raw):
    """True if `raw` is a device id this server issued."""
    raw = str(raw or "")
    if len(raw) != RANDOM_CHARS + SIGNATURE_CHARS:
        return False
    return hmac.compare_digest(raw[RANDOM_CHARS:], _mac(raw[:RANDOM_CHARS]))


def allow_sender(survey, st, what):
    """One more device votes on `what` (a question, a page of a block, a question of the Q&A) from this sender's
    network in the current run: False once LIVE_SENDER_QUESTION_VOTES devices did (0 = no limit). Counted in rate_hit
    for all processes; the network is never stored, only a keyed digest of it (limits._shared_key). Counted within
    the request's own transaction (the device receipt of the request holds the write lock already), so the row goes
    with the vote: a vote refused later counts nothing."""
    import time

    from app import limits
    from app.models import RateHit

    limit = int(current_app.config.get("LIVE_SENDER_QUESTION_VOTES", 300) or 0)
    network = limits._network()
    if not limit or network is None:
        return True
    key = limits._shared_key("live_sender", survey.id, st["run"]["id"], what, network)
    if db.session.get_bind().dialect.name == "postgresql":  # one count at a time per key, until the vote is stored
        lock = int.from_bytes(hashlib.sha256(f"rate_hit:live_sender:{key}".encode()).digest()[:8], "big", signed=True)
        db.session.execute(db.text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    now = time.time()
    hits = db.session.scalar(db.select(db.func.count(RateHit.id)).where(
        RateHit.kind == "live_sender", RateHit.key == key, RateHit.at > now - SENDER_WINDOW))
    if hits >= limit:
        limits._note_refused("live_sender")
        return False
    db.session.add(RateHit(kind="live_sender", key=key, at=now))
    return True
