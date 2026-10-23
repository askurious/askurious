"""Places for heavy evaluations are fair per account (audit 2026-10-18, BIZ-08): one account holds at most one of
the HEAVY_PARALLEL places at a time, so repeated exports of one researcher cannot leave all others with 503."""
from contextlib import ExitStack

from flask import Flask

from app import heavy


def _app(tmp_path, name):
    app = Flask(name)  # a Flask app of its own each: its own g, as in another request
    app.config.update(UPLOAD_FOLDER=str(tmp_path), HEAVY_PARALLEL=2, HEAVY_WAIT=0)
    return app


def test_one_account_takes_at_most_one_place_and_another_account_gets_the_free_one(tmp_path):
    first, second, other, third = (_app(tmp_path, f"a{i}") for i in range(4))
    with ExitStack() as stack:
        stack.enter_context(first.test_request_context())
        assert heavy.hold(account=7)
        stack.callback(heavy.release)
        with second.test_request_context():
            assert not heavy.hold(account=7)  # the same account's second export does not take the free place
        with other.test_request_context():
            assert heavy.hold(account=8)  # another researcher gets it
            try:
                with third.test_request_context():
                    assert not heavy.hold(account=9)  # both places taken now
            finally:
                heavy.release()
    with second.test_request_context():
        assert heavy.hold(account=7)  # free again once the first one ended
        heavy.release()


def test_the_signed_in_person_is_the_account(tmp_path, monkeypatch):
    first, second = _app(tmp_path, "b1"), _app(tmp_path, "b2")
    monkeypatch.setattr(heavy, "_account", lambda: "42")
    with first.test_request_context():
        assert heavy.hold()
        try:
            with second.test_request_context():
                assert not heavy.hold()
        finally:
            heavy.release()


def test_anonymous_requests_and_a_single_place_keep_the_old_rule(tmp_path):
    a, b = _app(tmp_path, "c1"), _app(tmp_path, "c2")
    with a.test_request_context():
        assert heavy.hold()  # no login manager: anonymous, no account lock
        try:
            with b.test_request_context():
                assert heavy.hold()
                heavy.release()
        finally:
            heavy.release()
