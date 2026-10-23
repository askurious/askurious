"""Code audit 2026-10-08, package E: card sorting, side-by-side matrix, Q sort (N2, N3, N8, N9, R1, R4) and the group
sizes of the live sample comparison worked out by the server (as N7)."""
from tests.static_sources import participant_js

import pytest
from flask import g
from werkzeug.datastructures import MultiDict

from app import boards
from app.extensions import db
from app.items.base import AnswerError
from app.models import Board, Item, Response, utcnow
from tests.conftest import add
from tests.dashboards.test_boards import D, _answer, _board, study  # noqa: F401
from tests.items.test_card_sort_qsort import sent, setup_cards, setup_q
from tests.items.test_side_by_side import editor_form, setup


# --- N2: a tampered card sort is an invalid answer, never a 500 ------------------------------------------------------

@pytest.mark.parametrize("data", [
    {"s": {"1": [["ki"]]}},                       # a card as a list: unhashable
    {"s": {"1": [{"x": 1}, "ki"]}},               # a card as an object
    {"s": {"9" * 5000: ["ki"]}},                  # a number key int() refuses (more than 4300 digits)
    {"s": {"1": "ki", "2": None}, "own": []},
])
def test_n2_tampered_card_sort_is_no_server_error(app, client, survey, data):
    setup_cards(client, mode="hybrid")  # given groups 1 and 2, own groups besides
    with app.app_context():
        item = db.session.get(Item, 2)
        try:
            item.kind.parse_answer(item, sent(item, data))
        except AnswerError:
            pass  # "Bitte sortieren Sie alle Karten": the page shows it, no 500


def test_n2_strings_are_kept_beside_junk(app, client, survey):
    setup_cards(client, required=False)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.parse_answer(item, sent(item, {"s": {"1": [["ki"], "ki", "ki", 5], "2": ["robo"]}})) == \
            {"s": {"1": ["ki"], "2": ["robo"]}}


# --- N3: own groups beside a given group with a high code -------------------------------------------------------------

def _hybrid(client):
    add(client, "card_sort")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "cs"), ("question", "Sortieren"), ("answer_mode", "optional"), ("mode", "hybrid"), ("max_own", "3"),
        ("option_id", "a"), ("option_code", "ki"), ("option_label", "KI"),
        ("option_id", "b"), ("option_code", "robo"), ("option_label", "Roboter"),
        ("option_id", "c"), ("option_code", "app"), ("option_label", "App"),
        ("group_id", "g1"), ("group_code", "1"), ("group_label", "Technik"),
        ("group_id", "g2"), ("group_code", "200"), ("group_label", "Sonstiges")]))


def test_n3_own_group_beside_a_high_given_code_is_kept(app, client, survey):
    _hybrid(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == []
        # the page now numbers own groups 101 ...; one from an older page (201) counts as an own group as well
        answer = item.kind.parse_answer(item, sent(item, {"s": {"1": ["ki"], "200": ["app"], "201": ["robo"]},
                                                          "own": {"201": "Maschinen"}}))
        assert answer == {"s": {"1": ["ki"], "200": ["app"], "101": ["robo"]}, "own": {"101": "Maschinen"}}
        # own groups are numbered in the order of their keys, at most max_own of them
        answer = item.kind.parse_answer(item, sent(item, {"s": {"104": ["app"], "102": ["ki"], "131": ["robo"]},
                                                          "own": {"102": "A", "104": "B", "131": "C"}}))
        assert answer == {"s": {"101": ["ki"], "102": ["app"], "103": ["robo"]}, "own": {"101": "A", "102": "B", "103": "C"}}


def test_n3_the_page_takes_the_smallest_free_own_number():
    from pathlib import Path

    js = participant_js()
    start = js.index('make.addEventListener("click"')
    part = js[start:start + 800]
    assert "Math.max" not in part and ".p-sort-group.is-own" in part and "100 + maxOwn" in part


# --- N8: ids from the editor form ------------------------------------------------------------------------------------

def test_n8_card_sort_group_ids_are_checked(app, client, survey):
    add(client, "card_sort")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "cs"), ("question", "Sortieren"), ("answer_mode", "optional"), ("mode", "closed"),
        ("option_id", "a"), ("option_code", "ki"), ("option_label", "KI"),
        ("group_id", 'x"]y'), ("group_code", "1"), ("group_label", "Technik"),
        ("group_id", "g2"), ("group_code", "2"), ("group_label", "Alltag")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        gids = [gr["id"] for gr in item.config["groups"]]
        assert gids[1] == "g2" and gids[0] != 'x"]y' and all(c.isalnum() for c in gids[0])
        assert item.text("groups")[gids[0]] == "Technik"


def test_n8_side_by_side_row_and_column_ids_are_checked(app, client, survey):
    add(client, "side_by_side")                                                     # 2
    form = editor_form(2)
    form = MultiDict([("row_id", 'r"1') if (k, v) == ("row_id", "r1") else (k, v) for k, v in form.items(multi=True)])
    form.add("col_id", 'c"]x')
    form.add('col_code_c"]x', "bad")
    form.add('col_title_c"]x', "Böse")
    client.post("/surveys/1/editor/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        from app.items.base import ID_RE

        assert all(ID_RE.match(r["id"]) for r in item.config["rows"]) and len(item.config["rows"]) == 3
        assert [c["id"] for c in item.config["columns"]] == ["c1", "c2", "c3", "c4", "c5"]
    from pathlib import Path

    js = participant_js()
    assert "'[data-sbs-col=\"' + CSS.escape(" in js


# --- N9: extra columns known from the export, not from their ending ---------------------------------------------------

def test_n9_q_sort_statement_ending_in_pre_is_a_statement(app, client, survey):
    setup_q(client, presort=False)
    with app.app_context():
        item = db.session.get(Item, 2)
        opts = [dict(o) for o in item.config["options"]]
        opts[0] = {**opts[0], "code": "s1_pre"}
        item.config = {**item.config, "options": opts}
        db.session.commit()
        item = db.session.get(Item, 2)
        assert item.kind.column_level(item, "qs_s1_pre") == item.kind.level(item)
        assert item.kind.scale_values(item, "qs_s1_pre") == item.kind.values(item)
        assert item.kind.codebook_info(item, "qs_s1_pre", "de")[0] == "Skala"
        item.config = {**item.config, "presort": True}  # with the presort: the presort column of statement s2
        db.session.commit()
        item = db.session.get(Item, 2)
        assert item.kind.column_level(item, "qs_s2_pre") == "ordinal" and item.kind.scale_values(item, "qs_s2_pre") is None


def test_n9_card_coded_like_a_rank_column_is_a_card(app, client, survey):
    setup_cards(client)  # without "rank"
    with app.app_context():
        item = db.session.get(Item, 2)
        opts = [dict(o) for o in item.config["options"]]
        opts[1] = {**opts[1], "code": "ki_rank"}
        item.config = {**item.config, "options": opts}
        db.session.commit()
        item = db.session.get(Item, 2)
        assert item.kind.column_level(item, "cs_ki_rank") == "nominal"
        assert item.kind.codebook_info(item, "cs_ki_rank", "de")[0] == "Auswahl"


# --- R1: a split tile of a tick-box column lines the groups up -------------------------------------------------------

def test_r1_split_sbs_multi_tile_rows_line_up(app, study):  # noqa: F811
    sid = setup(study, col_kind_c3="multi")
    _answer(app, {"2": "1", str(sid): {"r1": {"c1": 1, "c3": [1]}}}, n=12)   # group "Auto": only Bus, täglich
    _answer(app, {"2": "2", str(sid): {"r2": {"c1": 1, "c3": [2]}}}, n=12)   # group "Rad": only Bahn, wöchentlich
    _board(study, "empty")
    key = f"item:{sid}@c-c3"
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", key), (f"view-{key}", "table"), (f"n-{key}", "1"),
                                         (f"by-{key}", "item:2")]))
    with app.app_context():
        tiles = {t["key"]: t for t in boards.state(db.session.get(Board, 1), fresh=True)["tiles"]}
    split = tiles[key].get("split")
    assert split
    shares = {r["label"]: r["shares"] for r in split["rows"]}
    assert all(s[1] is None for label, s in shares.items() if label.startswith("Bus")), shares
    assert all(s[0] is None for label, s in shares.items() if label.startswith("Bahn")), shares
    assert shares["Bus: täglich"][0] == 1.0 and shares["Bahn: wöchentlich"][1] == 1.0
    assert not any(label.startswith("Fahrrad") for label in shares)  # a row no group answered is left out
    # the tile of everyone keeps leaving out rows nobody answered
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", key), (f"view-{key}", "table"), (f"n-{key}", "1")]))
    with app.app_context():
        tile = {t["key"]: t for t in boards.state(db.session.get(Board, 1), fresh=True)["tiles"]}[key]
    labels = [r["label"] for r in tile.get("rows", [])]
    assert labels and not any(label.startswith("Fahrrad") for label in labels)


# --- R4: carrying from a side-by-side matrix checks bounds and column -------------------------------------------------

@pytest.mark.parametrize("ref, kept", [("c1:1", True), ("c1:0", False), ("c1:3", False), ("c1:99", False),
                                       ("zz:1", False), ("c4:1", False), ("c3:3", True)])
def test_r4_carry_from_a_matrix_checks_the_column(app, client, survey, ref, kept):
    item_id = setup(client)                                                         # 2
    add(client, "page_break")                                                       # 3
    add(client, "likert")                                                           # 4
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "zz"), ("question", "Wie zufrieden?"), ("points", "5"), ("anchors", "satisfied"), ("answer_mode", "optional"),
        ("carry_item", f"{item_id}:{ref}"), ("carry_which", "chosen")]))
    with app.app_context():
        carry = db.session.get(Item, 4).config.get("carry")
        col, value = ref.split(":")
        assert carry == ({"item": item_id, "which": "chosen", "col": col, "value": int(value)} if kept else None)


# --- sample comparison: the group sizes come from the server in a participation ---------------------------------------

def test_sample_compare_sizes_are_worked_out_by_the_server(app, client, survey):
    add(client, "single_choice")                                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "fav"), ("question", "Lieblingsfarbe"), ("answer_mode", "optional"),
        ("option_id", ""), ("option_code", "1"), ("option_label", "rot"),
        ("option_id", ""), ("option_code", "2"), ("option_label", "blau")]))
    add(client, "page_break")                                                       # 3
    add(client, "sample_compare")                                                   # 4
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "live"), ("question", "So haben andere geantwortet"), ("source", "2"), ("show_own", "1")]))
    with app.app_context():
        for n in range(6):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": "1" if n % 2 else "2"}))
        db.session.commit()

    def parsed(sent_value):
        with app.app_context(), app.test_request_context():
            item = db.session.get(Item, 4)
            g.participation = Response(survey_id=1, source="real", answers={})
            return item.kind.parse_answer(item, MultiDict([("i4_n_2", sent_value)]), "de", {})

    assert parsed("999999999") == {"2": 6}
    assert parsed("0") == {"2": 6}
    with app.app_context(), app.test_request_context():
        item = db.session.get(Item, 4)
        g.participation = Response(survey_id=1, source="real", answers={})
        assert item.kind.parse_answer(item, MultiDict(), "de", {}) is None  # nothing shown, nothing stored
    with app.app_context():
        item = db.session.get(Item, 4)
        assert item.kind.parse_answer(item, MultiDict([("i4_n_2", "3")])) == {"2": 3}  # dummy runs: as sent
