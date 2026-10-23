"""Long format of every loop with a table (bucket list 2026-10-18, schleifentabelle-export-konzept E12): one row per
person and pass with the row's values and the answers of that pass, like the long format of vignette loops."""
import csv
import io

from app import export, loops
from app.extensions import db
from app.models import Block, Survey
from tests.conftest import CONSENT_OK
from tests.editor.test_loops import setup


def _rows(text, delimiter=","):
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:]), delimiter=delimiter))


def _take_part(app, choices):
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    for choice in choices:
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data={"nav": "next", "i2": choice})


def test_one_row_per_pass_with_values_and_answers(app, client, survey):
    setup(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    _take_part(app, ["1", "2", "1", "2"])
    with app.app_context():
        s = db.session.get(Survey, 1)
        [node] = export.loop_long_nodes(s)
        rows = _rows(export.loop_long_csv(s, node))
    assert rows[0] == ["resp_id", "source", "position", "zeile", "robot", "setting", "accept"]
    assert [r[2:] for r in rows[1:]] == [
        ["1", "1", "Pflegeroboter", "zu Hause", "1"], ["2", "2", "Pflegeroboter", "im Heim", "2"],
        ["3", "3", "Serviceroboter", "zu Hause", "1"], ["4", "4", "Serviceroboter", "im Heim", "2"]]
    assert {r[0] for r in rows[1:]} == {"1"}  # resp_id as in the other long formats


def test_passes_in_the_order_shown_and_only_those(app, client, survey):
    setup(client, mode="k", k="2", order="random")
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    _take_part(app, ["2", "1"])
    with app.app_context():
        s = db.session.get(Survey, 1)
        [node] = export.loop_long_nodes(s)
        data = list(csv.DictReader(io.StringIO(export.to_csv(s)[1:])))[0]
        rows = _rows(export.loop_long_csv(s, node))[1:]
    assert len(rows) == 2 and [r[2] for r in rows] == ["1", "2"]
    assert "|".join(r[3] for r in rows) == data["vignette_order"]  # the same rows, in the same order
    assert [r[6] for r in rows] == ["2", "1"]
    for r in rows:
        assert data[f"accept_{r[3]}"] == r[6]  # the same answer as in the wide data file


def test_name_clash_and_no_long_format_for_vignette_loops(app, client, survey, monkeypatch):
    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        node = loops.node_of(db.session.get(Block, 2))
        loop = loops.config_node(node)
        loop["columns"][0]["name"] = "zeile"
        loops.save_node(s, node["id"], loop)
        db.session.commit()
        [node] = export.loop_long_nodes(s)
        assert export.loop_long_columns(s, node)[:4] == ["position", "zeile", "zeile_2", "setting"]
        from app import vignettes

        monkeypatch.setattr(vignettes, "is_vignettes", lambda n: True)  # a vignette loop keeps its own long format
        assert export.loop_long_nodes(s) == []


def test_download_page_and_packages(app, client, survey):
    setup(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    _take_part(app, ["1", "1", "2", "2"])
    with app.app_context():
        nid = export.loop_long_nodes(db.session.get(Survey, 1))[0]["id"]
    page = client.get("/surveys/1/data").data.decode()
    assert f"/surveys/1/export/looplong/{nid}.csv" in page
    r = client.get(f"/surveys/1/export/looplong/{nid}.csv?sep=semicolon")
    assert r.status_code == 200 and "-schleife-vignette-lang-" in r.headers["Content-Disposition"]
    assert _rows(r.data.decode("utf-8"), ";")[0][2:4] == ["position", "zeile"]
    assert client.get("/surveys/1/export/looplong/nope.csv").status_code == 404
    with app.app_context():
        s = db.session.get(Survey, 1)
        from app import zenodo

        files = dict(zenodo.package(s))
        assert "loop_vignette_long.csv" in files
        assert "`loop_*_long.csv`" in export.readme_md(s)
