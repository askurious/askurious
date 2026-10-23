"""Real Linux flock proof with concurrent transaction and database/file snapshot; artificial data only."""
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from flask import Flask
from sqlalchemy import create_engine,text,event
from sqlalchemy.orm import Session

code_root=Path(__file__).parent
spec=importlib.util.spec_from_file_location('storage_guard_proof',code_root/'storage_guard.py')
guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
folder=Path(tempfile.mkdtemp(prefix='storage-guard-proof-'))
try:
    app=Flask('storage-proof');app.config['UPLOAD_FOLDER']=str(folder/'uploads')
    engine=create_engine('sqlite:///'+str(folder/'answers.db'))
    recording=folder/'audio.webm';recording.write_bytes(b'old')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE answer (value TEXT)'))
        connection.execute(text("INSERT INTO answer VALUES ('old')"))
    def gate(timeout,release=None,max_hold=3):
        args=[sys.executable,str(code_root/'storage_backup_lock.py'),str(folder),'--timeout',str(timeout)]
        if release is not None:
            args += ['--release-file',str(release),'--max-hold',str(max_hold)]
        return subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    with app.app_context(),Session(engine) as session:
        session.execute(text('SELECT 1'))
        blocked=gate(.15);assert blocked.wait(timeout=3)!=0
        nested=session.begin_nested();nested.rollback()
        blocked=gate(.15);assert blocked.wait(timeout=3)!=0
        observed=[]
        def committed(_session):
            blocked=gate(.15);observed.append(blocked.wait(timeout=3)!=0)
        event.listen(session,'after_commit',committed)
        session.commit();assert observed==[True]
    release=folder/'release'
    backup=gate(3,release)
    assert backup.stdout.readline().strip()=='READY'
    done=threading.Event();failure=[]
    def writer():
        try:
            with app.app_context(),Session(engine) as session:
                session.execute(text("UPDATE answer SET value='new'"))
                event.listen(session,'after_commit',lambda _s:recording.write_bytes(b'new'))
                session.commit()
        except BaseException as error:
            failure.append(type(error).__name__)
        finally:
            done.set()
    thread=threading.Thread(target=writer);thread.start()
    assert not done.wait(.25)
    shutil.copy2(folder/'answers.db',folder/'snapshot.db')
    shutil.copy2(recording,folder/'snapshot.webm')
    release.touch();backup.stdin.close()
    assert backup.wait(timeout=3)==0
    assert done.wait(5) and not failure
    thread.join()
    with sqlite3.connect(folder/'snapshot.db') as connection:
        assert connection.execute('SELECT value FROM answer').fetchone()[0]=='old'
    assert (folder/'snapshot.webm').read_bytes()==b'old' and recording.read_bytes()==b'new'
    expiring=gate(3,folder/'never-released',max_hold=.2)
    assert expiring.stdout.readline().strip()=='READY' and expiring.wait(timeout=3)!=0
    with app.app_context(),Session(engine) as session:
        session.execute(text('SELECT 1'));session.rollback()
    # Exercise the real Bash orchestration as well; Docker and the database use artificial stand-ins.
    home=folder/'backup-home';(home/'instance').mkdir(parents=True)
    (home/'instance/audio.webm').write_bytes(b'artificial-recording')
    bin_dir=folder/'bin';bin_dir.mkdir()
    scripts=folder/'scripts';scripts.mkdir()
    backup_script=(code_root/'backup.sh').read_text().replace('$HOME','$STORAGE_PROOF_HOME')
    backup_script=backup_script.replace('./alarm.sh','bash ./alarm.sh').replace('./offsite.sh','bash ./offsite.sh')
    (scripts/'backup.sh').write_text(backup_script)
    (scripts/'snapshot_uploads.py').write_bytes((code_root/'snapshot_uploads.py').read_bytes())
    # backup.sh sources it (the selection of the isolated stack); without a .env it chooses the plain one
    (scripts/'compose-env.sh').write_bytes((code_root/'compose-env.sh').read_bytes())
    for name in ('alarm.sh','offsite.sh'):
        p=scripts/name;p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o755)
    check_gate=folder/'check_gate.py'
    check_gate.write_text('''import fcntl,sys
with open(sys.argv[1],"a+b") as f:
 try: fcntl.flock(f,fcntl.LOCK_SH|fcntl.LOCK_NB)
 except BlockingIOError: sys.exit(0)
 sys.exit(1)
''')
    docker=bin_dir/'docker'
    docker.write_text('''#!/bin/bash
set -e
case "$*" in
 *storage_backup_lock.py*)
  for value in "$@"; do case "$value" in *:/backup-control) control=${value%:/backup-control} ;; esac; done
  signal=${@: -1}; signal=${signal##*/}
  exec python "$PROOF_CODE_ROOT/storage_backup_lock.py" "$STORAGE_PROOF_HOME/instance" --release-file "$control/$signal" --max-hold 5 ;;
 "compose ps --services --status running") printf 'researcher\\nparticipant-a\\nparticipant-b\\nhousekeeping\\ncompute\\n' ;;
 *"FROM pg_database"*) echo askurious ;;
 *pg_dump*) python "$PROOF/check_gate.py" "$STORAGE_PROOF_HOME/instance/.storage-backup.lock"; echo PGDMP-fake ;;
 *"pg_restore -l"*) grep -q '^PGDMP-fake' ;;
 *"python - /app/instance /snap"*)
  python "$PROOF/check_gate.py" "$STORAGE_PROOF_HOME/instance/.storage-backup.lock"
  python "$PROOF_CODE_ROOT/snapshot_uploads.py" "$STORAGE_PROOF_HOME/instance" "$STORAGE_PROOF_HOME/backups/uploads" 14 ;;
 *) exit 0 ;;
esac
''');docker.chmod(0o755)
    import os
    environment={**os.environ,'PATH':str(bin_dir)+':'+os.environ['PATH'],
                 'STORAGE_PROOF_HOME':str(home),'PROOF':str(folder),'PROOF_CODE_ROOT':str(code_root)}
    # /tmp is noexec in the production container. Export the artificial Docker command as a Bash function.
    body=docker.read_text().split('case "$*" in',1)[1].rsplit('esac',1)[0]
    function='docker() { case "$*" in'+body.replace('exit 0','return 0')+'esac; }; export -f docker; exec bash "$1"'
    completed=subprocess.run(['bash','-c',function,'proof',str(scripts/'backup.sh')],env=environment,capture_output=True,text=True,timeout=15)
    assert completed.returncode==0,(completed.stdout,completed.stderr)
    assert (home/'askurious-status/backup-ok').is_file()
    snapshots=list((home/'backups/uploads').glob('[0-9]*'))
    assert len(snapshots)==1 and (snapshots[0]/'audio.webm').read_bytes()==b'artificial-recording'
    assert not (snapshots[0]/'.storage-backup.lock').exists()
    print(json.dumps({'result':'PASS','nested_transaction_protected':True,'commit_callbacks_protected':True,
                      'writer_waited_for_backup':True,'database_and_audio_snapshot_match':True,
                      'timeout_releases_gate':True,'bash_backup_protocol_passed':True}))
finally:
    engine.dispose()
    shutil.rmtree(folder)
