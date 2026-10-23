"""Generated images as WebP at quality 80 (Philipp, 2026-10-08): stored so, older JPEGs converted once
(app/convert_webp.py), the Zenodo package with JPEG again."""
import io
import os

from PIL import Image

from app.extensions import db
from app.models import Asset, Item


def _jpeg(size=(1024, 1024)):
    out = io.BytesIO()
    Image.new("RGB", size, (120, 160, 200)).save(out, format="JPEG", quality=85)
    return out.getvalue()


def test_generated_images_are_stored_as_webp_at_their_resolution(app):
    from app import media

    png = io.BytesIO()
    Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(png, format="PNG")
    out = media.optimize(png.getvalue())
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP" and img.size == (1024, 1024) and img.getpixel((5, 5)) == (255, 255, 255)
    assert media._sniff(out) == "webp"


def test_old_jpegs_are_converted_once_and_zenodo_gets_jpeg(app, client, survey):
    from app import convert_webp, media, zenodo

    with app.app_context():
        s = db.session.get(Item, 1).block.survey
        name = media.save_bytes(s, _jpeg())
        db.session.add(Asset(item_id=1, filename=name, source="ai", status=Asset.APPROVED))
        db.session.add(Asset(item_id=1, filename=media.save_bytes(s, _jpeg((300, 200))), source="upload"))
        db.session.commit()
        lines = []
        assert convert_webp.run(report=lines.append) == 1 and "not applied" in lines[0]
        assert convert_webp.run(apply=True, report=lines.append) == 1
        asset = db.session.scalar(db.select(Asset).filter_by(source="ai"))
        assert asset.filename.endswith(".webp") and not media.exists(s, name) and media.exists(s, asset.filename)
        assert db.session.scalar(db.select(Asset).filter_by(source="upload")).filename.endswith(".jpg")  # stays
        assert convert_webp.run(apply=True, report=lines.append) == 0                                    # once
        packed = zenodo.images(s)[0][1]
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(packed)).namelist()
    assert any(n.endswith(".jpg") for n in names) and not any(n.endswith(".webp") for n in names)
    assert os.path.splitext(names[0])[1] in (".jpg", ".csv")
