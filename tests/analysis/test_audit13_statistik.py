"""Audit Daten und Berechnungen 2026-10-12, package F (hypotheses and statistics): S-2, S-4, S-5, S-6, S-8, S-9,
S-10. Reference values from independent reference computations."""
import math
import statistics

import pytest
from werkzeug.datastructures import MultiDict

from app import group_tests as gt
from app import hypotheses, scales, study_plan, weights
from app.extensions import db
from app.models import Block, Survey
from tests.conftest import add


def _choice(client, item_id, code, options, level):
    data = [("code", code), ("question", code + "?"), ("level", level)]
    for oid, ocode, label in options:
        data += [("option_id", oid), ("option_code", ocode), ("option_label", label)]
    client.post(f"/surveys/1/editor/items/{item_id}", data=MultiDict(data))


def _groups_item(client):
    add(client, "single_choice")  # 2: the groups
    _choice(client, 2, "grp", [("a", "1", "Gruppe A"), ("b", "2", "Gruppe B")], "nominal")


def test_s2_ordinal_answers_in_option_order(app, client, survey):
    """S-2: codes 1, 3, 2 for niedrig < mittel < hoch; the test ranks by the order of the options."""
    _groups_item(client)
    add(client, "single_choice")
    _choice(client, 3, "bild", [("x", "1", "niedrig"), ("z", "3", "mittel"), ("y", "2", "hoch")], "ordinal")
    with app.app_context():
        s = db.session.get(Survey, 1)
        table = ([{"grp": "1", "bild": "2"}] * 8 + [{"grp": "1", "bild": "1"}] * 2
                 + [{"grp": "2", "bild": "3"}] * 8 + [{"grp": "2", "bild": "1"}] * 2)
        h = {"text": "A höher als B", "kind": "difference", "iv": "grp", "dv": "bild", "analysis": "u",
             "direction": "pos"}
        out = hypotheses.evaluate_one(s, h, table)
        ref = gt.mann_whitney([2] * 8 + [0] * 2, [1] * 8 + [0] * 2)
        assert out["effect"][1] == pytest.approx(ref["r"]) and out["effect"][1] > 0
        assert out["p"] == pytest.approx(ref["p"])
        assert out["verdict"] == "unterstützt"
        assert [g["median_text"] for g in out["groups"]] == ["hoch", "mittel"]


def test_s5_letter_codes_are_tested(app, client, survey):
    _groups_item(client)
    add(client, "single_choice")
    _choice(client, 3, "bild", [("x", "a", "niedrig"), ("y", "b", "mittel"), ("z", "c", "hoch")], "ordinal")
    with app.app_context():
        s = db.session.get(Survey, 1)
        table = ([{"grp": "1", "bild": "c"}] * 8 + [{"grp": "1", "bild": "a"}] * 2
                 + [{"grp": "2", "bild": "a"}] * 8 + [{"grp": "2", "bild": "b"}] * 2)
        h = {"text": "A höher als B", "kind": "difference", "iv": "grp", "dv": "bild", "analysis": "u",
             "direction": "pos"}
        out = hypotheses.evaluate_one(s, h, table)
        assert out["p"] is not None and out["p"] < 0.05 and out["verdict"] == "unterstützt"


def test_s6_multiple_choice_not_tested_with_a_note(app, client, survey):
    _groups_item(client)
    add(client, "multiple_choice")
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "mc"), ("question", "Was?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Rad"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Auto")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        h = {"text": "Gruppe hängt mit Besitz zusammen", "kind": "difference", "iv": "grp", "dv": "mc"}
        assert study_plan.suggest(s, h) == ""
        assert study_plan.power_of(s, h)["n"] is None
        assert any("Mehrfachauswahl" in r for r in study_plan.reasons(s, h))
        table = [{"grp": "1", "mc_1": 1, "mc_2": 0}] * 8 + [{"grp": "2", "mc_1": 0, "mc_2": 1}] * 8
        out = hypotheses.evaluate_one(s, dict(h, analysis="chi2"), table)
        assert out.get("p") is None and any("Mehrfachauswahl" in n for n in out["notes"])
        assert not any("Weniger als zwei Gruppen" in n for n in out["notes"])


def test_s4_randomizer_groups_in_flow_order(app, client, survey, monkeypatch):
    add(client, "rating")  # 2: outcome
    client.post("/surveys/1/editor/items/2", data={"code": "y", "question": "y?", "style": "slider", "min": "0",
                                                    "max": "100", "step": "1", "level": "metric"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        ids = []
        for title in ("Treatment", "Kontrolle"):
            b = Block(survey_id=s.id, position=len(s.blocks) + 1, titles={"de": title})
            db.session.add(b)
            db.session.flush()
            ids.append(b.id)
        db.session.commit()
        rand = {"t": "rand", "code": "cond", "k": 1, "name": "Bedingung",
                "children": [{"t": "block", "block": ids[0]}, {"t": "block", "block": ids[1]}]}
        real = study_plan._item
        monkeypatch.setattr(study_plan, "_item", lambda sv, code: rand if code == "cond" else real(sv, code))
        table = ([{"cond": "Treatment", "y": v} for v in (70, 75, 80, 85, 90, 72, 78)]
                 + [{"cond": "Kontrolle", "y": v} for v in (50, 55, 60, 52, 58, 61, 49)])
        h = {"text": "Treatment wirkt", "kind": "causal", "iv": "cond", "dv": "y", "analysis": "t",
             "direction": "pos"}
        out = hypotheses.evaluate_one(s, h, table)
        assert [g["label"] for g in out["groups"]] == ["Treatment", "Kontrolle"]
        assert out["verdict"] == "unterstützt"
        assert "Treatment" in study_plan.direction_hint(s, h)


def _rho(y):
    x = [float(v) for v in range(1, len(y) + 1)]
    return scales.pearson(hypotheses._ranks(x), hypotheses._ranks([float(v) for v in y]))


@pytest.mark.parametrize("y, p", [
    ([1, 3, 2, 4], 1 / 3),  # S = 2, mean 10: S in {0, 2, 18, 20} for 1 + 3 + 3 + 1 of the 24 permutations
    ([2, 1, 4, 3, 6, 8, 5, 7], 0.0153769841269841),
])
def test_s8_spearman_exact(y, p):
    r, n, _ = _rho(y)
    assert gt.spearman_p(r, n) == pytest.approx(p, rel=1e-9)


def test_s8_spearman_exact_counts_match_brute_force():
    """The dynamic programming of _s_counts against all 8! permutations, counted one by one."""
    import itertools
    from collections import Counter

    n = 8
    brute = Counter(sum((i - q) ** 2 for i, q in enumerate(perm)) for perm in itertools.permutations(range(n)))
    counts = gt._s_counts(n)
    assert {s: c for s, c in enumerate(counts) if c} == dict(brute)


def test_s8_spearman_exact_up_to_twelve_pairs():
    """n = 12 without ties lies in the exact range (Askurious default SPEARMAN_EXACT = 12): 8.39e-05; the t
    distribution with 10 df gives 2.84e-05 here."""
    r, n, p_t = _rho([3, 1, 2, 6, 4, 5, 9, 7, 8, 12, 10, 11])
    assert n == gt.SPEARMAN_EXACT
    assert gt.spearman_p(r, n) == pytest.approx(8.389533563144674e-05, rel=1e-12)
    assert p_t == pytest.approx(2.84280453485474e-05, rel=1e-8)


@pytest.mark.parametrize("n", [5, 9, 13])
def test_s8_spearman_moments_match_exact_distribution(n):
    """E(rho^2), E(rho^4), E(rho^6) of _rho_moments equal the moments of the exact distribution of S."""
    from fractions import Fraction

    counts, whole = gt._s_counts(n), math.factorial(n)
    exact = [sum(Fraction(c) * (1 - Fraction(6 * s, n ** 3 - n)) ** k for s, c in enumerate(counts) if c) / whole
             for k in (2, 4, 6)]
    assert list(gt._rho_moments(n)) == exact


def test_s8_spearman_approximation_against_exact_at_13():
    """First n above the exact range: the fitted beta approximation stays within 1.2 % of the exact p for p between
    .001 and .2 (measured 1.1 %) and within 4e-4 everywhere (measured 3.2e-4)."""
    n, whole = 13, math.factorial(13)
    counts, mean = gt._s_counts(n), (n ** 3 - n) / 6
    worst_rel = worst_abs = 0.0
    for s, c in enumerate(counts):
        if not c or s < mean:
            continue
        exact = sum(k for t, k in enumerate(counts) if k and abs(t - mean) >= s - mean) / whole
        approx = gt.spearman_p(1 - 6 * s / (n ** 3 - n), n)
        worst_abs = max(worst_abs, abs(approx - exact))
        if .001 <= exact <= .2:
            worst_rel = max(worst_rel, abs(approx - exact) / exact)
    assert worst_rel < 0.012 and worst_abs < 4e-4


def test_s8_spearman_large_n_close_to_t():
    """Far above the exact range the approximation and the t distribution with n - 2 df agree closely."""
    n, rho = 2000, 0.05
    t = rho * math.sqrt((n - 2) / (1 - rho * rho))
    assert gt.spearman_p(rho, n) == pytest.approx(gt.t_sf2(t, n - 2), rel=5e-3)


def test_s9_mann_whitney_exact_and_normal_approximation():
    # 19 against 1 without ties: exact (0.1); before, the normal approximation gave 0.141
    t = gt.mann_whitney(list(range(1, 20)), [20])
    assert t["exact"] and t["p"] == pytest.approx(0.1)
    x = [1.1, 2.3, 3.2, 4.8, 5.5, 6.1, 7.4, 8.9, 9.2, 10.6, 11.3, 12.8]
    y = [2.0, 3.9, 6.6, 7.7, 13.1, 14.2, 15.5, 16.4, 17.8, 18.1, 19.9, 20.5]
    assert gt.mann_whitney(x, y)["p"] == pytest.approx(0.0172711929341355, rel=1e-9)
    # ties, 60 + 60 cases: normal approximation with continuity correction
    xs, ys = [1] * 15 + [2] * 20 + [3] * 25, [1] * 25 + [2] * 20 + [3] * 15
    t = gt.mann_whitney(xs, ys)
    assert not t["exact"] and t["p"] == pytest.approx(0.0261519348714142, rel=1e-9)
    # 50 in one group: no longer exact
    assert not gt.mann_whitney([i + 0.5 for i in range(50)], [float(i) for i in range(10)])["exact"]


def test_s10_weighted_sd_for_sampling_weights():
    x, w = [50, 60, 70, 80, 90], [1, 2, 0.5, 1.5, 3]
    assert weights.sd(x, w) == pytest.approx(16.7559457730681, rel=1e-12)
    assert weights.sd(x, [1] * 5) == pytest.approx(statistics.stdev(x))


def test_methods_text_names_no_continuity_correction():
    from app import methods_text

    assert "ohne Kontinuitätskorrektur" in methods_text.TEXT["de"]["tests"]
    assert "without continuity correction" in methods_text.TEXT["en"]["tests"]
