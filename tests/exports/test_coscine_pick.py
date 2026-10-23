"""Coscine (app/coscine.py, experimental), Coscine-Ausbau 2026-10-20, package E (K6, E9, E10): images and tables taken
from a Coscine resource into a survey, copied like an upload. Coscine's API is replaced by FakeCoscine of
test_coscine.py."""
import io

from app.extensions import db
from app.models import Item
from tests.conftest import add
from tests.exports.test_coscine import connect, fake, on  # noqa: F401 - fake is a fixture


def _png():
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


def _put(fake, path, data, resource="r1"):
    fake.files[path], fake.where[path] = data, resource


def _image_item(app):
    with app.app_context():
        return db.session.scalar(db.select(Item.id).filter_by(type="image"))


def test_picker_lists_resources_and_fitting_files(app, client, survey, fake):
    on(client)
    connect(client, fake)
    add(client, "image")
    item_id = _image_item(app)
    page = client.get(f"/surveys/1/editor/items/{item_id}").data.decode()
    assert "aus Coscine wählen" in page and "coscine.js" in page
    targets = client.get("/coscine/survey/1/pick?art=image").get_json()["targets"]
    assert [t["value"] for t in targets] == ["p1:r1", "p1:r2", "p1:r3"]
    _put(fake, "stimuli/roboter.png", _png())
    _put(fake, "stimuli/tabelle.csv", b"a;b")
    files = client.get("/coscine/survey/1/pick?art=image&ziel=p1:r1").get_json()["files"]
    assert [f["path"] for f in files] == ["stimuli/roboter.png"]
    files = client.get("/coscine/survey/1/pick?art=table&ziel=p1:r1").get_json()["files"]
    assert [f["path"] for f in files] == ["stimuli/tabelle.csv"]


def test_image_taken_from_coscine_and_again(app, client, survey, fake):
    on(client)
    connect(client, fake)
    add(client, "image")
    item_id = _image_item(app)
    _put(fake, "stimuli/roboter.png", _png())
    r = client.post(f"/coscine/survey/1/pick/image/{item_id}", data={"ziel": "p1:r1", "pfad": "stimuli/roboter.png"},
                    follow_redirects=True)
    assert "Bild aus Coscine übernommen." in r.data.decode()
    with app.app_context():
        item = db.session.get(Item, item_id)
        first = [a.filename for a in item.assets]
        assert len(first) == 1 and item.config["coscine"]["pfad"] == "stimuli/roboter.png"
    page = client.get(f"/surveys/1/editor/items/{item_id}").data.decode()
    assert "Aus Coscine: stimuli/roboter.png" in page and "neu holen" in page
    client.post(f"/coscine/survey/1/pick/image/{item_id}", data={"ziel": "p1:r1", "pfad": "stimuli/roboter.png"})
    with app.app_context():
        again = [a.filename for a in db.session.get(Item, item_id).assets]
    assert len(again) == 1 and again != first  # a new copy replaces the old one


def test_refused_files(app, client, survey, fake):
    on(client)
    connect(client, fake)
    add(client, "image")
    item_id = _image_item(app)
    _put(fake, "kein-bild.png", b"not an image")
    for path in ("kein-bild.png", "stimuli/tabelle.csv", "../x.png"):
        r = client.post(f"/coscine/survey/1/pick/image/{item_id}", data={"ziel": "p1:r1", "pfad": path},
                        follow_redirects=True)
        assert "Bild aus Coscine übernommen." not in r.data.decode()
    with app.app_context():
        assert not db.session.get(Item, item_id).assets
    assert client.get("/coscine/survey/1/pick/text?ziel=p1:r1&pfad=x.png").status_code == 502


def test_table_text_from_coscine(app, client, survey, fake):
    on(client)
    connect(client, fake)
    _put(fake, "listen/berufe.csv", "Bäcker/in;7123\nKöchin;7124\n".encode("cp1252"))
    answer = client.get("/coscine/survey/1/pick/text?ziel=p1:r1&pfad=listen/berufe.csv").get_json()
    assert answer["text"].startswith("Bäcker/in;7123")
    _put(fake, "listen/utf8.csv", "﻿Marke;Modell\nVW;Golf\n".encode("utf-8"))
    assert client.get("/coscine/survey/1/pick/text?ziel=p1:r1&pfad=listen/utf8.csv").get_json()["text"].startswith("Marke")


def test_picker_needs_feature_token_and_editor_role(app, client, survey, fake):
    add(client, "image")
    item_id = _image_item(app)
    assert "aus Coscine wählen" not in client.get(f"/surveys/1/editor/items/{item_id}").data.decode()
    assert client.get("/coscine/survey/1/pick?art=image").status_code == 404
    on(client)
    assert "aus Coscine wählen" not in client.get(f"/surveys/1/editor/items/{item_id}").data.decode()  # no token yet
    assert client.get("/coscine/survey/1/pick?art=image").status_code == 404
