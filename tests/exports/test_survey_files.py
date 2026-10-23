"""Survey files as ZIP, the preview before reading in and several surveys in one file (app/survey_files.py)."""
import io
import json
import os
import re
import zipfile

import pytest

from app import survey_files
from app.extensions import db
from app.models import Asset, Survey, User, UserLogin
from tests.editor.test_duplicate import OTHER, _as, _folder, build
from tests.exports.test_transfer import _count, _file, _pending, _preview, _upload, _media_names
from tests.conftest import CREATE_ALL


def _zip(client, url="/surveys/1/export/survey.zip", **args):
    r = client.get(url, query_string=args)
    assert r.status_code == 200 and r.mimetype == "application/zip" and "attachment" in r.headers["Content-Disposition"]
    return r.data


def _members(body):
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        return {i.filename: z.read(i) for i in z.infolist()}


def _pack(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _code(app, survey_id):
    with app.app_context():
        return db.session.get(Survey, survey_id).code


def test_a_survey_as_zip_holds_its_images_as_files_and_is_read_in_again(app, client, survey):
    build(app, client)
    body = _zip(client)
    members = _members(body)
    data = json.loads(members["survey.json"])
    images = sorted(n for n in members if n.startswith("files/"))
    assert data["files"] == {} and len(images) == 2 and "README.md" in members
    as_json = _file(client)
    assert sorted("files/" + n for n in as_json["files"]) == images
    assert len(body) < len(json.dumps(as_json).encode())                              # without base64: smaller
    assert "ist als Entwurf angelegt" in _upload(client, None, raw=body, name="b.zip", title="Aus ZIP")
    with app.app_context():
        new = db.session.get(Survey, 2)
        assets = [a for b in new.blocks for i in b.items for a in i.assets]
        assert new.title == "Aus ZIP" and len(assets) == 1 and new.theme["logo"]["file"]
        assert sorted(_media_names(_folder(app, new.uid))) == sorted([assets[0].filename, new.theme["logo"]["file"]]
                                                                    + ([new.theme["logo"]["icon"]] if new.theme["logo"].get("icon") else []))
    # without images: no folder of images
    assert not [n for n in _members(_zip(client, files="0")) if n.startswith("files/")]


def test_several_surveys_in_one_zip_and_which_are_read_in(app, client, survey):
    build(app, client)
    assert "ist als Entwurf angelegt" in _upload(client, _file(client), title="Zweite")
    page = client.get("/dashboard/").data.decode()
    assert "Mehrere als Datei exportieren" in page
    page = client.get("/dashboard/export").data.decode()
    assert "Test" in page and "Zweite" in page and 'data-check-all="survey"' in page
    # none chosen: said so; a code of somebody else's survey is no choice
    page = client.get("/dashboard/export", query_string={"download": "1", "survey": "zzzz"}, follow_redirects=True).data.decode()
    assert "mindestens eine Befragung" in page
    body = _zip(client, "/dashboard/export", download="1", survey=[_code(app, 1), _code(app, 2)], files="1")
    members = _members(body)
    jsons = sorted(n for n in members if n.endswith("/survey.json"))
    assert len(jsons) == 2 and {json.loads(members[n])["survey"]["title"] for n in jsons} == {"Test", "Zweite"}
    assert len([n for n in members if "/files/" in n]) == 4

    page, token = _preview(client, raw=body, name="viele.zip")
    assert "Die Datei enthält 2 Befragungen" in page and page.count('name="take-') == 2 and _count(app) == 2
    assert page.lower().count("gibt es bei ihnen schon") == 2                       # both are here: own titles

    def answer(**form):
        return client.post("/surveys/new/import", data={"token": token, **form}, follow_redirects=True).data.decode()

    assert "mindestens eine Befragung zum Einlesen" in answer(**{"title-0": "A", "title-1": "B"}) and _count(app) == 2
    assert "bekommt schon eine andere Befragung aus dieser Datei" in answer(**{"take-0": "1", "take-1": "1", "title-0": "Neu",
                                                                            "title-1": " neu "})
    page = answer(**{"take-0": "1", "take-1": "1", "title-0": "Neu A", "title-1": "Neu B"})
    assert "2 Befragungen sind als Entwürfe angelegt" in page and "Meine Befragungen" in page and _pending(app) == []
    with app.app_context():
        assert sorted(s.title for s in db.session.scalars(db.select(Survey))) == ["Neu A", "Neu B", "Test", "Zweite"]
    # one of several
    page, token = _preview(client, raw=body, name="viele.zip")
    assert "„Nur eine“ ist als Entwurf angelegt" in answer(**{"take-1": "1", "title-0": "x", "title-1": "Nur eine"})
    assert _count(app) == 5


def test_only_surveys_one_can_open_are_exported(app, client, survey):
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    with app.app_context():
        db.session.add(UserLogin(user_id=db.session.scalar(db.select(User.id).filter_by(orcid=OTHER)), kinds=CREATE_ALL))
        db.session.commit()
    assert "Test" not in other.get("/dashboard/export").data.decode()
    page = other.get("/dashboard/export", query_string={"download": "1", "survey": _code(app, 1)}).data.decode()
    assert "mindestens eine Befragung" in page


def test_a_pre_registration_snapshot_is_read_in(app, client, survey):
    """Its survey.json holds the images itself; the other files of the snapshot are passed over."""
    build(app, client)
    data = _file(client)
    body = _pack({"README.md": "x", "survey.json": json.dumps(data), "codebuch.json": json.dumps({"a": 1})})
    page, _ = _preview(client, raw=body, name="v1.zip")
    assert "2 Dateien" in page
    assert "ist als Entwurf angelegt" in _upload(client, None, raw=body, name="v1.zip", title="Prereg")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Asset.id))) == 2


@pytest.mark.parametrize("files, word", [
    ({"a.txt": "x"}, "keine Befragungsdatei"),
    ({"survey.json": "{}"}, "keine Befragungsdatei"),
    ({"../survey.json": '{"format": "queryous-survey", "version": 1}'}, "keine Befragungsdatei"),
    ({"a/b/survey.json": '{"format": "queryous-survey", "version": 1}'}, "keine Befragungsdatei"),
    ({"survey.json": '{"format": "queryous-survey", "version": 99}'}, "Formatversion 99"),
])
def test_what_a_zip_must_hold(app, client, survey, files, word):
    assert word in _upload(client, None, raw=_pack(files), name="x.zip") and _count(app) == 1 and _pending(app) == []


def test_a_zip_from_a_stranger_does_no_harm(app, client, survey, monkeypatch):
    assert "beschädigt" in _upload(client, None, raw=b"PK\x03\x04" + b"\x00" * 50, name="x.zip")
    # a member larger than it says, or than may be read: refused without unpacking it
    app.config["IMPORT_MAX_BYTES"] = 200_000
    data = _file(client)
    data["survey"]["description"] = " " * 300_000
    assert "zu groß oder beschädigt" in _upload(client, None, raw=_pack({"survey.json": json.dumps(data)}), name="x.zip")
    app.config["IMPORT_MAX_BYTES"] = 40 * 1024 * 1024
    # an image that unpacks to more than one upload, and names that are paths: left out, nothing written elsewhere
    build(app, client)
    members = _members(_zip(client))
    data = json.loads(members["survey.json"])
    image = next(i for b in data["blocks"] for i in b["items"] if i["type"] == "image")
    name = image["images"][0]["file"]
    members[f"files/{name}"] = b"\x00" * (app.config["MAX_CONTENT_LENGTH"] + 1)
    image["images"].append({"file": "../../x.png"})
    members["files/../../x.png"] = members[next(n for n in members if n.startswith("files/") and name not in n)]
    members["survey.json"] = json.dumps(data)
    page = _upload(client, None, raw=_pack(members), name="x.zip", title="Fremd")
    assert "ist als Entwurf angelegt" in page and page.count("Ein Bild wurde nicht übernommen") == 2
    # too many surveys in one file
    monkeypatch.setattr(survey_files, "MAX_SURVEYS", 1)
    two = _pack({"a/survey.json": json.dumps(data), "b/survey.json": json.dumps(data)})
    assert "mehr als 1 Befragungen" in _upload(client, None, raw=two, name="x.zip")


def test_an_encrypted_zip_is_refused(app, client, survey):
    body = bytearray(_pack({"survey.json": json.dumps(_file(client))}))
    central = body.index(b"PK")
    body[6] |= 0x1                                                                    # the flag "encrypted", in both headers
    body[central + 8] |= 0x1
    assert "verschlüsselt" in _upload(client, None, raw=bytes(body), name="x.zip") and _count(app) == 1


def test_export_list_tags_kind_and_archived_once(app, client, survey):
    # the kind, then the state; "Archiviert" only once (Ralf, 2026-10-04: it stood twice)
    client.post("/surveys/1/archive", data={"archived": "1"})
    html = client.get("/dashboard/export").data.decode()
    assert "Fragebogen</span>" in html and html.count("Archiviert</span>") == 1 and "archiviert</span>" not in html
