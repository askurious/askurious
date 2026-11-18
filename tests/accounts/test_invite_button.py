"""The button "Per E-Mail einladen" in "Person hinzufügen" (_people.html) and the line that says why not
(app/invites.py invite_refusal): shown only to whoever may invite there, with the same rule create() applies, so
that a refused person sees the reason and the server still refuses the POST."""
from datetime import date

from app import invites
from app.extensions import db
from app.models import MemberInvite, Survey, UserLogin
from tests.accounts.test_org_invite_scope import MEMBER, _new_group, _setup, _user
from tests.conftest import mail_on

NEW = "neu@example.org"
BUTTON = 'data-dialog-open="invite-mail-member-q"'
GROUP_BUTTON = 'data-dialog-open="invite-mail-group-member-q"'


def _survey_of(client, title):
    client.post("/surveys/new", data={"title": title, "languages": ["de"], "default_language": "de"})
    with client.application.app_context():
        return db.session.scalar(db.select(Survey.id).filter_by(title=title))


def test_button_and_dialog_for_whoever_may_invite(app, client, survey):
    mail_on(app)
    page = client.get(f"/surveys/{survey}").data.decode()
    assert BUTTON in page and 'id="invite-mail-member-q"' in page
    assert f'action="/invite/survey/{survey}"' in page and 'type="email" name="email" required' in page
    assert "14 Tage und nur einmal" in page and "beide Wege sind gleichwertig" in page
    assert "person-invite-refused" not in page


def test_no_button_without_mail(app, client, survey):
    page = client.get(f"/surveys/{survey}").data.decode()
    assert BUTTON not in page and "person-invite-refused" not in page


def test_scope_admins_shows_the_reason_and_refuses_the_post(app, client):
    mail_on(app)
    clients = _setup(app, "admins")
    member = clients[MEMBER]
    sid = _survey_of(member, "Studie")
    page = member.get(f"/surveys/{sid}").data.decode()
    assert BUTTON not in page and "person-invite-refused" in page
    assert "nur Admins der Organisation Personen von außen" in page and "Bitte einen Admin der Organisation fragen" in page
    page = member.get(f"/surveys/{sid}?q={NEW}").data.decode()
    assert f'action="/invite/survey/{sid}"' not in page          # no invitation form after the search either
    said = member.post(f"/invite/survey/{sid}", data={"email": NEW, "role": "editor"}, follow_redirects=True).data.decode()
    assert "nur Admins der Organisation Personen von außen" in said
    gid = _new_group(member, "Kurs")
    page = member.get(f"/groups/{gid}").data.decode()
    assert GROUP_BUTTON not in page and "Bitte einen Admin der Organisation fragen" in page
    with app.app_context():
        assert db.session.scalar(db.select(MemberInvite)) is None
        assert invites.invite_refusal(_user(MEMBER), survey=db.session.get(Survey, sid))


def test_account_with_a_period_shows_the_reason_and_refuses_the_post(app, client):
    mail_on(app)
    clients = _setup(app, "create")
    member = clients[MEMBER]
    sid = _survey_of(member, "Studie")
    assert BUTTON in member.get(f"/surveys/{sid}").data.decode()
    with app.app_context():
        db.session.get(UserLogin, _user(MEMBER).id).valid_until = date(2099, 1, 1)
        db.session.commit()
    page = member.get(f"/surveys/{sid}").data.decode()
    assert BUTTON not in page and "Ihr Konto gilt nur für einen Zeitraum" in page
    said = member.post(f"/invite/survey/{sid}", data={"email": NEW, "role": "editor"}, follow_redirects=True).data.decode()
    assert "Ihr Konto gilt nur für einen Zeitraum" in said
    with app.app_context():
        assert db.session.scalar(db.select(MemberInvite)) is None


def test_scope_platform_names_the_platform_admins(app, client):
    mail_on(app)
    member = _setup(app, "platform")[MEMBER]
    sid = _survey_of(member, "Studie")
    page = member.get(f"/surveys/{sid}").data.decode()
    assert BUTTON not in page and "nur Admins der Plattform" in page and "Bitte einen Admin der Plattform fragen" in page
