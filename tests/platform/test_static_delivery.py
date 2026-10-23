"""Content hashes and protected nginx media delegation (SP13 PF-12/PF-13/PF-17e)."""
import hashlib
from pathlib import Path

from app import media
from app.extensions import db
from app.models import Survey


def test_version_must_match_the_bytes_before_long_caching(app, client):
    path = Path(app.static_folder) / "participant.js"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    good = client.get(f"/static/participant.js?v={digest}")
    bad = client.get("/static/participant.js?v=not-this-version")
    plain = client.get("/static/participant.js")
    assert good.status_code == 200 and good.cache_control.max_age == 31536000
    assert good.cache_control.immutable and not bad.cache_control.immutable and not plain.cache_control.immutable
    assert bad.cache_control.max_age == 0 and plain.cache_control.max_age is None


def test_media_delegates_only_existing_allowed_files(app, client, survey):
    with app.app_context():
        study = db.session.get(Survey, 1)
        folder = Path(app.config["UPLOAD_FOLDER"]) / study.uid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "video.mp4").write_bytes(b"abcdefghij")
        uid = study.uid
    app.config["MEDIA_X_ACCEL"] = True
    result = client.get(f"/media/{uid}/video.mp4")
    assert result.status_code == 200 and result.data == b""
    assert result.headers["X-Accel-Redirect"] == f"/_protected_media/{uid}/video.mp4"
    # nginx passes the app's Cache-Control on with the X-Accel redirect: private, a day, not immutable, so an image
    # withdrawn is gone from its address once deleted (audit 2026-10-18, INJ-3)
    cc = result.headers["Cache-Control"]
    assert "private" in cc and "public" not in cc and "immutable" not in cc
    assert result.cache_control.max_age == media.MEDIA_MAX_AGE == 86400
    assert client.get(f"/media/{uid}/missing.mp4").status_code == 404
    assert client.get(f"/media/{uid}/answers.json").status_code == 404
    assert client.get(f"/_protected_media/{uid}/video.mp4").status_code == 404


def test_public_pages_skip_participant_styles_and_handbook_images_reserve_space(app, client):
    html = client.get("/").get_data(as_text=True)
    assert "app.css" in html and "participant.css" not in html and "live.css" not in html
    html = client.get("/docs/build").get_data(as_text=True)
    import re

    images = re.findall(r"<img[^>]*static/handbook[^>]*>", html)
    assert images and all('width="' in image and 'height="' in image for image in images)
