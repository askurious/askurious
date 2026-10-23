"""Invitation mails to participants (app/survey_invites.py, app/mailqueue.py, mailversand-konzept.md M6 to M8): the
address list, the personal link that never reaches the data, "begonnen"/"teilgenommen", reminders only to those who
have not taken part, opting out, the daily limit, the links per person by mail, and nothing without mail."""
import re
from datetime import timedelta

from app import retention, survey_invites
from app.extensions import db
from app.models import MailOutbox, Response, Survey, SurveyInvite, utcnow
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions, mail_on

INV = "/surveys/1/invitations"


def _start(app, client):
    fill_consent(client)
    add(client, "text_input")
    fill_questions(app)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        assert db.session.get(Survey, 1).status == "running"


def _link(msg):
    return re.search(r"https?://[^/\s]+(/s/\S+\?einladung=\S+)", msg.get_content()).group(1)


def _no_mail(msg):
    return re.search(r"https?://[^/\s]+(/s/\S+/no-mail/\S+)", msg.get_content()).group(1)


def _finish(app, p, url):
    p.post(url, data=CONSENT_OK)
    for _ in range(10):
        with app.app_context():
            last = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
            if last.status != "active":
                return last
        p.post(url, data={"nav": "next"})
    raise AssertionError("the participation did not end")


def test_nothing_without_mail(app, client, survey):
    assert "Einladungen per E-Mail" not in client.get("/surveys/1/field").data.decode()
    assert client.post(INV + "/addresses", data={"addresses": "a@example.org"}).status_code == 404


def test_list_send_take_part_and_remind(app, client, survey):
    sent = mail_on(app)
    _start(app, client)
    assert "Einladungen per E-Mail" in client.get("/surveys/1/field").data.decode()
    page = client.post(INV + "/addresses", data={"addresses": "email;name\nanna@example.org;Anna\nben@example.org;\nkeine adresse\nANNA@example.org"},
                       follow_redirects=True).data.decode()
    assert "2 Adressen aufgenommen" in page and "1 standen schon auf der Liste" in page and "1 Zeilen ohne gültige Adresse" in page
    page = client.post(INV + "/send", data={"kind": "invite"}, follow_redirects=True).data.decode()
    assert "2 E-Mails sind unterwegs" in page
    assert len(sent) == 2
    anna = next(m for m in sent if m["To"] == "anna@example.org")
    assert "Guten Tag Anna," in anna.get_content() and "Guten Tag ," not in next(m for m in sent if m["To"] == "ben@example.org").get_content()
    assert anna["List-Unsubscribe"].startswith("<http") and "einladung=" in anna.get_content()
    with app.app_context():
        assert not db.session.scalars(db.select(MailOutbox).filter(MailOutbox.sent_at.is_(None))).all()  # the queue is empty once sent
        assert {i.sends for i in db.session.scalars(db.select(SurveyInvite))} == {1}
    link = _link(anna)
    url = link.split("?")[0]
    p = app.test_client()
    p.get(link)
    response = _finish(app, p, url)
    token = link.split("einladung=")[1]
    assert token not in str(response.params) and token not in str(response.answers)  # nothing of the link in the data
    with app.app_context():
        states = {i.email: i.status for i in db.session.scalars(db.select(SurveyInvite))}
        assert states == {"anna@example.org": "open", "ben@example.org": "open"}  # shown only after the daily step
        survey_invites.apply_states()
        states = {i.email: i.status for i in db.session.scalars(db.select(SurveyInvite))}
        assert states == {"anna@example.org": "done", "ben@example.org": "open"}
    # a reminder after 3 days goes to Ben only
    client.post(INV + "/texts", data={"reminder1": "3"})
    with app.app_context():
        for i in db.session.scalars(db.select(SurveyInvite)):
            i.last_sent_at = utcnow() - timedelta(days=4)
        db.session.commit()
        sent.clear()
        survey_invites.due(utcnow())
        from app import mailqueue

        mailqueue.send_some(100)
    assert [m["To"] for m in sent] == ["ben@example.org"] and "Erinnerung" in sent[0]["Subject"]
    with app.app_context():
        sent.clear()
        survey_invites.due(utcnow())  # the step is done: nothing twice
        assert not db.session.scalars(db.select(MailOutbox).filter(MailOutbox.sent_at.is_(None))).all()


def test_opt_out(app, client, survey):
    sent = mail_on(app)
    _start(app, client)
    client.post(INV + "/addresses", data={"addresses": "anna@example.org"})
    client.post(INV + "/send", data={"kind": "invite"})
    path = _no_mail(sent[0])
    p = app.test_client()
    assert "Keine E-Mails mehr zu dieser Befragung" in p.get(path).data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(SurveyInvite)).status == "open"  # a GET only asks
    assert "keine weiteren E-Mails" in p.post(path).data.decode()
    with app.app_context():
        row = db.session.scalar(db.select(SurveyInvite))
        assert row.status == "optout" and row.email == "" and row.name == ""
    page = client.post(INV + "/addresses", data={"addresses": "anna@example.org"}, follow_redirects=True).data.decode()
    assert "1 haben sich abgemeldet" in page
    client.post(INV + "/delete")
    with app.app_context():
        assert db.session.scalar(db.select(SurveyInvite)).status == "optout"  # only the hash stays


def test_texts_need_the_link_and_test_mail(app, client, survey):
    sent = mail_on(app)
    page = client.post(INV + "/texts", data={"invite_subject_de": "Hallo", "invite_body_de": "ohne Link"},
                       follow_redirects=True).data.decode()
    assert "Platzhalter {link}" in page
    client.post(INV + "/texts", data={"invite_subject_de": "Bitte mitmachen: {titel}", "invite_body_de": "Hallo {name}: {link}"})
    from app.models import User, UserLogin

    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        db.session.add(UserLogin(user_id=me.id, email="philipp@example.org"))
        db.session.commit()
    client.post(INV + "/test", data={"lang": "de", "kind": "invite"})
    assert sent[-1]["To"] == "philipp@example.org" and sent[-1]["Subject"] == "[Test] Bitte mitmachen: Test"
    assert "Hallo Philipp: http" in sent[-1].get_content()


def test_not_before_the_start(app, client, survey):
    mail_on(app)
    client.post(INV + "/addresses", data={"addresses": "anna@example.org"})
    page = client.post(INV + "/send", data={"kind": "invite"}, follow_redirects=True).data.decode()
    assert "erst raus, wenn die Befragung läuft" in page


def test_daily_limit(app, client, survey):
    sent = mail_on(app)
    app.config["INVITES_PER_DAY"] = 2
    _start(app, client)
    client.post(INV + "/addresses", data={"addresses": "a@example.org\nb@example.org\nc@example.org"})
    page = client.post(INV + "/send", data={"kind": "invite"}, follow_redirects=True).data.decode()
    assert "1 weitere überschreiten die Tagesgrenze (2 je Befragung" in page and len(sent) == 2


def test_retention_and_deletion_take_the_list(app, client, survey):
    mail_on(app)
    client.post(INV + "/addresses", data={"addresses": "anna@example.org"})
    with app.app_context():
        retention.anonymise(db.session.get(Survey, 1))
        db.session.commit()
        assert db.session.scalar(db.select(SurveyInvite)) is None


def test_links_per_person_by_mail(app, client, survey):
    sent = mail_on(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {"params": [{"name": "w", "mode": "store", "allowed": ["abc1", "def2"]}]}
        db.session.commit()
    client.post("/surveys/1/value-links/mail", data={"mail:w:abc1": "stud@example.org", "mail:w:def2": "",
                                                     "mail:w:zzz": "x@example.org", "note": "Bis Freitag"})
    assert [m["To"] for m in sent] == ["stud@example.org"]
    assert "w=abc1" in sent[0].get_content() and "Bis Freitag" in sent[0].get_content()
    with app.app_context():
        assert not db.session.scalars(db.select(MailOutbox).filter(MailOutbox.sent_at.is_(None))).all()  # the address is not kept


def test_limit_per_account_across_surveys(app, client, survey, monkeypatch):
    sent = mail_on(app)
    app.config["MAIL_PER_ACCOUNT_DAY"] = 3
    notes = []
    from app import notify

    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: notes.append(text))
    _start(app, client)
    client.post(INV + "/addresses", data={"addresses": "a@example.org\nb@example.org"})
    client.post(INV + "/send", data={"kind": "invite"})
    assert len(sent) == 2 and len(notes) == 1 and "Serienmails" in notes[0]
    with app.app_context():
        row = db.session.scalar(db.select(MailOutbox))
        assert row.sent_at and row.to == "" and row.body == "" and row.sender_id  # kept without address and text
        s = db.session.get(Survey, 1)
        s.panel = {**(s.panel or {}), "params": [{"name": "w", "mode": "store", "allowed": ["abc1", "def2"]}]}
        db.session.commit()
    page = client.post("/surveys/1/value-links/mail", data={"mail:w:abc1": "x@example.org", "mail:w:def2": "y@example.org"},
                       follow_redirects=True).data.decode()
    assert len(sent) == 3 and "1 weitere überschreiten die Grenze von 3 E-Mails je Konto und Tag" in page
    assert len(notes) == 1  # only the first series of the day
    client.post(INV + "/delete")  # deleting the list does not reset the count
    with app.app_context():
        from app import mailqueue

        assert mailqueue.by_account(db.session.scalar(db.select(MailOutbox.sender_id))) == 3
        mailqueue.sweep(utcnow() + timedelta(days=2))
        assert db.session.scalar(db.select(MailOutbox)) is None


def test_the_test_mail_only_for_admins_with_an_address(app, client, survey):
    """survey_invites.test_mail beyond its happy path above (code review 2026-10-15, L8): "Bearbeiten" is refused
    (the invitations need "Verwalten"), and an account without an e-mail address is told so; nothing is sent."""
    from app.models import SurveyMember, User, UserLogin
    from tests.conftest import allow

    sent = mail_on(app)
    client.post(INV + "/test", data={"lang": "de", "kind": "invite"})
    assert sent == [] and "Ihr Konto hat keine E-Mail-Adresse" in client.get("/surveys/1/field").data.decode()
    other = "0000-0002-1825-0097"
    allow(app, "0000-0000-0000-001X", other, keep=True)
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=other, name="Zweite", role="editor", position=1))
        db.session.commit()
    editor = app.test_client()
    editor.get(f"/auth/dev-login?orcid={other}&name=Zweite")
    editor.post("/terms/", data={"accept": "1"})
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(orcid=other))
        row = db.session.get(UserLogin, uid) or UserLogin(user_id=uid, status="active")
        row.email = "zweite@example.org"
        db.session.add(row)
        db.session.commit()
    assert editor.post(INV + "/test", data={"lang": "de", "kind": "invite"}).status_code == 302
    assert sent == []


def test_only_invited_people_each_link_once(app, client, survey):
    """Audit 2026-10-18, BIZ-09: the list limited nobody; the general link worked, and a personal link passed on began
    any number of participations. With "Nur mit persönlichem Link teilnehmen" the survey takes only links of the list,
    each for one participation; without the tick, and once the list is deleted, it is as before."""
    sent = mail_on(app)
    _start(app, client)
    client.post(INV + "/addresses", data={"addresses": "anna@example.org\nben@example.org"})
    client.post(INV + "/send", data={"kind": "invite"})
    links = {m["To"]: _link(m) for m in sent}
    url = links["anna@example.org"].split("?")[0]
    assert "Nur mit persönlichem Link teilnehmen" in client.get("/surveys/1/field").data.decode()
    assert app.test_client().get(url).status_code == 200  # off: the general link works
    client.post(INV + "/texts", data={"only": "1"})
    with app.app_context():
        assert survey_invites.settings(db.session.get(Survey, 1))["only"]
    page = app.test_client().get(url)
    assert page.status_code == 400 and "unvollständig" in page.data.decode()
    assert app.test_client().get(url + "?einladung=falsch").status_code == 400
    anna = app.test_client()
    assert anna.get(links["anna@example.org"]).status_code == 200
    forwarded = app.test_client()  # the same link in another browser, opened before Anna began
    assert forwarded.get(links["anna@example.org"]).status_code == 200
    assert anna.post(url, data=CONSENT_OK).status_code == 302  # Anna begins: the link is used now
    assert "bereits teilgenommen" in forwarded.post(url, data=CONSENT_OK, follow_redirects=True).data.decode()
    assert "bereits teilgenommen" in app.test_client().get(links["anna@example.org"]).data.decode()
    assert anna.get(url).status_code == 200  # Anna herself goes on
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(source="real")) == 1
    ben = app.test_client()
    ben.get(links["ben@example.org"])
    assert ben.post(url, data=CONSENT_OK).status_code == 302
    client.post(INV + "/delete")
    assert app.test_client().get(url).status_code == 200  # no list: the general link works again


def test_a_cell_beyond_the_csv_field_limit_is_no_server_error(app, client, survey):
    """Gesamtaudit 2026-10-22, C-03: one quoted cell longer than csv.field_size_limit() raised csv.Error (a 500), in
    the address list here and in "Liste einfügen" of a choice question."""
    from werkzeug.datastructures import MultiDict

    long = 'x;"' + "A" * 140_000 + '"\n'
    mail_on(app)
    assert client.post(INV + "/addresses", data={"addresses": "email;name\n" + long}).status_code in (200, 302)
    assert survey_invites.rows_of("email;name\n" + long)[0] == []
    add(client, "single_choice")
    r = client.post("/surveys/1/editor/items/2", data=MultiDict(
        [("code", "q"), ("question", "Q?"), ("answer_mode", "optional"), ("options_paste", long)]))
    assert r.status_code in (200, 302)
