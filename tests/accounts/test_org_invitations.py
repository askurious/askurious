"""Verwaltung > Personen, block "Offene Einladungen" (Ralf 2026-10-20, app/org_invites.py): every open invitation of
the organisation with kind, date, who invited and end, sorted and in pages, and a red symbol that withdraws it."""
from app.models import AuthToken, SignInOrcid, User, WorkGroupMember
from app.extensions import db
from tests.accounts.test_organisations import OTHER, _as, _org_with_admin
from tests.conftest import allow

CARL, BEA = "0000-0000-0000-0582", "0000-0000-0000-0353"


def _block(page):
    return page.split('id="einladungen"')[1].split("</section>")[0] if 'id="einladungen"' in page else ""


def _setup(app, client):
    org_id, gid = _org_with_admin(app, client)
    other = _as(app, OTHER, "Kollegin")
    allow(app, OTHER, keep=True)
    # an account without ORCID first: its empty iD once hid the iDs still waiting (NOT IN with NULL)
    other.post(f"/org/{org_id}/people/new", data={"name": "Dora Ohne", "email": "dora@example.org", "valid_mode": "type"})
    other.post(f"/org/{org_id}/people", data={"action": "add", "orcid": CARL, "note": "Carl Neu", "valid_mode": "type"})
    other.post(f"/groups/{gid}/members", data={"orcid": BEA, "name": "Bea Gruppe"})
    return org_id, gid, other


def test_open_invitations_are_listed_with_kind_date_and_inviter(app, client):
    org_id, _, other = _setup(app, client)
    block = _block(other.get(f"/org/{org_id}/people").data.decode())
    assert "Offene Einladungen" in block and ">3</span>" in block
    assert "Carl Neu" in block and "Anmeldung mit ORCID iD" in block
    assert "Bea Gruppe" in block and "Gruppe Lehrstuhl" in block
    assert "Dora Ohne" in block and "Einmal-Link ohne ORCID" in block
    assert block.count(">Kollegin<") == 3                                               # who invited
    assert "e_sort=name" in block and "e_sort=until" in block                           # heads that sort
    block = _block(other.get(f"/org/{org_id}/people?e_sort=name&e_dir=asc").data.decode())
    assert block.index("Bea Gruppe") < block.index("Carl Neu") < block.index("Dora Ohne")
    assert 'class="icon-btn admin-act danger"' in block and "data-confirm-danger" in block


def test_withdrawing_takes_back_each_kind(app, client):
    org_id, gid, other = _setup(app, client)
    for key in (CARL, None, "dora"):
        with app.app_context():
            if key == "dora":
                key = "l" + str(db.session.scalar(db.select(User.id).filter_by(name="Dora Ohne")))
            elif key is None:
                key = BEA
        r = other.post(f"/org/{org_id}/people/invitations", data={"key": key})
        assert r.status_code == 302 and "#einladungen" in r.headers["Location"]
    with app.app_context():
        assert db.session.get(SignInOrcid, CARL) is None                                 # can no longer sign in
        assert db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=BEA)) is None
        dora = db.session.scalar(db.select(User).filter_by(name="Dora Ohne"))
        assert dora is not None                                                          # the account stays,
        assert db.session.scalar(db.select(AuthToken).filter_by(user_id=dora.id, purpose="setup")) is None  # its link ends
    assert _block(other.get(f"/org/{org_id}/people").data.decode()) == ""                # nothing open: no block
    r = other.post(f"/org/{org_id}/people/invitations", data={"key": CARL}, follow_redirects=True)
    assert "nicht mehr offen" in r.data.decode()


def test_only_the_organisations_admins_withdraw(app, client):
    org_id, _, _ = _setup(app, client)
    stranger = _as(app, "0000-0000-0000-0809", "Fremd")
    assert stranger.post(f"/org/{org_id}/people/invitations", data={"key": CARL}).status_code == 404
    with app.app_context():
        assert db.session.get(SignInOrcid, CARL) is not None


def test_ids_added_in_the_administration_are_open_invitations_of_the_main_organisation(app, client):
    """Since the sign-in list is no longer shown on its own (Ralf 2026-10-29), an iD added with "Person hinzufügen" of
    the administration (no organisation of its own: the main one) is listed and withdrawn here."""
    from app import orgs

    allow(app)
    client.post("/admin/admit", data={"action": "add", "orcid": CARL, "note": "Martina"})
    with app.app_context():
        assert db.session.get(SignInOrcid, CARL).org_id is None
        main = orgs.default_id()
    block = _block(client.get(f"/org/{main}/people").data.decode())
    assert "Martina" in block and "Anmeldung mit ORCID iD" in block
    r = client.post(f"/org/{main}/people/invitations", data={"key": CARL})
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(SignInOrcid, CARL) is None
