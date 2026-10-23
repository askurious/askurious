"""Who brings people in from outside, per organisation (app/orgs.py SCOPES; concept
konzept-organisation-sichtbarkeit-einladungen-2026-10-23.md, E10-E14): "create" as before, "admins" only the
organisation's admins, "platform" only site admins. It holds for surveys, groups, join links and invitations by
e-mail; people already in the organisation are always added; the organisation's admins only make it stricter."""
from app import invites, joinlinks, orgs
from app.extensions import db
from app.models import Organisation, Survey, User, WorkGroup
from tests.conftest import allow

ME = "0000-0000-0000-001X"        # site admin (tests/conftest.py)
MEMBER = "0000-0002-1825-0097"    # creates surveys, member of "main"
ORG_ADMIN = "0000-0000-0000-0124"  # admin of "main"
COLLEAGUE = "0000-0000-0000-0239"  # signed in, at home in "main"
STRANGER = "0000-0000-0000-0458"   # never signed in


def _as(app, orcid, name):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    c.post("/terms/", data={"accept": "1"})
    return c


def _user(orcid):
    return db.session.scalar(db.select(User).filter_by(orcid=orcid))


def _main():
    return db.session.scalar(db.select(Organisation).filter_by(key=orgs.MAIN))


def _setup(app, scope):
    allow(app, MEMBER, ORG_ADMIN, COLLEAGUE, keep=True)
    clients = {o: _as(app, o, n) for o, n in ((MEMBER, "Mitglied"), (ORG_ADMIN, "Orgadmin"), (COLLEAGUE, "Kollegin"))}
    with app.app_context():
        main = _main()
        orgs.add(main, _user(ORG_ADMIN), role="admin")
        main.invite_scope = scope
        db.session.commit()
        orgs.forget()
    return clients


def _new_group(client, name):
    client.post("/groups/new", data={"name": name})
    with client.application.app_context():
        return db.session.scalar(db.select(WorkGroup.id).filter_by(name=name))


def _in_group(app, gid, orcid):
    with app.app_context():
        group = db.session.get(WorkGroup, gid)
        return any(m.user is not None and m.user.orcid == orcid for m in group.members)


def test_the_scope_is_the_stricter_of_platform_and_organisation(app, client):
    with app.app_context():
        main = _main()
        assert orgs.scope_of(main) == "create"
        main.invite_out = False                     # the former switch off counts as "admins"
        assert orgs.platform_scope(main) == "admins"
        main.invite_out, main.invite_scope, main.invite_scope_own = True, "admins", "create"
        assert orgs.scope_of(main) == "admins"      # the organisation cannot make it looser
        main.invite_scope_own = "platform"
        assert orgs.scope_of(main) == "platform"


def test_members_bring_in_nobody_from_outside_but_add_colleagues(app, client):
    clients = _setup(app, "admins")
    member = clients[MEMBER]
    gid = _new_group(member, "Kurs")
    member.post(f"/groups/{gid}/members", data={"orcid": STRANGER, "name": "Fremd"})
    assert not _in_group(app, gid, STRANGER)
    member.post(f"/groups/{gid}/members", data={"orcid": COLLEAGUE, "name": "Kollegin"})
    assert _in_group(app, gid, COLLEAGUE)
    page = member.get(f"/groups/{gid}").data.decode()
    assert "nur Admins der Organisation Personen von außen" in page   # the button stays, greyed with the reason
    with app.app_context():
        group = db.session.get(WorkGroup, gid)
        assert not joinlinks.may_make(_user(MEMBER), group)
        refused = invites.create("neu@example.org", "member", _user(MEMBER), group=group)
        assert refused and "Admins der Organisation" in refused
    # the survey of a member: the same
    member.post("/surveys/new", data={"title": "Eigene", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        sid = db.session.scalar(db.select(Survey.id).filter_by(title="Eigene"))
    member.post(f"/surveys/{sid}/members", data={"orcid": STRANGER, "name": "Fremd", "role": "viewer"})
    with app.app_context():
        assert all(m.user is None or m.user.orcid != STRANGER for m in db.session.get(Survey, sid).members)


def test_admins_of_the_organisation_and_of_the_platform_still_bring_people_in(app, client):
    clients = _setup(app, "admins")
    lead = clients[ORG_ADMIN]
    gid = _new_group(lead, "Projekt")
    lead.post(f"/groups/{gid}/members", data={"orcid": STRANGER, "name": "Fremd"})
    assert _in_group(app, gid, STRANGER)
    with app.app_context():
        main = _main()
        main.invite_scope = "platform"
        db.session.commit()
        assert not orgs.may_bring_in(_user(ORG_ADMIN), main.id, None)
        assert orgs.may_bring_in(_user(ME), main.id, None)
        assert orgs.may_bring_in(_user(ORG_ADMIN), main.id, _user(COLLEAGUE))   # inside: always


def test_the_organisation_admins_set_it_only_stricter_and_withdraw_resting_links(app, client):
    clients = _setup(app, "create")
    member = clients[MEMBER]
    gid = _new_group(member, "Kurs")
    member.post(f"/join/group/{gid}", data={"days": "14", "max_uses": "5"})
    with app.app_context():
        main_id = _main().id
        group = db.session.get(WorkGroup, gid)
        assert len(joinlinks.open_for(group)) == 1
    admin = clients[ORG_ADMIN]
    admin.post(f"/org/{main_id}/people/outside", data={"invite_scope": "admins"})
    with app.app_context():
        main = _main()
        assert main.invite_scope_own == "admins" and orgs.scope_of(main) == "admins"
        assert len(orgs.open_from_outside(main.id)) == 1      # the member's link rests now
        assert joinlinks.paused(joinlinks.open_for(db.session.get(WorkGroup, gid))[0])
    page = admin.get(f"/org/{main_id}/people").data.decode()
    assert "Personen von außen" in page and "ruhen" in page
    admin.post(f"/org/{main_id}/people/outside", data={"action": "withdraw"})
    with app.app_context():
        assert orgs.open_from_outside(main_id) == []
        main = _main()
        main.invite_scope = "platform"
        db.session.commit()
    admin.post(f"/org/{main_id}/people/outside", data={"invite_scope": "create"})   # looser than the platform: no
    with app.app_context():
        assert orgs.scope_of(_main()) == "platform"


def test_site_admins_choose_the_scope_on_the_organisation_page(app, client):
    with app.app_context():
        main_id = _main().id
    client.post(f"/admin/orgs/{main_id}", data={"action": "caps", "invite_scope": "admins", "invite_in": "1"})
    with app.app_context():
        main = _main()
        assert main.invite_scope == "admins" and not main.invite_out and main.invite_in
    page = client.get(f"/admin/orgs/{main_id}").data.decode()
    assert "Wer darf Personen von außen" in page
