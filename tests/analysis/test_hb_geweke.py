"""Limits of the Geweke-type check of the HB chains (app/hb.py, geweke_limits and geweke_check; Ralf, 2026-11-23):
log-likelihood against Student's t with 2 (BATCHES - 1) degrees of freedom and only a hint, population means against
a Bonferroni limit of the same t without a "one may pass" rule. Results stored before keep being shown."""
import numpy as np
import pytest

from app import hb, hb_convergence


def test_limits():
    ll, mu = hb.geweke_limits(1)
    assert ll == pytest.approx(2.1009, abs=1e-3)  # t(18), two-sided 5 %
    assert mu == pytest.approx(ll)
    assert hb.geweke_limits(20)[1] == pytest.approx(3.5101, abs=1e-3)  # t(18) at 1 - 0.05 / 40
    assert hb.geweke_limits(100)[1] == pytest.approx(4.2332, abs=1e-3)
    assert hb.geweke_limits(0)[1] == pytest.approx(ll)


def test_bonferroni_holds_for_healthy_chains():
    """Independent draws: some mean beyond its limit in about 5 % of the runs (the normal quantile gave 11 %)."""
    flagged = sum(hb.geweke_check(np.random.default_rng(s).standard_normal(2000),
                                  np.random.default_rng(s + 1000).standard_normal((2000, 20)))["geweke_mu_flagged"] > 0
                  for s in range(200))
    assert flagged <= 20


def test_log_likelihood_only_hints():
    rng = np.random.default_rng(3)
    lls = np.concatenate([rng.standard_normal(1000) - 0.3, rng.standard_normal(1000)])  # second half better
    gw = hb.geweke_check(lls, rng.standard_normal((2000, 3)))
    assert gw["geweke_ll"] < -gw["geweke_ll_limit"] and gw["geweke_ll_hint"]
    assert gw["converged"] and gw["geweke_mu_flagged"] == 0


def test_one_drifting_mean_fails():
    rng = np.random.default_rng(5)
    mus = rng.standard_normal((2000, 20))
    mus[1000:, 5] += 0.5
    gw = hb.geweke_check(rng.standard_normal(2000), mus)
    assert gw["geweke_mu_flagged"] == 1 and not gw["converged"]
    assert gw["geweke_mu_limit"] == pytest.approx(3.510, abs=1e-3)


def test_too_short_is_not_checked():
    gw = hb.geweke_check(np.zeros(10), np.zeros((10, 2)))
    assert not gw["checked"] and not gw["converged"] and not gw["geweke_ll_hint"]


def test_cva_run_stores_the_limits():
    import random

    from app import cva_analysis, cva_design, cva_hb

    levels = [3, 2]
    plan = cva_design.generate(levels, 6, "single", 1, seed=1)[0]
    rng = random.Random(1)
    persons = [{"pid": f"p{p}", "x": [[1.0] + cva_analysis.effects_row(t, levels) for t in plan],
                "y": [5 + t[0] - t[1] + rng.gauss(0, 0.6) for t in plan]} for p in range(20)]
    res = cva_hb.run({"format": cva_hb.FORMAT, "type": "cva", "form": "single", "scale": [0, 10], "levels": levels,
                      "persons": persons, "key": "k", "settings": {"burn": 200, "draws": 400, "thin": 2, "seed": 1}})
    k = len(res["mu"])
    assert res["geweke_ll_limit"] == pytest.approx(hb.geweke_limits(k)[0], abs=1e-3)
    assert res["geweke_mu_limit"] == pytest.approx(hb.geweke_limits(k)[1], abs=1e-3)
    assert isinstance(res["geweke_ll_hint"], bool)
    assert res["geweke_ok"] == (res["checked"] and res["geweke_mu_flagged"] == 0)


def test_view_of_an_old_result():
    v = hb_convergence.view({"settings": {"burn": 10, "draws": 10, "thin": 1, "seed": 1}, "diag_ok": True,
                             "rhat_max": 1.0, "ess_min": 900, "geweke_ok": True, "kept": 1000, "geweke_ll": -2.5})
    assert v["geweke_ll_limit"] is None and v["geweke_mu_limit"] is None and v["geweke_ll_hint"] is False
