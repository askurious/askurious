"""The Linux volume gate spans database transactions and commit/rollback file callbacks."""
import os
import subprocess
import sys
from pathlib import Path
import pytest
from sqlalchemy import event

from app.extensions import db

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='Linux flock used by the Docker server')


def available(app):
    path = Path(app.config['UPLOAD_FOLDER']).parent / '.storage-backup.lock'
    code = '''import fcntl,sys
with open(sys.argv[1],"a+b") as f:
 try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError: sys.exit(1)
'''
    return subprocess.run([sys.executable,'-c',code,str(path)], timeout=5).returncode == 0


def test_commit_file_callbacks_remain_protected(app):
    with app.app_context():
        db.session.execute(db.text('SELECT 1'))
        assert not available(app)
        session = db.session()
        observed = []
        def files(_session):
            observed.append(available(app))
        event.listen(session, 'after_commit', files)
        try:
            db.session.commit()
        finally:
            event.remove(session, 'after_commit', files)
        assert observed == [False]
        assert available(app)


def test_nested_rollback_does_not_release_outer_transaction(app):
    with app.app_context():
        db.session.execute(db.text('SELECT 1'))
        nested = db.session.begin_nested()
        db.session.execute(db.text('SELECT 1'))
        nested.rollback()
        assert not available(app)
        db.session.rollback()
        assert available(app)


def test_concurrent_writer_and_real_backup_protocol(tmp_path):
    import json
    import shutil
    root = Path(__file__).resolve().parents[2]
    proof = tmp_path / 'proof'
    proof.mkdir()
    files = {'storage_guard.py': root/'app/storage_guard.py',
             'storage_backup_lock.py': root/'tools/storage_backup_lock.py',
             'backup.sh': root/'deploy/docker/backup.sh',
             'snapshot_uploads.py': root/'deploy/docker/snapshot_uploads.py',
             'compose-env.sh': root/'deploy/docker/compose-env.sh',
             'proof.py': Path(__file__).with_name('storage_guard_probe.py')}
    for name, source in files.items():
        shutil.copy2(source, proof/name)
    result = subprocess.run([sys.executable,str(proof/'proof.py')],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['bash_backup_protocol_passed']


def test_a_request_waits_a_limited_time_for_the_backup_window(app):
    """Abgleich 2026-10-15 NB-4/T05-N-2: a request waited for the backup window without a limit (up to 90 s, nginx
    gave up after 60 s); now STORAGE_GATE_WAIT seconds, then StorageBusy (503). Work outside a request still waits."""
    import time

    from app import storage_guard

    path = Path(app.config['UPLOAD_FOLDER']).parent / '.storage-backup.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen([sys.executable, '-c', '''import fcntl,sys,time
f=open(sys.argv[1],"a+b"); fcntl.flock(f,fcntl.LOCK_EX); print("held",flush=True); time.sleep(3)''', str(path)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'held'
        app.config['STORAGE_GATE_WAIT'] = 0.3
        with app.test_request_context('/'):
            started = time.monotonic()
            with pytest.raises(storage_guard.StorageBusy):
                db.session.execute(db.text('SELECT 1'))
            assert time.monotonic() - started < 2
            db.session.rollback()
    finally:
        holder.wait(timeout=10)
