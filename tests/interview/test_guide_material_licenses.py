"""A guide licence is explicitly granted for immutable material, not inferred from a study's data licence."""
import copy
import hashlib
import io
import json
import re
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import pytest
from PIL import Image

from app import asset_rights, interview_versions, media, zenodo, zenodo_auth
from app.extensions import db
from app.models import Asset, Item, Survey, User
from tests.exports.test_zenodo import FakeZenodo
from tests.interview.test_interview import docx_text, make


def freeze(app, client, code, sid, licence="cc-by-4.0", **extra):
    form = {"note": "endgueltig", "material_license": licence, "material_rights_confirm": "1", **extra}
    response = client.post(f"/guide/{code}/versions", data=form, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        return copy.deepcopy(db.session.get(Survey, sid).interview["versions"][-1])


@pytest.fixture
def remote(monkeypatch):
    fake = FakeZenodo()
    monkeypatch.setattr(zenodo.requests, "request", fake)
    monkeypatch.setattr(zenodo_auth, "available", lambda target: True)
    monkeypatch.setattr(zenodo_auth, "access_token", lambda user, target: "secret-token")
    monkeypatch.setattr(zenodo_auth, "connection", lambda user, target: object())
    return fake


def test_selection_starts_without_a_grant_or_checked_confirmation(app, client):
    code, _ = make(app, client)
    page = client.get(f"/guide/{code}/export").data.decode()
    select = re.search(r'<select name="material_license">(.*?)</select>', page, re.S).group(1)
    values = re.findall(r'<option value="([^"]*)"', select)
    assert values[0] == "" and "selected" not in select
    assert set(values[1:]) == set(interview_versions.MATERIAL_LICENSES)
    checkbox = re.search(r'<input\b[^>]*name="material_rights_confirm"[^>]*>', page).group()
    assert "checked" not in checkbox


@pytest.mark.parametrize("fields", [
    {"material_license": "cc-by-4.0"},
    {"material_license": "All rights reserved", "material_rights_confirm": "1"},
    {"material_license": "", "material_rights_confirm": "1"},
])
def test_unconfirmed_or_invalid_selection_does_not_freeze_a_revision(app, client, fields):
    code, sid = make(app, client)
    response = client.post(f"/guide/{code}/versions", data=fields, follow_redirects=True)
    assert response.status_code == 200 and "Fassung 1 festgehalten" not in response.data.decode()
    with app.app_context():
        survey = db.session.get(Survey, sid)
        assert not survey.interview.get("versions")
        assert interview_versions.path(survey, "fassung-1.zip") is None


@pytest.mark.parametrize("licence", list(interview_versions.MATERIAL_LICENSES))
def test_grant_is_frozen_in_manifest_documents_and_upload(app, client, remote, licence):
    code, sid = make(app, client)
    notice = "Quotation on page 2: Ada Example, separate permission for redistribution, ref P-42."
    version = freeze(app, client, code, sid, licence, material_third_party=notice)
    grant = version["material_license"]
    assert grant["id"] == licence and grant["confirmed"] is True
    assert grant["confirmed_by"] and grant["confirmed_at"] and grant["third_party"] == notice
    raw = client.get(f"/guide/{code}/versions/{version['name']}").data
    assert hashlib.sha256(raw).hexdigest() == version["sha256"]
    with ZipFile(io.BytesIO(raw)) as packed:
        assert json.loads(packed.read(interview_versions.LICENSE_MANIFEST)) == grant
        assert json.loads(packed.read("image-rights.json")) == {}
        licence_text = packed.read("LICENSE.txt").decode()
        assert interview_versions.MATERIAL_LICENSES[licence][1] in licence_text
        assert "werden nicht umlizenziert" in licence_text and notice in licence_text
        assert notice in docx_text(packed.read("interview-guide.docx"))
        assert "Materiallizenz" in docx_text(packed.read("interview-card.docx"))
        for row in packed.read("checksums.sha256").decode().splitlines():
            digest, name = row.split("  ", 1)
            assert hashlib.sha256(packed.read(name)).hexdigest() == digest
    response = client.post(f"/guide/{code}/versions/{version['name']}/zenodo",
                           data={"target": "sandbox", "action": "upload"}, follow_redirects=True)
    assert "als Entwurf auf Zenodo angelegt" in response.data.decode()
    assert remote.meta["license"] == licence and remote.files[version["name"]] == raw
    assert "Fremdbestandteile behalten" in remote.meta["description"]


def test_new_selection_never_relicenses_an_old_revision(app, client, remote):
    code, sid = make(app, client)
    first = freeze(app, client, code, sid, "cc-by-sa-4.0")
    old_bytes = client.get(f"/guide/{code}/versions/{first['name']}").data
    second = freeze(app, client, code, sid, "cc-zero")
    client.post(f"/guide/{code}/settings", data={"part": "interview", "order": "linear", "short": "after", "post": "1"})
    with app.app_context():
        versions = db.session.get(Survey, sid).interview["versions"]
        assert versions[0]["material_license"] == first["material_license"]
        assert versions[1]["material_license"]["id"] == second["material_license"]["id"] == "cc-zero"
    client.post(f"/guide/{code}/versions/{first['name']}/zenodo", data={"target": "sandbox", "action": "upload"})
    assert remote.meta["license"] == "cc-by-sa-4.0" and remote.files[first["name"]] == old_bytes


@pytest.mark.parametrize("change", ["licence", "bytes"])
def test_inconsistent_frozen_material_is_rejected_before_oauth(app, client, monkeypatch, change):
    code, sid = make(app, client)
    version = freeze(app, client, code, sid)
    with app.app_context():
        survey = db.session.get(Survey, sid)
        if change == "licence":
            changed = copy.deepcopy(version)
            changed["material_license"]["id"] = "cc-zero"
            survey.interview = {**survey.interview, "versions": [changed]}
            db.session.commit()
        else:
            where = Path(interview_versions.path(survey, version["name"]))
            where.write_bytes(where.read_bytes() + b"changed")
    token = Mock(side_effect=AssertionError("OAuth must not be called"))
    monkeypatch.setattr(zenodo_auth, "access_token", token)
    response = client.post(f"/guide/{code}/versions/{version['name']}/zenodo",
                           data={"target": "sandbox", "action": "upload"}, follow_redirects=True)
    assert "stimmen nicht überein" in response.data.decode()
    token.assert_not_called()


def test_unlicensed_legacy_revisions_stay_downloadable_and_cannot_be_relicensed_on_upload(app, client, remote):
    code, sid = make(app, client)
    client.post(f"/guide/{code}/versions", data={"note": "erste"})
    with app.app_context():
        survey = db.session.get(Survey, sid)
        legacy = copy.deepcopy(survey.interview["versions"][0])
        legacy.pop("material_license", None)
        legacy["zenodo"] = {"id": 42, "target": "sandbox", "state": "draft"}
        survey.interview = {**survey.interview, "versions": [legacy]}
        db.session.commit()
    response = client.post(f"/guide/{code}/versions/{legacy['name']}/zenodo",
                           data={"target": "sandbox", "action": "upload", "material_license": "cc-by-4.0",
                                 "material_rights_confirm": "1"}, follow_redirects=True)
    assert "neue Fassung" in response.data.decode() and not remote.calls
    assert client.get(f"/guide/{code}/versions/{legacy['name']}").status_code == 200
    remote.published = True
    client.post(f"/guide/{code}/versions/{legacy['name']}/zenodo", data={"target": "sandbox", "action": "refresh"})
    with app.app_context():
        assert db.session.get(Survey, sid).interview["versions"][0]["zenodo"]["state"] == "published"


def test_unconfirmed_image_blocks_public_freezing_but_private_revision_is_available(app, client):
    code, sid = make(app, client)
    with app.app_context():
        survey = db.session.get(Survey, sid)
        raw = io.BytesIO()
        Image.new("RGB", (4, 4), "blue").save(raw, "PNG")
        filename = media.save_bytes(survey, raw.getvalue())
        item = Item(type="image", code="PIC", answer_mode="optional", config={}, texts={"de": {"question": "Bild"}})
        survey.blocks[0].items.append(item)
        asset = Asset(filename=filename, source="upload", status=Asset.APPROVED)
        item.assets.append(asset)
        db.session.commit()
        asset_id = asset.id
    response = client.post(f"/guide/{code}/versions", data={"material_license": "cc-by-4.0", "material_rights_confirm": "1"},
                           follow_redirects=True)
    assert "fehlen bestätigte Rechte" in response.data.decode()
    with app.app_context():
        assert not db.session.get(Survey, sid).interview["versions"]
    client.post(f"/guide/{code}/versions", data={})
    assert client.get(f"/guide/{code}/versions/fassung-1.zip").status_code == 200
    with app.app_context():
        survey = db.session.get(Survey, sid)
        asset = db.session.get(Asset, asset_id)
        person = db.session.scalar(db.select(User))
        asset_rights.record(survey, asset, {"basis": "license", "creator": "Ada Example", "source": "https://example.org/image",
                                          "license": "CC BY-NC 4.0", "public_confirm": "1"}, person)
        db.session.commit()
    licensed = freeze(app, client, code, sid)
    raw = client.get(f"/guide/{code}/versions/{licensed['name']}").data
    with ZipFile(io.BytesIO(raw)) as packed:
        receipt = next(iter(json.loads(packed.read("image-rights.json")).values()))
        assert receipt["rights"]["license"] == "CC BY-NC 4.0"  # independent of the guide's own CC BY licence
        assert json.loads(packed.read(interview_versions.LICENSE_MANIFEST))["id"] == "cc-by-4.0"


@pytest.mark.parametrize("key", ["gdms", "kut", "tam_pu"])
def test_material_licence_cannot_override_restricted_instrument_wording(app, client, key):
    code, sid = make(app, client)
    with app.app_context():
        survey = db.session.get(Survey, sid)
        survey.blocks[0].items.append(Item(type="info", code="PROTECTED", answer_mode="optional",
                                         config={"construct": {"key": key}}, texts={"de": {"question": "Protected text"}}))
        db.session.commit()
    response = client.post(f"/guide/{code}/versions", data={"material_license": "cc-by-4.0", "material_rights_confirm": "1"},
                           follow_redirects=True)
    assert "ohne allgemeine Veröffentlichungsfreigabe" in response.data.decode()
    with app.app_context():
        assert not db.session.get(Survey, sid).interview["versions"]
    client.post(f"/guide/{code}/versions", data={})
    assert client.get(f"/guide/{code}/versions/fassung-1.zip").status_code == 200


def test_foreign_survey_file_cannot_import_a_local_material_grant(app, client):
    from app import interview
    from tests.exports.test_transfer import _upload

    code, sid = make(app, client)
    version = freeze(app, client, code, sid)
    data = json.loads(client.get(f"/surveys/{sid}/export/survey.json").data)
    data["material_license"] = version["material_license"]
    data["interview"]["material_license"] = version["material_license"]
    data["interview"]["versions"] = [version]
    _upload(client, data, title="Imported licence claim")
    with app.app_context():
        imported = db.session.scalar(db.select(Survey).filter_by(title="Imported licence claim"))
        assert interview.state(imported)["versions"] == []
        assert "material_license" not in imported.interview


def test_non_image_definition_attachment_is_not_misclassified_as_unconfirmed_image(app, client, monkeypatch):
    import base64
    from app import packages

    code, sid = make(app, client)
    original = packages.survey_file

    def with_attachment(survey, public=False):
        content = original(survey, public)
        definition = json.loads(content[0][1])
        definition["files"]["notes.txt"] = base64.b64encode(b"Own research notes").decode()
        return [(content[0][0], json.dumps(definition).encode()), *content[1:]]

    monkeypatch.setattr(packages, "survey_file", with_attachment)
    version = freeze(app, client, code, sid)
    raw = client.get(f"/guide/{code}/versions/{version['name']}").data
    with ZipFile(io.BytesIO(raw)) as packed:
        assert json.loads(packed.read("image-rights.json")) == {}
