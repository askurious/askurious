"""Admins of an organisation are named by their account, not by an ORCID iD. Since migration 0085 accounts with a
password have no iD, and asking for an empty iD found any such account: on the RWTH server the organisation CLS showed
Carolin Topel as admin, who is not in it, while its admin had a password account (Ralf 2026-10-19). An admin entered
by iD before a first sign-in is taken out as a whole by the ×, with "Rückgängig"; before, the entry stayed a
membership that no list of people shows."""
from app import accounts, orgs
from app.extensions import db
from app.models import Organisation, OrganisationMember, User

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
NOBODY = "0000-0000-0000-0124"   # an iD without an account


def _org(app, admin):
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        org = orgs.make("CLS", me)
        db.session.flush()
        orgs.add(org, admin(me), role="admin", by=me)
        db.session.commit()
        return org.id


def test_an_admin_with_a_password_is_named_by_the_account(app, client):
    def admin(me):
        accounts.create_local("Carolin Topel", "carolin@example.org", "", False, me)   # in no organisation
        return accounts.create_local("Laura Zentgraf", "laura@example.org", "", False, me)
    a = _org(app, admin)
    for page in (client.get("/admin/orgs").data.decode(), client.get(f"/admin/orgs/{a}").data.decode()):
        assert "Laura Zentgraf" in page and "Carolin Topel" not in page
    with app.app_context():
        assert orgs.display_name(None) == "" and orgs.display_name("") == ""


def test_the_cross_takes_an_entry_without_an_account_out_and_undo_brings_it_back(app, client):
    a = _org(app, lambda me: NOBODY)
    page = client.post(f"/admin/orgs/{a}", data={"action": "unadmin", "orcid": NOBODY}, follow_redirects=True).data.decode()
    assert "nicht mehr in der Organisation" in page and "Rückgängig" in page
    with app.app_context():
        assert db.session.scalar(db.select(OrganisationMember.id).filter_by(org_id=a)) is None
        assert orgs.counts(db.session.get(Organisation, a))["invited"] == 0
    assert "Rückgängig" not in client.get(f"/admin/orgs/{a}").data.decode()   # offered once
    client.post(f"/admin/orgs/{a}", data={"action": "admin", "who": NOBODY, "who_name": "Niemand"})
    with app.app_context():
        assert [(m.orcid, m.role) for m in db.session.get(Organisation, a).members] == [(NOBODY, "admin")]


def test_the_log_names_a_password_account_without_none(app, client):
    from app import audit

    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        greta = accounts.create_local("Greta Clemens", "greta@example.org", "", False, me)
        db.session.commit()
        assert audit._who(greta).endswith("Greta Clemens") and audit._who(me).endswith(f"({ME})")
        with app.test_request_context():
            assert audit.log_who("Nr. 19 Greta Clemens (None)") == "Nr. 19 Greta Clemens"
