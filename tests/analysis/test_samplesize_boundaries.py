"""Independent boundary cases from the mathematical review of 2026-10-13."""
import math
from statistics import NormalDist

import pytest

from tests.analysis.test_samplesize import js, needs_node


@needs_node
@pytest.mark.parametrize("measurements,rho", [(4, -.8), (3, -.5), (4, -1 / 3), (20, -.1)])
def test_impossible_repeated_measure_correlation_rejects_plan_and_curve(measurements, rho):
    values = dict(alpha=.05, power=.8, meas=measurements, rmgroups=2, rho=rho, eps=1, f=.25)
    for effect in ("within", "between", "inter"):
        v = {**values, "rmeffect": effect}
        planned, sensitivity, curve, raw = js(["plan", "rm", v],
            ["plan", "rm", {**v, "mode": "effect", "avail": 100}], ["curve", "rm", v],
            ["rmPower", 100, 2, measurements, rho, 1, .25, effect, .05])
        assert not planned["ok"] and planned["warning"]
        assert not sensitivity["ok"] and sensitivity["warning"]
        assert curve is None and raw is None  # invalid raw power is NaN, JSON represents it as null


@needs_node
def test_valid_negative_repeated_measure_correlation_is_still_accepted():
    v = dict(alpha=.05, power=.8, meas=4, rmgroups=1, rho=-.3, eps=1, f=.25)
    assert js(["plan", "rm", v])[0]["ok"]


@needs_node
def test_two_sided_proportion_power_includes_both_normal_tails():
    cases = [(20, 20, .5, .5), (20, 20, .5, .501), (80, 120, .3, .45), (120, 80, .45, .3)]
    calls = [["propsPower", *c, .05, sided] for c in cases for sided in (False, True)]
    got = js(*calls)
    norm = NormalDist()
    for i, (n1, n2, p1, p2) in enumerate(cases):
        pooled = (n1 * p1 + n2 * p2) / (n1 + n2)
        se0 = math.sqrt(pooled * (1 - pooled) * (1/n1 + 1/n2))
        se1 = math.sqrt(p1*(1-p1)/n1 + p2*(1-p2)/n2)
        d = abs(p1 - p2)
        for sided in (False, True):
            z = norm.inv_cdf(.95 if sided else .975)
            expected = norm.cdf((d-z*se0)/se1)
            if not sided:
                expected += norm.cdf((-d-z*se0)/se1)
            assert got[2*i + int(sided)] == pytest.approx(expected, abs=2e-8)
    assert got[0] == pytest.approx(.05, abs=2e-8)


@needs_node
def test_finite_mean_plans_actual_degrees_of_freedom():
    v = dict(sd=1, emargin=.5, conf=.95, pop=10)
    planned, reached, seven, eight = js(["plan", "mean", v],
        ["plan", "mean", {**v, "mode": "effect", "avail": 7}],
        ["meanHalf", 7, 1, .95, 10, 1], ["meanHalf", 8, 1, .95, 10, 1])
    assert planned["total"] == 8 and reached["effect"] == "± 0,51"
    assert seven == pytest.approx(.5065588785665371, abs=1e-10)
    assert eight < .5 < seven


@needs_node
@pytest.mark.parametrize("population,deff,share", [(0, 1, 1), (10, 1, 1), (200, 2, .25), (500, 3, .3)])
def test_mean_plan_and_sensitivity_share_formula(population, deff, share):
    v = dict(sd=2, emargin=.5, conf=.95, pop=population, cv=math.sqrt(deff-1), share=share*100)
    result = js(["plan", "mean", v])[0]
    n = result["total"]
    sub = math.floor(n*share+1e-9)
    prev = math.floor((n-1)*share+1e-9)
    width, before, reverse = js(["meanHalf", sub, 2, .95, population*share, deff],
        ["meanHalf", prev, 2, .95, population*share, deff],
        ["plan", "mean", {**v, "mode": "effect", "avail": n}])
    assert width <= .5 and (before is None or before > .5)
    assert reverse["ok"] and reverse["warning"] == ""
    assert float(reverse["effect"].removeprefix("± ").replace(",", ".")) == pytest.approx(width, abs=.0051)


@needs_node
def test_census_has_zero_sampling_error_even_with_design_effect():
    v = dict(sd=1, conf=.95, pop=10, mode="effect", avail=10, cv=3)
    r = js(["plan", "mean", v])[0]
    assert r["ok"] and r["effect"] == "± 0,00" and "Vollerhebung" in r["detail"]
