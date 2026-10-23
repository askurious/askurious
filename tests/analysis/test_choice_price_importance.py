"""A price the choices cannot separate from the attributes gets no importance (audit 2026-10-23, M7)."""
import numpy as np

from app.choice_model import ChoiceSet, Coding, price_scale, summarise

LEVELS = [3, 2]
ATTRS = [{"code": f"A{a}", "label": f"A{a}", "level_labels": [f"L{lv}" for lv in range(n)]} for a, n in enumerate(LEVELS)]


def test_price_tied_to_an_attribute_is_without_importance():
    """The price only changed together with A1 (10 with L0, 40 with L1): its range is as arbitrary as the split
    between both, so it is left out of the importances and named in "Ohne Wichtigkeit"."""
    rng = np.random.default_rng(3)
    prices = (10, 40)
    coding = Coding(LEVELS, price_scale(prices), asc=True, price_center=25)
    sets, shown = [], []
    for p in range(150):
        for _ in range(8):
            profs = [[int(rng.integers(3)), int(rng.integers(2))] for _ in range(3)]
            pr = [prices[q[1]] for q in profs]
            v = np.array([[0.6, 0.0, -0.6][q[0]] - 0.02 * (c - 25) for q, c in zip(profs, pr)] + [-0.5])
            v = v + rng.gumbel(size=4)
            sets.append(ChoiceSet([coding.row(q, c) for q, c in zip(profs, pr)] + [coding.asc_row()], int(np.argmax(v)), p))
            shown += pr
    m = summarise(sets, coding, ATTRS, 150, shown)
    assert m["price"] is not None and m["price"]["importance"] is None
    shares = [a["importance"] for a in m["attributes"] if a["importance"] is not None]
    assert shares and abs(sum(shares) - 1) < 1e-9
    [warning] = [w for w in m["warnings"] if w.startswith("Ohne Wichtigkeit:")]
    assert "Preis" in warning
