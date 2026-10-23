"""W5: quota admission must cover the physical write across threads and processes, not just a preflight."""
import errno
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import media
from app.extensions import db
from app.models import Survey
from tests.editor.test_storage_room import PNG


@pytest.fixture(autouse=True)
def enable_accounting(app):
    app.config["STORAGE_USAGE_ENABLED"] = True


def test_parallel_images_cannot_share_the_last_room(app, client, survey, monkeypatch):
    app.config.update(SURVEY_STORAGE_MAX_MB=1, ACCOUNT_STORAGE_MAX_MB=0, STORAGE_MIN_FREE_MB=0)
    monkeypatch.setattr(media, "make_thumbnail", lambda *args: None)
    real = media.storage_bytes

    def measured(uid):
        size = real(uid)
        time.sleep(.1)  # recreates the gap between a successful check and the write
        return size

    monkeypatch.setattr(media, "storage_bytes", measured)
    start = threading.Barrier(2)

    def upload():
        with app.app_context():
            s = db.session.get(Survey, 1)
            start.wait(timeout=10)
            try:
                media.save_bytes(s, PNG + b"x" * 600_000)
                return "stored"
            except ValueError:
                return "full"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: upload(), range(2)))
    assert sorted(results) == ["full", "stored"]
    with app.app_context():
        assert real(db.session.get(Survey, 1).uid) < media.MB


def test_separate_processes_share_the_admission_lock(tmp_path):
    program = '''
import os, sys, time
from flask import Flask
from types import SimpleNamespace
from app import media
app = Flask(__name__)
app.config.update(UPLOAD_FOLDER=sys.argv[1], STORAGE_USAGE_ENABLED=True, SURVEY_STORAGE_MAX_MB=1,
                  ACCOUNT_STORAGE_MAX_MB=0, STORAGE_MIN_FREE_MB=0)
media.make_thumbnail = lambda *args: None
real = media.storage_bytes
def count(uid):
    size = real(uid)
    time.sleep(.4)
    return size
media.storage_bytes = count
with app.app_context():
    try:
        media.save_bytes(SimpleNamespace(uid='shared'), b'\\x89PNG\\r\\n\\x1a\\n' + b'x' * 600000)
        print('stored')
    except ValueError:
        print('full')
'''
    processes = [subprocess.Popen([sys.executable, "-c", program, str(tmp_path / "uploads")],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    results = []
    for process in processes:
        out, err = process.communicate(timeout=45)
        assert process.returncode == 0, err
        results.append(out.strip())
    assert sorted(results) == ["full", "stored"]


@pytest.mark.parametrize("code", [None, errno.ENOSPC, errno.EDQUOT])
def test_failed_write_releases_admission_and_removes_partial_file(app, client, survey, monkeypatch, code):
    import builtins

    app.config.update(SURVEY_STORAGE_MAX_MB=1, ACCOUNT_STORAGE_MAX_MB=0, STORAGE_MIN_FREE_MB=0)
    real = builtins.open

    class Broken:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def write(self, data):
            raise OSError(code, "disk full") if code is not None else OSError("disk full")

    def broken(path, mode="r", *args, **kwargs):
        if mode == "xb":
            with real(path, "wb") as output:
                output.write(b"partial")
            return Broken()
        return real(path, mode, *args, **kwargs)

    with app.app_context():
        s = db.session.get(Survey, 1)
        with monkeypatch.context() as patch:
            patch.setattr(builtins, "open", broken)
            error = media.StorageFull if code is not None else OSError
            with pytest.raises(error):
                media.save_bytes(s, PNG)
        assert media.storage_bytes(s.uid) == 0
        assert media.save_bytes(s, PNG)


def test_each_media_piece_checks_the_total_quota(app, client, survey):
    from app.items.stimulus import CHUNK_BYTES

    app.config.update(SURVEY_STORAGE_MAX_MB=10, ACCOUNT_STORAGE_MAX_MB=0, STORAGE_MIN_FREE_MB=0)
    with app.app_context():
        s = db.session.get(Survey, 1)
        slot, token = "1-" + "0" * 16, "a" * 32
        media.add_media_part(s, slot, token, 0, b"x" * CHUNK_BYTES)
        folder = media.upload_folder(s.uid)
        with open(os.path.join(folder, "other.bin"), "wb") as output:
            output.write(b"x" * (10 * media.MB - CHUNK_BYTES))
        with pytest.raises(ValueError, match="ihren Platz"):
            media.add_media_part(s, slot, token, 1, b"x")


def test_nested_admission_and_svg_are_guarded(app, client, survey):
    app.config.update(SURVEY_STORAGE_MAX_MB=1, ACCOUNT_STORAGE_MAX_MB=0, STORAGE_MIN_FREE_MB=0)
    with app.app_context():
        s = db.session.get(Survey, 1)
        with media.writing(s, len(PNG)):
            assert media.save_bytes(s, PNG)
        folder = media.upload_folder(s.uid)
        with open(os.path.join(folder, "full.bin"), "wb") as output:
            output.write(b"x" * media.MB)
        with pytest.raises(ValueError, match="ihren Platz"):
            media.save_logo(s, b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>')


def test_unattended_upload_counts_the_admins_other_surveys(app, client, survey):
    client.post("/surveys/new", data={"title": "Two", "languages": ["de"], "default_language": "de"})
    app.config.update(SURVEY_STORAGE_MAX_MB=0, ACCOUNT_STORAGE_MAX_MB=1, STORAGE_MIN_FREE_MB=0)
    with app.app_context():
        other = db.session.get(Survey, 2)
        os.makedirs(media.upload_folder(other.uid), exist_ok=True)
        with open(os.path.join(media.upload_folder(other.uid), "full.bin"), "wb") as output:
            output.write(b"x" * media.MB)
        with pytest.raises(ValueError, match="Platz Ihres Kontos"):
            media.save_bytes(db.session.get(Survey, 1), PNG)


def test_parallel_surveys_cannot_share_the_last_account_room(app, client, survey, monkeypatch):
    client.post("/surveys/new", data={"title": "Two", "languages": ["de"], "default_language": "de"})
    app.config.update(SURVEY_STORAGE_MAX_MB=0, ACCOUNT_STORAGE_MAX_MB=1, STORAGE_MIN_FREE_MB=0)
    monkeypatch.setattr(media, "make_thumbnail", lambda *args: None)
    start = threading.Barrier(2)

    def upload(sid):
        with app.app_context():
            s = db.session.get(Survey, sid)
            start.wait(timeout=10)
            try:
                media.save_bytes(s, PNG + b"x" * 600_000)
                return "stored"
            except ValueError:
                return "full"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(upload, (1, 2))) == ["full", "stored"]


def test_copy_and_derived_scan_cannot_bypass_the_room(app, client, survey, tmp_path):
    from PIL import Image

    from app import paper

    app.config.update(SURVEY_STORAGE_MAX_MB=1, ACCOUNT_STORAGE_MAX_MB=0, STORAGE_MIN_FREE_MB=0)
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder = media.upload_folder(s.uid)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "full.bin"), "wb") as output:
            output.write(b"x" * media.MB)
        source = tmp_path / "copy.png"
        source.write_bytes(PNG)
        target = os.path.join(folder, "copied.png")
        with pytest.raises(ValueError):
            media.copy_file(str(source), target, s.uid)
        assert not os.path.exists(target)
        with pytest.raises(ValueError):
            paper._save_image(s, Image.new("RGB", (2, 2)))


def test_unhandled_quota_error_is_clear_and_not_cached(app, client, survey, monkeypatch):
    from app.storage_room import StorageFull

    def full():
        raise StorageFull("No room")

    monkeypatch.setitem(app.view_functions, "dashboard.index", full)
    response = client.get("/dashboard/")
    assert response.status_code == 507 and response.data == b"No room"
    assert response.headers["Cache-Control"] == "no-store"


def test_media_finalization_cannot_move_files_during_admission(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        slot, token = "1-" + "0" * 16, "a" * 32
        payload = b"\x00\x00\x00\x18ftypmp42" + b"x" * 100
        media.add_media_part(s, slot, token, 0, payload)
        started = threading.Event()

        def finish():
            with app.app_context():
                other = db.session.get(Survey, 1)
                started.set()
                return media.finish_media(other, slot, token)

        with ThreadPoolExecutor(1) as pool:
            with media.writing(s, 0, check=False):
                result = pool.submit(finish)
                assert started.wait(timeout=10)
                time.sleep(.1)
                assert not result.done()
                assert os.path.isfile(os.path.join(media.upload_folder(s.uid), ".parts", slot + "." + token))
            filename, mime, size = result.result(timeout=10)
        assert mime == "video/mp4" and size == len(payload)
        assert os.path.getsize(os.path.join(media.upload_folder(s.uid), filename)) == len(payload)
