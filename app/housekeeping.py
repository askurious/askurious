"""Single Docker housekeeping loop; participant requests do no scheduled maintenance (SP13 PF-7)."""
import os
import time
from pathlib import Path

from app import create_app, lifecycle

HEARTBEAT = Path("/tmp/housekeeping-heartbeat")


def main():
    app = create_app()
    while True:
        started = time.monotonic()
        try:
            with app.app_context():
                from app import export_tables, interview_transcribe, security_log, storage_usage, whisper_jobs

                # each on its own: one that fails does not keep the others from running (code review 2026-10-15,
                # E1); close_due_surveys isolates its own steps, and puts the round off only while the database is busy
                for name, step in (("close_due_surveys", lifecycle.close_due_surveys),
                                   ("export_tables.sweep", export_tables.sweep),
                                   ("storage_usage.reconcile", storage_usage.reconcile),
                                   ("interview_transcribe.resume_stale", interview_transcribe.resume_stale),
                                   ("security_log.sweep", security_log.sweep),
                                   ("whisper_jobs.drain_cancels", whisper_jobs.drain_cancels)):
                    try:
                        step()
                    except Exception:
                        from app.extensions import db

                        db.session.rollback()
                        app.logger.exception("Housekeeping step %s failed", name)
            HEARTBEAT.touch()
        except Exception:
            app.logger.exception("Housekeeping failed")
        time.sleep(max(1, 60 - (time.monotonic() - started)))


if __name__ == "__main__":
    if hasattr(os, "nice"):
        os.nice(10)
    main()
