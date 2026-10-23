"""A site admin gives an account another home organisation on its page of the administration (app/orgs.py, move_home;
Ralf 2026-10-18): the former home stays a membership, a guest's row goes, a user type of the former organisation
becomes the one of the same name in the new one, refused while an organisation marked the account for deletion or the
new one has no accounts left."""
from app import account_types, orgs
from app.extensions import db
from app.models import Organisation, OrgDeletion, User, utcnow
from tests.accounts.test_organisations import OTHER, THIRD, _as, _org_with_admin
from tests.conftest import allow


def _third(app):
    allow(app, THIRD, keep=True)
    _as(app, THIRD, "Dritte")
    with app.app_context():
        return db.session.scalar(db.select(User.id).filter_by(orcid=THIRD))


def test_page_shows_the_home_only_with_several_organisations(app, client):
    uid = _third(app)
    assert 'id="heimat"' not in client.get(f"/admin/accounts/{uid}").data.decode()
    org_id, _ = _org_with_admin(app, client)
    page = client.get(f"/admin/accounts/{uid}").data.decode()
    assert 'id="heimat"' in page and "Heimat-Organisation" in page and "Lehrstuhl X" in page


def test_moved_home_keeps_the_former_one_and_maps_the_user_type(app, client):
    uid = _third(app)
    org_id, _ = _org_with_admin(app, client)
    with app.app_context():
        user = db.session.get(User, uid)
        account_types.assign(user, account_types.default_type(None), "")
        db.session.commit()
        main_type = account_types.of(user)
        assert main_type is not None and orgs.home_id(user) == orgs.default_id()
    r = client.post(f"/admin/accounts/{uid}", data={"action": "home", "org": org_id}, follow_redirects=True)
    assert "ist jetzt Lehrstuhl X" in r.data.decode()
    with app.app_context():
        orgs.forget()
        user = db.session.get(User, uid)
        assert orgs.home_id(user) == org_id
        assert orgs.default_id() in orgs.ids_of(user)                   # the former home stays a membership
        t = account_types.of(user)
        assert t.name_de == main_type.name_de and account_types.org_of(t) == org_id
        assert orgs.manages(db.session.scalar(db.select(User).filter_by(orcid=OTHER)), user,
                            db.session.get(Organisation, org_id))      # its admins manage the account now
    # and back: the organisation stays a membership
    with app.app_context():
        main_id = orgs.default_id()
    client.post(f"/admin/accounts/{uid}", data={"action": "home", "org": main_id})
    with app.app_context():
        orgs.forget()
        user = db.session.get(User, uid)
        assert orgs.home_id(user) == main_id and org_id in orgs.ids_of(user)
        assert orgs.row(org_id, THIRD).is_home is False


def test_a_guest_leaves_the_guests(app, client):
    uid = _third(app)
    org_id, _ = _org_with_admin(app, client)
    with app.app_context():
        orgs.add(db.session.get(Organisation, orgs.guest_id()), THIRD, home=True)
        db.session.commit()
    client.post(f"/admin/accounts/{uid}", data={"action": "home", "org": org_id})
    with app.app_context():
        orgs.forget()
        assert orgs.home_id(db.session.get(User, uid)) == org_id
        assert orgs.row(orgs.guest_id(), THIRD) is None


def test_refused_while_marked_for_deletion_or_without_accounts_left(app, client):
    uid = _third(app)
    org_id, _ = _org_with_admin(app, client)
    with app.app_context():
        db.session.get(Organisation, org_id).max_accounts = 1             # its admin is at home there already
        db.session.commit()
    r = client.post(f"/admin/accounts/{uid}", data={"action": "home", "org": org_id}, follow_redirects=True)
    assert "Höchstzahl an Konten" in r.data.decode()
    with app.app_context():
        db.session.get(Organisation, org_id).max_accounts = None
        db.session.add(OrgDeletion(user_id=uid, org_id=orgs.default_id(), due_at=utcnow()))
        db.session.commit()
    r = client.post(f"/admin/accounts/{uid}", data={"action": "home", "org": org_id}, follow_redirects=True)
    assert "zum Löschen vorgemerkt" in r.data.decode()
    with app.app_context():
        orgs.forget()
        assert orgs.home_id(db.session.get(User, uid)) == orgs.default_id()
