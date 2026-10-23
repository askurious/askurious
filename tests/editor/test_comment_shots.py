"""Images on comments from test runs and where a comment was written (app/comment_shots.py)."""
import csv
import io
import os
import re
import time
import zipfile

from app.extensions import db
from app.models import PreviewComment, Survey
from tests.conftest import add

ENV = {"env_device": "Smartphone", "env_os": "iOS", "env_browser": "Safari", "env_version": "19", "env_size": "390x844",
       "env_lang": "de", "env_scheme": "dunkel"}


def _png(width=300, height=200, color=(200, 30, 30)):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "PNG")
    return out.getvalue()


def _jpeg_with_gps():
    from PIL import Image

    img = Image.new("RGB", (400, 300), (10, 120, 200))
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (50.0, 46.0, 0.0)}  # GPS position
    exif[0x0110] = "Kamera X"                      # camera model
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


def _setup(client):
    add(client, "info")                                  # 2
    client.post("/surveys/1/editor/items/2", data={"code": "intro", "question": "Willkommen"})


def _send(client, url, data=None, image=None, name="bild.png"):
    form = {"block_id": "2", "item_id": "2", "page": "2", "text": "Bricht um", **ENV, **(data or {})}
    if image is not None:
        form["shot"] = (io.BytesIO(image), name)
    return client.post(url, data=form, content_type="multipart/form-data")


def _folder(app, uid):
    return os.path.join(os.path.dirname(app.config["UPLOAD_FOLDER"]), "comment-shots", uid)


def _uid(app):
    with app.app_context():
        return db.session.get(Survey, 1).uid


def _phone_path(client):
    html = client.get("/surveys/1/editor/preview?device=mobile").data.decode()
    return "/" + re.search(r'value="(https?://[^"]+/p/[^"]+)"', html).group(1).split("://", 1)[1].split("/", 1)[1]


def test_image_and_env_are_stored_shown_and_exported(app, client, survey):
    _setup(client)
    html = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    assert "data-shot-pick" in html and "data-shot-file" in html
    r = _send(client, "/surveys/1/editor/preview/comment", image=_jpeg_with_gps(), name="foto.jpg")
    assert r.status_code == 200 and r.json["shot"] is True
    cid = r.json["id"]
    with app.app_context():
        c = db.session.get(PreviewComment, cid)
        assert c.shot.endswith(".png") and (c.shot_w, c.shot_h) == (400, 300)
        assert c.env == {"device": "Smartphone", "os": "iOS", "browser": "Safari 19", "size": "390x844", "lang": "de",
                         "scheme": "dunkel"}
    files = os.listdir(_folder(app, _uid(app)))
    assert len(files) == 1
    shot = client.get(f"/surveys/1/editor/preview/comment/{cid}/shot")
    assert shot.status_code == 200 and shot.mimetype == "image/png" and "no-store" in shot.headers["Cache-Control"]
    from PIL import Image

    stored = Image.open(io.BytesIO(shot.data))
    assert not stored.getexif() and "exif" not in stored.info                   # no camera, no position
    assert client.get("/media/" + _uid(app) + "/" + files[0]).status_code == 404  # never public
    page = client.get("/surveys/1/editor/comments").data.decode()
    assert f"/preview/comment/{cid}/shot" in page and "iOS · Smartphone · Safari 19 · 390 × 844 · de · dunkel" in page
    assert "ZIP mit Bildern" in page
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/comments.csv").data.decode("utf-8-sig"))))
    assert rows[0]["image"] == f"bilder/{cid}.png" and rows[0]["browser"] == "Safari 19" and rows[0]["window"] == "390x844"
    z = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/comments.zip").data))
    assert sorted(z.namelist()) == ["bilder/%d.png" % cid, "comments.csv"]
    assert z.read(f"bilder/{cid}.png") == shot.data
    assert "comments.zip" in client.get("/surveys/1/data").data.decode()


def test_without_image_the_export_stays_a_csv(app, client, survey):
    _setup(client)
    r = _send(client, "/surveys/1/editor/preview/comment", data={"env_device": "Fernseher", "env_size": "9x9",
                                                                 "env_browser": "<b>", "env_lang": "de-DE"})
    assert r.status_code == 200 and r.json["shot"] is False
    with app.app_context():
        assert db.session.get(PreviewComment, r.json["id"]).env == {"os": "iOS", "lang": "de-DE", "scheme": "dunkel"}
    data = client.get("/surveys/1/data").data.decode()
    assert "comments.csv" in data and "comments.zip" not in data


def test_refused_images_store_neither_comment_nor_file(app, client, survey):
    _setup(client)
    r = _send(client, "/surveys/1/editor/preview/comment", image=b"GIF89a kein Bild", name="x.gif")
    assert r.status_code == 400 and r.json["error"]
    from app import comment_shots

    big = b"\x89PNG\r\n\x1a\n" + b"0" * (comment_shots.MAX_BYTES + 10)
    assert _send(client, "/surveys/1/editor/preview/comment", image=big).status_code == 400
    app.config["COMMENT_SHOTS_MAX_MB"] = 0                                      # no room left in this survey
    assert "ohne Bild" in _send(client, "/surveys/1/editor/preview/comment", image=_png()).json["error"]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(PreviewComment.id))) == 0
    assert not os.path.isdir(_folder(app, _uid(app))) or not os.listdir(_folder(app, _uid(app)))


def test_testers_see_only_their_own_images_and_take_them_along(app, client, survey):
    _setup(client)
    path = _phone_path(client)
    phone = app.test_client()                                                   # a test person, not logged in
    phone.get(path + "?page=2", follow_redirects=True)
    mine = _send(phone, path + "/comment", image=_png()).json
    other = _send(client, "/surveys/1/editor/preview/comment", image=_png(color=(0, 0, 0))).json
    assert phone.get(f"{path}/comment/{mine['id']}/shot").status_code == 200
    assert phone.get(f"{path}/comment/{other['id']}/shot").status_code == 404
    page = phone.get(path + "?page=2", follow_redirects=True).data.decode()
    assert f"/comment/{mine['id']}/shot" in page and f"/comment/{other['id']}/shot" not in page         and '<span class="p-comments-hint"><svg class="icon"' in page  # the other's image: only named, with the camera icon
    member = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    assert f"/comment/{mine['id']}/shot" in member and f"/comment/{other['id']}/shot" in member  # members see all
    assert client.get(f"/surveys/1/editor/preview/comment/{mine['id']}/shot").status_code == 200
    folder = _folder(app, _uid(app))
    assert len(os.listdir(folder)) == 2
    assert phone.post(f"{path}/comment/{mine['id']}/delete").status_code == 200
    assert len(os.listdir(folder)) == 1                                         # the image went with the comment


def test_phone_link_limit_for_images(app, client, survey):
    _setup(client)
    app.config["COMMENT_SHOTS_PER_DAY"] = 1
    path = _phone_path(client)
    phone = app.test_client()
    phone.get(path + "?page=2", follow_redirects=True)
    assert _send(phone, path + "/comment", image=_png()).status_code == 200
    r = _send(phone, path + "/comment", image=_png())
    assert r.status_code == 400 and "ohne Bild" in r.json["error"]
    assert _send(phone, path + "/comment").status_code == 200                   # without an image it still goes
    assert _send(client, "/surveys/1/editor/preview/comment", image=_png()).status_code == 200  # members: no limit


def test_remove_image_keeps_the_comment(app, client, survey):
    _setup(client)
    cid = _send(client, "/surveys/1/editor/preview/comment", image=_png()).json["id"]
    client.post(f"/surveys/1/editor/comments/{cid}", data={"remove_shot": "1"})
    with app.app_context():
        c = db.session.get(PreviewComment, cid)
        assert c.text == "Bricht um" and c.shot is None and c.status == "open"
    assert os.listdir(_folder(app, _uid(app))) == []
    assert client.get(f"/surveys/1/editor/preview/comment/{cid}/shot").status_code == 404


def test_sweep_removes_folders_of_deleted_surveys_and_stray_files(app, client, survey):
    _setup(client)
    _send(client, "/surveys/1/editor/preview/comment", image=_png())
    folder = _folder(app, _uid(app))
    stray = os.path.join(folder, "AAAAAAAAAAAAAAAA.png")
    open(stray, "wb").write(_png())
    gone = os.path.join(os.path.dirname(folder), "no-such-survey")
    os.makedirs(gone)
    with app.app_context():
        from app import comment_shots

        assert comment_shots.run() == 1 and not os.path.exists(gone)
        assert os.path.exists(stray)                                            # might belong to a comment being stored
        old = time.time() - comment_shots.STALE_SECONDS - 10
        os.utime(stray, (old, old))
        assert comment_shots.run() == 1 and not os.path.exists(stray)
        assert len(os.listdir(folder)) == 1                                     # held by its comment


def test_preview_pages_may_take_a_picture_of_themselves(app, client, survey):
    _setup(client)
    page = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True)
    html = page.data.decode()
    assert "display-capture=(self)" in page.headers["Permissions-Policy"]
    assert "data-shot-capture" in html and "data-shot-editor" in html and "comment_shot.js" in html
    assert html.index("comment_shot.js") < html.index("comments.js")                 # comments.js uses it
    mobile = client.get("/surveys/1/editor/preview?device=mobile")
    assert "display-capture=(self)" in mobile.headers["Permissions-Policy"] and "comment_shot.js" in mobile.data.decode()
    phone = app.test_client().get(_phone_path(client) + "?page=2", follow_redirects=True)
    assert "display-capture=(self)" in phone.headers["Permissions-Policy"]
    assert "display-capture=()" in client.get("/surveys/1/editor/").headers["Permissions-Policy"]   # nowhere else
