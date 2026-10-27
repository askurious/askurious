"""Short and long versions of a construct linked, and inserting a construct opened from the list (app/construct_families.py;
user test 2026-10-27, point 13)."""
from app import construct_families, constructs
from app.extensions import db


def _two(app):
    with app.app_context():
        keys = [c.key for c in constructs.search() if not constructs.is_linked_key(c.key)][:2]
    return keys


def test_admin_links_two_versions_and_the_pages_show_them(app, client, survey):
    a, b = _two(app)
    client.post(f"/admin/constructs/{a}/family", data={"family": "Testfamilie", "version": "Kurzfassung"})
    client.post(f"/admin/constructs/{b}/family", data={"family": "Testfamilie", "version": "Langfassung"})
    with app.app_context():
        assert construct_families.of(a) == ("Testfamilie", "Kurzfassung")
        assert construct_families.siblings(a) == [(b, "Langfassung")]
    page = client.get(f"/constructs/{a}").data.decode()
    assert 'id="fassungen"' in page and "Langfassung" in page and "Kurzfassung" in page
    listing = client.get("/constructs/").data.decode()
    assert "weitere Fassungen:" in listing
    client.post(f"/admin/constructs/{a}/family", data={"family": "", "version": ""})
    with app.app_context():
        assert construct_families.of(a) is None


def test_a_construct_from_the_list_offers_the_own_drafts(app, client, survey):
    a, _ = _two(app)
    page = client.get(f"/constructs/{a}").data.decode()
    assert "In eine Befragung einfügen" in page and '<option value="1">Test</option>' in page
    chosen = client.get(f"/constructs/{a}?survey=1").data.decode()
    assert "Am Ende des Blocks einfügen" in chosen
