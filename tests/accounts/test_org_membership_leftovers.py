"""Memberships of organisations follow the account (Ralf 2026-10-18: "Askurious" showed 5 people on the tab
"Organisationen" and 0 in every list): deleting an account removes its rows in organisation_member, linking an ORCID
iD to a password account moves them to the iD, and the count on the tab counts what the lists show."""
from app import accounts, orgs
from app.extensions import db
from app.models import Organisation, OrganisationMember, User, local_key

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"


def _org_with(app, orcid, name="Lehrstuhl"):
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        org = orgs.make(name, me)
        db.session.flush()
        orgs.add(org, orcid, by=me)
        db.session.commit()
        return org.id


def test_deleting_an_account_removes_its_org_memberships(app, client):
    app.test_client().get(f"/auth/dev-login?orcid={OTHER}&name=Andere")
    org_id = _org_with(app, OTHER)
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        accounts.delete(db.session.scalar(db.select(User).filter_by(orcid=OTHER)), "admin", {}, admin=me)
        assert db.session.scalar(db.select(OrganisationMember).filter_by(orcid=OTHER)) is None
        assert orgs.counts(db.session.get(Organisation, org_id))["people"] == 0


def test_linking_an_orcid_moves_the_org_membership(app, client):
    with app.app_context():
        user = User(orcid=local_key(), name="Passwort")
        db.session.add(user)
        db.session.commit()
        old = user.orcid
    org_id = _org_with(app, old)
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=old))
        accounts.link_orcid(user, OTHER, "Andere")
        db.session.commit()
        assert db.session.scalar(db.select(OrganisationMember).filter_by(orcid=old)) is None
        assert db.session.scalar(db.select(OrganisationMember).filter_by(orcid=OTHER, org_id=org_id)) is not None


def test_the_count_shows_accounts_and_invited_apart(app, client):
    org_id = _org_with(app, OTHER)                                # an iD without an account, e.g. a first admin
    with app.app_context():
        c = orgs.counts(db.session.get(Organisation, org_id))
        assert c["people"] == len(orgs.members_of(db.session.get(Organisation, org_id))) == 0
        assert c["invited"] == 1
