"""Survey state transitions and automatic closing.

netcup web hosting has no background workers, so closing is checked lazily on requests
(throttled) and additionally via /cron/close for a Plesk scheduled task.
"""
import time
from datetime import timedelta

from flask import current_app
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.lang import de_en
from app.models import Survey, utcnow

DEFAULT_FIELD_DAYS = 30
_DOOMED = "folders_to_remove"  # session.info: folders of deleted surveys, removed after the commit
_CHECK_INTERVAL = 60  # seconds between lazy checks per worker process
_last_check = 0.0
_SWEEP_INTERVAL = 3600  # seconds between sweeps of old database copies per worker process
_last_sweep = None
# seconds between the rarer sweeps (sign-in sessions, access log, comment images, interviews) per worker process:
# each sends a DELETE or reads a folder, which on SQLite took the write lock every minute (code review 2026-10-07, K2)
_RARE_INTERVAL = 3600
_last_rare = None


class LifecycleError(ValueError):
    pass


# the largest sample size (as for a survey file, transfer.py)
MAX_RESPONSES = 10_000_000


def check_start(survey, field_end, max_responses, at=None):
    """What has to hold for a start, now or at the planned moment `at` ("Start planen"): raises LifecycleError with
    the reason. The same checks for both, so a planned start is refused for what would stop the button."""
    if survey.status == Survey.RUNNING:
        raise LifecycleError(de_en("Die Befragung läuft bereits.", "The survey is already running."))
    if survey.archived:  # code review 2026-10-15, H4
        raise LifecycleError(de_en("Die Befragung ist archiviert. Stellen Sie sie zuerst wieder her.",
                                   "The survey is archived. Restore it first."))
    # neither an end date nor a sample size: the survey runs until it is paused or closed
    if field_end is not None and field_end <= (at or utcnow()):
        raise LifecycleError(de_en("Das Feldende muss nach dem geplanten Start liegen.", "The end of fieldwork must lie after the planned start.")
                             if at else de_en("Das Feldende muss in der Zukunft liegen.", "The end of fieldwork must lie in the future."))
    if max_responses is not None and max_responses < 1:
        raise LifecycleError(de_en("Die maximale Stichprobengröße muss mindestens 1 sein.", "The maximum sample size must be at least 1."))
    if max_responses is not None and max_responses > MAX_RESPONSES:  # the column is a 32-bit integer in PostgreSQL
        raise LifecycleError(de_en("Die maximale Stichprobengröße darf höchstens 10.000.000 sein.", "The maximum sample size may be at most 10,000,000."))
    if max_responses and sample_full(survey, max_responses):
        raise LifecycleError(de_en("Die maximale Stichprobengröße ist bereits erreicht.", "The maximum sample size has already been reached."))
    if survey.status == Survey.DRAFT:
        from app import design

        errors = design.blocking(design.check(survey, start=True))
        if errors:
            raise LifecycleError(
                de_en("Der Fragebogen ist noch nicht startklar: ", "The questionnaire is not ready to start yet: ")
                + "; ".join(f.message for f in errors[:3])
                + (de_en(" (und {n} weitere)", " (and {n} more)", n=len(errors) - 3) if len(errors) > 3 else "")
            )


def start(survey, field_end, max_responses):
    check_start(survey, field_end, max_responses)
    survey.start_at = None  # a planned start is done with, as is the note of one that did not go through
    survey.start_plan = None
    survey.field_end = field_end
    survey.max_responses = max_responses or None
    if survey.status == Survey.DRAFT:  # first start of this questionnaire version: freeze it (app/prereg.py)
        from app import prereg

        # before the first write of the start, and without flushing the new field end: on SQLite the write lock is
        # taken by the first statement that writes, and every other writer waited while freeze encoded the images,
        # built the Word file and packed the ZIP (code review 2026-10-07, E4)
        with db.session.no_autoflush:
            prereg.freeze(survey, pending=True)  # published once the start is committed (code review 2026-10-15, D6)
    from app import undo

    undo.forget(survey)  # the structure is fixed from now on: no change to the draft can be taken back
    if survey.status == Survey.DRAFT:
        # the pool of a draft holds its tests and dummies; from the start on the results are about real people
        survey.data_pool = {**(survey.data_pool or {}), "sources": ["real"]}
        consent = survey.consent_item
        if consent is not None:  # what participants confirm stays as at the start (items/consent.py, L8 L9)
            consent.kind.freeze_confirmations(consent)
    survey.status = Survey.RUNNING
    survey.archived = False
    survey.opened_at = survey.opened_at or utcnow()
    survey.closed_at = None
    survey.close_reason = ""
    _log(survey, "start")
    from app import seal

    seal.field_event(survey, "fieldstarted")  # an entry in the seal chain, stamped by the DFN at once (app/seal.py)


def start_committed(survey, field_end, max_responses):
    """The whole start, by the button and by a planned start alike: start(), commit, then the optional automatic
    upload of the new pre-registration to Coscine (which never stops the start). Returns (frozen, coscine): whether a
    pre-registration was frozen, and the outcome of the upload ((ok, message) or None)."""
    from app import prereg

    frozen = len(prereg.listing(survey))
    start(survey, field_end, max_responses)
    db.session.commit()
    new = prereg.listing(survey)
    done = None
    if len(new) > frozen:
        from app import coscine

        done = coscine.auto_store(survey, f"prereg:{new[0][0]}")
    return len(new) > frozen, done


# the latest a start can be planned ahead (a typo in the year should not keep a draft waiting for decades)
PLAN_DAYS = 366


def plan_start(survey, at, field_end, max_responses, by=None):
    """"Start planen": the survey starts by itself at `at` (naive UTC) with these end criteria. The checks of the
    start hold now already (check_start), so a planned start with errors in the pre-launch check is refused with the
    same messages; at the moment itself they are made again (start_due_surveys)."""
    now = utcnow()
    if at <= now:
        raise LifecycleError(de_en("Der geplante Start muss in der Zukunft liegen.", "The planned start must lie in the future."))
    if at > now + timedelta(days=PLAN_DAYS):
        raise LifecycleError(de_en("Der Start lässt sich höchstens ein Jahr im Voraus planen.",
                                   "The start can be planned at most one year ahead."))
    check_start(survey, field_end, max_responses, at=at)
    from app import lang

    survey.start_at = at
    survey.start_plan = {"field_end": field_end.isoformat() if field_end else None, "max_responses": max_responses or None,
                         "lang": lang.current(), "by": getattr(by, "id", None)}


def cancel_plan(survey):
    """No planned start any more; also removes the note of one that did not go through."""
    survey.start_at = None
    survey.start_plan = None


def planned_failure(survey):
    """The note of a planned start that did not go through: {"failed": reason, "planned": datetime or None}, or None."""
    plan = survey.start_plan or {}
    if not plan.get("failed"):
        return None
    return {"failed": plan["failed"], "planned": _parse_utc(plan.get("planned"))}


def planned_end(survey):
    """The end criteria a planned start goes with: (field_end or None, max_responses or None)."""
    plan = survey.start_plan or {}
    return _parse_utc(plan.get("field_end")), plan.get("max_responses")


class _PageLanguage:
    """The researchers' language for what a planned start writes (the reason of a refusal, the readable texts of the
    pre-registration): that of the person who planned it, not that of whoever's request runs the housekeeping."""

    def __init__(self, lang):
        self.lang = lang

    def __enter__(self):
        from flask import g, has_request_context

        self.active = has_request_context() and self.lang in ("de", "en")
        if self.active:
            self.before = g.get("ui_lang")
            g.ui_lang = self.lang

    def __exit__(self, *exc):
        if self.active:
            from flask import g

            if self.before is None:
                g.pop("ui_lang", None)
            else:
                g.ui_lang = self.before


START_TRIES = 5  # a planned start that fails with an error of the code (not a refusal, not a busy database) this often
START_LEASE = timedelta(minutes=15)  # how long a claimed start may take before another round takes it up again


def start_due_surveys(now=None, survey_id=None):
    """Start the surveys whose planned start has come (housekeeping, about once a minute, and /cron/close), by the same
    path as the button (start_committed): pre-registration, seal chain, Coscine. Each planned start is first claimed
    by one statement that moves start_at START_LEASE ahead (a lease) and notes the planned moment in the plan, so two
    processes doing the housekeeping at once never start one survey twice. Should the process end during the start
    (a worker killed at its time limit, a deploy, no memory left), the lease runs out and a later round takes the
    start up again, counted as a try; before, the claim emptied start_at and such a start was lost without a word
    (audit 2026-10-18, FUNC-03). If a check fails now (something changed since it was planned), the survey stays as
    it is and keeps a note for its researchers on the page "Datenerhebung" (and in Slack, if it is configured). An
    error of the code puts it off to the next round, at most START_TRIES times; then it counts as failed (audit
    2026-10-10, T3). survey_id: only that survey (a participant's link, participate._start_if_due). Returns the
    number started."""
    now = now or utcnow()
    # not an archived survey: archiving cancels the plan, this holds for one archived before that (H4)
    query = db.select(Survey.id, Survey.start_at).where(Survey.start_at.is_not(None), Survey.start_at <= now,
                                                        Survey.archived.is_not(True))
    if survey_id is not None:
        query = query.where(Survey.id == survey_id)
    due = db.session.execute(query).all()
    started = 0
    for survey_id, due_at in due:
        lease = max(now, utcnow()) + START_LEASE
        claimed = db.session.execute(db.update(Survey).where(Survey.id == survey_id, Survey.start_at == due_at)
                                     .values(start_at=lease).execution_options(synchronize_session=False)).rowcount
        if not claimed:
            db.session.commit()
            continue  # another process took it
        survey = db.session.get(Survey, survey_id)
        db.session.refresh(survey)  # start_at as the claim left it
        plan = dict(survey.start_plan or {})
        # the moment it was planned for; a claim still in the plan is one whose process ended during the start
        planned = _parse_utc(plan.pop("claimed", None))
        cut_off = planned is not None
        planned = planned or due_at
        if cut_off:
            plan["tries"] = (plan.get("tries") or 0) + 1
            current_app.logger.warning("Planned start of survey %s: the last try ended without a word, trying again",
                                       survey_id)
        if survey.status == Survey.RUNNING:  # started by hand in the same moment
            survey.start_at = None
            survey.start_plan = None
            db.session.commit()
            continue
        if (plan.get("tries") or 0) >= START_TRIES:
            _start_failed(survey, planned, _repeated_error(), plan.get("lang"))
            continue
        survey.start_plan = {**plan, "claimed": planned.isoformat()}
        db.session.commit()  # the claim with its lease, before the work that may take long (prereg.freeze)
        with _PageLanguage(plan.get("lang")):
            try:
                start_committed(survey, _parse_utc(plan.get("field_end")), plan.get("max_responses"))
            except LifecycleError as e:
                db.session.rollback()
                _start_failed(survey, planned, str(e), plan.get("lang"))
                continue
            except Exception as e:
                # not a refusal (the database busy, an error of the code): the plan stays, the next round tries again.
                # A busy database goes up to maybe_close_due_surveys; anything else is logged, so that the request
                # that happened to run the housekeeping (perhaps a participant's) goes on
                db.session.rollback()
                busy = isinstance(e, (OperationalError, StaleDataError))
                tries = (plan.get("tries") or 0) + (0 if busy else 1)
                if not busy:
                    current_app.logger.exception("Planned start of survey %s put off", survey_id)
                if tries >= START_TRIES:  # not again and again in every round (and every participant's request)
                    survey = db.session.get(Survey, survey_id)
                    _start_failed(survey, planned, _repeated_error(), plan.get("lang"))
                    continue
                db.session.execute(db.update(Survey).where(Survey.id == survey_id, Survey.start_at == lease)
                                   .values(start_at=planned, start_plan={**plan, "tries": tries} if tries else plan or None)
                                   .execution_options(synchronize_session=False))
                db.session.commit()
                if busy:
                    raise
                continue
        started += 1
    return started


def _repeated_error():
    return de_en("Beim Start trat wiederholt ein interner Fehler auf; bitte von Hand starten oder den Start neu planen.",
                 "An internal error occurred repeatedly at the start; please start by hand or plan the start again.")


def _parse_utc(text):
    from datetime import datetime

    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def _start_failed(survey, planned, reason, lang):
    survey.start_at = None
    survey.start_plan = {"failed": reason, "planned": planned.isoformat(timespec="seconds"), "lang": lang}
    db.session.commit()
    current_app.logger.warning("Planned start of survey %s not done: %s", survey.code, reason)
    try:  # as a cap that was reached (limits.note_cap): a line for the operators, if Slack is configured
        from flask import url_for

        from app import brand, notify

        notify.slack(f":warning: Befragung {notify.survey_ref(survey)}: Der geplante Start wurde nicht "
                     f"ausgeführt. {notify._plain(reason)} "
                     f"<{brand.public_base()}{url_for('surveys.field', survey_id=survey.id)}|In Askurious öffnen>")
    except Exception as e:  # noqa: BLE001 - the report must not stop the housekeeping
        current_app.logger.warning("Failed planned start of survey %s not reported in Slack: %s", survey.code, type(e).__name__)


def close(survey, reason="manual", at=None):
    """End (or pause, reason "paused") the fieldwork. A paused survey can be closed too (audit 2026-10-05, L-10):
    from the pause by hand, or by the housekeeping once its field end is over (close_due_surveys); `at` is then the
    field end, from which the retention periods count."""
    paused = survey.status == Survey.CLOSED and survey.close_reason == "paused"
    if survey.status != Survey.RUNNING and not (paused and reason != "paused"):
        raise LifecycleError(de_en("Nur laufende oder pausierte Befragungen können geschlossen werden.", "Only running or paused surveys can be closed."))
    if reason in ("manual", "paused"):  # by hand; the housekeeping still ends an archived pause at its field end
        _not_archived(survey)
    survey.status = Survey.CLOSED
    survey.closed_at = at or utcnow()
    survey.close_reason = reason
    _log(survey, "close", reason, at)
    if reason != "paused":
        from app import seal

        seal.field_event(survey, "fieldclosed", reason)  # an entry in the seal chain, stamped by the DFN at once


def _not_archived(survey):
    """An archived survey changes its state only after it is restored, as check_start says for a start (H4)."""
    if survey.archived:
        raise LifecycleError(de_en("Die Befragung ist archiviert. Stellen Sie sie zuerst wieder her.",
                                   "The survey is archived. Restore it first."))


def _log(survey, event, reason="", at=None):
    entry = {"at": (at or utcnow()).isoformat(timespec="milliseconds"), "event": event}
    if reason:
        entry["reason"] = reason
    survey.status_log = list(survey.status_log or []) + [entry]


RUN_NAME_LENGTH = 100


def may_start_run(survey):
    """A new run ("Neuer Durchgang") is for a questionnaire under way: started at least once, not a live vote (it
    has runs of its own, app/live/), an interview guide or a diary study."""
    return (survey.status != Survey.DRAFT and survey.live is None and survey.interview is None
            and survey.sampling is None)


def runs(survey):
    """[{"n", "name", "since"}] of a questionnaire: run 1 from the start of the fieldwork (since None), then one for
    every "Neuer Durchgang" (an entry "run" in the status log). The data stay as they are; a participation belongs to
    the run in which it began (run_of)."""
    from datetime import datetime

    out = [{"n": 1, "name": de_en("Durchgang 1", "Run 1"), "since": None}]
    for e in survey.status_log or []:
        if isinstance(e, dict) and e.get("event") == "run" and e.get("at"):
            out.append({"n": len(out) + 1, "name": e.get("reason") or de_en("Durchgang {n}", "Run {n}", n=len(out) + 1),
                        "since": datetime.fromisoformat(e["at"])})
    return out


def run_of(all_runs, started_at):
    """The number of the run in which a participation that began at `started_at` lies."""
    n = 1
    for run in all_runs[1:]:
        if started_at is not None and started_at >= run["since"]:
            n = run["n"]
    return n


def run_since(survey):
    """When the current run began, or None while there is only the first: the live comparisons (crowd, map,
    comparison with the sample) count only participations from then on (balance.answers)."""
    return runs(survey)[-1]["since"]


def new_run(survey, name=""):
    """Begin a new run (Philipp, 2026-10-13): from now on the live comparisons start from zero, e.g. for the next
    lecture with the same questionnaire. Nothing is deleted; the export names each participation's run."""
    if not may_start_run(survey):
        raise LifecycleError(de_en("Einen neuen Durchgang gibt es nur für Fragebögen, deren Datenerhebung begonnen hat.",
                                   "A new run is only possible for questionnaires whose data collection has begun."))
    before = runs(survey)
    base = " ".join((name or "").split())[:RUN_NAME_LENGTH]
    if not base:  # unnamed: named by its number in the language of whoever reads it (runs)
        _log(survey, "run")
        return de_en("Durchgang {n}", "Run {n}", n=len(before) + 1)
    taken, unique, n = {r["name"] for r in before}, base, 2
    while unique in taken:
        unique, n = f"{base} ({n})", n + 1
    _log(survey, "run", unique)
    return unique


def note(survey, event, reason=""):
    """An entry in the status log that changes no state, e.g. "links": completion links changed in the field
    (app/timeline.py shows it as a marker)."""
    _log(survey, event, reason)


TEXT_MERGE_MINUTES = 10  # saves of the same element within this time make one entry (the editor saves by itself)


def note_texts(survey, what, now=None):
    """A wording changed after the start (question, options, translation, block title): an entry "texts" in the
    status log with the element and its languages, e.g. "q3 (de, en)". The questionnaire version stays, so this
    is how one can tell which participations saw which wording (started before or after); the undo steps go after
    7 days (audit 2026-10-12, T-4). Further saves of the same element within TEXT_MERGE_MINUTES only move "until"."""
    from datetime import datetime

    now = now or utcnow()
    log = list(survey.status_log or [])
    last = log[-1] if log else None
    if isinstance(last, dict) and last.get("event") == "texts" and last.get("reason") == what:
        try:
            since = datetime.fromisoformat(last.get("until") or last["at"])
        except (KeyError, TypeError, ValueError):
            since = None
        if since is not None and timedelta(0) <= now - since <= timedelta(minutes=TEXT_MERGE_MINUTES):
            log[-1] = {**last, "until": now.isoformat(timespec="milliseconds")}
            survey.status_log = log
            return
    _log(survey, "texts", what, now)


def _changed_languages(state, attr):
    """The languages whose texts differ before and after this flush, or [] (nothing loaded before: unknown)."""
    history = state.attrs[attr].history
    if not history.added or not history.deleted:
        return []
    old, new = history.deleted[0] or {}, history.added[0] or {}
    if not isinstance(old, dict) or not isinstance(new, dict):
        return []
    return sorted(str(lang) for lang in set(old) | set(new) if old.get(lang) != new.get(lang))


@event.listens_for(Session, "before_flush")
def _note_text_corrections(session, flush_context, instances):
    """Every change of a wording in a survey that has started goes into its status log (note_texts), whichever page
    made it: the element editor, the translation page, the text review, undo."""
    from sqlalchemy import inspect

    from app.models import Block, Item

    with session.no_autoflush:
        for obj in list(session.dirty):
            if isinstance(obj, Item):
                attr = "texts"
            elif isinstance(obj, Block):
                attr = "titles"
            else:
                continue
            langs = _changed_languages(inspect(obj), attr)
            if not langs:
                continue
            block = obj.block if isinstance(obj, Item) else obj
            survey = block.survey if block is not None else None
            if survey is None or survey.status == Survey.DRAFT:
                continue  # a draft has no participations yet, or a new questionnaire version (qversion)
            name = obj.code if isinstance(obj, Item) else f"Block {(obj.position or 0) + 1}"
            note_texts(survey, f"{name} ({', '.join(langs)})")


def reopen_for_editing(survey):
    """Paused/closed -> draft with a new questionnaire version. Existing responses stay (with
    their version); a snapshot of data and codebook is saved first."""
    if survey.status != Survey.CLOSED:
        raise LifecycleError(de_en("Nur pausierte oder geschlossene Befragungen können zur Bearbeitung geöffnet werden.", "Only paused or closed surveys can be opened for editing."))
    _not_archived(survey)  # a draft in the archive, with a backup and a new version (audit 2026-10-18, FUNC-07)
    from app import snapshots

    snapshots.save(survey)
    from app import answer_lock

    answer_lock.freeze_on_reopen(survey)  # the grid of a Q sort stays as the answers were given (audit 2026-10-12, M-4)
    survey.status = Survey.DRAFT
    survey.qversion = (survey.qversion or 1) + 1
    survey.closed_at = None
    survey.close_reason = ""
    _log(survey, "edit", f"v{survey.qversion}")


def deletable(survey):
    """Only a draft without any real participation (complete or not) can be deleted: abandoned set-ups and
    try-outs. Everything that ever collected real answers can only be archived."""
    return survey.status == Survey.DRAFT and not survey.has_real_responses()


def delete(survey, force=False, seal_reason=None, hide_title=False):
    """Remove the survey with its questionnaire, settings, test and dummy runs, comments and uploaded files.
    Link labels stay taken (their survey becomes NULL), so an old link can never lead to another survey.
    force: also with real participations, which then go too. Only for deleting an account whose surveys
    belong to nobody else (app/accounts.py) and for the platform taking a survey down (app/platform_erase.py, whose
    reason goes into the last record of a seal chain as `seal_reason`). hide_title: the title no longer shows with a
    seal chain that stays (/verify/, seal.json; the platform's take-down and an account deleted, S7)."""
    from app.models import (BrowserReceipt, ConstructCitation, ContactEntry, DataToken, EditMark, EditStep, Feedback,
                            InterviewerAccess,
                            LiveFeedback, LiveInk,
                            LiveQuestion, LiveReaction, MemberInvite, OnceValue, PreviewComment, ResearchProject,
                            SemVersion, SplitSession, SurveyHide, SurveyLabel)

    if not force and not deletable(survey):
        raise LifecycleError(de_en("Nur Entwürfe ohne echte Teilnahmen lassen sich löschen. Andere Befragungen können "
                                   "archiviert werden.", "Only drafts without real participations can be deleted. Other surveys "
                                   "can be archived."))
    from app import maxdiff_hb, snapshots

    snapshots.listing(survey)  # both bring files from the time of folders by number into the survey's own folders
    for item in (i for b in survey.blocks for i in b.items if i.kind and i.kind.long_export):
        maxdiff_hb._paths(item)
    folders = survey_folders(survey.uid)
    from app import contact, interview_search

    interview_search.forget(survey.uid)  # the word counts of its interviews kept in memory (code review 2026-10-08, R5)
    contact.lock(survey.id)  # after a running bundle of the separated section's entries (contact.bundle)
    from app.models import ResponsePoolRevision, ResponseRevision

    for model in (ResponsePoolRevision, ResponseRevision, BrowserReceipt, ContactEntry, SplitSession, PreviewComment, EditMark, EditStep, LiveFeedback, LiveInk,
                  LiveQuestion, LiveReaction,
                  OnceValue, MemberInvite, DataToken, SurveyHide, InterviewerAccess, SemVersion):  # not everywhere removed by the database itself
        db.session.execute(db.delete(model).where(model.survey_id == survey.id))
    from app import paper

    paper.delete_survey(survey)  # print runs, sheets and scanned pages (their files: survey_folders)
    from app import survey_invites

    survey_invites.forget(survey)  # the address list for invitation mails and its waiting mails
    # labels stay taken; complaints stay, with the code in their text; sources entered for the survey and research
    # projects stay without it: "SET NULL" of their keys, which SQLite does not do by itself (audit 2026-10-12, E-10)
    for model in (SurveyLabel, Feedback, ConstructCitation, ResearchProject):
        db.session.execute(db.update(model).where(model.survey_id == survey.id).values(survey_id=None))
    # the participations in one statement (their OnceValue rows went above), as retention.purge does: through the
    # cascade of the session every row was loaded with its answers first, all inside the write lock (code review
    # 2026-10-07, K8). Survey.responses has passive_deletes, so the session loads none of them again
    from app.models import Response, keep_floor_before_bulk_delete

    from app import seal

    seal.closed(survey, seal_reason, hide_title=hide_title)  # a seal chain stays, with a record that the survey went (app/seal.py)
    for obj in [o for o in db.session.identity_map.values() if isinstance(o, Response) and o.survey_id == survey.id]:
        db.session.expunge(obj)
    keep_floor_before_bulk_delete(Response, Response.survey_id == survey.id)  # SQLite gives no id twice (audit 2026-10-21, B1)
    db.session.execute(db.delete(Response.__table__).where(Response.__table__.c.survey_id == survey.id))
    from app import diary

    diary.forget(survey.id, invites=True)  # the persons and invitation codes of a diary study (no foreign key: by hand)
    from app import ai_chat

    ai_chat.forget_survey(survey.id, access_too=True)  # the messages of the KI-Chat and the model access (no foreign key)
    db.session.expire(survey, ["responses"])
    db.session.delete(survey)
    db.session.flush()
    # the files go once the deletion is committed: if the commit fails, the survey keeps its files. A note names the
    # folders before the commit, so that the housekeeping removes them should the process end right after it or a
    # file resist (cleanup.retry; audit 2026-10-18, FUNC-04)
    from app import cleanup

    cleanup.note(survey.uid, period=cleanup.DELETED)
    db.session.info.setdefault(_DOOMED, []).append((survey.uid, folders))


def survey_folders(uid):
    """Every folder of a survey's files: images, saved states (they hold all answers), HB results (utilities per
    participation id), recordings of interviews (app/interview_audio.py) and scanned paper sheets (app/paper.py)."""
    import os

    from flask import current_app

    from app import paper

    base = current_app.config["UPLOAD_FOLDER"]
    return [os.path.join(base, uid), os.path.join(base, "snapshots", uid), os.path.join(base, "hb", uid),
            os.path.join(os.path.dirname(base), "interview-audio", uid), os.path.join(paper.root(), uid)]


def remove_folders(folders):
    """Remove these folders with all they hold; True when none is left. A folder that resists is logged."""
    import os
    import shutil

    from flask import current_app

    from app import jobs

    clean = True
    for folder in folders:
        if os.path.basename(os.path.dirname(os.path.abspath(folder))) == "hb":
            jobs.forget(folder, prefix="")  # running estimations stop and write nothing (code review 2026-10-07, C3)
        try:
            shutil.rmtree(folder)
        except FileNotFoundError:
            pass
        except OSError as e:
            clean = False
            current_app.logger.error("Folder of a deleted survey not removed (tried again later): %s: %s", folder, e)
        if os.path.exists(folder):
            clean = False
    return clean


@event.listens_for(Session, "after_commit")
def _remove_folders(session):
    from app import cleanup

    for uid, folders in session.info.pop(_DOOMED, []):
        if remove_folders(folders):
            cleanup.done(uid, period=cleanup.DELETED)


@event.listens_for(Session, "after_rollback")
def _keep_folders(session):
    session.info.pop(_DOOMED, None)


def default_field_end():
    from app.timeutil import day_end_utc, to_local

    # a field time of 30 days, today included: until the end of the local day 29 days from today, midnight at its end
    # (FUNC-06; midnight UTC would be an hour or two into that day in Germany)
    return day_end_utc(to_local(utcnow()).date() + timedelta(days=DEFAULT_FIELD_DAYS - 1))


def close_due_surveys():
    """Close running surveys whose field time is over or whose sample is complete, and paused ones whose field end
    has passed: for participants they ended then (participate.py), and their retention periods begin (L-10).

    Each step runs on its own (_step): one that fails is logged and rolled back, and the others still run; before, a
    step that raised every time (retention.run, say) kept the mail queue, the rate counters, the sign-in sessions
    and every later deletion from ever running (code review 2026-10-15, E1)."""
    now = utcnow()
    _step("start_due_surveys", start_due_surveys, now)  # planned starts ("Start planen"), before the closing
    closed = _step("close_due", _close_due, now) or 0
    from app import quotas

    _step("quotas.release_expired", quotas.release_expired, now)  # reserved quota places of participations idle for too long
    from app import external

    _step("external.sweep_params", external.sweep_params, now)  # URL parameters held for a return link that has run out
    from app import contact

    # PostgreSQL: finished separated sections become entries, all rewritten at once (commits by itself)
    _step("contact.bundle", contact.bundle)
    from app import retention

    _step("retention.run", retention.run, now)  # retention periods that are over (commits by itself, and only if something went)
    from app import limits

    _step("limits.sweep", limits.sweep)  # expired shared rate counters
    from app import transfer

    _step("transfer.sweep_pending", transfer.sweep_pending)  # survey files put aside for an import that was never finished
    from app import panel

    _step("panel.sweep_pastes", panel.sweep_pastes)  # pasted panel link lists never taken over
    from flask import has_request_context

    from app import account_jobs

    _step("account_jobs", account_jobs.kick if has_request_context() else account_jobs.run)
    from app import mailqueue

    _step("mailqueue.run", mailqueue.run, now)  # due reminders of invitation mails, and sending what waits (mail on only)
    from app import seal

    _step("seal.stamp_soon", seal.stamp_soon)  # new entries of seal chains stamped within about ten minutes (in a thread)
    _rare_sweeps(now)
    _step("database_copies", _sweep_database_copies)
    # always end the transaction, also when nothing changed: on SQLite a DELETE that finds nothing still takes the
    # write lock, which otherwise stayed held to the end of whatever request ran the housekeeping (K2, D1)
    db.session.commit()
    return closed


def sample_full(survey, target=None):
    """Whether the survey has its sample: `target` (max_responses) completions, not counting those with a bot signal
    (quality.bot_signal: the hidden field filled in, the code word for AI systems sent back), which a script sent to
    close the survey for the real participants (audit 2026-10-22, D-06). The completions' paradata are read only once
    the plain count reaches the target. A diary study counts persons as before."""
    target = target or survey.max_responses
    done = survey.completed_count()
    if not target or done < target:
        return False
    if survey.sampling is not None:
        return True
    from app import quality
    from app.models import Response

    rows = db.session.scalars(db.select(Response.paradata).filter(
        Response.survey_id == survey.id, Response.completed_at.is_not(None), Response.is_preview.is_(False),
        *([Response.not_live_extra()] if survey.live is not None else [])))
    return done - sum(1 for p in rows if quality.bot_signal(p)) >= target


def _close_due(now):
    from sqlalchemy.orm import selectinload

    closed = 0
    for s in db.session.scalars(db.select(Survey).options(selectinload(Survey.quotas)).filter_by(status=Survey.RUNNING)):
        if s.field_end is not None and s.field_end <= now:
            close(s, "time")
            closed += 1
        elif s.max_responses and sample_full(s):
            close(s, "sample")
            closed += 1
        elif s.quotas and _quotas_exhausted(s):
            # caught up as the sample size is: two completions at the same moment may each have missed the other
            # (close_if_sample_complete locks the survey now; audit 2026-10-05, T-12)
            close(s, "quota")
            closed += 1
    for s in db.session.scalars(db.select(Survey).filter_by(status=Survey.CLOSED, close_reason="paused")):
        if s.field_end is not None and s.field_end <= now:
            close(s, "time", at=max(s.field_end, s.closed_at or s.field_end))
            closed += 1
    return closed


def _transient(error):
    """The database is busy, or a row changed in the same moment: worth trying again next round, not a broken step."""
    if isinstance(error, StaleDataError):
        return True
    text = str(getattr(error, "orig", error)).lower()
    return isinstance(error, OperationalError) and any(
        word in text for word in ("locked", "deadlock", "could not obtain lock", "canceling statement", "lock timeout"))


def _step(name, fn, *args):
    """One step of the housekeeping, committed on its own: a failure rolls back only this step, is logged with its
    name, and the other steps still run (code review 2026-10-15, E1). A busy database (_transient) still puts off
    the whole round as before: the next round tries again, and a request does not wait for a lock once per step."""
    try:
        result = fn(*args)
        db.session.commit()
        return result
    except Exception as e:
        db.session.rollback()
        if _transient(e):
            raise
        current_app.logger.exception("Housekeeping step %s failed", name)
        return None


def _rare_sweeps(now):
    """Hourly per process: what needs no minute-by-minute care (code review 2026-10-07, K2). The tests call each
    of these directly. Each step on its own (_step, E1)."""
    global _last_rare
    if _last_rare is not None and time.monotonic() - _last_rare < _RARE_INTERVAL:
        return
    _last_rare = time.monotonic()
    from app import browser_receipts, interview_data

    _step("browser_receipts.sweep", browser_receipts.sweep, now)
    from app import data_revision

    _step("data_revision.sweep", data_revision.sweep)  # the revision rows of deleted surveys (audit 2026-10-15, T05-N-1)
    from app.models import mark_live_extra

    # further words of live votes an older program stored during a rolling deploy without the column (PERF-02)
    _step("models.mark_live_extra", mark_live_extra)
    # interviews of an interview guide past their period (experimental, commits by itself)
    _step("interview_data.run", interview_data.run, now)
    from app.items import voice

    # voice messages no participation holds any more (deleted in bulk, first page never stored): hourly, and only
    # the answers of participations that have a folder of recordings (code review 2026-10-15, E3; it was every
    # minute, with every answer of every survey that has one)
    _step("voice.run", voice.run, now)
    from app import diary

    # persons of diary studies who never finished the entry part, or have no entry left (commits)
    _step("diary.sweep", diary.sweep, now)
    from app import ai_chat

    # messages of the KI-Chat whose participation or survey is gone (deleted past the session events; commits)
    _step("ai_chat.sweep", ai_chat.sweep)
    from app import seal

    # DFN timestamps of seal chains and pre-registrations, chains against their data
    _step("seal.sweep_in_background", seal.sweep_in_background)
    from app import interview_audio

    _step("interview_audio.run", interview_audio.run, now)  # their recordings past the period for recordings
    from app import comment_shots

    # images of comments from test runs of deleted surveys, and those no comment names
    _step("comment_shots.run", comment_shots.run, now)
    from app import paper

    _step("paper.run", paper.run, now)  # scans of paper questionnaires past their time, loose pages, broken-off uploads
    from app import coscine

    # once a day: the Coscine tokens behind switched-on automatic uploads, checked at Coscine (K7)
    _step("coscine.check_tokens", coscine.check_tokens, now)
    from app import media

    # pieces of video and audio uploads not continued for an hour (audit 2026-10-09, D1)
    _step("media.sweep_parts", media.sweep_parts)
    from app import sessions

    _step("sessions.sweep", sessions.sweep, now)  # sign-in sessions that ended or were not used for the cookie lifetime
    from app import account_types

    # user types with a period (konzept-nutzertypen-2026-10-15.md): notices by mail, blocking at the end, deletion after
    # the type's months when nothing has to be handed over
    _step("account_types.run", account_types.run, now)
    from app import org_delete

    _step("org_delete.run", org_delete.run, now)  # accounts an organisation admin marked for deletion, when due
    from app import audit

    _step("audit.sweep", audit.sweep, now)  # entries of the access log older than a year
    from app import signin

    _step("signin.sweep_waiting", signin.sweep_waiting, now)  # the waiting list of the sign-in after 90 days (audit 2026-10-10, L-10)
    from app import signup_links

    _step("signup_links.sweep", signup_links.sweep)  # registration links 30 days after they expired (privacy page)
    from app import data_access, invites, joinlinks

    # expired invitations of researchers (an e-mail address) and join links of groups, also when nobody opens the
    # list (the privacy page promises 14 days; audit 2026-10-10, L-7); expired data access tokens (commits by itself)
    _step("invites.sweep", invites.sweep)
    from app import people_keys

    _step("people_keys.sweep", people_keys.sweep)  # pending accounts no row names any more (B2, data minimisation)
    _step("joinlinks.sweep", joinlinks.sweep)
    _step("data_access.sweep", data_access.sweep)
    from app import agent_access

    _step("agent_access.sweep", agent_access.sweep)  # tokens of the AI agent interface expired more than 30 days ago
    from app import report_jobs

    _step("report_jobs.sweep", report_jobs.sweep)  # private report/download snapshots expire after one hour


def _sweep_database_copies():
    """Hourly: copies of the database before migrations past their days, and snapshots a move that ended hard left
    in the temp folder (audit 2026-10-05, L-5). Both hold every answer; neither step raises."""
    global _last_sweep
    if current_app.testing:  # the tests never touch the real instance folder (tests/editor/test_audit_b_copies.py calls these)
        return
    if _last_sweep is not None and time.monotonic() - _last_sweep < _SWEEP_INTERVAL:
        return
    _last_sweep = time.monotonic()
    from app import dbmove, migrations

    migrations.sweep_backups(current_app._get_current_object())
    dbmove.sweep_copies()


def maybe_close_due_surveys():
    global _last_check
    if time.monotonic() - _last_check < _CHECK_INTERVAL:
        return
    _last_check = time.monotonic()
    try:
        close_due_surveys()
    except (OperationalError, StaleDataError) as e:
        # housekeeping inside whatever request comes first: the database is busy, or a participation changed
        # in the same moment. The request itself goes on; the next check does the housekeeping
        db.session.rollback()
        current_app.logger.warning("Closing due surveys put off: %s", str(e).splitlines()[0])
    except Exception:
        # anything else neither: the request belongs to someone else (often a participant), and the housekeeping
        # is no part of it (code review 2026-10-15, E2)
        db.session.rollback()
        current_app.logger.exception("Housekeeping in a request failed")


def _quotas_exhausted(survey):
    from app import quotas

    return quotas.exhausted(survey)


def close_if_sample_complete(survey):
    """Call right after a response has ended, so the threshold is exact: the sample is complete, or the quotas
    let nobody in any more (quotas.exhausted).

    The survey row is locked first and read again (SELECT ... FOR UPDATE on PostgreSQL), and the counters of its
    cells are read again after that: two completions at the same moment then count one after the other, and the
    second sees the first once it is committed. Without the lock each counted only what was committed plus itself,
    and with max - 2 completed before, both saw max - 1 and neither closed (audit 2026-10-05, T-12). The lock is
    held to the end of the request; completions of one survey wait for each other that short while. SQLite has one
    writer, held by this transaction since its first write, and ignores FOR UPDATE.

    FOR NO KEY UPDATE, not FOR UPDATE (as seal._append): it waits for the other completions as well, but not for
    the FOR KEY SHARE locks that every row written with a reference to the survey holds; with FOR UPDATE two
    completions that had each written such a row waited for each other (deadlock, code audit 2026-10-08)."""
    if survey.status != Survey.RUNNING:
        return
    if not survey.max_responses and not survey.quotas:
        return  # nothing that could fill up: no lock, completions do not wait for each other (audit 2026-10-09, N-10)
    db.session.flush()  # the completion itself; and no change to the survey is lost by reading it again
    db.session.refresh(survey, with_for_update={"key_share": True})
    if survey.status != Survey.RUNNING:  # closed by the completion that held the lock before
        return
    if survey.max_responses and sample_full(survey):
        close(survey, "sample")
    elif survey.quotas:
        for quota in survey.quotas:
            for cell in quota.cells:  # the counters as committed now, not as loaded earlier in this request
                db.session.expire(cell, ["running", "done", "refused", "over", "freed", "closed"])
        if _quotas_exhausted(survey):
            close(survey, "quota")
