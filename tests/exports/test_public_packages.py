"""Public and archived data packages (Zenodo, Coscine): the long formats follow data.csv in what they leave out
(open answers without the tick, E-1) and in whom they hold (unfinished participations, E-2); images.csv has the
formula protection (E-5); an upload to Coscine that breaks off is noted (E-10). Audit of 2026-10-05, package C."""
import csv
import io
import zipfile
from datetime import datetime

from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import add
from tests.analysis.test_vignettes import FORM, _add, _node

TEXT = "Ich heisse Erika Mustermann aus Aachen"


def _vignettes(app, client, *types):
    """A vignette loop around block 2 with a rating and the given questions; returns the plan's node."""
    add(client, "rating")
    for t in types:
        add(client, t)
    _add(client)
    node = _node(app)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data=FORM)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data={"action": "generate"})
    return _node(app)


def _table(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def test_vignette_long_format_leaves_out_open_answers_without_the_tick(app, client, survey):
    from app import coscine, zenodo

    node = _vignettes(app, client, "text_input")
    with app.app_context():
        text = db.session.scalar(db.select(Item).filter_by(block_id=2, type="text_input"))
        rating = db.session.scalar(db.select(Item).filter_by(block_id=2, type="rating"))
        text.code, rating.code = "warum", "gut"
        deck = node["plan"]["decks_rows"][0]
        answers = {**{f"{text.id}#{rid}": TEXT for rid in deck}, **{f"{rating.id}#{rid}": 4 for rid in deck}}
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers=answers,
                                layout={"loops": {node["id"]: list(deck)}}, started_at=datetime(2026, 10, 4, 9),
                                completed_at=datetime(2026, 10, 4, 10), pid="p0"))
        db.session.commit()
        s = db.session.get(Survey, 1)
        name = f"vignettes_{node['code']}_long.csv"
        closed = dict(zenodo.package(s, open_answers=False))
        assert TEXT not in closed["data.csv"].decode() and TEXT not in closed[name].decode()
        rows = _table(closed[name])
        assert "warum" not in rows[0] and rows[0]["gut"] == "4" and len(rows) == len(deck)  # the rest stays
        readme = closed["README.md"].decode()
        assert "Free-text answers" in readme and "`vignettes_*_long.csv`" in readme
        opened = dict(zenodo.package(s, open_answers=True))
        assert _table(opened[name])[0]["warum"] == TEXT
        # Coscine takes the same package: without "offen" no free text in any file
        parts = {n: d for n, d, _ in coscine.files(s, {"paket"})}
        assert all(TEXT not in d.decode("utf-8", "ignore") for d in parts.values())
        assert TEXT in {n: d for n, d, _ in coscine.files(s, {"paket", "offen"})}[name].decode()
        # the download for the researchers keeps everything
        from app import export

        assert TEXT in export.vignettes_long_csv(s, node)


def test_unfinished_participations_in_every_long_format(app, client, survey):
    from app import coscine

    node = _vignettes(app, client)
    with app.app_context():
        rating = db.session.scalar(db.select(Item).filter_by(block_id=2, type="rating"))
        deck = node["plan"]["decks_rows"][0]
        db.session.add(Response(survey_id=1, source="real", status=Response.ACTIVE, answers={},
                                started_at=datetime(2026, 10, 4, 8), pid="p-active"))
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED,
                                answers={f"{rating.id}#{rid}": 5 for rid in deck},
                                layout={"loops": {node["id"]: list(deck)}}, started_at=datetime(2026, 10, 4, 9),
                                completed_at=datetime(2026, 10, 4, 10), pid="p-done"))
        db.session.commit()
        s = db.session.get(Survey, 1)
        name = f"vignettes_{node['code']}_long.csv"
        files = {n: d for n, d, _ in coscine.files(s, {"paket", "abbrueche"})}
        data = {r["resp_id"]: r["status"] for r in _table(files["data.csv"])}
        assert data == {"1": "active", "2": "completed"}
        assert {r["resp_id"] for r in _table(files[name])} == {"2"}  # the completed one, as in data.csv
        readme = files["README.md"].decode()
        assert "unfinished ones included" in readme and "one row per completed participation" not in readme
        assert "`resp_id` joins them" in readme
        files = {n: d for n, d, _ in coscine.files(s, {"paket"})}
        assert {r["resp_id"] for r in _table(files[name])} == {"1"} and len(_table(files["data.csv"])) == 1
        assert "one row per completed participation" in files["README.md"].decode()


def test_images_csv_has_the_formula_protection(app, client, survey):
    import os

    from app import media, zenodo
    from app.models import Asset

    add(client, "image")
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.scalar(db.select(Item).filter_by(type="image"))
        folder = media.upload_folder(s.uid)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "a1.png"), "wb") as f:
            f.write(b"\x89PNG fake")
        item.assets.append(Asset(filename="a1.png", status=Asset.APPROVED, source="ai", prompt="=HYPERLINK(\"x\")", model="m"))
        db.session.commit()
        packed = zipfile.ZipFile(io.BytesIO(dict(zenodo.images(s))["images.zip"]))
        head, row = list(csv.reader(io.StringIO(packed.read("images.csv").decode())))[:2]
        assert row[head.index("prompt")] == "'=HYPERLINK(\"x\")"  # the rights columns follow since PR #1187


def test_broken_off_upload_is_noted(app, client, survey, monkeypatch):
    from app import coscine
    from tests.exports.test_coscine import BASE, BASIS, FakeCoscine, _basis_form, connect, on

    fake = FakeCoscine()
    app.config["COSCINE_API"] = BASE
    monkeypatch.setattr(coscine.requests, "request", fake)
    on(client)
    connect(client, fake)
    fake.profile = BASIS
    page = client.get("/coscine/survey/1?ziel=p1:r3").data.decode()
    assert 'data-confirm="In die WORM-Ressource ablegen?"' in page  # asked before the upload into WORM
    assert 'data-confirm="In die WORM' not in client.get("/coscine/survey/1?ziel=p1:r2").data.decode()
    real = fake.__call__

    def breaking(method, url, **kw):  # the third file does not arrive
        if method == "POST" and "README.md" in url:
            raise coscine.requests.ConnectionError("Netz weg")
        return real(method, url, **kw)

    monkeypatch.setattr(coscine.requests, "request", breaking)
    r = client.post("/coscine/survey/1", data=_basis_form("p1:r3", paket="1"))
    page = r.data.decode()
    assert r.status_code == 502 and "Abgebrochen nach" in page and "WORM-Ressource nicht mehr entfernen" in page
    with app.app_context():
        entry = db.session.get(Survey, 1).coscine["uploads"][0]
        assert entry["partial"] and entry["failed"] == "README.md" and entry["worm"]
        assert [f["name"] for f in entry["files"]] == ["data.csv", "codebook.csv", "questionnaire.md"]
        assert entry["planned"] == 10 and entry["folder"] in page
    page = client.get("/surveys/1/data").data.decode()
    assert "Abgebrochen: nur 3 von 10 Dateien" in page and "fehlgeschlagen bei <code>README.md</code>" in page
    # nothing in Coscine yet: an ordinary error, nothing noted
    monkeypatch.setattr(coscine.requests, "request", lambda method, url, **kw: (_ for _ in ()).throw(
        coscine.requests.ConnectionError("Netz weg")) if method == "PUT" else real(method, url, **kw))
    r = client.post("/coscine/survey/1", data=_basis_form("p1:r3", paket="1"))
    assert r.status_code == 502
    with app.app_context():
        assert len(db.session.get(Survey, 1).coscine["uploads"]) == 1
