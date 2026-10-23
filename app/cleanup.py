"""File cleanups still owed after a deletion (audit 2026-10-05, L-8).

A deletion on request (app/erasure.py) and the retention periods (app/retention.py) commit the rows first and
clean the files after: the lines of the participations in the saved states, the kept results, the HB estimates.
Should that fail (a file locked, a full disk) or the process end in between (restart, deploy), the next run would
not come back to it, since the rows that led there are gone. So a note per survey folder is written before the
commit and removed once the files are clean; every housekeeping run (lifecycle.close_due_surveys ->
retention.run) does what a note still names.

A note is a small JSON file instance/uploads/cleanup/<uid>.json (no table, no schema change):
  pids      participation IDs deleted on request: their lines go from the saved states. Only those the database
            no longer holds are acted on, so a note from a deletion whose commit failed changes nothing
  periods   "personal" and/or "answers": the files of that period, again only if the period is over;
            "deleted": the survey was deleted (lifecycle.delete), all its folders go once its row is gone
            (audit 2026-10-18, FUNC-04: a process ending right after the commit, or a file that resisted, left
            the saved data files and HB results of a deleted survey on the disk for good)
It holds no answer and no identifier from the link; a participation ID says nothing about the person.
"""
import json
import os
import threading
import time

from flask import current_app

FOLDER = "cleanup"
DELETED = "deleted"
# a note "deleted" whose survey is still there after this long: the deletion was rolled back, the note goes
DELETION_SETTLED = 3600


def _path(uid):
    return os.path.join(current_app.config["UPLOAD_FOLDER"], FOLDER, f"{uid}.json")


def read(uid):
    """{"pids": [...], "periods": [...]} still owed for this survey folder ({} if nothing)."""
    try:
        with open(_path(uid), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(uid, data):
    path = _path(uid)
    data = {k: sorted(set(v)) for k, v in data.items() if v}
    if not data:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _held(uid):
    """The note of this survey folder held by one writer at a time, also across processes: two deletions at once
    would otherwise each read the note and write it back without the other's IDs, and a cleanup that then fails were
    never tried again (code review 2026-10-15, D4)."""
    from app import report_jobs

    path = _path(uid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return report_jobs._claim(path + ".claim")


def note(uid, pids=(), period=None):
    """Before the commit: these files are owed. Never raises (the deletion itself must not fail over the note)."""
    try:
        with _held(uid):
            data = read(uid)
            data["pids"] = list(data.get("pids") or []) + [str(p) for p in pids]
            data["periods"] = list(data.get("periods") or []) + ([period] if period else [])
            _write(uid, data)
    except OSError as e:
        current_app.logger.error("Cleanup note for survey folder %s not written: %s", uid, e)


def done(uid, pids=(), period=None):
    """The files are clean: take them out of the note."""
    try:
        with _held(uid):
            data = read(uid)
            gone = {str(p) for p in pids}
            data["pids"] = [p for p in data.get("pids") or [] if p not in gone]
            data["periods"] = [p for p in data.get("periods") or [] if p != period]
            _write(uid, data)
    except OSError as e:
        current_app.logger.error("Cleanup note for survey folder %s not updated: %s", uid, e)


def owed():
    """The uids of all survey folders with a note."""
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], FOLDER)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(n[:-len(".json")] for n in names if n.endswith(".json"))


def _age(uid):
    """Seconds since the note was last written."""
    try:
        return time.time() - os.path.getmtime(_path(uid))
    except OSError:
        return 0


def retry(now):
    """Do what the notes still name (from retention.run). Returns the number of folders cleaned."""
    from app import erasure, retention
    from app.extensions import db
    from app.models import Response, Survey

    from app import lifecycle

    cleaned = 0
    for uid in owed():
        data = read(uid)
        survey = db.session.scalar(db.select(Survey).filter_by(uid=uid))
        if survey is None:
            # deleted: its folders go now if lifecycle.delete could not remove them (FUNC-04). A note without
            # "deleted" is from before; that survey's own deletion removed its folders then
            if DELETED in (data.get("periods") or []):
                if not lifecycle.remove_folders(lifecycle.survey_folders(uid)):
                    continue  # logged; the note stays for the next run
                cleaned += 1
            with _held(uid):
                _write(uid, {})
            continue
        if DELETED in (data.get("periods") or []) and _age(uid) > DELETION_SETTLED:
            done(uid, period=DELETED)  # its deletion never came about (rolled back)
        pids = set(data.get("pids") or [])
        if pids:
            still = set(db.session.scalars(db.select(Response.pid).filter(Response.survey_id == survey.id,
                                                                          Response.pid.in_(pids))))
            try:
                if pids - still:
                    erasure._forget_in_files(survey, pids - still)
            except OSError as e:
                current_app.logger.error("Erasure in survey %s: files still not cleaned: %s", survey.code, e)
            else:
                done(uid, pids=pids)
                cleaned += 1
        dates = retention.due(survey)
        for period in [p for p in ("answers", "personal") if p in (data.get("periods") or [])]:
            if dates[period] is None or dates[period] > now:  # the deletion that led here never came about
                done(uid, period=period)
                continue
            try:
                retention._clean_files(uid, blank_ids=period == "personal")
            except OSError as e:
                current_app.logger.error("Retention: files of survey folder %s still not cleaned: %s", uid, e)
            else:
                done(uid, period=period)
                cleaned += 1
    return cleaned
