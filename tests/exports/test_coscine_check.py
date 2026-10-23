"""Coscine (app/coscine.py, experimental), Coscine-Ausbau 2026-10-20, package B (K2): an upload checked against its
folder in Coscine (presence and size, on request SHA-256), and every upload folder of a survey in the resource.
Coscine's API is replaced by FakeCoscine of test_coscine.py."""
from app.extensions import db
from app.models import Survey
from tests.exports.test_coscine import connect, fake, on  # noqa: F401 - fake is a fixture
from tests.exports.test_coscine_token_space import _basis_form


def _stored(app, client, fake):
    from tests.conftest import add

    on(client)
    add(client, "likert")
    connect(client, fake)
    client.post("/coscine/survey/1", data=_basis_form("p1:r2"), follow_redirects=True)
    with app.app_context():
        entry = db.session.get(Survey, 1).coscine["uploads"][0]
    assert (entry["project_id"], entry["resource_id"]) == ("p1", "r2") and fake.files
    return entry


def _check(client, entry, deep=False):
    data = {"ordner": entry["folder"], **({"gruendlich": "1"} if deep else {})}
    return client.post("/coscine/survey/1/check", data=data).data.decode()


def test_check_an_upload_in_coscine(app, client, survey, fake):
    entry = _stored(app, client, fake)
    assert "In Coscine prüfen" in client.get("/surveys/1/data").data.decode()
    page = _check(client, entry)
    assert "Vollständig: Alle Dateien sind in Coscine, ihre Größen stimmen." in page and "Gründlich prüfen" in page
    assert "geprüft: vollständig" in client.get("/surveys/1/data").data.decode()

    folder = entry["folder"]
    del fake.files[f"{folder}/README.md"]
    fake.files[f"{folder}/data.csv"] += b"\n1,2,3"
    fake.files[f"{folder}/notiz.txt"] = b"von Hand in Coscine"
    fake.where[f"{folder}/notiz.txt"] = "r2"
    page = _check(client, entry)
    assert "2 von 7 Dateien fehlen in Coscine oder weichen ab." in page
    assert "<strong>fehlt</strong>" in page and "<strong>geändert</strong>" in page
    assert "zusätzlich, nicht von Askurious abgelegt" in page
    with app.app_context():
        check = db.session.get(Survey, 1).coscine["uploads"][0]["check"]
    assert check["ok"] is False and check["extra"] == 1 and check["deep"] is False
    assert "Dateien fehlen oder weichen ab" in client.get("/surveys/1/data").data.decode()


def test_thorough_check_compares_checksums(app, client, survey, fake):
    entry = _stored(app, client, fake)
    name = f"{entry['folder']}/codebook.csv"
    fake.files[name] = bytes(reversed(fake.files[name]))  # same size, other content
    assert "Vollständig" in _check(client, entry)          # the quick check sees only the size
    page = _check(client, entry, deep=True)
    assert "1 von 7 Dateien fehlen in Coscine oder weichen ab." in page and "Gründlich geprüft" in page
    with app.app_context():
        assert db.session.get(Survey, 1).coscine["uploads"][0]["check"]["deep"] is True


def test_every_folder_of_the_survey_in_the_resource(app, client, survey, fake):
    entry = _stored(app, client, fake)
    other = entry["folder"].rsplit("/", 1)[0] + "/2025-01-01_120000"
    fake.files[f"{other}/data.csv"], fake.where[f"{other}/data.csv"] = b"alt", "r2"
    page = client.get("/coscine/survey/1/resource").data.decode()
    assert entry["folder"] in page and other in page and page.count("nicht in der Liste von Askurious") == 1
    assert "Alle Ablagen dieser Befragung in der Ressource ansehen" in client.get("/surveys/1/data").data.decode()


def test_check_needs_the_role_and_a_known_folder(app, client, survey, fake):
    entry = _stored(app, client, fake)
    assert client.post("/coscine/survey/1/check", data={"ordner": "askurious/x/y"}).status_code == 404
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members[0].role = "editor"
        db.session.commit()
    assert client.post("/coscine/survey/1/check", data={"ordner": entry["folder"]}).status_code in (302, 403, 404)
    assert client.get("/coscine/survey/1/resource").status_code in (302, 403, 404)
