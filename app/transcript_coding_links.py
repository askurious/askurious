"""Coding links: a person without an account codes in one analysis of an interview guide (app/transcript_coding.py,
experimental "transcript_coding"; concept "Interviews kodieren", E2 (3), Ralf 2026-10-27).

Built like the interviewer link (app/interviewer.py), but its own table, its own session key and its own routes, so
that nothing of the interviewer links changes: the team makes a link with a name, a required expiry date at most
MAX_DAYS days ahead and at most MAX_DEVICES devices, and sees it once with its QR code (only the hash of the key is
stored, CodingLink). The key stands after "#", so that it reaches no server or proxy log and no referrer; the opening
page sends it once in the body of a POST (coding_link.js). Whoever opens it works in that analysis only: they see
its category system and its interviews, add subcategories and candidates, and propose changes; no other page of the
platform.

The session holds {analysis id: {"a" link, "r" revision, "d" device, "t" last request}} under SESSION_KEY. "Sperren",
"Neuer Link" and "Geräte abmelden" give the link a new revision, so every session of it ends with its next request;
it also ends with its expiry date, after IDLE minutes without a request, and when the guide is archived."""
import base64
import hashlib
import secrets
from datetime import datetime, timedelta

from flask import abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user

from app.extensions import db
from app.lang import de_en
from app.models import CodingLink, CodingProject, utcnow
from app.transcript_coding import bp, guide, project_of, signed_in_or_coder

SESSION_KEY = "ck"
MAX_DAYS = 90  # the expiry date is required and at most this far ahead (E2)
MAX_DEVICES = 3
IDLE = 60  # minutes
HINTS_VERSION = "2026-10-27"


def _digest(key):
    return hashlib.sha256(key.encode()).hexdigest()


def _revision():
    return secrets.token_urlsafe(18)[:24]


def link(survey, key):
    from app import brand

    return brand.public_base() + url_for("transcript_coding.link_start", code=survey.code) + "#" + key


def links(project):
    return list(db.session.scalars(db.select(CodingLink).filter_by(project_id=project.id)
                                   .order_by(CodingLink.created_at, CodingLink.id)))


def create(project, name, days, devices=MAX_DEVICES, by=None, access_ids=()):
    """A new link; returns (link, key) or (None, problem). The caller commits; the key is shown once."""
    name = " ".join(str(name or "").split())[:80]
    if not name:
        return None, de_en("Bitte einen Namen angeben.", "Please enter a name.")
    if any(l.name.casefold() == name.casefold() for l in links(project)):
        return None, de_en("Für „{name}“ gibt es schon einen Kodierlink.", "There is already a coding link for “{name}”.", name=name)
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 0
    if not 1 <= days <= MAX_DAYS:
        return None, de_en("Bitte ein Ablaufdatum in höchstens {n} Tagen angeben.", "Please enter an expiry date at most {n} days ahead.", n=MAX_DAYS)
    key = secrets.token_urlsafe(32)
    row = CodingLink(project_id=project.id, survey_id=project.survey_id, name=name, token_hash=_digest(key),
                     revision=_revision(), max_devices=max(1, min(int(devices or MAX_DEVICES), MAX_DEVICES)), devices=[],
                     access_ids=sorted({int(a) for a in access_ids}), created_by_id=getattr(by, "id", None),
                     created_by_name=(getattr(by, "name", "") or "")[:200], expires_at=utcnow() + timedelta(days=days))
    db.session.add(row)
    return row, key


def state(row, now=None):
    if row.blocked:
        return "blocked"
    if row.expires_at <= (now or utcnow()):
        return "expired"
    return "active" if row.last_used_at else "invited"


def usable(row, survey, project, now=None):
    return (row is not None and survey is not None and project is not None and row.project_id == project.id
            and row.survey_id == survey.id and project.survey_id == survey.id and survey.interview is not None
            and not survey.archived and not row.blocked and row.expires_at > (now or utcnow()))


def _entries():
    entries = session.get(SESSION_KEY)
    return entries if isinstance(entries, dict) else {}


def any_session():
    return bool(_entries())


def _forget(project_id):
    entries = dict(_entries())
    if entries.pop(str(project_id), None) is not None:
        session[SESSION_KEY] = entries


def _stale(device, now, minutes=IDLE + 1):
    try:
        last = datetime.fromisoformat(device.get("last") or "")
    except (TypeError, ValueError):
        return True
    return last <= now - timedelta(minutes=minutes)


def _drop_device(row, device_id):
    row.devices = [d for d in (row.devices or []) if d.get("id") != device_id]
    db.session.commit()


def current(survey, project):
    """The coding link of this analysis in this browser, or None; checks what can end it at every request."""
    entry = _entries().get(str(project.id))
    if not isinstance(entry, dict):
        return None
    now = utcnow()
    row = db.session.get(CodingLink, entry.get("a")) if isinstance(entry.get("a"), int) else None
    try:
        last = datetime.fromisoformat(entry.get("t", ""))
    except (TypeError, ValueError):
        last = None
    devices = {d.get("id"): d for d in (row.devices or [])} if row is not None else {}
    valid = usable(row, survey, project, now) and entry.get("r") == row.revision and entry.get("d") in devices
    if not valid or last is None or last <= now - timedelta(minutes=IDLE):
        if valid:
            _drop_device(row, entry["d"])
        _forget(project.id)
        return None
    entries = dict(_entries())
    entries[str(project.id)] = {**entry, "t": now.isoformat(timespec="seconds")}
    session[SESSION_KEY] = entries
    device = devices[entry["d"]]
    if row.last_used_at is None or row.last_used_at <= now - timedelta(minutes=1) or _stale(device, now, minutes=1):
        row.last_used_at = now
        row.devices = [{**d, "last": now.isoformat(timespec="seconds")} if d.get("id") == device.get("id") else d
                       for d in (row.devices or [])]
        db.session.commit()
    return row


def show_once(survey, project):
    """The link just made or renewed, once: {"link", "url", "qr"} or None."""
    from app.links import qr

    new = session.pop("ck_new", None)
    if not isinstance(new, dict):
        return None
    row = db.session.get(CodingLink, new.get("a")) if isinstance(new.get("a"), int) else None
    if row is None or row.project_id != project.id or row.token_hash != _digest(str(new.get("k", ""))):
        return None
    url = link(survey, new["k"])
    return {"link": row, "url": url, "qr": "data:image/svg+xml;base64," + base64.b64encode(qr(url)).decode()}


# --- the team's part -----------------------------------------------------------------------------------------------


def _team(code, pid):
    from app.transcript_coding import _in

    return _in(code, pid, team_only=True)


def _back(survey, project):
    return redirect(url_for("transcript_coding.project", code=survey.code, pid=project.id) + "#kodierlinks")


@bp.route("/<code>/interviews/coding/<int:pid>/links", methods=["POST"])
@signed_in_or_coder
def link_add(code, pid):
    from app import interviewer, security_log

    survey, project, who = _team(code, pid)
    known = {a.id for a in interviewer.accesses(survey)}
    row, key = create(project, request.form.get("name"), request.form.get("days"), request.form.get("devices", MAX_DEVICES),
                      current_user, [a for a in request.form.getlist("access", type=int) if a in known])
    if row is None:
        flash(key, "error")
        return _back(survey, project)
    db.session.commit()
    security_log.event("coding_link_created", account=current_user.id, target=survey.code)
    session["ck_new"] = {"a": row.id, "k": key}
    return _back(survey, project)


LINK_ACTIONS = ("link", "block", "unblock", "signout", "extend", "delete")


@bp.route("/<code>/interviews/coding/<int:pid>/links/<int:lid>/<action>", methods=["POST"])
@signed_in_or_coder
def link_change(code, pid, lid, action):
    from app import security_log

    survey, project, who = _team(code, pid)
    row = db.session.get(CodingLink, lid)
    if row is None or row.project_id != project.id or action not in LINK_ACTIONS:
        abort(404)
    name = row.name
    if action == "link":
        key = secrets.token_urlsafe(32)
        row.token_hash, row.revision, row.devices = _digest(key), _revision(), []
        session["ck_new"] = {"a": row.id, "k": key}
        message = de_en("Neuer Link für {name}: Der alte öffnet nichts mehr.", "New link for {name}: the old one opens nothing any more.", name=name)
    elif action == "block":
        row.blocked, row.revision, row.devices = True, _revision(), []
        message = de_en("Kodierlink von {name} gesperrt.", "Coding link of {name} blocked.", name=name)
    elif action == "unblock":
        row.blocked = False
        message = de_en("Kodierlink von {name} entsperrt.", "Coding link of {name} unblocked.", name=name)
    elif action == "signout":
        row.revision, row.devices = _revision(), []
        message = de_en("Alle Geräte von {name} abgemeldet.", "All devices of {name} signed out.", name=name)
    elif action == "extend":  # never further than MAX_DAYS from today
        row.expires_at = utcnow() + timedelta(days=MAX_DAYS)
        message = de_en("Kodierlink von {name} gilt bis {day}.", "Coding link of {name} valid until {day}.", name=name,
                        day=f"{row.expires_at:%d.%m.%Y}")
    else:  # the categories of the link stay, with its name
        db.session.delete(row)
        message = de_en("Kodierlink von {name} gelöscht. Seine Kategorien bleiben.", "Coding link of {name} deleted. Its categories stay.", name=name)
    db.session.commit()
    security_log.event(f"coding_link_{action}", account=current_user.id, target=survey.code)
    flash(message, "success")
    return _back(survey, project)


# --- the coders' part ----------------------------------------------------------------------------------------------


def _refused(survey, text, status=400):
    return render_template("transcript_coding/start.html", survey=survey, problem=text, hints=HINTS_VERSION), status


@bp.route("/<code>/coding-link", methods=["GET", "POST"])
def link_start(code):
    """The page of a coding link: the notes to confirm; the key comes from the address's "#" (coding_link.js)."""
    from app import limits, security_log

    survey = guide(code)
    if request.method == "GET":
        return render_template("transcript_coding/start.html", survey=survey, problem=None, hints=HINTS_VERSION)
    if not limits.allow_coding_link_try(count=False):
        return _refused(survey, de_en("Zu viele Versuche. Bitte später erneut öffnen.", "Too many attempts. Please open it again later."), 429)
    key = request.form.get("key", "").strip()
    row = db.session.scalar(db.select(CodingLink).filter_by(token_hash=_digest(key))) if key else None
    if row is None or row.survey_id != survey.id:
        limits.allow_coding_link_try()
        security_log.event("coding_link_refused", target=survey.code, reason="unknown")
        return _refused(survey, de_en("Dieser Link gilt nicht (mehr). Bitte bei den Forschenden einen neuen erbitten.",
                                      "This link is not (or no longer) valid. Please ask the researchers for a new one."), 404)
    project = db.session.get(CodingProject, row.project_id)
    if not usable(row, survey, project):
        security_log.event("coding_link_refused", target=survey.code, reason=state(row))
        return _refused(survey, de_en("Dieser Kodierlink ist gesperrt oder abgelaufen. Bitte an die Forschenden wenden.",
                                      "This coding link is blocked or has expired. Please contact the researchers."), 403)
    if row.hints_version != HINTS_VERSION and request.form.get("hints") != HINTS_VERSION:
        return _refused(survey, de_en("Bitte die Hinweise lesen und bestätigen.", "Please read and confirm the notes."))
    from app.interviewer import device_kind

    old = _entries().get(str(project.id))
    mine = old.get("d") if isinstance(old, dict) and old.get("a") == row.id and old.get("r") == row.revision else None
    devices = [d for d in (row.devices or []) if not _stale(d, utcnow()) and (mine is None or d.get("id") != mine)]
    if len(devices) >= row.max_devices:
        security_log.event("coding_link_refused", target=survey.code, reason="devices")
        return _refused(survey, de_en("Dieser Link ist schon auf {n} Geräten geöffnet. Die Forschenden können die Geräte abmelden.",
                                      "This link is already open on {n} devices. The researchers can sign the devices out.",
                                      n=len(devices)), 403)
    now = utcnow().isoformat(timespec="seconds")
    device = {"id": secrets.token_urlsafe(12), "kind": device_kind(request.headers.get("User-Agent")), "first": now, "last": now}
    row.devices = devices + [device]
    row.hints_version, row.last_used_at = HINTS_VERSION, utcnow()
    db.session.commit()
    session.permanent = True
    session[SESSION_KEY] = {**_entries(), str(project.id): {"a": row.id, "r": row.revision, "d": device["id"], "t": now}}
    security_log.event("coding_link_opened", target=survey.code, kind=device["kind"])
    return redirect(url_for("transcript_coding.project", code=survey.code, pid=project.id))


@bp.route("/<code>/coding-link/<int:pid>/logout", methods=["POST"])
def link_logout(code, pid):
    survey = guide(code)
    entry = _entries().get(str(pid))
    if isinstance(entry, dict) and isinstance(entry.get("a"), int):
        row = db.session.get(CodingLink, entry["a"])
        if row is not None and row.project_id == pid and entry.get("r") == row.revision:
            _drop_device(row, entry.get("d"))
    _forget(pid)
    return render_template("transcript_coding/start.html", survey=survey, problem=None, hints=HINTS_VERSION, left=True)


def block_of_account(user_id):
    """Block the coding links an account made, as its interviewer links (account_access.block_interviewers): whoever
    had the account could have made one for themselves. Blocked, not deleted; returns how many."""
    return db.session.execute(db.update(CodingLink).where(CodingLink.created_by_id == user_id, CodingLink.blocked.is_(False))
                              .values(blocked=True, revision=_revision()).execution_options(synchronize_session=False)).rowcount

