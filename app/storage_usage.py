"""Durable byte accounting beneath the shared storage admission lock.

The filesystem remains authoritative. A dirty row is persisted before a writer begins;
the next lock owner rebuilds it after a crash. Known writes update only touched paths.
An unexpected Python write quarantines that uid to physical counting rather than trusting
a stale counter. This audit hook is an accounting fallback, not a Python sandbox.
"""
import hashlib
import os
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, has_app_context

_local = threading.local()
_ready = set()
INTERVAL = 900


def _root():
    return Path(os.path.abspath(current_app.config["UPLOAD_FOLDER"])).parent


def _folders(uid):
    root = _root()
    return (Path(os.path.abspath(current_app.config["UPLOAD_FOLDER"])) / uid, root / "paper-scans" / uid,
            root / "interview-audio" / uid)


def _meta():
    path = _root() / ".storage-usage"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def _marker(uid):
    return _meta() / (hashlib.sha256(uid.encode()).hexdigest() + ".unknown")


def _disabled():
    return (_meta() / "disabled").exists()


def _disable():
    (_meta() / "disabled").touch()
    _erase_database()
    current_app.logger.warning("Storage accounting unavailable; using physical quota counting")


def _erase_database():
    # Derived paths must not be retained indefinitely when a corrupt catalogue can
    # no longer process deletions. Quarantined uids and the disabled marker stay safe.
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            (_meta() / ("usage.sqlite3" + suffix)).unlink(missing_ok=True)
        except OSError:
            current_app.logger.warning("Unavailable storage inventory could not yet be removed")


def _scope():
    return getattr(_local, "scopes", {}).get(str(_root()))


def start_scope():
    if not hasattr(_local, "scopes"):
        _local.scopes = {}
    _local.scopes.setdefault(str(_root()), {})


@contextmanager
def _database():
    connection = sqlite3.connect(_meta() / "usage.sqlite3", timeout=10)
    try:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS usage (uid TEXT PRIMARY KEY, total INTEGER NOT NULL, "
                               "dirty INTEGER NOT NULL, reserved INTEGER NOT NULL, reconciled REAL NOT NULL, "
                               "revision TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS files (uid TEXT NOT NULL, path TEXT NOT NULL, "
                               "size INTEGER NOT NULL, PRIMARY KEY(uid, path))")
            yield connection
    finally:
        connection.close()


def _files(path):
    if path.is_symlink() and path.is_dir():
        return  # match os.walk's existing no-follow policy for directory links
    if path.is_dir():
        for directory, _, names in os.walk(path):
            for name in names:
                yield from _files(Path(directory) / name)
    else:
        try:
            yield os.path.abspath(path), path.stat().st_size
        except FileNotFoundError:
            pass


def _rebuild(connection, uid):
    rows = [(uid, path, size) for folder in _folders(uid) for path, size in _files(folder)]
    return _store(connection, uid, rows)


def _store(connection, uid, rows):
    total = sum(row[2] for row in rows)
    connection.execute("DELETE FROM files WHERE uid=?", (uid,))
    connection.executemany("INSERT INTO files VALUES (?, ?, ?)", rows)
    connection.execute("INSERT OR REPLACE INTO usage VALUES (?, ?, 0, 0, ?, ?)",
                       (uid, total, time.time(), secrets.token_hex(16)))
    return total


def _total(connection, uid):
    row = connection.execute("SELECT total, dirty FROM usage WHERE uid=?", (uid,)).fetchone()
    scope = _scope()
    key = (str(_root()), os.getpid(), uid)
    if row is None or key not in _ready or (row[1] and (scope is None or uid not in scope)):
        total = _rebuild(connection, uid)
        _ready.add(key)
        return total
    return row[0]


def _update(connection, uid, paths):
    total = _total(connection, uid)
    # Keep only highest touched ancestors; a removed directory also removes its catalogue.
    selected, seen = [], set()
    for value in sorted(paths, key=len):
        if not any(str(parent) in seen for parent in Path(value).parents):
            selected.append(value)
            seen.add(value)
    for value in selected:
        # LIKE would interpret underscores and percent signs in real filenames.
        prefix = value + os.sep
        upper = value + chr(ord(os.sep) + 1)
        old = connection.execute("SELECT path, size FROM files WHERE uid=? AND path=? UNION ALL "
                                 "SELECT path, size FROM files WHERE uid=? AND path>=? AND path<?",
                                 (uid, value, uid, prefix, upper)).fetchall()
        rows = [(uid, path, size) for path, size in _files(Path(value))]
        connection.executemany("DELETE FROM files WHERE uid=? AND path=?", [(uid, p) for p, _ in old])
        connection.executemany("INSERT OR REPLACE INTO files VALUES (?, ?, ?)", rows)
        total += sum(r[2] for r in rows) - sum(size for _, size in old)
    connection.execute("UPDATE usage SET total=? WHERE uid=?", (max(0, total), uid))
    return max(0, total)


def begin(uid, size):
    if _disabled():
        return
    try:
        with _database() as connection:
            _total(connection, uid)
            connection.execute("UPDATE usage SET dirty=1, reserved=reserved+?, revision=? WHERE uid=?",
                               (max(0, size), secrets.token_hex(16), uid))
        start_scope()
        scope = _scope()
        scope.setdefault(uid, set())
    except (OSError, sqlite3.Error):
        _disable()


def finish():
    scope = _scope()
    try:
        if scope and not _disabled():
            with _database() as connection:
                for uid, paths in scope.items():
                    _update(connection, uid, paths)
                    connection.execute("UPDATE usage SET dirty=0, reserved=0, revision=? WHERE uid=?",
                                       (secrets.token_hex(16), uid))
    except (OSError, sqlite3.Error):
        _disable()
    finally:
        getattr(_local, "scopes", {}).pop(str(_root()), None)


def used(uid):
    from app import media

    if not current_app.config.get("STORAGE_USAGE_ENABLED", False) or _disabled() or _marker(uid).exists():
        return media._physical_storage_bytes(uid)
    try:
        with _database() as connection:
            scope = _scope()
            return _update(connection, uid, scope[uid]) if scope and uid in scope else _total(connection, uid)
    except (OSError, sqlite3.Error):
        _disable()
        return media._physical_storage_bytes(uid)


def push(uid, size):
    if not hasattr(_local, "frames"):
        _local.frames = {}
    frame = {"uid": uid, "size": max(0, int(size)), "paths": {}}
    _local.frames.setdefault(str(_root()), []).append(frame)
    try:
        if uid:
            begin(uid, frame["size"])
    except BaseException:
        pop(frame)
        raise
    return frame


def pop(frame):
    frames = _local.frames[str(_root())]
    assert frames[-1] is frame
    frames.pop()


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def pending(uids):
    """Other nested writers' yet-unwritten reservations; the current request's size is checked separately."""
    frames = getattr(_local, "frames", {}).get(str(_root()), [])
    return sum(max(0, frame["size"] - max(0, sum(_size(path) - before for path, before in frame["paths"].items())))
               for frame in frames[:-1] if frame["uid"] in uids)


def _location(value):
    if isinstance(value, int) or not isinstance(value, (str, bytes, os.PathLike)):
        return None
    path = Path(os.path.abspath(os.fsdecode(value)))
    for base in (Path(os.path.abspath(current_app.config["UPLOAD_FOLDER"])), _root() / "paper-scans", _root() / "interview-audio"):
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] != "hb":
            return relative.parts[0], str(path)
    return None


# The hook below sees every audit event of the process (imports, sockets, sqlite ...): the events it cares for as one
# set built once, the first test of each call (before: a new set per event; Abgleich 2026-10-15, T05-N-3). An open for
# reading ends at the test of its flags, before any path work; only writes go on to _location.
_EVENTS = frozenset({"open", "os.remove", "os.rename", "os.link", "os.symlink", "os.truncate", "shutil.rmtree"})


def _audit(event, args):
    if event not in _EVENTS or not has_app_context():
        return
    if not current_app.config.get("UPLOAD_FOLDER"):
        return
    if event == "open" and not (args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
        return
    paths = args[:2] if event in {"os.rename", "os.link", "os.symlink"} else args[:1]
    for value in paths:
        location = _location(value)
        if location is None:
            continue
        uid, path = location
        if event == "os.remove" and not os.path.lexists(path):
            continue  # a failed cleanup attempt changes no bytes
        if event == "open" and Path(path).parent.name == ".parts" and path.endswith(".lock"):
            continue  # _PartLock creates an empty coordination file, never writes payload bytes
        scope = _scope()
        if scope is not None and not _disabled():
            if uid not in scope:
                begin(uid, 0)
            scope.setdefault(uid, set()).add(path)
            frames = getattr(_local, "frames", {}).get(str(_root()), [])
            if frames and frames[-1]["uid"] == uid:
                frames[-1]["paths"].setdefault(path, _size(path))
        else:
            # Keep quarantined until maintenance with writers stopped. A writable file
            # handle may still grow after its opening audit event has returned.
            _marker(uid).touch()


def reconcile(limit=25):
    """Scan without blocking uploads; publish only if no writer changed that uid meanwhile."""
    from app.storage_room import writing

    if _disabled():
        _erase_database()
        return 0
    try:
        with _database() as connection:
            uids = [r[0] for r in connection.execute(
                "SELECT uid FROM usage WHERE reconciled < ? ORDER BY reconciled LIMIT ?",
                (time.time() - INTERVAL, limit))]
        published = 0
        for uid in uids:
            with _database() as connection:
                before = connection.execute("SELECT revision, dirty FROM usage WHERE uid=?", (uid,)).fetchone()
            if before is None or before[1]:
                continue  # an active or crashed writer is recovered by admission
            rows = [(uid, path, size) for folder in _folders(uid) for path, size in _files(folder)]
            with writing(None, 0, check=False), _database() as connection:
                after = connection.execute("SELECT revision, dirty FROM usage WHERE uid=?", (uid,)).fetchone()
                if after != before:
                    continue
                _store(connection, uid, rows)
                if not any(folder.exists() for folder in _folders(uid)):
                    connection.execute("DELETE FROM files WHERE uid=?", (uid,))
                    connection.execute("DELETE FROM usage WHERE uid=?", (uid,))
                    _marker(uid).unlink(missing_ok=True)
                published += 1
        return published
    except (OSError, sqlite3.Error):
        _disable()
        return 0


sys.addaudithook(_audit)
