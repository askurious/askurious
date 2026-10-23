"""Small tiles reserve their space and use bounded thumbnails without changing originals (SP13 PF-17c)."""
import io
from pathlib import Path

from PIL import Image

from app import media
from app.extensions import db
from app.models import Survey


def png():
    output = io.BytesIO()
    Image.new("RGBA", (1200, 600), (10, 30, 50, 120)).save(output, format="PNG")
    return output.getvalue()


def test_transparency_original_and_thumbnail_erasure(app, client, survey):
    with app.app_context():
        study = db.session.get(Survey, 1)
        original = png()
        name = media.save_bytes(study, original)
        small = media.thumbnail_name(study.uid, name)
        assert small != name
        folder = Path(app.config["UPLOAD_FOLDER"]) / study.uid
        assert (folder / name).read_bytes() == original
        with Image.open(folder / small) as image:
            assert image.size == (400, 200) and image.mode == "RGBA"
            assert image.getpixel((0, 0))[3] == 120
        media.delete_after_commit(study, name)
        db.session.rollback()
        assert (folder / small).exists()
        media.delete_after_commit(study, name)
        db.session.commit()
        assert not (folder / name).exists() and not (folder / small).exists()


def test_optional_thumbnail_cannot_reject_a_valid_upload(app, client, survey, monkeypatch):
    with app.app_context():
        study = db.session.get(Survey, 1)
        monkeypatch.setattr(media, "check_room", lambda *a: (_ for _ in ()).throw(ValueError("full")))
        name = media.save_bytes(study, png(), check=False)
        assert media.thumbnail_name(study.uid, name) == name
        assert media.exists(study, name)
