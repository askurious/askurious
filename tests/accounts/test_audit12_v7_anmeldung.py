"""Audit 2026-10-11, package V7 "Anmeldung und Mail": e-mail addresses in ASCII and IDNA, a mail the server cannot
take counts as an attempt of its row (AU1/M8); at most 100 wrong passwords or codes in a row (AU3/N11); the block list
of the 10,000 most frequent passwords and words of the context (AU4/N12); PKCE at the ORCID sign-in (AU6); fixed
outside services with a limit on time and size, Zenodo without 500 (AU7); the old cookie "session" is gone (AU8)."""
import base64
import hashlib
import smtplib
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from app import emails, limits, mail, mailqueue, passwords, twofactor
from app.extensions import db
from app.models import MailOutbox, User, UserLogin, utcnow

ME = "0000-0000-0000-001X"
_REAL_SMTP = smtplib.SMTP  # the tests below put a stand-in in its place
MAIL = "gast@example.org"
GOOD = "ein langer Satz als Passwort"


# --- AU1: addresses -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value,stored", [
    ("a@münchen.de", "a@xn--mnchen-3ya.de"),             # the domain in ASCII, as stored and compared
    ("A@MÜNCHEN.DE", "a@xn--mnchen-3ya.de"),
    ("a@xn--mnchen-3ya.de", "a@xn--mnchen-3ya.de"),
    (" Anna.Muster+x@Uni-X.de ", "anna.muster+x@uni-x.de"),
])
def test_addresses_in_ascii(value, stored):
    assert emails.normal_email(value) == stored


@pytest.mark.parametrize("value", [
    "jürgen@example.org",          # the local part only in ASCII
    "ａdmin@example.org",           # full width
    "a​@example.org",         # zero width space
    "a@exa‮mple.org",         # right-to-left override
    "a b@example.org",        # no-break space
    "a..b@example.org", ".a@example.org", "a@-x.de", "a@x_y.de",
])
def test_disguising_addresses_are_refused(value):
    assert emails.normal_email(value) is None and not emails.is_email(value)


def test_reply_to_and_to_go_out_in_ascii(app):
    app.config.update(MAIL_HOST="smtp.example.org", MAIL_FROM="noreply@example.org")
    with app.app_context():
        msg = mail.message("a@münchen.de", "Betreff", "Text", reply_to="Forschung@Müller.de")
        assert msg["To"] == "a@xn--mnchen-3ya.de" and msg["Reply-To"] == "forschung@xn--mller-kva.de"
        assert "=?" not in msg.as_string()                       # no encoded word in an address
        # an address stored before the ASCII rule: no Reply-To rather than a broken one; no To: the mail is refused
        assert mail.message("a@b.de", "x", "y", reply_to="jürgen@example.org")["Reply-To"] is None
        with pytest.raises(ValueError):
            mail.message("jürgen@example.org", "x", "y")


def test_what_counts_as_an_attempt_of_the_row():
    assert mailqueue._refused(smtplib.SMTPNotSupportedError("SMTPUTF8 not supported by server"))
    assert mailqueue._refused(smtplib.SMTPResponseException(552, b"too big"))
    assert mailqueue._refused(ValueError("no address"))
    assert not mailqueue._refused(smtplib.SMTPHeloError(501, b"helo"))
    assert not mailqueue._refused(smtplib.SMTPConnectError(421, b"busy"))
    assert not mailqueue._refused(smtplib.SMTPResponseException(421, b"closing"))
    assert not mailqueue._refused(RuntimeError("mail is switched off"))


class _Relay:
    """An SMTP relay without SMTPUTF8 (the real send_message of smtplib runs); `drops` disconnects for one address."""

    def __init__(self):
        self.sent, self.drops = [], set()

    def smtp(self, *args, **kwargs):
        relay = self

        class SMTP(_REAL_SMTP):
            def __init__(self, *a, **k):
                super().__init__()
                self.ehlo_resp, self.does_esmtp, self.esmtp_features = b"fake", True, {"8bitmime": ""}

            def ehlo_or_helo_if_needed(self):
                pass

            def sendmail(self, from_addr, to_addrs, msg, mail_options=(), rcpt_options=()):
                if to_addrs[0] in relay.drops:
                    raise smtplib.SMTPServerDisconnected("gone")
                relay.sent.append(to_addrs[0])
                return {}

            def quit(self):
                pass

            def close(self):
                pass

        return SMTP()


def _waiting():
    return db.session.scalars(db.select(MailOutbox).where(MailOutbox.sent_at.is_(None)).order_by(MailOutbox.id)).all()


def test_an_address_with_an_umlaut_no_longer_holds_up_the_queue(app, monkeypatch):
    relay = _Relay()
    monkeypatch.setattr(smtplib, "SMTP", relay.smtp)
    app.config.update(MAIL_HOST="smtp.example.org", MAIL_FROM="noreply@example.org", MAIL_SECURITY="none")
    with app.app_context():
        # stored before the ASCII rule, and an IDN domain as it is stored now
        for to in ["jürgen@example.org", emails.normal_email("a@münchen.de"), "ben@example.org"]:
            mailqueue.enqueue(to, "Einladung", "Text", kind="links")
        db.session.commit()
        assert mailqueue.send_some(60) == 2
        assert sorted(relay.sent) == ["a@xn--mnchen-3ya.de", "ben@example.org"]
        [row] = _waiting()
        assert row.to == "jürgen@example.org" and row.attempts == 1   # counts, and goes after MAX_ATTEMPTS


def test_a_row_taken_for_a_server_failure_waits_so_the_next_pass_moves_on(app, monkeypatch):
    relay = _Relay()
    relay.drops.add("poison@example.org")
    monkeypatch.setattr(smtplib, "SMTP", relay.smtp)
    app.config.update(MAIL_HOST="smtp.example.org", MAIL_FROM="noreply@example.org", MAIL_SECURITY="none")
    with app.app_context():
        for to in ["poison@example.org", "anna@example.org", "ben@example.org"]:
            mailqueue.enqueue(to, "Einladung", "Text", kind="links")
        db.session.commit()
        assert mailqueue.send_some(60) == 0                       # the pass ends at the first row (C1) ...
        assert mailqueue.send_some(60) == 2                       # ... the next one starts with the next row
        [row] = _waiting()
        assert row.to == "poison@example.org" and row.attempts == 0
        row.claimed_at -= mailqueue.POISON_WAIT                    # after its short wait it is tried again
        db.session.commit()
        relay.drops.clear()
        assert mailqueue.send_some(60) == 1 and not _waiting()


# --- AU3: at most 100 wrong tries in a row --------------------------------------------------------------------------


@pytest.fixture
def guest(app, client):
    import re

    page = client.post("/admin/accounts/new", data={"name": "Gast", "email": MAIL}, follow_redirects=True).data.decode()
    link = re.search(r'value="https?://[^/"]+(/auth/password/[^"]+)"', page).group(1)
    c = app.test_client()
    c.post(link, data={"password": GOOD, "repeat": GOOD})
    with app.app_context():
        return db.session.scalar(db.select(UserLogin.user_id).filter_by(email=MAIL))


@pytest.fixture
def told(monkeypatch):
    out = []
    monkeypatch.setattr(twofactor, "tell", lambda what, user: out.append((what, user.id)))
    return out


def _pause_over(user_id):
    row = db.session.get(UserLogin, user_id)
    row.locked_until = utcnow() - timedelta(seconds=1)   # the 15 minutes have passed
    db.session.commit()
    twofactor._tries.clear()                             # another server process


def test_100_wrong_passwords_in_a_row_lock_until_an_admin_releases(app, client, guest, told, monkeypatch):
    monkeypatch.setattr(limits, "login_paused", lambda *a, **k: False)
    monkeypatch.setattr(limits, "login_failed", lambda *a, **k: True)     # many senders
    with app.test_request_context():
        for n in range(passwords.HARD_LIMIT - 1):
            assert passwords.check(MAIL, "falsches Passwort") is None
            _pause_over(guest)                                             # the pause does not reset the count
        row = db.session.get(UserLogin, guest)
        assert row.failed_streak == passwords.HARD_LIMIT - 1 and row.hard_locked_at is None and not told
        assert passwords.check(MAIL, "falsches Passwort") is None
        row = db.session.get(UserLogin, guest)
        assert passwords.hard_locked(row) and told == [("hard_locked", guest)]
        _pause_over(guest)
        assert passwords.check(MAIL, GOOD) is None                         # the right one too
        assert passwords.paused(db.session.get(User, guest))
    page = client.get(f"/admin/accounts/{guest}").data.decode()
    assert "Anmeldung gesperrt" in page and 'value="release"' in page
    client.post(f"/admin/accounts/{guest}", data={"action": "release"})
    with app.test_request_context():
        assert passwords.check(MAIL, GOOD) is not None
        assert db.session.get(UserLogin, guest).failed_streak == 0


def test_the_lock_runs_out_but_the_next_wrong_try_locks_again(app, guest, told, monkeypatch):
    monkeypatch.setattr(limits, "login_paused", lambda *a, **k: False)
    monkeypatch.setattr(limits, "login_failed", lambda *a, **k: True)
    with app.test_request_context():
        row = db.session.get(UserLogin, guest)
        row.failed_streak, row.hard_locked_at = passwords.HARD_LIMIT, utcnow() - passwords.HARD_LOCK - timedelta(minutes=1)
        db.session.commit()
        assert passwords.check(MAIL, "falsches Passwort") is None
        assert passwords.hard_locked(db.session.get(UserLogin, guest))    # one try per lock, not a hundred
        row = db.session.get(UserLogin, guest)
        row.hard_locked_at = utcnow() - passwords.HARD_LOCK - timedelta(minutes=1)
        db.session.commit()
        assert passwords.check(MAIL, GOOD) is not None                     # the right one ends the row
        assert db.session.get(UserLogin, guest).failed_streak == 0


def test_a_right_password_ends_the_row(app, guest, monkeypatch):
    monkeypatch.setattr(limits, "login_paused", lambda *a, **k: False)
    monkeypatch.setattr(limits, "login_failed", lambda *a, **k: True)
    with app.test_request_context():
        for _ in range(5):
            passwords.check(MAIL, "falsches Passwort")
        assert db.session.get(UserLogin, guest).failed_streak == 5
        assert passwords.check(MAIL, GOOD) is not None
        assert db.session.get(UserLogin, guest).failed_streak == 0


def test_100_wrong_codes_in_a_row_lock_the_factor(app, client, told):
    from app import secrets_box

    secret = twofactor.new_secret()
    with app.test_request_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=ME))
        row = passwords.login_row(user, create=True)
        row.totp_box = secrets_box.encrypt(secret)
        db.session.commit()
        right = {twofactor.code_at(secret, int(time.time() // 30) + k) for k in (-1, 0, 1, 2)}
        wrong = (f"{n:06d}" for n in range(1000000) if f"{n:06d}" not in right)
        for _ in range(passwords.HARD_LIMIT):
            assert not twofactor.check(user, next(wrong))
            db.session.commit()
            _pause_over(user.id)
        assert passwords.hard_locked(db.session.get(UserLogin, user.id)) and ("hard_locked", user.id) in told
        assert not twofactor.check(user, twofactor.code_at(secret, int(time.time() // 30)))
        assert "gesperrt" in twofactor.too_many_said(user)
        passwords.release(db.session.get(UserLogin, user.id))
        db.session.commit()
        assert twofactor.check(user, twofactor.code_at(secret, int(time.time() // 30)))
        assert db.session.get(UserLogin, user.id).failed_streak == 0


# --- AU4: block list ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("password", [
    "Passwort123!", "Sommer2026!!", "Winter2026!!", "Askurious2026", "Polltergeist1", "RWTHAachen2026", "Aachen123456",
    "Password1234", "123456789abc", "iloveyou12345", "letmein123456", "1qay2wsx3edc", "P@ssw0rd2024!", "Dragon1234567",
    "Superman12345", "12345678901!", "Queryous2026!",
])
def test_common_and_context_passwords_are_refused(app, password):
    with app.test_request_context():
        assert passwords.problem(password, "Anna Muster", "anna@example.org")


def test_the_persons_own_words_are_refused(app):
    with app.test_request_context():
        assert "eigene Name" in passwords.problem("AnnaMuster1990", "Anna Muster", "anna@example.org")
        assert "eigene Name" in passwords.problem("Muster.Anna!!", "Anna Muster", "anna@example.org")
        assert "eigene Name" in passwords.problem("Musterhaus2026", "Bea Kunz", "bea@musterhaus.de")


@pytest.mark.parametrize("password", [GOOD, "correct horse battery staple", "Mein Hund heißt Bello",
                                      "Fahrrad fahren im Regen 7", "Zuckerwatte!42", "9$2#7!4&1@3%x8q;0)5^"])
def test_good_passwords_pass(app, password):
    with app.test_request_context():
        assert passwords.problem(password, "Anna Muster", "anna@example.org") is None


def test_the_list_has_the_most_frequent_passwords():
    common = passwords._common()
    assert len(common) > 8000 and {"password", "qwerty", "dragon", "sommer", "passwort"} <= common
    assert passwords.normalize("Sommer2026!!") == "sommer" and "password" in passwords.stems("P@ssw0rd1")


# --- AU6: PKCE ----------------------------------------------------------------------------------------------------


def test_orcid_sign_in_with_pkce(app, monkeypatch):
    from app import auth, orcid_api

    sent = []

    class Answer:
        status_code = 200

        @staticmethod
        def json():
            return {"orcid": ME, "name": "Philipp"}

    app.config["ORCID_CLIENT_ID"] = "client"
    monkeypatch.setattr(auth.requests, "post", lambda url, data=None, **k: sent.append(data) or Answer)
    c = app.test_client()
    q = parse_qs(urlparse(c.get("/auth/login").headers["Location"]).query)
    with c.session_transaction() as s:
        state, verifier = s["oauth_state"], s["oauth_verifier"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] == [base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()]
    c.get(f"/auth/orcid/callback?state={state}&code=x")
    assert sent[-1]["code_verifier"] == verifier
    # a round trip begun before PKCE: no verifier in the session, the sign-in goes on without it
    d = app.test_client()
    with d.session_transaction() as s:
        s["oauth_state"] = "abc"
    assert d.get("/auth/orcid/callback?state=abc&code=x").status_code == 302 and "code_verifier" not in sent[-1]


# --- AU7: fixed outside services ----------------------------------------------------------------------------------


class _Slow:
    """A streamed answer that sends a byte every so often, or a huge one."""

    def __init__(self, chunk=b"x", wait=0.05, total=None):
        self.chunk, self.wait, self.total, self.headers, self.status_code = chunk, wait, total, {}, 200

    def iter_content(self, size):
        n = 0
        while self.total is None or n < self.total:
            time.sleep(self.wait)
            n += 1
            yield self.chunk

    def close(self):
        pass


def test_bounded_calls_end_in_time_and_size(monkeypatch):
    from app import bounded

    monkeypatch.setattr(requests, "request", lambda method, url, **kw: _Slow())
    began = time.monotonic()
    with pytest.raises(bounded.TooSlow):
        bounded.request("GET", "https://example.org", max_bytes=10 ** 6, seconds=0.3)
    assert time.monotonic() - began < 2
    monkeypatch.setattr(requests, "request", lambda method, url, **kw: _Slow(chunk=b"x" * 65536, wait=0))
    with pytest.raises(bounded.TooLarge):
        bounded.request("GET", "https://example.org", max_bytes=1000, seconds=5)
    monkeypatch.setattr(requests, "request", lambda method, url, **kw: _Slow(chunk=b"ok", wait=0, total=2))
    assert bounded.request("GET", "https://example.org", max_bytes=1000, seconds=5)._content == b"okok"


def test_the_dfn_by_https_by_default():
    import pathlib

    from app import seal

    assert seal.DEFAULT_TSA == "https://zeitstempel.dfn.de"
    source = (pathlib.Path(__file__).parents[2] / "config.py").read_text(encoding="utf-8")
    assert 'os.environ.get("TSA_URL", "https://zeitstempel.dfn.de")' in source


def test_zenodo_callback_with_no_json_says_so(app, client, monkeypatch):
    from app import zenodo_auth

    class Broken:
        status_code, text = 200, "<html>Wartung</html>"

        def json(self):
            raise ValueError("no json")

    app.config.update(ZENODO_SANDBOX_CLIENT_ID="id", ZENODO_SANDBOX_CLIENT_SECRET="secret")
    monkeypatch.setattr(zenodo_auth.requests, "post", lambda *a, **k: Broken())
    client.post("/zenodo/connect/sandbox", data={"next": "/dashboard/"})
    with client.session_transaction() as s:
        state = s["zenodo_oauth"]["state"]
    r = client.get(f"/zenodo/callback?code=abc&state={state}", follow_redirects=True)
    assert r.status_code == 200 and "keine gültige Antwort" in r.data.decode()

    class Odd:
        status_code = 200

        def json(self):
            return {"access_token": "acc", "expires_in": "bald"}

    monkeypatch.setattr(zenodo_auth.requests, "post", lambda *a, **k: Odd())
    client.post("/zenodo/connect/sandbox", data={"next": "/dashboard/"})
    with client.session_transaction() as s:
        state = s["zenodo_oauth"]["state"]
    assert client.get(f"/zenodo/callback?code=abc&state={state}").status_code == 302


# --- AU8: the old cookie -------------------------------------------------------------------------------------------


def test_no_switch_brings_the_old_cookie_back():
    import config

    from app import cookies

    assert not hasattr(config.Config, "LEGACY_SESSION_COOKIE_UNTIL")
    assert not hasattr(cookies.SessionInterface, "_legacy_open")
