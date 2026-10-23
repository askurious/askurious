"""W4: stream source files, bound inline JSON and use the shared expensive-work budget."""
import os
import tracemalloc
import zipfile

import pytest

from app import heavy, media, survey_files, transfer
from app.extensions import db
from app.models import Asset, Survey
from tests.editor.test_duplicate import build


def test_large_image_zip_is_bounded_and_inline_download_falls_back(app, client, survey):
    build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        asset = db.session.scalar(db.select(Asset).filter_by(item_id=5))
        path = os.path.join(media.upload_folder(s.uid), asset.filename)
        with open(path, "wb") as output:
            output.writelines(b"x" * 1024 * 1024 for _ in range(64))
        # Finish ORM preloading before measuring the archive's additional memory.
        paths = {}
        transfer.dump(s, raw=paths)
        assert paths[asset.filename] == path
        tracemalloc.start()
        try:
            result = survey_files.write([s])
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        try:
            assert peak < 48 * 1024 * 1024, peak
            with zipfile.ZipFile(result) as archive:
                info = archive.getinfo("files/" + asset.filename)
                assert info.file_size == 64 * 1024 * 1024
                with archive.open(info) as source:
                    total = 0
                    while chunk := source.read(65536):
                        assert chunk == b"x" * len(chunk)
                        total += len(chunk)
                    assert total == info.file_size
        finally:
            result.close()
    response = client.get("/surveys/1/export/survey.json")
    assert response.status_code == 302 and "/export/survey.zip" in response.location
    assert client.get("/surveys/1/export/survey.json?files=0").status_code == 200


def test_structure_and_zip_budgets_are_enforced_and_spools_close(app, client, survey, monkeypatch):
    with app.app_context():
        s = db.session.get(Survey, 1)
        app.config["EXPORT_JSON_MAX_BYTES"] = 10
        with pytest.raises(transfer.ExportTooLarge):
            transfer.dumps(s, with_files=False)
        app.config["EXPORT_JSON_MAX_BYTES"] = 32 * 1024 * 1024
        app.config["EXPORT_ZIP_MAX_BYTES"] = 10
        handles = []
        real = survey_files.tempfile.SpooledTemporaryFile

        def opened(*args, **kwargs):
            handle = real(*args, **kwargs)
            handles.append(handle)
            return handle

        monkeypatch.setattr(survey_files.tempfile, "SpooledTemporaryFile", opened)
        with pytest.raises(transfer.ExportTooLarge):
            survey_files.write([s])
        assert handles and all(handle.closed for handle in handles)
    assert client.get("/surveys/1/export/survey.zip").status_code == 413


def test_large_snapshot_keeps_images_and_checksum_without_inline_json(app, client, survey):
    import hashlib
    import json
    from pathlib import Path

    from app import prereg

    build(app, client)
    app.config["EXPORT_JSON_MAX_BYTES"] = 1024 * 1024
    with app.app_context():
        s = db.session.get(Survey, 1)
        asset = db.session.scalar(db.select(Asset).filter_by(item_id=5))
        path = Path(media.upload_folder(s.uid)) / asset.filename
        path.write_bytes(b"x" * (2 * 1024 * 1024))
        name = prereg.freeze(s)
        assert name
        frozen = Path(prereg.path(s, name))
        assert frozen.with_suffix(".sha256").read_text().split()[0] == survey_files.content_hash(frozen)
        with zipfile.ZipFile(frozen) as archive:
            definition = json.loads(archive.read("survey.json"))
            assert definition["files"] == {}
            assert archive.read("files/" + asset.filename) == path.read_bytes()
            for line in archive.read("checksums.sha256").decode().splitlines():
                checksum, member = line.split("  ")
                assert hashlib.sha256(archive.read(member)).hexdigest() == checksum


def test_word_picture_budget_skips_oversize_before_decoding(app, client, survey, monkeypatch):
    from types import SimpleNamespace

    from app import questionnaire_docx
    from app.models import Item

    build(app, client)
    with app.app_context():
        item = db.session.get(Item, 5)
        asset = db.session.scalar(db.select(Asset).filter_by(item_id=5))
        asset.status = Asset.APPROVED
        with open(os.path.join(media.upload_folder(item.block.survey.uid), asset.filename), "wb") as output:
            output.write(b"x" * 4096)
        app.config["EXPORT_JSON_MAX_BYTES"] = 1024
        monkeypatch.setattr(media, "open_image", lambda *args, **kwargs: pytest.fail("oversized picture was decoded"))
        ctx = SimpleNamespace(say=lambda de, en: en)
        with pytest.raises(transfer.ExportTooLarge):
            questionnaire_docx._picture(ctx, item)


@pytest.mark.parametrize("url", ["/surveys/1/export/survey.json", "/surveys/1/export/survey.zip",
                                  "/surveys/1/export/comments.csv", "/surveys/1/export/comments.zip",
                                  "/surveys/1/export/voice-messages.zip", "/surveys/1/export/questionnaire.docx",
                                  "/dashboard/export?download=1"])
def test_expensive_exports_obey_shared_capacity(app, client, survey, monkeypatch, url):
    monkeypatch.setattr(heavy, "hold", lambda *args, **kwargs: False)
    response = client.get(url)
    assert response.status_code == 503 and response.headers["Retry-After"] == "30"
