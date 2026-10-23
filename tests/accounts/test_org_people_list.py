"""Verwaltung > Personen like Administration > Personen (Ralf 2026-10-18): sorted and searched by the database, counts
only of the organisation's own surveys, symbols for the actions, and "Person hinzufügen" with and without ORCID iD,
the organisation given. On the platform the user types of "Person hinzufügen" follow the chosen organisation."""
import re

from app import orgs
from app.extensions import db
from app.models import OrganisationMember, SignInOrcid, User, UserLogin
from tests.accounts.test_organisations import FOURTH, OTHER, THIRD, _as, _org_with_admin
from tests.conftest import allow


def _table(page):
    return page.split('id="personen"')[1].split("</table>")[0]


def test_people_of_an_organisation_are_sorted_and_searched(app, client):
    org_id, _ = _org_with_admin(app, client)
    for orcid, name in ((THIRD, "Dritte Person"), (FOURTH, "Anton Vierter")):
        allow(app, orcid, keep=True)
        _as(app, orcid, name)
    other = _as(app, OTHER, "Kollegin")
    for orcid, name in ((THIRD, "Dritte Person"), (FOURTH, "Anton Vierter")):  # an existing account joins by accepting the invitation (audit 2026-10-18, AUTHZ-02)
        assert other.post(f"/org/{org_id}/people", data={"action": "add", "orcid": orcid}).status_code == 302
        link = re.search(r"/org/join/[A-Za-z0-9_.-]+", other.get(f"/org/{org_id}/people").data.decode()).group(0)
        _as(app, orcid, name).post(link, data={"answer": "join"})
    table = _table(other.get(f"/org/{org_id}/people").data.decode())
    assert table.index("Anton Vierter") < table.index("Dritte Person") < table.index("Kollegin")  # by name first
    assert 'p_sort=name' in table and 'p_sort=surveys' in table and 'p_sort=last' in table       # heads that sort
    table = _table(other.get(f"/org/{org_id}/people?p_sort=name&p_dir=desc").data.decode())
    assert table.index("Kollegin") < table.index("Anton Vierter")
    table = _table(other.get(f"/org/{org_id}/people?p_q=dritte").data.decode())
    assert "Dritte Person" in table and "Anton Vierter" not in table and "Kollegin" not in table
    table = _table(other.get(f"/org/{org_id}/people?p_q=lehrstuhl").data.decode())  # by the name of its group
    assert "Kollegin" in table and "Dritte Person" not in table
    # symbols, no worded buttons: the × takes someone out who is at home elsewhere
    table = _table(other.get(f"/org/{org_id}/people").data.decode())
    assert 'title="Aus der Organisation entfernen"' in table and ">Entfernen</button>" not in table


def test_an_organisation_admin_creates_an_account_without_orcid(app, client):
    org_id, _ = _org_with_admin(app, client)
    other = _as(app, OTHER, "Kollegin")
    page = other.get(f"/org/{org_id}/people").data.decode()
    assert 'id="person-hinzufuegen"' in page and f'action="/org/{org_id}/people/new"' in page
    assert 'name="org"' not in page.split('id="person-hinzufuegen"')[1]                      # the organisation is given
    r = other.post(f"/org/{org_id}/people/new", data={"name": "Neue Person", "email": "neu@example.org",
                                                      "valid_mode": "type"})
    assert r.status_code == 302 and f"/org/{org_id}/people/" in r.headers["Location"]
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(name="Neue Person"))
        assert db.session.get(UserLogin, user.id).email == "neu@example.org"
        assert orgs.home_id(user) == org_id
        uid = user.id
    page = other.get(r.headers["Location"]).data.decode()
    assert "Einmal-Link" in page and "/auth/password/" in page                            # shown once,
    assert "/auth/password/" not in other.get(r.headers["Location"]).data.decode()        # not again
    # the same address once more: refused, the dialog comes back with what was entered
    r = other.post(f"/org/{org_id}/people/new", data={"name": "Zweite", "email": "neu@example.org", "valid_mode": "type"})
    assert r.status_code == 422 and 'value="Zweite"' in r.data.decode() and "schon ein Konto" in r.data.decode()
    # blocked from the list, back to the list
    r = other.post(f"/org/{org_id}/people/{uid}", data={"action": "block", "back": "p_q=neue"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/org/{org_id}/people?p_q=neue#personen")
    with app.app_context():
        assert db.session.get(UserLogin, uid).status == UserLogin.BLOCKED


def test_an_orcid_taken_in_gets_only_types_of_the_organisation(app, client):
    from app import account_types

    org_id, _ = _org_with_admin(app, client)
    with app.app_context():
        foreign = account_types.default_type(orgs.default_id()).id                        # a type of "main"
    other = _as(app, OTHER, "Kollegin")
    new = "0000-0000-0000-0562"
    r = other.post(f"/org/{org_id}/people", data={"action": "add", "orcid": f"https://orcid.org/{new}",
                                                  "account_type": str(foreign), "valid_mode": "type"})
    assert r.status_code == 422 and "gibt es in der Organisation nicht" in r.data.decode()
    r = other.post(f"/org/{org_id}/people", data={"action": "add", "orcid": f"https://orcid.org/{new}",
                                                  "valid_mode": "type", "kind_live": "edit"})
    assert r.status_code == 302
    with app.app_context():
        listed = db.session.get(SignInOrcid, new)
        assert listed is not None and listed.org_id == org_id
        assert db.session.scalar(db.select(OrganisationMember).filter_by(orcid=new)) is None


def test_platform_dialog_tags_user_types_with_their_organisation(app, client):
    org_id, _ = _org_with_admin(app, client)
    page = client.get("/admin/people").data.decode().split('id="person-hinzufuegen"')[1]
    assert 'name="org"' in page and "data-org=" in page
