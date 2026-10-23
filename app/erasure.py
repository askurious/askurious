"""Deleting a participation because the person asks for it (withdrawal of consent, right to erasure).

Three ways lead to the same deletion:
  here      in the browser that took part, while answering and for 15 minutes after completion;
            needs no code and nothing stored for it, so it works in every survey
  code      with the deletion code the end page and the privacy page show (survey setting "erase_code"), from
            any device and at any later time. The code is computed from the participation ID and the server's
            key; the participation keeps only a hash of it. Researchers cannot read a code from the data,
            and a code does not say whose answers it belongs to
  request   by the researchers, for a person who wrote to them and named the code, the participation ID or an
            identifier from the link, or an e-mail address given in the separated section

What goes: the participation with everything stored about it, its lines in the saved states of the survey
(app/snapshots.py), and the kept results and hierarchical Bayes estimates it is part of (they are computed
anew without it). What cannot be reached from here: files downloaded before and records published on Zenodo.
For those the survey keeps a bare number per day (models.Erasure), so the researchers see that a file is out of
date; it holds nothing about the participation itself.
"""
import csv
import hashlib
import hmac
import io
import os

from flask import current_app

from app.extensions import db
from app.models import ContactEntry, Erasure, Response, SplitSession

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 32 characters; none that are mistaken for another (0 O, 1 I)
LENGTH, GROUP = 12, 4  # 60 bits, written XXXX-XXXX-XXXX
EXAMPLE = "K7MP-2QXD-9RTW"
# shown with the internal number of the survey in front, "142-K7MP-2QXD-9RTW" (Philipp, 2026-10-08): so the code
# alone says which survey it belongs to (the page "Antworten löschen" of the platform, public.erase); the hash
# covers the twelve characters only, so codes shown before without the number still work
WAYS = {"self": "von Teilnehmenden selbst", "request": "auf Anfrage durch die Forschenden",
        "retention": "durch Ablauf einer Aufbewahrungsfrist", "platform": "durch die Plattform"}
WAYS_EN = {"self": "by participants themselves", "request": "on request by the researchers",
           "retention": "by expiry of a retention period", "platform": "by the platform"}


def ways():
    """WAYS in the language of the page (the keys stay)."""
    from app.lang import english

    return WAYS_EN if english() else WAYS


def _write(code):
    return "-".join(code[i:i + GROUP] for i in range(0, LENGTH, GROUP))


def split(text):
    """(survey id or None, the code as it is hashed or None) of what someone typed: "142-K7MP-2QXD-9RTW", also
    without the number, in small letters or with spaces."""
    import re

    def clean(part):
        code = "".join(ch for ch in part.upper() if ch.isalnum())
        return code if len(code) == LENGTH and all(ch in ALPHABET for ch in code) else None

    import unicodedata

    # without invisible format marks (Cf): the direction marks around a code copied from a right-to-left page (X32)
    raw = "".join(ch for ch in str(text or "") if unicodedata.category(ch) != "Cf").strip()
    m = re.match(r"^(\d{1,9})[\s\-–]+(.+)$", raw)
    if m and clean(m.group(2)):
        return int(m.group(1)), clean(m.group(2))
    return None, clean(raw)  # without the number (codes shown before 2026-10-08)


def normalise(text):
    """The code as it is hashed: capitals without the number of the survey, the hyphens and spaces people type;
    None if this is no code."""
    return split(text)[1]


def example(survey=None):
    """How a code looks, with the number of the survey (or a made-up one)."""
    return f"{survey.id if survey is not None else 142}-{EXAMPLE}"


def digest(code):
    return hashlib.sha256(("erase:" + code).encode()).hexdigest()


def raw_code(survey, pid):
    """The twelve characters of the deletion code of the participation `pid` (as they are hashed)."""
    key = current_app.config["SECRET_KEY"]
    raw = hmac.new(key.encode() if isinstance(key, str) else key, f"erase|{survey.uid}|{pid}".encode(),
                   hashlib.sha256).digest()
    number = int.from_bytes(raw[:8], "big")
    return "".join(ALPHABET[(number >> (5 * i)) & 31] for i in range(LENGTH))


def written(survey, code):
    """A code as people read it: the survey's number and the characters in groups."""
    return f"{survey.id}-{_write(code)}"


def code_for(survey, response):
    """The deletion code of a stored real participation, or None (setting off, preview, not stored yet). The
    first call notes its hash with the participation, without touching the row's version: the page that shows
    the code must not make a page the person sends at the same moment look like sent twice."""
    if not getattr(survey, "erase_code", False) or response is None or response.id is None or response.is_preview \
            or not response.pid:
        return None
    if getattr(response, "enrolment_id", None) and (response.occasion or {}).get("part") != "entry":
        return None  # a diary study: one code per person, on the end of the entry part; it deletes all entries
    code = raw_code(survey, response.pid)
    if response.erase_hash != digest(code):
        db.session.execute(db.update(Response.__table__).where(Response.__table__.c.id == response.id)
                           .values(erase_hash=digest(code)))
        db.session.commit()
    return written(survey, code)


def by_code(survey, text):
    """The participation of this survey the code belongs to, or None. The number in front is not checked: the twelve
    characters decide, so a mistyped number does not stop a deletion (Ralf, 2026-10-08)."""
    code = normalise(text)
    if code is None:
        return None
    return db.session.scalar(db.select(Response).filter_by(survey_id=survey.id, erase_hash=digest(code), is_preview=False))


def anywhere(text):
    """(survey, participation) for a code typed without knowing the survey (public.erase), or (None, None). Looked
    up by the twelve characters in all surveys (index ix_response_erase_hash; 60 bits: no two alike), so a code
    without the number or with a mistyped one is found too (Ralf, 2026-10-08)."""
    from app.models import Survey

    code = normalise(text)
    if code is None:
        return None, None
    response = db.session.scalar(db.select(Response).filter_by(erase_hash=digest(code), is_preview=False).limit(1))
    if response is None:
        return None, None
    survey = db.session.get(Survey, response.survey_id)
    return (survey, response) if survey is not None and getattr(survey, "erase_code", False) else (None, None)


def find(survey, text):
    """What a request to the researchers names: ([participations], [entries of the separated section]). A
    deletion code, a participation ID or the value of an identifier stored from the link finds participations;
    an e-mail address finds the entries of the separated section that hold exactly this address."""
    term = " ".join(str(text or "").split())
    if not term:
        return [], []
    found = {}
    hit = by_code(survey, term)
    if hit is not None:
        found[hit.id] = hit
    real = db.select(Response).filter_by(survey_id=survey.id, is_preview=False)
    for r in db.session.scalars(real.filter_by(pid=term)):
        found[r.id] = r
    # the stored identifiers alone, not every participation with all its answers
    stored = db.session.execute(db.select(Response.id, Response.params).filter(
        Response.survey_id == survey.id, Response.is_preview.is_(False), Response.params.is_not(None)))
    for rid in [rid for rid, params in stored if any(str(v) == term for v in (params or {}).values())]:
        found.setdefault(rid, db.session.get(Response, rid))
    entries = []
    if "@" in term:
        from app import contact

        contact.lock(survey.id)  # no bundle rewrites the entries between finding and deleting them (K11)
        wanted = term.casefold()
        for model in (ContactEntry, SplitSession):
            for e in db.session.scalars(db.select(model).filter_by(survey_id=survey.id)):
                values = list((e.answers or {}).values()) + [getattr(e, "email", "")]
                if any(isinstance(v, str) and v.strip().casefold() == wanted for v in values):
                    entries.append(e)
    return [found[k] for k in sorted(found)], entries


def counted(survey, response):
    """Whether kept results and the states of the dashboards may hold this participation. Every real one may: they
    were perhaps computed with an earlier data pool ("also unfinished ones" before the pool was narrowed), and the
    methods part and the counts by status take participations outside the pool too (screen-outs). So any deletion of
    a real participation drops them, whatever the pool is today (audit 2026-10-12, E-8, E-9); test and dummy data
    only when they are in today's pool (surveys.stored_pool). A script that starts and deletes in a loop is held by
    the limit on deletions (limits.allow_erase; S18 of the audit 2026-10-06)."""
    from app.surveys import stored_pool

    if response.source == "real":
        return True
    pool = stored_pool(survey)
    return response.source in pool["sources"] and (pool["all"] or response.status == Response.COMPLETED)


def erase(survey, responses, way, entries=(), reason=None):
    """Remove these participations (and entries of the separated section) for good and count them for the day.
    `reason`: what the deletion records of a seal chain say instead of "erasure_<way>" (the platform's reason,
    app/platform_erase.py).
    Quota places they held are free again; results and estimates they were part of are dropped. The quotas are
    counted anew only when a participation held a quota decision; the kept results go with every real
    participation (counted), as they may have been computed with an earlier pool (audit 2026-10-12, E-8)."""
    from app.timeutil import to_local
    from app.models import utcnow

    from app import diary

    responses, entries = list(responses), list(entries)
    if not responses and not entries:
        return 0
    # a person of a diary study goes with all entries: counted as what goes, not as what was named (audit
    # 2026-10-11, N6)
    responses = diary.whole_persons(responses)[0]
    pids, in_results = _remove(survey, responses, entries, reason or f"erasure_{way}"[:20])
    day = to_local(utcnow()).strftime("%Y-%m-%d")
    count(survey.id, day, way, len(responses), len(entries))
    db.session.commit()
    if responses:
        _after_commit(survey, pids, in_results)
    return len(responses) + len(entries)


RESTORE = "erasure_restore"  # the deletion record of a participation erase_again finds without one in the chain


def erase_again(survey, responses, entries=(), reason=RESTORE):
    """After a database was played back from a dump (deploy/docker/reapply_erasures.py, audit 2026-10-10, S4):
    participations and entries deleted since the dump go again by the same way as in erase (anchor examples of the
    coding guide, paper scans, quotas, saved states, kept results and estimates), but uncounted, as the script puts
    the deletion counters back as they were, and uncommitted: the script commits everything at once and then calls
    the function returned here for the files. The script has put the seal chain back before, deletion records
    included, so a deletion here writes a record (reason RESTORE) only for a case the chain still names.
    Also the way of diary.sweep (reason "diary_pending": persons who never finished the entry part), which is
    neither counted nor asked for (audit 2026-10-12, D-12)."""
    responses, entries = list(responses), list(entries)
    if not responses and not entries:
        return lambda: None
    pids, in_results = _remove(survey, responses, entries, reason)
    survey_id = survey.id

    def files():
        if responses:
            from app.models import Survey

            _after_commit(db.session.get(Survey, survey_id), pids, in_results)
    return files


def _remove(survey, responses, entries, reason):
    """The deletion itself, shared by erase and erase_again; neither counts nor commits. Returns (pids, in_results)
    for _after_commit."""
    from app import diary, quotas

    responses, persons = diary.whole_persons(responses)  # a person of a diary study goes with all entries
    from app import diary_access

    for person in persons:
        diary_access.lock_person(person)
        diary_access.revoke(person)
    entries = list(entries) + persons
    # with the person ids of a diary study: the rows "verpasst" of the saved states name only them (audit 2026-10-12, D-3)
    pids = {r.pid for r in responses if r.pid} | {p.pid for p in persons if p.pid}
    in_quotas = any(r.quota for r in responses)
    in_results = any(counted(survey, r) for r in responses)
    if in_results:
        from app.boards.compute import _pool_count
        from app.boards.base import MIN_CELL

        # Record this in the same transaction as the deletion, before caches are removed.
        # Never publish the before/after difference of an erased person's answers (TN-30).
        survey.data_pool = {**(survey.data_pool or {}), "_public_after_erase":
                            max((survey.data_pool or {}).get("_public_after_erase", 0), _pool_count(survey) + MIN_CELL)}
    if responses and survey.codings:  # anchor examples quoting them and the AI's proposals go (audit 2026-10-10, L-2)
        from app import coding

        coding.forget(survey, responses)
    from app import paper

    paper.forget_responses(survey, responses)  # the scans of paper sheets go too; the paper is to be destroyed
    for row in responses + entries:
        db.session.delete(row)
    from app import seal

    db.session.info[seal.REASON] = reason  # the deletion records of a seal chain name the way
    try:
        db.session.flush()
    finally:
        db.session.info.pop(seal.REASON, None)
    if in_quotas and survey.quotas:
        quotas.recount(survey)  # the counters follow what the remaining participations hold
    if responses:
        from app import cleanup

        cleanup.note(survey.uid, pids=pids)  # before the commit: the files follow even after a failure (L-8)
    return pids, in_results


def _after_commit(survey, pids, in_results):
    """The copies outside the database, once the deletion is committed."""
    from app import balance, cleanup

    balance.forget(survey.id)  # their assignments no longer count
    try:
        _forget_in_files(survey, pids, results=in_results)
    except OSError as e:  # the participation is gone from the database; the housekeeping tries again
        current_app.logger.error("Erasure in survey %s: files not cleaned: %s", survey.code, e)
    else:
        cleanup.done(survey.uid, pids=pids)


def _forget_in_files(survey, pids, results=True):
    """The copies outside the database: kept results (also the weighted ones and the group comparisons), states of
    the dashboards and HB estimates the participations are part of (removed, they are computed anew), and their
    lines in the saved states. Also for the runs of a live vote deleted by its presenter (app/live/).
    results=False: only test or dummy data outside today's pool went (counted), so those stay; the HB estimates and the saved states are looked through by their participation IDs all the same."""
    import json

    from app import results_store, snapshots

    from app import report_jobs

    from app import export_tables

    export_tables.forget(survey)
    report_jobs.forget(survey)  # exports may contain unfinished or excluded responses as well
    if results:
        results_store.forget_files(survey.uid)
    from app import interview_search

    interview_search.forget(survey.uid)  # the word counts of the interviews kept in memory (code review 2026-10-08, R5)
    base = os.path.join(current_app.config["UPLOAD_FOLDER"], "hb", survey.uid)  # see maxdiff_hb.folder, results_store
    from app import jobs

    # every running estimation has them in its input (HB, LC, the bootstraps of SEM and omega): stopped, writes nothing,
    # its job files with the answers go (review C3; code review 2026-10-08, S6)
    jobs.forget(base, prefix="")
    if os.path.isdir(base):
        for name in os.listdir(base):
            path = os.path.join(base, name)
            if name.startswith("board-") and name.endswith(".json"):
                if results:
                    os.remove(path)  # the states of the dashboards and their step (app/boards/) count them too
            elif results and name.startswith(jobs.BOOTSTRAP_FILES) and name.endswith(".json"):
                os.remove(path)  # a finished bootstrap of SEM or omega not yet taken over: computed anew
            elif name.startswith("item_") and name.endswith(".json") and not name.endswith(".run.json"):
                try:
                    with open(path, encoding="utf-8") as f:
                        known = set(json.load(f).get("pids") or [])
                except (OSError, ValueError):
                    known = pids  # unreadable: better estimated anew than kept with someone in it
                if known & pids:
                    os.remove(path)
    for name, _, _ in snapshots.listing(survey):
        folder = os.path.join(snapshots.folder(survey), name)
        numbers = _drop_rows(os.path.join(folder, "data.csv"), "pid", pids)
        # a diary study: the rows of prompts nobody answered carry only the person id (audit 2026-10-12, D-3)
        _drop_rows(os.path.join(folder, "data.csv"), "person", pids)
        if numbers:  # the long formats know a participation by its number in the data file
            for fname in os.listdir(folder):
                if fname != "data.csv" and snapshots.is_data_file(fname):
                    _drop_rows(os.path.join(folder, fname), "resp_id", numbers)


def _drop_rows(path, column, values):
    """Rewrite a saved CSV file without the rows whose `column` is one of `values`. Returns the resp_id of the
    rows that went. The other rows keep their numbers, so data file and long formats still fit together."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except OSError:
        return set()
    if not rows or column not in rows[0]:
        return set()
    at = rows[0].index(column)
    number = rows[0].index("resp_id") if "resp_id" in rows[0] else None
    gone = [r for r in rows[1:] if len(r) > at and r[at] in values]
    if not gone:
        return set()
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerows([rows[0]] + [r for r in rows[1:] if not (len(r) > at and r[at] in values)])
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
    os.replace(tmp, path)  # readers never see half a file
    return {r[number] for r in gone} if number is not None else set()


def count(survey_id, day, way, responses, contacts):
    """Add to the numbers of the day and way, counted by the database in one step: two deletions at the same moment
    both read n and wrote n + their own otherwise (code review 2026-10-07, D3). Two first deletions of a day at the
    same moment may write two rows; summary adds them up, and later ones count on the first."""
    first = db.session.scalar(db.select(db.func.min(Erasure.id)).filter_by(survey_id=survey_id, day=day, way=way))
    if first is None:
        db.session.add(Erasure(survey_id=survey_id, day=day, way=way, responses=responses, contacts=contacts))
        return
    db.session.execute(db.update(Erasure).where(Erasure.id == first)
                       .values(responses=Erasure.responses + responses, contacts=Erasure.contacts + contacts)
                       .execution_options(synchronize_session=False))


def summary(survey):
    """What was deleted so far: {"responses", "contacts", "last": newest day or None, "ways": {way: n}}."""
    rows = list(survey.erasures)
    ways = {}
    for r in rows:
        ways[r.way] = ways.get(r.way, 0) + r.responses + r.contacts
    return {"responses": sum(r.responses for r in rows), "contacts": sum(r.contacts for r in rows),
            "last": max((r.day for r in rows), default=None), "ways": ways}
