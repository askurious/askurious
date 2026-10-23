"""Retention periods: how long a survey keeps what it collected, counted from the end of its fieldwork.

Two periods per survey (Survey.retention, picked from lists, no free input):
  personal   what can point to a person: the identifiers stored from the link (Response.params, their hashes in
             OnceValue) and the entries of the separated section (e-mail addresses for a raffle). After the period
             the identifiers are emptied and the entries deleted; the answers stay, pseudonymous, not anonymous: the
             random participation ID (pid) stays with them, and where the survey passed it on ({{pid}} in a completion
             link or to an external section), the data there can still be linked to them (audit 2026-10-21, B6)
  answers    everything collected: all participations, the entries of the separated section, what a live vote
             collected besides them (questions from the audience, "unclear" taps, reactions), the data files of
             the saved states, kept results and hierarchical Bayes estimates. The questionnaire stays

The clock starts when the survey is closed (not paused) and stops while it is open again; a survey that is
reopened and closed later counts from the later end. A live vote is never closed (app/live/): its clock starts
with its last activity, the latest participation, question from the audience, reaction or "unclear" tap, and starts
anew with every later one (concept "Befragungstypen im Vergleich" 2026-10-17, T14, variant A). New surveys start with PERSONAL_DEFAULT and
ANSWERS_DEFAULT; surveys from before the periods existed have none, and nothing of theirs is ever deleted by a
period until someone sets one. Once real participations exist a period can only become shorter: the consent
named it, and people agreed to that and no more.

The periods are carried out by the same housekeeping that closes surveys (lifecycle.close_due_surveys: lazily
with requests, and by /cron/close). What went is counted like a deletion on request (models.Erasure, way
"retention"), as bare numbers per day.
"""
import os

from flask import current_app

from app.extensions import db, json_given
from app.lang import de_en
from app.models import ContactEntry, OnceValue, Response, SplitSession, Survey, keep_floor_before_bulk_delete, utcnow

# months; None = no period
PERSONAL = ((None, "nicht automatisch"), (1, "1 Monat"), (3, "3 Monate"), (6, "6 Monate"), (12, "1 Jahr"), (24, "2 Jahre"))
ANSWERS = ((None, "nicht automatisch"), (12, "1 Jahr"), (24, "2 Jahre"), (36, "3 Jahre"), (60, "5 Jahre"), (120, "10 Jahre"))
PERSONAL_DEFAULT, ANSWERS_DEFAULT = 6, 120
# the labels of PERSONAL and ANSWERS in English (researcher pages in English, app/lang.py)
LABELS_EN = {None: "not automatically", 1: "1 month", 3: "3 months", 6: "6 months", 12: "1 year", 24: "2 years",
             36: "3 years", 60: "5 years", 120: "10 years"}


def _local(choices):
    from app.lang import english

    return tuple((months, LABELS_EN[months]) for months, _ in choices) if english() else choices


def personal():
    """PERSONAL in the language of the page."""
    return _local(PERSONAL)


def answers():
    """ANSWERS in the language of the page."""
    return _local(ANSWERS)
NOTICE_DAYS = 30  # the Export page warns this long before a period ends
# why ten years: the usual period for keeping research data; the platform is no archive, so it is the longest offered
DFG = ("Deutsche Forschungsgemeinschaft. (2019). Leitlinien zur Sicherung guter wissenschaftlicher Praxis: Kodex "
       "(Leitlinie 17: Archivierung). https://doi.org/10.5281/zenodo.3923602")
GDPR = ("Verordnung (EU) 2016/679 des Europäischen Parlaments und des Rates vom 27. April 2016 (Datenschutz-"
        "Grundverordnung), Art. 5 Abs. 1 Buchst. e (Speicherbegrenzung), Art. 7 Abs. 3 (Widerruf der Einwilligung), "
        "Art. 17 (Recht auf Löschung) und Art. 89 (Verarbeitung zu wissenschaftlichen Forschungszwecken). "
        "Amtsblatt der Europäischen Union, L 119, 1–88.")
# the same source on an English page (apa._english; UI/UX audit 2026-10-09, X86)
GDPR_EN = ("Regulation (EU) 2016/679 of the European Parliament and of the Council of 27 April 2016 (General Data "
           "Protection Regulation), Art. 5(1)(e), 7(3), 17 and 89. Official Journal of the European Union, L 119, 1–88.")


def default():
    return {"personal": PERSONAL_DEFAULT, "answers": ANSWERS_DEFAULT}


def of(survey):
    """{"personal": months or None, "answers": months or None}, whatever is stored."""
    stored = getattr(survey, "retention", None) or {}
    allowed = {"personal": {m for m, _ in PERSONAL}, "answers": {m for m, _ in ANSWERS}}
    return {key: stored.get(key) if stored.get(key) in allowed[key] else None for key in ("personal", "answers")}


def label(key, months):
    return dict(personal() if key == "personal" else answers()).get(months, dict(personal())[None])


# the periods of PERSONAL and ANSWERS in the words of a consent text (German, English; the catalog has the others)
PERIOD_WORDS = {1: ("1 Monat", "1 month"), 3: ("3 Monate", "3 months"), 6: ("6 Monate", "6 months"),
                12: ("1 Jahr", "1 year"), 24: ("2 Jahre", "2 years"), 36: ("3 Jahre", "3 years"),
                60: ("5 Jahre", "5 years"), 120: ("10 Jahre", "10 years")}


def words(months, lang):
    """The period in the words of a consent text, in `lang`."""
    from app.ui_i18n import tr

    return tr(lang, *PERIOD_WORDS[months])


def locked(survey):
    """Real participations exist: people agreed to the periods the consent named, so they can only get shorter."""
    return bool(survey.id) and survey.has_real_responses()


def _wanted(survey, form):
    """(new periods, names of periods left as they are because they would have become longer) for a settings form."""
    before, refused, new = of(survey), [], {}
    fixed = locked(survey)
    for key, choices in (("personal", PERSONAL), ("answers", ANSWERS)):
        raw = form.get(f"retention_{key}", "")
        wanted = int(raw) if raw.isdigit() and int(raw) in {m for m, _ in choices} else None
        longer = before[key] is not None and (wanted is None or wanted > before[key])
        if fixed and longer:
            refused.append(key)
            wanted = before[key]
        new[key] = wanted
    return new, refused


def out_of_order(periods):
    """The period "personal" would outlast the period "answers" (longer, or not automatic while "answers" is set):
    the invitation list and the identifiers would then be kept past the deletion of everything else (audit
    2026-10-10, L-4)."""
    return periods["answers"] is not None and (periods["personal"] is None or periods["personal"] > periods["answers"])


def order_error(survey, form):
    """Why the periods a settings form asks for may not be saved, or None: "personal" longer than "answers". A
    survey that already has such periods (from before the check) keeps them as long as the form leaves them."""
    if "retention_shown" not in form:
        return None
    new, _ = _wanted(survey, form)
    if new == of(survey) or not out_of_order(new):
        return None
    return de_en("Die Frist „Kennungen und Kontaktangaben löschen“ darf nicht länger sein als „Alle Teilnahmen "
                 "löschen“ und nicht „nicht automatisch“, solange die zweite Frist gesetzt ist.",
                 "The period “Delete identifiers and contact details” may not be longer than “Delete all "
                 "participations”, nor “not automatically” while the second period is set.")


def from_form(survey, form):
    """The periods a settings form asks for, as far as they may change. Returns the names of periods that were
    left as they are because they would have become longer."""
    new, refused = _wanted(survey, form)
    survey.retention = new if any(new.values()) else None
    return refused


def _add_months(start, months):
    year, month = divmod(start.month - 1 + months, 12)
    year, month = start.year + year, month + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return start.replace(year=year, month=month, day=min(start.day, last))


def last_activity(survey):
    """The latest moment a live vote collected something: a participation began or ended, a question from the
    audience, a reaction or an "unclear" tap came in. None while it has collected nothing."""
    from app.models import LiveFeedback, LiveQuestion, LiveReaction

    found = [db.session.scalar(db.select(db.func.max(column)).where(model.survey_id == survey.id))
             for model, column in ((Response, Response.started_at), (Response, Response.completed_at),
                                   (LiveQuestion, LiveQuestion.created_at), (LiveReaction, LiveReaction.created_at),
                                   (LiveFeedback, LiveFeedback.created_at))]
    found = [when for when in found if when is not None]
    return max(found) if found else None


def ended(survey):
    """When the fieldwork ended for good, or None while the survey is a draft, running, paused or open for
    editing: the periods count from here. A live vote, which is never closed, counts from its last activity."""
    if getattr(survey, "live", None) is not None:
        return last_activity(survey)
    if survey.status != Survey.CLOSED or survey.close_reason == "paused" or survey.closed_at is None:
        return None
    return survey.closed_at


def due(survey):
    """{"personal": datetime or None, "answers": datetime or None}: when each period ends (UTC)."""
    start, periods = ended(survey), of(survey)
    return {key: _add_months(start, months) if start is not None and months else None for key, months in periods.items()}


def _with_identifiers(survey):
    """The ids of the participations that hold an identifier from the link. A participation without one stores
    an empty value there (JSON null or {}), which the database does not take for "nothing": those are left out by
    their text already (both databases keep JSON as written), and the values are looked at here once more."""
    from sqlalchemy import String, cast

    rows = db.session.execute(db.select(Response.id, Response.params).filter(
        Response.survey_id == survey.id, Response.params.is_not(None),
        cast(Response.params, String).not_in(("null", "{}"))))
    return [rid for rid, params in rows if params]


def holds(survey):
    """What is there for each period to delete: {"personal": n identifiers and entries, "answers": n rows}."""
    entries = sum(db.session.scalar(db.select(db.func.count()).select_from(model).filter_by(survey_id=survey.id))
                  for model in (ContactEntry, SplitSession))
    ids = len(_with_identifiers(survey))
    addresses = _addresses(survey)  # the address list for invitation mails (audit 2026-10-10, L-4)
    from app.items import geolocation

    positions = geolocation.held(survey)  # exact positions of the location element (items/geolocation.py)
    rows = db.session.scalar(db.select(db.func.count(Response.id)).filter(Response.survey_id == survey.id))
    return {"personal": ids + entries + positions + addresses, "answers": rows + entries + addresses}


def _addresses(survey):
    from app.models import SurveyInvite

    return db.session.scalar(db.select(db.func.count(SurveyInvite.id)).filter_by(survey_id=survey.id)) or 0


def view(survey, now=None):
    """For the Export page: [{"key", "label", "due", "soon", "holds"}] of the periods that are set."""
    now, dates, there = now or utcnow(), due(survey), holds(survey)
    out = []
    for key, months in of(survey).items():
        if months:
            when = dates[key]
            out.append({"key": key, "label": label(key, months), "due": when, "holds": there[key],
                        "soon": when is not None and there[key] > 0 and (when - now).days < NOTICE_DAYS})
    return out


def _log(survey, responses, contacts):
    from app.timeutil import to_local

    if not responses and not contacts:
        return
    day = to_local(utcnow()).strftime("%Y-%m-%d")
    from app import erasure

    erasure.count(survey.id, day, "retention", responses, contacts)


def _drop_entries(survey):
    from app import contact

    contact.lock(survey.id)  # after a running bundle, which writes them anew (contact.bundle)
    n = 0
    for model in (ContactEntry, SplitSession):
        n += db.session.execute(db.delete(model).where(model.survey_id == survey.id)).rowcount
    return n


DONE = "personal_done"  # in Survey.retention: the end of fieldwork (closed_at) whose period "personal" was carried out


def _done_for(survey):
    """The period "personal" was carried out for this end of the fieldwork: nothing to do until the survey is
    opened again and closed later (another closed_at). Without the note every closed survey was anonymised anew
    once a minute for years (code review 2026-10-07, K3, D2)."""
    stored, end = getattr(survey, "retention", None) or {}, ended(survey)
    return end is not None and stored.get(DONE) == end.isoformat()


def _note_done(survey):
    """Note in Survey.retention that the period "personal" is carried out for this end of the fieldwork. Past the
    session, so Survey.updated_at stays: the survey does not move up on the start page."""
    value = {**(survey.retention or {}), DONE: ended(survey).isoformat()}
    db.session.execute(db.update(Survey).where(Survey.id == survey.id).values(retention=value,
                                                                              updated_at=Survey.updated_at)
                       .execution_options(synchronize_session=False))
    from sqlalchemy.orm.attributes import set_committed_value

    set_committed_value(survey, "retention", value)


def anonymise(survey):
    """The period "personal" is over: empty the stored identifiers, delete the entries of the separated section,
    and take the identifiers out of the saved states. Returns how much went. The participation ID (pid) stays: the
    answers are pseudonymous from then on, not anonymous, as a pid passed on to a panel or web app still links them
    (audit 2026-10-21, B6)."""
    from sqlalchemy import null

    table = Response.__table__  # not through the rows: their version stays, and nobody is answering any more
    ids = _with_identifiers(survey)
    for start in range(0, len(ids), 500):
        db.session.execute(db.update(table).where(table.c.id.in_(ids[start:start + 500])).values(params=null()))
    # the hashes of the identifiers go with them (past the session, so not by the mapper events of app/models.py)
    db.session.execute(db.delete(OnceValue).where(OnceValue.survey_id == survey.id))
    entries = _drop_entries(survey) + _addresses(survey)
    from app import survey_invites

    survey_invites.forget(survey)  # the address list for invitation mails: contact data
    _log(survey, 0, entries)
    from app import paper

    paper.forget_survey_scans(survey)  # handwriting on scanned paper sheets may point to a person
    from app.items import geolocation

    positions = geolocation.coarsen(survey)  # exact positions keep only their postal code and federal state
    if positions:
        from app import seal

        seal.amend_changed(survey, "retention")  # the seal chain records the changed answers (app/seal.py)
    if ids or positions:
        _files(survey, blank_ids=True)
    return len(ids) + entries + positions


def purge(survey):
    """The period "answers" is over: delete every participation and entry, and the files that hold answers."""
    from app import balance, quotas

    real = db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=survey.id, source="real"))
    db.session.execute(db.delete(OnceValue).where(OnceValue.survey_id == survey.id))  # past the mapper events too
    from app import seal

    seal.purged(survey)  # past the session as well: one deletion record for all cases (app/seal.py)
    from app import coding

    coding.forget_all(survey)  # anchor examples quoting the answers and the AI's proposals (audit 2026-10-10, L-2)
    from app import paper

    paper.forget_survey_scans(survey)  # scans of paper sheets hold answers
    paper.forget_purged(survey)  # the sheets no longer name participations that are gone (audit 2026-10-21, B1)
    keep_floor_before_bulk_delete(Response, Response.survey_id == survey.id)  # SQLite gives no id twice (B1)
    rows = db.session.execute(db.delete(Response.__table__).where(Response.__table__.c.survey_id == survey.id)).rowcount
    from app import diary

    diary.forget(survey.id)  # the persons of a diary study go with their entries
    from app import ai_chat

    ai_chat.forget_survey(survey.id)  # the messages of the KI-Chat go with the participations (the access stays)
    entries = _drop_entries(survey) + _addresses(survey)
    from app import survey_invites

    # the address list for invitation mails and the mails still waiting go with everything else, also when the
    # period "personal" is longer or not automatic (audit 2026-10-10, L-4)
    survey_invites.forget(survey)
    live = _drop_live(survey)
    if rows and survey.quotas:
        db.session.flush()
        quotas.recount(survey)
    _log(survey, real, entries)
    if rows:
        balance.forget(survey.id)
        _files(survey, blank_ids=False)
    return rows + entries + live


def _drop_live(survey):
    """What a live vote collected besides the participations: the questions from the audience, the "unclear"
    signals, the reactions and the browser receipts of its runs (as deleting a run does, live/control.py). They stay
    no longer than the answers (code review 2026-10-15, D2)."""
    from app.models import BrowserReceipt, LiveFeedback, LiveQuestion, LiveReaction

    gone = sum(db.session.execute(db.delete(model).where(model.survey_id == survey.id)).rowcount or 0
               for model in (LiveQuestion, LiveFeedback, LiveReaction))
    return gone + (db.session.execute(db.delete(BrowserReceipt).where(
        BrowserReceipt.survey_id == survey.id, BrowserReceipt.scope.like("live:%"))).rowcount or 0)


def _files(survey, blank_ids):
    """The copies outside the database. blank_ids: only the identifier columns of the saved data files go
    (url_...); else every file that holds answers. The folders are changed once the deletion is committed."""
    from app import snapshots

    from app import cleanup

    snapshots.listing(survey)  # snapshots from before they had a folder per survey move to it first
    db.session.info.setdefault(_PENDING, []).append((survey.uid, blank_ids))
    cleanup.note(survey.uid, period="personal" if blank_ids else "answers")  # kept until clean (audit L-8)


_PENDING = "retention_files"


def _clean_files(uid, blank_ids):
    import csv
    import io

    from app import report_jobs, results_store

    report_jobs.forget_uid(uid)  # cached files also contain the personal columns being anonymised
    if not blank_ids:
        from app.items import voice

        voice.remove_survey(uid)  # the voice messages are answers
        # recordings of interviews (app/interview_audio.py; guides have no such period yet, but a folder is answers)
        import shutil

        shutil.rmtree(os.path.join(os.path.dirname(current_app.config["UPLOAD_FOLDER"]), "interview-audio", uid), ignore_errors=True)
    results_store.forget_files(uid)  # the kept results, weighted ones and group comparisons (audit L-1)
    base = current_app.config["UPLOAD_FOLDER"]
    hb = os.path.join(base, "hb", uid)
    from app import interview_search, jobs

    if not blank_ids:
        interview_search.forget(uid)  # the word counts of the interviews kept in memory (code review 2026-10-08, R5)
        # every running estimation (HB, LC, the bootstraps of SEM and omega) stops and writes nothing, also on the
        # Rechenknecht, and its job files with the answers go (review C3; code review 2026-10-08, S6)
        jobs.forget(hb, prefix="")
    if os.path.isdir(hb):
        for name in os.listdir(hb):
            # the kept states of the dashboards (frequencies, word lists; app/boards/) are made anew when looked at
            board = name.startswith("board-") and name.endswith(".json")
            if board or (not blank_ids and name.startswith(jobs.ANSWER_FILES)):
                os.remove(os.path.join(hb, name))
    from app import snapshots

    snaps = os.path.join(base, "snapshots", uid)
    if not os.path.isdir(snaps):
        return
    from app.items import geolocation

    survey = db.session.scalar(db.select(Survey).filter_by(uid=uid)) if blank_ids else None
    positions = geolocation.position_columns(survey) if survey is not None else set()  # loops: no location element
    for folder in os.listdir(snaps):
        for name in os.listdir(os.path.join(snaps, folder)):
            path = os.path.join(snaps, folder, name)
            if not blank_ids:
                if snapshots.is_data_file(name):
                    os.remove(path)
                continue
            if name != "data.csv":
                continue
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            cols = [i for i, c in enumerate(rows[0] if rows else []) if c.startswith("url_") or c in positions]
            if not cols:
                continue
            for row in rows[1:]:
                for i in cols:
                    if i < len(row):
                        row[i] = ""
            buf = io.StringIO()
            buf.write("﻿")
            csv.writer(buf, lineterminator="\r\n").writerows(rows)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(buf.getvalue())
            os.replace(tmp, path)


def commit_files():
    """Call after the commit of anonymise/purge: now the files follow. A failure is logged and stays noted
    (app/cleanup.py): the next run of the housekeeping tries again."""
    from app import cleanup

    for uid, blank_ids in db.session.info.pop(_PENDING, []):
        try:
            _clean_files(uid, blank_ids)
        except OSError as e:
            current_app.logger.error("Retention: files of survey folder %s not cleaned: %s", uid, e)
        else:
            cleanup.done(uid, period="personal" if blank_ids else "answers")


def _files_left(uid):
    """True if files with answers are still there although the survey holds none: data files and long formats of
    the saved states, kept results, states of the dashboards, HB estimates (audit 2026-10-05, L-8)."""
    base = current_app.config["UPLOAD_FOLDER"]
    hb = os.path.join(base, "hb", uid)
    if os.path.isdir(hb) and any(n.endswith(".bin") or n.startswith(("board-", "item_")) for n in os.listdir(hb)):
        return True
    from app import snapshots

    snaps = os.path.join(base, "snapshots", uid)
    if not os.path.isdir(snaps):
        return False
    for folder in os.listdir(snaps):
        inside = os.path.join(snaps, folder)
        if os.path.isdir(inside) and any(snapshots.is_data_file(n) for n in os.listdir(inside)):
            return True
    return False


def _left(survey):
    """Anything purge deletes still stored for the survey: participations, the hashes of identifiers, entries of the
    separated section, the address list of the invitations, what a live vote collected (_drop_live)."""
    from app.models import BrowserReceipt, Enrolment, LiveFeedback, LiveQuestion, LiveReaction, SurveyInvite

    return any(db.session.scalar(db.select(model.id).where(model.survey_id == survey.id).limit(1)) is not None
               for model in (Response, OnceValue, ContactEntry, SplitSession, SurveyInvite, Enrolment,
                             LiveQuestion, LiveFeedback, LiveReaction))         or db.session.scalar(db.select(BrowserReceipt.key).where(BrowserReceipt.survey_id == survey.id,
                                                                 BrowserReceipt.scope.like("live:%")).limit(1)) is not None


def run(now=None):
    """Carry out the periods that are over, and the file cleanups still owed (app/cleanup.py). Returns how many
    surveys lost something.

    Each survey is committed on its own: an error in one (a broken quota, a file that cannot be read, the seal
    chain) is logged and reported, that survey is left as it was, and the others still lose what their periods
    say. Before, one survey failing every time rolled back the deletions of all others in every round (audit
    2026-10-18, FUNC-02). A busy database still puts off the whole round (lifecycle._transient)."""
    from app import cleanup

    from sqlalchemy import String, cast
    from sqlalchemy.orm import load_only

    now, touched = now or utcnow(), 0
    cleanup.retry(now)
    # only the columns the periods need (the rest loads when a purge reads it), and without the surveys whose
    # periods are JSON null, which is not SQL NULL (code review 2026-10-15, E5)
    # a live vote is never closed: its periods count from its last activity (ended)
    query = db.select(Survey).options(load_only(Survey.id, Survey.uid, Survey.status, Survey.close_reason,
                                                Survey.closed_at, Survey.retention, Survey.live)).filter(
        db.or_(Survey.status == Survey.CLOSED, json_given(Survey.live)), Survey.retention.is_not(None),
        cast(Survey.retention, String) != "null")
    # id and uid read now: after the rollback of a failed survey every object is expired
    surveys = [(s, s.id, s.uid) for s in db.session.scalars(query).all()]
    for survey, sid, uid in surveys:
        mark = len(db.session.info.setdefault(_PENDING, []))
        try:
            gone, changed = _run_one(survey, now)
            if changed:
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            del db.session.info.setdefault(_PENDING, [])[mark:]  # its files stay: its rows stayed too
            from app import lifecycle

            if lifecycle._transient(e):
                raise
            _failed(sid, uid, e)
            continue
        touched += bool(gone)
        if db.session.info.get(_PENDING):
            commit_files()
    return touched


def _run_one(survey, now):
    """The periods of one survey: (how much went, whether anything is to be committed)."""
    dates, gone, noted = due(survey), 0, False
    if dates["answers"] is not None and dates["answers"] <= now:
        if _answers_done(survey, now):  # all gone at this end of the fieldwork, checked today already (E5)
            return 0, False
        if _left(survey):  # not once a minute for ever once all is gone (audit 2026-10-10, L-5)
            gone = purge(survey)
        if not gone and _files_left(survey.uid):  # nothing in the database, but the saved states still hold it all
            _files(survey, blank_ids=False)
        elif not gone:
            _note_answers_done(survey, now)
            noted = True
    elif dates["personal"] is not None and dates["personal"] <= now and not _done_for(survey):
        gone = anonymise(survey)
        _note_done(survey)
        noted = True
    return gone, bool(gone) or noted


_REPORTED = {}  # survey uid -> the day its failure was last sent to Slack (per process: once a day is enough)


def _failed(survey_id, uid, error):
    """A survey whose periods could not be carried out: in the log every round, in Slack once a day."""
    current_app.logger.error("Retention: periods of survey %s not carried out, the others go on", survey_id,
                             exc_info=error)
    today = utcnow().date()
    if _REPORTED.get(uid) == today:
        return
    _REPORTED[uid] = today
    try:  # as a failed planned start (lifecycle._start_failed): a line for the operators, if Slack is configured
        from app import notify

        notify.slack(f":warning: Löschfristen der Befragung Nr. {survey_id} nicht ausgeführt "
                     f"({type(error).__name__}); die anderen Befragungen laufen weiter. Details im Log.")
    except Exception as e:  # noqa: BLE001 - the report must not stop the housekeeping
        current_app.logger.warning("Failed retention of survey %s not reported in Slack: %s", survey_id,
                                   type(e).__name__)


ANSWERS_DONE = "answers_done"  # in Survey.retention: "<closed_at> <day>", the day all answers were found gone


def _answers_done(survey, now):
    """The period "answers" was carried out for this end of the fieldwork and nothing was left on this day (UTC):
    once a day is enough to catch what came in after it (a paper questionnaire typed in later), instead of six
    queries and a look at the folders every minute for every survey past its period (code review 2026-10-15, E5)."""
    stored, end = (getattr(survey, "retention", None) or {}).get(ANSWERS_DONE), ended(survey)
    return end is not None and stored == f"{end.isoformat()} {now.date().isoformat()}"


def _note_answers_done(survey, now):
    """As _note_done, past the session: Survey.updated_at stays."""
    value = {**(survey.retention or {}), ANSWERS_DONE: f"{ended(survey).isoformat()} {now.date().isoformat()}"}
    db.session.execute(db.update(Survey).where(Survey.id == survey.id).values(retention=value,
                                                                              updated_at=Survey.updated_at)
                       .execution_options(synchronize_session=False))
    from sqlalchemy.orm.attributes import set_committed_value

    set_committed_value(survey, "retention", value)


def consent_sentence(survey, lang, stored_ids, separated):
    """What the consent says about the periods, in `lang`; "" if none is set."""
    from app.items import geolocation
    from app.ui_i18n import tr

    periods, out = of(survey), []
    if periods["personal"] and geolocation.personal_items(survey):
        out.append(tr(lang, "Genaue Positionen werden {span} nach dem Ende der Befragung auf Postleitzahl und Bundesland "
                      "gekürzt.", "Exact positions are cut down to the postal code and federal state {span} after the "
                      "end of the survey.", span=words(periods["personal"], lang)))
    if periods["personal"] and (stored_ids or separated):
        span = words(periods["personal"], lang)
        if stored_ids and separated:
            out.append(tr(lang, "Ihre Kennung aus dem Aufruf-Link und die getrennt gespeicherten Angaben werden {span} "
                          "nach dem Ende der Befragung gelöscht.", "Your identifier from the link and the details stored "
                          "separately are deleted {span} after the end of the survey.", span=span))
        elif stored_ids:
            out.append(tr(lang, "Ihre Kennung aus dem Aufruf-Link wird {span} nach dem Ende der Befragung gelöscht.",
                          "Your identifier from the link is deleted {span} after the end of the survey.", span=span))
        else:
            out.append(tr(lang, "Die getrennt gespeicherten Angaben werden {span} nach dem Ende der Befragung gelöscht.",
                          "The details stored separately are deleted {span} after the end of the survey.", span=span))
    if periods["answers"]:
        out.append(tr(lang, "Auf dieser Plattform werden alle Antworten {span} nach dem Ende der Befragung gelöscht.",
                      "On this platform all answers are deleted {span} after the end of the survey.",
                      span=words(periods["answers"], lang)))
    return " ".join(out)
