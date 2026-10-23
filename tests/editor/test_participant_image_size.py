"""Mobile image transfer and transparency (PF-11, audit 2026-10-13)."""
import io

from PIL import Image

from app import media


def test_uploaded_still_is_webp_bounded_and_preserves_alpha():
    image = Image.new("RGBA", (4000, 3000), (10, 20, 30, 100))
    source = io.BytesIO()
    image.save(source, "PNG")
    with Image.open(io.BytesIO(media.participant_upload(source.getvalue()))) as result:
        assert result.format == "WEBP" and result.size == (1600, 1200)
        assert result.getpixel((0, 0))[3] == 100


def test_animation_is_not_silently_reduced_to_its_first_frame():
    frames = [Image.new("RGB", (30, 20), color) for color in ("red", "blue")]
    source = io.BytesIO()
    frames[0].save(source, "GIF", save_all=True, append_images=frames[1:], duration=200, loop=0)
    with Image.open(io.BytesIO(media.participant_upload(source.getvalue()))) as result:
        assert result.n_frames == 2 and result.info["duration"] == 200


def test_existing_upload_conversion_is_previewable_and_keeps_the_original(app, client, survey):
    import os

    from app import resize_uploads
    from app.extensions import db
    from app.models import Asset, Item
    from tests.conftest import add

    add(client, "image")
    source = io.BytesIO()
    Image.new("RGB", (2000, 1000), "red").save(source, "PNG")
    with app.app_context():
        item = db.session.get(Item, 2)
        old = media.save_bytes(item.survey, source.getvalue())
        item.config = {**item.config, "source": "upload"}
        asset = Asset(item_id=item.id, key="", filename=old, source="upload", status=Asset.APPROVED)
        item.assets.append(asset)
        db.session.commit()
        assert resize_uploads.run(report=lambda text: None) == 1 and asset.filename == old
        assert resize_uploads.run(apply=True, report=lambda text: None) == 1
        assert asset.filename != old and media.size(item.survey.uid, asset.filename) == (1600, 800)
        assert os.path.isfile(os.path.join(media.upload_folder(item.survey.uid), old))
        assert resize_uploads.run(apply=True, report=lambda text: None) == 0
    page = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    assert 'width="1600" height="800"' in page and 'decoding="async"' in page
