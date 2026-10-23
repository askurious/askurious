"""PLS moderation keeps formative constructs in mode B in both stages (audit 2026-10-23, M1)."""
import numpy as np

from app import sem


def _data():
    rng = np.random.default_rng(5)
    n = 800
    parts = rng.normal(size=(n, 3))
    f = parts @ np.array([0.7, 0.4, 0.1])
    f /= f.std()
    w = rng.normal(size=n)
    y = 0.4 * f + 0.3 * w + 0.3 * f * w + rng.normal(size=n) * 0.7

    def items(v):
        return np.column_stack([0.8 * v + 0.6 * rng.normal(size=n) for _ in range(3)])

    x = np.hstack([parts, items(w), items(y)])
    cols = ["f0", "f1", "f2", "w0", "w1", "w2", "y0", "y1", "y2"]
    spec = {"latent": {"F": cols[:3], "W": cols[3:6], "Y": cols[6:]}, "observed": ["F:W"],
            "paths": [("F", "Y"), ("W", "Y"), ("F:W", "Y")], "products": {"F:W": {"x": "F", "w": "W"}}}
    return x, cols, spec


def _paths(result):
    return {(p["from"], p["to"]): p["std"] for p in result["paths"]}


def test_formative_construct_changes_the_moderated_model():
    """Before, the first stage dropped "formative": the moderated model was the same as with a reflective F."""
    x, cols, spec = _data()
    formative = sem.fit_pls(x, cols, {**spec, "formative": ["F"]})
    reflective = sem.fit_pls(x, cols, spec)
    assert "F" in formative["formative"] and not reflective["formative"]
    a, b = _paths(formative), _paths(reflective)
    assert max(abs(a[k] - b[k]) for k in a) > 1e-4
    # the scores of the first stage are those of the model without the interaction, F in mode B
    base = {k: v for k, v in spec.items() if k != "products"}
    base.update(observed=[], paths=[("F", "Y"), ("W", "Y")], formative=["F"])
    first = sem.fit_pls(x, cols, base, light=True)
    assert first["weights"]["F"] and len(first["weights"]["F"]) == 3
