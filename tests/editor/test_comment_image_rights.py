"""Attribution accompanies the edited screenshot; private metadata and the original pixels do not."""
import csv
import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

from app import comment_shots, image_rights
from tests.editor.test_comment_shots import _jpeg_with_gps, _png, _send, _setup


@pytest.mark.parametrize("jpeg", [False, True])
def test_separate_attribution_survives_comment_conversion_and_export(app, client, survey, monkeypatch, jpeg):
    _setup(client)
    if jpeg:
        monkeypatch.setattr(comment_shots, "PNG_LIMIT", 0)
    form = {"shot_rights_creator": "=Creator", "shot_rights_copyright": "© Studio",
            "shot_rights_credit": "Photo: Studio", "shot_rights_source": "https://example.org/photo?a=1&copyright=2",
            "shot_rights_license": "Individual permission", "shot_rights_license_url": "https://example.org/terms"}
    reply = _send(client, "/surveys/1/editor/preview/comment", data=form, image=_jpeg_with_gps())
    assert reply.status_code == 200
    shot = client.get(f"/surveys/1/editor/preview/comment/{reply.json['id']}/shot")
    rights = image_rights.from_bytes(shot.data)
    assert rights == {key.removeprefix("shot_rights_"): [value] for key, value in form.items()}
    im = Image.open(io.BytesIO(shot.data))
    assert im.format == ("JPEG" if jpeg else "PNG")
    assert 0x8825 not in im.getexif() and 0x0110 not in im.getexif()  # GPS and camera absent
    with zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/comments.zip").data)) as archive:
        row = next(csv.DictReader(io.StringIO(archive.read("comments.csv").decode("utf-8-sig"))))
        assert archive.read(row["image"]) == shot.data
        assert row["image_creator"] == "'=Creator"  # spreadsheet formula quoting remains effective
        assert row["image_license"] == "Individual permission"
        assert row["image_source"] == form["shot_rights_source"]


def test_embedded_attribution_and_manual_credit_are_combined(app, client, survey):
    _setup(client)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Author", "Photographer")
    metadata.add_text("Comment", "PRIVATE LOCATION")
    data = io.BytesIO()
    Image.open(io.BytesIO(_png())).save(data, "PNG", pnginfo=metadata)
    reply = _send(client, "/surveys/1/editor/preview/comment", image=data.getvalue(),
                  data={"shot_rights_credit": "<b>Agency</b>", "shot_rights_license_url": "javascript:alert(1)"})
    assert reply.status_code == 200
    shot = client.get(f"/surveys/1/editor/preview/comment/{reply.json['id']}/shot").data
    assert image_rights.from_bytes(shot) == {"creator": ["Photographer"], "credit": ["Agency"]}
    assert b"PRIVATE LOCATION" not in shot


def test_attribution_without_image_does_not_attach_to_comment(app, client, survey):
    _setup(client)
    html = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    assert 'data-shot-rights disabled' in html and 'data-shot-rights-panel hidden' in html
    assert all(f'name="shot_rights_{field}"' in html for field in image_rights.FIELDS)
    reply = _send(client, "/surveys/1/editor/preview/comment", data={"shot_rights_creator": "Unrelated"})
    assert reply.status_code == 200 and not reply.json["shot"]
    row = next(csv.DictReader(io.StringIO(client.get("/surveys/1/export/comments.csv").data.decode("utf-8-sig"))))
    assert row["image_creator"] == ""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed to exercise screenshot state transitions")
def test_browser_keeps_rights_when_marking_and_resets_them_for_another_image():
    root = Path(__file__).resolve().parents[2]
    subprocess.run([shutil.which("node"), str(root / "tests/editor/comment_rights_cases.js"),
                    str(root / "app/static/comments.js")], check=True, capture_output=True, timeout=30)
