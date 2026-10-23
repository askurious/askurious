"""Immutable offline page receipts and durable, excluded review cases for diary answers."""
import copy
import hashlib
import hmac
import json
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import event
from werkzeug.datastructures import MultiDict

from app import browser_receipts
from app.extensions import db
from app.models import BrowserReceipt, Enrolment, Item, Response, utcnow

PREFIX = "page:"
REVIEW = "review"


def scope(person):
    return f"offline:{person.id}"


def capture(survey, person, response, items):
    data = {"part": response.part, "language": response.language, "qversion": response.qversion,
            "layout": copy.deepcopy(response.layout),
            "items": [{"id": i.id, "type": i.type, "code": i.code, "answer_mode": i.answer_mode,
                       "config": copy.deepcopy(i.cfg), "texts": {lang: i.t(lang) for lang in survey.languages}}
                      for i in items]}
    # Identical pages share a receipt, so a regular sync does not duplicate a whole questionnaire every 15 minutes.
    token = hmac.new(person.secret_hash.encode(), json.dumps(data, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    row = browser_receipts.receipt(survey, scope(person), token, hours=24 * KEEP_DAYS)
    if not row.data:
        row.data = {**data, "at": utcnow().replace(microsecond=0).isoformat()}
        _keep_newest(survey, person, row)
    row.expires_at = utcnow() + timedelta(days=KEEP_DAYS)
    return PREFIX + token


MAX_PAGES = 60  # kept pages per person; the layout is drawn once a day and module (layout_seed), so far fewer in use


def layout_seed(person, part, qversion, day):
    """The seed of a kept offline page: the same for a person, module, questionnaire version and day, so fetching the
    page every 15 minutes gives the same page and reuses its receipt, while the order is drawn anew each day
    (audit 2026-10-15, NB-2: every fetch drew a new layout and stored a whole questionnaire for 30 days)."""
    mac = hmac.new(person.secret_hash.encode(), f"offline:{part}:{qversion}:{day}".encode(), hashlib.sha256)
    return int.from_bytes(mac.digest()[:8], "big")


KEEP_DAYS = 30  # a kept page lasts this long after its last fetch (capture)
WAIT_BUFFER_DAYS = 1  # beyond OFFLINE_DAYS: a page fetched this recently may still have an answer waiting on the phone


def _keep_newest(survey, person, row):
    """At most MAX_PAGES kept pages per person: the ones fetched longest ago go first (their expiry was set by their
    last fetch). A queued entry whose page went is read as one without a kept page (review or "changed"). A page
    fetched within OFFLINE_DAYS + WAIT_BUFFER_DAYS is never removed here, as an answer to it may still wait in the
    app's queue (with many modules a page fetched once a day and module went after a few days, and its answer was
    refused as "changed" at every sync; audit 2026-10-21, F6). They still go 30 days after their last fetch."""
    from app.diary_api import OFFLINE_DAYS

    waiting = utcnow() + timedelta(days=KEEP_DAYS - OFFLINE_DAYS - WAIT_BUFFER_DAYS)
    rows = list(db.session.scalars(db.select(BrowserReceipt).where(
        BrowserReceipt.survey_id == survey.id, BrowserReceipt.scope == scope(person), BrowserReceipt.key != row.key,
        BrowserReceipt.expires_at <= waiting
    ).order_by(BrowserReceipt.expires_at.desc()).offset(MAX_PAGES - 1)))
    for old in rows:
        db.session.delete(old)


def page(survey, person, version, part):
    if not isinstance(version, str) or not version.startswith(PREFIX) or len(version) > 80:
        return None
    row = db.session.get(BrowserReceipt, (survey.id, scope(person), browser_receipts.digest(version[len(PREFIX):])))
    data = row.data if row and row.expires_at > utcnow() else None
    return data if data and data.get("part") == part else None


def frozen_items(survey, data):
    found = []
    for definition in data["items"]:
        item = Item(**copy.deepcopy(definition))
        # Transient objects only: assigning an ORM relationship would add frozen definitions to the live survey.
        item.__dict__["block"] = SimpleNamespace(survey=survey, survey_id=survey.id)
        found.append(item)
    return found


def fields(data):
    """Read every supplied value, or refuse the whole oversized form; never silently truncate."""
    form = data.get("form")
    if not isinstance(form, dict) or len(form) > 1000:
        raise ValueError("invalid")
    result = MultiDict()
    for key, values in form.items():
        if not isinstance(key, str) or len(key) > 100:
            raise ValueError("invalid")
        values = values if isinstance(values, list) else [values]
        if len(values) > 1000:
            raise ValueError("invalid")
        for value in values:
            if not isinstance(value, (str, int, float)) or isinstance(value, bool) or len(str(value)) > 20000:
                raise ValueError("invalid")
            result.add(key, str(value))
    return result


def compatible(survey, definitions):
    """A past page may finish only while its values still have precisely the same meaning in current exports."""
    for old in definitions:
        current = db.session.get(Item, old.id)
        if current is None or current.block.survey_id != survey.id or current.type != old.type:
            return False
        if old.kind.answer_shape(old) != current.kind.answer_shape(current):
            return False
    return True


REVIEW_BYTES = 64 * 1024  # the kept original of one review case at most (JSON), a page of answers is far smaller
MAX_OPEN_REVIEWS = 50  # open review cases of one person; more wait in the app's queue until some are cleared


def _original(data, receipt):
    prefixes = [f"i{item['id']}" for item in (receipt or {}).get("items") or []]
    return {key: value for key, value in (data.get("form") or {}).items()
            if any(key == p or key.startswith(p + "_") for p in prefixes) or
            (not prefixes and key.startswith("i"))}


def review_refusal(survey, person, data, receipt):
    """Why an entry may not become a review case, or None: its original is larger than REVIEW_BYTES ("invalid"), or
    the person has MAX_OPEN_REVIEWS open cases ("limit"). Before, every invalid entry kept up to 2 MB of raw form for
    good, up to 50 a day (audit 2026-10-15, NB-3). Nothing is stored then; the app keeps the entry in its queue."""
    if len(json.dumps(_original(data, receipt), separators=(",", ":"))) > REVIEW_BYTES:
        return "invalid"
    open_cases = db.session.scalar(db.select(db.func.count(Response.id)).where(
        Response.survey_id == survey.id, Response.enrolment_id == person.id, Response.status == REVIEW))
    return "limit" if open_cases >= MAX_OPEN_REVIEWS else None


def review(response, data, receipt, reason, errors=None):
    response.status = REVIEW
    response.completed_at = None
    response.answers = {}  # original fields are never interpreted with the current questionnaire
    original = _original(data, receipt)
    response.occasion = {**(response.occasion or {}), "offline": True,
                         "review": {"reason": reason, "errors": {str(k): str(v) for k, v in (errors or {}).items()},
                                    "form": copy.deepcopy(original), "page": copy.deepcopy(receipt),
                                    # without a kept page: the questionnaire the answer was read against (F5)
                                    **({} if receipt else {"version": data.get("version")})}}


def review_page(survey, response):
    """The page a review case is read and accepted against: its kept page, or, for an entry of an older app sent
    without one, the current items of its part while the questionnaire is still the one the entry was read against
    (the signature the app sent). None when neither holds: then the case cannot be accepted, since its values would
    be read against another questionnaire (audit 2026-10-21, F5)."""
    from app import diary, runtime

    review = (response.occasion or {}).get("review") or {}
    if review.get("page"):
        return review["page"]
    if not review.get("version") or review["version"] != runtime._signature(survey):
        return None
    items = [i for b in survey.blocks if diary.part_of(b) == response.part for i in b.items]
    return {"part": response.part, "language": response.language, "qversion": response.qversion,
            "layout": copy.deepcopy(response.layout), "unpaged": True,
            "items": [{"id": i.id, "type": i.type, "code": i.code, "answer_mode": i.answer_mode,
                       "config": copy.deepcopy(i.cfg), "texts": {lang: i.t(lang) for lang in survey.languages}}
                      for i in items]}


@event.listens_for(Enrolment, "before_delete")
def _forget_page_receipts(_mapper, connection, person):
    connection.execute(db.delete(BrowserReceipt).where(BrowserReceipt.survey_id == person.survey_id,
                                                       BrowserReceipt.scope == scope(person)))
