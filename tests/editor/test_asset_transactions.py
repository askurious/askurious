import json
import pytest
from pathlib import Path
from app.extensions import db
from app.models import Survey, Item, Asset

@pytest.mark.parametrize("rollback", [True, False])
def test_asset_replacement_files_follow_transaction(app, client, survey, rollback):
    from app.editor.images import _replace_static_asset
    from app import media
    from tests.conftest import add
    add(client, "image")
    with app.app_context():
        s = db.session.get(Survey, survey)
        item = db.session.get(Item, 2)
        folder = Path(media.upload_folder(s.uid))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "original.png").write_bytes(b"original stimulus")
        (folder / "replacement.png").write_bytes(b"replacement stimulus")
        item.assets.append(Asset(filename="original.png", key="", source="upload", status=Asset.APPROVED))
        db.session.commit()
        _replace_static_asset(s, item, Asset(filename="replacement.png", key="", source="upload", status=Asset.APPROVED))
        db.session.flush()
        db.session.rollback() if rollback else db.session.commit()
        db.session.expire_all()
        restored = [a.filename for a in db.session.get(Item, 2).assets]
        print("FILE_ROLLBACK_REPRO", json.dumps({"asset_rows_after_rollback": restored, "original_exists": (folder / "original.png").exists(), "replacement_exists": (folder / "replacement.png").exists()}))
        assert restored == (["original.png"] if rollback else ["replacement.png"])
        assert (folder / "original.png").exists() == rollback
        assert (folder / "replacement.png").exists() != rollback



def test_failed_upload_commit_keeps_original_file_and_removes_new_file(app, client, survey, monkeypatch):
    import io
    from PIL import Image
    from app import media
    from tests.conftest import add
    add(client, "image")
    with app.app_context():
        s = db.session.get(Survey, survey)
        folder = Path(media.upload_folder(s.uid))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "original.png").write_bytes(b"original")
        item = db.session.get(Item, 2)
        item.assets.append(Asset(filename="original.png", key="", source="upload", status=Asset.APPROVED))
        db.session.commit()
    data = io.BytesIO()
    Image.new("RGB", (2,2)).save(data, format="PNG")
    data.seek(0)
    def fail():
        raise RuntimeError("simulated database failure")
    with monkeypatch.context() as m:
        m.setattr(db.session, "commit", fail)
        with pytest.raises(RuntimeError, match="simulated"):
            client.post('/surveys/1/editor/items/2/upload', data={'file':(data, 'new.png')})
    assert sorted(p.name for p in folder.iterdir() if not p.name.endswith(".thumbnail.claim")) == ["original.png"]
    with app.app_context():
        assert [a.filename for a in db.session.get(Item, 2).assets] == ["original.png"]
