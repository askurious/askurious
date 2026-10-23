"""The wait for the backup window has a limit within a request (Abgleich 2026-10-15 NB-4/T05-N-2). The real flock is
tested on Linux in test_storage_guard.py; here the waiting rule with a stand-in, on every system."""
import pytest

from app import storage_guard


class _Busy:
    LOCK_SH, LOCK_NB = 1, 4

    def __init__(self):
        self.calls = []

    def flock(self, handle, flags):
        self.calls.append(flags)
        if flags & self.LOCK_NB:
            raise BlockingIOError


def test_a_request_gives_up_after_storage_gate_wait(app):
    fake = _Busy()
    app.config["STORAGE_GATE_WAIT"] = 0.1
    with app.test_request_context("/"):
        with pytest.raises(storage_guard.StorageBusy):
            storage_guard._share(None, fake)
    assert fake.calls and all(flags & fake.LOCK_NB for flags in fake.calls)


def test_work_outside_a_request_still_waits(app):
    fake = _Busy()
    with app.app_context():
        storage_guard._share(None, fake)  # the blocking call of the stand-in returns at once
    assert fake.calls == [fake.LOCK_SH]


def test_the_answer_is_503_with_retry_after(app):
    with app.test_request_context("/surveys/"):
        response = app.make_response(app.handle_user_exception(storage_guard.StorageBusy()))
    assert response.status_code == 503 and response.headers["Retry-After"] == "10"


def test_participants_get_their_page_to_send_again(app, client, survey):
    from tests.participation.test_participate import build, uid

    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    code = uid(app)
    with app.test_request_context(f"/s/{code}", method="POST"):
        response = app.make_response(app.handle_user_exception(storage_guard.StorageBusy()))
    assert response.status_code == 503 and response.headers["Retry-After"] == "10"
    assert "<html" in response.get_data(as_text=True).lower()
