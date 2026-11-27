"""After a dump was played back (pg_restore, deploy/docker/BETRIEB.md "Rückweg nach einer Migration"): delete again what
was deleted since the dump (audit 2026-10-10, L-8, S3, S4). A dump holds the database as it was then; whatever was
deleted since would be back: participations (deleted by the person with the deletion code, on request, by a retention
period, by the platform), accounts (with e-mail address, password hash and ORCID iD), revoked read tokens of the data,
ended sign-in sessions; and the seal chains would end where they ended at the time of the dump: their deletion records
and timestamps, and the codes mailed out since, would be gone.

Two steps around the restore, with the app stopped:

    python reapply_erasures.py save             BEFORE pg_restore, while the database still holds the newer state
    python reapply_erasures.py apply            AFTER pg_restore: says what it would do and changes nothing
    python reapply_erasures.py apply --do       ... and does it

"save" writes instance/reapply-erasures.json (readable by the server user only; --file elsewhere): the ids of the
participations, the entries of the separated section, the surveys, the accounts, the read tokens, sign-in tokens,
sessions and passkeys there are now, the deleted accounts, every seal chain entry and timestamp, and the deletion counters
(models.Erasure); the ids of the links and accesses (LINKS), of the people of diary studies and of the entries of
invitation lists with their state; and what says whether a link or a sign-in still holds (STATE: blocked, renewed,
used, the status and sign-in version of an account, when its password changed, a digest of its locked second
factor). No answers, no addresses, no password hashes. "apply" compares the restored database with it:
  - the seal chains get back the entries and timestamps written since the dump (the same hashes, so codes and
    timestamps given out stay valid), and the deleted cases lose their salt and participation id again;
  - participations and entries of the separated section that the database holds again but that were gone before the
    restore are deleted again by the app's own way (app/erasure.py, erase_again): anchor examples of the coding guide
    that quote them, paper scans, quota counters, saved states, kept results and estimates follow as at the first
    deletion; a case the restored chain still names gets its deletion record there;
  - the deletion counters per survey, day and way go back up to what they were;
  - read tokens (data_token), sign-in tokens, sessions and passkeys that were gone are deleted again (a passkey
    removed since the dump signs nobody in again); so are agent tokens,
    interviewer links, dashboard and quota links, join, registration and founding links and invitations revoked,
    used up or withdrawn since the dump, and the people of a diary study who left since;
  - links and accounts that stayed get back what ended them since the dump: an interviewer link blocked or renewed,
    a link used, an account blocked or ended, sessions signed out (session_version). An account whose password or
    second factor changed since the dump does not get the old one back: the restored password or factor is removed
    and the account named, so a site admin hands out a new one-time link;
  - entries of invitation lists deleted since the dump go again, and those that opted out lose address and name
    again, with their waiting mails;
  - accounts deleted since the dump are deleted again by the app's own way (accounts.delete): surveys that were gone
    before the restore go with them, those that were still there are taken over by a site admin (the run names them);
  - surveys deleted since the dump are only named: delete them again in the app.

"apply" refuses (and changes nothing) when the file does not belong to the restored database (S3): a chain of the
database goes beyond the saved one or parts from it, the database holds participations or chain entries newer than
the file (the file is older than the dump: "save" was forgotten), or the file names another database or not one of
its surveys. "apply --do" then renames the file, so it is never applied twice.

"apply --do" runs with the app's code, which must fit the restored database (same migration); it starts no server and
migrates nothing. After a rollback over a migration that is the code from before: "deploy-docker.sh rollback
--no-start" first (BETRIEB.md). --tables-only is the way out with code that does not fit: everything on the tables
alone, as before 2026-10-11, without the app's way (it names the surveys whose coding guide or paper scans need a look,
and the deleted accounts, which then have to be deleted again in the app).

    docker compose run --rm --no-deps -T researcher python - save < reapply_erasures.py     (in ~/askurious/deploy/docker)

DATABASE_URL as the app reads it; without it the SQLite file instance/app.db.
"""
import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone

import sqlalchemy as sa

FILE = os.path.join("instance", "reapply-erasures.json")
CHUNK = 500
# gone since the dump: revoked, used, signed out or (passkey) removed by the person or a site admin
TOKENS = ("data_token", "auth_token", "login_session", "passkey")
# links and accesses whose row goes when they are revoked, withdrawn or used up: gone again as well
LINKS = ("agent_token", "interviewer_access", "board_share", "quota_share", "group_join_link", "sign_up_link",
         "org_found_link", "member_invite")
# of rows that stayed: the columns that say whether the link or the sign-in still holds, put back as saved
# (table: (key column, columns)); a column the table does not have is left out
STATE = {
    "interviewer_access": ("id", ("blocked", "revision", "token_hash", "expires_at")),
    "board_share": ("id", ("token", "token_box", "revoked", "expires_at")),
    "quota_share": ("id", ("token", "token_box", "revoked", "expires_at")),
    "group_join_link": ("id", ("uses", "expires_at")),
    "sign_up_link": ("id", ("uses", "expires_at")),
    "org_found_link": ("id", ("used_at", "org_id", "used_by_id", "expires_at")),
    "user_login": ("user_id", ("status", "block_reason", "valid_until", "session_version", "locked_until",
                               "hard_locked_at")),
}


class Refused(Exception):
    pass


def database_url():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    for prefix in ("postgres://", "postgresql://"):  # psycopg 3, as config.database_url
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url or "sqlite:///" + os.path.abspath(os.path.join("instance", "app.db"))


def database_name(url):
    """The name of the database (PostgreSQL) or the file (SQLite) of a URL: the file of another brand's database names
    another one (S3)."""
    url = sa.engine.make_url(url) if isinstance(url, str) else url
    return os.path.basename(url.database or "")


def _enc(value):
    if isinstance(value, datetime):
        return {"dt": value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"b64": base64.b64encode(bytes(value)).decode()}
    return value


def _dec(value):
    if isinstance(value, dict) and "dt" in value:
        return datetime.fromisoformat(value["dt"])
    if isinstance(value, dict) and "b64" in value:
        return base64.b64decode(value["b64"])
    return value


def _tables(engine):
    meta = sa.MetaData()
    names = set(sa.inspect(engine).get_table_names())
    meta.reflect(engine, only=[n for n in ("survey", "response", "once_value", "contact_entry", "split_session",
                                           "seal_entry", "seal_anchor", "erasure", "quota", "user", "deleted_account",
                                           "user_login", "survey_invite", "mail_outbox", "enrolment", "ai_chat_turn",
                                           "coding_memo", *TOKENS, *LINKS, *STATE) if n in names])
    return meta.tables


def _digest(value):
    import hashlib

    return hashlib.sha256(str(value).encode()).hexdigest()[:24] if value else ""


def _state(conn, tables):
    """{table: {key: {column: value}}} of STATE, as it is now."""
    out = {}
    for name, (key, columns) in STATE.items():
        if name not in tables:
            continue
        table = tables[name]
        have = [c for c in columns if c in table.c]
        out[name] = {str(row._mapping[key]): {c: _enc(row._mapping[c]) for c in have}
                     for row in conn.execute(sa.select(table.c[key], *[table.c[c] for c in have]))}
    return out


def _credentials(conn, tables):
    """{user id: {"password": when it last changed, "factor": digest of the locked second factor}}: enough to see that
    a password or factor changed since the dump, nothing to sign in with."""
    if "user_login" not in tables:
        return {}
    login = tables["user_login"]
    return {str(r.user_id): {"password": _enc(r.password_changed_at) if "password_changed_at" in login.c else None,
                             "factor": _digest(r.totp_box) if "totp_box" in login.c else ""}
            for r in conn.execute(sa.select(login))}


def _rows(conn, table):
    return [{k: _enc(v) for k, v in row._mapping.items() if k != "id"} for row in conn.execute(sa.select(table))]


def _ids(conn, tables, name):
    return {row[0] for row in conn.execute(sa.select(tables[name].c.id))} if name in tables else set()


def _utc(value):
    """A time of the database (naive UTC, or ISO text in the seal chain) as an aware UTC datetime; None if none."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def save(engine, path):
    tables = _tables(engine)
    with engine.connect() as conn:
        survey = tables.get("survey")
        data = {"format": 2,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "database": database_name(engine.url),
                "surveys": sorted(_ids(conn, tables, "survey")),
                "survey_uids": sorted(r[0] for r in conn.execute(sa.select(survey.c.uid))) if survey is not None else [],
                "responses": sorted(_ids(conn, tables, "response")),
                "contact_entries": sorted(_ids(conn, tables, "contact_entry")),
                "split_sessions": sorted(_ids(conn, tables, "split_session")),
                "users": sorted(_ids(conn, tables, "user")),
                "deleted_accounts": [{"user_id": r.user_id, "by": r.by, "deleted_at": _enc(r.deleted_at)} for r in
                                     conn.execute(sa.select(tables["deleted_account"]))]
                if "deleted_account" in tables else [],
                "tokens": {name: sorted(_ids(conn, tables, name)) for name in TOKENS if name in tables},
                "links": {name: sorted(_ids(conn, tables, name)) for name in LINKS if name in tables},
                "state": _state(conn, tables),
                "credentials": _credentials(conn, tables),
                "enrolments": sorted(_ids(conn, tables, "enrolment")),
                "survey_invites": {str(r.id): r.status for r in conn.execute(
                    sa.select(tables["survey_invite"].c.id, tables["survey_invite"].c.status))}
                if "survey_invite" in tables else None,
                "seal_entry": _rows(conn, tables["seal_entry"]) if "seal_entry" in tables else [],
                "seal_anchor": _rows(conn, tables["seal_anchor"]) if "seal_anchor" in tables else [],
                "erasure": _rows(conn, tables["erasure"]) if "erasure" in tables else []}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(f"Gesichert: {len(data['responses'])} Teilnahmen, {len(data['users'])} Konten, "
          f"{len(data['seal_entry'])} Einträge der Siegelketten, {len(data['erasure'])} Löschzähler -> {path}")
    return data


def _delete(conn, table, column, ids):
    ids = sorted(ids)
    for i in range(0, len(ids), CHUNK):
        conn.execute(table.delete().where(column.in_(ids[i:i + CHUNK])))


def refusals(conn, tables, data, url):
    """Why this file does not belong to the restored database (S3), as sentences; [] when it does. Applying a file
    that is older than the dump deleted every participation since the file, without a deletion record."""
    found = []
    if data.get("database") and url is not None and data["database"] != database_name(url):
        found.append(f"Die Datei stammt aus der Datenbank „{data['database']}“, eingespielt ist „{database_name(url)}“.")
    if "survey" in tables and data.get("survey_uids"):
        now = {r[0] for r in conn.execute(sa.select(tables["survey"].c.uid))}
        if now and not now & set(data["survey_uids"]):
            found.append("Keine Befragung der Datenbank steht in der Datei: sie gehört zu einer anderen Datenbank.")
    saved_at = _utc(data.get("saved_at"))
    if saved_at is not None and "response" in tables:
        response = tables["response"]
        newer = conn.execute(sa.select(sa.func.count(response.c.id)).where(sa.or_(
            response.c.started_at > saved_at.replace(tzinfo=None),
            *([response.c.last_active_at > saved_at.replace(tzinfo=None)] if "last_active_at" in response.c else [])
        ))).scalar() or 0
        if newer:
            found.append(f"Die Datenbank enthält {newer} Teilnahmen, die nach der Datei begonnen oder beantwortet wurden "
                         f"({data['saved_at']}): die Datei ist älter als der eingespielte Stand.")
    if "seal_entry" in tables:
        seal = tables["seal_entry"]
        saved = {}
        for row in data.get("seal_entry") or []:
            saved.setdefault(row["survey_uid"], {})[row["seq"]] = row["digest"]
        heads = conn.execute(sa.select(seal.c.survey_uid, sa.func.max(seal.c.seq)).group_by(seal.c.survey_uid)).all()
        for uid, head in heads:
            digest = conn.execute(sa.select(seal.c.digest).where(seal.c.survey_uid == uid, seal.c.seq == head)).scalar()
            if saved.get(uid, {}).get(head) != digest:
                found.append(f"Die Siegelkette {uid} der Datenbank (bis Eintrag {head}) passt nicht zum gesicherten "
                             f"Stand ({'bis Eintrag ' + str(max(saved[uid])) if uid in saved else 'dort keine Kette'}).")
        if saved_at is not None:
            latest = max((_utc(r[0]) for r in conn.execute(sa.select(seal.c.at)) if _utc(r[0])), default=None)
            if latest is not None and latest > saved_at:
                found.append(f"Die Siegelketten der Datenbank haben Einträge vom {latest.isoformat(timespec='seconds')},"
                             f" nach der Datei ({data['saved_at']}).")
    return found


def plan_and_apply(conn, tables, data, url=None, remover=None):
    """Everything apply does on the tables, inside the caller's transaction. Returns the report as a dict.
    remover(gone, entries, users, report): the participations, entries and accounts deleted again by the app's way
    (apply --do); without one they are only counted (the dry run) or deleted on the tables (--tables-only: "tables")."""
    report = {"refused": refusals(conn, tables, data, url), "responses": {}, "entries": 0, "sessions": 0,
              "surveys": [], "seal_added": 0, "seal_forgotten": 0, "anchors": 0, "erasure": {}, "quotas": [],
              "tokens": {}, "accounts": [], "accounts_named": [], "taken_over": [], "codings": [], "paper": [],
              "links": {}, "state": {}, "passwords": [], "factors": [], "invites": 0, "optouts": 0, "enrolments": 0}
    if report["refused"]:
        return report
    response = tables["response"]
    gone = {}  # survey id -> ids of participations deleted again
    restored = conn.execute(sa.select(response.c.id, response.c.survey_id)).all()
    kept_before = set(data["responses"])
    for rid, sid in restored:
        if rid not in kept_before:
            gone.setdefault(sid, set()).add(rid)
    gone_ids = {rid for ids in gone.values() for rid in ids}
    alive = {rid for rid, _ in restored} - gone_ids
    report["responses"] = {sid: len(ids) for sid, ids in gone.items()}
    report["surveys"] = sorted(_ids(conn, tables, "survey") - set(data["surveys"]))

    # the seal chains: entries since the dump back, in order, after the entry the restored chain ends with (that it
    # is one of the saved ones, refusals checked)
    if "seal_entry" in tables and data["seal_entry"]:
        seal = tables["seal_entry"]
        saved = {}
        for row in data["seal_entry"]:
            saved.setdefault(row["survey_uid"], {})[row["seq"]] = row
        now = {}
        for row in conn.execute(sa.select(seal.c.id, seal.c.survey_uid, seal.c.seq, seal.c.digest, seal.c.response_id)):
            now.setdefault(row.survey_uid, {})[row.seq] = row
        for uid, rows in saved.items():
            have = now.get(uid, {})
            head = max(have, default=0)
            for seq in sorted(s for s in rows if s > head):
                row = {k: _dec(v) for k, v in rows[seq].items()}
                if row.get("response_id") not in alive:
                    row["response_id"], row["salt"] = None, None  # a case the restore did not bring back
                conn.execute(seal.insert().values(**row))
                report["seal_added"] += 1
            for seq, entry in have.items():  # cases deleted since the dump lose salt and id again
                if seq in rows and rows[seq].get("response_id") is None and entry.response_id is not None:
                    conn.execute(seal.update().where(seal.c.id == entry.id).values(salt=None, response_id=None))
                    report["seal_forgotten"] += 1
    if "seal_anchor" in tables and data["seal_anchor"]:
        anchor = tables["seal_anchor"]
        have = {(r.survey_uid, r.seq, r.digest) for r in conn.execute(
            sa.select(anchor.c.survey_uid, anchor.c.seq, anchor.c.digest))}
        for row in data["seal_anchor"]:
            if (row["survey_uid"], row["seq"], row["digest"]) not in have:
                conn.execute(anchor.insert().values(**{k: _dec(v) for k, v in row.items()}))
                report["anchors"] += 1

    # the deletion counters back to what they said before the restore
    if "erasure" in tables and data["erasure"]:
        erasure = tables["erasure"]
        surveys = _ids(conn, tables, "survey")

        def sums(rows):
            out = {}
            for r in rows:
                key = (r["survey_id"], r["day"], r["way"])
                n = out.setdefault(key, [0, 0])
                n[0] += r["responses"] or 0
                n[1] += r["contacts"] or 0
            return out

        before = sums(data["erasure"])
        after = sums([dict(r._mapping) for r in conn.execute(sa.select(erasure))])
        for (sid, day, way), (resp, cont) in sorted(before.items()):
            have_r, have_c = after.get((sid, day, way), (0, 0))
            if sid in surveys and (resp > have_r or cont > have_c):
                conn.execute(erasure.insert().values(survey_id=sid, day=day, way=way, responses=max(resp - have_r, 0),
                                                     contacts=max(cont - have_c, 0)))
                report["erasure"][sid] = report["erasure"].get(sid, 0) + max(resp - have_r, 0)

    # read tokens revoked, sign-in tokens used, sessions ended since the dump: gone again (revoking removes the row)
    for name in TOKENS:
        if name in tables and name in (data.get("tokens") or {}):
            extra = _ids(conn, tables, name) - set(data["tokens"][name])
            if extra:
                _delete(conn, tables[name], tables[name].c.id, extra)
            report["tokens"][name] = len(extra)

    _links_and_state(conn, tables, data, report)
    _invite_lists(conn, tables, data, report)
    if "enrolment" in tables and data.get("enrolments") is not None:
        left = _ids(conn, tables, "enrolment") - set(data["enrolments"])
        if left and callable(remover) and "enrolment_id" in response.c:
            # those with participations go with them by the app's way (erase_again takes the whole person)
            left -= {r[0] for r in conn.execute(sa.select(response.c.enrolment_id).where(
                response.c.id.in_(sorted(gone_ids)), response.c.enrolment_id.is_not(None)))} if gone_ids else set()
        if left:
            _delete(conn, tables["enrolment"], tables["enrolment"].c.id, left)
        report["enrolments"] = len(left)

    entries = set()
    if "contact_entry" in tables:
        entries = _ids(conn, tables, "contact_entry") - set(data["contact_entries"])
        report["entries"] = len(entries)
    if "split_session" in tables:
        extra = _ids(conn, tables, "split_session") - set(data["split_sessions"])
        if extra:
            _delete(conn, tables["split_session"], tables["split_session"].c.id, extra)
        report["sessions"] = len(extra)
    users = sorted(_ids(conn, tables, "user") - set(data["users"])) if "users" in data else []

    if remover is not None and remover != "tables":
        remover(gone, entries, users, report)
        return report
    # the dry run and --tables-only: what needs a look by hand, as the app's way does not run
    report["accounts_named"] = users
    if "survey" in tables and gone:
        survey = tables["survey"]
        if "codings" in survey.c:
            report["codings"] = sorted(r[0] for r in conn.execute(sa.select(survey.c.id).where(
                survey.c.id.in_(sorted(gone)), survey.c.codings.is_not(None))) if r[0] is not None)
    if "channel" in response.c and gone_ids:
        report["paper"] = sorted({r[0] for r in conn.execute(sa.select(response.c.survey_id).where(
            response.c.id.in_(sorted(gone_ids)), response.c.channel == "paper"))})
    if "quota" in tables and gone:
        quota = tables["quota"]
        report["quotas"] = sorted({r[0] for r in conn.execute(
            sa.select(quota.c.survey_id).where(quota.c.survey_id.in_(sorted(gone))))})
    if remover == "tables":
        if gone_ids:
            seal = tables.get("seal_entry")
            if seal is not None:
                for i in range(0, len(sorted(gone_ids)), CHUNK):
                    part = sorted(gone_ids)[i:i + CHUNK]
                    report["seal_forgotten"] += conn.execute(seal.update().where(seal.c.response_id.in_(part))
                                                             .values(salt=None, response_id=None)).rowcount or 0
            if "once_value" in tables:
                _delete(conn, tables["once_value"], tables["once_value"].c.response_id, gone_ids)
            for name in ("ai_chat_turn", "coding_memo"):  # what the app's way takes along with a participation
                if name in tables and "response_id" in tables[name].c:
                    _delete(conn, tables[name], tables[name].c.response_id, gone_ids)
            _delete(conn, response, response.c.id, gone_ids)
        if entries:
            _delete(conn, tables["contact_entry"], tables["contact_entry"].c.id, entries)
    return report


def _links_and_state(conn, tables, data, report):
    """Links and accesses gone since the dump go again; of those that stayed, and of the accounts, what ended them
    since comes back (STATE). A password or second factor that changed since the dump is not restored to the old one:
    it is removed, and the account named."""
    for name in LINKS:
        if name in tables and name in (data.get("links") or {}):
            extra = _ids(conn, tables, name) - set(data["links"][name])
            if extra:
                _delete(conn, tables[name], tables[name].c.id, extra)
            report["links"][name] = len(extra)
    for name, saved in (data.get("state") or {}).items():
        if name not in tables or name not in STATE:
            continue
        table, key = tables[name], STATE[name][0]
        columns = [c for c in STATE[name][1] if c in table.c]
        changed = 0
        for row in conn.execute(sa.select(table.c[key], *[table.c[c] for c in columns])).all():
            before = saved.get(str(row._mapping[key]))
            if before is None:
                continue
            values = {}
            for c in columns:
                if c not in before:
                    continue
                was, now = _dec(before[c]), row._mapping[c]
                if c == "session_version":  # never back: a sign-out since the dump stays one
                    was = max(was or 0, now or 0)
                if was != now:
                    values[c] = was
            if values:
                conn.execute(table.update().where(table.c[key] == row._mapping[key]).values(**values))
                changed += 1
        report["state"][name] = changed
    if "user_login" in tables and data.get("credentials"):
        login = tables["user_login"]
        for row in conn.execute(sa.select(login)).all():
            before = data["credentials"].get(str(row.user_id))
            if before is None:
                continue
            if ("password_hash" in login.c and row.password_hash
                    and _dec(before.get("password")) != row._mapping.get("password_changed_at")):
                conn.execute(login.update().where(login.c.user_id == row.user_id).values(password_hash=None))
                report["passwords"].append(row.user_id)
            if "totp_box" in login.c and row.totp_box and before.get("factor") != _digest(row.totp_box):
                gone = {c: None for c in ("totp_box", "totp_step", "totp_backup") if c in login.c}
                conn.execute(login.update().where(login.c.user_id == row.user_id).values(**gone))
                report["factors"].append(row.user_id)


def _invite_lists(conn, tables, data, report):
    """Entries of invitation lists deleted since the dump go again; those that opted out since lose address and name
    again and get no mail that was still waiting."""
    saved = data.get("survey_invites")
    if saved is None or "survey_invite" not in tables:
        return
    invite = tables["survey_invite"]
    gone, opted = [], []
    for row in conn.execute(sa.select(invite.c.id, invite.c.status)).all():
        if str(row.id) not in saved:
            gone.append(row.id)
        elif saved[str(row.id)] == "optout" and row.status != "optout":
            opted.append(row.id)
    for i in range(0, len(opted), CHUNK):
        conn.execute(invite.update().where(invite.c.id.in_(opted[i:i + CHUNK])).values(
            status="optout", email="", name="", **({"pending_status": None} if "pending_status" in invite.c else {})))
    if "mail_outbox" in tables and "invite_id" in tables["mail_outbox"].c and (gone or opted):
        outbox = tables["mail_outbox"]
        for i in range(0, len(gone + opted), CHUNK):
            conn.execute(outbox.delete().where(outbox.c.invite_id.in_((gone + opted)[i:i + CHUNK]),
                                               *([outbox.c.sent_at.is_(None)] if "sent_at" in outbox.c else [])))
    if gone:
        _delete(conn, invite, invite.c.id, gone)
    report["invites"], report["optouts"] = len(gone), len(opted)


def _app_remover(data, after):
    """The participations, entries and accounts go by the app's own way (S4), in the app's session."""
    from app import erasure
    from app.extensions import db
    from app.models import ContactEntry, Response, Survey

    def remove(gone, entries, users, report):
        by_survey = {}
        for e in db.session.scalars(db.select(ContactEntry).where(ContactEntry.id.in_(sorted(entries)))):
            by_survey.setdefault(e.survey_id, []).append(e)
        for sid in sorted(set(gone) | set(by_survey)):
            survey = db.session.get(Survey, sid)
            ids = sorted(gone.get(sid, ()))
            rows = [r for i in range(0, len(ids), CHUNK) for r in db.session.scalars(
                db.select(Response).where(Response.id.in_(ids[i:i + CHUNK])).order_by(Response.id))]
            after.append(erasure.erase_again(survey, rows, by_survey.get(sid, [])))
        report["users"] = users  # accounts follow after the commit of the rest (accounts.delete commits itself)
    return remove


def _accounts_again(data, users, report):
    """Accounts deleted since the dump: deleted again by accounts.delete, as a site admin would (S4). Surveys that
    were gone before the restore go with them; those that were still there are taken over by the first site admin
    (as a deletion by an admin does), the run names them. Without an admin the accounts are only named."""
    from app import accounts
    from app.extensions import db
    from app.models import DeletedAccount, Survey, User

    saved_by = {r["user_id"]: r for r in data.get("deleted_accounts") or []}
    surveys_before = set(data["surveys"])
    for uid in users:
        user = db.session.get(User, uid)
        if user is None:
            continue
        admin = db.session.scalar(db.select(User).where(User.is_admin.is_(True), User.id.not_in(users))
                                  .order_by(User.id).limit(1))
        if admin is None:
            report["accounts_named"].append(uid)
            continue
        plan = accounts.plan(user)
        choices = {"confirm_data": "1"}
        for entry in plan["data"] + plan["running"]:
            sid = entry["survey"].id
            if sid not in surveys_before:
                choices[f"data_{sid}"] = "delete"
            else:
                report["taken_over"].append((sid, admin.id))
        accounts.delete(user, "admin", choices, admin=admin)
        row = db.session.scalar(db.select(DeletedAccount).where(DeletedAccount.user_id == uid)
                                .order_by(DeletedAccount.id.desc()).limit(1))
        if row is not None and uid in saved_by:  # who deleted it and when, as at the first time
            row.by = saved_by[uid]["by"] or row.by
            row.deleted_at = _dec(saved_by[uid]["deleted_at"]) or row.deleted_at
            db.session.commit()
        report["accounts"].append(uid)
    report["surveys"] = sorted(sid for sid in report["surveys"] if db.session.get(Survey, sid) is not None)


def code_fits(engine):
    """(True, version, head) when the database is at the migration of this code; the app's way needs that."""
    from app import migrations

    head = migrations.head()
    try:
        with engine.connect() as conn:
            current = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    except sa.exc.DBAPIError:
        current = None
    return current == head, current, head


def _done(path):
    """The file applied: renamed, so that no later restore uses it by mistake (S3)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = f"{path}.applied-{stamp}"
    os.replace(path, target)
    return target


def apply(engine, path, do=False, tables_only=False):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not do or tables_only:
        tables = _tables(engine)
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                report = plan_and_apply(conn, tables, data, engine.url, remover="tables" if do else None)
            except Exception:
                trans.rollback()
                raise
            if do and not report["refused"]:
                trans.commit()
            else:
                trans.rollback()
        if do and not report["refused"]:
            report["file"] = _done(path)
        _print(report, data, do)
        return report
    from flask import has_app_context

    if has_app_context():
        return _apply_with_app(path, data)
    fits, current, head = code_fits(engine)
    if not fits:
        report = {"refused": [f"Die Datenbank steht auf der Migration {current}, der Code hier auf {head}: Teilnahmen "
                              "und Konten gehen den Weg der App nur mit dem Code, der zur Datenbank passt. Nach einem "
                              "Rollback über eine Migration erst „deploy-docker.sh rollback --no-start“ (BETRIEB.md); "
                              "sonst --tables-only."]}
        _print(report, data, do)
        return report
    from app import create_app

    with create_app().app_context():
        return _apply_with_app(path, data)


def _apply_with_app(path, data):
    from app import erasure
    from app.extensions import db

    if not hasattr(erasure, "erase_again"):
        report = {"refused": ["Der Code hier ist älter als dieses Skript (ohne erasure.erase_again): mit --tables-only "
                              "ausführen."]}
        _print(report, data, True)
        return report
    after = []
    tables = _tables(db.engine)
    conn = db.session.connection()
    try:
        report = plan_and_apply(conn, tables, data, db.engine.url, remover=_app_remover(data, after))
    except Exception:
        db.session.rollback()
        raise
    if report["refused"]:
        db.session.rollback()
        _print(report, data, True)
        return report
    db.session.commit()
    for files in after:
        files()
    _accounts_again(data, report.pop("users", []), report)
    report["file"] = _done(path)
    _print(report, data, True)
    return report


def _print(report, data, do):
    if report.get("refused"):
        print(f"ABGEBROCHEN, nichts geändert. Die Datei ({data.get('saved_at')}) passt nicht zur eingespielten "
              "Datenbank:")
        for line in report["refused"]:
            print(f"  - {line}")
        print("Was tun: „save“ muss unmittelbar vor pg_restore laufen, mit der Datenbank, die zurückgespielt wird "
              "(BETRIEB.md, Rückweg nach einer Migration). Eine alte Datei nie nehmen: Sie löschte alle Teilnahmen seit "
              "ihr, ohne Löschvermerk. Ist das Zurückspielen schon geschehen und keine passende Datei da, die App erst "
              "nach Rücksprache wieder freigeben.")
        return
    print(f"Stand vor dem Zurückspielen: {data['saved_at']}." + ("" if do else " Probelauf: nichts geändert."))
    verb = "gelöscht" if do else "zu löschen"
    for sid in sorted(report["responses"]):
        counted = report["erasure"].get(sid, 0)
        warn = "  ACHTUNG: mehr als laut Löschzählern" if report["responses"][sid] > counted else ""
        print(f"  Befragung {sid}: {report['responses'][sid]} Teilnahmen {verb}"
              f" (laut Löschzähler seit dem Dump: {counted}){warn}")
    print(f"  Getrennt gespeicherte Angaben {verb}: {report['entries']}, offene Sitzungen darin: {report['sessions']}")
    print(f"  Siegelketten: {report['seal_added']} Einträge zurück, {report['seal_forgotten']} Fälle wieder ohne Salz,"
          f" {report['anchors']} Zeitstempel zurück")
    tokens = report.get("tokens") or {}
    print(f"  Lese-Tokens {verb}: {tokens.get('data_token', 0)}, Anmelde-Tokens: {tokens.get('auth_token', 0)},"
          f" Sitzungen: {tokens.get('login_session', 0)}, Passkeys: {tokens.get('passkey', 0)}")
    links = report.get("links") or {}
    if any(links.values()) or any((report.get("state") or {}).values()):
        print(f"  Links und Zugänge {verb}: " + (", ".join(f"{name} {n}" for name, n in sorted(links.items()) if n) or "0")
              + "; auf den Stand vor dem Zurückspielen gebracht (gesperrt, erneuert, genutzt, abgemeldet): "
              + (", ".join(f"{name} {n}" for name, n in sorted((report.get("state") or {}).items()) if n) or "0"))
    if report.get("invites") or report.get("optouts") or report.get("enrolments"):
        print(f"  Einladungslisten: {report.get('invites', 0)} Einträge {verb}, {report.get('optouts', 0)} Abmeldungen"
              f" wiederhergestellt; Personen von Tagebuchstudien {verb}: {report.get('enrolments', 0)}")
    for uid in report.get("passwords") or []:
        print(f"  ACHTUNG: Konto {uid} hat sein Passwort seit dem Dump geändert: das alte gilt nicht wieder"
              + (" (mit --do entfernt)." if not do else ", es ist entfernt. Bitte einen Einmal-Link ausstellen."))
    for uid in report.get("factors") or []:
        print(f"  ACHTUNG: Konto {uid} hat seinen zweiten Faktor seit dem Dump geändert oder entfernt: der alte gilt"
              " nicht wieder" + (" (mit --do entfernt)." if not do else ", er ist entfernt. Bitte neu einrichten lassen."))
    for uid in report["accounts"]:
        print(f"  Konto {uid} erneut gelöscht.")
    for sid, admin in report["taken_over"]:
        print(f"  Befragung {sid}: vom Konto des Admins {admin} übernommen (wie beim ersten Löschen durch einen Admin;"
              " prüfen, wer sie danach hatte).")
    for uid in report["accounts_named"]:
        print(f"  ACHTUNG: Konto {uid} wurde seit dem Dump gelöscht"
              + (": wird mit --do erneut gelöscht." if not do else ": in der App erneut löschen."))
    for sid in report["surveys"]:
        print(f"  ACHTUNG: Befragung {sid} wurde seit dem Dump gelöscht: in der App erneut löschen.")
    for sid in report["codings"]:
        print(f"  Befragung {sid}: Kodierleitfaden" + (" wird mit --do bereinigt." if not do else
                                                        " von Hand prüfen (Ankerbeispiele aus gelöschten Antworten)."))
    for sid in report["paper"]:
        print(f"  Befragung {sid}: Papierbögen" + (" werden mit --do bereinigt." if not do else
                                                   " von Hand prüfen (Scans gelöschter Teilnahmen)."))
    for sid in report["quotas"]:
        print(f"  Befragung {sid}: " + ("Quoten werden mit --do neu gezählt." if not do else
                                       "auf der Seite Quoten „Quoten neu zählen“."))
    if report.get("file"):
        print(f"Datei umbenannt: {report['file']} (löschen, sobald alles geprüft ist).")
    if not do:
        print("Mit --do ausführen.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=("save", "apply"))
    parser.add_argument("--file", default=FILE)
    parser.add_argument("--do", action="store_true", help="apply: change the database (default: a dry run)")
    parser.add_argument("--tables-only", action="store_true",
                        help="apply --do on the tables alone, without the app's way (code that does not fit)")
    args = parser.parse_args(argv)
    engine = sa.create_engine(database_url())
    if args.step == "save":
        save(engine, args.file)
        return 0
    report = apply(engine, args.file, do=args.do, tables_only=args.tables_only)
    return 1 if report.get("refused") else 0


if __name__ == "__main__":
    sys.exit(main())
