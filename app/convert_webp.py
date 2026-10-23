"""Generated images stored before 2026-10-08 as JPEG: once to WebP at quality 80, at the same resolution
(media.optimize; Philipp 2026-10-08). Uploaded images stay as they were uploaded.

    python -m app.convert_webp            show what would change
    python -m app.convert_webp --apply    convert

Each image gets a new file name (so no browser keeps the old one from its cache); the asset points to it once
the database has it, then the old file goes. Run again: nothing left to do."""
import os
import sys


def run(apply=False, report=print):
    from app import media
    from app.extensions import db
    from app.models import Asset

    done = before = after = 0
    for asset in db.session.scalars(db.select(Asset).filter_by(source="ai").order_by(Asset.id)):
        if os.path.splitext(asset.filename)[1].lower() not in (".jpg", ".jpeg", ".png"):
            continue
        survey = asset.item.block.survey
        path = os.path.join(media.upload_folder(survey.uid), asset.filename)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        new = media.optimize(data)
        before, after, done = before + len(data), after + len(new), done + 1
        if apply:
            old = asset.filename
            asset.filename = media.save_bytes(survey, new, check=False)  # smaller than the old one
            db.session.commit()
            media.delete(survey, old)
    report(f"{done} images, {before // 1024} KB -> {after // 1024} KB" + ("" if apply else " (not applied)"))
    return done


if __name__ == "__main__":
    from app import create_app

    app = create_app()
    with app.app_context():
        run(apply="--apply" in sys.argv)
