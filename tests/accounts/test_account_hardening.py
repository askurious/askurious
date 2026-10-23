"""Account protection that could be walked around (audit of 2026-10-01, package 5): the fresh sign-in, the
start of "Mit ORCID verbinden", and the pause after wrong passwords."""
from tests.accounts.password_setup import confirmed_password
import pytest

from app import accounts, limits, passwords
from app.extensions import db
from app.models import User, UserLogin
from tests.accounts.test_passwords import GOOD, MAIL, OTHER, _as, _sign_in, guest  # noqa: F401  (guest: fixture)


@pytest.fixture(autouse=True)
def _no_failures_from_other_tests(monkeypatch):
    limits._fallback.clear()  # the counts per sender and account are in each test's own database (audit 2026-10-13)


def _stale(c):
    with c.session_transaction() as s:
        s["signed_in_at"] = s["signed_in_at"] - accounts.FRESH_SECONDS - 5
        return s["signed_in_at"]


def test_signing_out_everywhere_does_not_renew_the_sign_in(app, client):
    """It did: one click, and a session left open could add a password to the account or delete it."""
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    before = _stale(other)
    data = {"email": "angreifer@example.org", "password": GOOD, "repeat": GOOD}
    assert "liegt zu lange zurück" in other.post("/account/password", data=data, follow_redirects=True).data.decode()
    assert "Alle anderen Sitzungen" in other.post("/account/sign-out-everywhere", follow_redirects=True).data.decode()
    assert other.get("/account/").status_code == 200          # still signed in here
    with other.session_transaction() as s:
        assert s["signed_in_at"] == before
    assert "liegt zu lange zurück" in other.post("/account/password", data=data, follow_redirects=True).data.decode()
    assert "liegt zu lange zurück" in other.post("/account/delete", data={"name": "Kollegin"}, follow_redirects=True).data.decode()
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        assert user is not None and not passwords.has_password(user)


def test_adding_a_password_does_not_renew_the_sign_in_either(app, client):
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    with other.session_transaction() as s:
        s["signed_in_at"] -= 600  # ten minutes ago: still fresh
        before = s["signed_in_at"]
    data = {"email": "kollegin@example.org", "password": GOOD, "repeat": GOOD}
    assert "Passwort aktiviert" in confirmed_password(app, other, data).data.decode()
    with other.session_transaction() as s:
        assert s["signed_in_at"] == before
    # changing it with the current password is a proof of its own
    data = {"current": GOOD, "password": "noch ein langer neuer Satz", "repeat": "noch ein langer neuer Satz"}
    assert "Passwort gespeichert" in other.post("/account/password", data=data, follow_redirects=True).data.decode()
    with other.session_transaction() as s:
        assert s["signed_in_at"] > before


def test_connecting_orcid_starts_with_the_form_only(app, client, guest):  # noqa: F811
    app.config["ORCID_CLIENT_ID"] = "client"
    page = guest.get("/account/").data.decode()
    assert 'action="/auth/login"' in page and 'name="link" value="1"' in page and 'name="current"' in page
    assert guest.get("/auth/login?link=1&fresh=1").status_code == 302      # what a link on another site could do
    with guest.session_transaction() as s:
        assert "orcid_link" not in s                                        # a plain sign-in, no connection
    r = guest.post("/auth/login", data={"link": "1", "fresh": "1", "current": GOOD})
    assert r.status_code == 302 and "prompt=login" in r.headers["Location"]
    with guest.session_transaction() as s:
        assert "orcid_link" in s


def test_connecting_orcid_asks_for_the_password_and_the_code(app, client, guest, monkeypatch):  # noqa: F811
    """Audit 2026-10-22, A-01: an open session alone connected the iD of whoever sat at the computer, a lasting way
    into the account past the password and the second factor. Now the current password (and, where set up, a code)
    comes first, the round trip must be fresh, and the owner gets a mail."""
    from app import mail, twofactor

    app.config["ORCID_CLIENT_ID"] = "client"
    with guest.session_transaction() as s:
        s["signed_in_at"] = 0                                                 # an old session, left open
    for data in ({"link": "1", "fresh": "1"}, {"link": "1", "fresh": "1", "current": "falsch geraten"}):
        r = guest.post("/auth/login", data=data)
        assert "/account/" in r.headers["Location"] and "oauth" not in r.headers["Location"]
        with guest.session_transaction() as s:
            assert "orcid_link" not in s
    # a second factor set up: the password alone is not enough
    monkeypatch.setattr(twofactor, "enabled", lambda user: True)
    monkeypatch.setattr(twofactor, "check_any", lambda user, code: code == "123456")
    with guest.session_transaction() as s:                                    # this browser gave its code at sign-in
        s["signed_in_via"] = "password+code"
    guest.post("/auth/login", data={"link": "1", "current": GOOD, "code": "000000"})
    with guest.session_transaction() as s:
        assert "orcid_link" not in s
    guest.post("/auth/login", data={"link": "1", "current": GOOD, "code": "123456"})
    with guest.session_transaction() as s:
        assert s["orcid_link"]["user"] and s["orcid_link"]["at"] > 0
        s["orcid_link"]["at"] -= accounts.FRESH_SECONDS + 1                  # the round trip began too long ago
        s["oauth_state"] = "st"
    from app import auth

    class Answer:
        status_code = 200

        @staticmethod
        def json():
            return {"orcid": "0000-0000-0000-0125", "name": "Jemand"}

    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: Answer)
    page = guest.get("/auth/orcid/callback?state=st&code=x", follow_redirects=True).data.decode()
    assert "abgebrochen" in page
    with app.app_context():
        assert db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-0125")) is None
    # in time: connected, and the owner hears of it
    sent = []
    monkeypatch.setattr(mail, "enabled", lambda app=None: True)
    monkeypatch.setattr(mail, "send", lambda to, subject, text, reply_to=None: sent.append((to, subject)))
    guest.post("/auth/login", data={"link": "1", "current": GOOD, "code": "123456"})
    with guest.session_transaction() as s:
        s["oauth_state"] = "st2"
    guest.get("/auth/orcid/callback?state=st2&code=x")
    with app.app_context():
        assert db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-0125")) is not None
    assert sent == [(MAIL, "Askurious: ORCID iD verbunden")]


def test_a_link_on_another_site_neither_signs_in_nor_keeps_the_browser_signed_in(app, guest):  # noqa: F811
    """Audit 2026-10-22, A-03: GET /auth/login?remember=1 from another site started a silent ORCID sign-in that kept
    the browser signed in for 30 days."""
    app.config["ORCID_CLIENT_ID"] = "client"
    r = guest.get("/auth/login?remember=1&next=/surveys/", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 302 and "/auth/sign-in" in r.headers["Location"] and "orcid" not in r.headers["Location"]
    guest.get("/auth/login?remember=1")                                       # without the header: no remembering
    with guest.session_transaction() as s:
        assert not s.get("remember_signin")


def _try(app, ip, password="falsches Passwort"):
    return app.test_client().post("/auth/sign-in", data={"email": MAIL, "password": password},
                                  environ_base={"REMOTE_ADDR": ip})


def _row(app):
    with app.app_context():
        row = db.session.scalar(db.select(UserLogin).filter_by(email=MAIL))
        return row.failed_count, row.locked_until


def test_wrong_passwords_pause_the_sender_not_the_account(app, client, guest):  # noqa: F811
    """Ten wrong passwords used to pause the account for everyone: who knew an address kept its owner out."""
    for _ in range(passwords.MAX_FAILURES):
        assert _try(app, "203.0.113.5").status_code == 401
    assert _row(app) == (10, None)                                           # the account itself is not paused
    assert _try(app, "203.0.113.5", GOOD).status_code == 401                # this sender is, also with the right password
    assert _try(app, "203.0.113.6", GOOD).status_code == 302                # the owner, from elsewhere, gets in
    assert _row(app)[0] == 0


def test_many_senders_together_pause_the_account(app, client, guest):  # noqa: F811
    for n in range(passwords.ACCOUNT_FAILURES // passwords.MAX_FAILURES):
        for _ in range(passwords.MAX_FAILURES):
            _try(app, f"203.0.113.{10 + n}")
        assert (_row(app)[1] is not None) == (n == 4), n                    # not before the fiftieth
    assert _try(app, "203.0.113.99", GOOD).status_code == 401               # for everyone, for 15 minutes


def test_unknown_sender_keeps_the_old_rule(app, client, guest):  # noqa: F811
    c = app.test_client()                                                    # 127.0.0.1: says nothing about the sender
    for _ in range(passwords.MAX_FAILURES):
        _sign_in(c, password="falsches Passwort")
    assert _row(app)[1] is not None
