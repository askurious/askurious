"""Researchers by e-mail (app/invites.py, mailversand-konzept.md M5, M5a): inviting by address to a survey or a group
with mail on, accepting with a new password account, with ORCID or signed in, withdrawing, and notice mails."""
import re

from app import invites
from app.extensions import db
from app.models import MemberInvite, SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember
from tests.conftest import mail_on

NEW = "kollegin@example.org"
GOOD = "ein langer Satz als Passwort"


def _token(msg):
    return re.search(r"/invite/(\S+)", msg.get_content()).group(1)


def test_nothing_without_mail(app, client, survey):
    page = client.get(f"/surveys/{survey}?q={NEW}#members").data.decode()
    assert "Per E-Mail einladen" not in page
    assert client.post(f"/invite/survey/{survey}", data={"email": NEW, "role": "editor"}).status_code == 400


def test_invite_to_a_survey_and_accept_with_a_password(app, client, survey):
    sent = mail_on(app)
    page = client.get(f"/surveys/{survey}?q={NEW}").data.decode()
    assert "Per E-Mail einladen" in page
    page = client.post(f"/invite/survey/{survey}", data={"email": NEW, "role": "viewer"}, follow_redirects=True).data.decode()
    assert f"Einladung an {NEW} verschickt" in page and "Offene Einladungen per E-Mail" in page
    msg = sent[-1]
    assert msg["To"] == NEW and "Test" in msg["Subject"] and "14 Tage" in msg.get_content()
    token = _token(msg)
    with app.app_context():
        row = db.session.scalar(db.select(MemberInvite))
        assert row.token_hash != token and row.role == "viewer"  # only the hash
    guest = app.test_client()
    page = guest.get(f"/invite/{token}").data.decode()
    assert "Konto anlegen und annehmen" in page and NEW in page
    assert "Passwort braucht mindestens" in guest.post(f"/invite/{token}", data={"way": "password", "name": "Kim", "password": "kurz", "repeat": "kurz"}, follow_redirects=True).data.decode()
    guest.post(f"/invite/{token}", data={"way": "password", "name": "Kim", "password": GOOD, "repeat": GOOD})
    with app.app_context():
        login = db.session.scalar(db.select(UserLogin).filter_by(email=NEW))
        user = db.session.get(User, login.user_id)
        member = db.session.scalar(db.select(SurveyMember).filter_by(survey_id=survey, orcid=user.orcid))
        assert member.role == "viewer" and login.password_hash and login.may_create is None  # no right to create (S11)
        assert db.session.scalar(db.select(MemberInvite)) is None
    assert "nicht mehr gültig" in app.test_client().get(f"/invite/{token}", follow_redirects=True).data.decode()  # once only


def test_signed_in_person_accepts(app, client, survey):
    sent = mail_on(app)
    client.post(f"/invite/survey/{survey}", data={"email": NEW, "role": "editor"})
    token = _token(sent[-1])
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Zweite")
    other.post("/terms/", data={"accept": "1"})
    assert "Einladung annehmen als Zweite" in other.get(f"/invite/{token}").data.decode()
    other.post(f"/invite/{token}")
    with app.app_context():
        assert db.session.scalar(db.select(SurveyMember).filter_by(survey_id=survey, orcid="0000-0002-1825-0097")).role == "editor"


def test_orcid_way_makes_the_member_before_the_sign_in_check(app, client, survey):
    sent = mail_on(app)
    client.post(f"/invite/survey/{survey}", data={"email": NEW, "role": "editor"})
    token = _token(sent[-1])
    guest = app.test_client()
    app.config["ORCID_CLIENT_ID"] = "test"
    guest.get(f"/invite/{token}")
    with guest.session_transaction() as s:
        assert invites.SESSION_KEY not in s                                     # merely opened: nothing kept (T6)
    r = guest.post(f"/invite/{token}", data={"way": "orcid"})
    assert "/auth/login" in r.headers["Location"]
    with guest.session_transaction() as s:
        held = s[invites.SESSION_KEY]
    assert token not in str(held)                                               # encrypted in the cookie
    with app.test_request_context():
        from flask import session

        session[invites.SESSION_KEY] = held
        goal = invites.accept_after_orcid("0000-0000-0000-0124", "Dritte")
        assert goal.endswith(f"/surveys/{survey}")
        assert invites.SESSION_KEY not in session                               # used once, then gone
    with app.app_context():
        member = db.session.scalar(db.select(SurveyMember).filter_by(orcid="0000-0000-0000-0124"))
        assert not member.signed_up and member.name == "Dritte"  # a pending account (B2)
        from app.auth import _may_sign_in

        assert _may_sign_in("0000-0000-0000-0124")


def test_withdraw(app, client, survey):
    mail_on(app)
    client.post(f"/invite/survey/{survey}", data={"email": NEW, "role": "editor"})
    with app.app_context():
        invite_id = db.session.scalar(db.select(MemberInvite.id))
    page = client.post(f"/invite/withdraw/{invite_id}", follow_redirects=True).data.decode()
    assert "Einladung zurückgezogen" in page
    with app.app_context():
        assert db.session.scalar(db.select(MemberInvite)) is None


def test_invite_to_a_group(app, client):
    sent = mail_on(app)
    client.post("/groups/new", data={"name": "Team"})
    with app.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id))
    client.post(f"/invite/group/{gid}", data={"email": NEW, "role": "member"})
    token = _token(sent[-1])
    assert "Gruppe" in sent[-1]["Subject"]
    guest = app.test_client()
    guest.post(f"/invite/{token}", data={"way": "password", "name": "Kim", "password": GOOD, "repeat": GOOD})
    with app.app_context():
        assert db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, name="Kim")).role == "member"


def test_notice_when_an_account_is_added_and_switching_it_off(app, client, survey):
    sent = mail_on(app)
    client.post("/admin/accounts/new", data={"name": "Gast", "email": NEW})
    with app.app_context():
        guest = db.session.scalar(db.select(User).join(UserLogin, UserLogin.user_id == User.id).filter(UserLogin.email == NEW))
        key = f"u{guest.id}"  # how forms name an account without ORCID (people_keys.key_for)
    sent.clear()
    client.post(f"/surveys/{survey}/members", data={"orcid": key, "role": "editor"})
    assert len(sent) == 1 and sent[0]["To"] == NEW and "beteiligt" in sent[0].get_content()
    with app.app_context():
        user = db.session.get(User, int(key[1:]))
        user.display = {"mail_notices": False}
        db.session.commit()
    client.post("/groups/new", data={"name": "Team"})
    with app.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id))
    client.post(f"/groups/{gid}/members", data={"orcid": key, "role": "member"})
    assert len(sent) == 1  # switched off


def test_account_page_switch_only_with_mail(app, client):
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        db.session.add(UserLogin(user_id=me.id, email="admin@example.org"))
        db.session.commit()
        my_id = me.id
    assert "Hinweis per E-Mail" not in client.get("/account/appearance").data.decode()
    mail_on(app)
    assert "Hinweis per E-Mail" in client.get("/account/appearance").data.decode()
    client.post("/account/mail-notices", data={})
    with app.app_context():
        assert db.session.get(User, my_id).display["mail_notices"] is False


def test_survey_deletion_takes_its_invitations(app, client, survey):
    mail_on(app)
    client.post(f"/invite/survey/{survey}", data={"email": NEW, "role": "editor"})
    from app import lifecycle
    from app.models import Survey

    with app.app_context():
        lifecycle.delete(db.session.get(Survey, survey))
        db.session.commit()
        assert db.session.scalar(db.select(MemberInvite)) is None


def test_an_invitation_to_someone_already_involved_raises_the_role(app, client, survey):
    """Code review 2026-10-15, H7: the invitation was used up and the role stayed; now the higher of both counts, as
    with a join link, and never a lower one."""
    sent = mail_on(app)
    other_id = "0000-0002-1825-0097"
    with app.app_context():
        db.session.add(SurveyMember(survey_id=survey, orcid=other_id, name="Zweite", role="viewer", position=1))
        db.session.commit()
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={other_id}&name=Zweite")
    other.post("/terms/", data={"accept": "1"})
    for invited, kept in (("editor", "editor"), ("viewer", "editor")):
        client.post(f"/invite/survey/{survey}", data={"email": NEW, "role": invited})
        other.post(f"/invite/{_token(sent[-1])}")
        with app.app_context():
            assert db.session.scalar(db.select(SurveyMember).filter_by(survey_id=survey, orcid=other_id)).role == kept
            assert db.session.scalar(db.select(MemberInvite)) is None
