"""Code audit 2026-10-08, PostgreSQL: the row lock on the survey that makes completions count one after the other
(lifecycle.close_if_sample_complete, seal._after_flush and seal._append) is FOR NO KEY UPDATE, not FOR UPDATE.

Every row written with a reference to the survey (a participation, a separated section, a chain entry ...) holds a
FOR KEY SHARE lock on the survey row until its transaction ends (PostgreSQL's check of the foreign key). FOR UPDATE
waits for every such lock: two requests that had each written such a row and then both locked the survey waited
for each other, and PostgreSQL broke one off with "deadlock detected" (the participant got the page "please send
again", the completion was lost for the moment). FOR NO KEY UPDATE waits for the other lockers of the survey, so
the completions still count one after the other, but not for the KEY SHARE locks of the references."""
import threading
from datetime import timedelta

from app import lifecycle
from app.extensions import db
from app.models import Response, SplitSession, Survey, utcnow
from tests.participation.test_audit_g_concurrency import _sample_survey, needs_pg


def _two_at_once(app, step):
    """Two transactions that each write a row referencing survey 1 (the KEY SHARE lock), meet, then run step(k)."""
    barrier = threading.Barrier(2, timeout=10)
    errors = []

    def run(k):
        with app.app_context():
            try:
                db.session.add(SplitSession(survey_id=1, language="de", layout={}, answers={}, position=0))
                db.session.flush()  # the foreign key check: FOR KEY SHARE on the survey row until the commit
                barrier.wait()
                step(k)
                db.session.commit()
            except Exception as e:  # noqa: BLE001 - reported below
                db.session.rollback()
                errors.append(f"{type(e).__name__}: {str(e).splitlines()[0]}")

    threads = [threading.Thread(target=run, args=(k,)) for k in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return errors


@needs_pg
def test_two_completions_lock_the_survey_without_a_deadlock(app, client, survey):
    _sample_survey(app, client, 5)

    def step(k):
        lifecycle.close_if_sample_complete(db.session.get(Survey, 1))

    assert _two_at_once(app, step) == []  # before: one DeadlockDetected
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.RUNNING
        assert db.session.scalar(db.select(db.func.count()).select_from(SplitSession)) == 2


@needs_pg
def test_two_ends_sealed_at_once_without_a_deadlock(app, client, survey):
    """The lock of seal._after_flush when a participation ends (code audit 2026-10-08, S7), with the chain on, so
    _append locks as well; the entries still follow one another (no fork in the chain)."""
    from app import seal

    uid = _sample_survey(app, client, 5)
    with app.app_context():
        s = db.session.get(Survey, 1)
        seal.enable(s)
        ids = []
        for _ in range(2):
            r = Response(survey_id=1, source="real", started_at=utcnow() - timedelta(minutes=5))
            db.session.add(r)
            db.session.flush()
            ids.append(r.id)
        db.session.commit()

    def step(k):
        r = db.session.get(Response, ids[k])
        r.status, r.completed_at = Response.COMPLETED, utcnow()
        db.session.flush()

    assert _two_at_once(app, step) == []  # before: one DeadlockDetected
    with app.app_context():
        assert all(db.session.get(Response, i).status == Response.COMPLETED for i in ids)
        rows = db.session.execute(db.select(seal.TABLE.c.seq, seal.TABLE.c.prev, seal.TABLE.c.digest)
                                  .where(seal.TABLE.c.survey_uid == db.session.get(Survey, 1).uid)
                                  .order_by(seal.TABLE.c.seq)).all()
        assert [r.seq for r in rows] == list(range(1, len(rows) + 1)) and len(rows) >= 3
        assert all(b.prev == a.digest for a, b in zip(rows, rows[1:]))  # one line, no fork
    assert uid
