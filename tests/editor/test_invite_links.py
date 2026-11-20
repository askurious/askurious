"""Personal invitation links for several people at once (app/invite_links.py, Ralf 2026-11-20, E1-E9): one one-time
link per person, also with mail off; roles "Bearbeiten"/"Ansehen" only; who comes in adds nobody; the access ends on
the chosen day (membership, and the period of an account that came in this way alone)."""
import re
from datetime import timedelta

from app import invite_links
from app.extensions import db
from app.models import MemberInvite, SurveyMember, User, UserLogin, utcnow
from app.timeutil import local_today
from tests.conftest import mail_on

GOOD = "ein langer Satz als Passwort"


def _form(people, role="editor", days="14", end=None):
    end = end or (local_today() + timedelta(days=60)).isoformat()
    return {"person_name": [n for n, _e in people], "person_email": [e for _n, e in people], "role": role,
            "days": days, "access_until": end}


def _links(page):
    return re.findall(r'value="(https?://[^"]*/invite/([A-Za-z0-9_-]+))"', page)


def test_dialog_and_links_without_mail(app, client, survey):
    page = client.get(f"/surveys/{survey}").data.decode()
    assert "Mehrere Personen per Link einladen" in page and 'name="access_until"' in page
    r = client.post(f"/invite/links/survey/{survey}", data=_form([("Anna Becker", "anna@example.org"), ("Jonas Kraft", "")]))
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    page = r.data.decode()
    links = _links(page)
    assert len(links) == 2 and "Anna Becker" in page and "Jonas Kraft" in page
    assert "Per Mail senden" not in page  # mail is off
    with app.app_context():
        rows = db.session.scalars(db.select(MemberInvite).order_by(MemberInvite.id)).all()
        assert [r.name for r in rows] == ["Anna Becker", "Jonas Kraft"] and rows[1].email == ""
        assert all(r.handed_out and r.role == "editor" and r.access_until is not None for r in rows)
        assert all(r.token_hash not in page for r in rows)  # only hashes kept
    page = client.get(f"/surveys/{survey}").data.decode()
    assert "Offene Einladungen" in page and "Neuer Link" in page and "Jonas Kraft" in page


def test_no_admin_role_and_limits(app, client, survey):
    assert client.post(f"/invite/links/survey/{survey}", data=_form([("A", "")], role="admin")).status_code == 400
    many = [(f"Person {i}", "") for i in range(invite_links.MAX_PEOPLE + 1)]
    page = client.post(f"/invite/links/survey/{survey}", data=_form(many), follow_redirects=True).data.decode()
    assert "Höchstens 20 Personen" in page
    too_late = (local_today() + timedelta(days=500)).isoformat()
    page = client.post(f"/invite/links/survey/{survey}", data=_form([("A", "")], end=too_late),
                       follow_redirects=True).data.decode()
    assert "höchstens 12 Monate" in page
    page = client.post(f"/invite/links/survey/{survey}", data=_form([("", "x@example.org")]),
                       follow_redirects=True).data.decode()
    assert "fehlt der Name" in page
    with app.app_context():
        assert db.session.scalar(db.select(MemberInvite)) is None


def test_only_admins_create(app, client, survey):
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Zweite")
    other.post("/terms/", data={"accept": "1"})
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0002-1825-0097"))
        db.session.add(SurveyMember(survey_id=survey, user=user, role="editor", position=5))
        db.session.commit()
    r = other.post(f"/invite/links/survey/{survey}", data=_form([("A", "")]))
    assert r.status_code in (302, 303)
    with app.app_context():
        assert db.session.scalar(db.select(MemberInvite)) is None


def test_accept_without_address_new_account_ends_with_access(app, client, survey):
    end = local_today() + timedelta(days=60)
    page = client.post(f"/invite/links/survey/{survey}", data=_form([("Jonas Kraft", "")], end=end.isoformat())).data.decode()
    (_link, token), = _links(page)
    guest = app.test_client()
    page = guest.get(f"/invite/{token}").data.decode()
    assert "für Jonas Kraft bestimmt" in page and 'name="email"' in page
    guest.post(f"/invite/{token}", data={"way": "password", "name": "Jonas", "email": "jonas@example.org",
                                         "password": GOOD, "repeat": GOOD})
    with app.app_context():
        login = db.session.scalar(db.select(UserLogin).filter_by(email="jonas@example.org"))
        member = db.session.scalar(db.select(SurveyMember).filter_by(survey_id=survey, user_id=login.user_id))
        assert member.role == "editor" and member.access_until is not None
        assert login.valid_until == member.access_until  # the account ends with the access (E9)
        assert db.session.scalar(db.select(MemberInvite)) is None
    # who came in this way adds nobody
    guest.post(f"/surveys/{survey}/members", data={"orcid": "0000-0002-1825-0097", "role": "editor"})
    with app.app_context():
        assert db.session.scalar(db.select(SurveyMember).filter_by(survey_id=survey).join(
            User, User.id == SurveyMember.user_id).filter(User.orcid == "0000-0002-1825-0097")) is None
    assert "nicht mehr gültig" in app.test_client().get(f"/invite/{token}", follow_redirects=True).data.decode()


def test_existing_account_keeps_its_period_and_membership_ends(app, client, survey):
    page = client.post(f"/invite/links/survey/{survey}", data=_form([("Zweite", "")])).data.decode()
    (_link, token), = _links(page)
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Zweite")
    other.post("/terms/", data={"accept": "1"})
    other.post(f"/invite/{token}")
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0002-1825-0097"))
        member = db.session.scalar(db.select(SurveyMember).filter_by(survey_id=survey, user_id=user.id))
        assert member.role == "editor" and member.access_until is not None
        login = db.session.get(UserLogin, user.id)
        assert login is None or login.valid_until is None  # the account keeps its own period
        assert invite_links.end_due(now=utcnow()) == 0
        assert invite_links.end_due(now=member.access_until + timedelta(minutes=1)) == 1
        assert db.session.scalar(db.select(SurveyMember).filter_by(survey_id=survey, user_id=user.id)) is None


def test_renew_withdraw_and_csv(app, client, survey):
    page = client.post(f"/invite/links/survey/{survey}", data=_form([("Anna", "anna@example.org")])).data.decode()
    (_link, old), = _links(page)
    r = client.post(f"/invite/links/survey/{survey}/csv", data={"token": old})
    text = r.data.decode("utf-8-sig")
    assert r.status_code == 200 and "anna@example.org" in text and old in text
    with app.app_context():
        invite_id = db.session.scalar(db.select(MemberInvite.id))
    page = client.post(f"/invite/links/{invite_id}/renew").data.decode()
    (_link, new), = _links(page)
    assert new != old
    assert "nicht mehr gültig" in app.test_client().get(f"/invite/{old}", follow_redirects=True).data.decode()
    assert "für Anna bestimmt" in app.test_client().get(f"/invite/{new}").data.decode()
    client.post(f"/invite/withdraw/{invite_id}")
    assert "nicht mehr gültig" in app.test_client().get(f"/invite/{new}", follow_redirects=True).data.decode()


def test_send_by_mail(app, client, survey):
    sent = mail_on(app)
    page = client.post(f"/invite/links/survey/{survey}",
                       data=_form([("Anna", "anna@example.org"), ("Ohne", "")])).data.decode()
    assert page.count("Per Mail senden") == 1 and "keine Adresse" in page
    tokens = [t for _l, t in _links(page)]
    assert sent == []  # nothing goes out by itself
    r = client.post(f"/invite/links/survey/{survey}/mail", data={"token": tokens[0]})
    assert r.get_json()["ok"] and sent[-1]["To"] == "anna@example.org" and tokens[0] in sent[-1].get_content()
    assert client.post(f"/invite/links/survey/{survey}/mail", data={"token": tokens[1]}).status_code == 400


def test_csv_cells_are_no_formulas():
    assert invite_links._cell("=HYPERLINK()") == "'=HYPERLINK()" and invite_links._cell("Anna") == "Anna"
