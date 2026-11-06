"""Founding links (app/org_links.py): a new person creates an organisation and becomes its admin, through the real ORCID
callback; and the limit of accounts of an organisation on the ways that did not check it."""
import re
from datetime import timedelta

import pytest

from app import log_mask, notify, org_links, orgs
from app.auth import _may_sign_in
from app.extensions import db
from app.models import (GroupJoinLink, MemberInvite, Organisation, OrganisationMember, OrgFoundLink, SignInOrcid, User,
                        WorkGroup, WorkGroupMember, utcnow)
from tests.conftest import allow

NEW = "0000-0000-0000-0124"
SECOND = "0000-0000-0000-0239"
EXISTING = "0000-0002-1825-0097"


class _Answer:
    status_code = 200

    def __init__(self, orcid):
        self.orcid = orcid

    def json(self):
        return {"orcid": self.orcid, "name": "Kim Neu"}


@pytest.fixture
def closed(app):
    """Sign-in only with an invitation; the iDs of the tests are on no list."""
    app.config["ORCID_CLIENT_ID"] = "test"
    app.config["ORCID_CLIENT_SECRET"] = "test"
    return app


def _make(client, **form):
    data = {"action": "create", "days": "28", "max_accounts": "100", "note": "Für Kim"}
    data.update(form)
    client.post("/admin/orgs/links", data=data)
    found = re.search(r"/joinorg/([A-Za-z0-9_-]{30,})", client.get("/admin/orgs").data.decode())
    return found.group(1) if found else None


def _orcid(guest, monkeypatch, orcid):
    """Come back from ORCID as this iD."""
    import app.auth as auth

    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: _Answer(orcid))
    with guest.session_transaction() as s:
        s["oauth_state"] = "st"
    return guest.get("/auth/orcid/callback?state=st&code=x")


def _found(app, monkeypatch, token, orcid=NEW, name="Institut Neu", **form):
    guest = app.test_client()
    answer = guest.post(f"/joinorg/{token}", data={"name": name, **form})
    assert "/auth/login" in answer.headers["Location"]
    return guest, _orcid(guest, monkeypatch, orcid)


def test_the_button_stands_on_the_organisations_page_with_four_weeks_and_100(client):
    page = client.get("/admin/orgs").data.decode()
    assert 'href="#gruendungslink-anlegen"' in page and 'id="gruendungslinks"' in page
    assert '<option value="28" selected>' in page and 'name="max_accounts" min="1" max="100000" value="100"' in page


def test_a_new_person_gets_account_and_organisation_past_the_closed_sign_in(closed, client, monkeypatch):
    app = closed
    app.config["NEW_ACCOUNTS_MAY_CREATE"] = True  # the default of a server; tests/conftest.py switches it off
    token = _make(client)
    with app.app_context():
        link = db.session.scalar(db.select(OrgFoundLink))
        assert timedelta(days=27) < link.expires_at - utcnow() <= timedelta(days=28)
        assert not _may_sign_in(NEW)
    guest, back = _found(app, monkeypatch, token, long_name="Institut für Neues")
    assert "/org/" in back.headers["Location"] or back.status_code == 302
    with guest.session_transaction() as s:
        assert "_user_id" in s  # signed in
    with app.app_context():
        org = db.session.scalar(db.select(Organisation).filter_by(name="Institut Neu"))
        user = db.session.scalar(db.select(User).filter_by(orcid=NEW))
        assert org.long_name == "Institut für Neues" and org.max_accounts == 100 and org.invite_scope == "admins"
        assert not user.pending and orgs.home_id(user) == org.id and orgs.is_admin_of(user, org.id)
        link = db.session.scalar(db.select(OrgFoundLink))
        assert link.used_at is not None and link.org_id == org.id and link.used_by_id == user.id
        from app.auth import may_create_surveys

        assert may_create_surveys(user)


def test_a_used_link_works_no_more(closed, client, monkeypatch):
    app = closed
    token = _make(client)
    _found(app, monkeypatch, token)
    assert app.test_client().get(f"/joinorg/{token}").status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Organisation.id)).filter_by(name="Institut Neu")) == 1
        assert db.session.scalar(db.select(User).filter_by(orcid=SECOND)) is None


def test_two_people_at_the_last_moment_make_one_organisation(closed, client, monkeypatch):
    app = closed
    token = _make(client)
    first = app.test_client()
    second = app.test_client()
    first.post(f"/joinorg/{token}", data={"name": "Erste"})
    second.post(f"/joinorg/{token}", data={"name": "Zweite"})
    _orcid(first, monkeypatch, NEW)
    _orcid(second, monkeypatch, SECOND)
    with app.app_context():
        names = set(db.session.scalars(db.select(Organisation.name)))
        assert "Erste" in names and "Zweite" not in names
        assert db.session.scalar(db.select(User).filter_by(orcid=SECOND)) is None


def test_breaking_off_leaves_nothing(closed, client):
    app = closed
    token = _make(client)
    guest = app.test_client()
    guest.post(f"/joinorg/{token}", data={"name": "Abgebrochen"})
    with guest.session_transaction() as s:
        s["oauth_state"] = "st"
    guest.get("/auth/orcid/callback?state=st&error=access_denied")
    with app.app_context():
        assert db.session.scalar(db.select(OrgFoundLink)).used_at is None
        assert db.session.scalar(db.select(Organisation).filter_by(name="Abgebrochen")) is None
    assert app.test_client().get(f"/joinorg/{token}").status_code == 200


def test_an_existing_account_stays_and_the_link_stays_open(closed, client, monkeypatch):
    app = closed
    allow(app, EXISTING, keep=True)
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={EXISTING}&name=Da")
    token = _make(client)
    assert other.get(f"/joinorg/{token}").status_code == 302  # signed in: no form
    with app.app_context():
        home = orgs.home_id(db.session.scalar(db.select(User).filter_by(orcid=EXISTING)))
    _found(app, monkeypatch, token, orcid=EXISTING)
    with app.app_context():
        assert db.session.scalar(db.select(OrgFoundLink)).used_at is None
        assert orgs.home_id(db.session.scalar(db.select(User).filter_by(orcid=EXISTING))) == home
        assert db.session.scalar(db.select(Organisation).filter_by(name="Institut Neu")) is None


def test_a_name_taken_meanwhile_uses_nothing_and_signs_nobody_in(closed, client, monkeypatch):
    app = closed
    token = _make(client)
    guest = app.test_client()
    guest.post(f"/joinorg/{token}", data={"name": "Doppelt"})
    with app.app_context():
        db.session.add(Organisation(name="Doppelt"))
        db.session.commit()
        orgs.forget()
    back = _orcid(guest, monkeypatch, NEW)
    assert f"/joinorg/{token}" in back.headers["Location"]
    assert 'value="Doppelt"' in guest.get(f"/joinorg/{token}").data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(OrgFoundLink)).used_at is None
        assert db.session.scalar(db.select(User).filter_by(orcid=NEW, pending=False)) is None


def test_an_id_on_the_list_for_elsewhere_gets_the_new_home(closed, client, monkeypatch):
    app = closed
    with app.app_context():
        other = Organisation(name="Woanders")
        db.session.add(other)
        db.session.flush()
        db.session.add(SignInOrcid(orcid=NEW, note="", org_id=other.id))
        db.session.commit()
    _found(app, monkeypatch, _make(client))
    with app.app_context():
        org = db.session.scalar(db.select(Organisation).filter_by(name="Institut Neu"))
        assert orgs.home_id(db.session.scalar(db.select(User).filter_by(orcid=NEW))) == org.id


def test_revoked_expired_and_a_former_admin_end_a_link(closed, client):
    app = closed
    token = _make(client)
    with app.app_context():
        link_id = db.session.scalar(db.select(OrgFoundLink.id))
    client.post("/admin/orgs/links", data={"action": "revoke", "link_id": link_id})
    assert app.test_client().get(f"/joinorg/{token}").status_code == 302
    token = _make(client, note="zwei")
    with app.app_context():
        link = db.session.scalar(db.select(OrgFoundLink).where(OrgFoundLink.note == "zwei"))
        link.created_by_id = 999999
        db.session.commit()
        assert org_links.valid(token) is None


def test_only_site_admins_make_links(closed):
    app = closed
    allow(app, EXISTING, keep=True)
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={EXISTING}&name=Da")
    other.post("/admin/orgs/links", data={"action": "create", "days": "28", "max_accounts": "5"})
    with app.app_context():
        assert db.session.scalar(db.select(OrgFoundLink)) is None


@pytest.mark.parametrize("full", [False, True])
def test_every_new_organisation_is_reported_with_its_way_and_no_name(closed, client, monkeypatch, full):
    app = closed
    sent = []
    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: sent.append(text))
    monkeypatch.setattr(notify, "full", lambda: full)
    _found(app, monkeypatch, _make(client), name="<!channel> Geheim")
    client.post("/admin/orgs/new", data={"name": "Von Hand"})
    with app.app_context():
        group = WorkGroup(name="Wird Organisation")
        db.session.add(group)
        db.session.commit()
        group_id = group.id
    client.post(f"/admin/groups/{group_id}/to-org", data={"name": "Aus Gruppe"})
    orgs_said = [t for t in sent if "Neue Organisation" in t]
    assert len(orgs_said) == 3
    assert "Gründungslink" in orgs_said[0] and "Administration" in orgs_said[1] and "aus einer Gruppe" in orgs_said[2]
    for text in orgs_said:
        assert "Geheim" not in text and "Von Hand" not in text and "Aus Gruppe" not in text and NEW not in text


def test_the_invitations_page_lists_open_founding_links(client):
    _make(client, note="Für Kim")
    page = client.get("/admin/invitations").data.decode()
    part = page[page.index('id="organisationen"'):page.index('id="gruppen"')]
    assert "Gründungslink" in part and "Für Kim" in part


def test_the_token_is_masked_in_the_log():
    assert "abcdefghijklmnopqrstuvwxyz" not in log_mask.mask("GET /joinorg/abcdefghijklmnopqrstuvwxyz012345")


# --- the limit of accounts on the ways that did not check it ----------------------------------------------------------

def _full_group(app):
    """A group in an organisation without places: one account has its home there, the limit is one."""
    with app.app_context():
        org = Organisation(name="Voll", max_accounts=1)
        db.session.add(org)
        db.session.flush()
        filler = db.session.scalar(db.select(User))
        db.session.add(OrganisationMember(org_id=org.id, user_id=filler.id, is_home=True))
        group = WorkGroup(name="Voll AG", org_id=org.id)
        db.session.add(group)
        db.session.commit()
        orgs.forget()
        return org.id, group.id


def test_a_join_link_with_orcid_stops_at_the_limit(closed, client, monkeypatch):
    app = closed
    from app import joinlinks

    monkeypatch.setattr(joinlinks, "paused", lambda link, group=None: False)
    _org_id, group_id = _full_group(app)
    with app.test_request_context():
        link = GroupJoinLink(group_id=group_id, role="member", token_hash="d" * 64, max_uses=5, created_by_id=1,
                             expires_at=utcnow() + timedelta(days=3))
        db.session.add(link)
        db.session.commit()
        _goal, said = joinlinks.join(link, user=None, orcid=NEW, name="Kim")
        assert said and "Plätze" in said
        assert db.session.get(GroupJoinLink, link.id).uses == 0
        assert db.session.scalar(db.select(WorkGroupMember.id).where(WorkGroupMember.group_id == group_id)) is None


def test_a_mail_invitation_to_a_group_stops_at_the_limit_and_stays(closed, client):
    app = closed
    from app import invites

    _org_id, group_id = _full_group(app)
    with app.test_request_context():
        invite = MemberInvite(group_id=group_id, role="member", email="a@example.org", token_hash="e" * 64,
                              created_by_id=1, expires_at=utcnow() + timedelta(days=3))
        db.session.add(invite)
        db.session.commit()
        monkey = invites.authorised
        invites.authorised = lambda i: True
        try:
            _goal, said = invites.join(invite, orcid=NEW, name="Kim")
        finally:
            invites.authorised = monkey
        assert said and "Plätze" in said and db.session.get(MemberInvite, invite.id) is not None


def test_a_first_sign_in_from_the_list_stops_at_the_limit(closed, client, monkeypatch):
    app = closed
    org_id, _group_id = _full_group(app)
    with app.app_context():
        db.session.add(SignInOrcid(orcid=NEW, note="", org_id=org_id))
        db.session.commit()
    guest = app.test_client()
    _orcid(guest, monkeypatch, NEW)
    with app.app_context():
        assert db.session.scalar(db.select(User).filter_by(orcid=NEW, pending=False)) is None
