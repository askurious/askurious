"""Vignettes (factorial survey): the design module (app/doe.py), the vignette loop (app/vignettes.py) and the
choice between a table and factors at the top of the loop page."""
import random

import pytest

from app import doe, flow, loops, vignettes
from app.extensions import db
from app.models import Survey


# --- design -------------------------------------------------------------------------------


def test_full_factorial_leaves_out_prohibited_pairs():
    assert len(doe.full_factorial([3, 2, 2])) == 12
    assert len(doe.full_factorial([3, 2, 2], [((0, 0), (1, 1))])) == 10


def test_d_optimal_gives_different_allowed_profiles_and_a_good_efficiency():
    forbidden = [((0, 0), (1, 1))]
    D = doe.d_optimal([3, 3, 2, 4], 24, forbidden, seed=1)
    assert len(D) == 24 and len({tuple(r) for r in D}) == 24
    assert not any(r[0] == 0 and r[1] == 1 for r in D)
    assert doe.d_efficiency(D, [3, 3, 2, 4]) > 90


def test_d_optimal_refuses_impossible_sizes():
    with pytest.raises(ValueError):
        doe.d_optimal([2, 2], 5)  # only 4 combinations
    with pytest.raises(ValueError):
        doe.d_optimal([3, 3, 3], 4)  # 7 parameters


def test_full_design_is_orthogonal_and_decks_are_balanced():
    D = doe.full_factorial([3, 3, 2])
    assert doe.d_efficiency(D, [3, 3, 2]) == pytest.approx(100)
    decks = doe.blocks(D, [3, 3, 2], 3, seed=2)
    assert sorted(i for d in decks for i in d) == list(range(18))
    assert all(len(d) == 6 for d in decks)
    s = doe.stats(D, [3, 3, 2], decks)
    assert s["decks_missing_level"] == 0 and s["level_spread"] == 0 and s["deck_eff"] > 99


def test_blocks_beat_consecutive_slices():
    levels = [3, 2, 2, 4]
    D = doe.d_optimal(levels, 24, seed=3)
    blocked = doe.blocks(D, levels, 4, seed=3)
    ordered = [D[i] for d in blocked for i in d]
    assert doe.deck_efficiency(ordered, levels, 6) >= doe.deck_efficiency(D, levels, 6)


# --- the vignette loop --------------------------------------------------------------------------


def _node(app, nid=None):
    with app.app_context():
        survey = db.session.get(Survey, 1)
        nodes = [n for n, _, _ in flow.walk(flow.tree(survey)) if vignettes.is_vignettes(n)]
        return next(n for n in nodes if nid is None or n["id"] == nid)


def _add(client):
    """A loop around block 2 with factors as its source (the choice at the top of the loop page)."""
    r = client.post("/surveys/1/editor/blocks/2/loop")
    nid = r.headers["Location"].split("/loop/")[1].split("?")[0]
    return client.post(f"/surveys/1/flow/loop/{nid}", data={"action": "source_factors"})


FORM = {
    "name": "Roboter", "code": "vig", "order": "random", "method": "doptimal", "per_person": "4", "decks": "3",
    "attr_id": ["a1", "a2", "a3"], "attr_label": ["Aussehen", "Einsatzort", "Daten"], "attr_code": ["", "", ""],
    "lvl_a1_id": ["l11", "l12", "l13"], "lvl_a1_label": ["humanoid", "Roboterarm", "Kiste"], "lvl_a1_def": ["", "", ""],
    "lvl_a1_hint": ["humanoid robot", "robot arm", "box robot"],
    "lvl_a2_id": ["l21", "l22", "l23"], "lvl_a2_label": ["Pflegeheim", "Krankenhaus", "Zuhause"], "lvl_a2_def": ["", "", ""],
    "lvl_a2_hint": ["", "", ""],
    "lvl_a3_id": ["l31", "l32"], "lvl_a3_label": ["nur lokal", "in der Cloud"], "lvl_a3_def": ["", "auf Servern"],
    "lvl_a3_hint": ["", ""],
}


def test_one_card_for_tables_and_factors_without_the_tick(app, client, survey):
    flow_page = client.get("/surveys/1/flow/").get_data(as_text=True)
    assert 'value="vignettes"' not in flow_page and "je Tabellenzeile oder je Vignette" in flow_page
    assert "Vignetten (faktorieller Survey)" not in client.get("/account/").get_data(as_text=True)
    client.post("/surveys/1/editor/blocks/2/loop")
    nid = next(n["id"] for n, _, _ in flow.walk(flow.tree(_survey(app))) if n["t"] == "loop")
    page = client.get(f"/surveys/1/flow/loop/{nid}").get_data(as_text=True)
    assert "Woher kommen die Durchläufe?" in page and 'value="source_factors"' in page
    assert "data-confirm=" not in page.split("loop-source")[1].split("</form>")[0]  # an empty table: no question
    r = client.post(f"/surveys/1/flow/loop/{nid}", data={"action": "source_factors"})
    assert r.headers["Location"].endswith(f"/vignettes/{nid}?lang=de") or f"/vignettes/{nid}" in r.headers["Location"]
    node = _node(app, nid)
    assert node["name"] == "Vignetten 1" and node["attributes"] == [] and node["plan"]["method"] == "doptimal"
    assert client.get(f"/surveys/1/flow/loop/{nid}").status_code == 302  # the loop page sends it to its editor
    page = client.get(f"/surveys/1/flow/vignettes/{nid}").get_data(as_text=True)
    assert 'value="source_table"' in page and "experimentell" not in page


def test_vignettes_from_factors_to_decks(app, client, survey):
    _add(client)
    node = _node(app)
    page = f"/surveys/1/flow/vignettes/{node['id']}"
    assert client.get(page).status_code == 200
    client.post(page, data=FORM)
    node = _node(app)
    assert [a["code"] for a in node["attributes"]] == ["aussehen", "einsatzort", "daten"]
    assert node["rows"] == []  # no plan yet
    client.post(page, data={"action": "generate"})
    node = _node(app)
    assert vignettes.plan_current(_survey(app), node)
    assert len(node["rows"]) == 12 and len(node["plan"]["decks_rows"]) == 3
    names = [c["name"] for c in node["columns"]]
    assert names == ["aussehen", "aussehen_hint", "einsatzort", "daten", "daten_def"]
    assert next(c for c in node["columns"] if c["name"] == "aussehen_hint")["hidden"]
    html = client.get(page).get_data(as_text=True)
    assert "D-Effizienz" in html and "Vorschau" in html
    # a label changes the rows, not the plan
    client.post(page, data={**FORM, "lvl_a1_label": ["menschenähnlich", "Roboterarm", "Kiste"]})
    node = _node(app)
    assert vignettes.plan_current(_survey(app), node)
    labels = {((r["values"].get("fa1") or {}).get("de")) for r in node["rows"]}
    assert "menschenähnlich" in labels and "humanoid" not in labels
    # one more level: the plan is outdated and the loop has no rows
    client.post(page, data={**FORM, "lvl_a3_id": ["l31", "l32", "l33"], "lvl_a3_label": ["nur lokal", "in der Cloud", "gar nicht"],
                            "lvl_a3_def": ["", "", ""], "lvl_a3_hint": ["", "", ""]})
    node = _node(app)
    assert not vignettes.plan_current(_survey(app), node) and node["rows"] == []
    with app.app_context():
        found = loops.check(db.session.get(Survey, 1))
    assert any("Versuchsplan" in m for _, m, _, _ in found)


def _survey(app):
    with app.app_context():
        s = db.session.get(Survey, 1)
        db.session.expunge(s)
        return s


def test_each_person_gets_the_least_shown_deck(app, client, survey):
    _add(client)
    node = _node(app)
    page = f"/surveys/1/flow/vignettes/{node['id']}"
    client.post(page, data=FORM)
    client.post(page, data={"action": "generate"})
    node = _node(app)
    decks = node["plan"]["decks_rows"]
    counts, rng, seen = {}, random.Random(5), []
    with app.app_context():
        s = db.session.get(Survey, 1)
        for _ in range(6):
            rows = vignettes.choose_rows(s, node, rng, counts)
            assert sorted(rows) in [sorted(d) for d in decks]
            seen.append(next(n for n, d in enumerate(decks) if rows[0] in d))
            for r in rows:
                counts[r] = counts.get(r, 0) + 1
    assert sorted(seen) == [0, 0, 1, 1, 2, 2]


def test_factors_back_to_a_table_keep_rows_and_decks(app, client, survey):
    from app import loop_plan

    _add(client)
    node = _node(app)
    page = f"/surveys/1/flow/vignettes/{node['id']}"
    client.post(page, data=FORM)
    client.post(page, data={"action": "generate"})
    before = _node(app)
    html = client.get(page).get_data(as_text=True)
    assert "data-confirm" not in html.split('class="panel loop-source"')[1].split(">")[0]  # no question (W28)
    r = client.post(page, data={"action": "source_table"})
    assert f"/loop/{node['id']}" in r.headers["Location"]
    html = client.get(r.headers["Location"]).get_data(as_text=True)  # said afterwards, with "Rückgängig"
    assert "Die Tabelle ist jetzt die Quelle" in html and "jede Person bekommt weiter eines der Decks" in html
    assert 'class="flash-undo"' in html
    with app.app_context():
        after = flow.find(flow.tree(db.session.get(Survey, 1)), node["id"])[0]
        assert "source" not in after and "attributes" not in after and after["name"] == "Roboter"
        assert after["rows"] == before["rows"] and after["columns"] == before["columns"]
        assert after["mode"] == "plan" and after["subset"]["decks_rows"] == before["plan"]["decks_rows"]
        assert loop_plan.current(db.session.get(Survey, 1), after)  # everyone keeps getting one deck
    html = client.get(f"/surveys/1/flow/loop/{node['id']}").get_data(as_text=True)
    assert "D-Effizienz" in html and 'value="source_factors"' in html
    # and back: the factor columns become factors again, the plan and the row ids stay
    r = client.post(f"/surveys/1/flow/loop/{node['id']}", data={"action": "source_factors"})
    html = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Die Spalten aussehen, einsatzort, daten sind Faktoren" in html and "Decks des Versuchsplans sind geblieben" in html
    again = _node(app, node["id"])
    assert [a["code"] for a in again["attributes"]] == ["aussehen", "einsatzort", "daten"]
    assert sorted(l["hint"] for l in again["attributes"][0]["levels"]) == ["box robot", "humanoid robot", "robot arm"]
    assert "auf Servern" in again["texts"]["de"]["level_defs"].values()
    assert again["plan"]["decks_rows"] == before["plan"]["decks_rows"]
    assert [r["id"] for r in again["rows"]] == [r["id"] for r in before["rows"]]
    assert vignettes.plan_current(_survey(app), again) and again["name"] == "Roboter"
    client.post("/surveys/1/editor/undo")
    assert not vignettes.is_vignettes(_node_any(app, node["id"]))


def _node_any(app, nid):
    with app.app_context():
        return flow.find(flow.tree(db.session.get(Survey, 1)), nid)[0]


def test_translation_of_levels_reaches_the_rows(app, client, survey):
    from app import translations

    _add(client)
    node = _node(app)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data=FORM)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data={"action": "generate"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        unit = next(u for u in translations.units(s, "en") if u.key == f"loop:{node['id']}")
        assert unit.role("l.l11") == "aussehen, Stufe 1"
        unit.set("en", {"l": {"l11": "humanoid robot"}})
        db.session.commit()
    rows = _node(app)["rows"]
    assert any((r["values"]["fa1"].get("en")) == "humanoid robot" for r in rows)


# --- export and analysis -------------------------------------------------------------------------


def _with_answers(app, client, persons=12, effect=2.0):
    """A plan of 12 vignettes in 3 decks, a rating in the loop and `persons` real participations whose answer is
    3 + effect for "Kiste" (level 3 of the first factor), else 3, plus a person offset."""
    from datetime import datetime

    from app.models import Item, Response
    from tests.conftest import add

    add(client, "rating")
    _add(client)
    node = _node(app)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data=FORM)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data={"action": "generate"})
    node = _node(app)
    rng = random.Random(1)
    with app.app_context():
        item = db.session.scalar(db.select(Item).filter_by(block_id=2, type="rating"))
        profiles = dict(zip(node["plan"]["ids"], node["plan"]["profiles"]))
        for p in range(persons):
            deck = node["plan"]["decks_rows"][p % 3]
            offset = rng.choice([-1, 0, 1])
            answers = {f"{item.id}#{rid}": max(1, min(5, round(3 + offset + (effect if profiles[rid][0] == 2 else 0))))
                       for rid in deck}
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers=answers,
                                    layout={"loops": {node["id"]: list(deck)}}, completed_at=datetime(2026, 10, 4),
                                    pid=f"p{p}"))
        db.session.commit()
        return node, item.code


def test_long_format_has_a_row_per_person_and_vignette(app, client, survey):
    node, code = _with_answers(app, client)
    r = client.get(f"/surveys/1/export/vignettes/{node['id']}.csv")
    assert r.status_code == 200
    lines = r.get_data(as_text=True).lstrip("﻿").strip().splitlines()
    head = lines[0].split(",")
    assert head[:5] == ["resp_id", "source", "deck", "position", "vignette"]
    assert "aussehen" in head and "aussehen_label" in head and code in head
    assert len(lines) - 1 == 12 * 4


def test_analysis_finds_the_effect_of_a_level(app, client, survey):
    from app import export, results

    node, code = _with_answers(app, client, persons=30)
    with app.app_context():
        s = db.session.get(Survey, 1)
        table = export.rows(s, False, ("real",), with_hb=False, with_quality=False)
        found = vignettes.analysis(s, table)
    q = found[0]["questions"][0]
    assert q["persons"] == 30 and q["n"] == 120
    means = {l["label"]: l["mean"] for l in q["by_level"][0]["levels"]}
    assert means["Kiste"] > means["humanoid"] + 1
    kiste = next(c for c in q["coefs"] if c["name"] == "aussehen: Kiste")
    assert kiste["b"] > 1 and kiste["p"] < 0.001 and kiste["ci"][0] > 0
    assert 0 <= q["icc"] < 1
    page = client.get("/surveys/1/results").get_data(as_text=True)
    assert "Wirkung der Faktoren" in page and "geclustert" in page


# --- D-optimal subset of a table -----------------------------------------------------------------


def _table(app, client, crossing=True):
    """A loop around block 2 with a table robot x place (x a free text column unless crossing)."""
    import itertools

    client.post("/surveys/1/editor/blocks/2/loop")
    with app.app_context():
        nid = next(n["id"] for n, _, _ in flow.walk(flow.tree(db.session.get(Survey, 1))) if n["t"] == "loop")
    robots, places = ["humanoid", "Arm", "Kiste"], ["Heim", "Klinik", "Zuhause", "Büro"]
    rows = list(itertools.product(robots, places))
    data = {"name": "Tabelle", "code": "tab", "mode": "plan", "plan_per_person": "4", "plan_decks": "2", "order": "random",
            "col_id": ["c1", "c2"] + ([] if crossing else ["c3"]), "col_name": ["robot", "ort"] + ([] if crossing else ["bild"]),
            "plan_col": ["c1", "c2"], "row_id": [f"r{n}" for n in range(len(rows))]}
    for n, (rb, pl) in enumerate(rows):
        data[f"cell_r{n}_c1"], data[f"cell_r{n}_c2"] = rb, pl
        if not crossing:
            data[f"cell_r{n}_c3"] = f"Bild {n}"
    client.post(f"/surveys/1/flow/loop/{nid}", data=data)
    return nid


def _loop(app, nid):
    with app.app_context():
        return flow.find(flow.tree(db.session.get(Survey, 1)), nid)[0]


def test_subset_of_a_table_in_decks(app, client, survey):
    from app import loop_plan

    nid = _table(app, client, crossing=False)
    node = _loop(app, nid)
    assert node["mode"] == "plan" and node["subset"]["cols"] == ["c1", "c2"]
    with app.app_context():
        assert any("Versuchsplan" in m for _, m, _, _ in loops.check(db.session.get(Survey, 1)))
    client.post(f"/surveys/1/flow/loop/{nid}", data={"action": "plan_generate"})
    node = _loop(app, nid)
    decks = node["subset"]["decks_rows"]
    assert len(decks) == 2 and all(len(d) == 4 for d in decks) and len({r for d in decks for r in d}) == 8
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert loop_plan.current(s, node)
        rows = loops.choose_rows(s, node, random.Random(1))
        assert sorted(rows) in [sorted(d) for d in decks]
    page = client.get(f"/surveys/1/flow/loop/{nid}").get_data(as_text=True)
    assert "loop-row-out" in page and "D-Effizienz" in page and "Ausschnitt nach Versuchsplan" in page
    r = client.post(f"/surveys/1/flow/loop/{nid}", data={"action": "source_factors"})
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Die Spalten robot, ort sind Faktoren" in page and "Decks des Versuchsplans sind geblieben" in page
    node = _loop(app, nid)
    assert [a["code"] for a in node["attributes"]] == ["robot", "ort"] and node["plan"]["decks_rows"] == decks
    assert {r["id"] for r in node["rows"]} == {r for d in decks for r in d}  # the column "bild" drops out


def test_a_crossed_table_turns_into_vignettes(app, client, survey):
    nid = _table(app, client, crossing=True)
    r = client.post(f"/surveys/1/flow/loop/{nid}", data={"action": "source_factors"})
    assert "/vignettes/" in r.headers["Location"]
    assert "alle Kombinationen sind der Versuchsplan" in client.get(r.headers["Location"]).get_data(as_text=True)
    node = _loop(app, nid)
    assert node["source"] == "factors" and [a["code"] for a in node["attributes"]] == ["robot", "ort"]
    assert len(node["rows"]) == 12 and len(node["plan"]["decks_rows"]) == 3


# --- AI suggestions ------------------------------------------------------------------------------


def test_ai_suggestions_are_taken_over_only_when_ticked(app, client, survey, monkeypatch):
    import json as _json

    from app import ai

    answers = {
        "vignette_factors": {"factors": [{"name": "Kosten", "levels": ["kostenlos", "10 € im Monat"], "why": "Preis"},
                                         {"name": "Hersteller", "levels": ["Start-up"], "why": "zu wenige Stufen"}]},
        "vignette_levels": {"levels": ["Zuhause", "im Büro"], "why": "Orte"},
        "vignette_prohibitions": {"pairs": [{"factor_a": "Aussehen", "level_a": "Kiste", "factor_b": "Daten",
                                             "level_b": "in der Cloud", "why": "unplausibel"},
                                            {"factor_a": "Gibt es nicht", "level_a": "x", "factor_b": "Daten", "level_b": "y"}]},
        "vignette_text": {"text": "Ein {{aussehen}} hilft {{einsatzort}}.", "why": ""},
    }
    monkeypatch.setattr(ai, "generate_text", lambda instructions, prompt, purpose="text", item_type=None:
                        _json.dumps(answers[purpose]))
    _add(client)
    node = _node(app)
    page = f"/surveys/1/flow/vignettes/{node['id']}"
    client.post(page, data=FORM)
    html = client.post(page, data={"action": "ai_factors"}).get_data(as_text=True)
    assert "Vorgeschlagene Faktoren" in html and "Kosten" in html and "Hersteller" not in html  # one level: dropped
    client.post(page, data={"action": "ai_take_factors", "f0_name": "Kosten", "f0_level": ["kostenlos", "10 € im Monat"]})
    assert len(_node(app)["attributes"]) == 3  # nothing ticked: nothing taken
    client.post(page, data={"action": "ai_take_factors", "take": ["0"], "f0_name": "Kosten",
                            "f0_level": ["kostenlos", "10 € im Monat", "50 € im Monat"]})
    node = _node(app)
    new = node["attributes"][-1]
    assert new["code"] == "kosten" and new["ai"] and len(new["levels"]) == 3
    assert "KI-generiert" in client.get(page).get_data(as_text=True)
    html = client.post(page, data={"action": "ai_levels", "aid": "a2"}).get_data(as_text=True)
    assert "im Büro" in html and "Weitere Stufen" in html
    client.post(page, data={"action": "ai_take_levels", "aid": "a2", "take": ["im Büro"]})
    assert len(next(a for a in _node(app)["attributes"] if a["id"] == "a2")["levels"]) == 4
    html = client.post(page, data={"action": "ai_pairs"}).get_data(as_text=True)
    assert "Kiste und in der Cloud" in html and "Gibt es nicht" not in html
    client.post(page, data={"action": "ai_take_pairs", "take": ["a1:l13|a3:l32"]})
    assert _node(app)["prohibitions"] == [{"a": "a1", "la": "l13", "b": "a3", "lb": "l32"}]
    html = client.post(page, data={"action": "ai_text"}).get_data(as_text=True)
    assert "Entwurf des Vignettentexts" in html and "Es fehlen die Platzhalter" in html
    client.post(page, data={"action": "ai_take_text", "text": "Ein {{aussehen}} hilft {{einsatzort}} mit {{daten}}."})
    with app.app_context():
        from app.models import Item

        first = db.session.scalars(db.select(Item).filter_by(block_id=2).order_by(Item.position)).first()
        assert first.type == "info" and "{{daten}}" in first.texts["de"]["question"]


# --- documentation: codebook, method section, snapshot, Zenodo -------------------------------------


def test_documentation_names_the_plan_and_ai_factors(app, client, survey):
    import os

    from app import export, methods, snapshots, zenodo

    node, code = _with_answers(app, client)
    with app.app_context():
        import copy

        s = db.session.get(Survey, 1)
        root = copy.deepcopy(flow.tree(s))
        flow.find(root, node["id"])[0]["attributes"][0]["ai"] = True
        s.flow = root
        db.session.commit()
        s = db.session.get(Survey, 1)
        book = {row[0]: row for row in export.codebook(s)}
        note = book["vig_order"][4]
        assert "D-optimaler Ausschnitt: 12 Vignetten" in note and "3 Decks zu 4" in note
        assert "KI-generiert: Faktoren aussehen" in note
        text = methods.draft(methods.facts(s))
        assert "faktoriellen Survey" in text and "3 Decks zu je 4 Vignetten" in text and "von einer KI vorgeschlagen" in text
        assert "Factorial survey" in methods.facts_text(methods.facts(s))
        names = [name for name, _ in zenodo.package(s)]
        assert "vignettes_vig_long.csv" in names
        name = snapshots.save(s)
        assert "vignettes_vig_long.csv" in os.listdir(os.path.join(snapshots.folder(s), name))


def test_data_files_have_vignette_columns_by_position(app, client, survey):
    import csv
    import io

    from app import export

    node, code = _with_answers(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        text = export.to_csv(s)
        rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
        head = list(rows[0])
        assert "vig_deck" in head and "vig_p4" in head and "vig_p1_aussehen" in head and f"{code}_p4" in head
        assert f"{code}_1" not in head and "vig_p5" not in head
        table = export.rows(s, False, ("real",), with_hb=False, with_quality=False)
        for row, raw in zip(rows, table):
            shown = raw["vig_order"].split("|")
            assert row["vig_p1"] == shown[0]
            assert row[f"{code}_p2"] == str(raw[f"{code}_{shown[1]}"])
        book = export.codebook_csv(s)
        assert f"{code}_p1," in book and "vig_p1_aussehen" in book and f"{code}_1," not in book
        cols, data, meta = export._spss_dictionary(s, False, ("real",), None, True, None)
        names = [c for c, _ in cols]
        assert "vig_p1" in names and f"{code}_3" not in names
def test_dashboard_tile_with_the_mean_per_level(app, client, survey):
    from app import boards
    from app.models import Board

    node, code = _with_answers(app, client, persons=30)
    with app.app_context():
        s = db.session.get(Survey, 1)
        known = boards._known(s)
        keys = [k for k in known if "@f-" in k]
        assert len(keys) == 3 and not any(k.count("@") and "@p" in k for k in known)
        key = next(k for k in keys if k.endswith("@f-a1"))
        board = Board(survey=s, name="Test", tiles=[{"key": key, "view": "means", "w": 6}], position=0)
        db.session.add(board)
        db.session.commit()
        data = boards._build(board, "de")
    tile = next(t for t in data["tiles"] if t.get("key") == key) if isinstance(data, dict) else None
    assert tile is not None
    assert "Kiste" in str(tile) and "humanoid" in str(tile) and tile["title"].endswith("· Aussehen")


def test_a_plan_for_two_way_interactions(app, client, survey):
    levels, pairs = [3, 3, 2], [(0, 1)]
    assert doe.n_params(levels, pairs) == 1 + 2 + 2 + 1 + 4
    full = doe.full_factorial(levels)
    assert doe.d_efficiency(full, levels, pairs) == pytest.approx(100)
    D = doe.d_optimal(levels, 12, seed=4, pairs=pairs)
    assert doe.d_efficiency(D, levels, pairs) > 0  # the interaction can be estimated
    with pytest.raises(ValueError):
        doe.d_optimal(levels, 9, pairs=pairs)  # 10 parameters
    _add(client)
    node = _node(app)
    page = f"/surveys/1/flow/vignettes/{node['id']}"
    client.post(page, data=FORM)
    old_sig = vignettes.signature(_survey(app), _node(app))
    client.post(page, data={**FORM, "interaction": ["a1|a2"], "per_person": "6", "decks": "3"})
    node = _node(app)
    assert node["plan"]["interactions"] == [["a1", "a2"]] and vignettes.pairs(node) == [(0, 1)]
    assert vignettes.signature(_survey(app), node) != old_sig
    client.post(page, data={"action": "generate"})
    assert vignettes.plan_current(_survey(app), _node(app))
    assert "Wechselwirkungen" in vignettes.codebook_note(_survey(app), _node(app))


def test_a_factor_that_varies_only_between_persons(app, client, survey):
    _add(client)
    node = _node(app)
    page = f"/surveys/1/flow/vignettes/{node['id']}"
    assert "nur zwischen Personen" in client.get(page).get_data(as_text=True)
    client.post(page, data={**FORM, "attr_between": ["a3"], "per_person": "6", "decks": "3"})
    r = client.post(page, data={"action": "generate"})
    assert not vignettes.plan_current(_survey(app), _node(app))  # 3 decks for 2 levels of "Daten": refused
    client.post(page, data={**FORM, "attr_between": ["a3"], "per_person": "3", "decks": "4"})
    client.post(page, data={"action": "generate"})
    node = _node(app)
    assert node["attributes"][2]["between"] and vignettes.plan_current(_survey(app), node)
    plan = node["plan"]
    profiles = dict(zip(plan["ids"], plan["profiles"]))
    for deck in plan["decks_rows"]:
        assert len({profiles[r][2] for r in deck}) == 1  # one level of "Daten" per deck
    assert sorted(profiles[d[0]][2] for d in plan["decks_rows"]) == [0, 0, 1, 1]
    assert "nur zwischen Personen" in vignettes.codebook_note(_survey(app), node)


# --- code review 2026-10-15, package C -------------------------------------------------------------


def test_analysis_leaves_out_missing_codes_and_nominal_questions(app, client, survey):
    """C1: "weiß nicht" (-98) went into the means, the regression and the ICC; C2: a nominal question was averaged.
    Missing codes are left out as in hypotheses._values; a question not measured on a scale is named, not analysed."""
    from app import export
    from app.models import Item

    node, code = _with_answers(app, client, persons=30)
    with app.app_context():
        item = db.session.scalar(db.select(Item).filter_by(block_id=2, type="rating"))
        item.config = {**item.config, "dont_know": True}
        s = db.session.get(Survey, 1)
        first = min(s.responses, key=lambda r: r.id)
        first.answers = {k: "dk" for k in first.answers}  # all four of one person "weiß nicht"
        db.session.commit()
    add_choice = client.post("/surveys/1/editor/blocks/2/items", data={"type": "single_choice"})
    assert add_choice.status_code in (200, 302)
    with app.app_context():
        s = db.session.get(Survey, 1)
        table = export.rows(s, False, ("real",), with_hb=False, with_quality=False)
        assert any(v == -98 for r in table for k, v in r.items() if k.startswith(code + "_"))
        found = vignettes.analysis(s, table)
        choice = db.session.scalar(db.select(Item).filter_by(block_id=2, type="single_choice"))
    q = found[0]["questions"][0]
    assert q["code"] == code and q["n"] == 116 and q["persons"] == 29
    assert all(l["mean"] is None or 1 <= l["mean"] <= 5 for f in q["by_level"] for l in f["levels"])
    assert [x["code"] for x in found[0]["questions"]] == [code] and found[0]["skipped"] == [choice.code]
    page = client.get("/surveys/1/results").get_data(as_text=True)
    assert "nicht auf einer Skala gemessen" in page and choice.code in page


def test_plan_is_not_made_again_over_real_answers_and_ids_are_new(app, client, survey):
    """C3: the row ids came from the signature and the position only: a plan made again after "Zur Bearbeitung
    öffnen" gave the new profiles the ids of the old ones, and the stored answers were read with the new levels."""
    import copy

    node, code = _with_answers(app, client)
    r = client.post(f"/surveys/1/flow/vignettes/{node['id']}", data={"action": "generate"}, follow_redirects=True)
    assert "echte Antworten" in r.get_data(as_text=True)
    assert _node(app)["plan"]["ids"] == node["plan"]["ids"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        again = copy.deepcopy(node)
        vignettes.generate(s, again, seed=1)
        once_more = copy.deepcopy(node)
        vignettes.generate(s, once_more, seed=1)
    assert len(set(again["plan"]["ids"])) == len(again["plan"]["ids"])
    assert not set(again["plan"]["ids"]) & set(node["plan"]["ids"])
    assert not set(again["plan"]["ids"]) & set(once_more["plan"]["ids"])


def test_codebook_names_the_levels_of_the_right_factor(app, client, survey):
    """C8: the factor of a level column was found by the end of its name, so "alter" took the column of "x_alter"."""
    from app import export

    _add(client)
    node = _node(app)
    form = {**FORM, "attr_code": ["alter", "x_alter", ""]}
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data=form)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data={"action": "generate"})
    node = _node(app)
    assert [a["code"] for a in node["attributes"]][:2] == ["alter", "x_alter"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        from app import vignette_positions

        book = {row[0]: row for row in vignette_positions.codebook(s, export.codebook(s))}
    assert "Pflegeheim" in book["vig_p1_x_alter"][3] and "humanoid" not in book["vig_p1_x_alter"][3]
    assert "humanoid" in book["vig_p1_alter"][3]
