"""Math audit 2026-10-05, package C (aggregate choice models and designs): C-1 to C-5."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from app import choice_model as cm
from app import conjoint_design, maxdiff_analysis, methods
from app.intervals import t_quantile
from tests.analysis.test_conjoint import _choice_data
from tests.analysis.test_maxdiff import simulate


def _fieller(a, b, vaa, vbb, vab, z):
    """Independent from app.choice_model.fieller: the r where (a + r b)^2 = z^2 Var(a + r b), solved for r."""
    A, B, C = b * b - z * z * vbb, 2 * (a * b - z * z * vab), a * a - z * z * vaa
    if A <= 0:
        return None
    roots = np.roots([A, B, C])
    return float(min(roots.real)), float(max(roots.real))


# --- C-1: willingness to pay with the Fieller interval ---------------------------------------------------------------

def test_c1_fieller_interval_and_its_unbounded_case():
    lo, hi = cm.fieller(1.0, -0.05, 0.04, 0.0002, 0.0, q=1.96)
    assert lo < 1.0 / 0.05 < hi
    for r in (lo, hi):  # the ends are where the ratio is just not rejected
        assert (1.0 + r * -0.05) ** 2 == pytest.approx(1.96 ** 2 * (0.04 + r * r * 0.0002), rel=1e-9)
    assert hi - 20 > 20 - lo  # skewed away from 0: the denominator can be smaller, not much larger
    # |z| of the denominator at or below q: no bounded interval
    assert cm.fieller(1.0, -0.05, 0.04, (0.05 / 1.96) ** 2, 0.0, q=1.96) is None
    assert cm.fieller(1.0, -0.05, 0.04, 0.01, 0.0, q=1.96) is None


def test_c1_willingness_to_pay_near_the_boundary_uses_fieller():
    # the case of the audit: price coefficient only just significant (z = -2.4); the delta interval was symmetric
    sets, coding, attrs, prices = _choice_data(100, 3, price_beta=-0.012)
    m = cm.summarise(sets, coding, attrs, 100, prices)
    z = m["price"]["beta"] / m["price"]["se"]
    assert -3 < z < -1.96 and m["wtp_ok"] and m["q"] == cm.Z95
    beta, cov = cm.fit(sets, coding.k)[:2]
    p, s = coding.price, coding.price_scale
    for a, attr in enumerate(m["attributes"]):
        for lv, row in enumerate(attr["levels"]):
            g = coding.level_vector(a, lv)
            lo, hi = _fieller(float(g @ beta), float(beta[p]), float(g @ cov @ g), float(cov[p, p]), float(g @ cov[:, p]),
                              cm.Z95)
            assert row["wtp"] == pytest.approx(float(g @ beta) / -float(beta[p]) * s)
            assert row["wtp_lo"] == pytest.approx(lo * s, rel=1e-7) and row["wtp_hi"] == pytest.approx(hi * s, rel=1e-7)
    top = m["attributes"][0]["levels"][0]                                  # true 0.6 / 0.012 = 50
    assert top["wtp_lo"] < 50 < top["wtp_hi"] and top["wtp_hi"] - top["wtp"] > 2 * (top["wtp"] - top["wtp_lo"])
    # not significant: no willingness to pay, the warning names Fieller
    sets, coding, attrs, prices = _choice_data(100, 4, price_beta=-0.012)
    weak = cm.summarise(sets, coding, attrs, 100, prices)
    assert not weak["wtp_ok"] and any("nicht bestimmt" in w and "Fieller" in w for w in weak["warnings"])


def test_c1_well_determined_price_fieller_close_to_delta():
    sets, coding, attrs, prices = _choice_data(300, 9)
    m = cm.summarise(sets, coding, attrs, 300, prices)
    beta, cov = cm.fit(sets, coding.k)[:2]
    a, lv = 0, 0
    row = m["attributes"][a]["levels"][lv]
    bp = beta[coding.price] / coding.price_scale
    g = coding.level_vector(a, lv) / -bp
    g[coding.price] += row["beta"] / (bp ** 2 * coding.price_scale)
    half = cm.Z95 * math.sqrt(g @ cov @ g)
    assert row["wtp_lo"] == pytest.approx(row["wtp"] - half, rel=0.05)
    assert row["wtp_hi"] == pytest.approx(row["wtp"] + half, rel=0.05)


def test_c1_segments_use_the_same_willingness_to_pay():
    import inspect

    from app import segments

    assert "wtp(coding, b, cc, a, lv)" in inspect.getsource(segments._cbc_view)
    sets, coding, attrs, prices = _choice_data(200, 9)
    beta, cov = cm.fit(sets, coding.k)[:2]
    w, lo, hi = cm.wtp(coding, beta, cov, 1, 0)
    assert lo < w < hi
    assert cm.wtp(coding, beta, None, 1, 0) == (None, None, None)


def test_c1_rest_weak_price_note_on_the_willingness_to_pay():
    """C-1 rest: between |z| = 1.96 and 4 the willingness to pay is shown, but only where |beta_price| came out large
    by chance, so on average it is too small (audit, logit-design/t3_wtp_weak: median 26 % too small at z about -2).
    The page says so, with |z|; from |z| = 4 (Askurious's choice) no note."""
    assert cm.weak_price(-2.4) == 2.4 and cm.weak_price(-3.99) == 3.99
    assert cm.weak_price(-4.0) is None and cm.weak_price(-1.9) is None and cm.weak_price(None) is None
    assert cm.weak_price(-2.2, q=2.3) is None                              # not significant with the t quantile
    sets, coding, attrs, prices = _choice_data(100, 3, price_beta=-0.012)  # z about -2.4 (the case of C-1)
    m = cm.summarise(sets, coding, attrs, 100, prices)
    z = m["price"]["beta"] / m["price"]["se"]
    assert m["wtp_ok"] and m["wtp_weak"] == pytest.approx(-z)
    sets, coding, attrs, prices = _choice_data(300, 9)
    strong = cm.summarise(sets, coding, attrs, 300, prices)
    assert strong["wtp_ok"] and strong["price"]["beta"] / strong["price"]["se"] < -4 and strong["wtp_weak"] is None
    sets, coding, attrs, prices = _choice_data(100, 4, price_beta=-0.012)
    assert cm.summarise(sets, coding, attrs, 100, prices)["wtp_weak"] is None   # no WTP at all: no note either


@pytest.mark.parametrize("price_beta, weak", [(-0.004, True), (-0.03, False)])
def test_c1_rest_weak_price_note_on_the_page(app, client, survey, price_beta, weak):
    from unittest import mock

    from tests.analysis.test_cbc_results import setup, simulate as cbc_simulate

    generate = conjoint_design.generate
    with mock.patch.object(conjoint_design, "generate", lambda *a, **kw: generate(*a, **{**kw, "seed": 3})):
        setup(client, price_attr=True)
    cbc_simulate(app, 60, price_beta=price_beta)                           # z -2.35 and -16.8 with this design
    html = client.get("/surveys/1/results").data.decode()
    assert ("Preiskoeffizient nur knapp bestimmt (|z| = 2,4)" in html) is weak
    assert "Zahlungsbereitschaft" in html and ("Grenze |z| = 4 von Askurious festgelegt" in html) is weak
    assert "Hole, A. R. (2007)" in html


def test_c1_rest_weak_price_note_per_segment(app, client, survey):
    from unittest import mock

    from tests.analysis.test_segments import cbc_with_segments

    cbc_with_segments(app, client)
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    with app.app_context():
        from app import segments
        from app.extensions import db
        from app.models import Item

        item = db.session.get(Item, 2)
        view = segments.summary(item, segments.load(item), "de")
        assert view["price"]["weak"] == [cm.weak_price(z) for z in view["price"]["z"]]
    # every segment with a WTP counts as only just determined when the limit is set very high: the note names them
    with mock.patch.object(cm, "WTP_WEAK_Z", 1e9):
        html = client.get("/surveys/1/results").data.decode()
    assert "Preiskoeffizient nur knapp bestimmt in Segment 1 (|z| = " in html


# --- C-2: t(G - 1) quantile for the cluster-robust intervals of few persons -------------------------------------------

def test_c2_critical_value():
    assert cm.critical({"robust": True, "clusters": 8}) == pytest.approx(2.364624, abs=1e-5)
    assert cm.critical({"robust": True, "clusters": 49}) == pytest.approx(t_quantile(0.975, 48))
    assert cm.critical({"robust": True, "clusters": 50}) == cm.Z95
    assert cm.critical({"robust": False, "clusters": 8}) == cm.Z95   # Hessian: coverage was fine (95 %)
    assert cm.critical(None) == cm.Z95


def test_c2_conjoint_intervals_with_few_persons():
    sets, coding, attrs, prices = _choice_data(10, 3)
    m = cm.summarise(sets, coding, attrs, 10, prices)
    assert m["robust"] and m["t_df"] == 9 and m["q"] == pytest.approx(t_quantile(0.975, 9))
    # the whiskers of the charts reach q standard errors
    chart, lv = m["charts"][0], max(m["attributes"][0]["levels"], key=lambda x: x["beta"])
    row = next(r for r in chart["rows"] if r["label"] == lv["label"])
    scale = (row["w"]) / abs(lv["beta"])
    assert (row["hi"] - row["lo"]) / scale == pytest.approx(2 * m["q"] * lv["se"], rel=0.02)
    # not more persons than parameters: Hessian, z
    sets, coding, attrs, prices = _choice_data(8, 3)
    small = cm.summarise(sets, coding, attrs, 8, prices)
    assert not small["robust"] and small["q"] == cm.Z95 and small["t_df"] is None
    sets, coding, attrs, prices = _choice_data(60, 3)
    assert cm.summarise(sets, coding, attrs, 60, prices)["q"] == cm.Z95


def test_c2_maxdiff_intervals_with_few_persons():
    from app.maxdiff_design import generate

    true = [1.2, 0.8, 0.4, 0.1, -0.1, -0.3, -0.7, -1.4]
    versions = generate(8, 4, 6, 3, seed=3)
    labels = [f"S{i + 1}" for i in range(8)]
    answers, _ = simulate(true, versions, 15, seed=5)
    m = maxdiff_analysis.mnl(8, versions, answers, [], labels, 4)
    assert m["robust"] and m["t_df"] == 14 and m["q"] == pytest.approx(t_quantile(0.975, 14))
    # the simulated score intervals are wider than with z by about t / z
    info = {}
    sets, _, k, _ = maxdiff_analysis.choice_sets(8, versions, answers, [])
    beta, cov = cm.fit(sets, k, info=info)[:2]
    assert info == {"robust": True, "clusters": 15}
    widths = [r["hi"] - r["lo"] for r in m["rows"]]
    assert all(w > 0 for w in widths)
    many, _ = simulate(true, versions, 120, seed=5)
    assert maxdiff_analysis.mnl(8, versions, many, [], labels, 4)["q"] == cm.Z95


# --- C-3: level balance of the CBC design --------------------------------------------------------------------------

def test_c3_generated_designs_balance_the_levels():
    # the case of the audit: [2, 3, 2], 6 tasks of 3; seed 0 had level counts [12, 6] at D-efficiency 1.0
    design, eff = conjoint_design.generate([2, 3, 2], 6, 3, seed=0)
    D = np.array(design).reshape(-1, 3)
    assert [np.bincount(D[:, j]).tolist() for j in range(3)] == [[9, 9], [6, 6, 6], [9, 9]]
    assert eff > 0.99
    for seed in range(1, 4):
        design, eff = conjoint_design.generate([2, 2, 2, 2], 8, 3, seed=seed)
        assert all(hi == lo for lo, hi in conjoint_design.level_frequency(design, [2, 2, 2, 2])) and eff > 0.99
    assert conjoint_design.level_frequency([[[0, 1], [1, 1], [1, 0]]], [2, 2]) == [(1, 2), (1, 2)]
    assert conjoint_design.imbalance([[0, 0], [1, 1]], [2, 2]) == 0


def test_c3_editor_shows_level_frequency_and_honest_tooltip(app, client, survey):
    from tests.analysis.test_conjoint import E, setup

    setup(client, versions=2)
    html = client.post(E + "/items/2/conjoint/design", follow_redirects=True).get_data(as_text=True)
    assert "Stufenhäufigkeit" in html and "1 = beste Information des Logit-Modells" in html
    assert "Stufen gleich oft" not in html


def test_c3_constant_refers_to_the_mean_price_of_the_levels():
    from app import cbc_analysis

    src = open(cbc_analysis.__file__, encoding="utf-8").read()
    assert 'result["center"] = "levels"' in src
    tpl = open("app/templates/surveys/_choice_model.html", encoding="utf-8").read()
    assert "mittleren Preis der Stufen" in tpl


# --- C-4: "clusterrobust" only when the model is -------------------------------------------------------------------

def test_c4_maxdiff_says_whether_the_errors_are_cluster_robust():
    from app.maxdiff_design import generate

    versions = generate(8, 4, 6, 3, seed=3)
    answers, _ = simulate([1.2, 0.8, 0.4, 0.1, -0.1, -0.3, -0.7, -1.4], versions, 6, seed=2)
    m = maxdiff_analysis.mnl(8, versions, answers, [], [f"S{i}" for i in range(8)], 4)
    assert m["robust"] is False and m["q"] == cm.Z95                       # 6 persons, 7 parameters: Hessian
    tpl = open("app/templates/surveys/_maxdiff_results.html", encoding="utf-8").read()
    assert '{% if x.robust %}{{ de_en("Standardfehler clusterrobust' in tpl and "nicht clusterrobust" in tpl


def _item(type_, levels, config, numbers=None):
    kind = SimpleNamespace(level_numbers=lambda item, a: numbers)
    return SimpleNamespace(type=type_, kind=kind, config=config, levels=levels)


def test_c4_methods_count_the_parameters_as_coded():
    # the design of the audit: 3 x 3 x 2 and a price with 4 levels (linear), "none": 2 + 2 + 1 + 1 + 1 = 7, not 9
    item = _item("conjoint", [3, 3, 2, 4], {"none": True}, numbers=[10, 20, 30, 40])
    assert methods._parameters(item, [3, 3, 2, 4], 3) == 7
    assert methods._parameters(_item("conjoint", [3, 3, 2, 4], {"none": False}, [10, 20, 30, 40]), [3, 3, 2, 4], 3) == 6
    # price levels that are not numbers: effects coded like the rest
    assert methods._parameters(_item("conjoint", [3, 3, 2, 4], {"none": True}, None), [3, 3, 2, 4], 3) == 9
    # ACBC: threshold constant always, summed price when on
    assert methods._parameters(_item("acbc", [3, 3, 2], {"price": {"on": True}}), [3, 3, 2], None) == 7
    assert methods._parameters(_item("acbc", [3, 3, 2], {}), [3, 3, 2], None) == 6
    # dual response: the constant of "none" from the follow-up question
    assert methods._parameters(_item("conjoint", [3, 3, 2, 4], {"none": False, "dual": True}, [10, 20, 30, 40]),
                               [3, 3, 2, 4], 3) == 7


# --- C-5: MaxDiff scores are probability scores, not choice probabilities among the statements -------------------------

def test_c5_score_texts():
    entry = {"code": "md", "type": "maxdiff", "statements": 10, "sets": 8, "set_size": 4, "versions": 1, "anchor": False,
             "n": 120, "robust": True, "hb": None, "holdouts": 0, "segments": None}
    de = methods._choice_de(entry)
    assert "Wahrscheinlichkeits-Scores: die Wahrscheinlichkeit" in de and "3 durchschnittlichen Aussagen" in de
    assert "Wahlwahrscheinlichkeiten" not in de
    en = methods._choice_en(entry)
    assert "probability scores: probability" in en and "3 average statements" in en
    tpl = open("app/templates/surveys/_maxdiff_results.html", encoding="utf-8").read()
    assert "sie hängen von der Set-Größe ab" in tpl and "Verhältnisse lassen sich lesen (20 ist doppelt" not in tpl
