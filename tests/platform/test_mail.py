"""E-mail from the site (app/mail.py, mailversand-konzept.md): off without the .env lines, and then no page shows a
mail function; the section "Mailserver" on Administration > Server; "Passwort vergessen?" mails a link by itself;
admins send one-time links by mail."""
import re
from datetime import timedelta

import pytest

from app import mail, passwords
from app.extensions import db
from app.models import AuthToken, UserLogin
from tests.conftest import mail_on

MAIL = "gast@example.org"
GOOD = "ein langer Satz als Passwort"


def _create(client, **extra):
    return client.post("/admin/accounts/new", data={"name": "Gast", "email": MAIL, **extra}, follow_redirects=True)


def _has_password(app):
    with app.app_context():
        assert db.session.scalar(db.select(UserLogin).filter_by(email=MAIL)).password_hash


def _create_with_password(app, client):
    page = _create(client).data.decode()
    link = re.search(r"(/auth/password/[A-Za-z0-9_-]+)", page).group(1)
    app.test_client().post(link, data={"password": GOOD, "repeat": GOOD})
    _has_password(app)


def _link(msg):
    return re.search(r"https?://[^/\s]+(/auth/password/\S+)", msg.get_content()).group(1)


def test_off_without_settings_and_nothing_shows(app, client):
    with app.app_context():
        assert not mail.enabled() and mail.missing() == ["MAIL_HOST", "MAIL_FROM"]
    page = client.get("/admin/database").data.decode()
    assert "Mailserver" in page and "Kein Versand eingestellt" in page and "MAIL_HOST" in page
    assert "per E-Mail" not in client.get("/admin/people").data.decode()
    _create(client)
    with app.app_context():
        user_id = db.session.scalar(db.select(UserLogin.user_id).filter_by(email=MAIL))
    assert "per E-Mail" not in client.get(f"/admin/accounts/{user_id}").data.decode()
    assert "Anfrage senden" in client.get("/auth/forgot-password").data.decode()
    assert "verschickt keine E-Mails" in client.get("/imprint").data.decode()
    assert client.post(f"/admin/accounts/{user_id}", data={"action": "link_mail"}).status_code == 400


def test_settings_defaults(app):
    with app.app_context():
        app.config.update(MAIL_HOST="mail.example.org", MAIL_FROM="noreply@example.org", MAIL_PORT=465)
        assert mail.enabled() and mail.settings()["security"] == "ssl"
        app.config.update(MAIL_PORT=0, MAIL_SECURITY="")
        assert mail.settings()["port"] == 587 and mail.settings()["security"] == "starttls"
        app.config.update(MAIL_USER="u")
        assert mail.missing() == ["MAIL_PASSWORD"]
        msg = mail.message("a@example.org", "S", "T", reply_to="r@example.org")
        assert msg["From"] == "Askurious <noreply@example.org>" and msg["Reply-To"] == "r@example.org"


def test_admin_section_and_test_mail(app, client):
    sent = mail_on(app)
    page = client.get("/admin/database").data.decode()
    assert "eingeschaltet" in page and "smtp.example.org" in page and "Test-E-Mail senden" in page
    r = client.post("/admin/database", data={"action": "mail_test", "to": "admin@example.org"}, follow_redirects=True)
    assert "Test-E-Mail an admin@example.org abgegeben" in r.data.decode()
    assert sent[-1]["To"] == "admin@example.org" and "Der Versand funktioniert" in sent[-1].get_content()


def test_test_mail_says_why_it_failed(app, client, monkeypatch):
    mail_on(app)

    def broken(msg, app=None):
        raise OSError("Connection refused")

    monkeypatch.setattr(mail, "deliver", broken)
    r = client.post("/admin/database", data={"action": "mail_test", "to": "admin@example.org"}, follow_redirects=True)
    assert "Die Test-E-Mail ging nicht raus: OSError: Connection refused" in r.data.decode()


def test_new_account_link_by_mail(app, client):
    sent = mail_on(app)
    assert "Einmal-Link per E-Mail an die Person schicken" in client.get("/admin/people").data.decode()
    page = _create(client, send_mail="1").data.decode()
    assert "per E-Mail an gast@example.org unterwegs" in page and "/auth/password/" not in page
    msg = sent[-1]
    assert msg["To"] == MAIL and "72 Stunden" in msg.get_content()
    other = app.test_client()
    other.post(_link(msg), data={"password": GOOD, "repeat": GOOD})
    assert "/auth/sign-in" not in other.get("/dashboard/").headers.get("Location", "")  # signed in
    _has_password(app)


def test_new_account_without_tick_shows_the_link(app, client):
    sent = mail_on(app)
    page = _create(client).data.decode()
    assert "/auth/password/" in page and not sent


def test_admin_sends_a_new_link_by_mail(app, client):
    sent = mail_on(app)
    _create(client)
    with app.app_context():
        user_id = db.session.scalar(db.select(UserLogin.user_id).filter_by(email=MAIL))
    assert "Neuen Einmal-Link per E-Mail schicken" in client.get(f"/admin/accounts/{user_id}").data.decode()
    page = client.post(f"/admin/accounts/{user_id}", data={"action": "link_mail"}, follow_redirects=True).data.decode()
    assert "per E-Mail an gast@example.org geschickt" in page and "/auth/password/" not in page
    assert sent[-1]["To"] == MAIL


def test_forgot_password_mails_a_link(app, client):
    _create_with_password(app, client)
    with app.app_context():
        user_id = db.session.scalar(db.select(UserLogin.user_id).filter_by(email=MAIL))
    sent = mail_on(app)
    other = app.test_client()
    assert "Link senden" in other.get("/auth/forgot-password").data.decode()
    page = other.post("/auth/forgot-password", data={"email": MAIL}, follow_redirects=True).data.decode()
    assert "kommt gleich eine E-Mail" in page
    assert len(sent) == 1 and "1 Stunde" in sent[0].get_content() and sent[0]["To"] == MAIL
    with app.app_context():  # the admins are not involved
        assert db.session.get(UserLogin, user_id).reset_requested_at is None
        token = db.session.scalar(db.select(AuthToken).filter_by(user_id=user_id))
        assert token.created_by_id is None and token.expires_at - token.created_at <= timedelta(hours=1, seconds=5)
    other.post("/auth/forgot-password", data={"email": MAIL})  # within ten minutes: no second mail
    assert len(sent) == 1
    other.post(_link(sent[0]), data={"password": GOOD, "repeat": GOOD})
    _has_password(app)


def test_forgot_password_unknown_address_gets_the_same_answer(app):
    sent = mail_on(app)
    c = app.test_client()
    page = c.post("/auth/forgot-password", data={"email": "niemand@example.org"}, follow_redirects=True).data.decode()
    assert "kommt gleich eine E-Mail" in page and not sent


def test_mail_in_the_persons_language(app, client):
    sent = mail_on(app)
    _create_with_password(app, client)
    with app.app_context():
        login = db.session.scalar(db.select(UserLogin).filter_by(email=MAIL))
        from app.models import User

        user = db.session.get(User, login.user_id)
        user.display = {"lang": "en"}
        db.session.commit()
    app.test_client().post("/auth/forgot-password", data={"email": MAIL})
    assert sent[-1]["Subject"] == "Askurious: set a new password"


def test_privacy_names_mail_only_when_on(app, client):
    mail_on(app)
    page = client.get("/imprint").data.decode()
    assert "Empfängeradresse für E-Mails der Plattform" in page and "verschickt keine E-Mails" not in page


def test_recently_mailed_counts_only_self_service(app, client):
    _create(client)
    with app.app_context():
        from app.models import User

        user = db.session.get(User, db.session.scalar(db.select(UserLogin.user_id).filter_by(email=MAIL)))
        assert not passwords.recently_mailed(user)  # the admin's link
        passwords.new_link_token(user, None, hours=1)
        db.session.commit()
        assert passwords.recently_mailed(user)


def test_plain_text_login_is_named_loudly(app, monkeypatch, caplog, tmp_path):
    """Audit 2026-10-18, CFG-09: MAIL_SECURITY=none with MAIL_USER sent MAIL_PASSWORD unencrypted without a word. Now
    the start, every login and Administration > Server say so (mail goes on: a deploy must not cut it off); a relay on
    this machine (localhost) is no problem. smtplib stands in."""
    import logging
    import smtplib

    calls = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            calls.append("connect")

        def login(self, *args):
            calls.append("login")

        def send_message(self, *args, **kwargs):
            calls.append("send")

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    app.config.update(MAIL_HOST="smtp.example.org", MAIL_FROM="noreply@example.org", MAIL_SECURITY="none",
                      MAIL_USER="relay", MAIL_PASSWORD="geheim")
    with app.app_context():
        assert mail.plaintext_login() and "unencrypted" in mail.problem()
        msg = mail.message("person@example.org", "Betreff", "Text")
        with caplog.at_level(logging.ERROR):
            mail.deliver(msg)
        assert calls[-2:] == ["login", "send"] and "SMTP login without TLS" in caplog.text and "geheim" not in caplog.text
        app.config["MAIL_HOST"] = "localhost"                     # a relay on this machine: no word
        assert not mail.plaintext_login() and not mail.problem()
    from app import create_app
    from tests.conftest import _config

    (tmp_path / "start").mkdir()

    class Plain(_config(tmp_path / "start")):
        MAIL_HOST, MAIL_FROM, MAIL_SECURITY, MAIL_USER = "smtp.example.org", "noreply@example.org", "none", "relay"

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        create_app(Plain)
    assert "password travels unencrypted" in caplog.text


def test_admin_page_names_the_plain_text_login(app, client):
    app.config.update(MAIL_HOST="smtp.example.org", MAIL_FROM="noreply@example.org", MAIL_SECURITY="none",
                      MAIL_USER="relay", MAIL_PASSWORD="geheim")
    page = client.get("/admin/database").get_data(as_text=True)
    assert "MAIL_SECURITY ist none, aber MAIL_USER ist gesetzt" in page and "geheim" not in page
