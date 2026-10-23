"""The separated section: answers after the flow's split point, stored so they cannot be
linked to any response (see models.ContactEntry and participate.py).

Safeguards: no reference to the response, no timestamp, random key, WITHOUT ROWID table (in
PostgreSQL all rows are written anew in random order about once a minute instead, see store and bundle), the handler writes nothing
about the response, exports are sorted by content (never by submission order), and downloads need
MIN_RESPONSES completed participations.
"""
import csv
import io
import json
import re
import secrets
from types import SimpleNamespace

from app.export import _cell
from app.items.base import plain
from app.extensions import db
from app.models import ContactEntry, Response, SplitSession

MIN_RESPONSES = 5
GONE_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
SEED = "_seed"  # in an entry's answers: the random id of its section, from which an assignment drew its sides


def separated_items(survey):
    """Answer-collecting items after the split, in flow order."""
    from app import flow

    blocks = {b.id: b for b in survey.blocks}
    return [i for bid in flow.blocks_after_split(survey) for i in blocks[bid].items if i.kind.collects_answer]


def needs_seed(survey):
    """True if an item of the section draws something from the id of the section's session (an assignment task
    with sides per person or per element, items/assign.py swapped): the entry keeps that random id (SEED), so
    the export can say which side each category was on. It is not the id of the participation."""
    return any(i.type == "assign" and (i.config or {}).get("sides", "person") != "fixed" for i in separated_items(survey))


def borrowed(survey, response):
    """{answer key: answer} of the main part that items of the section read to show something (a comparison or
    placement, items/sample_compare.py, whose source question comes before the split). The section's pages get
    them in memory only, for each page anew (participate._run_split): they are never stored with its answers."""
    from app.items.sample_compare import SampleCompare

    items = separated_items(survey)
    inside = {str(i.id) for i in items}
    wanted = {str(src.id) for i in items if isinstance(i.kind, SampleCompare) for src in i.kind.sources(i)} - inside
    return {k: v for k, v in (response.answers or {}).items() if k.split("#")[0] in wanted}


def has_separated(survey):
    from app import flow

    return flow.has_separated(survey)


def completed_count(survey):
    return db.session.scalar(
        db.select(db.func.count(Response.id)).filter_by(survey_id=survey.id, source="real", status=Response.COMPLETED)
    )


def _unfinished(survey):
    """Sections left before their end still count with what was entered so far (e.g. the e-mail for a
    draw); they hold no timestamp, so there is no telling them apart from a participant still typing. Also the
    finished ones that wait for the next bundle on PostgreSQL (store): their answers are those of the entry."""
    rows = db.session.scalars(db.select(SplitSession).filter_by(survey_id=survey.id)).all()
    seed = needs_seed(survey)
    return [SimpleNamespace(answers={**(s.answers or {}), **({SEED: s.id} if seed else {})}, email="", comment="")
            for s in rows if any(v not in (None, "", [], {}) for v in (s.answers or {}).values())]


def entries(survey):
    """All entries, sorted by content (never in submission order)."""
    rows = db.session.scalars(db.select(ContactEntry).filter_by(survey_id=survey.id)).all() + _unfinished(survey)
    key = lambda e: json.dumps(e.answers or {}, sort_keys=True, ensure_ascii=False) + e.email.lower() + e.comment.lower()
    return sorted(rows, key=key)


def count(survey):
    return (db.session.scalar(db.select(db.func.count()).select_from(ContactEntry).filter_by(survey_id=survey.id))
            + len(_unfinished(survey)))


def can_download(survey):
    return completed_count(survey) >= MIN_RESPONSES


def _email_values(survey, entry):
    items = [i for i in separated_items(survey) if i.type == "text_input" and i.config.get("validation") == "email"]
    values = [(entry.answers or {}).get(str(i.id)) for i in items]
    # addresses given to an e-mail question that has left the section since still take part in the draw
    known = {str(i.id) for i in separated_items(survey)}
    values += [v for k, v in (entry.answers or {}).items()
               if k.split("#")[0] not in known and isinstance(v, str) and GONE_EMAIL_RE.fullmatch(v.strip())]
    return [v for v in values + [entry.email] if v]


def draw(survey, n):
    """Random winners among distinct e-mail addresses given in the separated section."""
    emails = sorted({e.lower() for entry in entries(survey) for e in _email_values(survey, entry)})
    return secrets.SystemRandom().sample(emails, min(n, len(emails)))


# pg_advisory_xact_lock(STORE_LOCK, survey id): one rewrite of a survey's entries at a time, and no deletion of
# entries while one runs (any number, only used here)
STORE_LOCK = 4711


def _postgres():
    return db.engine.dialect.name == "postgresql"


def lock(survey_id):
    """PostgreSQL: wait for a running bundle of this survey (bundle) and keep the next one out until this
    transaction ends. Whoever deletes entries or finished sections takes it first: the bundle deletes the rows and
    writes them anew under new row versions, and a deletion that waited on a row of the old version would find
    nothing and leave the new one standing. SQLite has one writer: nothing to do."""
    from sqlalchemy import text

    if _postgres():
        db.session.execute(text("SELECT pg_advisory_xact_lock(:lock, :survey)"), {"lock": STORE_LOCK, "survey": survey_id})


def store(survey, answers, split=None):
    """Keep the answers of a finished separated section (its session `split`, if any) as a new entry.

    SQLite stores the rows in the order of their random key (WITHOUT ROWID): the entry is written at once and the
    session deleted. PostgreSQL has no such table: every row carries the number of the transaction that wrote it
    (xmin, only ever rising) and its place in the file (ctid), so whoever can read the database could line the
    entries up with the completion times of the responses (code review 2026-10-03, PG-3). There the finished
    session itself waits, marked completed and holding the answers of the entry, and the housekeeping writes all
    entries of the survey anew about once a minute, the waiting ones with them, in one transaction and in random
    order (bundle): then they share one xmin, and their place in the file says nothing. Until then the waiting
    session counts as an entry everywhere (entries, count, the draw, the export, deletion on request, the
    retention periods), as an unfinished one does. Before, every finished section wrote all entries anew, n rows
    for each of n entries (code review 2026-10-07, K11).

    Why the session waits and not a table of its own: the session already is such a row, with no reference to the
    participation and no time, kept in the database (so for all server processes and through a crash), and every
    place that reads or deletes entries already reads and deletes it. While the section ran, its row was just as
    close to the participation's completion as it is while it waits; a row of its own would carry the same xmin."""
    from app.models import _random_id

    if _postgres() and split is not None:
        split.answers = answers
        split.status = SplitSession.COMPLETED
        return
    db.session.add(ContactEntry(id=_random_id(), survey_id=survey.id, email="", comment="", answers=answers))
    if split is not None:
        db.session.delete(split)


def bundle(now=None):
    """PostgreSQL, from the housekeeping (lifecycle.close_due_surveys, about once a minute): the finished sections
    that wait (store) become entries. For each survey with any, in a transaction of its own: the waiting sessions
    and all entries are deleted and written anew in random order, so all entries of the survey share one xmin
    and lie in no telling order. Returns how many entries were added. A row is only written anew if this bundle
    deleted it itself (RETURNING): what a deletion on request took out in between stays out."""
    import secrets

    from flask import current_app
    from sqlalchemy.exc import SQLAlchemyError

    from app.models import _random_id

    if not _postgres():
        return 0
    split, entry = SplitSession.__table__, ContactEntry.__table__
    waiting = list(db.session.scalars(db.select(SplitSession.survey_id).filter_by(status=SplitSession.COMPLETED).distinct()))
    added = 0
    if waiting:
        db.session.commit()  # what the caller did so far is not taken back with a bundle that fails
    for survey_id in sorted(waiting):
        try:
            lock(survey_id)
            new = db.session.execute(split.delete().where(split.c.survey_id == survey_id, split.c.status == SplitSession.COMPLETED)
                                     .returning(split.c.answers)).scalars().all()
            old = [dict(r._mapping) for r in db.session.execute(
                entry.delete().where(entry.c.survey_id == survey_id)
                .returning(entry.c.id, entry.c.survey_id, entry.c.email, entry.c.comment, entry.c.answers))]
            rows = old + [{"id": _random_id(), "survey_id": survey_id, "email": "", "comment": "", "answers": a} for a in new]
            secrets.SystemRandom().shuffle(rows)
            if rows:
                db.session.execute(entry.insert(), rows)
            db.session.commit()
            added += len(new)
        except SQLAlchemyError as e:  # e.g. the survey was deleted in the same moment: the next bundle tries again
            db.session.rollback()
            current_app.logger.warning("Entries of the separated section of survey %s not bundled: %s", survey_id,
                                       str(e).splitlines()[0])
    return added


def delete_all(survey):
    lock(survey.id)
    db.session.execute(db.delete(ContactEntry).where(ContactEntry.survey_id == survey.id))
    db.session.execute(db.delete(SplitSession).where(SplitSession.survey_id == survey.id))
    db.session.commit()


def _gone_column(key):
    return "entfernt_" + key.replace("#", "_")


def to_csv(survey, separator="comma"):
    items = separated_items(survey)
    all_entries = entries(survey)
    legacy = any(e.email or e.comment for e in all_entries)
    cols = []
    for i in items:
        cols += [(c, plain(label)) for c, label in i.kind.export_columns(i)]
    # answers to questions that have left the section since (removed or moved after "Zur Bearbeitung öffnen"):
    # they stay in the export as they were stored, so no address given for a draw gets out of reach
    known = {str(i.id) for i in items}
    gone = sorted({k for e in all_entries for k, v in (e.answers or {}).items()
                   if k.split("#")[0] not in known and not k.startswith("_") and v not in (None, "", [], {})})
    cols += [(_gone_column(k), "Antwort auf eine Frage, die nicht mehr im getrennten Abschnitt steht") for k in gone]
    if legacy:  # own names: an item of the section may be coded "email" or "comment"
        cols += [("legacy_email", "E-Mail (altes Kontaktformular)"),
                 ("legacy_comment", "Kommentar (altes Kontaktformular)")]
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf, delimiter=";" if separator == "semicolon" else ",", lineterminator="\r\n")
    writer.writerow([c for c, _ in cols])
    for e in all_entries:
        row = {}
        answers = {k: v for k, v in (e.answers or {}).items() if not k.startswith("_")}
        # what the export of the main part adds from the participation itself (export.rows): the sides of an
        # assignment from the section's seed (entries stored before it was kept have none: left empty), computed
        # values from the section's answers; no pid, so no voice file names
        entry = SimpleNamespace(id=(e.answers or {}).get(SEED), pid=None, answers=answers, layout={})
        for i in items:
            answer = answers.get(str(i.id))
            row.update(i.kind.export_values(i, answer))
            if not (i.type == "assign" and entry.id is None):
                row.update(i.kind.export_response_values(i, answer, entry))
        for k in gone:
            value = (e.answers or {}).get(k)
            row[_gone_column(k)] = value if isinstance(value, (str, int, float)) or value is None else json.dumps(
                value, ensure_ascii=False)
        if legacy:
            row.update(legacy_email=e.email, legacy_comment=e.comment)
        writer.writerow([_cell(row.get(c), separator == "semicolon") for c, _ in cols])
    return buf.getvalue()
