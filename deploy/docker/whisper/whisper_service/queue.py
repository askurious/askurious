"""Durable bounded queue. Audio lives only in an opaque, private spool directory."""
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import time
import threading
from contextlib import contextmanager

from . import estimates
from .checkpoints import MAX_BYTES


class QueueFull(Exception):
    pass


class Queue:
    def __init__(self, settings):
        self.settings = settings
        self.wake = threading.Event()
        self.folder = settings.state
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.files = self.folder / "files"
        self.files.mkdir(exist_ok=True, mode=0o700)
        self.database = self.folder / "jobs.sqlite"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, options TEXT NOT NULL,
                    result TEXT, error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, created);
                DROP INDEX IF EXISTS jobs_active;
                CREATE TABLE IF NOT EXISTS timings (
                    settings TEXT PRIMARY KEY, rate REAL NOT NULL, samples INTEGER NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS phase_timings (
                    settings TEXT NOT NULL, stage TEXT NOT NULL, rate REAL NOT NULL,
                    samples INTEGER NOT NULL, updated REAL NOT NULL, PRIMARY KEY(settings,stage)
                );
            """)
            columns = {row[1] for row in db.execute('PRAGMA table_info(jobs)')}
            for column in ('duration', 'started'):
                if column not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {column} REAL')
            for column, definition in (('phases', "TEXT NOT NULL DEFAULT '{}'"), ('failures', 'INTEGER NOT NULL DEFAULT 0')):
                if column not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {column} {definition}')
        os.chmod(self.database, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def audio(self, job_id):
        return self.files / f"{job_id}.audio"

    def result_file(self, job_id):
        return self.files / f"{job_id}.result"

    def discard_files(self, job_id):
        for suffix in ("audio", "input", "result", "result.tmp", "progress", "progress.tmp", "complete", "complete.tmp",
                       "checkpoint", "checkpoint.tmp"):
            (self.files / f"{job_id}.{suffix}").unlink(missing_ok=True)

    def submit(self, stream, parameters):
        # room for the largest allowed part on top of the reserve, so a long queue never fills the disk
        if shutil.disk_usage(self.folder).free < self.settings.min_free_mb * 1024 * 1024 + self.settings.max_bytes:
            raise QueueFull()
        job_id = secrets.token_hex(16)
        destination = self.audio(job_id)
        canonical = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode())
        size = 0
        try:
            with destination.open("xb") as target:
                os.chmod(destination, 0o600)
                while chunk := stream.read(64 * 1024):
                    size += len(chunk)
                    if size > self.settings.max_bytes:
                        raise ValueError("upload_too_large")
                    digest.update(chunk)
                    target.write(chunk)
            if not size:
                raise ValueError("empty_audio")
            now = time.time()
            duration = estimates.duration(destination)
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                pending = db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
                if pending >= self.settings.max_pending:
                    raise QueueFull()
                db.execute("INSERT INTO jobs(id,fingerprint,state,created,updated,options,duration) VALUES(?,?,'queued',?,?,?,?)",
                           (job_id, digest.hexdigest(), now, now, canonical, duration))
            self.wake.set()  # Only after commit: a woken worker must be able to claim the job.
            return job_id
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def counts(self):
        with self.connect() as db:
            rows = db.execute("SELECT state,COUNT(*) FROM jobs WHERE state IN ('queued','running') GROUP BY state").fetchall()
        values = dict(rows)
        queued, running = values.get("queued", 0), values.get("running", 0)
        # free places, so that the app sends a recording only when the service takes it (app/whisper_jobs.py)
        return {"queued": queued, "running": running, "free": max(0, self.settings.max_pending - queued - running)}

    def progress(self, row):
        if row["state"] == "queued":
            with self.connect() as db:
                position = db.execute("SELECT COUNT(*) FROM jobs WHERE state='queued' AND "
                                      "(created<? OR (created=? AND id<=?))",
                                      (row["created"], row["created"], row["id"])).fetchone()[0]
            return {"stage": "queued", "position": max(1, min(self.settings.max_pending, position))}
        if row["state"] == "running":
            try:
                stage = json.loads((self.files / f'{row["id"]}.progress').read_text())["stage"]
            except (OSError, ValueError, KeyError, TypeError):
                stage = "loading"
            return {"stage": stage if isinstance(stage, str) and stage in {"loading", "decoding", "transcribing", "aligning", "diarizing"} else "loading", "position": 0}
        return {"stage": row["state"], "position": 0}

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET state='running',updated=?,started=?,attempts=attempts+1 WHERE id=?",
                           (time.time(), time.time(), row["id"]))
        return dict(row) if row else None

    def observe_duration(self, job_id):
        try:
            data = json.loads((self.files / f'{job_id}.progress').read_text())
            value = data.get('duration')
            if type(value) in (int, float) and 0 < value <= self.settings.max_seconds:
                phases = estimates.metadata(data)
                encoded = json.dumps(phases, sort_keys=True)
                with self.connect() as db:
                    db.execute("UPDATE jobs SET duration=?,phases=? WHERE id=? AND state='running' AND "
                               "(duration IS NULL OR duration!=? OR phases!=?)", (value, encoded, job_id, value, encoded))
                return True
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return False

    def estimate(self, job_id):
        now = time.time()
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM jobs WHERE state IN ('queued','running') "
                                                   "ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END,created,id")]
            timings = {row['settings']: dict(row) for row in db.execute('SELECT * FROM timings WHERE updated>=?', (now - 7 * 86400,))}
            phases = {(row['settings'], row['stage']): dict(row) for row in
                      db.execute('SELECT * FROM phase_timings WHERE updated>=?', (now - 7 * 86400,))}
        return estimates.estimate(rows, job_id, timings, self.settings.threads, now, phases)

    def finish(self, job_id, result=None, error=""):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE id=? AND state='running'", (job_id,)).fetchone()
            if row and not error and result and row['duration'] and row['duration'] >= 10:
                phase_data = estimates.metadata(json.loads(row['phases']))
                for stage, elapsed in phase_data.get('completed', {}).items():
                    rate = min(120, max(.0001, elapsed / row['duration']))
                    db.execute("INSERT INTO phase_timings VALUES(?,?,?,1,?) ON CONFLICT(settings,stage) DO UPDATE SET "
                               "rate=CASE WHEN phase_timings.updated<? THEN excluded.rate ELSE phase_timings.rate*.7+excluded.rate*.3 END, "
                               "samples=CASE WHEN phase_timings.updated<? THEN 1 ELSE MIN(1000,phase_timings.samples+1) END,updated=excluded.updated",
                               (estimates.key(json.loads(row['options']), self.settings.threads), stage, rate, time.time(),
                                time.time() - 7 * 86400, time.time() - 7 * 86400))
            # Legacy total timings are only meaningful for an uninterrupted first attempt.
            if row and not error and result and row['started'] and row['duration'] and row['duration'] >= 10 and row['attempts'] == 1:
                rate = min(120, max(.05, (time.time() - row['started'] - 20) / row['duration']))
                db.execute("INSERT INTO timings VALUES(?,?,1,?) ON CONFLICT(settings) DO UPDATE SET "
                           "rate=CASE WHEN timings.updated<? THEN excluded.rate ELSE timings.rate*.7+excluded.rate*.3 END, "
                           "samples=CASE WHEN timings.updated<? THEN 1 ELSE MIN(1000,timings.samples+1) END,updated=excluded.updated",
                           (estimates.key(json.loads(row['options']), self.settings.threads), rate, time.time(),
                            time.time() - 7 * 86400, time.time() - 7 * 86400))
            changed = db.execute("UPDATE jobs SET state=?,result=?,error=?,options='{}',phases='{}',updated=? WHERE id=? AND state='running'",
                                 ("failed" if error else "done", json.dumps(result) if result is not None else None,
                                  error, time.time(), job_id)).rowcount
        self.discard_files(job_id)
        return bool(changed)

    def cancel(self, job_id):
        with self.connect() as db:
            changed = db.execute("UPDATE jobs SET state='cancelled',options='{}',phases='{}',result=NULL,error='',updated=? WHERE id=?",
                                 (time.time(), job_id)).rowcount
        self.discard_files(job_id)
        return bool(changed)

    def postpone(self, job_id):
        with self.connect() as db:
            changed = db.execute("UPDATE jobs SET state='queued',updated=? WHERE id=? AND state='running'", (time.time(), job_id)).rowcount
        if not changed:
            self.discard_files(job_id)  # Cancellation/expiry won while the child was being stopped.
            return
        for suffix in ("input", "result", "result.tmp", "progress", "progress.tmp", "complete", "complete.tmp"):
            (self.files / f"{job_id}.{suffix}").unlink(missing_ok=True)

    def retry(self, job_id):
        """One automatic retry after a downstream error, never an OOM/timeout loop."""
        try:
            if not 0 < (self.files / f'{job_id}.checkpoint').stat().st_size <= MAX_BYTES:
                return False
        except OSError:
            return False
        with self.connect() as db:
            changed = db.execute("UPDATE jobs SET state='queued',failures=failures+1,updated=? "
                                 "WHERE id=? AND state='running' AND failures<1", (time.time(), job_id)).rowcount
        return bool(changed)

    def recover(self):
        """Only the process holding the worker lock calls this after a restart."""
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='queued',updated=? WHERE state='running'", (time.time(),))

    def sweep(self, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute('DELETE FROM timings WHERE updated<?', (now - 7 * 86400,))
            db.execute('DELETE FROM phase_timings WHERE updated<?', (now - 7 * 86400,))
            rows = db.execute("SELECT id FROM jobs WHERE (state IN ('queued','running') AND created<?) "
                              "OR (state NOT IN ('queued','running') AND updated<?)",
                              (now - self.settings.queue_seconds, now - self.settings.result_seconds)).fetchall()
            for row in rows:
                db.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
                self.discard_files(row["id"])
            known = {row[0] for row in db.execute("SELECT id FROM jobs")}
        for path in self.files.iterdir():
            try:
                if path.name.split(".")[0] not in known and now - path.stat().st_mtime > 3600:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
