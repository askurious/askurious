"""Places of an organisation and requests for more (app/org_seats.py, Philipp 2026-11-13): the admins of the
organisation see them under Verwaltung > Personen and ask with a reason, Slack hears of it, a site admin grants with a
number (-1: no limit) or refuses."""
from app import notify, orgs
from app.extensions import db
from app.models import Organisation, OrgSeatRequest
from tests.accounts.test_organisations import _org_with_admin, _as, OTHER


def _limit(app, org_id, n):
    with app.app_context():
        db.session.get(Organisation, org_id).max_accounts = n
        db.session.commit()


def test_the_people_page_shows_the_places_and_the_request(app, client):
    org_id, _ = _org_with_admin(app, client)
    admin = _as(app, OTHER, "Kollegin")
    page = admin.get(f"/org/{org_id}/people").data.decode()
    assert 'id="plaetze"' in page and "unbegrenzt" in page and 'id="plaetze-beantragen"' not in page  # no limit, no request
    _limit(app, org_id, 5)
    page = admin.get(f"/org/{org_id}/people").data.decode()
    assert "von 5 belegt" in page and 'href="#plaetze-beantragen"' in page and 'id="plaetze-beantragen"' in page


def test_a_request_goes_to_slack_and_a_site_admin_grants_it(app, client, monkeypatch):
    org_id, _ = _org_with_admin(app, client)
    _limit(app, org_id, 5)
    said = []
    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: said.append((text, blocks)))
    admin = _as(app, OTHER, "Kollegin")
    assert admin.post(f"/org/{org_id}/people/seats", data={"reason": " "}).status_code == 302  # no reason: refused
    assert not said
    admin.post(f"/org/{org_id}/people/seats", data={"reason": "Neues Projekt mit 12 Studierenden"})
    assert len(said) == 1 and "Mehr Plätze beantragt" in said[0][0] and f"Nr. {org_id}" in said[0][0]
    assert "Studierenden" not in str(said[0])  # short messages: no reason, no name
    admin.post(f"/org/{org_id}/people/seats", data={"reason": "noch einmal"})  # one open request at a time
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(OrgSeatRequest.id))) == 1
        rid = db.session.scalar(db.select(OrgSeatRequest.id))
    page = admin.get(f"/org/{org_id}/people").data.decode()
    assert "liegt den Admins der Plattform vor" in page and 'href="#plaetze-beantragen"' not in page
    # the site admin sees it on the list and on the organisation's page, with twice the places as preset
    assert f"/admin/orgs/{org_id}#plaetze" in client.get("/admin/orgs").data.decode()
    page = client.get(f"/admin/orgs/{org_id}").data.decode()
    assert "Neues Projekt mit 12 Studierenden" in page and 'name="seats" type="number" min="-1" max="100000" value="10"' in page
    assert client.post(f"/admin/orgs/{org_id}/seats", data={"request_id": rid, "action": "grant", "seats": "0"}).status_code == 302
    with app.app_context():
        assert db.session.get(OrgSeatRequest, rid).status == "open"  # 0 is no number of places
    client.post(f"/admin/orgs/{org_id}/seats", data={"request_id": rid, "action": "grant", "seats": "12"})
    with app.app_context():
        row = db.session.get(OrgSeatRequest, rid)
        assert row.status == "granted" and row.seats_before == 5 and row.seats_after == 12
        assert orgs.get(org_id).max_accounts == 12
    page = admin.get(f"/org/{org_id}/people").data.decode()
    assert "von 12 belegt" in page and "angenommen: jetzt 12 Plätze" in page


def test_minus_one_means_no_limit_and_a_refusal_keeps_the_places(app, client, monkeypatch):
    org_id, _ = _org_with_admin(app, client)
    _limit(app, org_id, 5)
    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: None)
    admin = _as(app, OTHER, "Kollegin")
    admin.post(f"/org/{org_id}/people/seats", data={"reason": "mehr"})
    with app.app_context():
        rid = db.session.scalar(db.select(OrgSeatRequest.id))
    client.post(f"/admin/orgs/{org_id}/seats", data={"request_id": rid, "action": "refuse"})
    with app.app_context():
        assert db.session.get(OrgSeatRequest, rid).status == "refused" and orgs.get(org_id).max_accounts == 5
    assert "abgelehnt" in admin.get(f"/org/{org_id}/people").data.decode()
    admin.post(f"/org/{org_id}/people/seats", data={"reason": "bitte doch"})  # a new one after the refusal
    with app.app_context():
        rid = db.session.scalar(db.select(OrgSeatRequest.id).where(OrgSeatRequest.status == "open"))
    client.post(f"/admin/orgs/{org_id}/seats", data={"request_id": rid, "action": "grant", "seats": "-1"})
    with app.app_context():
        assert orgs.get(org_id).max_accounts is None and orgs.accounts_left(orgs.get(org_id)) is None


def test_only_site_admins_decide(app, client, monkeypatch):
    org_id, _ = _org_with_admin(app, client)
    _limit(app, org_id, 5)
    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: None)
    admin = _as(app, OTHER, "Kollegin")
    admin.post(f"/org/{org_id}/people/seats", data={"reason": "mehr"})
    with app.app_context():
        rid = db.session.scalar(db.select(OrgSeatRequest.id))
    answer = admin.post(f"/admin/orgs/{org_id}/seats", data={"request_id": rid, "action": "grant", "seats": "-1"})
    assert answer.status_code in (403, 404) or "/admin/orgs" not in answer.headers.get("Location", "")
    with app.app_context():
        assert orgs.get(org_id).max_accounts == 5


def test_the_groups_page_explains_groups_with_a_link_to_the_handbook(app, client):
    org_id, _ = _org_with_admin(app, client)
    page = _as(app, OTHER, "Kollegin").get(f"/org/{org_id}/groups").data.decode()
    head = page.split("Gruppen der Organisation")[1]
    assert "Gruppen sind die Teams innerhalb der Organisation" in head and '/docs/more#gruppen' in head
