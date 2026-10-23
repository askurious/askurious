"""Bounded maintenance command for thumbnails of images uploaded before PF-17c."""
import argparse
import os
from pathlib import Path

from app import create_app, media
from app.extensions import db
from app.models import Survey


def backfill(limit=500):
    made = seen = 0
    for study in db.session.scalars(db.select(Survey).order_by(Survey.id)).yield_per(20):
        folder = Path(media.upload_folder(study.uid))
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.stem.endswith("-400") or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            if (folder / (path.stem + "-400.webp")).is_file():
                continue
            width, height = media.size(study.uid, path.name)
            if not width or max(width, height) <= 400:
                continue
            seen += 1
            if media.make_thumbnail(study, path.name):
                made += 1
            if seen >= limit:
                return seen, made
    return seen, made


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("limit must be positive")
    if hasattr(os, "nice"):
        os.nice(10)
    app = create_app()
    with app.app_context():
        seen, made = backfill(args.limit)
    print(f"Checked {seen} images; created {made} thumbnails.")


if __name__ == "__main__":
    main()
