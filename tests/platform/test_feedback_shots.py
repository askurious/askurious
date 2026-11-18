"""The image on a piece of feedback (app/feedback_shots.py; concept "Bildschirmfotos an Rückmeldungen", Ralf 2026-11-18,
E1-E9): stored anew without metadata, only for platform admins, never to Slack, gone 30 days after "erledigt", with the
account and with "Bild entfernen"."""
import io
import os
import time
from datetime import timedelta
from pathlib import Path

from app.extensions import db
from app.models import Feedback, User, utcnow

ROOT = Path(__file__).resolve().parents[2]


def _png(width=300, height=200):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


def _jpeg_with_gps():
    from PIL import Image

    img = Image.new("RGB", (400, 300), (10, 120, 200))
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (50.0, 46.0, 0.0)}  # GPS position
    exif[0x0110] = "Kamera X"                      # camera model
    exif[315] = "Fotografin"                       # creator: no rights either, unlike comment images
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


def _send(client, image=None, name="bild.png", message="Hier bricht es um"):
    form = {"message": message, "severity": "2", "page": "/dashboard/"}
    if image is not None:
        form["shot"] = (io.BytesIO(image), name)
    return client.post("/feedback/new", data=form, content_type="multipart/form-data")


def _folder(app):
    return os.path.join(os.path.dirname(app.config["UPLOAD_FOLDER"]), "feedback-shots")


def _item(app, fid=1):
    with app.app_context():
        item = db.session.get(Feedback, fid)
        db.session.expunge(item)
        return item


def test_image_is_stored_anew_without_metadata(app, client):
    from PIL import Image

    assert _send(client, _jpeg_with_gps(), "foto.jpg").status_code == 302
    item = _item(app)
    assert item.shot and item.shot.endswith(".png") and (item.shot_w, item.shot_h) == (400, 300)
    path = os.path.join(_folder(app), item.shot)
    with Image.open(path) as img:
        assert img.format == "PNG" and not img.getexif() and not img.info.get("XML:com.adobe.xmp")
        assert not any(k in img.info for k in ("Author", "exif"))
    raw = Path(path).read_bytes()
    assert b"Kamera X" not in raw and b"Fotografin" not in raw
    page = client.get("/feedback/").data.decode()
    assert f"/feedback/{item.id}/shot" in page and "Bild entfernen" in page and "data-confirm-danger" in page
    shot = client.get(f"/feedback/{item.id}/shot")
    assert shot.status_code == 200 and shot.mimetype == "image/png" and "no-store" in shot.headers["Cache-Control"]


def test_wrong_or_too_large_image_is_refused_but_the_text_is_saved(app, client, monkeypatch):
    from app import comment_shots

    r = _send(client, b"kein Bild", "x.png", message="Text bleibt")
    assert r.status_code == 302
    flashed = client.get("/dashboard/").data.decode()
    assert "Das Bild wurde nicht gespeichert" in flashed
    monkeypatch.setattr(comment_shots, "MAX_BYTES", 100)
    _send(client, _png(), message="Zu groß")
    with app.app_context():
        items = list(db.session.scalars(db.select(Feedback).order_by(Feedback.id)))
        assert [(f.message, f.shot) for f in items] == [("Text bleibt", None), ("Zu groß", None)]
    assert not os.path.isdir(_folder(app)) or not os.listdir(_folder(app))


def test_full_folder_refuses_new_images(app, client):
    app.config["FEEDBACK_SHOTS_MAX_MB"] = 0
    _send(client, _png())
    assert _item(app).shot is None
    assert "Gerade sind sehr viele Bilder zu Rückmeldungen gespeichert" in client.get("/dashboard/").data.decode()


def test_only_platform_admins_see_the_image(app, client):
    _send(client, _png())
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/feedback/1/shot").status_code == 403
    assert other.post("/feedback/1/shot/delete").status_code == 403
    assert app.test_client().get("/feedback/1/shot").status_code in (302, 401)       # not signed in
    assert client.get("/feedback/99/shot").status_code == 404
    assert _item(app).shot


def test_slack_gets_no_image_only_the_note(app, client, monkeypatch):
    from app import notify

    said = []
    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: said.append((text, blocks)))
    _send(client, _png())
    _send(client, message="ohne Bild")
    (with_image, blocks), (without, _) = said
    assert "mit Bild" in with_image and "mit Bild" not in without
    dump = repr(blocks)
    assert "image" not in dump.replace("mit Bild", "") and "/shot" not in dump and ".png" not in dump


def test_image_goes_30_days_after_done_and_reopening_stops_it(app, client):
    from app import feedback_shots

    _send(client, _png())
    _send(client, _png())
    client.post("/feedback/1/status", data={"status": "done"})
    client.post("/feedback/2/status", data={"status": "done"})
    client.post("/feedback/2/status", data={"status": "new"})           # "wieder öffnen": done_at gone
    first = _item(app).shot
    with app.app_context():
        assert db.session.get(Feedback, 2).done_at is None
        assert feedback_shots.run(utcnow() + timedelta(days=29)) == 0
        assert feedback_shots.run(utcnow() + timedelta(days=31)) == 1
        one, two = db.session.get(Feedback, 1), db.session.get(Feedback, 2)
        assert one.shot is None and one.message and two.shot           # the text stays (E6)
    assert not os.path.exists(os.path.join(_folder(app), first))


def test_sweep_removes_files_no_feedback_names(app, client):
    from app import feedback_shots

    _send(client, _png())
    folder = _folder(app)
    stray, fresh = os.path.join(folder, "A" * 16 + ".png"), os.path.join(folder, "B" * 16 + ".png")
    for path in (stray, fresh):
        Path(path).write_bytes(_png())
    old = time.time() - 2 * 3600
    os.utime(stray, (old, old))
    with app.app_context():
        assert feedback_shots.run() == 1
    assert not os.path.exists(stray) and os.path.exists(fresh)
    assert os.path.exists(os.path.join(folder, _item(app).shot))


def test_remove_button_deletes_the_file_at_once(app, client):
    _send(client, _png())
    name = _item(app).shot
    r = client.post("/feedback/1/shot/delete")
    assert r.status_code == 302
    item = _item(app)
    assert item.shot is None and item.shot_w is None and item.message == "Hier bricht es um"
    assert not os.path.exists(os.path.join(_folder(app), name))
    assert client.get("/feedback/1/shot").status_code == 404


def test_deleting_the_account_removes_its_images(app, client):
    from app import accounts

    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    other.post("/terms/", data={"accept": "1"})
    _send(other, _png())
    name = _item(app).shot
    assert os.path.exists(os.path.join(_folder(app), name))
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0001-5555-5555"))
        accounts.delete(user, "self")
        db.session.commit()
    item = _item(app)
    assert item.user_id is None and item.shot is None and item.message
    assert not os.path.exists(os.path.join(_folder(app), name))


def test_dialog_has_the_buttons_and_capture_is_allowed_only_for_researchers(app, client, survey):
    page = client.get("/dashboard/")
    html = page.data.decode()
    assert "display-capture=(self)" in page.headers["Permissions-Policy"]
    assert 'enctype="multipart/form-data"' in html and 'name="shot"' in html
    assert "Rückgängig" not in html and "Bild markieren" not in html    # the marking dialog is built on first use
    assert "Seite aufnehmen" in html and "Bild wählen" in html and "Bitte Angaben von Teilnehmenden abdecken." in html
    assert html.index("comment_shot.js") < html.index("feedback_shot.js")
    # the page of its own works without JavaScript: the plain file field is in the form
    own = client.get("/feedback/new").data.decode()
    assert own.count('name="shot"') == 2                               # the page's form and the header dialog's
    # participants: no capture, no feedback dialog
    from tests.conftest import add

    add(client, "info")
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        from app.models import Survey

        uid = db.session.get(Survey, 1).uid
    p = app.test_client().get(f"/s/{uid}", follow_redirects=True)
    assert "display-capture=()" in p.headers["Permissions-Policy"] and "feedback_shot.js" not in p.data.decode()


def test_editor_starts_with_cover_and_shares_the_comment_code():
    """E3 and no second copy of the image code: feedback_shot.js uses load, encode, edit and capture of
    comment_shot.js, as comments.js does."""
    shot = (ROOT / "app/static/feedback_shot.js").read_text(encoding="utf-8")
    assert 'CS.edit(buildEditor(sprite), base, marks, "cover")' in shot and "CS.load(" in shot and "CS.encode(" in shot
    assert "toBlob" not in shot and "getDisplayMedia" not in shot
    comments = (ROOT / "app/static/comments.js").read_text(encoding="utf-8")
    assert "toBlob" not in comments and "CS0.load" in comments
    assert 'aria-pressed", t[0] === "cover" ? "true"' in shot
