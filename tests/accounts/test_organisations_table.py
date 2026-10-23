"""Administration > Organisationen as a table with a page per organisation (variant A of the concept of 2026-10-18,
Ralf): search and sort in the database, accounts and invited iDs apart, admins also by ORCID iD without an account,
and the admin role taken away from an iD without an account."""
from app import orgs
from app.extensions import db
from app.models import Organisation, User

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
NOBODY = "0000-0000-0000-0124"   # an iD without an account


def _org(app, name, admin=None):
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        org = orgs.make(name, me)
        db.session.flush()
        if admin:
            orgs.add(org, admin, role="admin", by=me)
        db.session.commit()
        return org.id


def test_the_table_searches_sorts_and_counts_invited_apart(app, client):
    a = _org(app, "Zentrum Alpha", admin=NOBODY)
    _org(app, "Institut Beta")
    page = client.get("/admin/orgs").data.decode()
    assert 'class="list-table is-num admin-orgs"' in page and page.index("Institut Beta") < page.index("Zentrum Alpha")
    assert "1 eingeladen" in page and f'href="/admin/orgs/{a}"' in page
    with app.app_context():
        guest = orgs.guest_id()
    assert "Gäste (ohne Organisation)" in page and f'id="org-{guest}"' not in page   # a line under the table
    found = client.get("/admin/orgs?o_q=alpha").data.decode()
    assert "Zentrum Alpha" in found and "Institut Beta" not in found
    desc = client.get("/admin/orgs?o_sort=name&o_dir=desc").data.decode()
    assert desc.index("Zentrum Alpha") < desc.index("Institut Beta")


def test_the_page_of_an_organisation_holds_names_admins_and_limits(app, client):
    a = _org(app, "Zentrum Alpha", admin=NOBODY)
    page = client.get(f"/admin/orgs/{a}").data.decode()
    assert 'value="Zentrum Alpha"' in page and "Grenzen und Schalter" in page and "Organisation löschen" in page
    assert "noch ohne Konto" in page and 'name="who"' in page
    with app.app_context():
        guest = orgs.guest_id()
    assert client.get(f"/admin/orgs/{guest}").status_code == 404


def test_admin_by_orcid_and_taken_away_without_an_account(app, client):
    a = _org(app, "Zentrum Alpha", admin=NOBODY)
    app.test_client().get(f"/auth/dev-login?orcid={OTHER}&name=Andere")      # an account, not yet in the organisation
    r = client.post(f"/admin/orgs/{a}", data={"action": "admin", "who": OTHER}, follow_redirects=True)
    assert "ist jetzt Admin" in r.data.decode()
    r = client.post(f"/admin/orgs/{a}", data={"action": "unadmin", "orcid": NOBODY}, follow_redirects=True)
    assert "ist nicht mehr Admin" in r.data.decode()
    with app.app_context():
        org = db.session.get(Organisation, a)
        assert {m.orcid for m in org.members if m.role == "admin"} == {OTHER}
