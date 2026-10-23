"""What the pooled choice models show when the data do not determine a value (audit 2026-10-01, M-1 to M-4, M-6):
no standard error and no willingness to pay for what only the weak prior sets, the right cause in the warning,
the price centred on the mean price, and separated MaxDiff statements off the score scale."""
import numpy as np

from app import choice_model
from app.choice_model import ChoiceSet, Coding, price_scale, summarise
from app.maxdiff_analysis import mnl
from app.maxdiff_design import generate

LEVELS = [3, 4, 2]
TRUE = [np.array([0.6, 0.0, -0.6]), np.array([0.9, 0.3, -0.4, -0.8]), np.array([0.25, -0.25])]
ATTRS = [{"code": f"A{a}", "label": f"A{a}", "level_labels": [f"L{lv}" for lv in range(n)]} for a, n in enumerate(LEVELS)]


def data(n, seed, prices=(10, 20, 30, 40), price_beta=-0.05, center=None, none=-0.5, profile=None, never=None):
    """Pooled CBC sets with a linear price and a none option. profile(rng) draws one profile; `never`
    (attribute, level) is never chosen; none=None: the none option is offered but never taken."""
    rng = np.random.default_rng(seed)
    center = sum(prices) / len(prices) if center is None else center
    coding = Coding(LEVELS, price_scale(prices), asc=True, price_center=center)
    draw = profile or (lambda rng: [int(rng.integers(n_)) for n_ in LEVELS])
    sets, shown = [], []
    for p in range(n):
        for _ in range(10):
            profs = [draw(rng) for _ in range(3)]
            pr = [prices[int(rng.integers(len(prices)))] for _ in range(3)]
            v = [sum(TRUE[a][q[a]] for a in range(3)) + price_beta * (c - sum(prices) / len(prices))
                 for q, c in zip(profs, pr)] + [-np.inf if none is None else none]
            v = np.array(v) + rng.gumbel(size=4)
            if never:
                v[[i for i, q in enumerate(profs) if q[never[0]] == never[1]]] = -np.inf
            sets.append(ChoiceSet([coding.row(q, c) for q, c in zip(profs, pr)] + [coding.asc_row()], int(np.argmax(v)), p))
            shown += pr
    return sets, coding, shown


def test_prices_far_from_zero_are_no_separation():
    """Prices 900 to 1100: uncentred, the constant was about -16, read as separation and shrunk."""
    sets, coding, shown = data(300, 1, prices=(900, 1000, 1100), price_beta=-0.015)
    m = summarise(sets, coding, ATTRS, 300, shown)
    assert not m["ridge"] and not m["warnings"] and m["price_centered"]
    assert abs(m["asc"] + 0.5) < 0.25 and m["asc_se"] and abs(m["price"]["beta"] + 0.015) < 0.002
    # the centre changes the constant only: same price coefficient, same part-worths, same likelihood
    sets0, coding0, _ = data(300, 1, prices=(900, 1000, 1100), price_beta=-0.015, center=0.0)
    beta, _, ll, converged, _ = choice_model.fit(sets, coding.k)
    beta0, _, ll0, converged0, _ = choice_model.fit(sets0, coding0.k)
    assert converged and converged0 and abs(ll - ll0) < 1e-6
    assert np.allclose(np.delete(beta, coding.asc), np.delete(beta0, coding.asc), atol=1e-5)
    assert abs(beta0[coding.asc] - (beta[coding.asc] + m["price"]["beta"] * 1000)) < 1e-4 and abs(beta0[coding.asc]) > 14


def test_separated_level_takes_errors_and_payments_from_its_whole_attribute():
    sets, coding, shown = data(200, 5, never=(0, 2))
    m = summarise(sets, coding, ATTRS, 200, shown)
    a0, a1 = m["attributes"][0], m["attributes"][1]
    assert a0["loose"] and all(lv["se"] is None and lv["wtp"] is None for lv in a0["levels"])
    assert not a1["loose"] and all(lv["se"] and lv["wtp"] is not None for lv in a1["levels"])
    assert m["asc_se"] is None and any("nur die Abstände zwischen den übrigen Stufen" in w for w in m["warnings"])
    # what the data do determine does not depend on the strength of the prior: the distance L0 - L1
    gaps = []
    for strength in (0.01, 0.1, 1.0):
        beta = choice_model.fit(sets, coding.k, ridge=coding.penalty() / choice_model.RIDGE * strength)[0]
        gaps.append((beta[0] - beta[1], beta[0]))
    assert max(g for g, _ in gaps) - min(g for g, _ in gaps) < 0.02
    assert max(b for _, b in gaps) - min(b for _, b in gaps) > 0.3   # the single values do


def test_a_level_that_was_never_shown_is_named():
    def profile(rng):
        return [int(rng.integers(3)), int(rng.integers(3)), int(rng.integers(2))]  # A1: L3 never

    sets, coding, shown = data(150, 2, profile=profile)
    m = summarise(sets, coding, ATTRS, 150, shown)
    text = " ".join(m["warnings"])
    assert "Nie gezeigt (A1: L3)" in text and "Kombinationen von Stufen" not in text and "Nie oder immer" not in text
    a1 = m["attributes"][1]
    assert a1["loose"] and a1["levels"][3]["unshown"] and all(lv["se"] is None for lv in a1["levels"])
    assert all(lv["se"] for lv in m["attributes"][0]["levels"]) and m["wtp_ok"]
    assert all(lv["wtp"] is None for lv in a1["levels"]) and m["attributes"][0]["levels"][0]["wtp"] is not None


def test_attributes_that_only_came_together_are_named():
    def profile(rng):
        first = int(rng.integers(2))
        return [first, int(rng.integers(4)), first]  # A2 always equals A0 (which never shows L2)

    levels = [2, 4, 2]
    rng = np.random.default_rng(3)
    coding = Coding(levels, None, asc=False)
    sets = []
    for p in range(120):
        for _ in range(8):
            profs = [profile(rng) for _ in range(3)]
            v = np.array([0.6 * (q[0] == 0) + TRUE[1][q[1]] for q in profs]) + rng.gumbel(size=3)
            sets.append(ChoiceSet([coding.row(q) for q in profs], int(np.argmax(v)), p))
    attrs = [{"code": f"A{a}", "label": f"A{a}", "level_labels": [f"L{lv}" for lv in range(n)]} for a, n in enumerate(levels)]
    m = summarise(sets, coding, attrs, 120, [])
    text = " ".join(m["warnings"])
    assert "Nicht bestimmbar (A0, A2)" in text and "liegen stets vorn" not in text
    assert all(lv["se"] is None for a in (0, 2) for lv in m["attributes"][a]["levels"])
    assert all(lv["se"] for lv in m["attributes"][1]["levels"])                     # A1 is estimated as usual


def test_none_never_chosen_is_named_and_leaves_the_attributes_alone():
    sets, coding, shown = data(60, 4, none=None)
    m = summarise(sets, coding, ATTRS, 60, shown, constant="„Keine davon“")
    text = " ".join(m["warnings"])
    assert "„Keine davon“ wurde nie gewählt" in text and "Kombinationen von Stufen" not in text
    assert m["asc_se"] is None and all(lv["se"] for a in m["attributes"] for lv in a["levels"])
    assert abs(m["attributes"][0]["levels"][0]["beta"] - 0.6) < 0.3


def test_robust_is_only_claimed_when_the_sandwich_was_used():
    sets, coding, shown = data(4, 6)                              # 4 persons, 7 parameters
    model = summarise(sets, coding, ATTRS, 4, shown)
    assert not model["robust"]
    assert any("Standardfehler sind modellbasiert" in w for w in model["warnings"])
    sets, coding, shown = data(40, 6)
    assert summarise(sets, coding, ATTRS, 40, shown)["robust"]


# --- MaxDiff ----------------------------------------------------------------------------------------------


def _maxdiff(n, seed, first_always_best=True, anchor=None):
    rng = np.random.default_rng(seed)
    versions = generate(8, 4, 8, 2, seed=1)
    true = np.array([9.0 if first_always_best else 1.0, 0.8, 0.5, 0.2, 0.0, -0.3, -0.6, -1.0])
    answers = []
    for p in range(n):
        v = p % len(versions)
        tasks = {}
        for t, s in enumerate(versions[v]):
            noise = rng.gumbel(size=len(s))
            b = 0 if first_always_best and 0 in s else s[int(np.argmax(true[s] + noise))]
            rest = [i for i in s if i != b]
            tasks[t] = (b, rest[int(np.argmax(-true[rest] + rng.gumbel(size=len(rest))))])
        answers.append((v, tasks))
    anchors = [anchor] * n if anchor is not None else []
    return versions, answers, anchors


def test_separated_statement_is_off_the_scale_and_the_others_do_not_move():
    versions, answers, anchors = _maxdiff(80, 1)
    labels = [f"S{i + 1}" for i in range(8)]
    m = mnl(8, versions, answers, anchors, labels, 4)
    top = m["rows"][0]
    assert top["n"] == 1 and top["side"] == "top" and top["score"] is None and top["lo"] is None and top["u_se"] is None
    rest = m["rows"][1:]
    assert abs(sum(r["score"] for r in rest) - 100) < 1e-6 and all(r["lo"] < r["score"] < r["hi"] for r in rest)
    assert abs(sum(r["u"] for r in rest)) < 1e-6 and all(r["u_se"] for r in rest)
    assert len(m["chart"]["rows"]) == 7 and "über bzw. unter allen anderen" in " ".join(m["warnings"])
    # the scores of the others do not depend on how strongly the separated statement is held back
    from app import maxdiff_analysis

    scores = []
    for strength in (0.01, 1.0):
        old, maxdiff_analysis.RIDGE = maxdiff_analysis.RIDGE, strength
        try:
            x = mnl(8, versions, answers, anchors, labels, 4)
        finally:
            maxdiff_analysis.RIDGE = old
        scores.append({r["n"]: r["score"] for r in x["rows"][1:]})
    assert max(abs(scores[0][n] - scores[1][n]) for n in scores[0]) < 0.3


def test_a_flat_anchor_gives_no_interval_and_no_clear_side():
    versions, answers, anchors = _maxdiff(60, 2, first_always_best=False, anchor=[1] * 8)
    m = mnl(8, versions, answers, anchors, [f"S{i + 1}" for i in range(8)], 4)
    text = " ".join(m["warnings"])
    assert "alle oder keine Aussagen angekreuzt" in text and "stets vor oder hinter" not in text
    assert m["threshold"]["lo"] is None and m["threshold"]["u_se"] is None
    assert all(r["above_sure"] is False for r in m["rows"])


def test_separation_leaves_the_separated_attribute_without_importance(monkeypatch):
    """Audit 2026-10-03, C-1: under separation every importance moved with the strength of the prior (they are shares
    of a sum holding the bounded range). Now the separated attribute has none, the others are shares among
    themselves and stay put when the prior changes."""
    sets, coding, shown = data(200, 5, never=(0, 2))
    shares = []
    for strength in (0.01, 0.1, 1.0):
        monkeypatch.setattr(choice_model, "RIDGE", strength)
        m = summarise(sets, coding, ATTRS, 200, shown)
        a0, a1, a2 = m["attributes"]
        assert a0["importance"] is None and a1["importance"] and a2["importance"]
        assert abs(a1["importance"] + a2["importance"] + m["price"]["importance"] - 1) < 1e-9
        assert any(w.startswith("Ohne Wichtigkeit: A0.") for w in m["warnings"])
        shares.append((a1["importance"], m["price"]["importance"]))
    assert max(s[0] for s in shares) - min(s[0] for s in shares) < 0.01
    assert max(s[1] for s in shares) - min(s[1] for s in shares) < 0.01


def test_price_coefficient_is_shown_per_round_unit():
    """Audit 2026-10-03, C-2: prices in the ten thousands gave "−0,0001 (SE 0,0000)"; the coefficient is now
    shown per a power of ten a tenth of the price range."""
    assert choice_model.price_unit([20000, 25000, 30000, 35000]) == 1000
    assert choice_model.price_unit([50, 100, 150]) == 10 and choice_model.price_unit([5, 9]) == 1
    assert choice_model.price_unit([10, 10]) == 1 and choice_model.price_unit([]) == 1
