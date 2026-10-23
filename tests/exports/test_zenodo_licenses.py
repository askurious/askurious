"""Licences are validated before OAuth refresh, draft creation, replacement or file deletion."""
from unittest.mock import Mock

import pytest

from app import zenodo, zenodo_auth
from app.extensions import db
from app.models import Survey
from tests.conftest import fill_consent


VERSION = {"n": 1, "name": "fassung-1.zip", "at": "2026-10-17T12:00:00", "sha256": "abc"}


@pytest.mark.parametrize(("entered", "expected"), [
    ("CC BY 4.0", "cc-by-4.0"), (" CC-BY 4.0 ", "cc-by-4.0"),
    ("cc-by-4.0", "cc-by-4.0"), ("CC BY-SA 4.0", "cc-by-sa-4.0"),
    ("cc-by-sa-4.0", "cc-by-sa-4.0"), ("CC BY-NC 4.0", "cc-by-nc-4.0"),
    ("cc-by-nc-4.0", "cc-by-nc-4.0"), ("CC0", "cc-zero"),
    ("CC0 1.0", "cc-zero"), ("cc-zero", "cc-zero"),
    ("\tCc  By-Nc\n4.0 ", "cc-by-nc-4.0"),
])
def test_known_licences_keep_their_meaning(app, client, survey, entered, expected):
    with app.app_context():
        s = db.session.get(Survey, survey)
        s.consent_item.config = {**s.consent_item.config, "license": entered}
        assert zenodo.metadata(s)["license"] == expected


@pytest.mark.parametrize("guide", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_invalid_licence_never_touches_a_deposit(app, client, survey, tmp_path, guide, existing):
    api = Mock(target="sandbox")
    state = {"id": 42, "target": "sandbox", "files": ["existing.csv"]} if existing else {}
    version = {**VERSION, "zenodo": state}
    # The path deliberately does not exist: licence validation must precede file access as well.
    with app.app_context():
        s = db.session.get(Survey, survey)
        s.zenodo = dict(state)
        for entered in ("All rights reserved", "CC BY-NC-SA 4.0", "CC BY", "", " \n", None, {}, 0):
            s.consent_item.config = {**s.consent_item.config, "license": entered}
            with pytest.raises(zenodo.ZenodoError):
                if guide:
                    zenodo.upload_guide(s, api, version, tmp_path / "missing.zip")
                else:
                    zenodo.upload(s, api)
            api.request.assert_not_called()
            assert s.zenodo == state and version["zenodo"] == state
        s.consent_item.config = {k: v for k, v in s.consent_item.config.items() if k != "license"}
        with pytest.raises(zenodo.ZenodoError):
            zenodo.upload_guide(s, api, version, tmp_path / "missing.zip") if guide else zenodo.upload(s, api)
        api.request.assert_not_called()


@pytest.mark.parametrize("entered", ["CC BY-NC 4.0", "CC BY-SA 4.0", "CC0"])
def test_guide_does_not_override_a_different_known_licence(app, client, survey, tmp_path, entered):
    api = Mock(target="sandbox")
    with app.app_context():
        s = db.session.get(Survey, survey)
        s.consent_item.config = {**s.consent_item.config, "license": entered}
        with pytest.raises(zenodo.ZenodoError, match="bestätigte Materiallizenz"):
            zenodo.upload_guide(s, api, {**VERSION, "zenodo": {"id": 42, "target": "sandbox"}}, tmp_path / "missing.zip")
        api.request.assert_not_called()
        assert s.consent_item.config["license"] == entered


def test_a_data_licence_does_not_grant_a_guide_licence(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, survey)
        s.consent_item.config = {**s.consent_item.config, "license": "CC BY 4.0"}
        with pytest.raises(zenodo.ZenodoError, match="bestätigte Materiallizenz"):
            zenodo.guide_metadata(s, VERSION)


@pytest.mark.parametrize("entered", ["All rights reserved", ""])
def test_survey_route_reports_licence_error_before_oauth(app, client, survey, monkeypatch, entered):
    fill_consent(client, open_data="1", license="CC BY 4.0")
    with app.app_context():
        s = db.session.get(Survey, survey)
        s.consent_item.config = {**s.consent_item.config, "license": entered}
        s.zenodo = {"id": 42, "target": "sandbox", "state": "draft"}
        db.session.commit()
    token = Mock(side_effect=AssertionError("OAuth must not be contacted"))
    monkeypatch.setattr(zenodo_auth, "available", lambda target: True)
    monkeypatch.setattr(zenodo_auth, "access_token", token)
    page = client.post("/surveys/1/zenodo", data={"target": "sandbox", "action": "upload"}, follow_redirects=True)
    text = page.data.decode()
    assert page.status_code == 200
    assert ("Lizenz wird für den Zenodo-Upload nicht unterstützt" if entered else "fehlt eine Lizenz") in text
    token.assert_not_called()
    with app.app_context():
        assert db.session.get(Survey, survey).zenodo == {"id": 42, "target": "sandbox", "state": "draft"}


def test_guide_route_reports_missing_licence_before_oauth(app, client, monkeypatch):
    from tests.interview.test_interview import make

    code, sid = make(app, client)
    client.post(f"/guide/{code}/versions", data={"note": "saved version"})
    token = Mock(side_effect=AssertionError("OAuth must not be contacted"))
    monkeypatch.setattr(zenodo_auth, "available", lambda target: True)
    monkeypatch.setattr(zenodo_auth, "access_token", token)
    for existing in (False, True):
        state = {"id": 42, "target": "sandbox", "state": "draft"} if existing else None
        with app.app_context():
            s = db.session.get(Survey, sid)
            s.interview = {**s.interview, "versions": [{**s.interview["versions"][0], "zenodo": state}]}
            db.session.commit()
        page = client.post(f"/guide/{code}/versions/fassung-1.zip/zenodo",
                           data={"target": "sandbox", "action": "upload"}, follow_redirects=True)
        assert page.status_code == 200 and "keine bestätigte Materiallizenz" in page.data.decode()
        assert "weiterhin heruntergeladen" in page.data.decode()
        token.assert_not_called()
        with app.app_context():
            assert db.session.get(Survey, sid).interview["versions"][0]["zenodo"] == state
    assert client.get(f"/guide/{code}/versions/fassung-1.zip").status_code == 200
