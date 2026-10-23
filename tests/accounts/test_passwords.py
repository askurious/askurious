"""Sign-in with e-mail address and password (app/passwords.py, app/auth.py): accounts without ORCID made by a
site admin, the one-time link, the password rules, the pause after wrong passwords, ending sessions, and such
accounts as co-owners, group members and authors."""
from tests.accounts.password_setup import confirmed_password
import re
from datetime import timedelta

import pytest

from app import limits, passwords, theme, zenodo
from app.extensions import db
from app.models import (AuthToken, Contact, Design, DesignShare, SignInGrant, Survey, SurveyMember, User, UserLogin,
                        WorkGroup, WorkGroupMember, utcnow)
from tests.conftest import allow

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
MAIL = "gast@example.org"
GOOD = "ein langer Satz als Passwort"


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _create(client, name="Gast", email=MAIL, **extra):
    """The admin creates an account; returns the path of the one-time link the page shows."""
    page = client.post("/admin/accounts/new", data={"name": name, "email": email, **extra}, follow_redirects=True).data.decode()
    m = re.search(r'value="https?://[^/"]+(/auth/password/[^"]+)"', page)
    return m.group(1) if m else page


def _login(app):
    with app.app_context():
        return db.session.scalar(db.select(UserLogin).filter_by(email=MAIL))


def _local(app):
    with app.app_context():
        return db.session.scalar(db.select(User).join(UserLogin, UserLogin.user_id == User.id).filter(UserLogin.email == MAIL))


@pytest.fixture
def guest(app, client):
    """An account without ORCID, password set over the one-time link, terms accepted; on a client of its own."""
    link = _create(client)
    c = app.test_client()
    c.post(link, data={"password": GOOD, "repeat": GOOD})
    c.post("/terms/", data={"accept": "1"})
    return c


def _sign_in(c, email=MAIL, password=GOOD, **extra):
    return c.post("/auth/sign-in", data={"email": email, "password": password, **extra})


def test_password_rules():
    assert "mindestens 12 Zeichen" in passwords.problem("kurz")
    assert "zu leicht" in passwords.problem("password1234") and "zu leicht" in passwords.problem("aaaaaaaaaaaaaa")
    assert "zu leicht" in passwords.problem("123456789012345")
    assert "eigene Name" in passwords.problem("Maximiliane Muster", name="Maximiliane Muster")
    assert "eigene Name" in passwords.problem("gast.forschung@example.org", email="gast.forschung@example.org")
    assert passwords.problem(GOOD, "Gast", MAIL) is None                   # no character classes asked for
    assert passwords.normal_email("  Gast@Example.ORG ") == MAIL and passwords.normal_email("kein at") is None


def test_admin_creates_an_account_and_the_person_sets_the_password(app, client):
    assert "Konto anlegen" in client.get("/admin/people").data.decode()
    assert "gültige E-Mail-Adresse" in _create(client, email="keine adresse")
    link = _create(client, affiliation="Uni Musterstadt")
    assert link.startswith("/auth/password/")
    assert "gibt es schon ein Konto" in _create(client, name="Zweite", email=MAIL.upper())
    user, row = _local(app), _login(app)
    assert user.orcid is None and not user.has_orcid and not user.is_admin  # no key since 2b (E3 A)
    assert user.affiliation == "" and user.number == 2  # no institution any more (organisations, Ralf 2026-10-16)
    assert row.password_hash is None and row.may_create is None and row.email_verified_at is None
    page = client.get("/admin/people").data.decode()
    assert MAIL in page and "Passwort noch nicht gesetzt" in page
    with app.app_context():  # only the hash of the token is stored
        token = db.session.scalar(db.select(AuthToken))
        assert token.token_hash not in link and link.rsplit("/", 1)[1] not in token.token_hash

    c = app.test_client()
    assert _sign_in(c).status_code == 401                                   # no password yet
    page = c.get(link).data.decode()
    assert "Passwort setzen" in page and "Gast" in page and MAIL in page
    assert "mindestens 12 Zeichen" in c.post(link, data={"password": "kurz", "repeat": "kurz"}).data.decode()
    assert "stimmen nicht überein" in c.post(link, data={"password": GOOD, "repeat": GOOD + "x"}).data.decode()
    r = c.post(link, data={"password": GOOD, "repeat": GOOD}, follow_redirects=True).data.decode()
    assert "Nutzungsbedingungen" in r                                         # signed in; the terms come first
    c.post("/terms/", data={"accept": "1"})
    assert "Gast" in c.get("/account/").data.decode()
    assert _login(app).password_hash.startswith("scrypt:")
    # the link worked once
    assert "nicht mehr gültig" in app.test_client().get(link, follow_redirects=True).data.decode()


def test_sign_in_and_out(app, client, guest):
    guest.post("/auth/logout")
    assert guest.get("/account/").status_code == 302
    assert "/auth/sign-in" in guest.get("/account/").headers["Location"]      # the page with both ways in
    app.config["ORCID_CLIENT_ID"] = "configured-client"  # the ORCID button only with a client (#1190)
    page = guest.get("/auth/sign-in").data.decode()
    assert "Mit ORCID anmelden" in page and 'name="password"' in page
    # the same answer for a wrong password and an unknown address
    wrong, unknown = _sign_in(guest, password="falsches Passwort"), _sign_in(guest, email="niemand@example.org")
    assert wrong.status_code == unknown.status_code == 401
    text = "E-Mail-Adresse oder Passwort stimmen nicht"
    assert text in wrong.data.decode() and text in unknown.data.decode()
    r = _sign_in(guest, email="  GAST@example.org ", next="/account/")
    assert r.status_code == 302 and r.headers["Location"] == "/account/"
    assert _sign_in(app.test_client(), next="https://evil.example/").headers["Location"] == "/dashboard/"
    assert guest.get("/admin/people").status_code == 403                         # never a site admin


def test_ten_wrong_passwords_pause_the_account(app, client, guest):
    c = app.test_client()
    for _ in range(passwords.MAX_FAILURES):
        assert _sign_in(c, password="falsches Passwort").status_code == 401
    assert _login(app).locked_until is not None
    assert _sign_in(c).status_code == 401                                   # also the right one, for 15 minutes
    with app.app_context():
        row = db.session.scalar(db.select(UserLogin).filter_by(email=MAIL))
        row.locked_until = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert _sign_in(c).status_code == 302
    assert _login(app).failed_count == 0


def test_sign_in_attempts_per_sender_are_limited(app, client, guest, monkeypatch):
    monkeypatch.setattr(limits, "_network", lambda: "198.51.100.1")  # one sender; counted in the database
    app.config["LOGIN_LIMIT"] = 3
    c = app.test_client()
    for _ in range(3):
        _sign_in(c, email="niemand@example.org")
    r = _sign_in(c)
    assert r.status_code == 401                                             # the right password, but too many attempts
    page = r.data.decode()
    assert "zu viele Anmeldeversuche" in page and "Passwort stimmen nicht" not in page   # its own message (H5)
    app.config["LOGIN_LIMIT"] = 0
    assert _sign_in(c).status_code == 302


def test_only_failed_sign_ins_count_toward_the_limit_per_sender(app, client, guest, monkeypatch):
    """Code review 2026-10-15, H5: a seminar behind one address (NAT) was kept out by its own right sign-ins."""
    monkeypatch.setattr(limits, "_network", lambda: "198.51.100.2")
    app.config["LOGIN_LIMIT"] = 3
    for _ in range(5):
        assert _sign_in(app.test_client()).status_code == 302
    for _ in range(3):
        _sign_in(app.test_client(), email="niemand@example.org")
    assert _sign_in(app.test_client()).status_code == 401


def test_change_password_ends_other_sessions(app, client, guest):
    second = app.test_client()
    assert _sign_in(second).status_code == 302
    assert second.get("/account/").status_code == 200
    data = {"current": GOOD, "password": "noch ein langer neuer Satz", "repeat": "noch ein langer neuer Satz"}
    assert "aktuelle Passwort stimmt nicht" in guest.post("/account/password", data={**data, "current": "falsch"},
                                                         follow_redirects=True).data.decode()
    assert "mindestens 12 Zeichen" in guest.post("/account/password", data={**data, "password": "kurz", "repeat": "kurz"},
                                                 follow_redirects=True).data.decode()
    assert "Passwort gespeichert" in guest.post("/account/password", data=data, follow_redirects=True).data.decode()
    assert guest.get("/account/").status_code == 200                          # this browser stays signed in
    assert second.get("/account/").status_code == 302                         # the other one is out
    assert _sign_in(second).status_code == 401
    assert _sign_in(second, password="noch ein langer neuer Satz").status_code == 302


def test_sign_out_everywhere(app, client, guest):
    second = app.test_client()
    _sign_in(second)
    r = second.post("/account/sign-out-everywhere", follow_redirects=True).data.decode()
    assert "Alle anderen Sitzungen" in r
    assert second.get("/account/").status_code == 200 and guest.get("/account/").status_code == 302
    # also for an ORCID account, which has no row of its own until then
    other, twin = app.test_client(), app.test_client()
    _as(other, OTHER, "Kollegin")
    _as(twin, OTHER, "Kollegin")
    other.post("/account/sign-out-everywhere")
    assert other.get("/account/").status_code == 200 and twin.get("/account/").status_code == 302


def test_a_new_link_replaces_the_password_and_the_old_link(app, client, guest):
    uid = _local(app).id
    page = client.get(f"/admin/accounts/{uid}").data.decode()
    assert "Das Passwort ist gesetzt" in page and MAIL in page
    first = re.search(r'(/auth/password/[^"]+)"', client.post(f"/admin/accounts/{uid}", data={"action": "link"}, follow_redirects=True).data.decode()).group(1)
    newer = re.search(r'(/auth/password/[^"]+)"', client.post(f"/admin/accounts/{uid}", data={"action": "link"}, follow_redirects=True).data.decode()).group(1)
    assert guest.get("/account/").status_code == 200                          # the old password holds until a new one is set
    c = app.test_client()
    assert "nicht mehr gültig" in c.get(first, follow_redirects=True).data.decode()
    c.post(newer, data={"password": "vergessen und neu gesetzt", "repeat": "vergessen und neu gesetzt"})
    assert c.get("/account/").status_code == 200 and guest.get("/account/").status_code == 302
    assert _sign_in(app.test_client()).status_code == 401
    with app.app_context():  # an expired link is worth nothing
        token = passwords.new_link_token(db.session.get(User, uid), db.session.get(User, uid))
        db.session.scalar(db.select(AuthToken)).expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert "nicht mehr gültig" in c.get(f"/auth/password/{token}", follow_redirects=True).data.decode()


def test_admin_changes_the_address_and_the_right_to_create(app, client, guest):
    uid = _local(app).id
    assert guest.get(f"/admin/accounts/{uid}").status_code == 403
    assert "Neue Befragung" not in guest.get("/dashboard/").data.decode()
    assert guest.post("/groups/new", data={"name": "Team"}).status_code == 403
    r = client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "1"}, follow_redirects=True).data.decode()
    assert "legt jetzt Befragungen jeder Art an" in r
    assert "</svg> Fragebogen</a>" in guest.get("/dashboard/").data.decode()
    guest.post("/surveys/new", data={"title": "Vom Gast", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(title="Vom Gast"))
        assert survey.members[0].orcid == _local(app).orcid and survey.consent_item.config["researchers"] == [f"u{_local(app).id}"]
    # "*" in the whitelist is about ORCID iDs
    client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "0"})
    allow(app, "*")
    assert "Neue Befragung" not in guest.get("/dashboard/").data.decode()
    allow(app)

    _create(client, name="Andere", email="andere@example.org")
    r = client.post(f"/admin/accounts/{uid}", data={"action": "email", "email": "andere@example.org"}, follow_redirects=True).data.decode()
    assert "gibt es schon ein Konto" in r
    client.post(f"/admin/accounts/{uid}", data={"action": "email", "email": "Neu@example.org"})
    guest.post("/auth/logout")
    assert _sign_in(guest).status_code == 401 and _sign_in(guest, email="neu@example.org").status_code == 302
    # the tick also for an ORCID account, instead of an entry in the server's configuration
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    with app.app_context():
        oid = db.session.scalar(db.select(User.id).filter_by(orcid=OTHER))
    assert "Neue Befragung" not in other.get("/dashboard/").data.decode()
    client.post(f"/admin/accounts/{oid}", data={"action": "may_create", "value": "1"})
    assert "</svg> Fragebogen</a>" in other.get("/dashboard/").data.decode()
    assert client.post(f"/admin/accounts/{oid}", data={"action": "link"}).status_code == 400  # no address, no link


def test_own_details(app, client, guest):
    page = guest.get("/account/").data.decode()
    assert 'name="affiliation"' not in page and MAIL in page and "Passwort ändern" in page and "orcid.org" not in page
    guest.post("/account/details", data={"name": "  Dr.  Gast ", "affiliation": "RWTH Aachen University"})
    user = _local(app)
    assert user.name == "Dr. Gast" and user.affiliation == ""  # institutions are no longer recorded (2026-10-16)
    # no theme of an institution any more (Ralf 2026-10-19: organisation designs)
    assert "rwth" not in theme.BUILTIN
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    assert other.post("/account/details", data={"name": "Anders"}).status_code == 403  # ORCID accounts: from the record


def test_orcid_account_adds_a_password(app, client, guest):
    from tests.conftest import mail_on

    sent = mail_on(app)
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    page = other.get("/account/").data.decode()
    assert "zusätzlich zu ORCID" in page and 'name="email"' in page
    data = {"email": "kollegin@example.org", "password": GOOD, "repeat": GOOD}
    # the address of another account: the same answer as for a free one, its owner hears of it (audit 2026-10-18, AUTH-5)
    answer = other.post("/account/password", data={**data, "email": MAIL}, follow_redirects=True).data.decode()
    assert "schon ein anderes Konto" not in answer and "Bestätigen Sie die Anmeldeadresse" in answer
    assert sent[-1]["To"] == MAIL and "schon vergeben" in str(sent[-1]["Subject"]) and "confirm-email" not in sent[-1].get_payload()
    with other.session_transaction() as s:
        fresh = s["signed_in_at"]
        s["signed_in_at"] = fresh - 3600
    assert "liegt zu lange zurück" in other.post("/account/password", data=data, follow_redirects=True).data.decode()
    assert "Neu mit ORCID anmelden" in other.get("/account/").data.decode()
    with other.session_transaction() as s:
        s["signed_in_at"] = fresh
    assert "Passwort aktiviert" in confirmed_password(app, other, data).data.decode()
    c = app.test_client()
    app.config["DEV_LOGIN"] = False
    # the password is a second door to the same account; who may sign in is still decided by the ORCID rules
    assert "nicht freigeschaltet" in _sign_in(c, email="kollegin@example.org").data.decode()
    allow(app, OTHER)
    assert _sign_in(c, email="kollegin@example.org").status_code == 302
    page = c.get("/account/", follow_redirects=True).data.decode()
    assert "Kollegin" in page and f"orcid.org/{OTHER}" in page
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(User.id)).filter_by(orcid=OTHER)) == 1
    assert guest.get("/account/").status_code == 200                          # accounts without ORCID need no whitelist


def test_account_without_orcid_as_co_owner_group_member_and_author(app, client, guest):
    key = f"u{_local(app).id}"  # how forms name an account without ORCID (people_keys.key_for)
    client.post("/surveys/new", data={"title": "Studie", "languages": ["de"], "default_language": "de"})
    # found by the complete address only, never by a part of it
    page = client.get("/surveys/1?q=gast@example.org").data.decode()
    assert "Gast" in page and f'value="{key}"' in page and MAIL not in page.replace('value="gast@example.org"', "")
    assert f'value="{key}"' not in client.get("/surveys/1?q=gast@example").data.decode()
    assert client.post("/surveys/1/members", data={"orcid": "q-" + "a" * 16, "name": "Niemand"}).status_code == 400
    r = client.post("/surveys/1/members", data={"orcid": key, "name": "falscher Name", "role": "editor"}, follow_redirects=True).data.decode()
    assert "Gast ist jetzt beteiligt" in r
    page = client.get("/surveys/1").data.decode()
    assert "noch nicht angemeldet" not in page and f"orcid.org/{key}" not in page
    assert guest.get("/surveys/1").status_code == 200
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert [(f"u{m.user_id}", m.role, m.display_name) for m in survey.members][1] == (key, "editor", "Gast")
        creators = zenodo.metadata(survey)["creators"]
        assert creators[0]["orcid"] == ME and "orcid" not in creators[1] and creators[1]["name"] == "Gast"
    client.post("/groups/new", data={"name": "Team"})
    assert "Gast ist jetzt in der Gruppe" in client.post("/groups/1/members", data={"orcid": key}, follow_redirects=True).data.decode()
    assert "Team" in guest.get("/groups/").data.decode()
    with app.app_context():
        member = db.session.scalar(db.select(WorkGroupMember).filter_by(user_id=_local(app).id))
        assert member.name == "Gast"


def test_deleting_and_blocking_an_account_with_password(app, client, guest):
    uid = _local(app).id
    page = guest.get("/account/delete").data.decode()
    assert "Zur Bestätigung Ihr Passwort" in page and "E-Mail-Adresse und Passwort" in page
    assert "Das Passwort stimmt nicht" in guest.post("/account/delete", data={"password": "falsch"}, follow_redirects=True).data.decode()
    client.post(f"/admin/accounts/{uid}/lock", data={"action": "block"})
    assert guest.get("/account/").status_code == 302
    assert "gesperrt" in _sign_in(guest).data.decode()
    link = re.search(r'(/auth/password/[^"]+)"', client.post(f"/admin/accounts/{uid}", data={"action": "link"}, follow_redirects=True).data.decode()).group(1)
    assert "nicht mehr gültig" in guest.get(link, follow_redirects=True).data.decode()  # no way in around the block
    client.post(f"/admin/accounts/{uid}/lock", data={"action": "unblock"})
    _sign_in(guest)
    assert "Ihr Konto wurde gelöscht" in guest.post("/account/delete", data={"password": GOOD}, follow_redirects=True).data.decode()
    with app.app_context():
        assert db.session.get(User, uid) is None
        assert db.session.scalar(db.select(db.func.count(UserLogin.user_id))) == 0
        assert db.session.scalar(db.select(db.func.count(AuthToken.id))) == 0
    assert _sign_in(guest).status_code == 401
    assert _create(client).startswith("/auth/password/")                    # the address is free again


# --- connecting an ORCID iD to an account without one ---------------------------------------------------

NEW = "0000-0000-0000-0124"


def _connect(app, c, monkeypatch, orcid=NEW, name="Dr. Gast (ORCID)"):
    """Go through "Mit ORCID verbinden" with ORCID answering for this iD."""
    from app import auth, orcid_api

    class Answer:
        status_code = 200

        @staticmethod
        def json():
            return {"orcid": orcid, "name": name}

    app.config["ORCID_CLIENT_ID"] = "client"
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: Answer)
    r = c.post("/auth/login", data={"link": "1", "fresh": "1", "current": GOOD})
    assert r.status_code == 302 and "prompt=login" in r.headers["Location"]
    with c.session_transaction() as s:
        state = s["oauth_state"]
    return c.get(f"/auth/orcid/callback?state={state}&code=x", follow_redirects=True).data.decode()


def test_connect_orcid(app, client, guest, monkeypatch):
    key, uid = _local(app).orcid, _local(app).id
    assert "Mit ORCID verbinden" in guest.get("/account/").data.decode()
    client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "1"})
    guest.post("/surveys/new", data={"title": "Vom Gast", "languages": ["de"], "default_language": "de"})
    client.post("/surveys/new", data={"title": "Vom Admin", "languages": ["de"], "default_language": "de"})
    client.post("/groups/new", data={"name": "Team"})
    with app.app_context():
        own, shared = (db.session.scalar(db.select(Survey).filter_by(title=t)) for t in ("Vom Gast", "Vom Admin"))
        group = db.session.scalar(db.select(WorkGroup))
        # in the admin's survey the guest is an editor by key, and the iD was invited as admin before it had an account
        shared.members.append(SurveyMember(orcid=key, user_id=uid, name="Gast", role="editor", position=1))
        shared.members.append(SurveyMember(orcid=NEW, name="Eingeladen", role="admin", position=2))
        group.members.append(WorkGroupMember(orcid=key, user_id=uid, name="Gast"))
        admin = db.session.scalar(db.select(User).filter_by(orcid=ME))
        design = Design(owner_id=admin.id, name="Hausfarben", data=theme.standard())
        design.shares.append(DesignShare(user_id=uid))  # rows name the account (people_keys)
        db.session.add_all([design, Contact(owner_id=admin.id, user_id=uid, name="Gast"),
                            Contact(owner_id=admin.id, orcid=NEW, name="Eingeladen")])
        db.session.commit()
        own_id, shared_id = own.id, shared.id

    assert "jetzt mit Ihrer ORCID iD verbunden" in _connect(app, guest, monkeypatch)
    page = guest.get("/account/").data.decode()
    assert f"orcid.org/{NEW}" in page and "Mit ORCID verbinden" not in page and "Passwort ändern" in page
    with app.app_context():
        user = db.session.get(User, uid)
        assert (user.orcid, user.name) == (NEW, "Dr. Gast (ORCID)")
        assert db.session.scalar(db.select(db.func.count(User.id))) == 2
        own, shared = db.session.get(Survey, own_id), db.session.get(Survey, shared_id)
        assert [(m.orcid, m.role) for m in own.members] == [(NEW, "admin")]
        assert own.consent_item.config["researchers"] == [f"u{uid}"]  # by account, the iD does not change it
        # one entry instead of two, with the higher role and in the author's old place
        assert [(m.orcid, m.role, m.position, m.user_id) for m in shared.members] == [(ME, "admin", 0, admin_id(app)),
                                                                                      (NEW, "admin", 1, uid)]
        assert shared.members[1].display_name == "Dr. Gast (ORCID)"
        assert [m.orcid for m in db.session.scalar(db.select(WorkGroup)).members] == [ME, NEW]
        assert [s.orcid for s in db.session.scalars(db.select(DesignShare))] == [NEW]
        assert [(c.orcid, c.name) for c in db.session.scalars(db.select(Contact).filter_by(owner_id=admin_id(app)))] \
            == [(NEW, "Eingeladen")]
        for model in (SurveyMember, WorkGroupMember, DesignShare, Contact):
            assert db.session.scalar(db.select(db.func.count()).select_from(model).filter_by(orcid=key)) == 0, model
        assert zenodo.metadata(own)["creators"][0]["orcid"] == NEW
        assert db.session.get(SignInGrant, uid) is not None
    # both ways in lead to the same account, also where the iD is on no whitelist
    app.config["DEV_LOGIN"] = False
    assert guest.get("/account/").status_code == 200
    c = app.test_client()
    assert _sign_in(c).status_code == 302 and c.get(f"/surveys/{own_id}").status_code == 200
    app.config["DEV_LOGIN"] = True
    twin = app.test_client()
    twin.get(f"/auth/dev-login?orcid={NEW}&name=Dr. Gast")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(User.id))) == 2
    assert twin.get(f"/surveys/{own_id}").status_code == 200
    # deleting the account takes the admin's yes along
    guest.post("/account/delete", data={"password": GOOD})
    with app.app_context():
        assert db.session.get(User, uid) is None and db.session.scalar(db.select(db.func.count(SignInGrant.user_id))) == 0


def admin_id(app):
    return db.session.scalar(db.select(User.id).filter_by(orcid=ME))


def test_an_orcid_with_an_account_of_its_own_is_not_connected(app, client, guest, monkeypatch):
    key = _local(app).orcid
    assert "schon ein eigenes Konto" in _connect(app, guest, monkeypatch, orcid=ME)
    assert _local(app).orcid == key and _local(app).name == "Gast"
    assert "Mit ORCID verbinden" in guest.get("/account/").data.decode()


def test_orcid_sign_in_is_not_a_connection(app, client, guest, monkeypatch):
    """Without the word from the page "Konto", the callback signs in as before; an ORCID account cannot ask for
    a connection at all."""
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    page = _connect(app, other, monkeypatch, orcid=OTHER, name="Kollegin")
    assert "verbunden" not in page
    with other.session_transaction() as s:
        assert "orcid_link" not in s
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(User.id))) == 3 and _local(app).orcid is None
    # the word is taken back when the next sign-in starts without it
    guest.post("/auth/login", data={"link": "1", "current": GOOD})
    with guest.session_transaction() as s:
        assert "orcid_link" in s
    guest.get("/auth/login")
    with guest.session_transaction() as s:
        assert "orcid_link" not in s


def _forgot(c, email=MAIL):
    return c.post("/auth/forgot-password", data={"email": email}, follow_redirects=True)


def test_forgot_password_request_reaches_the_admins(app, client, guest, monkeypatch):
    sent = []
    monkeypatch.setattr("app.notify.slack", lambda text, blocks=None: sent.append(text))
    c = app.test_client()
    page = c.get("/auth/sign-in").data.decode()
    assert "/auth/forgot-password" in page and "Passwort vergessen?" in page
    answer = _forgot(c, "  " + MAIL.upper()).data.decode()
    assert "Anfrage gesendet" in answer
    assert _login(app).reset_requested_at is not None and len(sent) == 1 and MAIL not in sent[0]
    _forgot(c)                                                              # asked again: no second note
    assert len(sent) == 1
    # the same answer for an address without an account, and nothing stored
    assert "Anfrage gesendet" in _forgot(c, "niemand@example.org").data.decode()
    assert c.post("/auth/forgot-password", data={"email": "kein at"}).status_code == 422
    listing = client.get("/admin/people").data.decode()
    assert "passwort-anfragen" in listing and "Einmal-Link erzeugen" in listing
    user = _local(app)
    assert "Passwort vergessen?“ angefragt" in client.get(f"/admin/accounts/{user.id}").data.decode()
    # the one-time link answers the request; the old password stays valid until a new one is set
    page = client.post(f"/admin/accounts/{user.id}", data={"action": "link"}, follow_redirects=True).data.decode()
    assert "/auth/password/" in page and _login(app).reset_requested_at is None
    assert "passwort-anfragen" not in client.get("/admin/people").data.decode()
    assert _sign_in(app.test_client()).status_code == 302


def test_forgot_password_request_done_without_link_and_cleared_by_new_password(app, client, guest):
    c = app.test_client()
    _forgot(c)
    user = _local(app)
    client.post(f"/admin/accounts/{user.id}", data={"action": "request_done", "back": "list"})
    assert _login(app).reset_requested_at is None
    _forgot(c)
    assert _login(app).reset_requested_at is not None
    with app.app_context():
        passwords.set_password(db.session.get(User, user.id), "ein anderer langer Satz")
        db.session.commit()
    assert _login(app).reset_requested_at is None


def test_forgot_password_requests_per_sender_are_limited(app, client, guest, monkeypatch):
    monkeypatch.setattr(limits, "_network", lambda: "198.51.100.1")  # one sender; counted in the database
    app.config["RESET_LIMIT"] = 2
    c = app.test_client()
    _forgot(c, "a@example.org")
    _forgot(c, "b@example.org")
    assert "Anfrage gesendet" in _forgot(c).data.decode()                  # the same answer ...
    assert _login(app).reset_requested_at is None                           # ... but nothing noted
    app.config["RESET_LIMIT"] = 0
    _forgot(c)
    assert _login(app).reset_requested_at is not None


def test_forgot_password_ignores_blocked_accounts(app, client, guest):
    user = _local(app)
    client.post(f"/admin/accounts/{user.id}/lock", data={"action": "block"})
    _forgot(app.test_client())
    assert _login(app).reset_requested_at is None


def test_a_pause_caused_by_others_does_not_sign_the_owner_out(app, client, guest):
    """Code review 2026-10-15, H3: whoever only knew the address could pause the account from elsewhere, and then
    the owner's own session ended at changing the password. Now only the guessing sender's session ends; a browser
    known from an earlier sign-in is not held by the lock after HARD_LIMIT, as at the sign-in."""
    guest.environ_base["REMOTE_ADDR"] = "203.0.113.5"
    assert _sign_in(guest).status_code == 302                       # this browser is known from now on
    with app.app_context():
        row = db.session.scalar(db.select(UserLogin).filter_by(email=MAIL))
        row.locked_until = utcnow() + passwords.PAUSE                # paused by wrong passwords from elsewhere
        db.session.commit()
    new = "ein ganz neuer langer Satz"
    page = guest.post("/account/password", data={"current": GOOD, "password": new, "repeat": new},
                      follow_redirects=True).data.decode()
    assert "von anderswo" in page and "Sie bleiben angemeldet" in page
    assert guest.get("/account/").status_code == 200                          # still signed in
    with app.app_context():
        row = db.session.scalar(db.select(UserLogin).filter_by(email=MAIL))
        row.locked_until, row.hard_locked_at = None, utcnow()       # the lock after HARD_LIMIT from elsewhere
        db.session.commit()
    page = guest.post("/account/password", data={"current": GOOD, "password": new, "repeat": new},
                      follow_redirects=True).data.decode()
    assert "Passwort gespeichert" in page and guest.get("/account/").status_code == 200


def test_a_fresh_orcid_sign_in_sets_a_forgotten_password(app, client):
    """Ralf 2026-10-18 forgot his password: an account with ORCID sets a new one after a fresh sign-in with ORCID,
    without the old one; a sign-in that is not fresh or not with ORCID does not do."""
    from app.models import User

    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        passwords.login_row(me, create=True).email = "philipp@example.org"
        passwords.set_password(me, "A long unrelated sentence 7!")
        db.session.commit()
    new = {"forgot": "1", "password": "Another quite long sentence 9?", "repeat": "Another quite long sentence 9?"}
    client.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")       # fresh, with ORCID
    with client.session_transaction() as s:
        s["signed_in_at"] = 0                                                   # too long ago
    assert "Bitte erst neu mit ORCID anmelden" in client.post("/account/password", data=new, follow_redirects=True).data.decode()
    client.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    assert "Passwort vergessen?" in client.get("/account/").data.decode()
    assert "Passwort gespeichert" in client.post("/account/password", data=new, follow_redirects=True).data.decode()
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        assert passwords.matches(me, new["password"]) and not passwords.matches(me, "A long unrelated sentence 7!")
