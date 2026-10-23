"""Interviews without a network (offline mode, concept "Konzept Offline-Modus für Interviews", Ralf 2026-10-22, E1-E8
as recommended). Experimental (FEATURE), and switched on per guide (Survey.interview["offline"]).

Before a day in the field, with a network, the device takes a field pack: the server reserves a number of interviews
for it (E1 b, E2 a: 1 to MAX_SLOTS), each with its participation ID, its identifier and its deletion code, so the
server stays the only place that gives numbers and codes. Without a network the device fills these places one after
the other (app/static/interview-offline.js, the data encrypted with a device PIN, E3 a, E4 a); with a network again
it sends each interview as a whole (`sync`), and the recording with the batches of app/interview_audio.py.

Survey.interview[KEY] = [{"id": pack id, "device": device id, "made", "until": ISO times, "by": user id or None,
                          "access": interviewer access id or None, "by_name": name, "withdrawn": bool,
                          "slots": [{"pid", "label", "rid": interview id once synced, "erase": True when its code was
                                     used before the sync}]}]
A place without an interview takes no row: nothing counts in lists, results or exports until it is synced. A pack
lasts DAYS days (E5 b); `run` (housekeeping) drops it with every place never synced. A slot is found again by its
pid when a sync is repeated, so a lost answer never makes a second interview."""
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user

from app.extensions import db, json_given
from app.interviewer import interviewer_or_signed_in
from app.lang import de_en
from app.models import Response, new_pid, utcnow

bp = Blueprint("interview_offline", __name__, url_prefix="/guide")

FEATURE = "offline_interviews"
KEY = "offline_packs"
DAYS = 7          # E5 b: how long a pack and the data on the device last
MAX_SLOTS = 20    # E2 a
DEFAULT_SLOTS = 5


# --- who may use it ------------------------------------------------------------------------------------------------


def offered(survey, access=None):
    """The feature for whoever asks: a researcher who has it, an interviewer whose link was made by one who has it
    and may still edit the guide (as the participant screen, app/interview_screen.offered)."""
    from app import experimental, interviewer
    from app.models import User

    access = access if access is not None else interviewer.actor(survey)
    if access is None:
        return experimental.enabled(FEATURE, survey=survey)
    creator = db.session.get(User, access.created_by_id) if access.created_by_id else None
    return creator is not None and survey.can(creator, "editor") and experimental.enabled(FEATURE, user=creator, survey=survey)


def switched_on(survey):
    """E6 a: the guide allows it (its switch on the page "Interviews")."""
    return bool((survey.interview or {}).get("offline"))


def usable(survey):
    return switched_on(survey) and offered(survey)


def _who(code):
    """(survey, access): a researcher with "Bearbeiten" (access None) or an interviewer of this guide."""
    from app import interviewer

    survey = interviewer.guide(code)
    if current_user.is_authenticated and survey.can(current_user, "editor"):
        return survey, None
    access = interviewer.current(survey)
    if access is None or not interviewer.usable(access, survey):
        abort(404)
    return survey, access


# --- the packs -----------------------------------------------------------------------------------------------------


def _iso(t):
    return t.isoformat(timespec="seconds")


def _time(text):
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def packs(survey):
    """Copies: changing the loaded JSON in place would not be stored (SQLAlchemy sees no change)."""
    import copy

    return [copy.deepcopy(p) for p in (survey.interview or {}).get(KEY) or [] if isinstance(p, dict)]


def _store(survey, items):
    survey.interview = {**(survey.interview or {}), KEY: items}


def live(pack, now=None):
    """A pack the device may still fill and sync: not withdrawn, not expired."""
    until = _time(pack.get("until"))
    return not pack.get("withdrawn") and until is not None and until > (now or utcnow())


def reserved_labels(survey):
    """The identifiers of places not synced yet: no new interview takes them."""
    return {s.get("label") for p in packs(survey) if live(p) for s in p.get("slots") or [] if not s.get("rid")}


def _labels(survey, access, n):
    from app import interview_data, interviewer

    taken = {(r.interview or {}).get("label") for r in interview_data.interviews(survey)} | reserved_labels(survey)
    if access is not None:
        prefix, fmt = interviewer.short(access.name), "{p}-{n:02d}"
    else:
        prefix = (survey.interview or {}).get("prefix") if (survey.interview or {}).get("prefix") in interview_data.PREFIXES else "I"
        fmt = "{p}{n:02d}"
    out, k = [], 1
    while len(out) < n:
        label = fmt.format(p=prefix, n=k)
        if label not in taken:
            out.append(label)
        k += 1
    return out


def make_pack(survey, access, n, device=None):
    """Reserve n places (E1 b): pids, identifiers and the deletion codes, which go to the device only."""
    from app import erasure

    n = max(1, min(int(n or DEFAULT_SLOTS), MAX_SLOTS))
    now = utcnow()
    labels = _labels(survey, access, n)
    pack = {"id": secrets.token_urlsafe(9), "device": device or secrets.token_urlsafe(12), "made": _iso(now),
            "until": _iso(now + timedelta(days=DAYS)), "by": None if access is not None else current_user.id,
            "access": access.id if access is not None else None,
            "by_name": access.name if access is not None else (current_user.name or ""), "withdrawn": False,
            "slots": [{"pid": new_pid(survey.code), "label": label, "rid": None} for label in labels]}
    if not survey.erase_code:  # interviews get a deletion code to hand over (app/erasure.py)
        survey.erase_code = True
    _store(survey, packs(survey) + [pack])
    codes = {s["pid"]: erasure.written(survey, erasure.raw_code(survey, s["pid"])) for s in pack["slots"]}
    return pack, codes


def find_slot(survey, pack_id, pid):
    for p in packs(survey):
        if p.get("id") == pack_id:
            for s in p.get("slots") or []:
                if s.get("pid") == pid:
                    return p, s
            return p, None
    return None, None


def erase_pending(code, survey=None):
    """A deletion code of a place not synced yet (the interviewed person asks before the device came back online): the
    place is marked, the sync then refuses it and the device deletes its copy. Returns the survey or None."""
    from app import erasure
    from app.models import Survey

    raw = erasure.normalise(code)
    if raw is None:
        return None
    surveys = [survey] if survey is not None else db.session.scalars(
        db.select(Survey).filter(json_given(Survey.interview))).all()
    for s in surveys:
        items = packs(s)
        for p in items:
            for slot in p.get("slots") or []:
                if not slot.get("rid") and not slot.get("erase") and erasure.raw_code(s, slot.get("pid", "")) == raw:
                    slot["erase"] = True
                    _store(s, items)
                    db.session.commit()
                    return s
    return None


def run(now=None):
    """Housekeeping: packs past their time go, and with them every place never synced (E5 b). Returns how many."""
    from app.models import Survey

    now, gone = now or utcnow(), 0
    for survey in db.session.scalars(db.select(Survey).filter(json_given(Survey.interview))).all():
        items = packs(survey)
        keep = [p for p in items if (_time(p.get("until")) or now) > now]
        if len(keep) != len(items):
            gone += len(items) - len(keep)
            _store(survey, keep)
    if gone:
        db.session.commit()
    return gone


# --- the sync ------------------------------------------------------------------------------------------------------


def sync(survey, access, data):
    """One interview from the device, as a whole and as often as needed: the row is made once (found again by its
    pid), then the state of the view and the forms are stored as the online routes store them; "finish" ends it.
    Returns (status, body)."""
    from app import interview, interview_audio, interview_data

    if not isinstance(data, dict):
        return 400, {"ok": False, "error": "bad"}
    pack, slot = find_slot(survey, str(data.get("pack") or ""), str(data.get("pid") or ""))
    if pack is None or slot is None or pack.get("device") != data.get("device"):
        return 404, {"ok": False, "error": "unknown"}
    if access is not None and pack.get("access") != access.id:
        return 404, {"ok": False, "error": "unknown"}
    if slot.get("erase"):
        return 410, {"ok": False, "error": "erased"}  # its deletion code was used: the device deletes its copy
    row = db.session.scalar(db.select(Response).filter_by(survey_id=survey.id, pid=slot["pid"], is_preview=False))
    if row is None:
        if not live(pack):
            return 409, {"ok": False, "error": "withdrawn" if pack.get("withdrawn") else "expired"}
        consent = data.get("consent") if isinstance(data.get("consent"), dict) else {}
        if not consent.get("all"):
            return 400, {"ok": False, "error": "consent"}  # no interview without the tick (concept I10), also offline
        audio = bool(consent.get("audio")) and (access is None or access.may_record)
        form = {"consent_form": consent.get("form"), "consent": "1"}
        mode = data.get("mode") if data.get("mode") in interview.MODES else interview.state(survey)["mode"]
        started = _time(data.get("started")) or utcnow()
        meta = {"label": slot["label"], "date": str(data.get("date") or "")[:10], "mode": mode,
                "consent": {**interview_data.consent_of(form, audio), "all": str(consent.get("all"))[:25]},
                "marks": [], "elapsed": 0, "blocks": {}, "offline": pack["id"]}
        if access is not None:
            meta.update({"by": None, "by_name": access.name, "access": access.id})
        else:
            meta.update({"by": pack.get("by"), "by_name": pack.get("by_name") or ""})
        row = Response(survey_id=survey.id, language=survey.default_language, source="test" if data.get("trial") else "real",
                       status=Response.ACTIVE, answers={}, pid=slot["pid"], started_at=min(started, utcnow()),
                       last_active_at=utcnow(), interview=meta)
        db.session.add(row)
        db.session.flush()
    if access is not None and (row.interview or {}).get("access") != access.id:
        return 404, {"ok": False, "error": "unknown"}
    if row.status != Response.COMPLETED:
        if isinstance(data.get("state"), dict):
            interview_data.save(survey, row, data["state"])
        for form in data.get("forms") or []:
            if isinstance(form, list):
                from werkzeug.datastructures import MultiDict

                interview_data.save_forms(survey, row, MultiDict([(str(f[0]), str(f[1])) for f in form
                                                                  if isinstance(f, list) and len(f) == 2]))
    missing = {}
    if data.get("finish") and row.status != Response.COMPLETED:
        # E7 a: the browser checked the required questions; the server checks again and names what it finds, but ends
        # the interview all the same: it took place, and the person has the deletion code already
        missing = interview_data.missing_required(survey, row, "short")
        row.status, row.completed_at = Response.COMPLETED, _time(data.get("ended")) or utcnow()
    items = packs(survey)
    for p in items:
        if p.get("id") == pack["id"]:
            for s in p.get("slots") or []:
                if s.get("pid") == slot["pid"]:
                    s["rid"] = row.id
    _store(survey, items)
    from app import erasure

    erasure.code_for(survey, row)  # notes the hash, so the code given offline deletes this interview from now on
    db.session.commit()
    return 200, {"ok": True, "rid": row.id, "done": row.status == Response.COMPLETED,
                 "audio": url_for("interview_audio.upload", code=survey.code, rid=row.id),
                 "part": interview_audio.next_part(row), "missing": sorted(str(k) for k in missing)}


# --- the routes ----------------------------------------------------------------------------------------------------


@bp.route("/<code>/offline")
@interviewer_or_signed_in
def page(code):
    """The interview view for the field (app/static/interview-offline.js): the guide without an interview of its own;
    the device picks a place of its pack. Kept by the service worker (interview-sw.js), so it opens without a network."""
    from types import SimpleNamespace

    from app import interview

    survey, access = _who(code)
    if not usable(survey):
        abort(404)
    placeholder = SimpleNamespace(id=0, pid="", source="real", status=Response.ACTIVE, COMPLETED=Response.COMPLETED,
                                  interview={"label": "", "consent": {"audio": access is None or access.may_record}, "audio": []})
    return interview.conduct_page(survey, placeholder, offline=True, access=access)


@bp.route("/<code>/offline/pack", methods=["POST"])
@interviewer_or_signed_in
def pack_new(code):
    """A field pack for this device (with a network): its places and their deletion codes, once."""
    survey, access = _who(code)
    if not usable(survey):
        abort(404)
    data = request.get_json(silent=True) or {}
    device = str(data.get("device") or "")[:32] or None
    pack, codes = make_pack(survey, access, data.get("n"), device)
    db.session.commit()
    return jsonify(ok=True, pack=pack["id"], device=pack["device"], until=pack["until"],
                   slots=[{"pid": s["pid"], "label": s["label"], "code": codes[s["pid"]]} for s in pack["slots"]])


@bp.route("/<code>/offline/sync", methods=["POST"])
@interviewer_or_signed_in
def sync_view(code):
    survey, access = _who(code)
    if not offered(survey, access):
        abort(404)
    status, body = sync(survey, access, request.get_json(silent=True))
    if status != 200:
        db.session.rollback()
    return make_response(jsonify(body), status)


@bp.route("/<code>/offline/switch", methods=["POST"])
@interviewer_or_signed_in
def switch(code):
    """E6 a: the guide's switch, by a researcher with "Bearbeiten" who has the feature."""
    from app import interview_data

    survey = interview_data._survey(code, need="editor")
    if not offered(survey, None):
        abort(404)
    survey.interview = {**(survey.interview or {}), "offline": bool(request.form.get("on"))}
    db.session.commit()
    flash(de_en("Offline-Modus für diesen Leitfaden eingeschaltet.", "Offline mode switched on for this guide.") if switched_on(survey)
          else de_en("Offline-Modus für diesen Leitfaden ausgeschaltet.", "Offline mode switched off for this guide."), "success")
    return redirect(url_for("interview_data.index", code=survey.code))


@bp.route("/<code>/offline/packs/<pack_id>/withdraw", methods=["POST"])
@interviewer_or_signed_in
def withdraw(code, pack_id):
    """A pack taken back (a lost tablet): its places not synced yet are refused from now on."""
    from app import interview_data

    survey = interview_data._survey(code, need="editor")
    items = packs(survey)
    for p in items:
        if p.get("id") == pack_id:
            p["withdrawn"] = True
    _store(survey, items)
    db.session.commit()
    flash(de_en("Feldpaket zurückgezogen: Seine noch nicht abgeglichenen Interviews nimmt der Server nicht mehr an.",
                "Field pack withdrawn: the server no longer accepts its interviews not yet synced."), "success")
    return redirect(url_for("interview_data.index", code=survey.code))


def panel(survey, access=None):
    """What the pages "Interviews" and "Meine Interviews" show of it (templates/interview/_offline_panel.html)."""
    offered_ = offered(survey, access)
    may_switch = access is None and current_user.is_authenticated and survey.can(current_user, "editor")
    return {"offered": offered_ and (may_switch or switched_on(survey)), "on": switched_on(survey), "may_switch": may_switch,
            "days": DAYS, "packs": [p for p in summary(survey) if access is None or p["access"] == access.id]}


def summary(survey):
    """For the page "Interviews": the packs with their state (open places, synced ones)."""
    now = utcnow()
    out = []
    for p in packs(survey):
        slots = p.get("slots") or []
        out.append({"id": p.get("id"), "by_name": p.get("by_name") or "", "access": p.get("access"), "made": p.get("made") or "",
                    "until": p.get("until") or "", "live": live(p, now), "withdrawn": bool(p.get("withdrawn")),
                    "slots": len(slots), "synced": sum(1 for s in slots if s.get("rid"))})
    return out
