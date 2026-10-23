"""Categorised open answers (app/coding.py, stage 2) in the group comparison of the results page (app/compare.py) and
as a dashboard tile (app/boards/): the categories per group as for multiple choice (several per answer) or single
choice (one), only the categorised answers as the base, "Sonstiges" and "nicht verwertbar" last; on a dashboard only
counts, never the texts, with the rules for small cells."""
import json

from werkzeug.datastructures import MultiDict

from app import boards, compare
from app.extensions import db
from app.models import Board, BoardShare, Response, Survey
from tests.conftest import add, experiments_on

D = "/surveys/1/dashboards"
# (group, text): group 1 Ost, 2 West; "neu" stays uncategorised. Preis 5, Lieferung 4, nicht verwertbar 5
ROWS = ([("1", "Zu teuer")] * 4 + [("1", "Lieferung")] + [("1", "Unklar Zeppelin")] * 2
        + [("2", "Zu teuer")] + [("2", "Lieferung")] * 3 + [("2", "Unklar Zeppelin")] * 3 + [("2", "neu")])


def _survey(app, client, rows=ROWS):
    """Survey 1: a single choice (item 2, the groups Ost and West) and a free text (item 3, key "3") with the
    categories Preis (1) and Lieferung (2); "unklar" is "nicht verwertbar" (99)."""
    app.config["BOARD_CHECK_SECONDS"] = 0
    experiments_on(client, "coding")
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "grp"), ("question", "Gruppe?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Ost"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "West")]))
    add(client, "text_input")
    client.post("/surveys/1/editor/items/3", data={"code": "why", "question": "Warum?"})
    with app.app_context():
        for n, (group, text) in enumerate(rows):
            db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, pid=f"p{n}",
                                    qversion=1, answers={"2": group, "3": text}, layout={}))
        db.session.commit()
    for name in ("Preis", "Lieferung"):
        client.post("/surveys/1/categorise/3/add", data={"name": name})
    for norm, cat in (("zu teuer", 1), ("lieferung", 2), ("unklar zeppelin", 99)):
        r = client.post("/surveys/1/categorise/3/assign", data={"norm": norm, "cat": [str(cat)]},
                        headers={"Accept": "application/json"})
        assert r.get_json()["status"] == "saved"


def _single(client):
    client.post("/surveys/1/categorise/3/scheme", data={"single": "1"})


def _compare(app):
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        f = {"sources": ["real"], "all": False, "drop": False, "speeder": None}
        table = compare.table_of(s, f)
        return compare.compute(s, compare.chosen(s, "2"), results.compute(s), table)


def test_categories_per_group_as_multiple_choice(app, client, survey):
    _survey(app, client)
    cmp = _compare(app)
    (block,) = cmp["items"][3]
    assert block["kind"] == "multi" and block["coding"] and block["code"] == "why"
    assert [label for _, label in block["groups"]] == ["Ost", "West"]
    assert block["n"] == [7, 7]                      # the base: categorised answers only, "neu" is left out
    # the most frequent first, "nicht verwertbar" last although it is named as often as Preis; Sonstiges unnamed
    assert [r["label"] for r in block["rows"]] == ["Preis", "Lieferung", "nicht verwertbar"]
    preis = block["rows"][0]
    assert preis["cells"] == [4, 1] and preis["base"] == [7, 7] and abs(preis["shares"][0] - 4 / 7) < 1e-9
    assert len(preis["ci"]) == 2 and preis["test"]["df"] == 1
    # the overview of the page names it as categories
    with app.app_context():
        from app import results

        rows = compare.overview(cmp, results.compute(db.session.get(Survey, 1))["items"])["rows"]
    row = next(r for r in rows if r["item_id"] == 3)
    assert row["coding"] and row["option"]["of"] == 3
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    assert "Nach Gruppen: Gruppe?" in html and "Basis: Personen mit kategorisierter Antwort" in html
    assert "ausgewählt als größter Unterschied von 3 Kategorien" in html


def test_one_category_per_answer_as_single_choice(app, client, survey):
    _survey(app, client)
    _single(client)
    (block,) = _compare(app)["items"][3]
    assert block["kind"] == "nominal" and block["coding"] and block["single"]
    assert block["n"] == [7, 7]
    assert [r["label"] for r in block["rows"]] == ["Preis", "Lieferung", "nicht verwertbar"]
    assert [r["cells"] for r in block["rows"]] == [[4, 1], [1, 3], [2, 3]]
    assert all(abs(sum(r["shares"][j] for r in block["rows"]) - 1) < 1e-9 for j in range(2))  # column percentages
    assert block["test"]["df"] == 2
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    assert "Anteil der Kategorie an den kategorisierten Antworten der Gruppe" in html


def test_too_few_categorised_answers_in_a_group(app, client, survey):
    rows = [("1", "Zu teuer")] * 6 + [("2", "Zu teuer")] * 2 + [("2", "neu")] * 5
    _survey(app, client, rows)
    (block,) = _compare(app)["items"][3]
    # West has seven people but only two categorised answers: not shown on its own
    assert [label for _, label in block["groups"]] == ["Ost"] and block["n"] == [6]


def _tile_board(client, key, **extra):
    client.post(f"{D}/new", data={"template": "empty"})
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", key), (f"view-{key}", "bars"), (f"n-{key}", "1"),
                                          (f"pct-{key}", "1")] + list(extra.items())))


def _tile(app, key):
    with app.app_context():
        return next(t for t in boards.state(db.session.get(Board, 1), fresh=True)["tiles"] if t["key"] == key)


def test_dashboard_tile_with_the_categories(app, client, survey):
    _survey(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = next(i for b in s.blocks for i in b.items if i.id == 3)
        key = boards.coding_key(item, "why")
        known = boards._known(s)
    assert key == "item:3@k" and known[key]["shape"] == "multi"
    _tile_board(client, key)
    form = client.get(f"{D}/1").data.decode()
    assert f'value="{key}"' in form and "kategorisierte offene Antworten" in form
    tile = _tile(app, key)
    assert tile["title"] == "Warum? · Kategorien" and tile["code"] == "why" and tile["n"] == 14
    # Preis 5 and nicht verwertbar 5 of 14, Lieferung 4: a small cell, no number (last); Sonstiges before nicht verwertbar
    got = [(r["label"], r.get("count")) for r in tile["rows"]]
    assert got == [("Preis", 5), ("Sonstiges", 0), ("nicht verwertbar", 5), ("Lieferung", None)]
    # a link shows counts, never the texts
    client.post(f"{D}/1/link", data={"valid": "30"})
    with app.app_context():
        token = db.session.scalars(db.select(BoardShare)).first().token
        with open(boards._path(db.session.get(Board, 1)), encoding="utf-8") as f:
            kept = f.read()
    page = app.test_client().get(f"/d/{token}").data.decode()
    assert "Warum? · Kategorien" in page and "Preis" in page
    assert "Zeppelin" not in page and "Zeppelin" not in kept and '"_' not in kept


def test_dashboard_tile_one_category_puts_small_cells_together(app, client, survey):
    _survey(app, client)
    _single(client)
    with app.app_context():
        assert boards._known(db.session.get(Survey, 1))["item:3@k"]["shape"] == "cats"
    _tile_board(client, "item:3@k")
    tile = _tile(app, "item:3@k")
    assert tile["n"] == 14 and tile["shape"] == "cats"
    # Preis 5, nicht verwertbar 5, Lieferung 4: the four are put together with the next smallest
    labels = [r["label"] for r in tile["rows"]]
    assert "Lieferung" not in labels and "Weitere Antworten" in labels
    assert sum(r["count"] for r in tile["rows"]) == 14
    state = json.dumps(tile)
    assert "Zeppelin" not in state


def test_dashboard_tile_split_by_groups(app, client, survey):
    rows = [("1", "Zu teuer")] * 8 + [("1", "Lieferung")] * 5 + [("2", "Zu teuer")] * 5 + [("2", "Lieferung")] * 7
    _survey(app, client, rows)
    _tile_board(client, "item:3@k", **{"by-item:3@k": "item:2"})
    tile = _tile(app, "item:3@k")
    assert tile["split"] and [(g["label"], g["n"]) for g in tile["split"]["groups"]] == [("Ost", 13), ("West", 12)]
    shares = {r["label"]: r["shares"] for r in tile["split"]["rows"]}
    assert abs(shares["Preis"][0] - 8 / 13) < 0.01 and abs(shares["Lieferung"][1] - 7 / 12) < 0.01


def test_no_tile_without_categories(app, client, survey):
    experiments_on(client, "coding")
    add(client, "text_input")
    with app.app_context():
        assert not any("@k" in k for k in boards._known(db.session.get(Survey, 1)))
