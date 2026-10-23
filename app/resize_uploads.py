"""PF-11: migrate existing uploaded participant pictures to bounded WebP files.

python -m app.resize_uploads           dry run (counts, bytes; no image names or answers)
python -m app.resize_uploads --apply   write new files and update assets; originals remain for recovery
"""
import io
import os
import sys

from PIL import Image


def run(apply=False, report=print):
    from app import media
    from app.extensions import db
    from app.models import Asset, Item

    count = before = after = 0
    assets = db.session.scalars(db.select(Asset).join(Item).filter(
        Asset.source == "upload", Item.type.in_(("image", "image_mark"))).order_by(Asset.id))
    for asset in assets:
        survey = asset.item.survey
        path = os.path.join(media.upload_folder(survey.uid), asset.filename)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as source:
            original = source.read()
        with Image.open(io.BytesIO(original)) as image:
            if getattr(image, "is_animated", False) or (image.format == "WEBP" and max(image.size) <= 1600):
                continue
        converted = media.participant_upload(original)
        count, before, after = count + 1, before + len(original), after + len(converted)
        if apply:
            name = media.save_bytes(survey, converted)
            try:
                asset.filename = name
                db.session.commit()
            except Exception:
                db.session.rollback()
                media.delete(survey, name)
                raise
    report(f"{count} images: {before} -> {after} bytes" + ("" if apply else " (dry run)"))
    return count


if __name__ == "__main__":
    from app import create_app

    with create_app().app_context():
        run(apply="--apply" in sys.argv)
