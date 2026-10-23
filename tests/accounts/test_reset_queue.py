"""AU-10: the public reset handler neither resolves an account nor contacts a provider."""
from datetime import timedelta

from app import account_jobs, notify
from app.extensions import db
from app.models import AccountJob, UserLogin, utcnow
from tests.accounts.test_passwords import MAIL, guest


def test_known_and_unknown_requests_follow_the_same_durable_path(app, client, guest, monkeypatch):
    app.config.update(ACCOUNT_JOBS_SYNC=False, LIFECYCLE_IN_REQUEST=False)
    calls = []
    monkeypatch.setattr(notify, "reset_request", lambda user: calls.append(user.id))
    before = client.post("/auth/forgot-password", data={"email": MAIL})
    after = client.post("/auth/forgot-password", data={"email": "unknown@example.org"})
    assert before.status_code == after.status_code == 302 and before.location == after.location
    with app.app_context():
        jobs = db.session.scalars(db.select(AccountJob)).all()
        assert len(jobs) == 2 and all("example.org" not in row.payload_box for row in jobs)
        assert db.session.scalar(db.select(UserLogin).filter_by(email=MAIL)).reset_requested_at is None
        assert calls == []
        account_jobs.run()
        assert len(calls) == 1
        assert db.session.scalar(db.select(db.func.count(AccountJob.id))) == 0


def test_old_work_is_removed_without_resolving_or_notifying(app, client, guest, monkeypatch):
    app.config.update(ACCOUNT_JOBS_SYNC=False, LIFECYCLE_IN_REQUEST=False)
    monkeypatch.setattr(notify, "reset_request", lambda user: (_ for _ in ()).throw(AssertionError("expired job sent")))
    client.post("/auth/forgot-password", data={"email": MAIL})
    with app.app_context():
        db.session.scalar(db.select(AccountJob)).created_at = utcnow() - timedelta(hours=2)
        db.session.commit()
        account_jobs.run()
        assert db.session.scalar(db.select(db.func.count(AccountJob.id))) == 0



def test_unreadable_work_is_dropped_and_the_next_request_still_goes(app, client, guest, monkeypatch):
    """Code review 2026-10-15, E6: one job that cannot be read stopped every pass at itself (break) for up to an
    hour, and every password reset behind it waited."""
    app.config.update(ACCOUNT_JOBS_SYNC=False, LIFECYCLE_IN_REQUEST=False)
    calls = []
    monkeypatch.setattr(notify, "reset_request", lambda user: calls.append(user.id))
    with app.app_context():
        db.session.add(AccountJob(payload_box="not a box"))
        db.session.commit()
    client.post("/auth/forgot-password", data={"email": MAIL})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(AccountJob.id))) == 2
        account_jobs.run()
        assert len(calls) == 1
        assert db.session.scalar(db.select(db.func.count(AccountJob.id))) == 0
