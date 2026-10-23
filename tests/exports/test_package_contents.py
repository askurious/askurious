"""What the packages of a whole survey hold since the concept "Vereinheitlichung der Befragungsexporte" (E3, E4, E8):
the survey file in the Zenodo/Coscine package and in the snapshot before an edit, long formats under one name, and
video and audio files in the ZIP of a survey file."""
import io
import json
import os
import zipfile

from app import packages, snapshots, transfer, zenodo
from app.extensions import db
from app.media import upload_folder
from app.models import Asset, Item, Survey
from tests.conftest import add, fill_consent
from tests.exports.test_coscine_media import MP4
from tests.exports.test_transfer import _upload


def test_the_deposit_holds_the_public_survey_file(app, client, survey):
    fill_consent(client)
    add(client, "likert")
    with app.app_context():
        s = db.session.get(Survey, 1)
        files = dict(zenodo.package(s))
        data = transfer.loads(files["survey.json"])
        assert data["survey"]["title"] == s.title and data["files"] == {}
        assert "`survey.json`" in files["README.md"].decode()


def test_snapshot_before_an_edit_holds_the_survey_file_and_no_loose_look(app, client, survey):
    fill_consent(client)
    add(client, "likert")
    with app.app_context():
        s = db.session.get(Survey, 1)
        name = snapshots.save(s)
        inside = set(os.listdir(os.path.join(snapshots.folder(s), name)))
        assert {"data.csv", "codebook.csv", "questionnaire.md", packages.SURVEY} <= inside
        assert "theme.json" not in inside and packages.CHECKSUMS not in inside
        assert transfer.loads(open(snapshots.path(s, name, packages.SURVEY), "rb").read())["blocks"]
    page = client.get("/surveys/1/data").data.decode()
    assert f"/export/snapshot/{name}/survey.json" in page


def test_data_files_of_a_snapshot_under_old_and_new_names():
    for name in ("data.csv", "long_cbc.csv", "long_v1_loop.csv", "conjoint_cbc_long.csv", "vignettes_v1_long.csv",
                 "loop_l1_long.csv"):
        assert snapshots.is_data_file(name), name
    for name in ("codebook.csv", "questionnaire.md", "survey.json", "design_cbc.json", "x_table.csv"):
        assert not snapshots.is_data_file(name), name
    assert snapshots.EXTRA_RE.fullmatch("conjoint_cbc_long.csv") and snapshots.EXTRA_RE.fullmatch("long_cbc.csv")


def _with_video(app, client):
    fill_consent(client)
    add(client, "media")
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.scalar(db.select(Item).filter_by(type="media"))
        os.makedirs(upload_folder(s.uid), exist_ok=True)
        with open(os.path.join(upload_folder(s.uid), "clip.mp4"), "wb") as f:
            f.write(MP4)
        item.assets.append(Asset(key="", filename="clip.mp4", source="upload", status=Asset.APPROVED))
        item.config = {**item.config, "files": {"": {"kind": "video", "mime": "video/mp4", "bytes": len(MP4),
                                                     "seconds": 12.5, "name": "spot.mp4"}}}
        db.session.commit()


def test_video_goes_into_the_zip_and_comes_back(app, client, survey):
    _with_video(app, client)
    body = client.get("/surveys/1/export/survey.zip").data
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        assert z.read("files/clip.mp4") == MP4
        data = json.loads(z.read(packages.SURVEY))
    [clip] = next(i for b in data["blocks"] for i in b["items"] if i["type"] == "media")["media"]
    assert clip == {"file": "clip.mp4", "key": "", "kind": "video", "seconds": 12.5, "name": "spot.mp4"}
    assert "files" not in next(i for b in data["blocks"] for i in b["items"] if i["type"] == "media")["config"]
    with app.app_context():  # the JSON alone holds no video
        plain = transfer.dump(db.session.get(Survey, 1))
        assert "media" not in next(i for b in plain["blocks"] for i in b["items"] if i["type"] == "media")
    _upload(client, None, raw=body, name="x.zip", title="Kopie mit Video")
    with app.app_context():
        new = db.session.scalar(db.select(Survey).order_by(Survey.id.desc()))
        assert new.id != 1
        item = next(i for b in new.blocks for i in b.items if i.type == "media")
        [asset] = item.assets
        assert asset.filename != "clip.mp4" and asset.filename.endswith(".mp4") and asset.status == Asset.APPROVED
        with open(os.path.join(upload_folder(new.uid), asset.filename), "rb") as f:
            assert f.read() == MP4
        assert item.config["files"][""] == {"kind": "video", "mime": "video/mp4", "bytes": len(MP4), "seconds": 12.5,
                                            "name": "spot.mp4"}


def test_a_file_that_is_no_video_is_not_taken(app, client, survey):
    _with_video(app, client)
    body = client.get("/surveys/1/export/survey.zip").data
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        members = {i.filename: z.read(i) for i in z.infolist()}
    members["files/clip.mp4"] = b"<html>no video</html>" + b"\x00" * 20
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    page = _upload(client, None, raw=raw.getvalue(), name="x.zip", title="Kopie ohne Video")
    assert "nicht übernommen" in page
    with app.app_context():
        new = db.session.scalar(db.select(Survey).order_by(Survey.id.desc()))
        item = next(i for b in new.blocks for i in b.items if i.type == "media")
        assert item.assets == [] and "files" not in item.config
