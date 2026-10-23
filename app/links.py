"""Public survey links: short codes, chosen labels, QR codes, and the URL converter for survey ids.

Every survey has a random code (models.new_code: 6 characters without 0/o, 1/l/i). Researchers may add a
label (/s/<label>); labels are never released, so a printed link or QR code never leads to another study.
Labels need at least MIN_LABEL characters; shorter ones are reserved for site admins.
Researcher pages use the code as well (/surveys/<code>/...).
"""
import io
import re
from urllib.parse import quote

from flask import g
from werkzeug.routing import BaseConverter, ValidationError

from app.extensions import db
from app.models import Survey, SurveyLabel

MIN_LABEL = 5
LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]*\Z")


def local_path(value):
    """`value` if it is a path on this site (for "next"/"back" parameters), else "".
    Browsers read a backslash like a slash, so one is refused as well (it would lead to another host)."""
    from urllib.parse import urlsplit

    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(ch) < 32 for ch in value):
        return ""
    parts = urlsplit(value)
    return "" if parts.scheme or parts.netloc else value


def normalize(label):
    return (label or "").strip().lower()


def _admin(user):
    from app import twofactor

    return twofactor.admin_rights(user)  # with the code of the second factor while ADMIN_TOTP is on (audit 2026-10-06, H2)


def label_error(label, user):
    """Why this label cannot be used, or None."""
    if not label:
        return "Bitte einen Kurznamen eingeben."
    if len(label) > 40:
        return "Der Kurzname ist zu lang (höchstens 40 Zeichen)."
    if not LABEL_RE.match(label):
        return "Kurzname: nur Kleinbuchstaben, Ziffern, - und _, beginnend mit einem Buchstaben."
    if len(label) < MIN_LABEL and not _admin(user):
        return f"Kurznamen brauchen mindestens {MIN_LABEL} Zeichen."
    if db.session.get(SurveyLabel, label) or db.session.scalar(db.select(Survey.id).filter_by(code=label)):
        return f"Der Kurzname „{label}“ ist bereits vergeben (einmal genutzte Kurznamen bleiben gesperrt)."
    return None


def set_label(survey, label, user):
    """Register the label of the survey. Returns an error message or None. A survey gets one label only;
    it cannot be changed or replaced afterwards (the code always works as well)."""
    label = normalize(label)
    if survey.labels:
        return f"Diese Befragung hat bereits das Label „{survey.labels[0].label}“; ein Label wird nur einmal vergeben."
    error = label_error(label, user)
    if error:
        return error
    db.session.add(SurveyLabel(label=label, survey_id=survey.id, created_by_id=getattr(user, "id", None)))
    db.session.flush()
    db.session.expire(survey, ["labels"])
    return None


def resolve(key):
    """The survey behind a participant link: label or code."""
    from sqlalchemy.orm import selectinload, defer
    from app.models import Block

    key = (key or "").strip()
    low = key.lower()
    from sqlalchemy import case

    label = db.select(SurveyLabel.survey_id).where(SurveyLabel.label == low).scalar_subquery()
    label_exists = db.select(SurveyLabel.label).where(SurveyLabel.label == low).exists()
    code = db.select(Survey.id).where(Survey.code == low).scalar_subquery()
    # A taken/deleted label stays taken (NULL), even if it could also be a short code.
    survey_id = case((label_exists, label), else_=code)
    query = db.select(Survey).options(selectinload(Survey.blocks).selectinload(Block.items), selectinload(Survey.labels),
                                     defer(Survey.codings), defer(Survey.status_log), defer(Survey.study_plan))
    query = query.where(Survey.id == survey_id)
    return db.session.scalar(query)


def participant_url(survey, params=None):
    from app.hosts import participant_base

    url = participant_base() + f"/s/{survey.public_key}"
    if params:
        url += "?" + "&".join(f"{quote(k)}={quote(str(v))}" for k, v in params)
    return url


def qr(url, kind="svg"):
    """The QR code of a URL as SVG (default) or PNG bytes."""
    import segno

    code = segno.make(url, error="m")
    out = io.BytesIO()
    if kind == "png":
        code.save(out, kind="png", scale=10, border=2)
    else:
        code.save(out, kind="svg", scale=8, border=2, xmldecl=False)
    return out.getvalue()


class SurveyIdConverter(BaseConverter):
    """<sid:survey_id> in routes: the URL shows the survey code, the view still gets the numeric id."""

    regex = r"[A-Za-z0-9]+"

    def to_python(self, value):
        survey_id = db.session.scalar(db.select(Survey.id).filter_by(code=value.lower()))
        if survey_id is None:
            raise ValidationError()
        return survey_id

    def to_url(self, value):
        if isinstance(value, Survey):
            return value.code
        if isinstance(value, int) or str(value).isdigit():
            cache = g.setdefault("_survey_codes", {})
            sid = int(value)
            if sid not in cache:
                # a survey the request has loaded already gives its code without a query (one per card of the
                # start page before; audit 2026-10-06, P8)
                known = db.session.identity_map.get(db.session.identity_key(Survey, sid))
                code = vars(known).get("code") if known is not None else None  # loaded, not expired
                cache[sid] = code or db.session.scalar(db.select(Survey.code).filter_by(id=sid)) or str(sid)
            return cache[sid]
        return str(value)
