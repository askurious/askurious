"""The rate limits that guard a secret count in the database for all server processes (security audit 2026-10-13,
TN-2/DP-3/TN-32/AU-9/KR-8): deletion codes, survey and diary codes, sign-in, "Passwort vergessen?", /verify, read
tokens, secrets of the diary app, invitations and join links. Before, each of the 24 participants' processes counted
on its own (in memory), so the limits were up to 24 times as wide and began anew at every restart.

A "process" is simulated by forgetting everything the module keeps in memory: the count must stay."""
import time

import pytest

from app import limits
from app.extensions import db
from app.models import RateHit, SiteSetting

ADDR = {"REMOTE_ADDR": "198.51.100.23"}


def _new_process():
    """What a second server process (or the same after a restart) knows: nothing of this one's memory."""
    for store in (limits._asked, limits._fallback, limits._load_fallback):
        store.clear()


def test_deletion_codes_count_across_processes(app):
    """TN-2: ERASE_LIMIT holds for all processes together, not per process."""
    app.config.update(ERASE_LIMIT=3, ERASE_WINDOW=600)
    with app.test_request_context(environ_base=ADDR):
        for _ in range(3):
            _new_process()
            assert limits.allow_erase()
        _new_process()
        assert not limits.allow_erase()  # a fresh process does not give three more
    with app.test_request_context(environ_base={"REMOTE_ADDR": "198.51.100.24"}):
        assert limits.allow_erase()  # another sender goes on


def test_codes_sign_in_reset_verify_tokens_and_invitations_are_shared(app):
    """TN-2/AU-9/KR-8: every limit guarding a secret counts in rate_hit."""
    app.config.update(CODE_LIMIT=1, LOGIN_LIMIT=1, RESET_LIMIT=1, VERIFY_LIMIT=1, TOKEN_TRY_LIMIT=1)
    checks = [limits.allow_code_try, limits.allow_login, limits.allow_reset, limits.allow_verify,
              limits.allow_token_try, limits.allow_diary_token_try, limits.allow_invite_try]
    with app.test_request_context(environ_base=ADDR):
        for check in checks:
            _new_process()
            assert check()
            _new_process()
            assert not check(), check.__name__
    with app.app_context():
        kinds = set(db.session.scalars(db.select(RateHit.kind)))
    assert kinds == {"code", "login", "reset", "verify", "token_try", "diary_try", "invite_try"}


def test_a_row_names_no_address_and_ipv6_counts_by_its_64(app):
    """A row keeps a keyed digest of the sender (key changes daily), never the address; IPv6 by its /64 as before."""
    with app.test_request_context(environ_base={"REMOTE_ADDR": "2001:db8:1:2::1"}):
        first = limits._shared_sender()
    with app.test_request_context(environ_base={"REMOTE_ADDR": "2001:db8:1:2:ffff::7"}):
        assert limits._shared_sender() == first
    with app.test_request_context(environ_base={"REMOTE_ADDR": "2001:db8:1:3::1"}):
        assert limits._shared_sender() != first
    with app.test_request_context(environ_base=ADDR):
        assert limits.allow_erase()
    with app.app_context():
        row = db.session.scalar(db.select(RateHit))
        assert "198.51.100" not in row.key and len(row.key) == 32


def test_asking_does_not_count_and_counting_goes_through_at_once(app):
    """count=False only asks (its answer is kept a few seconds in the process); a wrong code counts at once."""
    app.config.update(CODE_LIMIT=2, CODE_WINDOW=600)
    with app.test_request_context(environ_base=ADDR):
        assert limits.allow_code_try(count=False) and limits.allow_code_try(count=False)
        with app.app_context():
            assert db.session.scalar(db.select(db.func.count(RateHit.id))) == 0
        assert limits.allow_code_try() and limits.allow_code_try()
        assert not limits.allow_code_try(count=False)  # the counting dropped the kept answer
        assert not limits.allow_code_try()


def test_the_window_moves_and_old_rows_are_swept(app, monkeypatch):
    """The housekeeping deletes rows whose window is over (lifecycle.close_due_surveys calls limits.sweep)."""
    app.config.update(ERASE_LIMIT=1, ERASE_WINDOW=60)
    clock = time.time()
    with app.test_request_context(environ_base=ADDR):
        assert limits.allow_erase() and not limits.allow_erase()
        monkeypatch.setattr(limits.time, "time", lambda: clock + 61)
        assert limits.allow_erase()  # the window moved on
    with app.app_context():
        assert limits.sweep(now=clock + 2 * 3600) == 2
        assert db.session.scalar(db.select(db.func.count(RateHit.id))) == 0


def test_the_housekeeping_sweeps_the_counters(app, monkeypatch):
    from app import lifecycle

    called = []
    monkeypatch.setattr(limits, "sweep", lambda now=None: called.append(1) or 0)
    with app.app_context():
        lifecycle.close_due_surveys()
    assert called


@pytest.mark.parametrize("commit", [False, True])
def test_sweep_joins_the_pending_write_and_the_callers_commit_or_rollback(app, commit):
    clock = time.time()
    with app.app_context():
        db.session.add_all([RateHit(kind="code", key="old", at=clock - 7200),
                            RateHit(kind="code", key="recent", at=clock)])
        db.session.commit()
        db.session.add(SiteSetting(key="sweep_transaction_test", value="pending"))
        db.session.flush()  # holds SQLite's write lock before the cleanup starts
        assert limits.sweep(now=clock) == 1
        assert db.session.scalar(db.select(RateHit.key)) == "recent"
        (db.session.commit if commit else db.session.rollback)()
        assert set(db.session.scalars(db.select(RateHit.key))) == ({"recent"} if commit else {"old", "recent"})
        assert (db.session.get(SiteSetting, "sweep_transaction_test") is not None) == commit


def test_failed_sweep_leaves_the_callers_transaction_usable(app, monkeypatch):
    with app.app_context():
        db.session.add(SiteSetting(key="sweep_failure_test", value="pending"))
        db.session.flush()
        # A real SQL error aborts a PostgreSQL transaction unless isolated by a savepoint.
        connection = db.session.connection()
        execute = connection.execute
        from sqlalchemy.sql.dml import Delete

        def fail_cleanup(statement, *args, **kwargs):
            # Inject inside the savepoint, without replacing SAVEPOINT itself on PostgreSQL.
            if isinstance(statement, Delete) and statement.table.name == "rate_hit":
                return execute(db.text("DELETE FROM missing_rate_sweep_table"))
            return execute(statement, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(connection, "execute", fail_cleanup)
            assert limits.sweep() == 0
        db.session.commit()
        assert db.session.get(SiteSetting, "sweep_failure_test").value == "pending"


def test_sweep_without_pending_writes_can_be_rolled_back(app):
    clock = time.time()
    with app.app_context():
        db.session.add(RateHit(kind="code", key="old", at=clock - 7200))
        db.session.commit()
        assert limits.sweep(now=clock) == 1
        db.session.rollback()
        assert db.session.scalar(db.select(RateHit.key)) == "old"


def test_sweep_keeps_rows_for_the_longest_configured_window(app):
    clock = time.time()
    app.config["ERASE_WINDOW"] = 10800
    with app.app_context():
        db.session.add_all([RateHit(kind="erase", key="old", at=clock - 10801),
                            RateHit(kind="erase", key="boundary", at=clock - 10800),
                            RateHit(kind="erase", key="within", at=clock - 7200)])
        db.session.commit()
        assert limits.sweep(now=clock) == 1
        db.session.commit()
        assert set(db.session.scalars(db.select(RateHit.key))) == {"boundary", "within"}


def test_wrong_passwords_per_sender_and_account_hold_across_processes(app):
    """AU-9: the pause of one sender for one account after wrong passwords is kept in the database too."""
    with app.test_request_context(environ_base=ADDR):
        for _ in range(3):
            _new_process()
            assert limits.login_failed(7, 3, 900)
        _new_process()
        assert limits.login_paused(7, 3, 900)
        assert not limits.login_paused(8, 3, 900)
        limits.login_succeeded(7)
        assert not limits.login_paused(7, 3, 900)
    with app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert limits.login_failed(7, 3, 900) is False  # the sender is not known: the count per account holds


def test_a_database_that_does_not_answer_refuses_protected_attempts(app, monkeypatch):
    app.config.update(ERASE_LIMIT=1, ERASE_WINDOW=600)

    def down(*a):
        raise RuntimeError("down")

    monkeypatch.setattr(limits, "_hits", down)
    with app.test_request_context(environ_base=ADDR):
        assert not limits.allow_erase() and not limits.allow_erase()


def test_invitation_and_join_links_that_lead_nowhere_are_limited(app, client):
    """TN-2: guessing invitation or join links meets 429 after TOKEN_TRY_LIMIT wrong ones; nginx brakes them too."""
    app.config.update(TOKEN_TRY_LIMIT=2)
    guest = app.test_client()
    codes = [guest.get(f"/invite/nothing{i}", environ_base=ADDR).status_code for i in range(3)]
    assert codes[:2] == [302, 302] and codes[2] == 429
    assert guest.get("/join/nothing", environ_base=ADDR).status_code == 429  # one counter for both kinds of link
    assert app.test_client().get("/join/nothing", environ_base={"REMOTE_ADDR": "198.51.100.99"}).status_code == 302


@pytest.mark.parametrize("limit", [1, 3])
def test_parallel_attempts_share_one_atomic_budget(app, monkeypatch, limit):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from time import sleep
    start = Barrier(8)
    original = limits._hits
    def slow_count(*args):
        value = original(*args)
        sleep(0.04)  # enlarge the old check/insert race without waiting behind a lock
        return value
    monkeypatch.setattr(limits, "_hits", slow_count)
    def attempt(_):
        with app.app_context():
            start.wait(timeout=10)
            return limits._take_shared("concurrent", "same-key", limit, 600)
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(attempt, range(8)))
    assert sum(accepted) == limit
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(RateHit.id))) == limit
        assert limits._take_shared("concurrent", "other-key", limit, 600)


def test_a_cached_allowance_cannot_hide_another_workers_hit(app):
    with app.app_context():
        assert limits._take_shared("cross-worker", "key", 1, 600, count=False)
        # A different process inserts without touching this process's memo.
        with db.engine.begin() as con:
            con.execute(RateHit.__table__.insert().values(kind="cross-worker", key="key", at=time.time()))
        assert not limits._take_shared("cross-worker", "key", 1, 600, count=False)
