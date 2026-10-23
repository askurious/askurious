"""The table of a loop as a file of its own (decisions.md, "Schleifentabelle als eigene Datei", 2026-10-08): one row
per pass, a column per column of the table, in the Export tab, the codebook's note, Zenodo and the pre-registration."""
import csv
import io

from werkzeug.datastructures import MultiDict

from app import export, flow, loops
from app.extensions import db
from app.models import Block, Survey
from tests.editor.test_loops import E, setup


def _rows(text, delimiter=","):
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:]), delimiter=delimiter))


def _set_table(client, url, app, cells, hidden=()):
    """Write the cells {(row, column name): text} of the default language; hidden: column names marked internal."""
    with app.app_context():
        loop = loops.config(db.session.get(Block, 2))
    form = [("name", "Vignetten"), ("code", "vignette"), ("mode", "all"), ("k", "1"), ("order", "fixed")]
    for c in loop["columns"]:
        form += [("col_id", c["id"]), ("col_name", c["name"])]
        if c["name"] in hidden:
            form.append(("col_hidden", c["id"]))
    for n, r in enumerate(loop["rows"], start=1):
        form.append(("row_id", r["id"]))
        for c in loop["columns"]:
            form.append((f"cell_{r['id']}_{c['id']}", cells.get((n, c["name"]), r["values"][c["id"]]["de"])))
    client.post(url, data=MultiDict(form))
    return loop


def test_one_row_per_pass_with_every_column_and_translations(app, client, survey):
    url = setup(client)
    tricky = 'Ein Roboter, der hilft; sagt: "Hallo"\nund geht'
    loop = _set_table(client, url, app, {(2, "setting"): tricky, (3, "robot"): "=1+1"}, hidden=("setting",))
    r0, c0 = loop["rows"][0]["id"], loop["columns"][0]["id"]
    client.post(url + "?lang=en", data={f"cell_{r0}_{c0}": "care robot"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        node = export.loop_tables(s)[0]
        rows = _rows(export.loop_table_csv(s, node, separator="semicolon"), ";")
    assert rows[0] == ["zeile", "robot", "setting", "robot_en"]  # internal: not translated
    assert [r[0] for r in rows[1:]] == ["1", "2", "3", "4"]  # the suffix _n and the numbers in vignette_order
    assert rows[1][1:] == ["Pflegeroboter", "zu Hause", "care robot"]
    assert rows[2][2] == tricky  # commas, semicolons, quotes and line breaks arrive whole
    assert rows[3][1] == "'=1+1"  # no formula in Excel
    assert rows[2][3] == ""  # not translated: empty, not the German text


def test_download_codebook_note_and_packages(app, client, survey):
    url = setup(client)
    _set_table(client, url, app, {}, hidden=("setting",))
    with app.app_context():
        nid = export.loop_tables(db.session.get(Survey, 1))[0]["id"]
    page = client.get("/surveys/1/data").data.decode()
    assert f"/surveys/1/export/loop/{nid}.csv" in page and "Schleifentabelle" in page
    r = client.get(f"/surveys/1/export/loop/{nid}.csv?sep=semicolon")
    assert r.status_code == 200 and "-vignette_table-" in r.headers["Content-Disposition"]
    assert _rows(r.data.decode("utf-8"), ";")[0][:3] == ["zeile", "robot", "setting"]
    assert client.get("/surveys/1/export/loop/nope.csv").status_code == 404
    with app.app_context():
        s = db.session.get(Survey, 1)
        note = next(row for row in export.codebook(s) if row[0] == "vignette_order")[4]
        assert "vignette_table.csv" in note and "nur intern: setting" in note
        from app import prereg, zenodo

        assert "vignette_table.csv" in dict(zenodo.package(s))
        assert "vignette_table.csv" in dict(prereg._files(s))
        assert "`*_table.csv`" in export.readme_md(s)


def test_plan_adds_deck_and_in_plan(app, client, survey):
    import copy

    from app import loop_plan

    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        root = copy.deepcopy(flow.tree(s))
        node = next(n for n, _, _ in flow.walk(root) if n["t"] == "loop")
        node["mode"] = "plan"
        node[loop_plan.KEY] = {"cols": [node["columns"][0]["id"]], "per_person": 1, "decks": 2}
        loop_plan.generate(s, node, seed=1)
        rows = export.loop_table_rows(s, node)
        assert rows[0][3:5] == ["im_plan", "deck"]
        assert sum(r[3] for r in rows[1:]) == 2 and sorted(r[4] for r in rows[1:] if r[4]) == [1, 2]


def test_copy_button_on_the_loop_page(app, client, survey):
    url = setup(client)
    page = client.get(url).data.decode()
    assert "data-copy-table" in page and "Tabelle kopieren" in page
    assert "data-copy-table" not in client.get(E + "/").data.decode()


def test_header_names_stay_unique(app, client, survey):
    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        node = export.loop_tables(s)[0]
        node["columns"][0]["name"] = "zeile"  # a column named like the first one of the file
        node["columns"][1]["name"] = "zeile_en"  # and like the translation of the renamed one
        head = export.loop_table_rows(s, node)[0]
    assert head == ["zeile", "zeile_2", "zeile_en", "zeile_en_2", "zeile_en_en"]


def test_findable_from_codebook_and_export_page(app, client, survey):
    """decisions.md, "Schleifentabelle auffindbar" (2026-10-10): the codebook names the file first, the Export page
    says the separator applies to it and copies the table as Excel copies cells."""
    import html

    url = setup(client)
    tricky = 'Ein Roboter, der hilft; sagt: "Hallo"\nund geht'
    loop = _set_table(client, url, app, {(2, "setting"): tricky}, hidden=("setting",))
    r0, c0 = loop["rows"][0]["id"], loop["columns"][0]["id"]
    client.post(url + "?lang=en", data={f"cell_{r0}_{c0}": "care robot"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        note = next(row for row in export.codebook(s) if row[0] == "vignette_order")[4]
        assert note.startswith("Tabelle mit einer Spalte je Tabellenspalte: vignette_table.csv (Reiter Export)")
        tsv = export.loop_table_tsv(s, export.loop_tables(s)[0])
    lines = tsv.split("\r\n")
    assert lines[0] == "zeile\trobot\tsetting"  # no translations: what the person sees in the table
    assert lines[1] == "1\tPflegeroboter\tzu Hause"
    assert lines[2] == '2\tPflegeroboter\t"Ein Roboter, der hilft; sagt: ""Hallo""\nund geht"'  # one cell, as Excel quotes
    page = client.get("/surveys/1/data").data.decode()
    assert "Codebuch und Schleifentabellen" in page and "als Tabelle mit Spalten unter" in page
    attr = page.split('data-copy-tsv="', 1)[1].split('"', 1)[0]
    assert html.unescape(attr).startswith("zeile\trobot\tsetting\r\n1\tPflegeroboter")
