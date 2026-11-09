"""Families from the construct catalogue and constructs of the catalogue changed on this installation (concept
konzept-konstrukt-repo, E10): families set here stay and come first; a changed construct keeps its content until an
admin keeps it as a construct of its own or takes the catalogue's version again."""
import copy
import io
import json
import zipfile

from app import construct_catalog as catalog
from app import construct_families, constructs
from app.extensions import db
from app.models import Construct


def test_families_of_the_catalogue_and_the_ones_set_here(app, monkeypatch):
    m = copy.deepcopy(catalog.manifest())
    m["families"] = [{"id": "technik", "name": {"de": "Technik", "en": "Technology"},
                      "members": [{"key": "tb", "label": {"de": "TB, 12 Items", "en": "TB, 12 items"}},
                                  {"key": "ati", "label": {"de": "ATI, 9 Items", "en": "ATI, 9 items"}}]}]
    monkeypatch.setattr(catalog, "active", lambda: m)
    with app.app_context():
        assert construct_families.all_()["tb"] == ("Technik", "TB, 12 Items")
        construct_families.put("tb", "Eigene Familie", "Kurz")  # set here: comes first, nothing lost
        construct_families.put("kut", "Eigene Familie", "Lang")
        db.session.commit()
        known = construct_families.all_()
        assert known["tb"] == ("Eigene Familie", "Kurz") and known["ati"] == ("Technik", "ATI, 9 Items")
        name, data = construct_families.catalogue_file()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            text = z.read("families/eigene-familie.yaml").decode()
        family = json.loads(text[text.index("{"):])
        assert family["id"] == "eigene-familie" and {x["key"] for x in family["members"]} == {"tb", "kut"}


def test_no_families_file_without_two_versions(app):
    with app.app_context():
        construct_families.put("tb", "Allein", "eine")
        db.session.commit()
        assert construct_families.catalogue_file() is None


def test_a_construct_changed_here_is_not_overwritten(app, client):
    with app.app_context():
        c = constructs.get("kamb")
        assert c.catalog_hash and not constructs.locally_changed(c)  # the baseline from the first start
        c.description = "Hier geändert."
        db.session.commit()
        assert constructs.locally_changed(c) and [x.key for x, _ in constructs.locally_changed_rows()] == ["kamb"]
        c.version = "1.0"  # as if the catalogue now had a newer version (2.0) than the row
        db.session.commit()
        constructs.ensure_seed()
        assert constructs.get("kamb").description == "Hier geändert."  # kept
    page = client.get("/admin/constructs").data.decode()
    assert "Hier geändert" in page and "Als eigenes Konstrukt behalten" in page
    client.post("/admin/constructs/catalog", data={"action": "keep_local", "key": "kamb"})
    with app.app_context():
        kept = db.session.scalar(db.select(Construct).filter_by(key="kamb_lokal"))
        assert kept.description == "Hier geändert." and kept.source == "instance"
        c = constructs.get("kamb")
        assert c.description != "Hier geändert." and c.version == "2.0" and not constructs.locally_changed(c)


def test_take_the_catalogues_version_again(app, client):
    with app.app_context():
        c = constructs.get("age")
        c.name = "Alter (hier)"
        db.session.commit()
    client.post("/admin/constructs/catalog", data={"action": "reset", "key": "age"})
    with app.app_context():
        assert constructs.get("age").name == "Alter" and not constructs.locally_changed(constructs.get("age"))
        assert db.session.scalar(db.select(Construct).filter_by(key="age_lokal")) is None
    assert client.post("/admin/constructs/catalog", data={"action": "reset", "key": "age"}).status_code == 404
