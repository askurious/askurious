"""Side-by-side matrix (app/items/side_by_side.py): rows with several questions as columns, each with its own kind of
answer (concept side-by-side-konzept.md, E1 to E15)."""
import random

import pytest
from werkzeug.datastructures import MultiDict

from app import carry
from app.extensions import db
from app.items.base import AnswerError
from app.models import Item
from tests.conftest import CONSENT_OK, add, fill_consent


def editor_form(item_id, **extra):
    """Rows bus, bahn, rad; columns: used (ja/nein, required), satisfaction (5 points, agreement), how often
    (drop-down), cost (number in €), comment (short text)."""
    data = [("code", "mob"), ("question", "Wie sind Sie unterwegs gewesen?"), ("answer_mode", "optional")]
    for rid, code, text in (("r1", "bus", "Bus"), ("r2", "bahn", "Bahn"), ("r3", "rad", "Fahrrad")):
        data += [("row_id", rid), ("row_code", code), ("row_text", text)]
    cols = [("c1", "nutz", "single", "Genutzt?", 2, "custom", ["ja", "nein"]),
            ("c2", "zuf", "single", "Wie zufrieden?", 5, "satisfied", []),
            ("c3", "oft", "dropdown", "Wie oft?", 3, "custom", ["täglich", "wöchentlich", "seltener"]),
            ("c4", "kost", "number", "Kosten pro Monat", 3, "custom", []),
            ("c5", "anm", "text", "Anmerkung", 3, "custom", [])]
    for cid, code, kind, title, points, anchors, labels in cols:
        data += [("col_id", cid), (f"col_code_{cid}", code), (f"col_kind_{cid}", kind), (f"col_title_{cid}", title),
                 (f"col_points_{cid}", str(points)), (f"col_anchors_{cid}", anchors)]
        data += [(f"col_label_{cid}", label) for label in labels]
    data += [("col_required_c1", "1"), ("col_level_c1", "nominal"), ("col_dk_c2", "1"), ("col_level_c2", "ordinal"), ("col_number_c4", "integer"),
             ("col_min_c4", "0"), ("col_max_c4", "500"), ("col_unit_c4", "€")]
    data = [(k, v) for k, v in data if k not in extra] + list(extra.items())
    return MultiDict(data)


def setup(client, **extra):
    add(client, "side_by_side")
    with client.application.app_context():
        item_id = max(i.id for i in db.session.scalars(db.select(Item)))
    r = client.post(f"/surveys/1/editor/items/{item_id}", data=editor_form(item_id, **extra))
    assert r.status_code in (200, 302)
    return item_id


def parse(app, item_id, fields, lang="de"):
    with app.app_context():
        item = db.session.get(Item, item_id)
        return item.kind.parse_answer(item, MultiDict([(f"i{item_id}_{k}", v) for k, v in fields]), lang, answers={})


def test_the_editor_stores_rows_columns_and_labels(app, client, survey):
    item_id = setup(client)
    with app.app_context():
        item = db.session.get(Item, item_id)
        cfg = item.config
        assert [r["code"] for r in cfg["rows"]] == ["bus", "bahn", "rad"]
        assert [(c["code"], c["kind"]) for c in cfg["columns"]] == [
            ("nutz", "single"), ("zuf", "single"), ("oft", "dropdown"), ("kost", "number"), ("anm", "text")]
        nutz, zuf = cfg["columns"][0], cfg["columns"][1]
        assert nutz["points"] == 2 and nutz["required"] and zuf["dont_know"] and zuf["points"] == 5
        assert item.kind.labels(item, nutz, "de") == ["ja", "nein"]
        assert item.kind.labels(item, zuf, "de")[0] == "sehr unzufrieden"       # the answer scale of the Likert matrix
        assert item.kind.labels(item, zuf, "en")[-1] == "very satisfied"         # in every language
        assert cfg["columns"][3]["min"] == 0 and cfg["columns"][3]["max"] == 500
        assert item.kind.validate(item) == []


def test_answers_per_cell_and_required_columns(app, client, survey):
    item_id = setup(client)
    answer = parse(app, item_id, [("r1_c1", "1"), ("r1_c2", "4"), ("r1_c3", "2"), ("r1_c4", "49"), ("r1_c5", " gut "),
                                  ("r2_c1", "2"), ("r3_c1", "1"), ("r3_c2", "dk"), ("r3_c4", "12,5")][:-1])
    assert answer["r1"] == {"c1": 1, "c2": 4, "c3": 2, "c4": 49, "c5": "gut"}
    assert answer["r2"] == {"c1": 2} and answer["r3"] == {"c1": 1, "c2": "dk"}
    with pytest.raises(AnswerError, match="noch offen: 1"):                       # "Genutzt?" is required in every row
        parse(app, item_id, [("r1_c1", "1"), ("r2_c1", "1")])
    with pytest.raises(AnswerError, match="ganze Zahl"):
        parse(app, item_id, [("r1_c1", "1"), ("r2_c1", "1"), ("r3_c1", "1"), ("r3_c4", "12,5")])
    with pytest.raises(AnswerError, match="von 0 bis 500 ein"):
        parse(app, item_id, [("r1_c1", "1"), ("r2_c1", "1"), ("r3_c1", "1"), ("r3_c4", "900")])
    with pytest.raises(AnswerError, match="Ungültige Antwort"):                   # no third answer to "ja/nein"
        parse(app, item_id, [("r1_c1", "3"), ("r2_c1", "1"), ("r3_c1", "1")])


def test_export_column_by_column_with_missing_codes(app, client, survey):
    item_id = setup(client)
    with app.app_context():
        item = db.session.get(Item, item_id)
        cols = [c for c, _ in item.kind.export_columns(item)]
        assert cols[:4] == ["mob_nutz_bus", "mob_nutz_bahn", "mob_nutz_rad", "mob_zuf_bus"]   # column by column (E8)
        assert cols[-1] == "mob_anm_rad" and len(cols) == 15
        labels = dict(item.kind.export_columns(item))
        assert labels["mob_zuf_bahn"] == "Bahn – Wie zufrieden?"
        values = item.kind.export_values(item, {"r1": {"c1": 1, "c2": 4, "c4": 49}, "r3": {"c2": "dk"}})
        assert values["mob_nutz_bus"] == 1 and values["mob_zuf_bus"] == 4 and values["mob_kost_bus"] == 49
        assert values["mob_zuf_rad"] == -98 and values["mob_nutz_bahn"] is None
        assert item.kind.missing_codes(item, "mob_zuf_bus") == (-98,)
        assert item.kind.column_level(item, "mob_nutz_bus") == "nominal"
        assert item.kind.column_level(item, "mob_zuf_bus") == "ordinal"
        assert item.kind.column_level(item, "mob_kost_bus") == "metric"
        assert item.kind.result_column(item, "mob_kost_bus") == ("auto", "€", None)
        assert item.kind.lists_texts(item, "mob_anm_bus")


def test_multiple_choice_column_has_one_column_per_answer(app, client, survey):
    item_id = setup(client, col_kind_c3="multi")
    with app.app_context():
        item = db.session.get(Item, item_id)
        cols = [c for c, _ in item.kind.export_columns(item)]
        assert "mob_oft_bus_1" in cols and "mob_oft_rad_3" in cols
    answer = parse(app, item_id, [("r1_c1", "1"), ("r2_c1", "1"), ("r3_c1", "1"), ("r1_c3", "1"), ("r1_c3", "3")])
    assert answer["r1"]["c3"] == [1, 3]
    with app.app_context():
        item = db.session.get(Item, item_id)
        values = item.kind.export_values(item, answer)
        assert (values["mob_oft_bus_1"], values["mob_oft_bus_2"], values["mob_oft_bus_3"]) == (1, 0, 1)
        # an answered row without a tick: nothing ticked, 0 (audit 2026-10-10, N-5); a row left out: missing
        assert values["mob_oft_bahn_1"] == 0
        assert item.kind.export_values(item, {"r1": answer["r1"]})["mob_oft_bahn_1"] is None


def test_dummy_answers_pass_the_check(app, client, survey):
    item_id = setup(client)
    with app.app_context():
        item = db.session.get(Item, item_id)
        rng = random.Random(5)
        for _ in range(20):
            fields = item.kind.dummy_fields(item, rng, {}, answers={})
            assert set(item.kind.parse_answer(item, MultiDict(fields), "de", answers={})) >= {"r1", "r2", "r3"}


def test_the_page_shows_rows_and_columns(app, client, survey):
    fill_consent(client)
    item_id = setup(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=CONSENT_OK)
    page = p.get("/s/1").data.decode()
    assert "data-sbs" in page and "Kosten pro Monat" in page and "sehr unzufrieden" in page
    assert f'name="i{item_id}_r2_c3"' in page and "<legend class=\"p-sbs-label\">" in page
    r = p.post("/s/1", data={"nav": "next", f"i{item_id}_r1_c1": "1"})
    assert "noch offen: 2" in r.data.decode() or "noch offen: 2" in p.get("/s/1").data.decode()


def run(app, client, answers, **extra):
    """Start the survey with the side-by-side matrix and send `answers` (lists of (cell, value)), one participation each."""
    fill_consent(client)
    item_id = setup(client, **extra)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    for cells in answers:
        p = app.test_client()
        p.get("/s/1")
        p.post("/s/1", data=CONSENT_OK)
        p.get("/s/1")
        p.post("/s/1", data=MultiDict([("nav", "next")] + [(f"i{item_id}_{k}", v) for k, v in cells]))
    return item_id


def test_editor_page_and_results_page(app, client, survey):
    run(app, client, [[("r1_c1", "1"), ("r2_c1", "1"), ("r3_c1", "2"), ("r1_c2", "5"), ("r2_c2", "4"), ("r1_c3", "1"),
                       ("r1_c4", "49"), ("r1_c5", "pünktlich")],
                      [("r1_c1", "1"), ("r2_c1", "2"), ("r3_c1", "2"), ("r1_c2", "dk"), ("r1_c4", "30")]])
    editor = client.get("/surveys/1/editor/items/2").data.decode()
    assert "data-sbs-col-editor" in editor and "Frage der Spalte" in editor
    page = client.get("/surveys/1/results").data.decode()
    assert "Bus – Wie zufrieden?" in page and "Bahn – Genutzt?" in page
    assert "sehr zufrieden" in page and "täglich" in page


def test_scale_columns_count_as_matrices_for_the_answer_patterns(app, client, survey):
    from app import quality

    item_id = setup(client)
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert quality.scale_columns(item) == [(5, "c2")]                       # not ja/nein, not the list
        seq = quality.sequence(item, {"r1": {"c2": 3}, "r2": {"c2": 3}, "r3": {"c2": "dk"}})
        assert quality.matrices_of(item, seq) == [(5, [3, 3, None])]


def test_rows_shown_only_to_some_are_exported_as_not_shown(app, client, survey):
    item_id = setup(client)
    with app.app_context():
        item = db.session.get(Item, item_id)
        rows = [dict(r) for r in item.config["rows"]]
        rows[1]["when"] = {"item": "p:auto", "op": "eq", "value": "1"}
        item.config = {**item.config, "rows": rows}
        db.session.commit()
        answer = item.kind.parse_answer(item, MultiDict([(f"i{item_id}_r1_c1", "1"), (f"i{item_id}_r3_c1", "2")]), "de",
                                        answers={"p:auto": "0"})
        assert answer["_hidden"] == ["r2"]                                          # not asked, so not open either
        values = item.kind.export_values(item, answer)
        assert values["mob_nutz_bahn"] == -77 and values["mob_zuf_bahn"] == -77 and values["mob_nutz_rad"] == 2
        assert item.kind.missing_codes(item, "mob_zuf_bahn") == (-77, -98)
        typ, labels, notes = item.kind.codebook_info(item, "mob_zuf_bahn", "de", "de")
        assert typ == "Auswahl" and "-98 = weiß nicht" in labels and "-77 = nicht gezeigt" in labels


def test_a_new_number_of_answers_relabels_every_language(app, client, survey):
    item_id = setup(client)
    client.post(f"/surveys/1/editor/items/{item_id}", data=editor_form(item_id, col_points_c2="7"))
    with app.app_context():
        item = db.session.get(Item, item_id)
        zuf = item.kind.column(item, "c2")
        assert zuf["points"] == 7 and len(item.kind.labels(item, zuf, "de")) == 7
        assert item.kind.labels(item, zuf, "en")[3] == "neither satisfied nor dissatisfied"

def test_a_column_is_asked_only_after_an_answer_to_its_left(app, client, survey):
    """E10: "Wie zufrieden?" and "Kosten" only where "Genutzt? = ja"; the server drops what was sent anyway (no
    script), such cells are not open, and the export says -77."""
    item_id = setup(client, col_when_c2="c1:1")
    client.post(f"/surveys/1/editor/items/{item_id}", data=editor_form(item_id, col_when_c2="c1:1", col_when_c4="c1:1"))
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.kind.column(item, "c2")["when"] == {"col": "c1", "codes": [1]}
        assert item.kind.column(item, "c4")["when"] == {"col": "c1", "codes": [1]}
        assert "when" not in item.kind.column(item, "c3") and item.kind.validate(item) == []
        assert item.kind.when_text(item, item.kind.column(item, "c2"), "de") == "„Genutzt?“ = ja"
    answer = parse(app, item_id, [("r1_c1", "1"), ("r1_c2", "4"), ("r2_c1", "2"), ("r2_c2", "5"), ("r2_c4", "20"),
                                  ("r3_c1", "1")])
    assert answer["r1"] == {"c1": 1, "c2": 4} and answer["r2"] == {"c1": 2}        # bahn not used: no satisfaction
    with app.app_context():
        item = db.session.get(Item, item_id)
        item.config = {**item.config, "columns": [{**c, "required": c["required"] or c["id"] == "c2"} for c in item.config["columns"]]}
        db.session.commit()
    with pytest.raises(AnswerError, match="noch offen: 1"):                       # only rad, used and not rated
        parse(app, item_id, [("r1_c1", "1"), ("r1_c2", "4"), ("r2_c1", "2"), ("r3_c1", "1")])
    with app.app_context():
        item = db.session.get(Item, item_id)
        values = item.kind.export_values(item, answer)
        assert values["mob_zuf_bus"] == 4 and values["mob_zuf_bahn"] == -77 and values["mob_kost_bahn"] == -77
        assert values["mob_kost_bus"] is None and values["mob_oft_bahn"] is None   # asked, not answered
        assert item.kind.missing_codes(item, "mob_kost_bus") == (-77,)
        typ, labels, notes = item.kind.codebook_info(item, "mob_zuf_bus", "de", "de")
        assert "-77 = nicht gezeigt" in labels and any("nur gefragt, wenn „Genutzt?“ = ja" in n for n in notes)
        assert "nur gefragt, wenn" in dict(item.kind.export_columns(item))["mob_zuf_bus"]


def test_a_condition_falls_with_its_column(app, client, survey):
    """A condition only on a choice column to its left: it goes when that column becomes a number or is removed."""
    item_id = setup(client, col_when_c2="c1:1")
    with app.app_context():
        assert db.session.get(Item, item_id).kind.column(db.session.get(Item, item_id), "c2")["when"]["col"] == "c1"
    client.post(f"/surveys/1/editor/items/{item_id}", data=editor_form(item_id, col_when_c2="c3:1", col_when_c3="c4:1"))
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert "when" not in item.kind.column(item, "c2") and "when" not in item.kind.column(item, "c3")
    client.post(f"/surveys/1/editor/items/{item_id}", data=editor_form(item_id, col_when_c4="c3:2"))
    client.post(f"/surveys/1/editor/items/{item_id}", data=editor_form(item_id, col_when_c4="c3:2", col_remove_c3="1"))
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert "when" not in item.kind.column(item, "c4")


def test_the_page_switches_off_cells_by_script(app, client, survey):
    item_id = setup(client, col_when_c2="c1:1")
    from tests.ui.test_accessibility_rules import render

    html = render(app, item_id, errors=False)
    assert 'data-sbs-when="{&#34;codes&#34;: [1], &#34;col&#34;: &#34;c1&#34;}"' in html or "data-sbs-when=" in html
    assert "entfällt" in html and "Die weiteren Fragen entfallen." in html


def test_one_dashboard_tile_per_column(app, client, survey):
    """E15: a choice column as a scale over the rows, a number as the mean per row, a short text as words."""
    from app import boards
    from app.models import Board, Survey
    from tests.dashboards.test_boards import _answer

    app.config["BOARD_CHECK_SECONDS"] = 0
    item_id = setup(client, col_level_c2="metric")
    with app.app_context():
        survey_ = db.session.get(Survey, 1)
        rows = {r["key"]: r for g in boards.candidates(survey_) for r in g["rows"] if r["key"].startswith(f"item:{item_id}")}
    assert rows[f"item:{item_id}@c-c1"]["shape"] == "scale" and "means" not in [v for v, _ in rows[f"item:{item_id}@c-c1"]["views"]]
    assert "means" in [v for v, _ in rows[f"item:{item_id}@c-c2"]["views"]]
    assert rows[f"item:{item_id}@c-c4"]["shape"] == "nums" and rows[f"item:{item_id}@c-c5"]["shape"] == "words"
    assert rows[f"item:{item_id}@c-c2"]["label"].endswith("· Wie zufrieden?")
    _answer(app, {str(item_id): {"r1": {"c1": 1, "c2": 4, "c4": 40}, "r2": {"c1": 2}}}, n=6)
    _answer(app, {str(item_id): {"r1": {"c1": 1, "c2": 5, "c4": 60}, "r2": {"c1": 1, "c2": "dk"}}}, n=6)
    client.post("/surveys/1/dashboards/new", data={"template": "empty"})
    client.post("/surveys/1/dashboards/1", data=MultiDict([
        ("name", "A"), ("pick", f"item:{item_id}@c-c2"), (f"view-item:{item_id}@c-c2", "table"), (f"n-item:{item_id}@c-c2", "1"),
        ("pick", f"item:{item_id}@c-c4"), (f"view-item:{item_id}@c-c4", "table"), (f"n-item:{item_id}@c-c4", "1")]))
    with app.app_context():
        tiles = {t["key"]: t for t in boards.state(db.session.get(Board, 1), fresh=True)["tiles"]}
    zuf, kost = tiles[f"item:{item_id}@c-c2"], tiles[f"item:{item_id}@c-c4"]
    assert zuf["title"].endswith("· Wie zufrieden?") and zuf["rows"][0]["label"] == "Bus" and zuf["rows"][0]["n"] == 12
    assert kost["rows"][0]["label"] == "Bus" and kost["rows"][0]["n"] == 12
    page = client.get("/surveys/1/dashboards/1/view").data.decode()
    assert "Wie zufrieden?" in page and "Kosten pro Monat" in page


def test_later_questions_take_the_rows_with_one_answer_in_a_column(app, client, survey):
    """E15: "Wie zufrieden?" (a Likert matrix on the next page) only for the means of transport used ("Genutzt? =
    ja"), and a second matrix for those not used."""
    from tests.editor.test_carry import start

    fill_consent(client)
    item_id = setup(client)                                                         # 2
    add(client, "page_break")                                                       # 3
    add(client, "likert")                                                           # 4
    add(client, "multiple_choice")                                                  # 5
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "zz"), ("question", "Wie zufrieden?"), ("points", "5"), ("anchors", "satisfied"), ("answer_mode", "optional"),
        ("carry_item", f"{item_id}:c1:1"), ("carry_which", "chosen")]))
    client.post("/surveys/1/editor/items/5", data=MultiDict([
        ("code", "warum"), ("question", "Was hätten Sie gern genutzt?"), ("answer_mode", "optional"),
        ("carry_item", f"{item_id}:c1:1"), ("carry_which", "unchosen")]))
    with app.app_context():
        likert = db.session.get(Item, 4)
        assert likert.config["carry"] == {"item": item_id, "which": "chosen", "col": "c1", "value": 1}
        assert [s["code"] for s in likert.config["statements"]] == ["zz_bus", "zz_bahn", "zz_rad"]
        assert likert.text("statements")["cf_r2"] == "Bahn" and likert.kind.validate(likert) == []
        assert carry.problems(likert) == [] and carry.editor_view(likert, "de")["item"] == f"{item_id}:c1:1"
        assert [o["code"] for o in db.session.get(Item, 5).config["options"]] == ["bus", "bahn", "rad"]
    editor = client.get("/surveys/1/editor/items/4").data.decode()
    assert "mob · Wie sind Sie unterwegs gewesen? · Genutzt? = ja" in editor
    p, uid = start(app, client)
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=MultiDict([("nav", "next"), (f"i{item_id}_r1_c1", "1"), (f"i{item_id}_r2_c1", "2"),
                                        (f"i{item_id}_r3_c1", "1"), (f"i{item_id}_r3_c2", "dk")]))
    html = p.get(f"/s/{uid}").data.decode()
    assert 'name="i4_cf_r1"' in html and 'name="i4_cf_r3"' in html and 'name="i4_cf_r2"' not in html
    assert [v for v in ("bus", "bahn", "rad") if f'name="i5" value="{v}"' in html] == ["bahn"], html[html.find('name="i5"') - 200:][:600]


def test_learning_fold_in_the_editor_and_hint_in_the_start_list(app, client, survey):
    """E14: the fold "Side-by-Side oder einzelne Fragen?" in the editor, and the start list names matrices with more
    than three columns."""
    from app import learning
    from app.models import Survey

    item_id = setup(client)                                                         # five columns
    editor = client.get(f"/surveys/1/editor/items/{item_id}").data.decode()
    assert "Side-by-Side oder einzelne Fragen?" in editor and "Revilla" in editor
    with app.app_context():
        checks = learning.method_checks(db.session.get(Survey, 1), 0, 0)
        assert any(key == "side_by_side" and not ok and "„mob“" in text for ok, text, _, _, key in checks)


def test_piping_names_every_cell(app, client, survey):
    """E15: {{mob_zuf_bus}} in a later text is the label of the answer in that cell."""
    from app import piping
    from app.models import Survey

    item_id = setup(client)
    with app.app_context():
        found = piping.sources(db.session.get(Survey, 1))
        assert {"mob_zuf_bus", "mob_kost_rad", "mob_anm_bahn"} <= set(found)
        values = piping.values(db.session.get(Survey, 1), {item_id: {"r1": {"c2": 4, "c4": 49}}, str(item_id): {"r1": {"c2": 4, "c4": 49}}}, "de")
        assert str(values["mob_zuf_bus"]) == "eher zufrieden" and str(values["mob_kost_bus"]) == "49"



def test_dashboard_tiles_of_tick_boxes_and_short_texts(app, client, survey):
    """Tick boxes: the share of each answer per row; short texts: the words named in any row."""
    from app import boards
    from app.models import Board
    from tests.dashboards.test_boards import _answer

    app.config["BOARD_CHECK_SECONDS"] = 0
    item_id = setup(client, col_kind_c3="multi")
    _answer(app, {str(item_id): {"r1": {"c1": 1, "c3": [1, 3], "c5": "pünktlich"}, "r2": {"c1": 1, "c3": [2], "c5": "voll"}}}, n=6)
    _answer(app, {str(item_id): {"r1": {"c1": 1, "c3": [1], "c5": "Pünktlich"}}}, n=6)
    client.post("/surveys/1/dashboards/new", data={"template": "empty"})
    keys = [f"item:{item_id}@c-c3", f"item:{item_id}@c-c5"]
    client.post("/surveys/1/dashboards/1", data=MultiDict([("name", "A")] + [x for k, v in zip(keys, ("table", "table"))
                                                                           for x in (("pick", k), (f"view-{k}", v), (f"n-{k}", "1"))]))
    with app.app_context():
        tiles = {t["key"]: t for t in boards.state(db.session.get(Board, 1), fresh=True)["tiles"]}
    multi, words = tiles[keys[0]], tiles[keys[1]]
    assert multi["shape"] == "multi" and multi["n"] == 12 and not multi.get("few")
    assert words["shape"] == "words" and words["n"] == 12
    page = client.get("/surveys/1/dashboards/1/view").data.decode()
    assert "Bus: täglich" in page and "pünktlich" in page.lower()


def test_results_look_up_the_cells_once(app, client, survey, monkeypatch):
    """Code review 2026-10-15, A9: every column of the results page searched all cells of the matrix (up to 4000
    columns, quadratic); while results.compute runs they are listed once per element."""
    from app import results
    from app.items.side_by_side import SideBySide
    from app.models import Response, Survey

    item_id = setup(client)
    with app.app_context():
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, pid="p1",
                                answers={str(item_id): {"r1": {"c1": 1, "c2": 4}}}, layout={}))
        db.session.commit()
        listed = []
        cells = SideBySide._list_cells
        monkeypatch.setattr(SideBySide, "_list_cells", lambda self, item: listed.append(1) or cells(self, item))
        entry = next(e for e in results.compute(db.session.get(Survey, 1))["items"] if e["item"].id == item_id)
        assert entry and listed == [1, 1]  # once reading the answers (export.rows has its own plan), once for the page
        item = db.session.get(Item, item_id)
        listed.clear()
        assert item.kind.column_level(item, "nichtda") is None and listed == [1]  # outside a computation: built anew
