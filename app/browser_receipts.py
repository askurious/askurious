"""Bounded, short-lived state outside replayable Flask cookies (SP13 TN-3/TN-9/AZ-12)."""
import hashlib
import secrets
from datetime import timedelta, timezone

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import BrowserReceipt, utcnow


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def receipt(survey, scope, token, *, lock=False, hours=168):
    key = (survey.id, scope, digest(token))
    row = db.session.get(BrowserReceipt, key)
    if row is None:
        try:
            with db.session.begin_nested():
                row = BrowserReceipt(survey_id=survey.id, scope=scope, key=key[2], data={},
                                     expires_at=(utcnow() + timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0))
                db.session.add(row)
                db.session.flush()
        except IntegrityError:
            row = db.session.get(BrowserReceipt, key)
    if lock:
        # UPDATE takes a transaction lock before reading the JSON: concurrent requests cannot lose a toggle/count.
        db.session.execute(db.update(BrowserReceipt).where(
            BrowserReceipt.survey_id == key[0], BrowserReceipt.scope == key[1], BrowserReceipt.key == key[2]
        ).values(version=BrowserReceipt.version + 1).execution_options(synchronize_session=False))
        db.session.refresh(row)
    if row.expires_at <= utcnow():
        row.data = {}
        row.expires_at = (utcnow() + timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
    return row


def decline(survey):
    token = secrets.token_urlsafe(24)
    row = receipt(survey, "decline", token)
    row.expires_at = (utcnow() + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    return token


def undo_decline(survey, token):
    # Legacy booleans cannot prove which decline was undone. They never alter the aggregate.
    if not isinstance(token, str) or len(token) > 64:
        return False
    result = db.session.execute(db.delete(BrowserReceipt).where(
        BrowserReceipt.survey_id == survey.id, BrowserReceipt.scope == "decline",
        BrowserReceipt.key == digest(token), BrowserReceipt.expires_at > utcnow()))
    return result.rowcount == 1


def sweep(now):
    db.session.execute(db.delete(BrowserReceipt).where(BrowserReceipt.expires_at <= now))
    db.session.commit()


def _derived_seed(survey, pid):
    """The random seed of a participation's first page, derived from its pid with the app's secret key: the same pid
    always gets the same layout, nobody without the key can tell or choose it, and nothing has to be stored for it
    (audit 2026-10-15, T02-NEU-1: opening a link must not write to the database)."""
    import hmac

    from flask import current_app

    mac = hmac.new(str(current_app.config["SECRET_KEY"]).encode(), f"assignment:{survey.id}:{pid}".encode(),
                   hashlib.sha256).digest()
    return int.from_bytes(mac[:7], "big") >> 3  # 53 bits, as secrets.randbits(53) before


def assignment(survey, pid, legacy_seed=None, *, count=False):
    """Seed and network count of a new participation's first page. Opening the link (GET, count=False) only reads:
    the seed comes from the pid (_derived_seed), nothing is written or committed, so crawlers and scripts without a
    cookie leave no rows (audit 2026-10-15, T02-NEU-1). count=True when the participation is stored (first submit,
    after limits.allow_start): only then is the start counted for the sender's network in this hour
    ("assignment_network_starts" = stored starts from the same network before this one in the hour; audit SP13 TN-5).
    A receipt written by the code before (a page opened before the update) is still honoured."""
    from app import limits

    existing = db.session.get(BrowserReceipt, (survey.id, "assignment", digest(pid)))
    if existing is not None and existing.expires_at > utcnow() and existing.data:
        return existing.data
    seed = legacy_seed if legacy_seed is not None else _derived_seed(survey, pid)
    earlier = 0
    if count:
        sender = limits._shared_sender()
        if sender:
            bucket = str(int(utcnow().replace(tzinfo=timezone.utc).timestamp()) // 3600)
            network = receipt(survey, "assignment-network", sender + ":" + bucket, lock=True, hours=2)
            earlier = int((network.data or {}).get("starts", 0))
            network.data = {"starts": earlier + 1}
            # committed at once, so the network row is not held locked while the participation is stored. That
            # commits what the request wrote before it too (the limits counted for this start), and a start that is
            # then not stored (the page sent twice at the same moment, a once-only panel ID used elsewhere) keeps its
            # count: one too many for the network's hour at most. What is loaded stays loaded (the survey, its
            # questionnaire): the first submit reads it on (code review 2026-10-15, F6)
            from app.participate import _commit_keeping_loaded

            _commit_keeping_loaded()
    return {"seed": seed, "earlier": earlier}
