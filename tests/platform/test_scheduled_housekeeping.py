"""Participant requests no longer initiate maintenance in Docker (SP13 PF-7)."""
from app import lifecycle


def test_disabled_request_housekeeping_and_context_without_browser(app, client, monkeypatch):
    calls = []
    monkeypatch.setattr(lifecycle, "maybe_close_due_surveys", lambda: calls.append(1))
    app.config["LIFECYCLE_IN_REQUEST"] = False
    assert client.get("/").status_code == 200
    assert calls == []
    # The new service uses an app context, not the next participant's request.
    with app.app_context():
        lifecycle.close_due_surveys()
    app.config["LIFECYCLE_IN_REQUEST"] = True
    assert client.get("/").status_code == 200 and calls == [1]


def test_a_failing_step_leaves_the_others_running(app, monkeypatch, caplog):
    """Code review 2026-10-15, E1: a step that raised every time (here the retention periods) kept every later
    step from ever running: mail queue, rate counters, sign-in sessions and later deletions."""
    import time

    from app import limits, mailqueue, retention, sessions

    calls = []
    monkeypatch.setattr(retention, "run", lambda now: (_ for _ in ()).throw(RuntimeError("broken")))
    monkeypatch.setattr(limits, "sweep", lambda: calls.append("limits"))
    monkeypatch.setattr(mailqueue, "run", lambda now: calls.append("mail"))
    monkeypatch.setattr(sessions, "sweep", lambda now: (_ for _ in ()).throw(KeyError("also broken")))
    from app import audit

    monkeypatch.setattr(audit, "sweep", lambda now: calls.append("audit"))
    with app.app_context():
        assert lifecycle.close_due_surveys() == 0
    assert calls == ["limits", "mail", "audit"]
    assert "Housekeeping step retention.run failed" in caplog.text
    assert "Housekeeping step sessions.sweep failed" in caplog.text
    # a busy database still puts the whole round off, as before
    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(retention, "run", lambda now: (_ for _ in ()).throw(
        OperationalError("DELETE", {}, Exception("database is locked"))))
    monkeypatch.setattr(lifecycle, "_last_rare", time.monotonic())
    calls.clear()
    with app.app_context():
        try:
            lifecycle.close_due_surveys()
            raise AssertionError("not put off")
        except OperationalError:
            pass
    assert calls == []


def test_housekeeping_never_fails_a_request(app, client, monkeypatch, caplog):
    """Code review 2026-10-15, E2: on the single machine the housekeeping runs in whatever request comes first; an
    error other than a busy database became that request's 500, often a participant's."""
    monkeypatch.setattr(lifecycle, "close_due_surveys", lambda: (_ for _ in ()).throw(RuntimeError("broken")))
    app.config["LIFECYCLE_IN_REQUEST"] = True
    assert client.get("/").status_code == 200
    assert "Housekeeping in a request failed" in caplog.text


def test_voice_recordings_are_swept_with_the_rare_sweeps(app, monkeypatch):
    """Code review 2026-10-15, E3: voice.run read every answer of every survey with recordings each minute; it
    belongs to the hourly sweeps."""
    import time

    from app.items import voice

    calls = []
    monkeypatch.setattr(voice, "run", lambda now=None: calls.append(1) or 0)
    with app.app_context():
        lifecycle.close_due_surveys()          # the first round of a process runs the rare sweeps too
        assert calls == [1]
        monkeypatch.setattr(lifecycle, "_last_rare", time.monotonic())
        lifecycle.close_due_surveys()
    assert calls == [1]
