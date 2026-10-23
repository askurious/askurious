"""The estimators of the SEM extensions (2026-10-17) against independently computed reference values: simulated data
with heavy tails (seed 2026, values rounded to six places) and the reference numbers for them (ML with robust
standard errors and the Satorra-Bentler test, FIML with robust errors, the four levels of invariance, latent means, a
second-order factor, WLSMV on the data cut into four categories). Not compared here: sampling weights (a reference
computation with weights differs by about 2 % in the errors, decisions.md)."""
import math

import numpy as np
import pytest

from app import sem, sem_means, sem_ordinal


def _data():
    rng = np.random.default_rng(2026)
    n = 400

    def three(n, shift=0.0, intercept=0.0):
        a = rng.normal(size=n) + shift
        b = 0.5 * a + math.sqrt(0.75) * rng.normal(size=n)
        c = 0.4 * b + 0.3 * a + math.sqrt(0.63) * rng.normal(size=n)

        def items(f, k):
            return [0.7 * f + math.sqrt(0.51) * rng.normal(size=n) for _ in range(k)]

        x = np.column_stack(items(a, 3) + items(b, 3) + items(c, 3))
        x *= np.sqrt(6 / rng.chisquare(8, size=n))[:, None]
        x[:, 5] += intercept
        return x

    x = np.vstack([three(n), three(n, 0.4, 0.5)])
    gaps = x.copy()
    gaps[rng.random(gaps.shape) < 0.08] = np.nan
    return np.round(x, 6), np.round(gaps, 6), n


COLS = [f"y{i}" for i in range(1, 10)]
LATENT = {"A": COLS[:3], "B": COLS[3:6], "C": COLS[6:]}
SPEC = {"latent": LATENT, "paths": [("A", "B"), ("B", "C"), ("A", "C")]}


def _ses(fit):
    found = {(p["from"], p["to"]): p["se"] for p in fit["paths"]}
    return [found[("A", "B")], found[("B", "C")], found[("A", "C")]]


def test_mlm_and_robust_fit_reference_values():
    x, _, n = _data()
    fit = sem.fit_ml(x[:n], COLS, SPEC)
    assert fit["chi2"] == pytest.approx(29.311507, abs=1e-4)
    assert fit["scaled"]["chi2"] == pytest.approx(18.339901, abs=1e-3)
    rob = fit["robust_fit"]
    assert (rob["cfi"], rob["tli"], rob["rmsea"]) == pytest.approx((1.0, 1.014124, 0.0), abs=1e-4)
    assert fit["srmr"] == pytest.approx(0.024568, abs=1e-5)
    assert _ses(fit) == pytest.approx([0.084374, 0.088970, 0.089111], abs=2e-5)


def test_fiml_with_mlr_reference_values():
    _, gaps, n = _data()
    fit = sem_means.fit_fiml(gaps[:n], COLS, SPEC)
    assert fit["chi2"] == pytest.approx(30.230422, abs=1e-3)
    assert fit["scaled"]["chi2"] == pytest.approx(21.700635, abs=1e-2)
    assert fit["srmr"] == pytest.approx(0.024218, abs=1e-4)
    assert _ses(fit) == pytest.approx([0.092590, 0.088532, 0.082518], abs=1e-4)


def test_invariance_and_latent_means_reference_values():
    x, _, n = _data()
    found = sem_means.invariance([x[:n], x[n:]], COLS, {"latent": LATENT})
    expected = [(74.202186, 46.313996, 0.026957), (77.956501, 48.639970, 0.030317),
              (116.452635, 76.141421, 0.037785), (127.393078, 80.719470, 0.041334)]
    for level, (chi2, scaled, srmr) in zip(found["levels"], expected):
        fit = level["fit"]
        assert fit["chi2"] == pytest.approx(chi2, abs=1e-3)
        assert fit["scaled"]["chi2"] == pytest.approx(scaled, abs=1e-2)
        assert fit["srmr"] == pytest.approx(srmr, abs=1e-5)
    means = found["levels"][2]["fit"]["latent_means"]
    assert [means[k][0]["est"] for k in "ABC"] == pytest.approx([0.237964, 0.247713, 0.169112], abs=1e-4)
    assert [means[k][0]["se"] for k in "ABC"] == pytest.approx([0.058349, 0.061464, 0.058974], abs=1e-4)


def test_second_order_reference_values():
    x, _, n = _data()
    fit = sem.fit_ml(x[:n], COLS, {"latent": LATENT, "higher": {"G": ["A", "B", "C"]}})
    assert fit["chi2"] == pytest.approx(29.311507, abs=1e-4)


def test_wlsmv_reference_values():
    """Gamma from the cross-products of the case scores, divided by N - 1 (Muthén et al., 1997)."""
    x, _, n = _data()
    codes = (np.digitize(x[:n], np.array([-1.0, 0.0, 0.8])) + 1).astype(float)
    fit = sem_ordinal.fit_wlsmv(codes, COLS, SPEC)
    assert fit["chi2"] == pytest.approx(11.857780, abs=1e-4)
    assert fit["scaled"]["chi2"] == pytest.approx(20.945877, abs=1e-3)
    assert (fit["cfi"], fit["rmsea"], fit["srmr"]) == pytest.approx((1.0, 0.0, 0.025933), abs=1e-5)
    std = {(p["from"], p["to"]): p["std"] for p in fit["paths"]}
    assert [std[("A", "B")], std[("B", "C")], std[("A", "C")]] == pytest.approx([0.464138, 0.345007, 0.384424], abs=1e-5)
    assert _ses(fit) == pytest.approx([0.075206, 0.070903, 0.082513], abs=1e-5)
