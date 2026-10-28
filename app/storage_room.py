"""Serialize quota admission and durable writes on the shared instance filesystem.

The exclusive OS lock is shared by threads, processes and containers mounting the same instance directory.
Dirty inventory and outstanding reservations are persisted before writing. Closed file changes update the
inventory; after a crash the next owner rebuilds from actual files. Unknown writers retain physical counting.
The OS releases the lock on process death. Do not hold it while decoding images or doing network work.
"""
import errno
import functools
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, has_app_context

from app import storage_usage
from app.lang import de_en

_held = threading.local()
WAIT = 30  # seconds a writer waits for the storage room before "Gerade werden viele Dateien gespeichert"


class StorageFull(ValueError):
    """Capacity is unavailable; existing upload handlers catch ValueError, other routes receive a clear 507."""


def register(app):
    def full(error):
        from flask import Response

        return Response(str(error), status=507, mimetype="text/plain", headers={"Cache-Control": "no-store"})

    app.register_error_handler(StorageFull, full)


@contextmanager
def writing(survey, size, check=True):
    from app import media

    root = Path(current_app.config["UPLOAD_FOLDER"]).parent
    root.mkdir(parents=True, exist_ok=True)
    key = str(root.resolve())
    active = getattr(_held, "roots", set())
    handle = None
    if key not in active:
        handle = (root / ".storage-room.lock").open("a+b")
        try:
            if os.name == "nt" and os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + WAIT
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise StorageFull(de_en("Gerade werden viele Dateien gespeichert. Bitte in einem Moment erneut versuchen.",
                                               "Many files are being stored right now. Please try again in a moment."))
                    time.sleep(.02)
            _held.roots = active | {key}
        except BaseException:
            handle.close()
            raise
    frame = None
    try:
        storage_usage.start_scope()
        uid = getattr(survey, "uid", None)
        frame = storage_usage.push(uid, size)
        if check:
            media.check_room(survey, size)
        yield
    except OSError as error:
        if error.errno in (errno.ENOSPC, errno.EDQUOT):
            raise StorageFull(de_en("Auf dem Server ist gerade kein Platz für weitere Dateien. Bitte später erneut versuchen "
                                    "oder an einen Admin der Plattform wenden.",
                                    "There is no room for further files on the server right now. Please try again later "
                                    "or contact a platform admin.")) from error
        raise
    finally:
        if frame is not None:
            storage_usage.pop(frame)
        if handle is not None:
            try:
                storage_usage.finish()
            finally:
                _held.roots = active
                _unlock(handle)
                handle.close()


def _unlock(handle):
    """Release the lock before closing: Windows frees a lock left at closing only later, and under load the next
    writer then waited out its WAIT ("Gerade werden viele Dateien gespeichert"; full run 2026-10-28). fcntl frees it
    at closing anyway."""
    if os.name != "nt":
        return
    import msvcrt

    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:  # not held (any more): closing is enough
        pass


def held():
    root = Path(current_app.config["UPLOAD_FOLDER"]).parent
    return str(root.resolve()) in getattr(_held, "roots", set())


def tracked(function):
    """Small file mutations without upload admission; the audit hook records their actual paths."""
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        if not has_app_context():
            return function(*args, **kwargs)
        with writing(None, 0, check=False):
            return function(*args, **kwargs)
    return wrapped


@tracked
def remove_file(path):
    os.remove(path)


@tracked
def remove_tree(path, **kwargs):
    import shutil

    shutil.rmtree(path, **kwargs)
