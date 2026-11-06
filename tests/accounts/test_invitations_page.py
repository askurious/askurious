"""Administration > Einladungen (app/open_invitations.py) and the third way of "Person hinzufügen", "Nur Link"
(app/signup_links.py, SignUpLink.password_ok): one person chooses ORCID or an email address with a new password,
both equal (Ralf 2026-11-06)."""
import re
from datetime import timedelta

from flask import session

from app import kind_access, log_mask, orgs, signup_links
from app.auth import _sign_in, may_create_surveys
from app.extensions import db
from app.models import (AccountType, GroupJoinLink, MemberInvite, Organisation, OrganisationMember, SignInOrcid, SignUpLink, User,
                        UserLogin,
                        WorkGroup, utcnow)
from tests.conftest import allow

PASSWORD = "ein-langes-gutes-passwort-42"


def _org(app, **kw):
    with app.app_context():
        org = Organisation(name="Institut Y", **kw)
        db.session.add(org)
        db.session.commit()
        return org.id


def _type(app, org_id):
    with app.app_context():
        return str(db.session.scalar(db.select(AccountType.id).where(AccountType.org_id == org_id)) or "")


def _make(client, org_id, **form):
    data = {"org": org_id, "account_type": form.pop("account_type", ""), "valid_mode": "type", "days": "14",
            "note": "Für Kim"}
    data.update(form)
    client.post("/admin/accounts/new-link", data=data)
    found = re.search(r"/signup/([A-Za-z0-9_-]{30,})", client.get("/admin/people").data.decode())
    return found.group(1) if found else None


def _register(app, token, **form):
    data = {"way": "password", "name": "Kim Neu", "email": "kim@example.org", "password": PASSWORD, "repeat": PASSWORD}
    data.update(form)
    return app.test_client().post(f"/signup/{token}", data=data)


def _way(page, way):
    start = page.index(f'data-way="{way}"')
    return page[start:page.index("</section>", start)]


def test_the_dialog_asks_how_and_gives_the_same_choices_on_all_three_ways(app, client):
    org_id = _org(app)
    for page in (client.get("/admin/people").data.decode(), client.get(f"/org/{org_id}/people").data.decode()):
        assert "Wie möchten Sie die Person hinzufügen?" in page and "Hat die Person eine ORCID iD?" not in page
        assert page.index('value="orcid"') < page.index('name="person_way" value="link"') < page.index('value="local"')
        assert page.index('data-way="orcid"') < page.index('data-way="link"') < page.index('data-way="local"')
        for way in ("orcid", "link", "local"):
            part = _way(page, way)
            for field in ('name="may_create"', 'name="account_type"', 'name="valid_mode"', 'name="kind_'):
                assert field in part, (way, field)


def _levels(user):
    return kind_access.full(kind_access.stored(user))


def test_the_choices_of_a_link_reach_the_account_with_a_password(app, client):
    org_id = _org(app)
    token = _make(client, org_id, kind_survey="view", kind_interview="edit", may_create="")
    _register(app, token)
    with app.app_context():
        login = db.session.scalar(db.select(UserLogin).filter_by(email="kim@example.org"))
        levels = _levels(db.session.get(User, login.user_id))
        assert levels["survey"] == "view" and levels["interview"] == "edit"


def test_the_tick_of_a_link_gives_create(app, client):
    org_id = _org(app)
    token = _make(client, org_id, kind_interview="edit", may_create="1")
    _register(app, token)
    with app.app_context():
        login = db.session.scalar(db.select(UserLogin).filter_by(email="kim@example.org"))
        assert _levels(db.session.get(User, login.user_id))["interview"] == "create"


def test_the_choices_of_a_link_reach_the_list_for_orcid(app, client):
    org_id = _org(app)
    token = _make(client, org_id, kind_interview="view", may_create="")
    stranger = app.test_client()
    app.config["ORCID_CLIENT_ID"] = "test"
    stranger.post(f"/signup/{token}", data={"way": "orcid"})
    with stranger.session_transaction() as s:
        held = s[signup_links.SESSION_KEY]
    with app.test_request_context():
        session[signup_links.SESSION_KEY] = held
        assert signup_links.accept_after_orcid("0000-0000-0000-0124", "Kim")
        row = db.session.get(SignInOrcid, "0000-0000-0000-0124")
        assert row.may_create is False and row.kinds["interview"] == "view"


def test_without_the_tick_an_id_on_the_list_may_not_create(app, client):
    app.config["NEW_ACCOUNTS_MAY_CREATE"] = True
    orcid = "0000-0000-0000-0124"
    client.post("/admin/admit", data={"action": "add", "orcid": orcid, "valid_mode": "type"})
    with app.test_request_context():
        row = db.session.get(SignInOrcid, orcid)
        assert row is not None and row.may_create is False
        user = _sign_in(orcid, "Kim")
        assert not may_create_surveys(user)


def test_with_the_tick_an_id_on_the_list_may_create(app, client):
    orcid = "0000-0000-0000-0124"
    client.post("/admin/admit", data={"action": "add", "orcid": orcid, "valid_mode": "type", "may_create": "1"})
    with app.test_request_context():
        assert may_create_surveys(_sign_in(orcid, "Kim"))


def test_the_link_offers_both_ways_and_makes_an_account_at_home(app, client):
    org_id = _org(app)
    token = _make(client, org_id)
    assert token
    app.config["ORCID_CLIENT_ID"] = "test"
    page = app.test_client().get(f"/signup/{token}").data.decode()
    assert "Mit ORCID anmelden" in page and 'value="password"' in page
    assert page.index("Mit ORCID anmelden") < page.index('value="password"')  # both ways, ORCID listed first
    assert _register(app, token).status_code == 302
    with app.app_context():
        login = db.session.scalar(db.select(UserLogin).filter_by(email="kim@example.org"))
        user = db.session.get(User, login.user_id)
        assert orgs.home_id(user) == org_id and user.orcid is None and login.password_hash
        link = db.session.scalar(db.select(SignUpLink))
        assert link.uses == 1 and link.max_uses == 1 and link.password_ok
    assert _register(app, token, email="zwei@example.org").status_code == 302  # used: back to the start
    with app.app_context():
        assert db.session.scalar(db.select(UserLogin).filter_by(email="zwei@example.org")) is None


def test_a_plain_link_stays_orcid_only(app, client):
    org_id = _org(app)
    client.post("/admin/signup-links", data={"action": "create", "org": org_id, "account_type": "", "period": "t:30",
                                             "max_uses": "5", "days": "14"})
    token = re.search(r"/signup/([A-Za-z0-9_-]{30,})", client.get("/admin/people").data.decode()).group(1)
    assert 'value="password"' not in app.test_client().get(f"/signup/{token}").data.decode()
    _register(app, token)
    with app.app_context():
        assert db.session.scalar(db.select(UserLogin).filter_by(email="kim@example.org")) is None


def test_a_taken_address_and_a_full_organisation_use_nothing(app, client):
    org_id = _org(app)
    first = _make(client, org_id)
    _register(app, first)
    second = _make(client, org_id, note="noch eine")
    assert "kein neues Konto" in _register(app, second).data.decode()
    with app.app_context():
        link = db.session.scalar(db.select(SignUpLink).where(SignUpLink.note == "noch eine"))
        assert link.uses == 0
        db.session.get(Organisation, org_id).max_accounts = 1
        db.session.commit()
    _register(app, second, email="drei@example.org")
    with app.app_context():
        assert db.session.scalar(db.select(UserLogin).filter_by(email="drei@example.org")) is None


def test_a_link_rests_when_its_maker_is_no_admin_any_more(app, client):
    org_id = _org(app)
    token = _make(client, org_id)
    with app.app_context():
        link = db.session.scalar(db.select(SignUpLink))
        link.created_by_id = 999999
        db.session.commit()
        assert signup_links.valid(token) is None


def test_the_page_lists_open_invitations_by_type(app, client):
    org_id = _org(app)
    _make(client, org_id, note="Für Kim")
    with app.app_context():
        group = WorkGroup(name="AG Offen")
        db.session.add(group)
        db.session.flush()
        later = utcnow() + timedelta(days=3)
        db.session.add_all([
            GroupJoinLink(group_id=group.id, role="lead", token_hash="a" * 64, max_uses=1, created_by_id=None,
                          expires_at=later),
            GroupJoinLink(group_id=group.id, role="member", token_hash="b" * 64, max_uses=5, created_by_id=None,
                          expires_at=utcnow() - timedelta(days=1)),
            MemberInvite(group_id=group.id, role="member", email="gast@example.org", token_hash="c" * 64,
                         expires_at=later)])
        pending = User(orcid="0000-0002-1825-0097", pending=True, name="Erste Admin")
        db.session.add(pending)
        db.session.flush()
        db.session.add(OrganisationMember(org_id=org_id, user_id=pending.id, role="admin"))
        db.session.commit()
    page = client.get("/admin/invitations").data.decode()
    parts = {k: page[page.index(f'id="{k}"'):] for k in ("organisationen", "gruppen", "personen")}
    assert "Erste Admin" in parts["organisationen"][:parts["organisationen"].index('id="gruppen"')]
    groups = parts["gruppen"][:parts["gruppen"].index('id="personen"')]
    assert "Leitungslink" in groups and "gast@example.org" in groups and "<td>Beitrittslink</td>" not in groups  # expired: gone
    assert "Für Kim" in parts["personen"] and "/signup/" not in page


def test_only_site_admins_see_the_page(app, client):
    allow(app, "0000-0002-1825-0097")
    assert app.test_client().get("/admin/invitations").status_code in (302, 401, 403)


def test_a_registration_token_is_masked_in_the_log():
    assert "abcdefghijklmnopqrstuvwxyz" not in log_mask.mask("GET /signup/abcdefghijklmnopqrstuvwxyz012345")


def test_each_part_pages_by_10_20_50(app, client):
    """E3 (Ralf 2026-11-06): lists paginate 10/20/50 rows, here each of the three parts on its own."""
    with app.app_context():
        group = WorkGroup(name="AG Viele")
        db.session.add(group)
        db.session.flush()
        later = utcnow() + timedelta(days=3)
        db.session.add_all([MemberInvite(group_id=group.id, role="member", email=f"p{i:02d}@example.org",
                                         token_hash=f"{i:064d}", expires_at=later) for i in range(12)])
        db.session.commit()
    page = client.get("/admin/invitations").data.decode()
    groups = page[page.index('id="gruppen"'):page.index('id="personen"')]
    assert groups.count("@example.org</td>") == 10 and ">12</span>" in groups and "ig_page=2" in groups
    groups = client.get("/admin/invitations?ig_n=20").data.decode()
    assert groups.count("@example.org</td>") == 12
