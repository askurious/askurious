"""Room for uploaded files (security audit 2026-10-13, DA-5): images, video and audio, paper scans and interview
recordings had no limit per survey or account, and only voice messages and recordings looked at the free space of the
disk. media.check_room now guards every such file: STORAGE_MIN_FREE_MB on the disk, SURVEY_STORAGE_MAX_MB per survey,
ACCOUNT_STORAGE_MAX_MB for all surveys a person runs as admin."""
import os
import shutil
from types import SimpleNamespace

import pytest

from app import media
from app.extensions import db
from app.models import Survey

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00"
       b"\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def _fill(app, uid, mb, name="big.bin"):
    folder = os.path.join(app.config["UPLOAD_FOLDER"], uid)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as f:
        f.write(b"\0" * int(mb * 1024 * 1024))


def _uid(app, sid=1):
    with app.app_context():
        return db.session.get(Survey, sid).uid


def test_a_survey_over_its_room_takes_no_image(app, client, survey):
    app.config.update(SURVEY_STORAGE_MAX_MB=1, ACCOUNT_STORAGE_MAX_MB=0)
    uid = _uid(app)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        assert media.save_bytes(s, PNG)
        _fill(app, uid, 1)
        with pytest.raises(ValueError, match="ihren Platz ausgeschöpft"):
            media.save_bytes(s, PNG)


def test_the_account_counts_all_its_surveys(app, client, survey):
    client.post("/surveys/new", data={"title": "Zwei", "languages": ["de"], "default_language": "de"})
    app.config.update(SURVEY_STORAGE_MAX_MB=0, ACCOUNT_STORAGE_MAX_MB=2)
    _fill(app, _uid(app, 2), 1.5)
    with client.application.test_request_context():
        from flask_login import login_user

        from app.models import User

        login_user(db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X")))
        media.check_room(db.session.get(Survey, 1), 100 * 1024)
        with pytest.raises(ValueError, match="Platz Ihres Kontos"):
            media.check_room(db.session.get(Survey, 1), 600 * 1024)


def test_a_nearly_full_disk_takes_no_file(app, client, survey, monkeypatch):
    app.config.update(STORAGE_MIN_FREE_MB=1000)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: SimpleNamespace(total=10 ** 12, used=0, free=900 * 1024 * 1024))
    with app.test_request_context():
        with pytest.raises(ValueError, match="kein Platz für weitere Dateien"):
            media.save_bytes(db.session.get(Survey, 1), PNG)


def test_paper_scans_are_checked_before_any_file_is_written(app, client, survey):
    from flask_login import login_user

    from app import paper
    from app.models import User

    app.config.update(SURVEY_STORAGE_MAX_MB=1)
    _fill(app, _uid(app), 1)
    upload = SimpleNamespace(read=lambda: b"%PDF-1.4 scan", filename="scan.pdf")
    with app.test_request_context():
        user = db.session.scalar(db.select(User))
        login_user(user)
        with pytest.raises(ValueError, match="ihren Platz ausgeschöpft"):
            paper.accept_upload(db.session.get(Survey, 1), [upload], user)
        assert not os.path.isdir(os.path.join(paper.root(), _uid(app), "inbox"))


def test_video_pieces_are_checked(app, client, survey):
    app.config.update(SURVEY_STORAGE_MAX_MB=1)
    _fill(app, _uid(app), 1)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        with pytest.raises(ValueError, match="ihren Platz ausgeschöpft"):
            media.add_media_part(s, "1-" + "0" * 16, "a" * 32, 0, b"x" * 10)
