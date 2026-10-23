"""Persistent storage accounting retains physical quotas without repeated tree walks."""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import perf_counter

import pytest

from app import media, storage_room, storage_usage
from app.extensions import db
from app.models import Survey
from tests.editor.test_storage_room import PNG


@pytest.fixture(autouse=True)
def enable_accounting(app):
    app.config.update(STORAGE_USAGE_ENABLED=True, SURVEY_STORAGE_MAX_MB=1,
                      ACCOUNT_STORAGE_MAX_MB=0, STORAGE_MIN_FREE_MB=0)


def test_known_appends_replacements_deletions_and_moves_need_no_history_walk(app, client, survey, monkeypatch):
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder = Path(media.upload_folder(s.uid))
        folder.mkdir(parents=True)
        path = folder / "sample.bin"
        with media.writing(s, 100):
            path.write_bytes(b"x" * 100)

        def no_walk(*args, **kwargs):
            raise AssertionError("repeat quota check traversed the entire tree")
        monkeypatch.setattr(os, "walk", no_walk)
        with media.writing(s, 25), path.open("ab") as output:
            output.write(b"x" * 25)
        with media.writing(s, 0, check=False):
            assert media.storage_bytes(s.uid) == 125
            path.write_bytes(b"x" * 10)
        with media.writing(s, 0, check=False):
            assert media.storage_bytes(s.uid) == 10
            os.replace(path, folder / "new.bin")
        storage_room.remove_file(folder / "new.bin")
        with media.writing(s, 0, check=False):
            assert media.storage_bytes(s.uid) == 0


def test_parent_components_in_upload_configuration_keep_the_real_quota(app, client, survey, monkeypatch):
    monkeypatch.setattr(media, "make_thumbnail", lambda *args: None)
    original = Path(app.config["UPLOAD_FOLDER"])
    app.config["UPLOAD_FOLDER"] = str(original.parent / "alias" / ".." / original.name)
    with app.app_context():
        s = db.session.get(Survey, 1)
        media.save_bytes(s, PNG + b"x" * 600_000)
        with pytest.raises(ValueError):
            media.save_bytes(s, PNG + b"x" * 600_000)


def test_untracked_open_file_never_returns_to_a_stale_counter(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder = Path(media.upload_folder(s.uid))
        folder.mkdir(parents=True)
        with media.writing(s, 0):
            assert media.storage_bytes(s.uid) == 0
        with (folder / "untracked.bin").open("wb") as output:
            output.write(b"x" * 600_000)
            output.flush()
            with media.writing(s, 0):
                assert media.storage_bytes(s.uid) == 600_000
            # No second open/audit event, and a reconciliation must not clear quarantine.
            output.write(b"x" * 400_000)
            output.flush()
            with storage_usage._database() as connection:
                connection.execute("UPDATE usage SET reconciled=0")
            assert storage_usage.reconcile() == 1
            assert storage_usage._marker(s.uid).exists()
            output.write(b"x" * 20_000)
            output.flush()
            with media.writing(s, 0, check=False):
                assert media.storage_bytes(s.uid) == 1_020_000
            with pytest.raises(ValueError):
                media.save_bytes(s, PNG + b"x" * 60_000)


def test_nested_reservations_cannot_share_unwritten_room(app, client, survey, monkeypatch):
    monkeypatch.setattr(media, "make_thumbnail", lambda *args: None)
    with app.app_context():
        s = db.session.get(Survey, 1)
        with media.writing(s, 600_000), pytest.raises(ValueError):
            media.save_bytes(s, PNG + b"x" * 600_000)
        assert media.save_bytes(s, PNG + b"x" * 600_000)


def test_nested_account_reservations_cover_different_surveys(app, client, survey, monkeypatch):
    monkeypatch.setattr(media, "make_thumbnail", lambda *args: None)
    client.post("/surveys/new", data={"title": "Two", "languages": ["de"], "default_language": "de"})
    app.config.update(SURVEY_STORAGE_MAX_MB=0, ACCOUNT_STORAGE_MAX_MB=1)
    with app.app_context():
        first, second = db.session.get(Survey, 1), db.session.get(Survey, 2)
        with media.writing(first, 600_000), pytest.raises(ValueError):
            media.save_bytes(second, PNG + b"x" * 600_000)


def test_deletion_does_not_increase_a_parent_reservation(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder = Path(media.upload_folder(s.uid))
        folder.mkdir(parents=True)
        path = folder / "old.bin"
        with media.writing(s, 100_000):
            path.write_bytes(b"x" * 100_000)
        with media.writing(s, 100_000):
            path.unlink()
            with media.writing(s, 0, check=False):
                assert storage_usage.pending({s.uid}) == 100_000


def test_crashed_process_rebuilds_partial_bytes_and_releases_reservation(tmp_path):
    program = '''
import os, sys
from types import SimpleNamespace
from flask import Flask
from app import media
app = Flask(__name__)
app.config.update(UPLOAD_FOLDER=sys.argv[1], STORAGE_USAGE_ENABLED=True,
                  SURVEY_STORAGE_MAX_MB=1, ACCOUNT_STORAGE_MAX_MB=0, STORAGE_MIN_FREE_MB=0)
media.make_thumbnail = lambda *args: None
with app.app_context():
    survey = SimpleNamespace(uid='crashed')
    if sys.argv[2] == 'crash':
        with media.writing(survey, 900000):
            os.makedirs(media.upload_folder(survey.uid), exist_ok=True)
            with open(os.path.join(media.upload_folder(survey.uid), 'partial.bin'), 'wb') as f:
                f.write(b'x' * 600000)
                f.flush()
                os._exit(17)
    else:
        try:
            media.save_bytes(survey, b'\\x89PNG\\r\\n\\x1a\\n' + b'x' * int(sys.argv[2]))
            print('stored')
        except ValueError:
            print('full')
'''
    def run(mode):
        return subprocess.run([sys.executable, "-c", program, str(tmp_path / "uploads"), mode],
                              capture_output=True, text=True, timeout=45, check=False)
    assert run("crash").returncode == 17
    refused = run("600000")
    assert refused.returncode == 0 and refused.stdout.strip() == "full", refused.stderr
    accepted = run("100000")
    assert accepted.returncode == 0 and accepted.stdout.strip() == "stored", accepted.stderr


def test_large_inventory_is_walked_once_then_queries_are_bounded(app, client, survey, monkeypatch):
    app.config["SURVEY_STORAGE_MAX_MB"] = 100
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder = Path(media.upload_folder(s.uid))
        folder.mkdir(parents=True)
        with media.writing(s, 0, check=False):
            for n in range(5000):
                (folder / f"file-{n}.bin").write_bytes(b"x" * 100)
        began = perf_counter()
        for _ in range(5):
            media._physical_storage_bytes(s.uid)
        physical = (perf_counter() - began) * 200
        calls = []
        real = os.walk
        monkeypatch.setattr(os, "walk", lambda *args, **kwargs: calls.append(args) or real(*args, **kwargs))
        began = perf_counter()
        for _ in range(5):
            with media.writing(s, 100):
                assert media.storage_bytes(s.uid) == 500_000
        cached = (perf_counter() - began) * 200
        assert calls == []
        print(f"5000 files, mean of 5 checks: physical={physical:.2f} ms; admission+ledger={cached:.2f} ms")


def test_background_scan_does_not_block_writers_or_publish_an_obsolete_snapshot(app, client, survey, monkeypatch):
    from types import SimpleNamespace

    paused, resume = Event(), Event()
    with app.app_context():
        s = db.session.get(Survey, 1)
        uid = s.uid
        folder = Path(media.upload_folder(uid))
        folder.mkdir(parents=True)
        path = folder / "value.bin"
        with media.writing(s, 100):
            path.write_bytes(b"x" * 100)
        with storage_usage._database() as connection:
            connection.execute("UPDATE usage SET reconciled=0")
    real = storage_usage._files
    def slow_scan(path):
        rows = list(real(path))
        if path == folder:
            paused.set()
            assert resume.wait(timeout=10)
        yield from rows
    monkeypatch.setattr(storage_usage, "_files", slow_scan)
    def scan():
        with app.app_context():
            return storage_usage.reconcile()
    with ThreadPoolExecutor(1) as pool:
        job = pool.submit(scan)
        assert paused.wait(timeout=10)
        try:
            with app.app_context(), media.writing(SimpleNamespace(uid=uid), 100):
                path.write_bytes(b"x" * 200)
        finally:
            resume.set()
        assert job.result(timeout=10) == 0
    with app.app_context(), media.writing(SimpleNamespace(uid=uid), 0, check=False):
        assert media.storage_bytes(uid) == 200


def test_corrupt_inventory_falls_back_to_physical_quota_counting(app, client, survey, monkeypatch):
    monkeypatch.setattr(media, "make_thumbnail", lambda *args: None)
    with app.app_context():
        s = db.session.get(Survey, 1)
        media.save_bytes(s, PNG + b"x" * 600_000)
        (storage_usage._meta() / "usage.sqlite3").write_bytes(b"invalid sqlite database")
        with pytest.raises(ValueError):
            media.save_bytes(s, PNG + b"x" * 600_000)
        assert storage_usage._disabled()
        assert not (storage_usage._meta() / "usage.sqlite3").exists()
        assert media.save_bytes(s, PNG)


def test_restarted_writer_rebuilds_restored_inventory(app, client, survey, monkeypatch):
    monkeypatch.setattr(media, "make_thumbnail", lambda *args: None)
    with app.app_context():
        s = db.session.get(Survey, 1)
        media.save_bytes(s, PNG + b"x" * 600_000)
        with storage_usage._database() as connection:
            connection.execute("UPDATE usage SET total=0")
        storage_usage._ready.discard((str(storage_usage._root()), os.getpid(), s.uid))
        with pytest.raises(ValueError):
            media.save_bytes(s, PNG + b"x" * 600_000)
