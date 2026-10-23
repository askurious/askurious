"""The "Schnellbox" and the full construct box (konzept-konstrukt-schnellbox-2026-10-16.md, Ralf: as recommended):
lists kept by the platform and by organisations, the editor's list, the box with "+ Einfügen" and "Rückgängig"."""
from app import constructs, orgs, shortlist
from app.extensions import db
from app.models import User


def _listed(app, org_id=None):
    with app.app_context():
        return [c.key for c in constructs.sidebar(db.session.scalar(db.select(User)), org_id)]


def _main(app):
    with app.app_context():
        return orgs.default_id()


def test_platform_list_then_the_organisations_own(app, client, survey):
    for key in ("kut", "tb", "kut"):  # the second "kut" is refused: once per list
        client.post("/constructs/shortlist/platform", data={"action": "add", "key": key})
    with app.app_context():
        assert shortlist.keys(None) == ["kut", "tb"]
    client.post("/constructs/tb/favourite", data={"json": "1"})  # own stars first, each construct once
    client.post("/constructs/bfi10/favourite", data={"json": "1"})
    assert _listed(app) == ["bfi10", "tb", "kut"]
    page = client.get("/surveys/1/editor/").data.decode()
    assert "empfohlen von Askurious" in page
    assert "Alle 38 Konstrukte durchsuchen" in page or "Konstrukte durchsuchen" in page
    assert "data-construct-box" in page

    main = _main(app)  # the survey's organisation gets a list of its own: it replaces the platform's
    client.post(f"/constructs/shortlist/{main}", data={"action": "add", "key": "aub"})
    client.post(f"/constructs/shortlist/{main}", data={"action": "add", "key": "r1"})
    client.post(f"/constructs/shortlist/{main}", data={"action": "up", "key": "r1"})
    assert _listed(app, main) == ["bfi10", "tb", "r1", "aub"]
    r = client.post("/constructs/tb/favourite", data={"json": "1", "survey": "1"}).json  # star off: the list comes back
    assert [c["key"] for c in r["sidebar"]] == ["bfi10", "r1", "aub"] and r["sidebar"][1]["by"]
    client.post(f"/constructs/shortlist/{main}", data={"action": "remove", "key": "r1"})
    with app.app_context():
        assert shortlist.keys(main) == ["aub"]
    assert "Schnellbox der Organisation" in client.get(f"/org/{main}/constructs").data.decode()
    admin_page = client.get("/admin/constructs").data.decode()
    assert "Schnellbox der Plattform" in admin_page and "Technikbereitschaft" in admin_page


def test_only_keepers_change_a_list(app, client, survey):
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    assert other.post("/constructs/shortlist/platform", data={"action": "add", "key": "tb"}).status_code == 404
    assert other.post(f"/constructs/shortlist/{_main(app)}", data={"action": "add", "key": "tb"}).status_code == 404
    assert other.get("/constructs/box?keep=platform").status_code == 404
    assert client.post("/constructs/shortlist/platform", data={"action": "add", "key": "nope", "json": "1"}).json["done"] is False


def test_the_full_box_inserts_and_takes_back(app, client, survey):
    client.post("/constructs/shortlist/platform", data={"action": "add", "key": "tb"})
    html = client.get("/constructs/box?survey=1&q=technik").data.decode()
    assert "data-box-insert=\"construct:tb\"" in html and "empfohlen von Askurious" in html and "Empfohlen von Askurious (1)" in html
    assert "Testaussage 1 von TB" in html  # the items, opened with the card (stand-in wording, tests/data/instruments.json)
    only = client.get("/constructs/box?survey=1&pick=org").data.decode()
    assert only.count('class="cbox-card"') == 1
    langs = client.get("/constructs/box?survey=1&langs=1").data.decode()  # de and en: no German-only scale
    assert 'data-key="tb"' not in langs and 'data-key="bfi10"' in langs

    r = client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:tb", "box": "1"}).json
    assert r["done"] and r["code"] == "TB" and r["undo_id"]
    with app.app_context():
        from app.models import Item
        assert db.session.scalar(db.select(db.func.count(Item.id)).filter_by(code="TB")) == 1
    back = client.post("/surveys/1/editor/undo", data={"only": str(r["undo_id"])}).json
    assert back["done"]
    with app.app_context():
        from app.models import Item
        assert db.session.scalar(db.select(db.func.count(Item.id)).filter_by(code="TB")) == 0

    kept = client.get("/constructs/box?keep=platform").data.decode()
    assert "data-box-keep" in kept and "data-box-insert" not in kept and "in der Liste" in kept


def test_ainstein_names_the_recommended_first(app, client):
    client.post("/constructs/shortlist/platform", data={"action": "add", "key": "kut"})
    with app.test_request_context():
        from app import ainstein

        rows = ainstein._toolbox()
        assert rows[0][0] == "kut" and rows[0][2].startswith("[empfohlen]")
        assert not any(short.startswith("[empfohlen]") for _k, _n, short, _s in rows[1:])
        assert not ainstein._toolbox(page=True)[0][2].startswith("[empfohlen]")
