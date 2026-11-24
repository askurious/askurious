"""Several chains per HB estimation (app/hb_chains.py): merging, reproducibility, processes, convergence over the chains."""
import functools

import numpy as np
import pytest

from app import hb, hb_chains
from app import hb_convergence as hc
from app.choice_model import ChoiceSet


def simulate(n=60, tasks=8, k=4, alts=3, seed=7, spread=0.6):
    rng = np.random.default_rng(seed)
    mu = rng.normal(0, 1, k)
    people = []
    for _ in range(n):
        b = mu + rng.normal(0, spread, k)
        sets = []
        for _ in range(tasks):
            X = rng.choice([-1.0, 0.0, 1.0], size=(alts, k))
            sets.append(ChoiceSet(X, int(np.argmax(X @ b + rng.gumbel(size=alts)))))
        people.append(sets)
    return people


def _scaled(B, factor):
    return B * factor


def _mean(B, stat):
    return stat(B).mean(axis=0)


SHORT = {"burn": 150, "draws": 400, "thin": 2, "seed": 3}


def test_merge_matches_the_single_chains():
    """beta, mu, person_stat and the traces of the merged result come from the chains run on their own with the
    random stream [seed, chain] and a quarter of the iterations each."""
    people, k = simulate(), 4
    stat = functools.partial(_scaled, factor=2.0)
    r = hb_chains.estimate(people, k, chains=4, workers=1, person_stat=stat,
                           summary=functools.partial(_mean, stat=stat), **SHORT)
    single = [hb.estimate(people, k, burn=150, draws=100, thin=2, seed=[3, c], person_stat=stat,
                          summary=functools.partial(_mean, stat=stat), keep=25) for c in range(4)]
    assert r["chains"] == 4 and r["kept"] == sum(s["kept"] for s in single) == 200
    assert np.allclose(r["beta"], np.mean([s["beta"] for s in single], axis=0))
    assert np.allclose(r["person_stat"], 2 * r["beta"])
    assert np.allclose(r["mu"], np.mean([s["mu"] for s in single], axis=0))
    assert np.allclose(r["sigma_diag"], np.mean([s["sigma_diag"] for s in single], axis=0))
    assert np.allclose(r["ll_trace"], np.concatenate([s["ll_trace"] for s in single]))
    assert np.allclose(r["mu_trace"], np.concatenate([s["mu_trace"] for s in single]))
    assert np.allclose(r["summaries"], np.concatenate([s["summaries"] for s in single]))
    assert r["draws"].shape == (hb.KEEP_DRAWS, len(people), k)
    assert np.allclose(r["draws"][:25], single[0]["draws"])
    assert np.allclose(r["rlh"], hb.rlh(hb.Data(people, k), r["beta"]))
    assert r["acceptance"] == pytest.approx(np.mean([s["acceptance"] for s in single]))
    assert r["settings"]["chains"] == 4 and r["settings"]["draws"] == 400 and r["settings"]["seed"] == 3


def test_person_rhat_over_all_halves_matches_two_part_formula():
    """For two parts the split-R-hat per person of hb_chains is that of hb.person_rhat."""
    rng = np.random.default_rng(1)
    sums = rng.normal(size=(2, 5, 3)) * 10
    squares = sums ** 2 / 10 + rng.uniform(1, 2, size=(2, 5, 3)) * 9
    assert np.allclose(hb_chains.person_rhat(sums, squares, 10), hb.person_rhat(sums, squares, 10), equal_nan=True)


def test_person_rhat_sees_one_chain_off():
    """A chain stuck elsewhere lifts the split-R-hat of the persons, the halves of four settled chains do not."""
    rng = np.random.default_rng(2)
    h, n, k = 50, 30, 2
    draws = rng.normal(size=(8, h, n, k))
    sums, squares = draws.sum(axis=1), (draws ** 2).sum(axis=1)
    assert np.nanmax(hb_chains.person_rhat(sums, squares, h)) < 1.2
    draws[6:] += 3.0
    sums, squares = draws.sum(axis=1), (draws ** 2).sum(axis=1)
    assert np.nanmin(hb_chains.person_rhat(sums, squares, h)) > 1.3


def test_same_seed_same_result_other_seed_other_result():
    people, k = simulate(n=30), 4
    a = hb_chains.estimate(people, k, chains=3, workers=1, **SHORT)
    b = hb_chains.estimate(people, k, chains=3, workers=1, **SHORT)
    c = hb_chains.estimate(people, k, chains=3, workers=1, **{**SHORT, "seed": 4})
    assert np.array_equal(a["beta"], b["beta"]) and np.array_equal(a["ll_trace"], b["ll_trace"])
    assert not np.allclose(a["beta"], c["beta"])


def test_one_chain_is_exactly_the_old_estimate():
    people, k = simulate(n=30), 4
    r = hb_chains.estimate(people, k, chains=1, **SHORT)
    old = hb.estimate(people, k, **SHORT)
    assert r["chains"] == 1 and np.array_equal(r["beta"], old["beta"]) and "chains" not in r["settings"]


def test_processes_give_the_same_result_as_one_after_another():
    """Chains in processes of their own (spawn) and one after another in this process: the same numbers; the
    progress reaches 1 and only grows."""
    people, k = simulate(n=30), 4
    stat = functools.partial(_scaled, factor=1.0)
    seen = []
    kw = {**SHORT, "person_stat": stat, "summary": functools.partial(_mean, stat=stat)}
    seq = hb_chains.estimate(people, k, chains=2, workers=1, **kw)
    par = hb_chains.estimate(people, k, chains=2, workers=2, progress=seen.append, **kw)
    for key in ("beta", "mu", "sigma_diag", "ll_trace", "mu_trace", "summaries", "person_rhat", "draws", "person_stat"):
        assert np.allclose(seq[key], par[key], equal_nan=True), key
    assert seen and seen[-1] == pytest.approx(1.0) and seen == sorted(seen)


def test_a_failing_chain_stops_the_estimation():
    with pytest.raises(RuntimeError, match="chain"):
        hb_chains.estimate(simulate(n=12), 4, chains=2, workers=2, prior="unknown", **SHORT)


def test_processes_follow_the_environment(monkeypatch):
    monkeypatch.setenv("HB_CHAIN_PROCESSES", "3")
    assert hb_chains.processes(4) == 3 and hb_chains.processes(2) == 2
    monkeypatch.setenv("HB_CHAIN_PROCESSES", "1")
    assert hb_chains.processes(4) == 1
    monkeypatch.delenv("HB_CHAIN_PROCESSES")
    assert 1 <= hb_chains.processes(4) <= 4


def test_geweke_limits_are_corrected_over_the_chains():
    people, k = simulate(n=30), 4
    r = hb_chains.estimate(people, k, chains=4, workers=1, **SHORT)
    assert r["geweke_mu_limit"] == pytest.approx(round(hb.geweke_limits(4 * k)[1], 3))
    assert r["geweke_ll_limit"] == pytest.approx(round(hb.geweke_limits(4)[1], 3))
    assert r["geweke_ll_limit"] > hb.geweke_limits(k)[0]


def test_extra_keys_of_a_later_estimate_are_merged_by_kind(monkeypatch):
    """Keys hb.estimate may add later (e.g. fixed parameters): numbers averaged, traces one after another."""
    real = hb.estimate

    def with_fixed(*args, **kwargs):
        r = real(*args, **kwargs)
        seed = kwargs["seed"][1]
        return {**r, "fixed": np.array([seed, 2.0 * seed]), "fixed_trace": np.full((r["kept"], 2), seed),
                "note": f"chain {seed}"}

    monkeypatch.setattr(hb, "estimate", with_fixed)
    r = hb_chains.estimate(simulate(n=20), 4, chains=4, workers=1, **SHORT)
    assert np.allclose(r["fixed"], [1.5, 3.0])
    assert r["fixed_trace"].shape == (r["kept"], 2) and r["fixed_trace"][-1, 0] == 3
    assert r["note"] == "chain 0"


def test_diagnose_compares_the_chains():
    """R-hat over the chains: four chains around the same value pass, one shifted chain fails, though every chain on
    its own is settled (its halves agree)."""
    rng = np.random.default_rng(5)
    n = 500
    good = rng.normal(size=(4 * n, 2))
    ll = rng.normal(size=4 * n)
    ok = hc.diagnose(good, ll, chains=4)
    assert ok["diag_ok"] and ok["chains"] == 4 and ok["rhat_max"] < 1.01
    shifted = good.copy()
    shifted[3 * n:, 0] += 1.0
    bad = hc.diagnose(shifted, ll, chains=4)
    assert not bad["diag_ok"] and bad["rhat_max"] > 1.05
    # read as one chain, the shift falls into the second half only: R-hat of the old kind is far smaller
    assert hc.rhat(shifted[:, 0]) < hc.rhat(shifted[:, 0], 4)
    assert len(ok["ll_trace"]) == hc.TRACE_POINTS


def test_trace_chart_one_line_per_chain():
    chart = hc.trace_chart(list(np.arange(40.0)), chains=4)
    assert chart["chains"] == 4 and len(chart["lines"]) == 4 and chart["points"] == chart["lines"][0]
    assert hc.trace_chart(list(np.arange(40.0)))["lines"] == [hc.trace_chart(list(np.arange(40.0)))["points"]]


def test_length_text_names_the_chains():
    four = {"burn": 5000, "draws": 20000, "thin": 10, "seed": 1, "chains": 4}
    assert hb_chains.length_text(four, "de") == "4 Ketten mit je 5.000 Burn-in und 5.000 Iterationen"
    assert hb_chains.length_text(four, "en") == "4 chains of 5,000 burn-in and 5,000 iterations each"
    old = {"burn": 5000, "draws": 20000, "thin": 10, "seed": 1}
    assert hb_chains.length_text(old, "de") == "5000 Burn-in, 20000 Iterationen"


def test_longer_run_keeps_the_chains():
    defaults = {"burn": 5000, "draws": 20000, "thin": 10, "seed": 1, "chains": 4}
    assert hc.longer_settings(defaults, defaults) == {**defaults, "burn": 10000, "draws": 40000}
    old = {"burn": 5000, "draws": 20000, "thin": 10, "seed": 1}
    assert hc.longer_settings(old, defaults)["chains"] == 4   # a result of one chain runs again with the chains of now


@pytest.mark.slow
def test_four_chains_pass_the_rule_on_healthy_data():
    """Simulated healthy CBC data, the default length in four chains (5000 burn-in, 5000 iterations each): the
    rule R-hat < 1.01 and ESS >= 400 over the chains holds."""
    people, k = simulate(n=150, tasks=10, k=5, seed=11, spread=0.5), 5
    r = hb_chains.estimate(people, k, chains=4, workers=1, burn=5000, draws=20000, thin=10, seed=1)
    d = hc.diagnose(r["mu_trace"], r["ll_trace"], geweke_ok=r["converged"], sigma=r["sigma_trace"],
                    person=r["person_rhat"], chains=r["chains"])
    assert r["kept"] == 2000 and d["chains"] == 4
    assert d["diag_ok"], d
