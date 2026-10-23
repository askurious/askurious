"""Coscine (app/coscine.py), Coscine-Ausbau 2026-10-20, package G: a survey file kept in Coscine read in through
"Aus einer Datei anlegen", with the same preview as an upload (wizard.import_file). Coscine's API is replaced by
FakeCoscine."""
import json
import re

from app.extensions import db
from app.models import Survey
from tests.exports.test_coscine import connect, fake  # noqa: F401 - fake is a fixture
from tests.exports.test_transfer import _file


def _put(fake, path, data):
    fake.files[path], fake.where[path] = data, "r1"


def test_survey_file_from_coscine_goes_through_the_preview(app, client, survey, fake):
    data = _file(client)
    connect(client, fake)
    _put(fake, "befragungen/akzeptanz.json", json.dumps(data).encode())
    _put(fake, "befragungen/notiz.txt", b"x")
    page = client.get("/surveys/new?start=datei").data.decode()
    assert 'data-coscine-pick="survey"' in page
    files = client.get("/coscine/pick?art=survey&ziel=p1:r1").get_json()["files"]
    assert [f["path"] for f in files] == ["befragungen/akzeptanz.json"]
    page = client.post("/surveys/new/import", data={"coscine_ziel": "p1:r1", "coscine_pfad": "befragungen/akzeptanz.json"}).data.decode()
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    r = client.post("/surveys/new/import", data={"token": token, "title-0": "Aus Coscine"})
    assert r.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(Survey).filter_by(title="Aus Coscine")) is not None


def test_refused_survey_files(app, client, survey, fake):
    r = client.post("/surveys/new/import", data={"coscine_ziel": "p1:r1", "coscine_pfad": "x.json"}, follow_redirects=True)
    assert "Coscine-Token hinterlegen" in r.data.decode()  # no token yet
    connect(client, fake)
    for path in ("bild.png", "../x.json"):
        r = client.post("/surveys/new/import", data={"coscine_ziel": "p1:r1", "coscine_pfad": path}, follow_redirects=True)
        assert "Diese Datei passt hier nicht." in r.data.decode()
    _put(fake, "kaputt.json", b"{kein json")
    r = client.post("/surveys/new/import", data={"coscine_ziel": "p1:r1", "coscine_pfad": "kaputt.json"}, follow_redirects=True)
    assert 'name="token"' not in r.data.decode()
    app.config["IMPORT_MAX_BYTES"] = 10
    _put(fake, "gross.json", b"{}" * 20)
    r = client.post("/surveys/new/import", data={"coscine_ziel": "p1:r1", "coscine_pfad": "gross.json"}, follow_redirects=True)
    assert "größer als" in r.data.decode()
    assert client.get("/coscine/pick?art=survey").status_code == 200
