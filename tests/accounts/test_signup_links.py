"""Registration links of the platform (app/signup_links.py; concept
konzept-organisation-sichtbarkeit-einladungen-2026-10-23.md, E1-E9): a site admin makes a link for a number of new
accounts with ORCID, with organisation, user type and a period from the registration on (also in days); it lets in
past the closed sign-up, not past the organisation's number of accounts, and changes no existing account."""
import re
from datetime import timedelta

from flask import session

from app import account_types, orgs, signup_links
from app.auth import _may_sign_in, _sign_in
from app.extensions import db
from app.models import Organisation, SignInOrcid, SignUpLink, User, UserLogin, utcnow
from tests.conftest import allow

NEW = "0000-0000-0000-0124"
SECOND = "0000-0000-0000-0239"
THIRD = "0000-0000-0000-0458"
EXISTING = "0000-0002-1825-0097"


def _org(app, **kw):
    with app.app_context():
        org = Organisation(name="Kurs X", **kw)
        db.session.add(org)
        db.session.commit()
        return org.id


def _make(client, org_id, **form):
    data = {"action": "create", "org": org_id, "account_type": "", "period": "t:30", "max_uses": "5", "days": "14",
            "note": "Seminar"}
    data.update(form)
    client.post("/admin/signup-links", data=data)
    page = client.get("/admin/people").data.decode()
    found = re.search(r"/signup/([A-Za-z0-9_-]{30,})", page)
    return found.group(1) if found else None


def _register(app, token, orcid, name):
    """The way through ORCID: the token waits in the session, the callback puts the iD on the list, then signs in."""
    stranger = app.test_client()
    app.config["ORCID_CLIENT_ID"] = "test"
    assert "/auth/login" in stranger.post(f"/signup/{token}").headers["Location"]
    with stranger.session_transaction() as s:
        held = s[signup_links.SESSION_KEY]
    assert token not in str(held)
    with app.test_request_context():
        session[signup_links.SESSION_KEY] = held
        took = signup_links.accept_after_orcid(orcid, name)
        if took and _may_sign_in(orcid):
            _sign_in(orcid, name)
        return took


def test_days_are_a_period():
    assert account_types.valid_spec("t:30") and account_types.parse("t:30") == ("t", 30)
    start = utcnow()
    end = account_types.end_for("t:30", start)
    assert timedelta(days=29) < end - start < timedelta(days=31, hours=2)


def test_a_link_makes_accounts_with_home_type_and_period(app, client):
    allow(app)  # open sign-up off: only the list
    org_id = _org(app)
    token = _make(client, org_id)
    assert token
    page = client.get("/admin/people").data.decode()
    assert "Einladungslink anlegen" in page and "0 von 5" in page and "30 Tage" in page
    assert "Der neue Link wird nur jetzt angezeigt" not in client.get("/admin/people").data.decode()  # once
    with app.app_context():
        assert db.session.scalar(db.select(SignUpLink.token_hash)) != token   # only the hash
        assert not _may_sign_in(NEW)
    app.config["ORCID_CLIENT_ID"] = "test"
    assert "Mit ORCID anmelden und Konto anlegen" in app.test_client().get(f"/signup/{token}").data.decode()
    assert _register(app, token, NEW, "Neu")
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=NEW))
        assert user is not None and orgs.home_id(user) == org_id
        row = db.session.get(UserLogin, user.id)
        assert row.valid_until is not None and timedelta(days=29) < row.valid_until - utcnow() < timedelta(days=31)
        listed = db.session.get(SignInOrcid, NEW)
        assert listed.link_id is not None and "Seminar" in listed.note
        link = db.session.scalar(db.select(SignUpLink))
        assert link.uses == 1 and [u.orcid for u in signup_links.came_by(link)] == [NEW]


def test_an_existing_account_stays_as_it_is(app, client):
    allow(app, EXISTING)
    app.test_client().get(f"/auth/dev-login?orcid={EXISTING}&name=Da")
    token = _make(client, _org(app))
    assert not _register(app, token, EXISTING, "Da")
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=EXISTING))
        assert orgs.home_id(user) == orgs.default_id()
        assert db.session.scalar(db.select(SignUpLink)).uses == 0


def test_used_up_full_or_revoked_links_let_nobody_in(app, client):
    allow(app)
    org_id = _org(app, max_accounts=1)
    token = _make(client, org_id, max_uses="5")
    assert _register(app, token, NEW, "Neu")
    assert not _register(app, token, SECOND, "Zweite")        # the organisation is full
    with app.app_context():
        assert db.session.get(SignInOrcid, SECOND) is None
        org = db.session.get(Organisation, org_id)
        org.max_accounts = None
        db.session.commit()
        link_id = db.session.scalar(db.select(SignUpLink.id))
    client.post("/admin/signup-links", data={"action": "revoke", "link_id": link_id})
    assert "gilt nicht mehr" in app.test_client().get(f"/signup/{token}", follow_redirects=True).data.decode()
    token = _make(client, org_id, max_uses="1")
    assert _register(app, token, SECOND, "Zweite")
    assert "gilt nicht mehr" in app.test_client().get(f"/signup/{token}", follow_redirects=True).data.decode()


def test_only_site_admins_make_links(app, client):
    allow(app, EXISTING)
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={EXISTING}&name=Da")
    other.post("/terms/", data={"accept": "1"})
    r = other.post("/admin/signup-links", data={"action": "create", "org": "", "period": "type", "max_uses": "5",
                                                "days": "14"})
    assert r.status_code in (403, 404)
    with app.app_context():
        assert db.session.scalar(db.select(SignUpLink.id)) is None


def test_the_dialog_offers_only_the_chosen_organisations_types(app, client):
    """"Einladungslink anlegen": each user type is tied to its organisation in the list (app.js shows only those of
    the chosen one), and the server refuses a type of another organisation instead of swapping it silently."""
    own, other = _org(app), _org(app)
    with app.app_context():
        account_types.ensure_per_org()
        mine = account_types.default_type(own).id
        foreign = account_types.default_type(other).id
    page = client.get("/admin/people").data.decode()
    dialog = page.split('id="einladungslink-anlegen"', 1)[1].split("</form>", 1)[0]
    assert "<optgroup" not in dialog
    assert re.search(rf'<option value="{mine}" data-org="{own}"', dialog)
    assert re.search(rf'<option value="{foreign}" data-org="{other}"', dialog)

    assert _make(client, own, account_type=str(foreign)) is None
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(SignUpLink.id))) == 0
    assert _make(client, own, account_type=str(mine))
    with app.app_context():
        assert db.session.scalar(db.select(SignUpLink.account_type_id)) == mine
