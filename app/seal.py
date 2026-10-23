"""Seal chain (Philipp 2026-10-08; on for new surveys, config SEAL_DEFAULT): data that cannot lose a case unnoticed.

Every real participation that ended (completed, screen-out, quota full, excluded; a declined one is never stored)
gets the next number of its survey's chain, its case number, and an entry whose hash follows from the entry before:
h_n = SHA-256(h_n-1 | n | kind | case | status | content | reason | time) (seal_verify.link). Whoever removes,
changes or reorders an entry breaks every later hash. What else is written, always as a new entry:

  amended   the stored data of a case changed after it ended (live votes, interviews, the retention period for
            exact positions); the entry holds the new hash of its content
  deleted   the deletion record of a case: a participant's request, the researchers', a live run thrown away, or the
            platform's (reason "platform: " and why, in a few words; app/platform_erase.py). The answers go, the
            record stays; it names the case number, never whose case it was
  purged    every case at once, when the period "answers" is over (app/retention.py)
  closed    the survey itself was deleted, by its researchers or taken down by the platform (with its reason);
            its chain stays (rows by the survey's uid, no foreign key), and so does its row in seal_chain (number,
            uid, title), so that /verify/<code> still finds the chain and shows that the survey went (L-1)
  prereg    the pre-registration of a questionnaire version was frozen (app/prereg.py): the SHA-256 of its ZIP as
            content, its file name as reason; written with the start of the fieldwork and stamped with it
  fieldstarted, fieldclosed  the fieldwork started, or was closed (by hand, at its end, at the sample or quotas
            reached; not a pause): the chain is stamped right after, so the states at the start and at the end of
            the fieldwork have a date of the DFN
  enabled   the switch was turned on; cases that had ended before follow, sealed in the order they ended

The content hash of a case is SHA-256 of a random salt and its stored data (answers, status, language, end,
questionnaire version, and the values that external sections and web services handed back: record). Not the
categories of open answers: they are the researchers' work and change while they code. The salt goes with the case,
so a deletion record does not let anyone test a guess of the deleted answers against it. Entries written before the
values of external sections and web services were covered (audit 2026-10-10, L-15) keep their record (column rec
empty = 1) and are checked by it; every new entry of a case holds RECORD.

Outside the database: the newest hash of a chain is stamped by the DFN (RFC 3161, TSA_URL, app/seal_verify.py), in
the background about every ten minutes (stamp_soon, and the hourly lifecycle._rare_sweeps) and at every download of
the chain. With SEAL_MERKLE the background stamps all chains of a round with one call: the token stamps the root of
a Merkle tree over their newest hashes, and each chain keeps its path to that root (anchor_round). The start and the
close of the fieldwork and the download still stamp their chain alone, at once. The timestamp proves that
the chain up to its entry existed no later than then; whoever could rewrite the database could rebuild a chain, but
not the timestamps. The download (seal.json with seal_verify.py, the checker that needs nothing of Askurious)
and the columns seal_seq and seal_hash of the data export let anyone check a data file: every case of the chain is
in it, or has a deletion record.

The entries are written in the flush that ends, changes or deletes a participation (after_flush below), so every
way there (participation, live votes, interviews, erasure, deleting test data) is covered without each knowing of
it. Bulk statements past the session (retention.purge, lifecycle.delete, geolocation.coarsen) call purged, closed
and amend_changed themselves."""
import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import sqlalchemy as sa
from flask import current_app
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app import seal_verify
from app.extensions import db
from app.models import Response, SealAnchor, SealChain, SealEntry, Survey

ENDED = (Response.COMPLETED, Response.SCREENOUT, Response.QUOTAFULL, Response.EXCLUDED)
WATCHED = ("status", "answers", "completed_at", "language", "qversion", "external")  # what record() reads
RECORD = 2  # what record() covers in new entries (SealEntry.rec): 1 without, 2 with external sections and web services
REASON = "seal_reason"  # Session.info: why the participations deleted in this flush go (erasure.erase, live)
PLATFORM = "platform: "  # the start of the reason of a deletion by the platform (app/platform_erase.py)
REASON_MAX = 200  # SealEntry.reason
DEFAULT_TSA = "https://zeitstempel.dfn.de"  # https (audit 2026-10-11, AU7); nonce and signature are checked as well
TSA_SECONDS = 30            # the whole call at most (app/bounded.py): no reply dragged out byte by byte (AU7)
TSA_MAX_BYTES = 256 * 1024  # a reply of the DFN is a few kilobytes
TIMEOUT = 10  # seconds for one timestamp
QUICK = (3, 5)  # (connect, read) inside a request someone waits for (the start of the fieldwork, prereg.freeze)
BATCH = 20  # chains stamped per background run at most
TABLE = SealEntry.__table__
CHAINS = SealChain.__table__
COLUMNS = [  # of the data export (app/export.py), with the German labels the data files keep
    ("seal_seq", "Fallnummer in der Siegelkette; leer = nicht versiegelt (nicht beendet, Test, Dummy)"),
    ("seal_hash", "Hashwert des Inhalts dieses Falls, wie zuletzt in der Siegelkette verzeichnet"),
]
COLUMNS_EN = {
    "seal_seq": "Case number in the seal chain; empty = not sealed (not ended, test, dummy)",
    "seal_hash": "Hash of the content of this case as last recorded in the seal chain",
}


def tsa_url():
    """The timestamp service; empty = none (tests, an installation without internet)."""
    return (current_app.config.get("TSA_URL", DEFAULT_TSA) or "").strip()


CASE_KINDS = ("sealed", "amended")  # entries that name one case: their time is the day only (_entry_time)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _entry_time(kind):
    """The time an entry holds. A case's entry holds the day only: its case number is in the data file (seal_seq),
    and a time to the microsecond would give back the end of every participation that public data cut to the month
    (export.PUBLIC_TIME_COLUMNS). The hash is built on the day as written, so the chain checks as before."""
    if kind in CASE_KINDS:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _now()


def _when(value):
    if value is None:
        return None
    return value.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.%f")


def record(response, version=RECORD):
    """What the content hash of a case covers: the stored answers and how the participation ended; from version 2
    also the values external sections and web services handed back (they stand in the data export as the person's
    data, L-15). Not the identifiers from the link (they go after their retention period), not paradata (time
    outside, duration of a call), not the categories of the coding (the researchers' work)."""
    rec = {"id": response.id, "status": response.status, "language": response.language,
           "qversion": response.qversion, "completed_at": _when(response.completed_at),
           "answers": response.answers or {}}
    if (version or 1) >= 2:
        rec["external"] = _returned(getattr(response, "external", None) or {})
    return rec


def _returned(kept):
    """The values in Response.external a case's hash covers: of an external section the values handed back and
    whether their signature held, of a web service whether the call worked and its values (external.py,
    webservice.py)."""
    out = {}
    for key, entry in kept.items():
        if not isinstance(entry, dict):
            continue
        if key == "_ws":  # webservice.KEY: {node id: {"ok", "v", "ms", "err"}}
            calls = {nid: {k: e[k] for k in ("ok", "v") if k in e} for nid, e in entry.items() if isinstance(e, dict)}
            if calls:
                out["_ws"] = calls
        elif not key.startswith("_") and entry.get("back"):  # an external section the person came back from
            out[key] = {k: entry[k] for k in ("v", "sig") if k in entry}
    return out


def content(rec, salt):
    text = json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(bytes.fromhex(salt) + text.encode("utf-8")).hexdigest()


def _case(entry):
    return entry.ref if entry.kind == "amended" else entry.seq


def _append(conn, uid, kind, ref=None, status=None, digest_of=None, salt=None, response_id=None, reason=None, rec=None):
    """Add an entry to the chain of survey uid; returns its number. The survey row is locked first (SELECT ... FOR
    NO KEY UPDATE on PostgreSQL; SQLite writes one at a time anyway), so two participations ending at once count one
    after the other. FOR NO KEY UPDATE and not FOR UPDATE: it waits for every other lock of the survey row and every
    change of it (enable sets seal), but not for the FOR KEY SHARE locks that every row written with a reference to
    the survey holds until its commit (the entry itself, a participation, a separated section). With FOR UPDATE two
    requests that had each written such a row and then both locked the survey waited for each other, and
    PostgreSQL broke one off (deadlock, code audit 2026-10-08)."""
    conn.execute(sa.select(Survey.id).where(Survey.uid == uid).with_for_update(key_share=True))
    head = conn.execute(sa.select(TABLE.c.seq, TABLE.c.digest).where(TABLE.c.survey_uid == uid)
                        .order_by(TABLE.c.seq.desc()).limit(1)).first()
    if head is None:
        _remember(conn, uid)
    prev = head.digest if head else seal_verify.genesis(uid)
    entry = {"seq": (head.seq if head else 0) + 1, "kind": kind, "ref": ref, "status": status,
             "content": digest_of, "reason": reason, "at": _entry_time(kind)}
    entry["digest"] = seal_verify.link(prev, entry)
    conn.execute(TABLE.insert().values(survey_uid=uid, prev=prev, salt=salt, response_id=response_id, rec=rec, **entry))
    return entry["seq"]


def _remember(conn, uid, title=None):
    """The survey of a chain in seal_chain (its number, uid, title), kept when the survey goes (L-1): written with
    the first entry, and at the end of a chain (closed) with the last title, also for a chain older code began."""
    if conn.execute(sa.select(CHAINS.c.survey_uid).where(CHAINS.c.survey_uid == uid)).first() is not None:
        if title is not None:
            conn.execute(CHAINS.update().where(CHAINS.c.survey_uid == uid).values(title=title[:300]))
        return
    row = conn.execute(sa.select(Survey.id, Survey.title).where(Survey.uid == uid)).first()
    if row is not None:
        conn.execute(CHAINS.insert().values(survey_uid=uid, survey_id=row.id, title=(title or row.title or "")[:300]))


def _latest(conn, response_id):
    return conn.execute(sa.select(TABLE).where(TABLE.c.response_id == response_id)
                        .order_by(TABLE.c.seq.desc()).limit(1)).first()


def _seal(conn, uid, response, latest=None, reason=None):
    """Seal a case that ended, or record that its content changed. Nothing if it is as recorded."""
    # an entry from before RECORD is compared by what its hash covered then (L-15)
    if latest is not None and content(record(response, latest.rec), latest.salt) == latest.content:
        return None
    if latest is None and response.status not in ENDED:
        return None
    rec, salt = record(response, RECORD), secrets.token_hex(16)
    if latest is None:
        return _append(conn, uid, "sealed", status=response.status, digest_of=content(rec, salt), salt=salt,
                       response_id=response.id, rec=RECORD)
    return _append(conn, uid, "amended", ref=_case(latest), status=response.status, digest_of=content(rec, salt),
                   salt=salt, response_id=response.id, reason=reason or "changed", rec=RECORD)


def _forget(conn, response_id=None, uid=None):
    """The salt and the participation's id leave the entries of a deleted case (or of every case of a chain)."""
    where = TABLE.c.response_id == response_id if uid is None else TABLE.c.survey_uid == uid
    conn.execute(TABLE.update().where(where).values(salt=None, response_id=None))


def _sealed(r):
    return r.source == "real" and not r.is_preview and r.id is not None


@event.listens_for(Session, "after_flush")
def _after_flush(session, _context):
    responses = [o for o in (*session.new, *session.dirty, *session.deleted) if isinstance(o, Response) and _sealed(o)]
    if not responses:
        return
    conn = session.connection()
    query = sa.select(Survey.id, Survey.uid, Survey.seal).where(Survey.id.in_({r.survey_id for r in responses}))
    if any(r.status in ENDED and r not in session.deleted for r in responses):
        # a participation ends: the survey row is locked before the switch is read (PostgreSQL waits here for an
        # enable() not yet committed and reads its seal=True then), so a case that ends while the chain is switched
        # on is sealed by one of the two, never by neither (code audit 2026-10-08, S7). No filter on seal in SQL:
        # a row that matched no WHERE in the snapshot would not be locked nor read again. SQLite has one writer.
        query = query.with_for_update(key_share=True)  # FOR NO KEY UPDATE, see _append
    uids = {sid: uid for sid, uid, on in conn.execute(query).all() if on}
    if not uids:
        return
    reason = session.info.get(REASON)
    for r in sorted(responses, key=lambda o: (o.completed_at is None, o.completed_at or 0, o.id)):
        uid = uids.get(r.survey_id)
        if uid is None:
            continue
        if r in session.deleted:
            latest = _latest(conn, r.id)
            if latest is not None:
                _append(conn, uid, "deleted", ref=_case(latest), status=latest.status, reason=reason or "deleted")
                _forget(conn, r.id)
            continue
        if r not in session.new:
            state = inspect(r)
            if not any(state.attrs[name].history.has_changes() for name in WATCHED):
                continue
        _seal(conn, uid, r, _latest(conn, r.id), reason)


def has_chain(survey):
    return db.session.scalar(sa.select(TABLE.c.id).where(TABLE.c.survey_uid == survey.uid).limit(1)) is not None


def enable(survey):
    """Switch the chain on: an entry "enabled", then every real participation that had ended, in the order they
    ended. Switching off does not exist once there is a chain."""
    if survey.seal:
        return
    survey.seal = True
    db.session.flush()
    conn = db.session.connection()
    _append(conn, survey.uid, "enabled")
    ended = db.session.scalars(sa.select(Response).where(
        Response.survey_id == survey.id, Response.source == "real", Response.is_preview.is_(False),
        Response.status.in_(ENDED)).order_by(Response.completed_at, Response.id))
    for r in ended:
        _seal(conn, survey.uid, r)


def purged(survey, reason="retention"):
    """Every case is deleted at once (retention.purge): one record for all. Only while an entry still names a
    participation: after the record their ids are gone (_forget), so a second run of the period writes no second
    record, nor one a minute for ever (audit 2026-10-10, L-5)."""
    named = db.session.scalar(sa.select(TABLE.c.id).where(TABLE.c.survey_uid == survey.uid,
                                                          TABLE.c.response_id.is_not(None)).limit(1))
    if named is not None:
        conn = db.session.connection()
        _append(conn, survey.uid, "purged", reason=reason)
        _forget(conn, uid=survey.uid)


def closed(survey, reason=None, hide_title=False):
    """The survey is deleted (lifecycle.delete): its chain ends with a record and stays. `reason`: a deletion by the
    platform (platform_reason), else "survey_deleted". hide_title: the platform took the survey down, or its account
    was deleted: the title is blanked in seal_chain, so /verify/ and seal.json show a neutral name instead (decision
    7a, audit 2026-10-10, S7). The title is in no hash (seal_verify.link), so the chain holds as before."""
    if has_chain(survey):
        conn = db.session.connection()
        # the chain stays findable by its check codes (L-1)
        _remember(conn, survey.uid, "" if hide_title else survey.title)
        _append(conn, survey.uid, "closed", reason=reason or "survey_deleted")
        _forget(conn, uid=survey.uid)


def platform_reason(text):
    """The reason of a deletion by the platform as it goes into the chain: "platform: " and the text on one line,
    without "|" (the fields of an entry are joined with it for the hash, seal_verify.link), cut to fit."""
    text = " ".join(str(text or "").replace("|", "/").split())
    return (PLATFORM + text)[:REASON_MAX]


def by_platform(rows):
    """The deletions by the platform in a chain's entries (dicts): [(case number or None for the whole survey,
    reason without "platform: ")]."""
    return [(e.get("ref") if e.get("kind") == "deleted" else None, e["reason"][len(PLATFORM):])
            for e in rows if e.get("kind") in ("deleted", "closed") and (e.get("reason") or "").startswith(PLATFORM)]


def field_event(survey, kind, reason=""):
    """The fieldwork started ("fieldstarted", lifecycle.start) or was closed ("fieldclosed", lifecycle.close, not a
    pause): an entry, and the chain is stamped as soon as this is committed (_stamp_after_commit), not at the next
    hourly round. So the DFN dates what the chain held at the start and at the end of the fieldwork."""
    if has_chain(survey):
        _append(db.session.connection(), survey.uid, kind, reason=str(reason or "")[:20])
        db.session.info.setdefault(STAMP_NOW, set()).add(survey.uid)


def prereg_frozen(survey, digest, name):
    """The pre-registration `name` was frozen with the SHA-256 `digest` of its ZIP (app/prereg.py; concept
    "Vereinheitlichung der Befragungsexporte", E10 B): an entry, so the chain says which material and which survey
    file belonged to the start of the fieldwork. Stamped with the entry "fieldstarted" that follows (field_event)."""
    if has_chain(survey):
        _append(db.session.connection(), survey.uid, "prereg", digest_of=digest, reason=str(name)[:REASON_MAX])
        db.session.info.setdefault(STAMP_NOW, set()).add(survey.uid)


STAMP_NOW = "seal_stamp_now"  # a Session.info key: the chains to stamp in a thread once the transaction is committed


@event.listens_for(Session, "after_commit")
def _stamp_after_commit(session):
    uids = session.info.pop(STAMP_NOW, None)
    if uids:
        try:
            # in rounds (SEAL_MERKLE) only these chains, alone and at once: the others wait for the next round
            anchor_now(uids) if merkle_on() else anchor_in_background()
        except Exception:  # never let a stamp stop a commit; the hourly round catches up
            current_app.logger.exception("stamping after closing the fieldwork failed")


def amend_changed(survey, reason):
    """After a change past the session (geolocation.coarsen): an entry for every case whose data changed."""
    if not survey.seal:
        return 0
    conn, n = db.session.connection(), 0
    rows = db.session.execute(sa.select(Response).where(Response.survey_id == survey.id, Response.source == "real",
                                                        Response.is_preview.is_(False)).order_by(Response.id))
    for (r,) in rows:
        db.session.refresh(r)
        latest = _latest(conn, r.id)
        if latest is not None and _seal(conn, survey.uid, r, latest, reason):
            n += 1
    return n


def amend_written(response_id, reason):
    """After a change of one participation written past the session (webservice.flush stores the values of a call
    after the request): an entry if the case is sealed and what its hash covers changed. Commits nothing."""
    r = db.session.get(Response, response_id, populate_existing=True)
    if r is None or not _sealed(r):
        return None
    uid = db.session.scalar(sa.select(Survey.uid).where(Survey.id == r.survey_id, Survey.seal.is_(True)))
    if uid is None:
        return None
    conn = db.session.connection()
    latest = _latest(conn, r.id)
    return _seal(conn, uid, r, latest, reason) if latest is not None else None


# --- reading the chain ----------------------------------------------------------------------------------------

def entries(uid):
    return db.session.execute(sa.select(TABLE).where(TABLE.c.survey_uid == uid).order_by(TABLE.c.seq)).all()


def anchors(uid):
    return db.session.scalars(sa.select(SealAnchor).where(SealAnchor.survey_uid == uid).order_by(SealAnchor.seq)).all()


def export_map(survey):
    """{participation id: (case number, last content hash)} for the columns seal_seq and seal_hash."""
    out = {}
    for e in db.session.execute(sa.select(TABLE.c.response_id, TABLE.c.seq, TABLE.c.ref, TABLE.c.kind, TABLE.c.content)
                                .where(TABLE.c.survey_uid == survey.uid, TABLE.c.response_id.is_not(None))
                                .order_by(TABLE.c.seq)):
        out[e.response_id] = (_case(e), e.content)
    return out


def chain(survey, url=None):
    """The chain as the download holds it (seal_verify.FORMAT). url: where the platform serves this chain (its
    seal.json under /verify/), so that seal_verify.py can compare an unstamped end of the file with it (S2); not part
    of any hash."""
    import base64

    out = {"format": seal_verify.FORMAT, "survey": survey.uid, "title": survey.title, "created": _now(),
           "tsa": tsa_url() or None}
    if url:
        out["url"] = url
    return {**out,
            "entries": [{"seq": e.seq, "kind": e.kind, "ref": e.ref, "status": e.status, "content": e.content,
                         "reason": e.reason, "at": e.at, "prev": e.prev, "digest": e.digest} for e in entries(survey.uid)],
            "anchors": [{"seq": a.seq, "digest": a.digest, "tsa": a.tsa, "time": a.time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "token": base64.b64encode(a.token).decode("ascii"), **({"path": a.path} if a.path else {})}
                        for a in anchors(survey.uid)]}


def chain_url(survey):
    """The public address of a survey's chain (seal.json under /verify/, by the code of its newest entry, which never
    changes), or None without a chain or without a survey number."""
    if getattr(survey, "id", None) is None:
        return None
    head = db.session.execute(sa.select(TABLE.c.seq, TABLE.c.digest).where(TABLE.c.survey_uid == survey.uid)
                              .order_by(TABLE.c.seq.desc()).limit(1)).first()
    if head is None:
        return None
    from app import brand

    return f"{brand.public_base()}/verify/{survey.id}-{head.seq}-{head.digest[:PREFIX]}/seal.json"


def readme(survey, data):
    """The README of the download, in the language of the person downloading."""
    from app.lang import english

    fingerprint = next(iter(seal_verify.TRUSTED_ROOTS))
    stamped = data["anchors"][-1] if data["anchors"] else None
    merkled = any(a.get("path") for a in data["anchors"])
    if english():
        return (f"Seal chain: {survey.title}\n\n"
                f"seal.json holds the {len(data['entries'])} entries of the chain of this survey"
                + (f", stamped up to entry {stamped['seq']} at {stamped['time']} (UTC) by {stamped['tsa']}.\n" if stamped
                   else ", not stamped yet (the timestamp service did not answer).\n")
                + "\nCheck the chain and its timestamps (Python 3.9 or later, pip install cryptography):\n\n"
                "    python seal_verify.py seal.json\n\n"
                "and a data file of the export (columns seal_seq and seal_hash) against it:\n\n"
                "    python seal_verify.py seal.json data.csv\n\n"
                "Every case of the chain must be in the data file or have a deletion record. A file exported without\n"
                "unfinished cases or without speeders lacks those by design; the report names the status of every\n"
                "case missing. A file of completed participations only (such as the package for Zenodo) is checked\n"
                "with --completed; the verdict then holds for the cases in the file:\n\n"
                "    python seal_verify.py --completed seal.json data.csv\n\n"
                "The hash of a case covers its answers, status, language, end and questionnaire version and the\n"
                "values external sections and web services handed back; not the categories of open answers (the\n"
                "researchers' coding, which changes while they work). Its salt is not exported, so the check of a\n"
                "data file shows the completeness and origin of its cases (which cases, which deletions), not\n"
                "whether the answer values in it are unchanged; the platform checks those against the hashes itself.\n\n"
                "Entries after the last timestamp are compared with the chain on the platform (address in seal.json,\n"
                "field url, if it lies on one of Askurious's own hosts; another installation is named with\n"
                "--server URL); offline, the check goes up to the stamped state only.\n\n"
                f"The timestamps lead to the DFN-Verein Community Root CA 2022, SHA-256 {fingerprint}.\n"
                + ("A timestamp with a field path stamps the root of a Merkle tree over the chains of one round;\n"
                   "the path leads from the entry's hash to that root (MERKLE_RULE in seal_verify.py). Its other\n"
                   "hashes are blinded and name no other survey.\n" if merkled else "")
                + "seal_verify.py describes every check at its top.\n"
                + "The checker is licensed under the MIT License; its full notice is included in seal_verify.py.\n")
    return (f"Siegelkette: {survey.title}\n\n"
            f"seal.json enthält die {len(data['entries'])} Einträge der Kette dieser Befragung"
            + (f", gestempelt bis Eintrag {stamped['seq']} um {stamped['time']} (UTC) von {stamped['tsa']}.\n" if stamped
               else ", noch ohne Zeitstempel (der Zeitstempeldienst hat nicht geantwortet).\n")
            + "\nKette und Zeitstempel prüfen (Python 3.9 oder neuer, pip install cryptography):\n\n"
            "    python seal_verify.py seal.json\n\n"
            "und eine Datendatei des Exports (Spalten seal_seq und seal_hash) dagegen:\n\n"
            "    python seal_verify.py seal.json daten.csv\n\n"
            "Jeder Fall der Kette muss in der Datendatei stehen oder einen Löschvermerk haben. Eine Datei ohne\n"
            "nicht beendete Fälle oder ohne Speeder lässt diese mit Absicht aus; der Bericht nennt zu jedem\n"
            "fehlenden Fall seinen Status. Eine Datei nur mit abgeschlossenen Teilnahmen (wie das Paket für\n"
            "Zenodo) wird mit --completed geprüft; das Ergebnis gilt dann für die Fälle in der Datei:\n\n"
            "    python seal_verify.py --completed seal.json daten.csv\n\n"
            "Der Hashwert eines Falls umfasst Antworten, Status, Sprache, Ende und Fragebogenversion sowie die\n"
            "Werte, die externe Abschnitte und Web Services zurückgegeben haben; nicht die Kategorien offener\n"
            "Antworten (die Kodierung der Forschenden, die sich bei der Arbeit ändert). Sein Salz wird nicht\n"
            "exportiert: Die Prüfung einer Datendatei zeigt deshalb Vollständigkeit und Herkunft der Fälle (welche\n"
            "Fälle, welche Löschungen), nicht, ob die Antwortwerte darin unverändert sind; diese prüft die\n"
            "Plattform selbst gegen die Hashwerte.\n\n"
            "Einträge nach dem letzten Zeitstempel gleicht das Prüfprogramm mit der Kette auf der Plattform ab\n"
            "(Adresse in seal.json, Feld url, wenn sie auf einem Host von Askurious selbst liegt; eine andere\n"
            "Installation wird mit --server URL genannt); ohne Verbindung prüft es nur bis zum gestempelten Stand.\n\n"
            f"Die Zeitstempel führen zur DFN-Verein Community Root CA 2022, SHA-256 {fingerprint}.\n"
            + ("Ein Zeitstempel mit dem Feld path stempelt die Wurzel eines Merkle-Baums über die Ketten einer\n"
               "Runde; der Pfad führt vom Hashwert des Eintrags zu dieser Wurzel (MERKLE_RULE in seal_verify.py).\n"
               "Seine übrigen Hashwerte sind verblendet und verraten keine andere Befragung.\n" if merkled else "")
            + "Was seal_verify.py prüft, steht am Anfang der Datei (Englisch).\n"
            + "Das Prüfprogramm steht unter der MIT-Lizenz; der vollständige Lizenztext steht in seal_verify.py.\n")


def summary(survey):
    """For the tab "Export": {"entries", "cases", "deleted", "anchored" (seq), "anchor_time", "head", "cite",
    "status" (the last check against the data, SealStatus)}."""
    rows = entries(survey.uid)
    cases, gone = seal_verify.state([dict(e._mapping) for e in rows])
    last = db.session.scalars(sa.select(SealAnchor).where(SealAnchor.survey_uid == survey.uid)
                              .order_by(SealAnchor.seq.desc(), SealAnchor.id.desc()).limit(1)).first()
    # the code to cite: the newest stamped entry, so the check page confirms it at once
    cited = cite_code(survey, last.seq) if last else None
    closed = [r.seq for r in rows if r.kind == "fieldclosed"]
    return {"entries": len(rows), "cases": len(cases), "deleted": len(gone), "head": rows[-1].seq if rows else 0,
            "anchored": last.seq if last else 0, "anchor_time": last.time if last else None, "cite": cited,
            # the cited state covers the latest close of the fieldwork (E8)
            "cite_closed": bool(last and closed and closed[-1] <= last.seq and survey.status == Survey.CLOSED),
            # the last check against the data as stored (siegelkette-konzept.md E1); not checked inside the request:
            # a missing or old one is started in the background (audit 2026-10-10, P5)
            "status": last_status(survey) if rows else None}


RECORD_COLUMNS = (Response.id, Response.status, Response.language, Response.qversion, Response.completed_at,
                  Response.answers, Response.external)  # what record() reads


def _data(survey, rows):
    """The participations stored now against the chain (entries as dicts): {"present", "problems": [[kind, value]]}.
    Kinds: "missing" (case gone without a deletion record), "changed" (case whose data changed past the app),
    "unsealed" (a participation that ended without an entry). Reads every participation once; no timestamps."""
    cases, gone = seal_verify.state(rows)
    by_response = export_map(survey)
    salts = {rid: (salt, rec) for rid, salt, rec in db.session.execute(
        sa.select(TABLE.c.response_id, TABLE.c.salt, TABLE.c.rec).where(
            TABLE.c.survey_uid == survey.uid, TABLE.c.response_id.is_not(None)).order_by(TABLE.c.seq))}
    present, problems = set(), []
    # the columns record() reads, not whole participations (layout, paradata, coding, quota …; audit 2026-10-10, P5)
    for r in db.session.execute(sa.select(*RECORD_COLUMNS).where(
            Response.survey_id == survey.id, Response.source == "real", Response.is_preview.is_(False))
            .order_by(Response.id)):
        if r.id not in by_response:
            if r.status in ENDED:
                problems.append(["unsealed", r.id])
            continue
        case, digest = by_response[r.id]
        present.add(case)
        salt, rec = salts[r.id]
        if content(record(r, rec), salt) != digest:
            problems.append(["changed", case])
    problems += [["missing", n] for n in sorted(set(cases) - gone - present)]
    problems.sort(key=lambda p: ({"missing": 0, "changed": 1, "unsealed": 2}[p[0]], p[1]))
    return {"present": len(present), "problems": problems}


def _links(data):
    """The chain's own problems without checking the signatures of its timestamps (cheap; the hourly check and
    /verify/): links, numbering, and every timestamp row naming an entry of this chain."""
    result = seal_verify.check_chain({**data, "anchors": []})
    by_seq = {e["seq"]: e["digest"] for e in data["entries"]}
    extra = [f"timestamp of entry {a['seq']}: not a hash of this chain" for a in data["anchors"]
             if by_seq.get(a["seq"]) != a["digest"]]
    return list(result["problems"]) + extra


PROBLEMS_KEPT = 50  # problems a SealStatus row keeps (the counts are always whole)


def _keep(survey, chain_problems, data):
    """Store the result of a check (SealStatus) and return the row; commits nothing."""
    from app.models import SealStatus

    row = db.session.get(SealStatus, survey.uid) or SealStatus(survey_uid=survey.uid)
    kinds = [p[0] for p in data["problems"]]
    row.checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    row.chain_ok, row.data_ok = not chain_problems, not data["problems"]
    row.present, row.missing = data["present"], kinds.count("missing")
    row.changed, row.unsealed = kinds.count("changed"), kinds.count("unsealed")
    row.problems = ([["chain", p] for p in chain_problems] + data["problems"])[:PROBLEMS_KEPT]
    if row.chain_ok and row.data_ok:
        row.notified = None  # whole again: a later break is told anew
    db.session.add(row)
    return row


def fingerprint(survey):
    """A cheap sign of change of what check_quick reads (audit 2026-10-10, S6/P5): the number, highest id and summed
    change counters (Response.version, raised by every change through the app) of the survey's real participations,
    and the newest entry of its chain. Reads one index row per participation, no answers."""
    count, top, versions = db.session.execute(
        sa.select(sa.func.count(Response.id), sa.func.max(Response.id), sa.func.sum(Response.version))
        .where(Response.survey_id == survey.id, Response.source == "real", Response.is_preview.is_(False))).one()
    head = db.session.scalar(sa.select(sa.func.max(TABLE.c.seq)).where(TABLE.c.survey_uid == survey.uid))
    return f"{count or 0}:{top or 0}:{versions or 0}:{head or 0}"


def check_quick(survey):
    """The chain's links and the stored data against it, without the signatures of the timestamps: what runs every
    hour and for /verify/. Stores and returns the SealStatus row (the caller commits)."""
    mark = fingerprint(survey)  # before reading: a change during the check makes the next round check again
    data = chain(survey)
    row = _keep(survey, _links(data), _data(survey, data["entries"]))
    row.fingerprint, row.looked_at = mark, row.checked_at
    return row


def last_status(survey):
    """The last check (SealStatus) as stored, or None, without checking inside the request (the tab "Export", audit
    2026-10-10, P5): a missing or older one than CHECK_EVERY is started in the background (check_soon)."""
    from app.models import SealStatus

    row = db.session.get(SealStatus, survey.uid)
    if row is None or datetime.now(timezone.utc).replace(tzinfo=None) - row.checked_at > timedelta(seconds=CHECK_EVERY):
        check_soon(survey.uid)
    return row


_soon = set()  # uids of chains a check_soon thread is checking


def check_soon(uid):
    """check_quick of one chain in a thread of its own (and tell_broken); nothing in tests or without
    SEAL_BACKGROUND, nothing while one runs for the same chain."""
    if current_app.testing or not current_app.config.get("SEAL_BACKGROUND", True):
        return
    with _cited_lock:
        if uid in _soon:
            return
        _soon.add(uid)
    app = current_app._get_current_object()

    def run():
        try:
            with app.app_context():
                survey = db.session.scalar(sa.select(Survey).where(Survey.uid == uid))
                if survey is not None:
                    row = check_quick(survey)
                    db.session.commit()
                    if not (row.chain_ok and row.data_ok):
                        tell_broken(survey, row)
        except Exception:
            app.logger.exception("check of the seal chain %s failed", uid)
        finally:
            with _cited_lock:
                _soon.discard(uid)

    threading.Thread(target=run, daemon=True, name="seal-check-one").start()


def status(survey, max_age=None):
    """The last check (SealStatus) of a survey with a chain; checked anew (and committed) if there is none or it is
    older than max_age seconds."""
    from app.models import SealStatus

    row = db.session.get(SealStatus, survey.uid)
    if row is None or (max_age is not None and datetime.now(timezone.utc).replace(tzinfo=None)
                       - row.checked_at > timedelta(seconds=max_age)):
        row = check_quick(survey)
        db.session.commit()
    return row


def problem_text(p):
    """A stored problem ([kind, value]) or one of the checker as a sentence in the language of the page. The checker
    (seal_verify.py) speaks English on its own; its sentences are translated here."""
    from app.lang import de_en

    kind, value = p if isinstance(p, (list, tuple)) else ("chain", p)
    if kind == "missing":
        return de_en("Fall {n} fehlt ohne Löschvermerk", "Case {n} is missing without a deletion record", n=value)
    if kind == "changed":
        return de_en("Fall {n}: Daten außerhalb der Plattform verändert", "Case {n}: data changed outside the platform",
                     n=value)
    if kind == "unsealed":
        return de_en("Teilnahme {id} ist beendet, aber nicht in der Kette",
                     "Participation {id} has ended but is not in the chain", id=value)
    text = str(value)
    for pattern, german in CHAIN_TEXTS:
        m = re.fullmatch(pattern, text)
        if m:
            return de_en(german, text.replace("{", "{{").replace("}", "}}"), **m.groupdict())
    return text


CHAIN_TEXTS = [  # the checker's sentences (seal_verify.check_chain) and their German
    (r"entry (?P<n>\d+): number (?P<x>\S+) out of order", "Eintrag {n}: Nummer {x} an falscher Stelle"),
    (r"entry (?P<n>\d+): does not follow the entry before", "Eintrag {n}: Folgt nicht auf den Eintrag davor"),
    (r"entry (?P<n>\d+): hash does not match its content", "Eintrag {n}: Hashwert passt nicht zum Inhalt"),
    (r"entry (?P<n>\d+): its time lies in the future", "Eintrag {n}: Seine Zeit liegt in der Zukunft"),
    (r"timestamp of entry (?P<n>\S+): not a hash of this chain",
     "Zeitstempel zu Eintrag {n}: Gehört nicht zu dieser Kette (der Eintrag fehlt oder wurde verändert)"),
    (r"timestamp of entry (?P<n>\S+): (?P<why>.+)", "Zeitstempel zu Eintrag {n} ungültig: {why}"),
]


def check(survey):
    """The chain against itself, its timestamps (signatures included) and the participations stored now ("Jetzt
    prüfen"): {"problems": [texts], "chain_problems", "data_problems" (texts), "summary", "present", "head",
    "head_seq", "kinds", "platform", "counts"}. A case stored without its entry, an entry whose case is gone without
    a deletion record, a case whose data changed past the app: each is a problem of the data. The result is kept
    (SealStatus, committed)."""
    data = chain(survey)
    result = seal_verify.check_chain(data, verified=stamps_checked())
    found = _data(survey, data["entries"])
    _keep(survey, list(result["problems"]), found)
    db.session.commit()
    chain_problems = [problem_text(p) for p in result["problems"]]
    data_problems = [problem_text(p) for p in found["problems"]]
    rows = data.get("entries") or []
    kinds = {}
    for e in rows:  # what the entries are: enabled, sealed (a case), amended, deleted, purged, closed
        kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
    return {"problems": chain_problems + data_problems, "chain_problems": chain_problems,
            "data_problems": data_problems, "summary": result, "present": found["present"],
            "head": rows[-1]["digest"] if rows else "", "head_seq": rows[-1]["seq"] if rows else 0, "kinds": kinds,
            "platform": by_platform(rows), "counts": counts(rows)}


# --- what a chain says in numbers (the tab "Export" and /verify/, siegelkette-konzept.md E3) ---------------------------

WAYS = ("self", "request", "consent", "legal", "retention", "platform", "live", "survey", "other")  # in this order
STATUSES = ("completed", "screenout", "quotafull", "excluded")
REQUEST = "request: "  # the start of a reason the researchers wrote themselves ("anderer Rechtsgrund")
CONSENT = "request_consent"  # the researchers delete a case without a valid consent (e.g. a minor)
WHY = ("wish", "consent", "other")  # what the researchers pick when they delete (surveys.erase_request, E4)
WHY_MIN, WHY_MAX = 10, 190  # characters of a reason in words, as for the platform


def request_reason(why, text=""):
    """The reason of a deletion by the researchers as it goes into the chain, or None if it is not valid: the
    person's request ("erasure_request", as before), no valid consent, or another legal ground in a few words
    ("request: " and the text on one line, without "|", like platform_reason). The words are public with the chain
    (seal.json, /verify/) and must name nobody; the form says so."""
    if why == "wish":
        return "erasure_request"
    if why == "consent":
        return CONSENT
    if why == "other":
        text = " ".join(str(text or "").replace("|", "/").split())
        if WHY_MIN <= len(text) <= WHY_MAX:
            return (REQUEST + text)[:REASON_MAX]
    return None


def way(reason):
    """The way of a deletion record from its reason: self (the person), request (the researchers, on the person's
    request), consent (the researchers: no valid consent), legal (the researchers, another legal ground in words),
    retention (a retention period), platform, live (a live run thrown away), survey (the survey deleted), other."""
    reason = reason or ""
    if reason.startswith(PLATFORM):
        return "platform"
    if reason == "erasure_self":
        return "self"
    if reason == CONSENT:
        return "consent"
    if reason.startswith(REQUEST):
        return "legal"
    if reason == "erasure_request":
        return "request"
    if reason in ("retention", "erasure_retention", "diary_pending"):  # diary.sweep: a fixed period, too
        return "retention"
    if reason.startswith("live_"):
        return "live"
    if reason == "survey_deleted":
        return "survey"
    return "other"


def reason_text(reason):
    """The words a deletion record gives as its reason (the platform's, or ones the researchers wrote), else ""."""
    reason = reason or ""
    for start in (PLATFORM, REQUEST):
        if reason.startswith(start):
            return reason[len(start):]
    return ""


def counts(rows):
    """What a chain (entries as dicts, in order) holds in numbers: {"cases", "by_status": {status: n} of every case
    that ended (deleted ones too), "deleted", "by_way": {way: n}, "remaining", "amended" (cases still there that
    changed after their end), "field": [(kind, time)], "reasons": [(case or None, way, words)] of the deletions that
    give their reason in words, "entries": {"total", "cases", "amended", "deleted", "events"} what the entries are: a
    chain holds more entries than cases, since deletion records and events (start and end of the fieldwork, switching
    the chain on, a retention period, the deletion of the survey) are entries too (Philipp, 2026-10-12)}."""
    status, gone, amended, field, reasons = {}, {}, set(), [], []
    for e in rows:
        kind, why = e.get("kind"), e.get("reason")
        if kind == "sealed":
            status[e["seq"]] = e.get("status")
        elif kind == "amended" and e.get("ref") in status:
            status[e["ref"]] = e.get("status")
            amended.add(e["ref"])
        elif kind == "deleted":
            gone.setdefault(e.get("ref"), way(why))
            if reason_text(why):
                reasons.append((e.get("ref"), way(why), reason_text(why)))
        elif kind in ("purged", "closed"):
            w = way(why) if kind == "closed" else "retention"
            for case in status:
                gone.setdefault(case, w)
            if reason_text(why):
                reasons.append((None, w, reason_text(why)))
        elif kind in ("fieldstarted", "fieldclosed", "prereg"):
            field.append((kind, e.get("at")))
    entries = {"total": len(rows), "cases": 0, "amended": 0, "deleted": 0, "events": 0}
    for e in rows:
        kind = e.get("kind")
        entries["cases" if kind == "sealed" else kind if kind in ("amended", "deleted") else "events"] += 1
    by_status, by_way = {}, {}
    for s in status.values():
        by_status[s] = by_status.get(s, 0) + 1
    for w in gone.values():
        by_way[w] = by_way.get(w, 0) + 1
    return {"cases": len(status), "by_status": by_status, "deleted": len(gone), "by_way": by_way,
            "remaining": len(status) - len(gone), "amended": len(amended - set(gone)), "field": field,
            "reasons": reasons, "entries": entries}


# --- timestamps -----------------------------------------------------------------------------------------------

def stamp(digest, timeout=TIMEOUT):
    """A timestamp of the DFN for a SHA-256 value (bytes): (reply as the service sent it, token, time). Raises on
    every failure; the token is checked before it is kept. timeout: as for requests (QUICK inside a request someone
    waits for)."""
    import requests

    from app import bounded

    url = tsa_url()
    if not url:
        raise seal_verify.Invalid("no timestamp service set (TSA_URL)")
    nonce = secrets.randbits(63)
    # the whole call within TSA_SECONDS (or the short wait inside a request), the reply at most TSA_MAX_BYTES (AU7)
    whole = min(TSA_SECONDS, sum(timeout) if isinstance(timeout, tuple) else timeout or TSA_SECONDS)
    reply = bounded.request("POST", url, max_bytes=TSA_MAX_BYTES, seconds=whole, via=requests.post,
                            data=seal_verify.request(digest, nonce), timeout=timeout,
                            headers={"Content-Type": "application/timestamp-query"})
    reply.raise_for_status()
    token = seal_verify.token_from_reply(reply.content)
    if seal_verify.info(token)["nonce"] != nonce:
        raise seal_verify.Invalid("the reply belongs to another request")
    when = seal_verify.verify(token, digest)
    return reply.content, token, when.replace(tzinfo=None)


def anchor(uid, timeout=TIMEOUT):
    """Stamp the newest entry of a chain if it is not stamped yet. Returns the new SealAnchor or None; commits. If
    the stamp covers the close of the fieldwork, the researchers get its check code by mail (tell_closed, E8).
    timeout: as for stamp (QUICK inside a request someone waits for)."""
    head = db.session.execute(sa.select(TABLE.c.seq, TABLE.c.digest).where(TABLE.c.survey_uid == uid)
                              .order_by(TABLE.c.seq.desc()).limit(1)).first()
    done = db.session.scalar(sa.select(sa.func.max(SealAnchor.seq)).where(SealAnchor.survey_uid == uid))
    if head is None or (done is not None and done >= head.seq):
        return None
    closed_now = db.session.scalar(sa.select(TABLE.c.id).where(
        TABLE.c.survey_uid == uid, TABLE.c.kind == "fieldclosed", TABLE.c.seq > (done or 0),
        TABLE.c.seq <= head.seq).limit(1)) is not None
    db.session.commit()  # no transaction held while the service answers
    _, token, when = stamp(bytes.fromhex(head.digest), timeout=timeout)
    row = SealAnchor(survey_uid=uid, seq=head.seq, digest=head.digest, tsa=tsa_url()[:200], token=token, time=when)
    db.session.add(row)
    db.session.commit()
    if closed_now:
        try:
            tell_closed(uid, row)
        except Exception:  # a mail never undoes a stamp
            current_app.logger.exception("check code at the close of the fieldwork not sent")
    return row


def stamp_at_check(survey):
    """Stamp the newest entry at "Jetzt prüfen" if it has no stamp yet, as the download does, with the short wait of a
    request (QUICK). The check itself needs no outside service (the stamps are checked offline); this only says why
    a chain is still unstamped (pre-test 2026-10-20, 30). Returns None, or a sentence in the language of the page:
    no service set, the service not reachable from the server (outgoing connections, e.g. on the Docker server), or
    an answer that is no valid stamp."""
    import requests

    from app import bounded
    from app.lang import de_en

    head = db.session.scalar(sa.select(sa.func.max(TABLE.c.seq)).where(TABLE.c.survey_uid == survey.uid))
    done = db.session.scalar(sa.select(sa.func.max(SealAnchor.seq)).where(SealAnchor.survey_uid == survey.uid))
    if head is None or (done is not None and done >= head):
        return None
    url = tsa_url()
    if not url:
        return de_en("Kein Zeitstempeldienst eingestellt (TSA_URL ist leer): Die Kette wird nicht gestempelt. Kette "
                     "und Daten sind trotzdem geprüft.",
                     "No timestamp service is set (TSA_URL is empty): the chain is not stamped. Chain and data were "
                     "checked all the same.")
    host = urlsplit(url).hostname or url
    try:
        anchor(survey.uid, timeout=QUICK)
        return None
    except (requests.HTTPError, bounded.TooLarge, seal_verify.Invalid, ValueError) as exc:
        db.session.rollback()
        current_app.logger.warning("timestamp of the seal chain %s at the check: bad answer: %s", survey.uid, exc)
        return de_en("Der Zeitstempeldienst ({host}) hat nicht mit einem gültigen Stempel geantwortet ({e}). Kette "
                     "und Daten sind trotzdem geprüft; das Stempeln wird später wiederholt.",
                     "The timestamp service ({host}) did not answer with a valid stamp ({e}). Chain and data were "
                     "checked all the same; stamping is tried again later.", host=host, e=str(exc)[:200])
    except OSError as exc:  # requests' ConnectionError and Timeout, a refused or blocked connection
        db.session.rollback()
        current_app.logger.warning("timestamp of the seal chain %s at the check: %s not reachable: %s",
                                   survey.uid, host, exc)
        return de_en("Der Zeitstempeldienst ({host}) ist vom Server aus nicht erreichbar; der Stempel steht deshalb "
                     "noch aus. Bitte die ausgehenden Verbindungen des Servers zu {host} (HTTPS, Port 443) prüfen "
                     "lassen. Kette und Daten sind trotzdem geprüft.",
                     "The timestamp service ({host}) cannot be reached from the server, so the stamp is still "
                     "missing. Please have the server's outgoing connections to {host} (HTTPS, port 443) checked. "
                     "Chain and data were checked all the same.", host=host)


def tell_closed(uid, row):
    """The fieldwork was closed and that state is stamped: the researchers get the check code by mail, so that it is
    kept outside the platform from the start (siegelkette-konzept.md E8). Returns the addresses mailed."""
    from app import brand, lang, mail, platform_erase
    from app.lang import de_en

    survey = db.session.scalar(sa.select(Survey).where(Survey.uid == uid))
    if survey is None or not mail.enabled():
        return []
    code = cite_code(survey, row.seq)
    link = brand.public_base() + "/verify/" + code
    told = []
    for name, email, user in platform_erase.recipients(survey):
        if not email:
            continue
        with mail.in_language(lang.of(user)):
            text = (de_en("Guten Tag {name},", "Hello {name},", name=name) + "\n\n"
                    + de_en("die Feldphase Ihrer Befragung „{t}“ ({c}) ist geschlossen, und der Zeitstempeldienst des "
                            "DFN hat den Stand der Siegelkette beim Feldende gestempelt. Der Prüfcode dieses Stands:",
                            "the fieldwork of your survey “{t}” ({c}) is closed, and the timestamp service of the DFN "
                            "has stamped the state of the seal chain at the close. The check code of this state:",
                            t=survey.title, c=survey.code) + "\n\n    " + code + "\n    " + link + "\n\n"
                    + de_en("Bitte bewahren Sie den Code außerhalb der Plattform auf und nennen Sie ihn im Methodenteil: "
                            "Unter dem Link sieht jede Person die Zahl der Fälle und Löschungen bis zu diesem Stand und "
                            "ob die Daten auf der Plattform heute dazu passen. Ein Code, den Sie selbst festhalten, "
                            "zeigt auch spätere Eingriffe an der Plattform vorbei an.",
                            "Please keep the code outside the platform and name it in your method section: under the "
                            "link anyone sees the number of cases and deletions up to this state and whether the data "
                            "on the platform still match it today. A code you keep yourself also shows later changes "
                            "made past the platform.") + mail.signature())
            mail.send(email, de_en("Prüfcode der Siegelkette: „{t}“", "Check code of the seal chain: “{t}”",
                                   t=survey.title), text)
        told.append(email)
    return told


def due():
    """The uids of chains whose newest entry is not stamped, oldest first."""
    heads = sa.select(TABLE.c.survey_uid, sa.func.max(TABLE.c.seq).label("head")).group_by(TABLE.c.survey_uid).subquery()
    done = sa.select(SealAnchor.survey_uid, sa.func.max(SealAnchor.seq).label("done")) \
        .group_by(SealAnchor.survey_uid).subquery()
    query = sa.select(heads.c.survey_uid).outerjoin(done, done.c.survey_uid == heads.c.survey_uid) \
        .where(sa.or_(done.c.done.is_(None), done.c.done < heads.c.head)).order_by(heads.c.survey_uid)
    return list(db.session.scalars(query))


STAMP_SOON = 600  # seconds: new entries wait at most about this long for their timestamp (E7), not an hour
_last_soon = None


def stamp_soon():
    """From the housekeeping's check every minute: every STAMP_SOON seconds per process, stamp the chains that have
    entries without a timestamp (in a thread). One stamp covers every entry before it, so the service gets at most
    one request per chain in that time. Not in tests: they call anchor and anchor_due directly."""
    global _last_soon
    if current_app.testing or not tsa_url() or not current_app.config.get("SEAL_BACKGROUND", True):
        return
    if _last_soon is not None and time.monotonic() - _last_soon < STAMP_SOON:
        return
    _last_soon = time.monotonic()
    if due():
        anchor_in_background()


# --- one timestamp per round for all chains (SEAL_MERKLE; konzept-merkle-zeitstempel-2026-10-16.md) ----------------
# The newest hashes of all chains with an unstamped end become the leaves of a Merkle tree (seal_verify.MERKLE_RULE),
# the DFN stamps its root once, and every chain keeps the path from its hash to the root. So the service gets one call
# per round however many surveys collect data, instead of one per chain. The tree hides the other chains: each leaf
# is first paired with a random value of its own (the first step of its path), the leaves are shuffled, a level of odd
# length gets a random node at a random place, and the tree always has MERKLE_DEPTH levels above the blinded leaves.
# A path therefore holds random-looking hashes of the same length for every chain, and nobody who knows the hashes of
# other chains (their seal.json is public) finds them in it, nor how many chains the round held.

MERKLE_DEPTH = 16  # levels above the blinded leaves: up to 65,536 chains a round with paths of the same length
ROUND_MAX = 20000  # chains in one round at most; the rest goes into the next one
ROUND_GAP = STAMP_SOON - 60  # seconds: a round younger than this (any process) lets the next wait


def merkle_on():
    return bool(current_app.config.get("SEAL_MERKLE", False))


def _inner(left, right):
    return hashlib.sha256(b"\x01" + left + right).digest()


def merkle_tree(heads, depth=MERKLE_DEPTH):
    """(root, [path of each head]) of a blinded tree over heads (hex SHA-256 values), by seal_verify.MERKLE_RULE:
    seal_verify.merkle_root(bytes.fromhex(heads[i]), paths[i]) == root for every i."""
    rng = secrets.SystemRandom()
    n = len(heads)
    if n == 0:
        raise ValueError("a tree needs at least one hash")
    depth = max(depth, (n - 1).bit_length())
    if depth + 1 > seal_verify.MERKLE_MAX_STEPS:
        raise ValueError("too many hashes for one tree")
    paths, level, at = [], [], list(range(n))
    for h in heads:
        blind = secrets.token_bytes(32)
        paths.append([{"side": "right", "hash": blind.hex()}])
        level.append(_inner(hashlib.sha256(b"\x00" + bytes.fromhex(h)).digest(), blind))
    order = list(range(n))
    rng.shuffle(order)  # position p holds the leaf of heads[order[p]]
    level = [level[i] for i in order]
    for p, i in enumerate(order):
        at[i] = p
    for _ in range(depth):
        if len(level) % 2:  # a random node at a random place, so that no side gives the size away
            place = rng.randrange(len(level) + 1)
            level.insert(place, secrets.token_bytes(32))
            at = [p + 1 if p >= place else p for p in at]
        for i, p in enumerate(at):
            paths[i].append({"side": "left" if p % 2 else "right", "hash": level[p ^ 1].hex()})
        level = [_inner(level[p], level[p + 1]) for p in range(0, len(level), 2)]
        at = [p // 2 for p in at]
    return level[0], paths  # 2**depth >= n: one node is left


def _due_heads(limit=ROUND_MAX):
    """[(uid, newest seq, its hash, newest stamped seq or 0)] of the chains whose newest entry is not stamped,
    the longest waiting first (the oldest stamp, a chain never stamped before all)."""
    heads = sa.select(TABLE.c.survey_uid, sa.func.max(TABLE.c.seq).label("head")).group_by(TABLE.c.survey_uid).subquery()
    done = sa.select(SealAnchor.survey_uid, sa.func.max(SealAnchor.seq).label("done"),
                     sa.func.max(SealAnchor.time).label("last")).group_by(SealAnchor.survey_uid).subquery()
    rows = db.session.execute(
        sa.select(heads.c.survey_uid, heads.c.head, TABLE.c.digest, done.c.done)
        .join(TABLE, sa.and_(TABLE.c.survey_uid == heads.c.survey_uid, TABLE.c.seq == heads.c.head))
        .outerjoin(done, done.c.survey_uid == heads.c.survey_uid)
        .where(sa.or_(done.c.done.is_(None), done.c.done < heads.c.head))
        .order_by(sa.case((done.c.last.is_(None), 0), else_=1), done.c.last, heads.c.survey_uid)
        .limit(limit)).all()
    return [(r.survey_uid, r.head, r.digest, r.done or 0) for r in rows]


def anchor_round(force=False):
    """One timestamp of the DFN for every chain whose newest entry is not stamped: the root of a Merkle tree over
    their newest hashes, and a SealAnchor with its path for each chain. Nothing if another process stamped a round less
    than ROUND_GAP seconds ago (force: all the same, the tests). Returns the number of chains stamped; commits. Chains
    whose stamp now covers the close of the fieldwork get their check code by mail, as with anchor (E8)."""
    if not force:
        last = db.session.scalar(sa.select(sa.func.max(SealAnchor.time)).where(SealAnchor.path.is_not(None)))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if last is not None and now - last < timedelta(seconds=ROUND_GAP):
            return 0
    rows = _due_heads()
    if not rows:
        return 0
    closed_now = _closed_now(rows)
    root, paths = merkle_tree([digest for _, _, digest, _ in rows])
    db.session.commit()  # no transaction held while the service answers
    _, token, when = stamp(root)
    tsa = tsa_url()[:200]
    made = [SealAnchor(survey_uid=uid, seq=seq, digest=digest, tsa=tsa, token=token, time=when, path=path)
            for (uid, seq, digest, _), path in zip(rows, paths)]
    db.session.add_all(made)
    db.session.commit()
    for row in made:
        if row.survey_uid in closed_now:
            try:
                tell_closed(row.survey_uid, row)
            except Exception:  # a mail never undoes a stamp
                current_app.logger.exception("check code at the close of the fieldwork not sent")
    return len(made)


def _closed_now(rows):
    """The uids among rows (as in anchor_round) with a close of the fieldwork after their last stamp."""
    span = {uid: (done, seq) for uid, seq, _, done in rows}
    uids, found = list(span), set()
    for start in range(0, len(uids), 500):
        for uid, seq in db.session.execute(sa.select(TABLE.c.survey_uid, TABLE.c.seq).where(
                TABLE.c.kind == "fieldclosed", TABLE.c.survey_uid.in_(uids[start:start + 500]))):
            if span[uid][0] < seq <= span[uid][1]:
                found.add(uid)
    return found


def anchor_now(uids):
    """Stamp these chains alone and at once, in a thread (the start and the close of the fieldwork in rounds,
    SEAL_MERKLE): their check code does not wait for the next round."""
    if not tsa_url() or not current_app.config.get("SEAL_BACKGROUND", True):
        return
    app, uids = current_app._get_current_object(), sorted(uids)

    def run():
        with app.app_context():
            for uid in uids:
                try:
                    anchor(uid)
                except Exception as exc:  # the next round takes it
                    db.session.rollback()
                    app.logger.warning("timestamp of the seal chain %s put off: %s", uid, exc)

    threading.Thread(target=run, daemon=True, name="seal-anchor-now").start()


def anchor_due():
    """Stamp the chains that need it and the pre-registrations without a timestamp; failures wait for the next run.
    With SEAL_MERKLE one round for all chains (anchor_round), else one call per chain, at most BATCH."""
    if not tsa_url():
        return 0
    if merkle_on():
        try:
            n = anchor_round()
        except Exception as exc:  # the service is down or answers nonsense: next round
            db.session.rollback()
            current_app.logger.warning("timestamp round of the seal chains put off: %s", exc)
            n = 0
        from app import prereg

        prereg.stamp_missing()
        return n
    n = 0
    for uid in due()[:BATCH]:
        try:
            if anchor(uid):
                n += 1
        except Exception as exc:  # the service is down or answers nonsense: next hour
            db.session.rollback()
            current_app.logger.warning("timestamp of the seal chain %s put off: %s", uid, exc)
            break
    from app import prereg

    prereg.stamp_missing()
    return n


_running = threading.Lock()


def anchor_in_background():
    """anchor_due in a thread of its own (the housekeeping runs inside a request, the service may take seconds)."""
    if not tsa_url() or not current_app.config.get("SEAL_BACKGROUND", True) or _running.locked():
        return
    app = current_app._get_current_object()

    def run():
        if not _running.acquire(blocking=False):
            return
        try:
            with app.app_context():
                anchor_due()
        except Exception:
            app.logger.exception("timestamps of the seal chains failed")
        finally:
            _running.release()

    threading.Thread(target=run, daemon=True, name="seal-anchor").start()



# --- the hourly check against the stored data (siegelkette-konzept.md E1) ---------------------------------------------

CHECK_EVERY = 3300  # seconds: a chain looked at longer ago is due (a little under the hourly round)
CHECK_FULL = 86400  # seconds: an unchanged chain (same fingerprint) is still checked in full once a day, for a change
#                     made past the app that raises no change counter
CHECK_BATCH = 50  # chains looked at per round at most, the longest unlooked first
_checking = threading.Lock()


def check_due():
    """Check the chains whose last look is due against the data stored now (check_quick) and tell the researchers
    and the operators of a new break (tell_broken). A chain whose survey has not changed since its last check (same
    fingerprint: a closed survey, one without new participations) is only looked at, not read again, unless its last
    full check is older than CHECK_FULL (audit 2026-10-10, S6/P5). Returns the number checked; every survey commits
    by itself."""
    from app.models import SealStatus

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff, full = now - timedelta(seconds=CHECK_EVERY), now - timedelta(seconds=CHECK_FULL)
    has = sa.select(TABLE.c.id).where(TABLE.c.survey_uid == Survey.uid).exists()
    looked = sa.func.coalesce(SealStatus.looked_at, SealStatus.checked_at)
    ids = db.session.scalars(
        sa.select(Survey.id).outerjoin(SealStatus, SealStatus.survey_uid == Survey.uid)
        .where(Survey.seal.is_(True), has, sa.or_(SealStatus.checked_at.is_(None), looked < cutoff,
                                                  SealStatus.checked_at < full))
        .order_by(sa.case((SealStatus.checked_at.is_(None), 0), else_=1), looked, Survey.id)
        .limit(CHECK_BATCH)).all()
    n = 0
    for sid in ids:
        survey = db.session.get(Survey, sid)
        try:
            kept = db.session.get(SealStatus, survey.uid)
            if (kept is not None and kept.fingerprint and kept.checked_at >= full
                    and kept.fingerprint == fingerprint(survey)):
                kept.looked_at = now  # nothing changed: the result stands
                db.session.commit()
                continue
            row = check_quick(survey)
            db.session.commit()
            n += 1
            if not (row.chain_ok and row.data_ok):
                tell_broken(survey, row)
        except Exception:  # one survey must not stop the others; it is due again next round
            db.session.rollback()
            current_app.logger.exception("check of the seal chain %s failed", survey.uid)
    return n


def tell_broken(survey, row):
    """Tell the researchers (mail, in their language) and the operators (Slack) once that a chain no longer holds
    against its data. Once per set of problems, also with several processes: the row's `notified` is set first, and
    only the process that set it sends. Returns True if this call told."""
    from app import mail, notify, platform_erase
    from app.lang import de_en
    from app.models import SealStatus

    key = hashlib.sha256(json.dumps(row.problems, sort_keys=True).encode()).hexdigest()
    done = db.session.execute(sa.update(SealStatus).where(
        SealStatus.survey_uid == survey.uid, sa.or_(SealStatus.notified.is_(None), SealStatus.notified != key))
        .values(notified=key))
    db.session.commit()
    if done.rowcount != 1:
        return False
    texts_de = [problem_text(p) for p in (row.problems or [])[:10]]
    notify.slack(f":warning: *Siegelkette gebrochen*: Befragung {notify.survey_ref(survey)}\n"
                 + "\n".join("> " + notify._plain(t) for t in texts_de))
    if not mail.enabled():
        return True
    from app import lang

    for name, email, user in platform_erase.recipients(survey):
        if not email:
            continue
        with mail.in_language(lang.of(user)):
            problems = "\n".join("- " + problem_text(p) for p in (row.problems or [])[:20])
            text = (de_en("Guten Tag {name},", "Hello {name},", name=name) + "\n\n"
                    + de_en("die stündliche Prüfung hat ergeben, dass die Siegelkette Ihrer Befragung „{t}“ ({c}) "
                            "nicht mehr zu den gespeicherten Daten passt:",
                            "the hourly check found that the seal chain of your survey “{t}” ({c}) no longer matches "
                            "the stored data:", t=survey.title, c=survey.code) + "\n\n" + problems + "\n\n"
                    + de_en("Das heißt: Daten wurden an der Plattform vorbei geändert oder entfernt. Askurious zeigt "
                            "das im Reiter „Export“ und auf der öffentlichen Prüfseite der Kette. Die Admins der "
                            "Plattform sind informiert.",
                            "This means that data were changed or removed past the platform. Askurious shows this "
                            "in the tab “Export” and on the public check page of the chain. The platform's admins "
                            "have been told.") + mail.signature())
            mail.send(email, de_en("Siegelkette Ihrer Befragung „{t}“ gebrochen",
                                   "Seal chain of your survey “{t}” broken", t=survey.title), text)
    return True


def sweep_in_background():
    """The hourly round of the housekeeping, in a thread of its own: stamp what is due (anchor_due, with a timestamp
    service) and check the chains against their data (check_due). Not in tests: they call both directly."""
    if current_app.testing or not current_app.config.get("SEAL_BACKGROUND", True):
        return
    anchor_in_background()
    if _checking.locked():
        return
    app = current_app._get_current_object()

    def run():
        if not _checking.acquire(blocking=False):
            return
        try:
            with app.app_context():
                check_due()
        except Exception:
            app.logger.exception("checks of the seal chains failed")
        finally:
            _checking.release()

    threading.Thread(target=run, daemon=True, name="seal-check").start()


# --- citing a state of the chain (public.verify, Philipp 2026-10-08) -------------------------------------------------
# A paper names one state of the chain by a short code: the survey's number, the entry's number and the first
# PREFIX hex characters of that entry's hash, "142-57-3f9a1c2e7b4d8a10" (64 bits: nobody finds another chain whose
# entry starts the same). The page /verify/<code> shows anyone, without an account, whether that state exists, holds,
# and which timestamp of the DFN covers it.

PREFIX = 16
# results of verify_cite per state of a chain: the entries and timestamps up to an entry never change, so a state is
# checked once per process, not on every call (audit 2026-10-09, K4: about 1.7 s CPU per call after 90 days)
_CITED = {}
_CITED_KEEP = 256
VERIFY_FRESH = 600  # seconds: /verify/ checks the data anew when the last check is older (E2)
_cited_lock = threading.Lock()


_STAMPS = {}  # every timestamp checked in this process: (its hash, token, trusted roots) -> time or the error
_STAMPS_KEEP = 50000  # about 300 bytes each


def stamps_checked():
    """The kept results of timestamp checks (seal_verify.check_chain, verified): a stamp's signature and certificates
    are checked once per process, not again at every new entry of a growing chain (audit 2026-10-10, S5: 3000 stamps
    took 1.1-1.9 s per call to /verify/ after each new case)."""
    if len(_STAMPS) > _STAMPS_KEEP:
        _STAMPS.clear()
    return _STAMPS


def cite_code(survey, seq=None):
    """The code of entry seq (default: the newest) of the survey's chain, or None without a chain."""
    rows = entries(survey.uid)
    row = next((r for r in rows if r.seq == seq), None) if seq else (rows[-1] if rows else None)
    return f"{survey.id}-{row.seq}-{row.digest[:PREFIX]}" if row else None


def parse_cite(code):
    """(survey id, entry number, hash prefix) of a code, or None."""
    import re

    m = re.fullmatch(r"\s*(\d{1,9})-(\d{1,9})-([0-9a-fA-F]{8,64})\s*", code or "")
    return (int(m.group(1)), int(m.group(2)), m.group(3).lower()) if m else None


def verify_cite(code):
    """What /verify/<code> shows: {"ok", "found", "survey", "seq", "digest", "entry_time", "cases", "deleted",
    "stamp": (seq, time) of the first DFN stamp covering the entry, "problems", "newer", "counts" (up to the entry),
    "counts_now", "cited_ok" (the cited state holds), "now" (the chain today and the data stored now against it),
    "gone" (the survey was deleted: {"at", "way", "why"}, else None)}; "ok" only if the cited state, the chain today
    and the data hold. found False if no chain has this state.

    The number in the code is looked up in seal_chain, which outlives the survey (L-1): the chain of a deleted survey
    is still found, with its title and the record of its deletion."""
    parsed = parse_cite(code)
    if parsed is None:
        return {"found": False}
    sid, seq, prefix = parsed
    survey, digest = _cited_survey(sid, seq, prefix)
    if survey is None:
        return {"found": False}
    newest = db.session.scalar(sa.select(sa.func.max(TABLE.c.seq)).where(TABLE.c.survey_uid == survey.uid))
    stamps = db.session.scalar(sa.select(sa.func.count()).select_from(SealAnchor).where(SealAnchor.survey_uid == survey.uid))
    key = (survey.uid, seq, digest, newest, stamps)
    with _cited_lock:
        kept = _CITED.get(key)
    if kept is None:
        out = _verify_state(survey, seq, prefix)
        if out.get("found"):
            with _cited_lock:
                if len(_CITED) >= _CITED_KEEP:
                    _CITED.pop(next(iter(_CITED)))
                _CITED[key] = {k: v for k, v in out.items() if k != "survey"}
    else:
        out = {**kept, "survey": survey}
    if not out.get("found"):
        return out
    # the chain as it is today and the data stored now against it (E2): not part of the kept result, the data change
    # without a new entry; checked again if the last check is older than VERIFY_FRESH
    st = status(survey, max_age=VERIFY_FRESH)
    data = {"chain_ok": st.chain_ok, "data_ok": st.data_ok, "present": st.present, "missing": st.missing,
            "changed": st.changed, "unsealed": st.unsealed, "checked_at": st.checked_at,
            "problems": [p for p in (st.problems or []) if p[0] in ("missing", "changed", "chain")][:10]}
    return {**out, "now": data, "cited_ok": out["ok"], "ok": out["ok"] and st.chain_ok and st.data_ok,
            "gone": _gone(survey.uid) if getattr(survey, "gone", False) else None}


def _cited_survey(sid, seq, prefix):
    """(the survey of a check code, the hash of its entry seq) or (None, None). The survey still there, or for a
    deleted one a stand-in with its uid and last title from seal_chain (id None: no participation is read for it).
    Several chains can have had the number (SQLite may give the number of a deleted survey again): the one whose
    entry seq starts with the prefix is meant. The entry and its prefix first: a wrong code costs a few rows, not the
    whole chain (K4)."""
    from types import SimpleNamespace

    live = db.session.get(Survey, sid)
    found = [(r.survey_uid, r.title) for r in db.session.execute(
        sa.select(CHAINS.c.survey_uid, CHAINS.c.title).where(CHAINS.c.survey_id == sid))]
    if live is not None and live.uid not in {uid for uid, _ in found}:
        found.append((live.uid, live.title))  # a chain older code began without its row in seal_chain
    for uid, title in found:
        digest = db.session.scalar(sa.select(TABLE.c.digest).where(TABLE.c.survey_uid == uid, TABLE.c.seq == seq))
        if not digest or not digest.startswith(prefix):
            continue
        if live is not None and live.uid == uid:
            return live, digest
        return SimpleNamespace(id=None, uid=uid, title=title, gone=True), digest
    return None, None


def _gone(uid):
    """The record that ended the chain of a deleted survey: {"at" (UTC), "way" (survey, platform), "why" (the
    platform's reason in words, else "")}, or None."""
    row = db.session.execute(sa.select(TABLE.c.at, TABLE.c.reason).where(TABLE.c.survey_uid == uid, TABLE.c.kind == "closed")
                             .order_by(TABLE.c.seq.desc()).limit(1)).first()
    if row is None:
        return None
    try:
        at = datetime.strptime(row.at[:19], "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        at = None
    return {"at": at, "way": way(row.reason), "why": reason_text(row.reason)}


def _verify_state(survey, seq, prefix):
    data = chain(survey)
    upto = [e for e in data["entries"] if e["seq"] <= seq]
    entry = upto[-1] if upto and upto[-1]["seq"] == seq else None
    if entry is None or not entry["digest"].startswith(prefix):
        return {"found": False}
    part = {**data, "entries": upto, "anchors": [a for a in data["anchors"] if a["seq"] <= seq]}
    result = seal_verify.check_chain(part, verified=stamps_checked())
    # a stamp of a later entry covers this one too: the later hash is built on it
    later = [a for a in data["anchors"] if a["seq"] >= seq]
    stamp = None
    for a in sorted(later, key=lambda a: a["seq"]):
        whole = {**data, "entries": [e for e in data["entries"] if e["seq"] <= a["seq"]], "anchors": [a]}
        checked = seal_verify.check_chain(whole, verified=stamps_checked())
        if not checked["problems"] and checked["last_anchor"]:
            stamp = checked["last_anchor"]
            break
    problems = [problem_text(p) for p in result["problems"]]
    return {"found": True, "ok": not problems and stamp is not None, "survey": survey, "seq": seq,
            "digest": entry["digest"], "entry_time": entry.get("at"), "cases": len(result["cases"]),
            "deleted": len(result["gone"]), "stamp": stamp, "problems": problems,
            "newer": len(data["entries"]) - len(upto),
            # the chain in numbers up to the cited entry and today (E3); the entries never change, so this is kept
            "counts": counts(upto), "counts_now": counts(data["entries"])}
