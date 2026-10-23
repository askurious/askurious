"""Public image distribution is an explicit declaration, independent of participant approval and metadata."""
import csv
import io
import json
import os
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from app import asset_rights, coscine, media, zenodo, zenodo_auth
from app.extensions import db
from app.models import Asset, Survey, User
from tests.conftest import add, fill_consent
from tests.exports.test_image_rights_paths import SVG_RIGHTS, attributed_png
from tests.exports.test_transfer import _upload


def uploaded(app, client):
    add(client, "image")
    client.post("/surveys/1/editor/items/2/upload", data={"file": (io.BytesIO(attributed_png()), "image.png")},
                content_type="multipart/form-data")
    with app.app_context():
        return db.session.scalar(db.select(Asset)).id


def grant_form(asset_id, **overrides):
    return {"asset_id": str(asset_id), "basis": "license", "creator": "Ada Example", "source": "Museum collection",
            "credit": "Photo: Ada Example / Museum", "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/", "public_confirm": "1", **overrides}


def declare(client, aid, **overrides):
    return client.post("/surveys/1/editor/image-rights", data=grant_form(aid, **overrides), follow_redirects=True)


def test_metadata_and_participant_approval_are_not_public_permission(app, client, survey):
    aid = uploaded(app, client)
    page = client.get("/surveys/1/editor/image-rights").data.decode()
    assert "Ada Example" in page and "CC BY 4.0" in page and "kein Berechtigungsnachweis" in page
    assert 'name="public_confirm" value="1" checked' not in page
    assert "Keine vertraulichen Verträge" in page
    with app.app_context():
        s, asset = db.session.get(Survey, 1), db.session.get(Asset, aid)
        assert asset.status == Asset.APPROVED and not asset_rights.declared(s, asset)
        with pytest.raises(asset_rights.RightsError, match="Öffentliche Archivierung gesperrt"):
            zenodo.package(s)
        assert dict(zenodo.package(s, require_public_image_rights=False))["images.zip"]
    # Local questionnaire/transfer downloads remain available.
    assert client.get("/surveys/1/export/survey.json").status_code == 200


def test_explicit_declaration_enables_archive_with_credits_and_can_be_revoked(app, client, survey):
    aid = uploaded(app, client)
    assert "Bildrechte gespeichert" in declare(client, aid).data.decode()
    with app.app_context():
        s, asset = db.session.get(Survey, 1), db.session.get(Asset, aid)
        assert asset_rights.declared(s, asset)
        assert asset.rights["confirmation"]["user_id"] == 1
        archived = dict(zenodo.package(s))["images.zip"]
    with ZipFile(io.BytesIO(archived)) as archive:
        row = next(csv.DictReader(io.StringIO(archive.read("images.csv").decode())))
        assert row["rights_creator"] == "Ada Example" and row["rights_credit"] == "Photo: Ada Example / Museum"
        assert row["rights_basis"] == "license" and row["rights_declared_by"]
        assert len(row["rights_original_sha256"]) == 64
    declare(client, aid, public_confirm="")
    with app.app_context():
        assert not asset_rights.declared(db.session.get(Survey, 1), db.session.get(Asset, aid))


@pytest.mark.parametrize("overrides", [{"creator": ""}, {"basis": ""}, {"source": ""},
                                       {"license": "", "license_url": ""},
                                       {"basis": "permission", "permission": ""}])
def test_incomplete_declaration_cannot_approve_public_distribution(app, client, survey, overrides):
    aid = uploaded(app, client)
    assert "Für die öffentliche Archivierung fehlen" in declare(client, aid, **overrides).data.decode()
    with app.app_context():
        assert not db.session.get(Asset, aid).rights


def test_own_work_and_individual_permission_are_supported_without_inventing_a_cc_licence(app, client, survey):
    aid = uploaded(app, client)
    declare(client, aid, basis="own", source="", license="", license_url="")
    with app.app_context():
        asset = db.session.get(Asset, aid)
        assert asset_rights.declared(db.session.get(Survey, 1), asset)
        assert asset.rights["basis"] == "own" and not asset.rights["license"]
    declare(client, aid, basis="permission", permission="Rights holder's written public-archive permission, ref M-12",
            license="", license_url="")
    with app.app_context():
        assert asset_rights.declared(db.session.get(Survey, 1), db.session.get(Asset, aid))


def test_changed_file_or_changed_terms_invalidate_previous_confirmation(app, client, survey):
    aid = uploaded(app, client)
    declare(client, aid)
    with app.app_context():
        s, asset = db.session.get(Survey, 1), db.session.get(Asset, aid)
        asset.rights = {**asset.rights, "license": "Different terms"}
        assert not asset_rights.declared(s, asset)
        asset.rights = {**asset.rights, "license": "CC BY 4.0"}
        with open(asset_rights.path(s, asset), "ab") as stream:
            stream.write(b"changed")
        with pytest.raises(asset_rights.RightsError):
            zenodo.package(s)


@pytest.mark.parametrize("existing", [False, True])
def test_missing_rights_stop_oauth_and_remote_draft_mutations(app, client, survey, monkeypatch, existing):
    uploaded(app, client)
    fill_consent(client, open_data="1", license="CC BY 4.0")
    state = {"id": 42, "target": "sandbox", "state": "draft"} if existing else None
    api = Mock(target="sandbox")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.zenodo = state
        db.session.commit()
        with pytest.raises(zenodo.ZenodoError, match="Öffentliche Archivierung gesperrt"):
            zenodo.upload(s, api)
        api.request.assert_not_called()
    token = Mock(side_effect=AssertionError("must not refresh OAuth"))
    monkeypatch.setattr(zenodo_auth, "available", lambda target: True)
    monkeypatch.setattr(zenodo_auth, "access_token", token)
    page = client.post("/surveys/1/zenodo", data={"target": "sandbox", "action": "upload"}, follow_redirects=True)
    assert "Öffentliche Archivierung gesperrt" in page.data.decode()
    token.assert_not_called()
    with app.app_context():
        assert db.session.get(Survey, 1).zenodo == state


def test_public_coscine_is_blocked_before_put_private_coscine_keeps_working(app, client, survey, monkeypatch):
    uploaded(app, client)
    put = Mock(return_value={"ok": True})
    monkeypatch.setattr(coscine, "put_parts", put)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for visibility in ("Public", ""):
            with pytest.raises(coscine.CoscineError, match="Öffentliche Archivierung gesperrt"):
                coscine.upload(s, Mock(), {}, {"visibility": {"displayName": visibility}}, Mock(), {}, {"paket"}, "Person")
            put.assert_not_called()
        assert coscine.upload(s, Mock(), {}, {"visibility": {"displayName": "Project Members"}}, Mock(), {}, {"paket"}, "Person") == {"ok": True}
        assert put.call_count == 1


def test_server_copy_keeps_declaration_but_import_does_not_trust_claim(app, client, survey):
    aid = uploaded(app, client)
    declare(client, aid)
    from app import duplicate

    with app.app_context():
        s = db.session.get(Survey, 1)
        new, _ = duplicate.create(s, db.session.get(User, 1), "Copied rights")
        db.session.commit()
        copied = asset_rights.image_assets(new)[0]
        assert asset_rights.declared(new, copied)
        assert copied.rights["copied_from"]["asset_id"] == aid
    data = json.loads(client.get("/surveys/1/export/survey.json").data)
    assert "ist als Entwurf angelegt" in _upload(client, data, title="Imported rights")
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Imported rights"))
        asset = asset_rights.image_assets(s)[0]
        assert asset.rights["creator"] == "Ada Example" and asset.rights["imported_unconfirmed"]
        assert "confirmation" not in asset.rights and not asset_rights.declared(s, asset)


def test_logo_and_icon_have_explicit_rights_and_frozen_receipts(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        logo = media.save_logo(s, SVG_RIGHTS)
        s.theme = {"logo": {**logo, "alt": {"de": "Logo"}}}
        db.session.commit()
        asset = asset_rights.theme_images(s)[0]
        target, filename = asset.id, asset.filename
        with pytest.raises(asset_rights.RightsError):
            asset_rights.snapshot_receipts(s, {filename: Path(asset_rights.path(s, asset))})
    assert "Ada Example" in client.get("/surveys/1/editor/image-rights").data.decode()
    assert "Bildrechte gespeichert" in declare(client, target).data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        asset = asset_rights.theme_images(s)[0]
        receipt = asset_rights.snapshot_receipts(s, {filename: Path(asset_rights.path(s, asset))})[filename]
        assert asset_rights.declared(s, asset)
        assert receipt["rights"]["creator"] == "Ada Example"
        assert receipt["sha256"] == receipt["rights"]["confirmation"]["sha256"]


def test_logo_rights_survive_colour_edit_reset_and_design_copy(app, client, survey):
    from app import designs, theme

    with app.app_context():
        s = db.session.get(Survey, 1)
        logo = media.save_logo(s, SVG_RIGHTS)
        s.theme = {"logo": {**logo, "alt": {"de": "Logo"}}}
        asset = asset_rights.theme_images(s)[0]
        asset_rights.record(s, asset, grant_form(asset.id), db.session.get(User, 1))
        # Stale records cannot consume the bounded slot of a still-used logo.
        s.theme = {**s.theme, "image_rights": {**{f"old-{n}.png": {} for n in range(30)}, **s.theme["image_rights"]}}
        s.theme = theme.clean(s.theme)
        assert set(s.theme["image_rights"]) == {asset.filename}
        db.session.commit()
    client.post("/surveys/1/design", data={"preset": "klar", "accent": "#336699", "font": "system", "radius": "soft"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert asset_rights.declared(s, asset_rights.theme_images(s)[0])
    client.post("/surveys/1/design", data={"reset": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert asset_rights.declared(s, asset_rights.theme_images(s)[0])
        design = designs.create(db.session.get(User, 1), "Image rights design", s.theme, from_uid=s.uid)
        before = asset_rights.theme_images(s)[0].filename
        designs.apply_to(s, design)
        db.session.commit()
        new = asset_rights.theme_images(s)[0]
        assert new.filename != before and asset_rights.declared(s, new)


def test_theme_import_keeps_details_but_requires_fresh_declaration(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        logo = media.save_logo(s, SVG_RIGHTS)
        s.theme = {"logo": {**logo, "alt": {"de": "Logo"}}}
        asset = asset_rights.theme_images(s)[0]
        asset_rights.record(s, asset, grant_form(asset.id), db.session.get(User, 1))
        db.session.commit()
    data = json.loads(client.get("/surveys/1/export/survey.json").data)
    assert "ist als Entwurf angelegt" in _upload(client, data, title="Imported theme rights")
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Imported theme rights"))
        asset = asset_rights.theme_images(s)[0]
        assert asset.rights["creator"] == "Ada Example"
        assert asset.rights["imported_unconfirmed"] and "confirmation" not in asset.rights
        assert not asset_rights.declared(s, asset)


@pytest.mark.sqlite_foreign_keys_off  # goes down and up again: SQLite's batch mode (tests/conftest.py, B8)
def test_nullable_rights_migration_does_not_grant_existing_images(app, client, survey):
    from alembic import command
    from sqlalchemy import text
    from app import migrations

    aid = uploaded(app, client)
    with app.app_context():
        # Explicitly exercise the upgrade of an already existing image row.
        db.session.remove()
        migrations._run(command.downgrade, "0077")
        migrations._run(command.upgrade)
        assert db.session.execute(text("SELECT rights FROM asset WHERE id = :id"), {"id": aid}).scalar() is None
        with db.engine.connect() as connection:
            assert migrations.differences(connection) == []


def test_frozen_public_snapshot_checks_frozen_images_and_instrument_wording(app, client, survey):
    import base64

    aid = uploaded(app, client)
    declare(client, aid)

    def snapshot(data):
        out = io.BytesIO()
        with ZipFile(out, "w") as archive:
            archive.writestr("befragung.json", json.dumps(data))
        return out.getvalue()

    with app.app_context():
        s, asset = db.session.get(Survey, 1), db.session.get(Asset, aid)
        blob = Path(asset_rights.path(s, asset)).read_bytes()
        data = {"blocks": [], "files": {asset.filename: base64.b64encode(blob).decode()}}
        asset_rights.require_public_snapshot(s, snapshot(data))
        data["files"][asset.filename] = base64.b64encode(b"unrelated image").decode()
        with pytest.raises(asset_rights.RightsError, match="Bilder dieser Fassung"):
            asset_rights.require_public_snapshot(s, snapshot(data))
        data = {"files": {}, "blocks": [{"items": [{"config": {"construct": {"key": "gdms"}}}]}]}
        with pytest.raises(asset_rights.RightsError):
            asset_rights.require_public_snapshot(s, snapshot(data))
