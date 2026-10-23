"""Documented instrument permissions cover exact private uses; login and general showcase access do not."""
import json

import pytest

from app import access, constructs, export
from app.extensions import db
from app.models import Item, Survey, User
from tests.instrument_permissions import grant_instrument


def _word(app, key, lang="en"):
    with app.app_context():
        return next(iter(constructs.get(key).spec["texts"][lang]["statements"].values()))


@pytest.mark.parametrize("key", sorted(constructs.PERMISSION_REQUIRED))
def test_login_and_admin_alone_never_release_catalogue_or_new_insertion(app, client, survey, key):
    word = _word(app, key)
    for path in (f"/constructs/{key}?lang=en", f"/constructs/{key}?survey=1&lang=en", "/constructs/box?survey=1"):
        page = client.get(path)
        assert page.status_code == 200
        assert word not in page.data.decode()
        assert "dokumentierte Berechtigung" in page.data.decode()
    response = client.post("/surveys/1/editor/blocks/2/items", data={"type": f"construct:{key}", "new_block": "after"},
                           follow_redirects=True)
    assert "dokumentierte Berechtigung" in response.data.decode()
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert len(survey.blocks) == 2 and len(survey.blocks[1].items) == 0
        with pytest.raises(ValueError, match="Berechtigung"):
            constructs.build(key, ["en"])


@pytest.mark.parametrize("change", ["empty", "broken", "evidence", "account", "survey", "language"])
def test_permission_requires_all_documentary_fields_and_actual_membership(app, client, survey, change):
    grant_instrument(app, "gdms")
    entry = app.config["INSTRUMENT_WORDING_PERMISSIONS"][0]
    if change == "empty":
        app.config["INSTRUMENT_WORDING_PERMISSIONS"] = []
    elif change == "broken":
        app.config["INSTRUMENT_WORDING_PERMISSIONS"] = "{not JSON"
    else:
        entry[{"evidence": "evidence", "account": "orcid", "survey": "survey_uid", "language": "languages"}[change]] = {
            "evidence": " ", "account": "another-account", "survey": "*", "language": ["en"]}[change]
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gdms"})
    with app.app_context():
        assert db.session.get(Item, 2) is None


@pytest.mark.parametrize("key", sorted(constructs.PERMISSION_REQUIRED))
def test_documented_private_use_keeps_public_wording_hidden_and_survives_permission_revocation(app, client, survey, key):
    grant_instrument(app, key)
    # Environment JSON and programmatic configuration have identical semantics.
    app.config["INSTRUMENT_WORDING_PERMISSIONS"] = json.dumps(app.config["INSTRUMENT_WORDING_PERMISSIONS"])
    word = _word(app, key)
    assert word in client.get(f"/constructs/{key}?survey=1&lang=en").data.decode()
    client.post("/surveys/1/editor/blocks/2/items", data={"type": f"construct:{key}"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.default_language = "en"
        assert word in export.questionnaire_md(survey)
        assert word not in export.questionnaire_md(survey, public=True)
        assert word not in export.codebook_csv(survey, public=True)
        with pytest.raises(ValueError, match=key):
            constructs.require_public_material(survey)
    app.config["INSTRUMENT_WORDING_PERMISSIONS"] = []
    assert word not in client.get(f"/constructs/{key}?survey=1&lang=en").data.decode()
    # Revocation stops new catalogue uses, not the running study's authorised member/participant path.
    assert word in client.get("/surveys/1/editor/items/2?lang=en").data.decode()


def test_legacy_showcase_does_not_grant_instrument_wording_or_inherit_a_study_permission(app, client, survey):
    grant_instrument(app, "gdms")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gdms"})
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    other.post("/terms/", data={"accept": "1"})
    grant_instrument(app, "gdms", orcid="0000-0001-5555-5555")
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.showcase = survey.org_showcase = True  # a flag retained from an older installation
        db.session.commit()
        assert not access.showcased(survey)
        assert survey.id not in db.session.scalars(access.showcase_query()).all()
        person = db.session.scalar(db.select(User).filter_by(orcid="0000-0001-5555-5555"))
        assert not constructs.permitted("gdms", survey, person)
        assert access.role(person, survey) is None
    assert other.get("/surveys/1/editor/").status_code == 404
    assert other.get("/constructs/gdms?survey=1").status_code == 404
    assert client.get("/surveys/1/editor/items/2").status_code == 200


def test_duplicate_import_and_frozen_public_material_cannot_inherit_private_permission(app, client, survey):
    from tests.exports.test_transfer import _file, _upload

    grant_instrument(app, "kut")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kut"})
    data = _file(client)
    response = client.post("/surveys/1/duplicate", data={"title": "Copy"}, follow_redirects=True)
    assert "dokumentierte Berechtigung" in response.data.decode()
    assert "dokumentierte Berechtigung" in _upload(client, data, title="Import")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Survey)) == 1
        # The frozen provenance remains restrictive even after today's study removes the instrument.
        db.session.delete(db.session.get(Item, 2))
        db.session.commit()
        constructs.require_public_material(db.session.get(Survey, 1))
        with pytest.raises(ValueError, match="kut"):
            constructs.require_public_definition(data)


def test_fallback_language_needs_its_own_permission_and_templates_keep_only_used_languages(app, client, survey):
    with app.app_context():
        study = db.session.get(Survey, 1)
        study.languages = ["de"]
        db.session.commit()
    grant_instrument(app, "gdms", languages=["de"])
    page = client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gdms"}, follow_redirects=True)
    assert "dokumentierte Berechtigung" in page.data.decode()
    with app.app_context():
        assert db.session.get(Item, 2) is None
    grant_instrument(app, "gdms", languages=["de", "en"])
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gdms"})
    grant_instrument(app, "kut", languages=["de"])
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kut"})
    with app.app_context():
        assert set(db.session.get(Item, 2).config["construct"]["template"]["texts"]) == {"en"}
        assert set(db.session.get(Item, 3).config["construct"]["template"]["texts"]) == {"de"}
    assert _word(app, "kut") not in client.get("/constructs/kut?survey=1&lang=en").data.decode()


@pytest.mark.parametrize("obfuscated", ["value", "construct", "key"])
def test_import_checks_restricted_provenance_after_normalizing_control_characters(app, client, survey, obfuscated):
    from tests.exports.test_transfer import _file, _upload

    grant_instrument(app, "kut")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kut"})
    data = _file(client)
    config = data["blocks"][1]["items"][0]["config"]
    if obfuscated == "value":
        config["construct"]["key"] = "k\x00ut"
    elif obfuscated == "construct":
        config["construct\x00"] = config.pop("construct")
    else:
        config["construct"]["k\x00ey"] = config["construct"].pop("key")
    assert not constructs.restricted_definition_keys(data)  # raw provenance alone would miss this
    assert "dokumentierte Berechtigung" in _upload(client, data, title="Obfuscated import")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Survey)) == 1


def test_item_copy_requires_the_actual_source_language_and_limits_its_template(app, client, survey):
    from app import duplicate

    grant_instrument(app, "kut")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kut"})
    client.post("/surveys/new", data={"title": "Target", "languages": ["de"], "default_language": "de"})
    person = grant_instrument(app, "kut", survey_id=2, languages=["de"])
    with app.app_context():
        item, target = db.session.get(Item, 2), db.session.get(Survey, 2)
        source = item.block.survey
        source.default_language = "en"
        item.texts = {"en": item.texts["en"]}  # copied DE would actually contain the English source
        db.session.commit()
        assert not constructs.permitted_copy(item, target, person)
        with pytest.raises(ValueError, match="Berechtigung"):
            duplicate.item_copy(item, target, set(), user=person)
        # When DE exists, its copy must not carry the unrequested EN wording in its hidden snapshot.
        item.texts = {"de": constructs.get("kut").spec["texts"]["de"], "en": item.texts["en"]}
        copied = duplicate.item_copy(item, target, set(), user=person)
        assert set(copied.texts) == {"de"}
        assert set(copied.config["construct"]["template"]["texts"]) == {"de"}


def test_automatic_language_fallback_keeps_source_permission_across_copy_chains(app, client, survey):
    from app import duplicate

    with app.app_context():
        source = db.session.get(Survey, 1)
        source.languages, source.default_language = ["en"], "en"
        db.session.commit()
    grant_instrument(app, "kut", languages=["en"])
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kut"})
    for title in ("First copy", "Second copy"):
        client.post("/surveys/new", data={"title": title, "languages": ["de"], "default_language": "de"})
    person = grant_instrument(app, "kut", survey_id=2, languages=["de", "en"])
    grant_instrument(app, "kut", survey_id=3, languages=["de"])
    with app.app_context():
        item = db.session.get(Item, 2)
        target = db.session.get(Survey, 2)
        first = duplicate.item_copy(item, target, set(), user=person)
        target.blocks[1].items.append(first)
        db.session.commit()
        item_id = first.id
    with app.app_context():  # fresh ORM object: no temporary untranslated Python attribute
        first, second = db.session.get(Item, item_id), db.session.get(Survey, 3)
        assert first.config["construct"]["wording_sources"] == {"de": ["en"]}
        with pytest.raises(ValueError, match="Berechtigung"):
            duplicate.item_copy(first, second, set(), user=person)
