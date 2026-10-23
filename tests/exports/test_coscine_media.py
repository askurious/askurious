"""Coscine (app/coscine.py), Coscine-Ausbau 2026-10-20, package F: video and sound from Coscine for the element
"Video / Audio", loaded in the background and put through the pieces of an upload; experimental ("coscine_media",
Ralf 2026-10-20: images for everyone, video and sound experimental). Coscine's API is replaced by FakeCoscine."""
from app.extensions import db
from app.models import Item
from tests.conftest import add, experiments_on, fill_consent
from tests.exports.test_coscine import connect, fake  # noqa: F401 - fake is a fixture

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200


def _put(fake, path, data):
    fake.files[path], fake.where[path] = data, "r1"


def _media_item(app, client):
    fill_consent(client)
    add(client, "media")
    with app.app_context():
        return db.session.scalar(db.select(Item.id).filter_by(type="media"))


def test_video_from_coscine_needs_the_experiment(app, client, survey, fake):
    connect(client, fake)
    item_id = _media_item(app, client)
    _put(fake, "videos/spot.mp4", MP4)
    page = client.get(f"/surveys/1/editor/items/{item_id}").data.decode()
    assert 'data-coscine-pick="media"' not in page
    assert client.post(f"/coscine/survey/1/pick/media/{item_id}", data={"ziel": "p1:r1", "pfad": "videos/spot.mp4"}).status_code == 404
    assert client.get("/coscine/survey/1/pick?art=media&ziel=p1:r1").status_code == 404
    assert client.get("/coscine/survey/1/pick?art=image&ziel=p1:r1").status_code == 200  # images stay for everyone


def test_video_taken_from_coscine(app, client, survey, fake):
    experiments_on(client, "coscine_media")
    connect(client, fake)
    item_id = _media_item(app, client)
    _put(fake, "videos/spot.mp4", MP4)
    _put(fake, "videos/notiz.txt", b"x")
    page = client.get(f"/surveys/1/editor/items/{item_id}").data.decode()
    assert 'data-coscine-pick="media"' in page
    files = client.get("/coscine/survey/1/pick?art=media&ziel=p1:r1").get_json()["files"]
    assert [f["path"] for f in files] == ["videos/spot.mp4"]
    r = client.post(f"/coscine/survey/1/pick/media/{item_id}", data={"ziel": "p1:r1", "pfad": "videos/spot.mp4", "key": ""})
    assert r.get_json() == {"ok": True}
    state = client.get(f"/coscine/survey/1/pick/media/{item_id}/state").get_json()["states"][""]
    assert state["state"] == "done" and state["pfad"] == "videos/spot.mp4"
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert len(item.assets) == 1 and item.config["files"][""]["mime"] == "video/mp4"
        assert item.config["files"][""]["bytes"] == len(MP4) and item.config["files"][""]["name"] == "spot.mp4"
    assert "Aus Coscine: videos/spot.mp4" in client.get(f"/surveys/1/editor/items/{item_id}").data.decode()


def test_refused_files_leave_an_error_and_no_file(app, client, survey, fake):
    experiments_on(client, "coscine_media")
    connect(client, fake)
    item_id = _media_item(app, client)
    _put(fake, "videos/kaputt.mp4", b"kein video" * 10)
    r = client.post(f"/coscine/survey/1/pick/media/{item_id}", data={"ziel": "p1:r1", "pfad": "videos/kaputt.mp4"})
    assert r.get_json() == {"ok": True}
    state = client.get(f"/coscine/survey/1/pick/media/{item_id}/state").get_json()["states"][""]
    assert state["state"] == "error" and "MP4" in state["error"]
    with app.app_context():
        assert not db.session.get(Item, item_id).assets
    assert "Aus Coscine fehlgeschlagen" in client.get(f"/surveys/1/editor/items/{item_id}").data.decode()
    for bad in ({"pfad": "../x.mp4"}, {"pfad": "bild.png"}, {"pfad": "videos/spot.mp4", "key": "unbekannt"}):
        r = client.post(f"/coscine/survey/1/pick/media/{item_id}", data={"ziel": "p1:r1", **bad})
        assert r.status_code == 400
    app.config["MEDIA_MAX_MB"] = 0  # larger than allowed: refused before anything is stored
    _put(fake, "videos/spot.mp4", MP4)
    client.post(f"/coscine/survey/1/pick/media/{item_id}", data={"ziel": "p1:r1", "pfad": "videos/spot.mp4"})
    state = client.get(f"/coscine/survey/1/pick/media/{item_id}/state").get_json()["states"][""]
    assert state["state"] == "error" and "größer" in state["error"]
