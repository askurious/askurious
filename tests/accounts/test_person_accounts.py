"""Members and shares point to accounts, the ORCID iD is one way to sign in (konto-schluessel-plan.md, all as
recommended, Ralf 2026-10-18): an iD added before its first sign-in gets a pending account (B2) that no list counts
and the first sign-in makes whole; connecting such an iD to an account without ORCID takes the pending account over;
the iD can be disconnected again while e-mail and password stay (B5)."""
from app.extensions import db
from app.models import User, WorkGroup, WorkGroupMember
from tests.conftest import allow

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
PASSWORD = "A long unrelated sentence 7!"


def _group_with(client, orcid, name="Kollegin"):
    client.post("/groups/new", data={"name": "Lehrstuhl", "description": "Team"})
    with client.application.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id).order_by(WorkGroup.id.desc()))
    client.post(f"/groups/{gid}/members", data={"orcid": orcid, "name": name, "role": "member"})
    return gid


def _user(app, orcid):
    with app.app_context():
        return db.session.scalar(db.select(User).filter_by(orcid=orcid))


def test_an_id_added_before_its_first_sign_in_has_a_pending_account(app, client):
    gid = _group_with(client, OTHER)
    pending = _user(app, OTHER)
    assert pending.pending and pending.number is None and pending.name == "Kollegin"
    with app.app_context():
        member = db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=OTHER))
        assert member.user_id == pending.id and not member.signed_up
    assert "noch nicht angemeldet" in client.get(f"/groups/{gid}").data.decode()
    assert "Kollegin" not in client.get("/admin/people").data.decode()          # in no list of accounts
    allow(app, OTHER)
    guest = app.test_client()
    guest.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")                  # the first sign-in makes it whole
    whole = _user(app, OTHER)
    assert whole.id == pending.id and not whole.pending and whole.number and whole.setup_due
    assert "noch nicht angemeldet" not in client.get(f"/groups/{gid}").data.decode()


def test_connecting_an_id_takes_its_pending_account_over(app, client):
    from app import accounts, passwords

    gid = _group_with(client, OTHER)
    pending_id = _user(app, OTHER).id
    with app.app_context():
        local = accounts.create_local("Gast", "gast@example.org", "", False, None)
        passwords.set_password(local, PASSWORD)
        db.session.commit()
        accounts.link_orcid(db.session.get(User, local.id), OTHER, "Kollegin")
        db.session.commit()
        assert db.session.get(User, pending_id) is None
        member = db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=OTHER))
        assert member.user_id == local.id and db.session.get(User, local.id).orcid == OTHER


def test_the_orcid_id_can_be_disconnected_while_the_password_stays(app, client):
    from app import passwords

    allow(app, OTHER)
    guest = app.test_client()
    guest.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    guest.post("/terms/", data={"accept": "1"})
    gid = _group_with(client, OTHER)
    uid = _user(app, OTHER).id
    with app.app_context():
        user = db.session.get(User, uid)
        passwords.login_row(user, create=True).email = "kollegin@example.org"
        passwords.set_password(user, PASSWORD)
        db.session.commit()
    guest.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")                  # set_password ended the session
    assert "ORCID trennen" in guest.get("/account/").data.decode()
    guest.post("/account/orcid/remove", data={"current": "falsch"})
    assert _user(app, OTHER) is not None
    guest.post("/account/orcid/remove", data={"current": PASSWORD})
    with app.app_context():
        user = db.session.get(User, uid)
        assert not user.has_orcid and user.orcid is None
        member = db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, user_id=uid))
        assert member.orcid == user.orcid                                       # the key column followed
    assert guest.get(f"/groups/{gid}").status_code == 200                       # still in the group
    signed = app.test_client().post("/auth/sign-in", data={"email": "kollegin@example.org", "password": PASSWORD})
    assert signed.status_code == 302


def test_an_admin_of_the_configuration_keeps_the_id(app, client):
    from app import passwords

    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        passwords.login_row(me, create=True).email = "philipp@example.org"
        passwords.set_password(me, PASSWORD)
        db.session.commit()
    client.get(f"/auth/dev-login?orcid={ME}&name=Philipp")
    assert "ORCID trennen" not in client.get("/account/").data.decode()
    assert client.post("/account/orcid/remove", data={"current": PASSWORD}).status_code == 403


def test_a_pending_account_nobody_names_any_more_goes(app, client):
    from app import people_keys

    gid = _group_with(client, OTHER)
    with app.app_context():
        assert people_keys.sweep() == 0                                         # still in the group
        member = db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=OTHER))
        db.session.delete(member)
        db.session.commit()
        assert people_keys.sweep() == 0                                         # still in an address book
        from app.models import Contact

        db.session.execute(db.delete(Contact).where(Contact.user_id == _user(app, OTHER).id))
        db.session.commit()
        assert people_keys.sweep() == 1
    assert _user(app, OTHER) is None


def test_an_account_without_orcid_has_no_key_and_is_named_by_its_number(app, client):
    """konto-schluessel-2b-vorschlag.md (E3 A): an account without ORCID made from now on has no key; forms name it
    "u<id>", consents and AInstein entries by account, while what was written with keys before is still read."""
    from app import accounts, people_keys
    from app.items.consent import named, refs_of
    from app.models import SurveyMember

    client.post("/surveys/new", data={"title": "Studie", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        guest = accounts.create_local("Gast", "gast@example.org", "", False, None)
        db.session.commit()
        uid = guest.id
        assert guest.orcid is None and people_keys.key_for(guest) == f"u{uid}"
        assert people_keys.account(f"u{uid}") is guest and accounts.local_account(f"u{uid}") is guest
    client.post("/surveys/1/members", data={"orcid": f"u{uid}", "name": "Gast", "role": "editor"})
    with app.app_context():
        member = db.session.scalar(db.select(SurveyMember).filter_by(user_id=uid))
        assert member is not None and member.orcid is None and refs_of(member) == [f"u{uid}"]  # no iD, no key
        me = db.session.scalar(db.select(SurveyMember).filter_by(survey_id=1, orcid=ME))
        assert named(me, [f"u{me.user_id}"]) and not named(me, [ME]) and not named(me, [f"u{uid}"])  # by account only
        user = db.session.get(User, uid)
        assert people_keys.names({"user_id": uid, "orcid": None}, user)
        assert not people_keys.names({"user_id": uid + 1, "orcid": None}, user)
