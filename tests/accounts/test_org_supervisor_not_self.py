"""An admin of an organisation cannot make themself the supervisor of an account (app/org_admin.py,
account_types.bringing_in): a supervisor becomes admin of every survey the person creates, and an admin of an
organisation sees no survey's content or answers. Neither on the account's card, nor by adding the iD or making the
account; a site admin still does, and supervisors set before stay (access audit 2026-11-11)."""
from app import account_types
from app.extensions import db
from app.models import AccountType, Survey, User
from tests.accounts.test_organisations import FIFTH, FOURTH, ME, OTHER, _as, _org_with_admin, _uid


def _world(app, client):
    org_id, _ = _org_with_admin(app, client)
    admin = _as(app, OTHER, "Kollegin")
    admin.post(f"/org/{org_id}/types", data={"action": "template", "template": "bachelor"})
    with app.app_context():
        tid = db.session.scalar(db.select(AccountType.id).filter_by(org_id=org_id, name_de="Bachelorarbeit"))
    admin.post(f"/org/{org_id}/people", data={"action": "add", "orcid": FOURTH, "name": "Vierte",
                                              "account_type": tid, "valid_mode": "type"})
    admin.post(f"/org/{org_id}/people", data={"action": "add", "orcid": FIFTH, "name": "Fünfte"})
    student = _as(app, FOURTH, "Vierte")
    _as(app, FIFTH, "Fünfte")
    return org_id, tid, admin, student


def _supervisors(app, uid):
    with app.app_context():
        return [u.id for u in account_types.supervisors_of(db.session.get(User, uid))]


def test_adding_the_id_does_not_make_the_admin_supervisor(app, client):
    org_id, tid, admin, student = _world(app, client)
    assert _supervisors(app, _uid(app, FOURTH)) == []


def test_the_card_offers_and_takes_not_the_admin_acting(app, client):
    org_id, tid, admin, student = _world(app, client)
    uid, me, fifth = _uid(app, FOURTH), _uid(app, OTHER), _uid(app, FIFTH)
    page = admin.get(f"/org/{org_id}/people/{uid}").data.decode()
    form = page[page.index('<select id="supervisor"'):]
    form = form[:form.index("</select>")]
    assert f'value="{fifth}"' in form and f'value="{me}"' not in form
    r = admin.post(f"/org/{org_id}/people/{uid}", data={"action": "supervisor", "supervisor": str(me)},
                   follow_redirects=True)
    assert "kann das Konto nicht betreuen" in r.data.decode()
    assert _supervisors(app, uid) == []
    # the person's next survey has no admin of the organisation in it
    student.post("/surveys/new", data={"title": "Abschlussarbeit", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Abschlussarbeit"))
        assert s is not None and {m.user_id for m in s.members} == {uid}


def test_making_the_account_does_not_make_the_admin_supervisor(app, client):
    org_id, tid, admin, student = _world(app, client)
    admin.post(f"/org/{org_id}/people/new", data={"name": "Dora Ohne", "email": "dora@example.org",
                                                  "account_type": tid, "valid_mode": "type"})
    with app.app_context():
        dora = db.session.scalar(db.select(User).filter_by(name="Dora Ohne"))
        assert dora is not None and account_types.of(dora) is not None and account_types.of(dora).id == tid
        assert account_types.supervisors_of(dora) == []


def test_a_supervisor_set_before_stays(app, client):
    org_id, tid, admin, student = _world(app, client)
    uid, me = _uid(app, FOURTH), _uid(app, OTHER)
    with app.app_context():
        account_types.set_supervisors(db.session.get(User, uid), [db.session.get(User, me)])
        db.session.commit()
    page = admin.get(f"/org/{org_id}/people/{uid}").data.decode()
    assert "Kollegin" in page[page.index('id="betreuung"'):]          # still listed, with its ×
    assert _supervisors(app, uid) == [me]


def test_a_site_admin_still_becomes_supervisor(app, client):
    org_id, tid, admin, student = _world(app, client)
    with app.app_context():
        site = db.session.get(User, _uid(app, ME))          # the site admin of tests/conftest.py
        assert account_types.bringing_in(site) is site
        assert account_types.bringing_in(db.session.get(User, _uid(app, OTHER))) is None
        assert account_types.bringing_in(None) is None
