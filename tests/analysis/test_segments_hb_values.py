"""The individual HB values per segment (segments._hb_means): every person in their most likely segment, per segment
mean, sample SD and n, the part-worths raw and rescaled per person, the importances, MaxDiff probability scores;
checked against a calculation by hand."""
from types import SimpleNamespace

import numpy as np
import pytest

from app import conjoint_hb, maxdiff_hb, segments

ATTRS = [{"label": "Farbe", "code": "farbe", "level_labels": ["Rot", "Blau"]},
         {"label": "Form", "code": "form", "level_labels": ["Rund", "Eckig", "Oval"]}]
# five people: part-worths of "Farbe" (2 levels) and "Form" (3 levels), zero-centred per attribute
UTIL = [[1.0, -1.0, 2.0, 0.0, -2.0],      # ranges 2 + 4 = 6: factor 200 / 6
        [3.0, -3.0, 1.0, 1.0, -2.0],      # ranges 6 + 3 = 9: factor 200 / 9
        [-0.5, 0.5, 0.0, 1.0, -1.0],      # ranges 1 + 2 = 3: factor 200 / 3
        [-2.0, 2.0, -1.0, 2.0, -1.0],     # ranges 4 + 3 = 7: factor 200 / 7
        [0.0, 0.0, 0.0, 0.0, 0.0]]        # ranges 0: no factor, left out of the rescaled values
IMPORTANCE = [[2 / 6, 4 / 6], [6 / 9, 3 / 9], [1 / 3, 2 / 3], [4 / 7, 3 / 7], [0.5, 0.5]]
# membership probabilities: persons 0, 1 in segment 1; 2, 3, 4 in segment 2; nobody in segment 3
TAU = [[0.6, 0.3, 0.1], [0.9, 0.05, 0.05], [0.2, 0.7, 0.1], [0.4, 0.45, 0.15], [0.1, 0.8, 0.1]]


def _cbc_item(monkeypatch, hb):
    kind = SimpleNamespace(attributes=lambda item, lang: ATTRS, price_attribute=lambda item: None)
    item = SimpleNamespace(type="conjoint", config={}, kind=kind, survey=SimpleNamespace(default_language="de"))
    monkeypatch.setattr(conjoint_hb, "current", lambda it: hb)
    monkeypatch.setattr(segments.lc, "MIN_PERSONS", 3)
    return item


def _hb(pids):
    return {"pids": pids, "util": UTIL, "spans": [(0, 0, 2), (1, 2, 3)], "importance": IMPORTANCE, "price": None}


def test_cbc_hard_assignment_mean_sd_n_on_both_scales(monkeypatch):
    pids = ["a", "b", "c", "d", "e"]
    item = _cbc_item(monkeypatch, _hb(pids))
    out = segments._hb_means(item, {"pids": pids}, {"tau": TAU, "pi": [0.4, 0.5, 0.1]}, 3)
    assert out["kind"] == "cbc" and out["form"] == segments.HB_MEANS and out["persons"] == 5 and out["n"] == [2, 3, 0]
    rot = out["rows"][0]["levels"][0]
    assert out["rows"][0]["label"] == "Farbe" and rot["label"] == "Rot"
    # raw: segment 1 = persons a, b (1 and 3); segment 2 = c, d, e (-0.5, -2, 0); segment 3 empty
    assert rot["values"] == [pytest.approx(2.0), pytest.approx(-2.5 / 3), None]
    assert rot["sd"][0] == pytest.approx(np.sqrt(2.0))                     # ((1 - 2)² + (3 - 2)²) / (2 - 1) = 2
    assert rot["sd"][1] == pytest.approx(np.std([-0.5, -2.0, 0.0], ddof=1)) and rot["sd"][2] is None
    # not weighted with the probabilities any more: the weighted mean of segment 1 would be another number
    weighted = sum(t[0] * u[0] for t, u in zip(TAU, UTIL)) / sum(t[0] for t in TAU)
    assert abs(weighted - rot["values"][0]) > 0.1
    oval = out["rows"][1]["levels"][2]
    assert oval["values"][:2] == [pytest.approx(-2.0), pytest.approx(-2 / 3)] and oval["sd"][0] == pytest.approx(0.0)
    # rescaled per person: person e (ranges 0) is left out, so segment 2 has two people
    diffs = out["diffs"]
    assert diffs["persons"] == 4 and diffs["left_out"] == 1 and diffs["n"] == [2, 2, 0]
    a, b, c, d = 200 / 6, 200 / 9, 200 / 3, 200 / 7
    rot = diffs["rows"][0]["levels"][0]
    assert rot["values"][0] == pytest.approx((1.0 * a + 3.0 * b) / 2)
    assert rot["values"][1] == pytest.approx((-0.5 * c - 2.0 * d) / 2) and rot["values"][2] is None
    assert rot["sd"][0] == pytest.approx(abs(1.0 * a - 3.0 * b) / np.sqrt(2))   # two values: |x1 - x2| / √2
    assert rot["sd"][1] == pytest.approx(abs(-0.5 * c + 2.0 * d) / np.sqrt(2))
    # importances: mean and SD of the individual importances, all five people (the scale does not matter)
    farbe, form = out["importance"]
    assert (farbe["label"], form["label"]) == ("Farbe", "Form")
    assert farbe["values"][0] == pytest.approx((2 / 6 + 6 / 9) / 2) and farbe["values"][2] is None
    assert farbe["values"][1] == pytest.approx((1 / 3 + 4 / 7 + 0.5) / 3)
    assert farbe["sd"][1] == pytest.approx(np.std([1 / 3, 4 / 7, 0.5], ddof=1)) and farbe["sd"][2] is None
    assert form["values"][0] == pytest.approx(1 - farbe["values"][0])


def test_segment_of_one_person_has_no_sd_and_only_common_persons_count(monkeypatch):
    # the HB result knows a, b, c, e and in another order; d has segments but no HB values
    hb = _hb(["e", "c", "b", "a"])
    hb["util"] = [UTIL[4], UTIL[2], UTIL[1], UTIL[0]]
    hb["importance"] = [IMPORTANCE[4], IMPORTANCE[2], IMPORTANCE[1], IMPORTANCE[0]]
    item = _cbc_item(monkeypatch, hb)
    tau = [[0.9, 0.1], [0.2, 0.8], [0.3, 0.7], [0.6, 0.4], [0.45, 0.55]]      # a alone in segment 1; d has no HB values
    out = segments._hb_means(item, {"pids": ["a", "b", "c", "d", "e"]}, {"tau": tau, "pi": [0.4, 0.6]}, 2)
    assert out["persons"] == 4 and out["n"] == [1, 3]
    rot = out["rows"][0]["levels"][0]
    assert rot["values"] == [pytest.approx(1.0), pytest.approx((3.0 - 0.5 + 0.0) / 3)]
    assert rot["sd"][0] is None and rot["sd"][1] == pytest.approx(np.std([3.0, -0.5, 0.0], ddof=1))
    assert out["importance"][0]["sd"][0] is None
    assert out["diffs"]["n"] == [1, 2] and out["diffs"]["rows"][0]["levels"][0]["sd"][0] is None
    # a tie of the probabilities goes to the first segment, as argmax does for the data set column <code>_lk
    out = segments._hb_means(item, {"pids": ["a", "b", "c", "e"]},
                             {"tau": [[0.5, 0.5], [0.2, 0.8], [0.3, 0.7], [0.4, 0.6]], "pi": [0.4, 0.6]}, 2)
    assert out["n"] == [1, 3]
    # too few common people, one segment, no HB result: nothing
    monkeypatch.setattr(segments.lc, "MIN_PERSONS", 5)
    assert segments._hb_means(item, {"pids": ["a", "b", "c", "e"]}, {"tau": tau[:4], "pi": [0.4, 0.6]}, 2) is None
    monkeypatch.setattr(segments.lc, "MIN_PERSONS", 3)
    assert segments._hb_means(item, {"pids": ["a", "b", "c", "e"]}, {"tau": tau[:4], "pi": [1.0]}, 1) is None
    monkeypatch.setattr(conjoint_hb, "current", lambda it: None)
    assert segments._hb_means(item, {"pids": ["a", "b", "c", "e"]}, {"tau": tau[:4], "pi": [0.4, 0.6]}, 2) is None


def test_loose_attribute_without_importance_and_price_importance(monkeypatch):
    pids = ["a", "b", "c", "d"]
    hb = {"pids": pids, "util": [u[:2] for u in UTIL[:4]], "spans": [(0, 0, 2)], "price": [-0.1, -0.2, -0.3, -0.4],
          "has_price_importance": True, "price_range": [10, 20],
          "importance": [[0.25, 0.75], [0.5, 0.5], [0.75, 0.25], [1.0, 0.0]]}
    item = _cbc_item(monkeypatch, hb)
    item.kind.price_attribute = lambda it: 1                             # "Form" is the price attribute here
    sol = {"tau": [[0.9, 0.1], [0.8, 0.2], [0.1, 0.9], [0.3, 0.7]], "pi": [0.5, 0.5]}
    out = segments._hb_means(item, {"pids": pids}, sol, 2)
    farbe, price = out["importance"]
    assert price["label"] == "Form" and price["values"] == [pytest.approx(0.625), pytest.approx(0.125)]
    assert farbe["sd"] == [pytest.approx(np.std([0.25, 0.5], ddof=1)), pytest.approx(np.std([0.75, 1.0], ddof=1))]
    # rescaled with the price range in the sum of the ranges (conjoint_hb.diff_factors): person a 200 / (2 + 0.1 * 10)
    assert out["diffs"]["rows"][0]["levels"][0]["values"][0] == pytest.approx((1.0 * 200 / 3 + 3.0 * 200 / 8) / 2)
    hb["loose"] = [0]                                                    # only the prior sets "Farbe": no importance
    out = segments._hb_means(item, {"pids": pids}, sol, 2)
    assert [r["label"] for r in out["importance"]] == ["Form"]
    # person d has no price range: importances() gives 0 where the remaining ranges sum to 0
    assert out["importance"][0]["values"] == [pytest.approx(1.0), pytest.approx(0.5)]


def test_maxdiff_probability_scores_per_segment(monkeypatch):
    pids = ["a", "b", "c", "d"]
    prob = [[60.0, 30.0, 10.0], [40.0, 50.0, 10.0], [10.0, 20.0, 70.0], [20.0, 20.0, 60.0]]
    monkeypatch.setattr(maxdiff_hb, "current", lambda it: {"pids": pids, "prob": prob, "est": [0, 2, 1]})
    monkeypatch.setattr(segments.lc, "MIN_PERSONS", 3)
    kind = SimpleNamespace(labels=lambda item, lang: ["Erste", "Zweite", "Dritte"])
    item = SimpleNamespace(type="maxdiff", config={}, kind=kind, survey=SimpleNamespace(default_language="de"))
    tau = [[0.9, 0.1], [0.6, 0.4], [0.2, 0.8], [0.45, 0.55]]
    out = segments._hb_means(item, {"pids": pids}, {"tau": tau, "pi": [0.5, 0.5]}, 2)
    assert out["kind"] == "maxdiff" and out["n"] == [2, 2] and "diffs" not in out and "importance" not in out
    # sorted by the mean score of everyone: Dritte (second column: 30), Erste (32.5), Zweite (37.5)
    assert [r["label"] for r in out["rows"]] == ["Zweite", "Erste", "Dritte"]
    erste = out["rows"][1]
    assert erste["values"] == [pytest.approx(50.0), pytest.approx(15.0)]
    assert erste["sd"] == [pytest.approx(np.sqrt(200.0)), pytest.approx(np.sqrt(50.0))]


def test_kept_views_of_the_earlier_form_are_computed_anew():
    item = SimpleNamespace(config={}, type="conjoint")
    stored = {"key": "k", "solutions": [{"K": 2}], "suggested": 2}
    assert segments.enriched_key(item, stored)[-1] == segments.HB_MEANS   # the key kept before had no such entry
