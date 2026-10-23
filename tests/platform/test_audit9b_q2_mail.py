"""Mail limits that hold and mail that does not break (audit 2026-10-09b, package Q2, E1 to E11, P7, P10, P11):
locks and UNIQUE indexes against doubles, invitations to researchers and reminders in the limit per account, one
sending process with a platform-wide rate, strict addresses, titles on one line, List-Unsubscribe only from our own
link, a key of their own for tokens, waiting mails of a deleted account, the list state once a day, nothing glued to
{link}, the addresses in one go, one SMTP connection per pass, the migration 0030."""
import re
import threading
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError

from app import mail, mailqueue, survey_invites
from app.extensions import db
from app.models import MailOutbox, MemberInvite, SiteSetting, Survey, SurveyInvite, User, UserLogin, utcnow
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions, mail_on, needs_sqlite

INV = "/surveys/1/invitations"
ME = "0000-0000-0000-001X"


def _start(app, client):
    fill_consent(client)
    add(client, "text_input")
    fill_questions(app)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        assert db.session.get(Survey, 1).status == "running"


def _me(app):
    with app.app_context():
        return db.session.scalar(db.select(User.id).filter_by(orcid=ME))


def _second_session(app):
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={ME}&name=Philipp")
    return other


def _together(first, second):
    out = [None, None]
    threads = [threading.Thread(target=lambda: out.__setitem__(0, first())),
               threading.Thread(target=lambda: out.__setitem__(1, second()))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return out


# --- E1: two requests at the same moment -----------------------------------------------------------------------------


@needs_sqlite
def test_two_sends_at_once_queue_each_address_once(app, client, survey, monkeypatch):
    """Both requests meet between counting and queuing (as e5_races.py did): the second waits for the lock and then
    sees what the first queued. Before: 80 rows, 40 addresses twice."""
    mail_on(app)
    _start(app, client)
    client.post(INV + "/addresses", data={"addresses": "\n".join(f"p{i}@example.org" for i in range(40))})
    app.config["MAIL_SYNC"] = False
    monkeypatch.setattr(mailqueue, "kick", lambda: None)
    barrier = threading.Barrier(2, timeout=1)
    announce = mailqueue.announce

    def meet(user, count, s):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return announce(user, count, s)

    monkeypatch.setattr(mailqueue, "announce", meet)
    other = _second_session(app)
    codes = _together(lambda: client.post(INV + "/send", data={"kind": "invite"}).status_code,
                      lambda: other.post(INV + "/send", data={"kind": "invite"}).status_code)
    assert codes == [302, 302]
    with app.app_context():
        rows = db.session.scalars(db.select(MailOutbox.to)).all()
        assert len(rows) == 40 and len(set(rows)) == 40


@needs_sqlite
def test_two_uploads_at_once_add_each_address_once(app, client, survey, monkeypatch):
    mail_on(app)
    barrier = threading.Barrier(2, timeout=1)
    real = survey_invites.email_hash

    def meet(s, email, key=None):
        if email == "a0@example.org":
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
        return real(s, email, key)

    monkeypatch.setattr(survey_invites, "email_hash", meet)
    text = "\n".join(f"a{i}@example.org" for i in range(25))
    other = _second_session(app)
    codes = _together(lambda: client.post(INV + "/addresses", data={"addresses": text}).status_code,
                      lambda: other.post(INV + "/addresses", data={"addresses": text}).status_code)
    assert codes == [302, 302]  # the second waits for the first instead of running into the UNIQUE index
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(SurveyInvite.id))) == 25


def test_the_database_refuses_doubles(app, survey):
    with app.app_context():
        db.session.add_all([SurveyInvite(survey_id=1, email="a@example.org", email_hash="h", token_hash="t1"),
                            SurveyInvite(survey_id=1, email="a@example.org", email_hash="h", token_hash="t2")])
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        db.session.add(MailOutbox(to="a@example.org", subject="s", body="b", invite_id=7))
        db.session.add(MailOutbox(to="", subject="", body="", invite_id=7, sent_at=utcnow()))  # sent: may stay beside
        db.session.commit()
        db.session.add(MailOutbox(to="a@example.org", subject="s", body="b", invite_id=7))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_the_list_stops_at_max_list(app, client, survey, monkeypatch):
    mail_on(app)
    monkeypatch.setattr(survey_invites, "MAX_LIST", 3)
    page = client.post(INV + "/addresses", data={"addresses": "\n".join(f"m{i}@example.org" for i in range(5))},
                       follow_redirects=True).data.decode()
    assert "3 Adressen aufgenommen" in page and "2 über der Grenze von 3 Adressen" in page


def test_reminders_once_per_step_and_one_process(app, client, survey):
    sent = mail_on(app)
    _start(app, client)
    client.post(INV + "/addresses", data={"addresses": "ben@example.org"})
    client.post(INV + "/send", data={"kind": "invite"})
    client.post(INV + "/texts", data={"reminder1": "3"})
    with app.app_context():
        invite = db.session.scalar(db.select(SurveyInvite))
        invite.last_sent_at = utcnow() - timedelta(days=4)
        db.session.commit()
        assert mailqueue.hold("mail_due", "other-process", 600)  # another process runs due() now
        app.config["MAIL_SYNC"] = False
        mailqueue.run(utcnow())
        assert mailqueue.waiting() == 0
        mailqueue.release("mail_due", "other-process")
        app.config["MAIL_SYNC"] = True
        sent.clear()
        survey_invites.due(utcnow())
        survey_invites.due(utcnow())  # once more at once: the waiting row is there, nothing twice
        assert mailqueue.waiting() == 1
        row = db.session.scalar(db.select(MailOutbox).filter(MailOutbox.sent_at.is_(None)))
        assert row.kind == "reminder" and row.sender_id == _me(app)  # E3: counts for the account that invited
        mailqueue.send_some(10)
        survey_invites.due(utcnow())  # sent: the entry has two mails now, the step is done
        assert mailqueue.waiting() == 0 and len(sent) == 1


def test_reminders_respect_the_limit_of_the_account(app, client, survey):
    mail_on(app)
    _start(app, client)
    app.config["MAIL_PER_ACCOUNT_DAY"] = 2
    client.post(INV + "/addresses", data={"addresses": "a@example.org\nb@example.org"})
    client.post(INV + "/send", data={"kind": "invite"})
    client.post(INV + "/texts", data={"reminder1": "3"})
    with app.app_context():
        for i in db.session.scalars(db.select(SurveyInvite)):
            i.last_sent_at = utcnow() - timedelta(days=4)
        db.session.commit()
        survey_invites.due(utcnow())
        assert mailqueue.waiting() == 0  # the two invitations used the account's room
        assert mailqueue.by_account(_me(app)) == 2


# --- E2: invitations to researchers and notices -------------------------------------------------------------------------


def test_inviting_the_same_address_again_or_withdrawing_gives_no_room_back(app, client, survey, monkeypatch):
    from app import invites

    sent = mail_on(app)
    monkeypatch.setattr(invites, "PER_DAY", 3)
    for _ in range(3):
        client.post(f"/invite/survey/{survey}", data={"email": "opfer@example.org", "role": "editor"})
    with app.app_context():
        invite_id = db.session.scalar(db.select(MemberInvite.id))
    client.post(f"/invite/withdraw/{invite_id}")
    page = client.post(f"/invite/survey/{survey}", data={"email": "neu@example.org", "role": "editor"},
                       follow_redirects=True).data.decode()
    assert "Mehr als 3 Einladungen in 24 Stunden sind nicht möglich" in page
    assert len(sent) == 3
    with app.app_context():
        assert mailqueue.by_account(_me(app)) == 3  # and they count for the account's limit


def test_invitations_stop_at_the_limit_of_the_account(app, client, survey):
    sent = mail_on(app)
    app.config["MAIL_PER_ACCOUNT_DAY"] = 1
    client.post(f"/invite/survey/{survey}", data={"email": "eins@example.org", "role": "editor"})
    page = client.post(f"/invite/survey/{survey}", data={"email": "zwei@example.org", "role": "editor"},
                       follow_redirects=True).data.decode()
    assert "Grenze von 1 E-Mails in 24 Stunden erreicht" in page and len(sent) == 1


def test_notices_count_and_stop_at_the_limit(app, client, survey):
    from app import invites

    sent = mail_on(app)
    with app.app_context():
        me = db.session.get(User, _me(app))
        others = []
        for n in range(2):
            u = User(orcid=f"0000-0002-0000-000{n}", name=f"P{n}")
            db.session.add(u)
            db.session.flush()
            db.session.add(UserLogin(user_id=u.id, email=f"p{n}@example.org"))
            others.append(u)
        db.session.commit()
        app.config["MAIL_PER_ACCOUNT_DAY"] = 1
        s = db.session.get(Survey, 1)
        with app.test_request_context():
            invites.notice(others[0], me, survey=s, role="editor")
            invites.notice(others[1], me, survey=s, role="editor")
        assert [m["To"] for m in sent] == ["p0@example.org"]
        assert mailqueue.by_account(me.id) == 1


# --- E4: one sending process, rate for the platform -------------------------------------------------------------------


def test_one_process_sends_and_the_rate_counts_the_platform(app, client, survey):
    sent = mail_on(app)
    with app.app_context():
        for n in range(3):
            mailqueue.enqueue(f"x{n}@example.org", "S", "B", kind="links", survey_id=1)
        db.session.commit()
        assert mailqueue.hold("mail_sender", "elsewhere", 180)
        assert mailqueue.send_some(10) == 0 and not sent  # another process sends
        mailqueue.release("mail_sender", "elsewhere")
        assert mailqueue.send_some(1) == 1
        assert mailqueue.send_some(1) == 0  # one mail went within the last minute: the rate is used up
        assert mailqueue.send_some(3) == 2 and len(sent) == 3
        assert db.session.scalar(db.select(SiteSetting.value).filter_by(key="mail_sender")) == ""


def test_a_claim_runs_out(app):
    with app.app_context():
        assert mailqueue.hold("mail_sender", "a", 180)
        assert not mailqueue.hold("mail_sender", "b", 180)
        assert mailqueue.hold("mail_sender", "a", 180)  # renewing our own
        row = db.session.get(SiteSetting, "mail_sender")
        row.value = (utcnow() - timedelta(seconds=5)).isoformat(timespec="seconds") + "|a"
        db.session.commit()
        assert mailqueue.hold("mail_sender", "b", 180)  # a stalled process loses it


# --- E5: one address -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["root,x@example.org", "a<b>,c@example.org", "x@example.org>,<root", "a;b@example.org",
                                   '"a b"@example.org', "a b@example.org", "a@ex\x01ample.org", "a(b)@example.org"])
def test_addresses_with_separators_are_refused(value):
    from app import passwords

    assert passwords.normal_email(value) is None


def test_plain_addresses_still_pass():
    from app import passwords

    assert passwords.normal_email(" Anna.Maria+test@Example.org ") == "anna.maria+test@example.org"
    assert passwords.normal_email("o'brien@example.org") == "o'brien@example.org"


def test_a_mail_goes_to_the_one_address_of_to(app, monkeypatch):
    """The SMTP envelope names only the address of To (send_message(to_addrs=...)); one connection for the pass, and
    the server's name from MAIL_FROM instead of socket.getfqdn() (E11)."""
    import smtplib

    made, envelopes = [], []

    class FakeSMTP:
        def __init__(self, host, port, local_hostname=None, timeout=None):
            made.append(local_hostname)

        def starttls(self, context=None):
            pass

        def send_message(self, msg, to_addrs=None):
            envelopes.append(to_addrs)

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    app.config.update(MAIL_HOST="smtp.example.org", MAIL_FROM="noreply@mail.example.org")
    with app.app_context():
        with mail.Connection() as connection:
            for n in range(3):
                connection.send(mail.message(f"p{n}@example.org", "S", "B"))
    assert made == ["mail.example.org"] and envelopes == [["p0@example.org"], ["p1@example.org"], ["p2@example.org"]]


# --- E6: titles on one line ------------------------------------------------------------------------------------------


def test_a_title_with_a_line_break_is_saved_on_one_line_and_mails_do_not_fail(app, client, survey):
    sent = mail_on(app)
    from app import surveys

    form = {"title": "Erste\nZeile zweite"}
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert surveys.apply_project(s, form) is None
        assert s.title == "Erste Zeile zweite"
        s.title = "Alt\r\nmit Umbruch"  # saved before: the mail still goes
        db.session.commit()
    page = client.post(f"/invite/survey/{survey}", data={"email": "neu@example.org", "role": "editor"},
                       follow_redirects=True)
    assert page.status_code == 200 and len(sent) == 1 and "\n" not in sent[0]["Subject"]
    assert "Alt mit Umbruch" in sent[0]["Subject"]


# --- E8: List-Unsubscribe only from our own link -------------------------------------------------------------------------


def test_list_unsubscribe_ignores_links_in_the_text(app, client, survey):
    sent = mail_on(app)
    _start(app, client)
    client.post(INV + "/texts", data={"invite_subject_de": "Hallo", "invite_body_de":
                                      "Abmelden: https://evil.example/no-mail/x\n\n{link}"})
    client.post(INV + "/addresses", data={"addresses": "anna@example.org"})
    client.post(INV + "/send", data={"kind": "invite"})
    header = sent[0]["List-Unsubscribe"]
    assert "evil.example" not in header and re.search(r"/s/\S+/no-mail/\S+>$", header)
    with app.app_context():
        row = MailOutbox(to="a@example.org", subject="s", kind="invite", survey_id=1,
                         invite_id=db.session.scalar(db.select(SurveyInvite.id)),
                         body="Text\n\n-- \nAbmelden: https://evil.example/s/x/no-mail/abc")
        assert mailqueue._headers(row, db.session.scalar(db.select(SurveyInvite))) is None


# --- E9: a key of their own --------------------------------------------------------------------------------------------


def test_token_key_keeps_old_links_and_opt_outs(app, client, survey):
    sent = mail_on(app)
    _start(app, client)
    client.post(INV + "/addresses", data={"addresses": "anna@example.org\nben@example.org"})
    client.post(INV + "/send", data={"kind": "invite"})
    ben = next(m for m in sent if m["To"] == "ben@example.org")
    client.post(re.search(r"https?://[^/\s]+(/s/\S+/no-mail/\S+)", ben.get_content()).group(1))  # Ben opts out
    old = re.search(r"einladung=(\S+)", next(m for m in sent if m["To"] == "anna@example.org").get_content()).group(1)
    app.config["TOKEN_KEY"] = "a key of its own"
    with app.app_context():
        s = db.session.get(Survey, 1)
        anna = db.session.scalar(db.select(SurveyInvite).filter_by(email="anna@example.org"))
        assert survey_invites.find(s, old).id == anna.id
        assert survey_invites.token_for(anna) == old  # reminders carry the link she has
    page = client.post(INV + "/addresses", data={"addresses": "ben@example.org"}, follow_redirects=True).data.decode()
    assert "1 haben sich abgemeldet" in page  # the opted-out address stays off the list
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert survey_invites.email_hash(s, "x@example.org") != survey_invites.email_hash(
            s, "x@example.org", survey_invites._keys()[-1])  # new entries with the new key


def test_a_new_secret_key_gives_the_entry_a_new_working_token(app, client, survey):
    sent = mail_on(app)
    _start(app, client)
    client.post(INV + "/addresses", data={"addresses": "anna@example.org"})
    client.post(INV + "/send", data={"kind": "invite"})
    app.config["SECRET_KEY"] = "another secret key, the old one leaked"
    with app.app_context():
        anna = db.session.scalar(db.select(SurveyInvite))
        value = survey_invites.token_for(anna)
        db.session.commit()
        assert survey_invites.find(db.session.get(Survey, 1), value).id == anna.id


# --- E10: account deleted ---------------------------------------------------------------------------------------------


def test_deleting_an_account_drops_its_waiting_mails(app, client, survey):
    from app import accounts

    with app.app_context():
        u = User(orcid="0000-0002-1111-2222", name="Weg")
        db.session.add(u)
        db.session.commit()
        db.session.add(MailOutbox(to="wartet@example.org", subject="s", body="b", kind="links", sender_id=u.id))
        db.session.add(MailOutbox(to="", subject="", body="", kind="links", sender_id=u.id, sent_at=utcnow()))
        db.session.commit()
        accounts.delete(db.session.get(User, u.id), "admin", {}, admin=None)
        db.session.commit()
        rows = db.session.scalars(db.select(MailOutbox)).all()
        assert [(r.to, r.sender_id) for r in rows] == [("", None)]


# --- E7 / decision (2): the state once a day, nothing glued to {link} ---------------------------------------------------


def test_nothing_may_follow_the_link(app, client, survey):
    sent = mail_on(app)
    page = client.post(INV + "/texts", data={"invite_subject_de": "Hallo", "invite_body_de": "Bitte: {link}&ref={name}"},
                       follow_redirects=True).data.decode()
    assert "Nach {link} muss ein Leerzeichen" in page
    client.post(INV + "/texts", data={"invite_subject_de": "Hallo", "invite_body_de": "Ihr Link: {link}. Danke!"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.panel["invite_mail"]["texts"]["de"]["invite"]["body"] == "Ihr Link: {link}. Danke!"
        # a text saved before the rule: the link stands on its own in the mail
        s.panel = {**s.panel, "invite_mail": {"texts": {"de": {"invite": {"subject": "Hallo", "body": "X {link}&ref={name}"}}}}}
        db.session.commit()
        subject, body = survey_invites.compose(s, SurveyInvite(survey_id=1, name="ID-4711", lang="de", email_hash="h"),
                                               value="tok")
        assert "einladung=tok &ref=ID-4711" in body


def test_states_come_once_a_day(app, client, survey):
    mail_on(app)
    with app.app_context():
        db.session.add(SurveyInvite(survey_id=1, email="a@example.org", email_hash="h", token_hash="t",
                                    pending_status=SurveyInvite.DONE))
        db.session.merge(SiteSetting(key="invite_states_day", value="2000-01-01"))  # the last daily step was long ago
        db.session.commit()
        now = utcnow()
        mailqueue.run(now)
        assert db.session.scalar(db.select(SurveyInvite.status)) == "done"
        invite = db.session.scalar(db.select(SurveyInvite))
        invite.status, invite.pending_status = SurveyInvite.OPEN, SurveyInvite.STARTED
        db.session.commit()
        mailqueue.run(now)  # the same day: nothing new shows
        assert db.session.scalar(db.select(SurveyInvite.status)) == "open"
        mailqueue.run(now + timedelta(days=1))
        assert db.session.scalar(db.select(SurveyInvite.status)) == "started"
        assert db.session.scalar(db.select(SurveyInvite.pending_status)) is None


# --- E11 / P7: the list in one go ------------------------------------------------------------------------------------


def test_adding_many_addresses_takes_few_statements(app, survey):
    with app.app_context():
        statements = []
        listen = lambda *args: statements.append(1)  # noqa: E731
        event.listen(db.engine, "before_cursor_execute", listen)
        try:
            result = survey_invites.add(db.session.get(Survey, 1), [(f"n{i}@example.org", "", "") for i in range(300)])
        finally:
            event.remove(db.engine, "before_cursor_execute", listen)
        assert result["added"] == 300 and len(statements) < 20
        rows = db.session.scalars(db.select(SurveyInvite)).all()
        assert all(r.token_hash == survey_invites._hash(survey_invites.token(r)) for r in rows)


# --- migration 0030 and P10/P11 -------------------------------------------------------------------------------------------


def test_0030_removes_doubles_before_its_indexes():
    from alembic import command

    from app import migrations

    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        migrations._run_on(connection, command.upgrade, "0029")
        run = connection.exec_driver_sql
        for row_id, hashed, status in ((1, "h", "open"), (2, "h", "optout"), (3, "h", "open"), (4, "k", "open")):
            run("INSERT INTO survey_invite (id, survey_id, email, email_hash, name, lang, token_hash, status, sends, "
                f"failed, created_at) VALUES ({row_id}, 1, '', '{hashed}', '', '', 't{row_id}', '{status}', 0, 0, "
                "CURRENT_TIMESTAMP)")
        for row_id, invite in ((1, 1), (2, 4), (3, 4)):
            run("INSERT INTO mail_outbox (id, survey_id, invite_id, kind, \"to\", subject, body, created_at, attempts) "
                f"VALUES ({row_id}, 1, {invite}, 'invite', 'a@example.org', 's', 'b', CURRENT_TIMESTAMP, 0)")
        connection.commit()
        migrations._run_on(connection, command.upgrade)
        assert [r[0] for r in run("SELECT id FROM survey_invite ORDER BY id")] == [2, 4]  # the opt-out stays
        assert [r[0] for r in run("SELECT id FROM mail_outbox ORDER BY id")] == [2]
        assert migrations.differences(connection) == []
    engine.dispose()


def test_every_foreign_key_has_an_index():
    """Deleting an account or a survey finds the rows that point to it by index (P10); feedback.status too (P11)."""
    missing = []
    for table in db.metadata.sorted_tables:
        lead = {list(ix.columns)[0].name for ix in table.indexes if list(ix.columns)}
        lead |= {c.name for c in table.columns if c.primary_key or c.unique}
        lead |= {list(c.columns)[0].name for c in table.constraints
                 if c.__class__.__name__ in ("PrimaryKeyConstraint", "UniqueConstraint") and list(c.columns)}
        missing += [f"{table.name}.{fk.parent.name}" for fk in table.foreign_keys if fk.parent.name not in lead]
    assert missing == []
    assert any(list(ix.columns)[0].name == "status" for ix in db.metadata.tables["feedback"].indexes)


def test_0030_may_roll():
    import os

    from tests.platform.test_rolling_restart import _rolling

    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(here, "migrations", "versions", "0033_mail_unique_fk_indexes.py"), encoding="utf-8") as f:
        assert _rolling(f.read()) is True


def test_participation_notes_state_without_showing_it(app, client, survey):
    sent = mail_on(app)
    _start(app, client)
    client.post(INV + "/addresses", data={"addresses": "anna@example.org"})
    client.post(INV + "/send", data={"kind": "invite"})
    link = re.search(r"https?://[^/\s]+(/s/\S+\?einladung=\S+)", sent[0].get_content()).group(1)
    p = app.test_client()
    p.get(link)
    p.post(link.split("?")[0], data=CONSENT_OK)
    with app.app_context():
        row = db.session.scalar(db.select(SurveyInvite))
        assert row.status == "open" and row.pending_status in ("started", "done")
