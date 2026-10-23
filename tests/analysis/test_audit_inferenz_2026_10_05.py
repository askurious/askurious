"""Packages A and H of the math audit of 2026-10-05 (audit-mathematik-2026-10-05, inferenz/bericht.md I-1, I-3 to
I-9 and nachtrag/bericht.md N-5 to N-7): group comparisons and hypotheses, learning and method texts. The cases of
the audit scripts (tests_check.py, hyp_check.py) are the regression tests here."""
import itertools
import math
from datetime import datetime, timedelta

from werkzeug.datastructures import MultiDict

from app import compare, learning, methods, units
from app import group_tests as gt
from app import hypotheses as hy
from app.extensions import db
from app.models import Response, Survey


def close(a, b, tol=1e-9):
    return abs(a - b) < tol


def run(monkeypatch, analysis, groups, values, level="metric", direction=""):
    """hypotheses.evaluate_one on bare lists: the reading of elements and plan replaced (as hyp_check.py)."""
    monkeypatch.setattr(hy, "_values", lambda s, code, t: (values, level) if code == "dv" else (groups, "nominal"))
    monkeypatch.setattr(hy, "_codes", lambda s, code, t: groups)
    monkeypatch.setattr(hy, "_labels", lambda s, code: {})
    h = {"text": "H", "kind": "difference", "iv": "iv", "dv": "dv", "analysis": analysis, "direction": direction}
    return hy.evaluate_one(None, h, None)


# --- I-3: exact tests where Cochran's rule warns -------------------------------------------------------------------

def _fisher_by_enumeration(a, b, c, d):
    r1, r2, c1, n = a + b, c + d, a + c, a + b + c + d
    probs = {x: math.comb(r1, x) * math.comb(r2, c1 - x) / math.comb(n, c1) for x in range(max(0, c1 - r2), min(r1, c1) + 1)}
    return sum(p for p in probs.values() if p <= probs[a] * (1 + 1e-7))


def test_fisher_exact_beside_chi2_for_small_2x2_tables():
    # the tables of the audit: chi-square p 0.0191 / 0.0098 / 0.0384, Fisher 0.0573 / 0.0476 / 0.0808
    for table, fisher in (([[1, 9], [6, 4]], 0.0573), ([[5, 0], [1, 4]], 0.0476), ([[0, 5], [4, 3]], 0.0808)):
        t = gt.chi2_test(table)
        assert t["cochran"] and t["exact"] == "fisher" and close(t["exact_p"], fisher, 5e-5)
        assert close(t["exact_p"], _fisher_by_enumeration(*table[0], *table[1]))
    # hypergeometric, two-sided: 3 1 / 1 3 gives p = 0.4857143
    assert close(gt.chi2_test([[3, 1], [1, 3]])["exact_p"], 0.4857142857, 1e-9)
    big = gt.chi2_test([[20, 10], [5, 15]])
    assert not big["cochran"] and big["exact"] == "fisher"                   # computed for every 2 x 2
    # multiple choice counts its 0/1 columns as floats: still counts
    assert close(gt.chi2_test([[1.0, 9.0], [6.0, 4.0]])["exact_p"], gt.chi2_test([[1, 9], [6, 4]])["exact_p"])


def test_monte_carlo_permutation_test_for_larger_tables():
    table = [[3, 1, 0], [1, 4, 2], [0, 2, 5]]
    t = gt.chi2_test(table)
    assert t["cochran"] and t["exact"] == "monte_carlo" and t["draws"] == gt.DRAWS
    assert gt.chi2_test(table)["exact_p"] == t["exact_p"]                   # fixed seed: the same p every time
    # against the exact permutation p of the same statistic (all 18! / margins orderings are too many: a larger
    # simulation with another seed must agree within a few Monte Carlo standard errors)
    import random

    rng = random.Random(1)
    labels = [j for j, c in enumerate([4, 7, 7]) for _ in range(c)]
    rs = [4, 7, 7]
    expected = [[r * c / 18 for c in [4, 7, 7]] for r in rs]
    hits = 0
    for _ in range(40_000):
        rng.shuffle(labels)
        chi2, start = 0.0, 0
        for i, r in enumerate(rs):
            counts = [labels[start:start + r].count(j) for j in range(3)]
            start += r
            chi2 += sum((o - e) ** 2 / e for o, e in zip(counts, expected[i]))
        hits += chi2 >= t["chi2"] - 1e-9
    assert abs(t["exact_p"] - hits / 40_000) < 4 * math.sqrt(0.02 * 0.98 / gt.DRAWS)
    assert gt.chi2_test([[30, 20, 25], [20, 30, 25]])["exact"] is None      # no warning, no Monte Carlo


def test_hypothesis_verdict_from_the_exact_test(monkeypatch):
    # hyp_check.py case 3: 1/9 vs 6/4 was "unterstützt" with chi-square p = .019; Fisher p = .057
    g = ["a"] * 10 + ["b"] * 10
    v = ["ja"] * 1 + ["nein"] * 9 + ["ja"] * 6 + ["nein"] * 4
    r = run(monkeypatch, "chi2", g, v, level="nominal")
    assert r["verdict"] == "nicht unterstützt" and close(r["p"], 0.0573, 5e-5) and r["exact"] == "fisher"
    assert r["sentence"] == "χ²(1, N = 20) = 5,49, V = 0,52; exakter Test nach Fisher, p = ,057"
    assert any("exakten Test nach Fisher (1934)" in n for n in r["notes"])
    assert hy.sources([r])[0][1] == [compare.FISHER, compare.AGRESTI]


def test_comparison_line_shows_the_exact_p(app):
    with app.test_request_context():
        tpl = app.jinja_env.from_string('{% from "surveys/_compare_macros.html" import test_line %}{{ test_line(b) }}')
        html = tpl.render(b={"kind": "nominal", "groups": [("1", "a"), ("2", "b")], "test": gt.chi2_test([[1, 9], [6, 4]])})
        assert "exakter Test nach Fisher (1934)" in html and ",057" in html and "maßgeblich ist das exakte" in html
        html = tpl.render(b={"kind": "metric", "groups": [("1", "a"), ("2", "b")], "test": None, "flat": [], "weighted": True})
        assert "Tests gibt es nur in der ungewichteten Ansicht" in html


# --- I-4: small side groups ----------------------------------------------------------------------------------------

def test_a_single_answer_in_a_side_group_does_not_stop_the_test(monkeypatch):
    g = ["a"] * 20 + ["b"] * 20 + ["c"]
    v = [float(x % 5) for x in range(20)] + [float(x % 5) + 2 for x in range(20)] + [3.0]
    r = run(monkeypatch, "anova", g, v)                                     # was: "offen", no sentence, no note
    assert r["verdict"] == "unterstützt" and r["sentence"].startswith("F(1, 38,0) = 19,00")
    assert "„c“: 1 Person" in r["notes"][0]
    r = run(monkeypatch, "t", g, v)                                         # a planned t-test stays one
    assert r["verdict"] == "unterstützt" and r["sentence"].startswith("t(38,0) = −4,36")
    assert "„c“: 1 Person" in r["notes"][0]
    r = run(monkeypatch, "t", ["a"] * 20 + ["b"] * 3, v[:23])               # no test: a note says why
    assert r["verdict"] == "offen" and "Weniger als zwei Gruppen mit mindestens 5 Antworten" in r["notes"][-1]
    r = run(monkeypatch, "t", ["a"] * 6 + ["b"] * 6, [1.0] * 6 + [2.0] * 6)
    assert r["verdict"] == "offen" and "streuen" in r["notes"][-1]


# --- I-5 and I-6: exact Mann-Whitney with ties ----------------------------------------------------------------------

def _permutation_p(xs, ys):
    pool, n1 = xs + ys, len(xs)

    def u(idx):
        a = [pool[i] for i in idx]
        b = [pool[i] for i in range(len(pool)) if i not in idx]
        return sum((p > q) + 0.5 * (p == q) for p in a for q in b)

    seen = u(range(n1))
    us = [u(set(c)) for c in itertools.combinations(range(len(pool)), n1)]
    return min(1.0, 2 * min(sum(x <= seen + 1e-9 for x in us), sum(x >= seen - 1e-9 for x in us)) / len(us))


def test_mann_whitney_exact_with_ties_below_20_cases():
    # tests_check.py section 3: normal approximation p = 0.0486 ("significant"), exact 0.1032; 0.0141 vs 0.0238
    for xs, ys, p in (([2, 3, 3, 3, 4], [3, 4, 4, 5, 5], 0.1032), ([1, 2, 2, 3, 3], [3, 4, 4, 5, 5], 0.0238),
                      ([1, 1, 2, 2, 3, 5], [3, 3, 4, 4, 5], None)):
        r = gt.mann_whitney(xs, ys)
        assert r["exact"] and close(r["p"], _permutation_p(xs, ys))
        if p is not None:
            assert close(r["p"], p, 5e-5)
    big = gt.mann_whitney([1, 2, 2, 3, 3] * 2, [3, 4, 4, 5, 5] * 2)            # 20 cases: the normal approximation
    assert not big["exact"] and close(big["p"], gt.norm_sf2(big["z"]))


def test_hypothesis_sentence_without_z_for_the_exact_test(monkeypatch):
    g = ["a"] * 5 + ["b"] * 6
    v = [1.2, 3.4, 2.2, 5.1, 4.4, 0.3, 0.9, 1.1, 2.0, 0.5, 0.7]
    r = run(monkeypatch, "u", g, v, level="ordinal")
    assert "exakter Test" in r["sentence"] and "z =" not in r["sentence"]


# --- I-7: p just below .05 ----------------------------------------------------------------------------------------

def test_p_just_below_05_does_not_read_as_050(app):
    assert hy._p_lang(0.04996) == "p = ,04996" and hy._verdict(0.04996, 1, "pos") == "unterstützt"
    assert hy._p_lang(0.0499) == "p = ,0499" and hy._p_lang(0.0504) == "p = ,050" and hy._p_lang(0.0123) == "p = ,012"
    assert hy._p_lang(0.04996, "en") == "p = .04996"
    assert units.p_places(0.012) == 3 and units.p_places(0.0496) == 4 and units.p_places(0.05) == 3
    with app.test_request_context():
        tpl = app.jinja_env.from_string('{% from "surveys/_compare_macros.html" import pval %}{{ pval(p) }}')
        assert ",04996" in tpl.render(p=0.04996) and ",050" in tpl.render(p=0.0504)


# --- I-1: the comparison in the weighted view ----------------------------------------------------------------------

def test_weighted_comparison_unit():
    values = ["x", "y", "x", "x", "y"] * 2 + ["x", "y", "y", "y", "y"] * 2
    member = ["1"] * 10 + ["2"] * 10
    weights = [2.0] * 5 + [1.0] * 5 + [1.0] * 20
    groups = [("1", "A"), ("2", "B")]
    rows = [{"value": "x", "label": "x"}, {"value": "y", "label": "y"}]
    b = compare.nominal(values, member, groups, rows, weights)
    x = next(r for r in b["rows"] if r["value"] == "x")
    assert x["cells"] == [6, 2] and b["n"] == [10, 10] and b["test"] is None and b["weighted"]
    assert close(x["shares"][0], (3 * 2 + 3) / 15)                          # weighted: 9 of 15
    m = compare.metric([1.0] * 5 + [3.0] * 5 + [2.0] * 10, member, groups, weights)
    assert close(m["per"][0]["mean"], (5 * 2 + 15) / 15) and m["test"] is None and m["weighted"]
    # the interval as on the page (results.numeric_stats, package B): linearization SE, t with Kish's n_eff - 1
    from app import intervals, results
    from app import weights as weighting

    xs, ws = [float(v) for v in range(12)], [0.5, 1.5] * 6
    d = gt.describe(xs, ws)
    assert d["ci"] == intervals.mean_ci(d["mean"], d["sd"], weighting.n_eff(ws), se=weighting.mean_se(xs, ws))
    assert d["ci"] == results.numeric_stats(xs, weights=ws)["ci"]
    o = compare.ordinal(values, member, groups, rows, weights)
    assert o["test"] is None and close(o["per"][0]["shares"]["x"], 9 / 15)


def _weighted_survey(app, client):
    from tests.quotas.test_quotas import build
    from tests.analysis.test_weights import W

    build(app, client, cells=[("Männer", "m", 25, [(2, "eq", "1")]), ("Frauen", "f", 75, [(2, "eq", "2")])])
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    t0 = datetime(2026, 9, 1, 10)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for i, (g, age) in enumerate([("1", 20 + i) for i in range(6)] + [("2", 40 + i) for i in range(6)]):
            s.responses.append(Response(status=Response.COMPLETED, source="real", answers={"2": g, "3": str(age)},
                                        started_at=t0, completed_at=t0 + timedelta(seconds=300 + i)))
        s.responses.append(Response(status=Response.COMPLETED, source="real", answers={"2": "2", "3": "99"},
                                    started_at=t0, completed_at=t0 + timedelta(seconds=20)))   # a speeder: marked
        db.session.commit()


def test_weighted_view_weighs_the_comparison_and_leaves_marked_out(app, client, survey, monkeypatch):
    _weighted_survey(app, client)
    seen = []
    real = compare.compute
    monkeypatch.setattr(compare, "compute", lambda s, by, data, table, weights=None, **kw:
                        seen.append((len(table), weights)) or real(s, by, data, table, weights, **kw))
    html = client.get("/surveys/1/results?gewichtet=1&markierte=1&gruppen=2&go=1").data.decode()
    assert seen[-1][0] == 12 and len(seen[-1][1]) == 12                     # the speeder stays out, as above
    assert "Tests gibt es nur in der ungewichteten Ansicht" in html and "Gewichtet: Anteile, Mediane, M, SD" in html
    assert "Im Gruppenvergleich („Nach Gruppen aufteilen“)" in html
    html = client.get("/surveys/1/results?markierte=1&gruppen=2&go=1").data.decode()
    assert seen[-1][0] == 13 and seen[-1][1] is None and "Welch t(" in html


# --- I-9: two-sided, alpha, no correction --------------------------------------------------------------------------

def test_methods_draft_names_sidedness_alpha_and_correction(app, client, survey):
    from tests.analysis.test_study_plan import _plan, _questions

    _questions(client, app)
    _plan(client)
    md = client.get("/surveys/1/export/methods-section.md").data.decode()
    assert "zweiseitig mit α = ,05" in md and "nicht korrigiert" in md and "weniger als 5 Antworten" in md
    en = client.get("/surveys/1/export/methods-section.md?lang=en").data.decode()
    assert "two-sided with α = .05" in en and "No correction" in en


# --- I-8, N-6, N-7: learning texts; N-5: one segment against the BIC --------------------------------------------------

def test_learning_texts():
    ci = learning.TERMS["ci"][2]
    assert "Verfahren" in ci and "95 % Sicherheit" not in ci and learning.HOEKSTRA in learning.TERMS["ci"][4]
    ess = learning.TERMS["ess"][2]
    assert "Ab 400 ist der Mittelwert verlässlich" in ess and "Mittelwert und Intervall verlässlich" not in ess
    hb = learning.REASONS["hb_or_logit"][1]
    assert "Wichtigkeit im Mittel der Teilnutzen" in hb and "unterschätzt" in hb


def test_one_segment_against_the_bic_suggestion():
    c = {"code": "md", "type": "maxdiff", "statements": 8, "sets": 6, "set_size": 4, "versions": 1, "anchor": False,
         "n": 100, "robust": False, "hb": None, "holdouts": 0,
         "segments": {"K": 1, "suggested": 3, "max": 4, "n": 100, "bic": 1.0, "entropy": None, "covariates": []}}
    text = methods._choice_de(c)
    assert "abweichend vom BIC-Vorschlag von 3 Segmenten" in text and "passte am besten" not in text
    c["segments"]["suggested"] = 1
    assert "die Lösung mit einem Segment passte am besten" in methods._choice_de(c)
