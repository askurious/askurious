"""Audit data and calculations 2026-10-10, package H (study plan and hypotheses): B-16 to B-25."""
import math
import random

import pytest
from werkzeug.datastructures import MultiDict

from app import group_tests as gt
from app import hypotheses, methods_text, power, study_plan
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add


def _items(client, app):
    """2: geschlecht (nominal, three answers), 3: bildung (ordinal, four), 4: alter (a number)."""
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict({
        "code": "geschlecht", "question": "Geschlecht?", "option_id": ["a", "b", "c"], "option_code": ["1", "2", "3"],
        "option_label": ["weiblich", "männlich", "divers"]}))
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/3", data=MultiDict({
        "code": "bildung", "question": "Bildung?", "option_id": ["a", "b", "c", "d"], "option_code": ["1", "2", "3", "4"],
        "option_label": ["niedrig", "mittel", "hoch", "sehr hoch"]}))
    add(client, "text_input")
    client.post("/surveys/1/editor/items/4", data={"code": "alter", "question": "Alter?", "validation": "integer"})
    with app.app_context():
        item = db.session.get(Item, 3)
        item.config = {**item.config, "level": "ordinal"}
        db.session.commit()


def _set_plan(app, *hyps, **sample):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.study_plan = {"hypotheses": [{"id": f"h{i}", "text": f"H {i}", "direction": "", **h} for i, h in enumerate(hyps)],
                        "sample": sample}
        db.session.commit()


# --- B-16 ------------------------------------------------------------------------------------------------------------

def test_b16_a_nominal_outcome_is_planned_and_tested_with_chi2(app, client, survey):
    _items(client, app)
    _set_plan(app, {"kind": "correlation", "iv": "bildung", "dv": "geschlecht", "effect": 0.3},
              {"kind": "difference", "iv": "bildung", "dv": "geschlecht"},
              {"kind": "difference", "iv": "geschlecht", "dv": "bildung"},
              {"kind": "correlation", "iv": "alter", "dv": "geschlecht", "effect": 0.3})
    with app.test_request_context("/"):
        s = db.session.get(Survey, 1)
        h1, h2, h3, h4 = study_plan.plan_of(s)["hypotheses"]
        assert study_plan.suggest(s, h1) == study_plan.suggest(s, h2) == "chi2"
        assert study_plan.suggest(s, h3) == "kw"                       # the compared variable is ordered: ranks
        p = study_plan.power_of(s, h1)
        assert p["measure"] == "w" and p["df"] == 6 and p["n"] == power.needed("chi2", 0.3, 0.8, 0.05, 2, 6)
        assert "nominal" in study_plan.test_reason(s, h1) and "beide Variablen nominal" not in study_plan.test_reason(s, h1)
        # metric against nominal: no test the results could compute, so no N, and a warning instead of "nur geordnet"
        assert study_plan.suggest(s, h4) == "" and study_plan.power_of(s, h4)["n"] is None
        assert "„geschlecht“ ist nominal" in " ".join(study_plan.reasons(s, h4))
        assert "Spearman" not in study_plan.test_reason(s, h4) and "nominal" in study_plan.test_reason(s, h4)
        assert any("H4" in f.message and "nominal" in f.message for f in study_plan.findings(s))
        rng = random.Random(1)
        table = [{"bildung": str(rng.randint(1, 4)), "geschlecht": str(rng.randint(1, 2))} for _ in range(120)]
        r1 = hypotheses.evaluate_one(s, h1, table)
    assert r1["tested"] == "chi2" and "V = " in r1["sentence"] and r1["p"] is not None


# --- B-17 ------------------------------------------------------------------------------------------------------------

class _Rows(list):
    def keep(self, test):
        return _Rows(r for r in self if test(r))


def test_b17_a_planned_group_below_the_minimum_is_not_reached(app, client, survey, monkeypatch):
    from app import compare, surveys

    _items(client, app)
    _set_plan(app, {"kind": "difference", "iv": "geschlecht", "dv": "alter", "effect": 0.4})
    monkeypatch.setattr(compare, "table_of", lambda *a, **k: _Rows([{}] * 123))
    monkeypatch.setattr(surveys, "stored_pool", lambda s: {"sources": ["real"]})
    fake = {"sizes": [60, 60], "groups": [{"code": "1", "n": 60}, {"code": "2", "n": 60}, {"code": "3", "n": 3}]}
    monkeypatch.setattr(hypotheses, "evaluate", lambda *a, **k: [fake])
    with app.test_request_context("/"):
        s = db.session.get(Survey, 1)
        [row] = study_plan.progress(s)["rows"]
        assert row["groups"] == 3 and row["have"] == 3 and row["share"] < 1
        fake["groups"] = fake["groups"][:2]                                      # the third group has no answer: 0
        [row] = study_plan.progress(s)["rows"]
        assert row["have"] == 0 and row["share"] == 0


# --- B-18 ------------------------------------------------------------------------------------------------------------

def test_b18_the_methods_draft_names_one_target(app, client, survey):
    from app import methods

    assert "angestrebt" not in methods_text.TEXT["de"]["power"] and "nötig" in methods_text.TEXT["de"]["power"]
    assert "aimed at" not in methods_text.TEXT["en"]["power"] and "needed" in methods_text.TEXT["en"]["power"]
    _items(client, app)
    _set_plan(app, {"kind": "correlation", "iv": "alter", "dv": "bildung", "effect": 0.3}, target=150)
    with app.test_request_context("/"):
        text = methods.facts_text(methods.facts(db.session.get(Survey, 1)))
    assert "Planned sample (usable" in text and "Target sample size (completions, set in the research plan): 150" in text


# --- B-19, B-20, B-21 ------------------------------------------------------------------------------------------------

def test_b19_the_effect_shows_the_decimals_its_n_was_computed_with():
    assert study_plan.effect_text({"measure": "r", "effect": 0.125}, "de") == "r = 0,125"
    assert study_plan.effect_text({"measure": "d", "effect": 0.5}, "de") == "d = 0,50"
    assert study_plan.effect_text({"measure": "d", "effect": 0.12}, "en") == "d = 0.12"
    assert study_plan.effect_text({"measure": "r", "effect": 0.125}, "en") == "r = .125"   # B-20


def test_b20_b21_english_power_line_sentences_and_markdown(app, client, survey):
    _items(client, app)
    _set_plan(app, {"kind": "correlation", "iv": "alter", "dv": "bildung", "effect": 0.3})
    with app.test_request_context("/"):
        s = db.session.get(Survey, 1)
        [h] = study_plan.plan_of(s)["hypotheses"]
        en, de = study_plan.power_line(s, h, "en"), study_plan.power_line(s, h, "de")
        assert "power 80%," in en and "r = .30" in en and "Power 80 %," in de and "r = 0,30" in de
        table = [{"alter": str(20 + i), "bildung": str(1 + i % 4)} for i in range(40)]
        out_en, out_de = hypotheses.evaluate_one(s, h, table, "en"), hypotheses.evaluate_one(s, h, table, "de")
        assert out_en["sentence"].split(" = ")[1].startswith((".", "−.")) and " = 0," in out_de["sentence"]
        assert "variable B" in study_plan.markdown(s, "en") and "variable b " not in study_plan.markdown(s, "en")
    assert methods_text._bounded(0.857, "en") == ".86" and methods_text._bounded(0.857, "de") == "0,86"


# --- B-22 ------------------------------------------------------------------------------------------------------------

def test_b22_the_calculator_computes_with_the_whole_per_cent_it_passes_on():
    from app import sample_model
    from tests.analysis.test_samplesize import NODE, js

    if NODE is None:
        pytest.skip("needs Node")
    [r] = js(["plan", "groups", {"effect": 0.62, "alpha": 0.05, "power": 0.8, "onesided": False, "dropout": 12.5}])
    assert r["completions"] == sample_model.completions(r["total"], 13)


# --- B-23 ------------------------------------------------------------------------------------------------------------

def test_b23_nothing_ticked_beside_none_of_them_is_missing(app, client, survey):
    from app import groupvars

    add(client, "multiple_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict({
        "code": "medien", "question": "Welche?", "option_id": ["a", "b", "c"], "option_code": ["1", "2", "3"],
        "option_label": ["Zeitung", "Radio", "keine davon"]}))
    with app.app_context():
        item = db.session.get(Item, 2)
        options = [dict(o) for o in item.config["options"]]
        options[2]["exclusive"] = True
        item.config = {**item.config, "options": options}
        db.session.commit()
        item = db.session.get(Item, 2)
        assert item.kind.export_values(item, []) == {"medien_1": None, "medien_2": None, "medien_3": None}
        assert item.kind.export_values(item, ["3"])["medien_3"] == 1 and item.kind.export_values(item, ["3"])["medien_1"] == 0
        var = groupvars._multiple(db.session.get(Survey, 1))[f"mc:{item.id}:1"]
        assert var.untouched and var.member({"medien_1": 0, "medien_2": 0, "medien_3": 0}) is None
        item.answer_mode = "required"                          # a required question cannot be left empty: 0 stays
        assert item.kind.export_values(item, [])["medien_1"] == 0
        item.answer_mode = "optional"
        options[2]["exclusive"] = False                        # without "keine davon": 0, as decision 5 had it
        item.config = {**item.config, "options": options}
        assert item.kind.export_values(item, [])["medien_1"] == 0
        db.session.rollback()


# --- B-24, B-25 ------------------------------------------------------------------------------------------------------

def test_b24_sensitivity_of_welch_names_delta_star(app, client, survey):
    _items(client, app)
    _set_plan(app, {"kind": "difference", "iv": "geschlecht", "dv": "alter", "effect": 0.5, "groups": ["1", "2"]})
    with app.test_request_context("/"):
        s = db.session.get(Survey, 1)
        [h] = study_plan.plan_of(s)["hypotheses"]
        table = ([{"geschlecht": "1", "alter": str(30 + i % 9)} for i in range(20)]
                 + [{"geschlecht": "2", "alter": str(33 + i % 7)} for i in range(50)])
        out = hypotheses.evaluate_one(s, h, table)
    text = out["sensitivity"]["text"]
    assert out["tested"] == "t" and "ab δ* = " in text and "Geplant war δ* = 0,50" in text
    assert "d = " not in text.replace("gleich d bei", "")


def test_b25_delta_star_is_corrected_for_small_groups():
    x, y = [5.1, 4.8, 6.0, 5.5, 5.9], [6.5, 7.1, 5.9, 6.8, 7.4]
    r = gt.welch_t(x, y)
    raw = r["d_raw"]
    s1 = sum((a - sum(x) / 5) ** 2 for a in x) / 4
    s2 = sum((a - sum(y) / 5) ** 2 for a in y) / 4
    m = (s1 + s2) ** 2 / (s1 ** 2 / 4 + s2 ** 2 / 4)
    assert math.isclose(r["d"], raw * (1 - 3 / (4 * m - 1)), rel_tol=1e-12) and abs(r["d"]) < abs(raw)
    assert r["d_ci"][0] < r["d"] < r["d_ci"][1]
    # the case of the audit: 8/30 with SD 3/1 at δ* = 1.5 was +0.13 too large; corrected within a few hundredths
    rng = random.Random(4)
    diff = 1.5 * math.sqrt((9 + 1) / 2)
    ds = [gt.welch_t([rng.gauss(diff, 3) for _ in range(8)], [rng.gauss(0, 1) for _ in range(30)])["d"]
          for _ in range(3000)]
    assert abs(sum(ds) / len(ds) - 1.5) < 0.04
