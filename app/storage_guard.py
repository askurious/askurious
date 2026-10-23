"""Keep ORM transactions and their file callbacks outside a consistent database/files backup window.

A request waits at most STORAGE_GATE_WAIT seconds for the window to end, then gets StorageBusy (503 with Retry-After;
the participants' page "please send again", participate._database_busy); before, it waited without a limit, up to the
backup's 90 s, while nginx gave up after 60 s and every worker thread hung (audit 2026-10-15, NB-4/T05-N-2). Work
outside a request (housekeeping, report jobs, transcription) still waits until the window ends."""
import os
import time
from pathlib import Path

from flask import current_app, has_app_context, has_request_context
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

LOCK_NAME = '.storage-backup.lock'
KEY = 'storage_backup_guard'


class StorageBusy(OperationalError):
    """The backup window did not end within STORAGE_GATE_WAIT: nothing of the request was stored."""

    def __init__(self):
        super().__init__('storage backup window', None, TimeoutError('storage backup window'))


def _share(handle, fcntl):
    if not has_request_context():
        fcntl.flock(handle, fcntl.LOCK_SH)
        return
    deadline = time.monotonic() + float(current_app.config.get('STORAGE_GATE_WAIT', 10))
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise StorageBusy() from None
            time.sleep(0.05)


@event.listens_for(Session, 'after_begin')
def protect_storage(session, transaction, connection):
    if os.name == 'nt' or not has_app_context() or KEY in session.info:
        return
    import fcntl

    root = Path(current_app.config['UPLOAD_FOLDER']).parent
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / LOCK_NAME).open('a+b')
    try:
        _share(handle, fcntl)
    except BaseException:
        handle.close()
        raise
    session.info[KEY] = handle


@event.listens_for(Session, 'after_transaction_end')
def release_storage(session, transaction):
    if transaction.parent is None:
        handle = session.info.pop(KEY, None)
        if handle is not None:
            handle.close()  # after_commit/after_rollback callbacks have already completed
