"""Mathe-Audit 2026-10-05, package B: intervals of scales in loops, the omega bootstrap, the pooled reliability,
weights after the cap and the weighted intervals (kennwerte/bericht.md K-1 to K-7, inferenz/bericht.md I-2).
The planner of the sample size (K-4) is tested in tests/analysis/test_samplesize.py."""
import math
import random
from datetime import datetime

import numpy as np
from werkzeug.datastructures import MultiDict

from app import construct_pool, intervals, results, scales, weights
from app.extensions import db
from app.models import Construct, Response, Survey
from tests.conftest import add


# --- K-1: draws without an omega count as below every value ----------------------------------------------------


def test_open_percentile_counts_missing_draws_as_lowest():
    values = [None] * 3 + [float(i) for i in range(197)]
    lo, hi = intervals.open_percentile(values)
    # sorted: three times "below everything", then 0, 1, 2, ...; the 2.5 % point (position 4.975) lies between 1 and 2
    assert math.isclose(lo, 1.975) and math.isclose(hi, 191.025)        # position 194.025 of the 200
    assert lo < intervals.percentile(values, 200)[0]                     # leaving them out shifted the lower end up
    assert intervals.open_percentile([None] * 10 + [float(i) for i in range(190)])[0] is None   # open below
    assert intervals.open_percentile([None] * 196 + [0.5] * 4) is None   # not even the upper end
    assert intervals.open_percentile([0.4, 0.5, 0.6]) == intervals.percentile([0.4, 0.5, 0.6], 3)


def test_omega_interval_with_a_weak_statement_is_open_below():
    """The case of the audit (k3_intervalle.py): n = 30, true loadings .5/.3/.2. Before: [0.095; 0.763] from the
    estimable draws alone, although 41 % of them had no omega."""
    rng = np.random.default_rng(3)
    loads = np.array([0.5, 0.3, 0.2])
    for _ in range(50):  # a sample whose omega exists, with many draws that have none
        x = rng.standard_normal((30, 1)) * loads + rng.standard_normal((30, 3)) * np.sqrt(1 - loads ** 2)
        if scales.mcdonald_omega(x.tolist()) is None:
            continue
        ci, failed = intervals.omega_boot(x.tolist())
        if failed and failed > 0.1:
            break
    assert failed > 0.025
    assert ci is not None and ci[0] is None and ci[1] > scales.mcdonald_omega(x.tolist())
    assert intervals.omega(x.tolist()) == ci


# --- K-2 / I-2: a scale in a loop: intervals over persons -----------------------------------------------------


def _loop_scale(app, client, persons=36):
    from app import loops
    from app.models import Block
    from tests.editor.test_loops import setup as loop_setup

    loop_setup(client)
    add(client, "likert")                                                   # 3, in the loop block
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "LK"), ("points", "5"), ("labeling", "full"), ("level", "metric"), ("question", "Gefallen"),
        ("stmt_id", "a"), ("stmt_code", ""), ("stmt_text", "A"), ("stmt_id", "b"), ("stmt_code", ""), ("stmt_text", "B"),
        ("stmt_id", "c"), ("stmt_code", ""), ("stmt_text", "C")]))
    rng = random.Random(5)
    answers = []
    with app.app_context():
        rows = [r["id"] for r in loops.config(db.session.get(Block, 2))["rows"]]
        s = db.session.get(Survey, 1)
        for _ in range(persons):
            level = rng.choice((1, 2, 3, 4, 5))  # a strong person effect: the passes of one person are alike
            mine = {f"3#{row}": {k: min(5, max(1, level + rng.choice((-1, 0, 0, 1)))) for k in "abc"} for row in rows}
            answers.append([[mine[f"3#{row}"][k] for k in "abc"] for row in rows])
            s.responses.append(Response(source="real", status=Response.COMPLETED, completed_at=datetime(2026, 9, 1),
                                        answers=mine))
        db.session.commit()
    return answers


def test_scale_in_a_loop_takes_its_intervals_over_persons(app, client, survey):
    answers = _loop_scale(app, client)
    with app.app_context():
        entry = next(e for e in results.compute(db.session.get(Survey, 1), omega_ci=True)["items"] if e["item"].code == "LK")
    scale = entry["scale"]
    assert scale["pooled"] and scale["n"] == 36 * 4 and scale["persons"] == 36
    # M over the passes as before; its interval cluster-robust over the persons: with equal passes the t interval
    # over the 36 person means
    person = [float(np.mean([np.mean(p) for p in passes])) for passes in answers]
    lo, hi = intervals.mean_ci(float(np.mean(person)), float(np.std(person, ddof=1)), 36)
    assert np.allclose(scale["mean_ci"], (lo, hi))
    flat = [float(np.mean(p)) for passes in answers for p in passes]
    naive = intervals.mean_ci(float(np.mean(flat)), float(np.std(flat, ddof=1)), len(flat))
    assert hi - lo > 1.4 * (naive[1] - naive[0])                           # the passes were counted as independent
    # no Feldt interval; the omega bootstrap draws persons with all their passes
    assert scale["alpha"] is not None and scale["alpha_ci"] is None
    matrix = [p for passes in answers for p in passes]
    owners = [i for i, passes in enumerate(answers) for _ in passes]
    if scale["omega"] is not None:
        assert scale["omega_ci"] == intervals.omega(matrix, persons=owners)
        assert scale["omega_ci"] != intervals.omega(matrix)
    html = client.get("/surveys/1/results").data.decode()
    assert "M clusterrobust mit den Personen als Einheiten (n = 36 Personen)" in html and "ein Intervall für α gibt es hier nicht" in html


def test_cluster_robust_mean_interval_is_centred_on_m():
    """Unequal numbers of passes: the interval lies around M over all passes (an interval around the mean of the
    person means need not hold M), SE^2 = G / (G - 1) * sum_g (sum_i (x_i - M))^2 / n^2 with t(G - 1)."""
    rng = np.random.default_rng(11)
    groups = []
    for g in range(30):
        u = rng.normal(0, 1)
        # persons with a high level answer more passes: the mean of the person means differs from M
        groups.append((u + rng.normal(0, 0.5, 6 if u > 0.5 else 1)).tolist())
    flat = [x for g in groups for x in g]
    m = float(np.mean(flat))
    lo, hi = intervals.cluster_mean_ci(groups)
    assert lo < m < hi and math.isclose((lo + hi) / 2, m)
    n, size = len(flat), len(groups)
    se = math.sqrt(size / (size - 1) * sum((sum(g) - len(g) * m) ** 2 for g in groups)) / n
    assert math.isclose(hi - m, intervals.t_quantile(0.975, size - 1) * se)
    # equal passes: the t interval over the person means
    equal = [rng.normal(0, 1, 4).tolist() for _ in range(20)]
    pm = [float(np.mean(g)) for g in equal]
    assert np.allclose(intervals.cluster_mean_ci(equal), intervals.mean_ci(float(np.mean(pm)), float(np.std(pm, ddof=1)), 20))
    assert intervals.cluster_mean_ci(equal[:9]) is None                  # from 10 persons on
    # coverage with person effects and unequal passes (ICC 0.5): about 95 %
    hit, reps = 0, 1000
    for _ in range(reps):
        sizes = rng.integers(1, 8, 40)
        u = rng.normal(0, math.sqrt(0.5), 40)
        lo, hi = intervals.cluster_mean_ci([(u[g] + rng.normal(0, math.sqrt(0.5), k)).tolist() for g, k in enumerate(sizes)])
        hit += lo <= 0 <= hi
    assert 0.92 <= hit / reps <= 0.975


def test_omega_bootstrap_over_persons_needs_30_of_them():
    rng = np.random.default_rng(2)
    z = rng.standard_normal(20)
    rows = np.repeat(z, 4)[:, None] * 0.7 + rng.standard_normal((80, 4)) * 0.6
    owners = np.repeat(np.arange(20), 4).tolist()
    assert intervals.omega(rows.tolist()) is not None and intervals.omega(rows.tolist(), persons=owners) is None


# --- K-3: Hartung-Knapp-Sidik-Jonkman ------------------------------------------------------------------------


def test_pooled_interval_after_hartung_knapp_with_t():
    """The example of k4_metaanalyse.py: value and tau^2 of DerSimonian-Laird stay, the interval uses the weighted
    residual variance and t(k - 1); with three studies it is wider than the z interval of before."""
    studies = [(0.82, 120, 10), (0.76, 300, 10), (0.88, 80, 10), (0.70, 45, 10), (0.85, 500, 10)]
    got = construct_pool.meta([(a, construct_pool.bonett_se(a, n, k)) for a, n, k in studies])
    assert abs(got["value"] - 0.815344) < 1e-6 and abs(got["tau2"] - 0.0816) < 1e-4
    t = np.log(1 - np.array([a for a, _, _ in studies]))
    v = np.array([2 * k / ((k - 1) * (n - 2)) for _, n, k in studies])
    w = 1 / (v + got["tau2"])
    mu = (w * t).sum() / w.sum()
    se = math.sqrt(max(1.0, (w * (t - mu) ** 2).sum() / 4) / w.sum())
    q = intervals.t_quantile(0.975, 4)
    assert np.allclose(got["ci"], (1 - math.exp(mu + q * se), 1 - math.exp(mu - q * se)))
    # three studies: wider than DerSimonian-Laird with z
    three = construct_pool.meta([(a, construct_pool.bonett_se(a, n, k)) for a, n, k in studies[:3]])
    w3 = 1 / (v[:3] + three["tau2"])
    m3 = (w3 * t[:3]).sum() / w3.sum()
    z_ci = (1 - math.exp(m3 + intervals.Z / math.sqrt(w3.sum())), 1 - math.exp(m3 - intervals.Z / math.sqrt(w3.sum())))
    assert three["ci"][0] < z_ci[0] and three["ci"][1] > z_ci[1]
    # equal studies: never narrower than DerSimonian-Laird (Röver et al., 2015), no point interval
    same = construct_pool.meta([(0.8, 0.1)] * 3)
    assert same["ci"][0] < 0.8 < same["ci"][1]
    assert construct_pool._log_ci((None, 0.8)) is None                     # an omega interval open below


# --- K-6: the pooled omega names its own number of surveys -----------------------------------------------------


def test_pooled_row_names_the_surveys_of_alpha_and_omega(app, client):
    from tests.analysis.test_construct_pool import _answers, _kut_survey

    rng = random.Random(7)
    ids = [_kut_survey(app, client, name) for name in ("Erste", "Zweite", "Dritte")]
    with app.app_context():
        items = [next(i for b in db.session.get(Survey, sid).blocks for i in b.items if i.type == "likert") for sid in ids]
        for item, n in zip(items, (40, 40, 20)):                            # the third: no omega interval below 30
            _answers(item, rng, n)
        for sid in ids:
            db.session.get(Survey, sid).status = Survey.CLOSED
        db.session.commit()
        r = construct_pool.report(db.session.scalars(db.select(Construct).filter(Construct.key == "kut")).one())
    scale = r["scales"][0]
    assert scale["alpha"]["k"] == 3 and scale["omega"]["k"] == 2
    assert scale["n"] == 100 and scale["n_omega"] == 80
    detail = client.get("/admin/constructs/kut").get_data(as_text=True)
    assert "α: 3 Befragungen, ω: 2" in detail and "ω: 80" in detail and "Hartung" in detail
    assert "IntHout, J., Ioannidis, J. P. A., &amp; Borm, G. F. (2014)" in detail


# --- K-5: weights after the cap keep mean 1 --------------------------------------------------------------------


def test_capped_weights_have_mean_one():
    """k2_gewichtung.py: 10 of 500 in a group with target 50 %: mean 0.600 before (sum 300 instead of 500)."""
    a = [1] * 10 + [0] * 490
    w, _ = weights.rake([a], [[0.5, 0.5]])
    assert abs(w.mean() - 1) < 1e-9 and abs(w.sum() - 500) < 1e-6
    assert w.min() >= weights.LOW - 1e-9 and w.max() <= weights.HIGH + 1e-9
    assert abs(w.max() / w.mean() - weights.HIGH) < 1e-9                 # the cap refers to the mean again
    # a weight vector capped and scaled in turn until both hold
    capped = weights.cap(np.array([30.0] + [1.0] * 99))
    assert abs(capped.mean() - 1) < 1e-12 and capped.max() <= weights.HIGH + 1e-12


# --- K-7: the weighted intervals with the linearization SE ---------------------------------------------------


def test_weighted_mean_interval_uses_the_linearization_se():
    """k2_gewichtung.py: a small up-weighted group with the larger spread. Kish's SD / sqrt(n_eff) was too narrow
    there; the linearization SE is sum w^2 (x - m)^2 / (sum w)^2 times n / (n - 1)."""
    rng = np.random.default_rng(1)
    small, large = rng.normal(0, 3, 40), rng.normal(0, 0.5, 160)
    x = np.concatenate([small, large])
    w = np.array([2.5] * 40 + [0.625] * 160)
    m = np.average(x, weights=w)
    se = math.sqrt(200 / 199 * np.sum(w ** 2 * (x - m) ** 2)) / w.sum()
    assert math.isclose(weights.mean_se(x.tolist(), w.tolist()), se)
    stats = results.numeric_stats(x.tolist(), weights=w.tolist())
    half = intervals.t_quantile(0.975, stats["n_eff"] - 1) * se
    assert np.allclose(stats["ci"], (m - half, m + half))
    kish = stats["sd"] / math.sqrt(stats["n_eff"])
    assert se > 1.1 * kish                                                 # Kish was too narrow here
    # all weights 1: the interval of the unweighted mean
    plain = results.numeric_stats(x.tolist(), weights=[1.0] * 200)
    assert np.allclose(plain["ci"], results.numeric_stats(x.tolist())["ci"])
