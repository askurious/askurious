"""What the participants' side does in the database: a recorder of the SQL statements of participant requests.

The participants' containers may run under a role of their own with only the rights in app/participant_grants.py
(audit 2026-10-18, CFG-04). This recorder sees every statement sent while a request of the participants' side
(hosts.side_of: participant or shared endpoints, and requests no endpoint matched) is being answered, and turns it
into (table, privilege) pairs the way PostgreSQL checks them:

- INSERT/UPDATE/DELETE need their privilege on the target and SELECT on it as well (RETURNING, WHERE);
- every other table named after FROM or JOIN needs SELECT;
- SELECT ... FOR UPDATE / FOR SHARE needs UPDATE on the locked table;
- INSERT ... ON CONFLICT DO UPDATE needs UPDATE;
- the cache triggers of app/data_revision.py run with the caller's rights: a write to response writes
  response_revision, a write to block or item updates survey.

Two uses: tests/conftest.py checks the tests of the participants' folders against the list (`violations`), and
PARTICIPANT_SQL_RECORD=<file> writes every pair seen with the endpoints that needed it (to measure the list anew).
Requests that carry a researcher's sign-in are left out (see _side).
"""
import json
import os
import re
import threading

from sqlalchemy import event
from sqlalchemy.engine import Engine

WRITE = ("INSERT", "UPDATE", "DELETE")
_NAME = r'"?([A-Za-z_][A-Za-z0-9_]*)"?'
_INSERT = re.compile(r"^\s*(?:WITH\b.*?\)\s*)?INSERT\s+(?:OR\s+\w+\s+)?INTO\s+" + _NAME, re.I | re.S)
_UPDATE = re.compile(r"^\s*(?:WITH\b.*?\)\s*)?UPDATE\s+(?:OR\s+\w+\s+)?" + _NAME, re.I | re.S)
_DELETE = re.compile(r"^\s*(?:WITH\b.*?\)\s*)?DELETE\s+FROM\s+" + _NAME, re.I | re.S)
_READ = re.compile(r"\b(?:FROM|JOIN)\s+" + _NAME, re.I)
_CONFLICT_UPDATE = re.compile(r"\bON\s+CONFLICT\b.*?\bDO\s+UPDATE\b", re.I | re.S)
# what the cache triggers of app/data_revision.py write with the rights of whoever wrote the row
TRIGGERS = {
    "response": {("response_revision", "SELECT"), ("response_revision", "INSERT"), ("response_revision", "UPDATE")},
    "block": {("survey", "SELECT"), ("survey", "UPDATE")},
    "item": {("survey", "SELECT"), ("survey", "UPDATE"), ("block", "SELECT")},
}

_lock = threading.Lock()
state = {"on": False, "seen": {}, "known": None}


def known_tables():
    if state["known"] is None:
        from app import models  # noqa: F401
        from app.extensions import db

        state["known"] = set(db.metadata.tables) | {"alembic_version"}
    return state["known"]


def pairs(statement, compiled=None):
    """The (table, privilege) pairs PostgreSQL checks for one statement."""
    known = known_tables()
    found = set()
    text = statement or ""
    target = None
    for verb, pattern in (("INSERT", _INSERT), ("UPDATE", _UPDATE), ("DELETE", _DELETE)):
        m = pattern.match(text)
        if m and m.group(1) in known:
            target = m.group(1)
            found |= {(target, verb), (target, "SELECT")}
            if verb == "INSERT" and _CONFLICT_UPDATE.search(text):
                found.add((target, "UPDATE"))
            found |= TRIGGERS.get(target, set())
            break
    for name in _READ.findall(text):
        if name in known:
            found.add((name, "SELECT"))
    # tables whose SELECT is granted for some columns only: every column the statement names must be one of them
    from app.participant_grants import columns

    for table, _ in list(found):
        allowed_columns = columns(table)
        if allowed_columns is not None:
            for column in re.findall(r'(?<![A-Za-z0-9_])"?' + table + r'"?\."?([A-Za-z_][A-Za-z0-9_]*)', text):
                found.add((table, "SELECT:" + column))
    lock = getattr(getattr(compiled, "statement", None), "_for_update_arg", None)
    if lock is not None:
        stmt = compiled.statement
        froms = stmt.get_final_froms() if hasattr(stmt, "get_final_froms") else getattr(stmt, "froms", [])
        locked = {t.name for t in froms if getattr(t, "name", None) in known}
        found |= {(name, "UPDATE") for name in locked}
    return found


def _side():
    from flask import has_request_context, request

    if not has_request_context():
        return None
    from flask import session

    try:
        signed_in = session.get("_user_id")
    except AssertionError:  # the session is being opened (app/cookies.py reads the old cookie's rows): none yet
        signed_in = None
    if signed_in:
        # a researcher's sign-in never reaches the participants' containers: their session cookie is signed with a
        # salt (and key) of its own and bound to the participants' address (app/cookies.py, app/hosts.py). A test that
        # opens a survey page with the researcher's client stands for the single-address mode, not for them
        return None
    from app import hosts

    side = hosts.side_of(request.endpoint)
    if side in (hosts.PARTICIPANT, hosts.SHARED) or request.endpoint is None:
        return request.endpoint or request.path
    return None


@event.listens_for(Engine, "before_cursor_execute")
def _record(_conn, _cursor, statement, _parameters, context, _executemany):
    if not state["on"]:
        return
    try:
        where = _side()
    except RuntimeError:
        return
    if where is None:
        return
    if getattr(context, "execution_options", {}).get("sqlite_only"):
        return  # sent on SQLite only (the floor of deleted ids, app/models.py), never under the participants' role
    for pair in pairs(statement, getattr(context, "compiled", None)):
        with _lock:
            state["seen"].setdefault(pair, set()).add(where)


def start():
    with _lock:
        state["seen"] = {}
        state["on"] = True


def stop():
    with _lock:
        state["on"] = False
        return dict(state["seen"])


def violations(seen):
    """The pairs outside app/participant_grants.py, each with the endpoints that needed it."""
    from app.participant_grants import allowed, columns

    def ok(table, privilege):
        if privilege.startswith("SELECT:"):
            return privilege[len("SELECT:"):] in (columns(table) or ())
        return privilege in allowed(table)

    return {pair: sorted(where) for pair, where in seen.items() if not ok(*pair)}


def write(seen, path):
    """Adds what one test saw to the file of a measuring run (one file per xdist worker)."""
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    target = f"{path}.{worker}.json"
    old = {}
    if os.path.exists(target):
        with open(target, encoding="utf-8") as handle:
            old = json.load(handle)
    for (table, privilege), where in seen.items():
        old.setdefault(table, {}).setdefault(privilege, [])
        old[table][privilege] = sorted(set(old[table][privilege]) | set(where))
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(old, handle, indent=1, sort_keys=True)
