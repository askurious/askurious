"""Hierarchical Bayes where the data set no limit (audit 2026-10-01, H-1 to H-3): a proper prior on the means,
separated statements and levels named and shown without interval, importance from posterior mean part-worths."""
import numpy as np

from app import conjoint_hb, hb, maxdiff_hb
from app.choice_model import Coding
from app.maxdiff_analysis import parameters
from app.maxdiff_design import generate

SHORT = {"burn": 400, "draws": 400, "thin": 2, "seed": 1}


def test_means_have_a_proper_prior():
    """The means are drawn from N(V n Sigma^-1 mean, V), V = (prior precision + n Sigma^-1)^-1: pulled towards 0
    where the data are weak. With the flat prior (mean of the betas, Sigma / n) a separated mean had no limit."""
    rng = np.random.default_rng(0)
    prec = np.eye(2) / hb.MU_PRIOR_SD ** 2
    draws = np.array([hb._draw_mu(rng, np.array([10.0, -10.0]), np.eye(2), 4, prec) for _ in range(20000)])
    v = 1 / (1 / hb.MU_PRIOR_SD ** 2 + 4)
    assert np.allclose(draws.mean(axis=0), [v * 40, -v * 40], atol=0.02) and abs(v * 40 - 10) > 0.05  # not the flat 10
    assert np.allclose(draws.var(axis=0), v, atol=0.01) and abs(np.corrcoef(draws.T)[0, 1]) < 0.03
    # with many persons the prior does not matter
    assert np.allclose(hb._draw_mu(rng, np.array([1.0, 2.0]), np.eye(2) * 1e-9, 500, prec), [1.0, 2.0], atol=1e-3)


def _maxdiff_input(n=30, seed=1, anchor=None):
    """Statement 1 is always chosen as best when shown."""
    rng = np.random.default_rng(seed)
    versions = generate(8, 4, 8, 2, seed=1)
    true = np.array([0.0, 0.8, 0.5, 0.2, 0.0, -0.3, -0.6, -1.0])
    persons, answers = [], []
    for p in range(n):
        v, tasks = p % 2, {}
        u = true + 0.5 * rng.standard_normal(8)
        for t, s in enumerate(versions[v]):
            b = 0 if 0 in s else s[int(np.argmax(u[s] + rng.gumbel(size=len(s))))]
            rest = [i for i in s if i != b]
            tasks[t] = (b, rest[int(np.argmax(-u[rest] + rng.gumbel(size=len(rest))))])
        answers.append((v, tasks))
        persons.append({"pid": f"p{p}", "v": v, "tasks": {str(t): list(bw) for t, bw in tasks.items()}, "anchor": anchor})
    est, col, k, thr = parameters(8, versions, answers, anchor is not None)
    return {"key": "x", "versions": versions, "n_items": 8, "est": est, "col": col, "k": k, "thr": thr, "set_size": 4,
            "persons": persons, "settings": SHORT}


def test_maxdiff_names_a_separated_statement_and_shows_it_without_interval():
    res = maxdiff_hb.run(_maxdiff_input(anchor=[1] * 8))
    assert res["separated"] == [0] and res["anchor_flat"]
    res.update(created="2026-10-01T12:00:00+00:00", pool={})
    view = maxdiff_hb.summary(res, [f"S{i + 1}" for i in range(8)])
    first = next(r for r in view["rows"] if r["n"] == 1)
    assert first["separated"] and first["lo"] is None and abs(first["u"]) < 8 * hb.MU_PRIOR_SD
    assert all(r["lo"] is not None for r in view["rows"] if r["n"] != 1)
    text = " ".join(view["warnings"])
    assert "Aussage 1:" in text and "begrenzt nur die Priori" in text and "alle oder keine Aussagen" in text
    # as in the pooled model the separated statement stands outside the sum 100 (audit 2026-10-21, D5; before: M-16 note)
    assert res["scores_without_separated"] and "nur für die übrigen Aussagen" in text
    assert abs(sum(r["score"] for r in view["rows"] if not r["separated"]) - 100) < 0.5
    assert view["threshold"]["lo"] is None


def _conjoint_input(n=40, seed=2, never=None):
    rng = np.random.default_rng(seed)
    levels = [3, 4, 2]
    true = [np.array([1.2, 0.0, -1.2]), np.array([0.2, 0.1, -0.1, -0.2]), np.array([0.6, -0.6])]
    coding = Coding(levels, None, asc=True)
    persons = []
    for p in range(n):
        own = [x + 0.4 * rng.standard_normal(len(x)) for x in true]
        sets = []
        for _ in range(10):
            profs = [[int(rng.integers(n_)) for n_ in levels] for _ in range(3)]
            v = np.array([sum(own[a][q[a]] for a in range(3)) for q in profs] + [-0.5]) + rng.gumbel(size=4)
            if never:
                v[[i for i, q in enumerate(profs) if q[never[0]] == never[1]]] = -np.inf
            sets.append(([coding.row(q).tolist() for q in profs] + [coding.asc_row().tolist()], int(np.argmax(v))))
        persons.append({"pid": f"p{p}", "sets": sets})
    return {"key": "x", "type": "conjoint", "settings": SHORT, "price_range": None, "persons": persons,
            "coding": {"levels": levels, "price_scale": None, "asc": True, "linear": [], "price_center": 0.0}}


def test_importance_comes_from_each_persons_mean_part_worths():
    res = conjoint_hb.run(_conjoint_input())
    util, imp = np.array(res["util"]), np.array(res["importance"])
    ranges = np.column_stack([util[:, start:start + n].max(axis=1) - util[:, start:start + n].min(axis=1)
                              for _, start, n in res["spans"]])
    assert np.allclose(imp, ranges / ranges.sum(axis=1, keepdims=True), atol=2e-3)   # stored values are rounded
    assert np.allclose(imp.sum(axis=1), 1, atol=1e-3) and res["imp_lo"] is None and res["imp_hi"] is None
    assert imp.mean(axis=0).argmax() == 0 and imp.mean(axis=0)[1] < 0.3              # the weak attribute stays weak
    assert res["separated"] == [] and res["loose"] == [] and res["constant_side"] is None


def test_conjoint_stores_what_the_data_leave_open():
    res = conjoint_hb.run(_conjoint_input(never=(0, 2)))
    assert res["separated"] == [[0, 2]] and res["loose"] == [0]
    assert np.abs(np.array(res["util"])).max() < 8 * hb.MU_PRIOR_SD


def test_conjoint_docstring_names_the_importance_from_posterior_means():
    """Audit 2026-10-03, C-3: the module text still described the importance at every draw."""
    from app import conjoint_hb

    assert "computed at every kept draw and averaged" not in conjoint_hb.__doc__
    assert "posterior mean part-worths" in conjoint_hb.__doc__
