"""Card sort (items/card_sort.py) and Q sort (items/q_sort.py): editor, answers, export, codebook, dummy data, results."""
import json

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Response, Survey
from tests.conftest import add


def setup_cards(client, mode="closed", rank=False, required=True):
    add(client, "card_sort")                                                       # item 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "cs"), ("question", "Sortieren Sie die Begriffe."), ("answer_mode", "required" if required else "optional"),
        ("mode", mode), ("max_own", "3"), *([("rank", "1")] if rank else []), ("randomize", "1"),
        ("option_id", "a"), ("option_code", "ki"), ("option_label", "KI"),
        ("option_id", "b"), ("option_code", "robo"), ("option_label", "Roboter"),
        ("option_id", "c"), ("option_code", "app"), ("option_label", "App"),
        ("group_id", "g1"), ("group_code", "1"), ("group_label", "Technik"),
        ("group_id", "g2"), ("group_code", "2"), ("group_label", "Alltag")]))


def setup_q(client, presort=True, shape=None, required=True):
    add(client, "q_sort")                                                          # item 2
    fields = [("code", "qs"), ("question", "Wie sehr stimmen Sie zu?"), ("answer_mode", "required" if required else "optional"),
              ("range", "2"), ("low", "gar nicht"), ("high", "voll"), ("pre_low", "eher nicht"), ("pre_mid", "neutral"),
              ("pre_high", "eher ja"), *([("presort", "1")] if presort else [])]
    for n in range(1, 10):
        fields += [("option_id", f"s{n}"), ("option_code", f"s{n}"), ("option_label", f"Aussage {n}")]
    if shape:
        fields += [("shape", "own")] + [(f"cap_{v}", str(c)) for v, c in zip(range(-2, 3), shape)]
    client.post("/surveys/1/editor/items/2", data=MultiDict(fields))


def sent(item, data):
    return MultiDict({f"i{item.id}": json.dumps(data)})


def test_closed_card_sort(app, client, survey):
    setup_cards(client, rank=True)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == [] and item.config["mode"] == "closed" and item.config["rank"]
        assert [g["code"] for g in item.config["groups"]] == ["1", "2"] and item.text("groups")["g2"] == "Alltag"
        with pytest.raises(AnswerError, match="jede Karte"):
            item.kind.parse_answer(item, sent(item, {"s": {"1": ["ki"]}}))
        # unknown groups and cards drop out, a card counts in its first group only; own groups need an open sort
        answer = item.kind.parse_answer(item, sent(item, {"s": {"1": ["robo", "ki", "ki", "xx"], "2": ["app", "ki"], "9": ["app"],
                                                                "101": ["app"]}}))
        assert answer == {"s": {"1": ["robo", "ki"], "2": ["app"]}}
        assert item.kind.export_values(item, answer) == {"cs_ki": "1", "cs_ki_rank": 2, "cs_robo": "1", "cs_robo_rank": 1,
                                                         "cs_app": "2", "cs_app_rank": 1}
        assert [c for c, _ in item.kind.export_columns(item)] == ["cs_ki", "cs_ki_rank", "cs_robo", "cs_robo_rank", "cs_app", "cs_app_rank"]
        kind, values, notes = item.kind.codebook_info(item, "cs_ki", "de")
        assert kind == "Auswahl" and values == "1 = Technik; 2 = Alltag" and "nicht einsortiert" in notes[0]
        assert item.kind.codebook_info(item, "cs_ki_rank", "de", ui="en")[0] == "Rang"
        assert item.kind.column_level(item, "cs_ki_rank") == "ordinal" and item.kind.column_level(item, "cs_ki") == "nominal"
        assert item.kind.display_value(item, MultiDict({"i2": "kein JSON"})) is None             # a broken field is no sort


def test_open_card_sort_names_and_numbers(app, client, survey):
    setup_cards(client, mode="open")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == [] and item.kind.given(item) == []
        with pytest.raises(AnswerError, match="Namen"):
            item.kind.parse_answer(item, sent(item, {"s": {"104": ["ki", "robo", "app"]}, "own": {"104": " "}}))
        # own groups are numbered 101, 102 … in the order they were made, empty ones dropped; given codes count as own
        answer = item.kind.parse_answer(item, sent(item, {"s": {"107": ["app"], "103": ["ki", "robo"], "105": []},
                                                          "own": {"103": "Maschinen", "107": "Handy", "105": "leer"}}))
        assert answer == {"s": {"101": ["ki", "robo"], "102": ["app"]}, "own": {"101": "Maschinen", "102": "Handy"}}
        values = item.kind.export_values(item, answer)
        assert values == {"cs_ki": "101", "cs_robo": "101", "cs_app": "102", "cs_own1": "Maschinen", "cs_own2": "Handy", "cs_own3": None}
        assert "101 = eigener Stapel 1; 102 = eigener Stapel 2; 103 = eigener Stapel 3" in item.kind.codebook_info(item, "cs_ki", "de")[1]
        assert item.kind.codebook_info(item, "cs_own1", "de")[0] == "Text"
        # at most max_own own groups
        three = item.kind.parse_answer(item, sent(item, {"s": {"101": ["ki"], "102": ["robo"], "103": ["app"], "104": []},
                                                         "own": {"101": "a", "102": "b", "103": "c"}}))
        assert len(three["own"]) == 3


def test_hybrid_and_optional(app, client, survey):
    setup_cards(client, mode="hybrid", required=False)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == []
        answer = item.kind.parse_answer(item, sent(item, {"s": {"2": ["ki"], "101": ["app"]}, "own": {"101": ""}}))
        assert answer == {"s": {"2": ["ki"], "101": ["app"]}, "own": {"101": ""}}
        assert item.kind.is_missing(item, answer)                                    # Roboter not sorted
        assert item.kind.parse_answer(item, sent(item, {"s": {}})) is None
        item.config = {**item.config, "groups": [{"id": "g1", "code": "101"}]}
        assert any("reserviert" in p for p in item.kind.validate(item))


def test_card_sort_page_dummy_and_results(app, client, survey):
    setup_cards(client, mode="hybrid", rank=True)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-sort="2"' in html and "Technik" in html and "Roboter" in html and "data-sort-new" in html and "data-sort-rank" in html
    with app.app_context():
        from app import dummy

        s = db.session.get(Survey, 1)
        dummy.generate(s, 12, seed=3)
        db.session.commit()
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(sorted(c for cards in a["s"].values() for c in cards) == ["app", "ki", "robo"] for a in answers)
        item = db.session.get(Item, 2)
        found = item.kind.results(item, [item.kind.export_values(item, a) for a in answers], "de")
        assert found["persons"] == 12 and len(found["pairs"]) == 3
        assert all(abs(sum(s or 0 for s in r["shares"]) + (r["own"] or 0) - 1) < 1e-9 for r in found["rows"])


def test_card_sort_results_on_table(app, client, survey):
    setup_cards(client, mode="hybrid")
    with app.app_context():
        item = db.session.get(Item, 2)
        table = [{"cs_ki": "1", "cs_robo": "1", "cs_app": "101", "cs_own1": "Handy"},
                 {"cs_ki": "1", "cs_robo": "2", "cs_app": "2"},
                 {"cs_ki": "", "cs_robo": None, "cs_app": None}]
        r = item.kind.results(item, table, "de")
        assert r["persons"] == 2 and r["groups"] == ["Technik", "Alltag"]
        ki, robo, app_ = r["rows"]
        assert ki["shares"] == [1.0, 0.0] and ki["own"] == 0.0 and app_["own"] == 0.5
        assert r["pairs"][0][1] == 0.5 and r["pairs"][0][0] is None                  # KI and Roboter together in one of two
        assert r["names"] == [("handy", 1)]


def test_q_sort_grid_and_answers(app, client, survey):
    setup_q(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.capacities(item) == [1, 2, 3, 2, 1] and item.kind.validate(item) == []
        laid = {"s1": -2, "s2": -1, "s3": -1, "s4": 0, "s5": 0, "s6": 0, "s7": 1, "s8": 1, "s9": 2}
        answer = item.kind.parse_answer(item, sent(item, {"q": laid, "p": {"s1": -1, "s9": 1, "s4": True}}))
        assert answer == {"q": laid, "p": {"s1": -1, "s9": 1}}
        with pytest.raises(AnswerError, match="alle Aussagen"):
            item.kind.parse_answer(item, sent(item, {"q": {**laid, "s9": -2}}))      # column -2 is full: s9 drops out
        values = item.kind.export_values(item, answer)
        assert values["qs_s1"] == -2 and values["qs_s1_pre"] == -1 and values["qs_s2_pre"] is None
        cols = dict(item.kind.export_columns(item))
        assert "−2 = gar nicht, +2 = voll" in cols["qs_s1"] and "qs_s1_pre" in cols
        kind, vals, notes = item.kind.codebook_info(item, "qs_s1", "de")
        assert kind == "Skala" and vals == "-2 = gar nicht; 2 = voll" and "-2: 1" in notes[1]
        assert item.kind.codebook_info(item, "qs_s1_pre", "de")[1] == "-1 = eher nicht; 0 = neutral; 1 = eher ja"


def test_q_sort_own_shape_and_checks(app, client, survey):
    setup_q(client, presort=False, shape=[2, 2, 1, 2, 2])
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["capacities"] == [2, 2, 1, 2, 2] and not item.config["presort"]
        assert item.kind.validate(item) == [] and item.kind.check_warnings(item) == []
        assert [c for c, _ in item.kind.export_columns(item)][:2] == ["qs_s1", "qs_s2"]
        item.config = {**item.config, "capacities": [1, 1, 1, 1, 1]}
        assert any("passen nicht" in p for p in item.kind.validate(item))
        item.config = {**item.config, "capacities": [3, 2, 1, 2, 1]}
        assert item.kind.check_warnings(item)                                          # not symmetric
        item.config = {**item.config, "capacities": [], "range": 6}
        assert any("weniger Aussagen" in p for p in item.kind.validate(item))


def test_q_sort_few_statements_can_be_sent(app, client, survey):
    """Pre-test 2026-10-20, 26: a required Q sort with 2 statements and the default range (7 columns) has no grid; the
    page lets the cards go into any column, but the server dropped every card and always answered "Bitte legen Sie
    alle Aussagen in das Raster."."""
    setup_q(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "range": 3, "capacities": [],
                       "options": [o for o in item.config["options"] if o["code"] in ("s1", "s2")]}
        assert item.kind.capacities(item) is None
        assert any("weniger Aussagen (2) als Spalten (7)" in p for p in item.kind.validate(item))  # the editor says so
        answer = item.kind.parse_answer(item, sent(item, {"q": {"s1": 2, "s2": 3}, "p": {"s1": 1, "s2": 1}}))
        assert answer == {"q": {"s1": 2, "s2": 3}, "p": {"s1": 1, "s2": 1}}
        assert not item.kind.is_missing(item, answer)
        assert item.kind.parse_answer(item, sent(item, {"q": {"s1": 3, "s2": 3}}))["q"] == {"s1": 3, "s2": 3}
        with pytest.raises(AnswerError, match="alle Aussagen"):
            item.kind.parse_answer(item, sent(item, {"q": {"s1": 2, "s2": 9}}))      # no column 9


def test_q_sort_own_grid_not_matching_statements(app, client, survey):
    """Pre-test 2026-10-20, 26: an own grid whose places do not add up to the statements. With more places than
    statements, laying all is enough; with fewer, the page can lay only as many as there are places."""
    setup_q(client, presort=False, shape=[2, 2, 2, 2, 2])                           # 10 places, 9 statements
    with app.app_context():
        item = db.session.get(Item, 2)
        laid = {f"s{n}": v for n, v in zip(range(1, 10), (-2, -2, -1, -1, 0, 0, 1, 1, 2))}
        assert item.kind.parse_answer(item, sent(item, {"q": laid}))["q"] == laid
        item.config = {**item.config, "capacities": [1, 2, 2, 2, 1]}                # 8 places, 9 statements
        assert any("passen nicht" in p for p in item.kind.validate(item))
        eight = {f"s{n}": v for n, v in zip(range(1, 9), (-2, -1, -1, 0, 0, 1, 1, 2))}
        answer = item.kind.parse_answer(item, sent(item, {"q": eight}))
        assert answer["q"] == eight and not item.kind.is_missing(item, answer)
        with pytest.raises(AnswerError, match="alle Aussagen"):
            item.kind.parse_answer(item, sent(item, {"q": dict(list(eight.items())[:7])}))


def test_quasi_normal_shapes():
    from app.items.q_sort import quasi_normal

    for n, k in ((9, 2), (25, 4), (33, 4), (40, 4), (60, 6), (80, 6)):
        caps = quasi_normal(n, k)
        assert sum(caps) == n and caps == caps[::-1] and min(caps) >= 1
        assert all(caps[i] <= caps[i + 1] for i in range(k))                         # rising to the middle
    assert quasi_normal(8, 4) is None


def test_q_sort_page_and_dummy(app, client, survey):
    setup_q(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-sort-q' in html and "data-sort-presort" in html and 'data-cap="3"' in html and "eher ja" in html
    with app.app_context():
        from app import dummy

        s = db.session.get(Survey, 1)
        dummy.generate(s, 5, seed=2)
        db.session.commit()
        item = db.session.get(Item, 2)
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(sorted(a["q"].values()) == [-2, -1, -1, 0, 0, 0, 1, 1, 2] for a in answers)
        table = [item.kind.export_values(item, a) for a in answers]
        r = item.kind.results(item, table, "de")
        assert r["persons"] == 5 and len(r["rows"]) == 9 and r["k"] == 2
        means = [row["mean"] for row in r["rows"]]
        assert means == sorted(means, reverse=True)
