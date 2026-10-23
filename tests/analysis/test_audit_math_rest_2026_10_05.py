"""Mathe-Audit 2026-10-05, package I (other calculations): PSM ties (R-1), Rechenwert in decimal (R-2), class edges
on dashboards and live screens (R-3, R-4), half up on the live screen (R-5), shared quiz places (R-6), no "−0" (R-7)."""
import random

import pytest

W = [0, 10, 30, 50, 70]


def _case(tc, c, e, te, a, b):
    return {"tc": tc, "c": c, "e": e, "te": te, "nms_c": a, "nms_e": b}


# --- R-1: NMS with ties of the weak order -------------------------------------------------------------------------


def test_nms_ties_close_the_intervals_at_cheap_and_expensive():
    from app import psm_analysis as P

    assert P.probability(_case(10, 10, 20, 30, 5, 3), 10, W) == pytest.approx(0.7)    # TC = C: "cheap" at the price (was 0)
    assert P.probability(_case(10, 20, 30, 30, 5, 3), 30, W) == pytest.approx(0.3)    # E = TE: "expensive" (was 0)
    tie = _case(10, 20, 20, 30, 5, 1)                                                # C = E: "expensive" at and above
    assert [P.probability(tie, p, W) for p in (20, 20.0001)] == pytest.approx([0.0, 0.0], abs=1e-4)
    assert P.probability(_case(10, 20, 20, 30, 1, 5), 20, W) == pytest.approx(0.7)
    assert P.probability(_case(10, 20, 20, 30, 1, 5), 19.999, W) < 0.01
    assert P.probability(_case(5, 5, 5, 5, 5, 4), 5, W) == pytest.approx(0.5)        # all four at one price
    assert P.probability(_case(10, 20, 30, 40, 5, 3), 10, W) == 0.0 and P.probability(_case(10, 20, 30, 40, 5, 3), 40, W) == 0.0


def test_nms_revenue_maximum_is_the_models_value_at_its_price():
    from app import psm_analysis as P

    # the audit's smallest example: the app said 26,67 with index 10,67, the model there gives 7,56; the true top is 30
    good = [_case(10, 20, 20, 30, 5, 1), _case(10, 20, 30, 40, 3, 5)]
    r = P.nms(good, [10, 20, 30, 40], W)
    top = r["max_revenue"]
    assert top["price"] == pytest.approx(30) and top["value"] == pytest.approx(10.5) and top["trial"] == pytest.approx(0.35)
    rng = random.Random(7)
    for _ in range(400):                                                              # rounded prices: many ties
        cases = [_case(*sorted(rng.choice([10, 20, 30, 40, 50]) for _ in range(4)), rng.randint(1, 5), rng.randint(1, 5))
                 for _ in range(rng.randint(2, 30))]
        prices = sorted({c[k] for c in cases for k in P.KEYS})
        if len(prices) < 2:
            continue
        r = P.nms(cases, prices, W)
        if r.get("nobody"):
            continue
        m = r["max_revenue"]
        # at a jump the top can be the limit just below or above the price (audit 2026-10-12, W-3)
        at = m["price"] + {"below": -1e-9, "above": 1e-9}.get(m["side"], 0)
        model = sum(P.probability(c, at, W) for c in cases) / len(cases)
        assert m["trial"] == pytest.approx(model, abs=1e-6) and m["value"] == pytest.approx(m["price"] * model, abs=1e-6)


def test_nms_trial_of_numpy_is_probability_with_ties():
    import statistics

    from app import psm_analysis as P

    rng = random.Random(3)
    cases = [_case(*sorted(rng.choice([5, 10, 15, 20]) for _ in range(4)), rng.randint(1, 5), rng.randint(1, 5))
             for _ in range(60)]
    prices = sorted({c[k] for c in cases for k in P.KEYS})
    assert P.nms(cases, prices, W)["trial"] == [statistics.fmean(P.probability(c, p, W) for c in cases) for p in prices]


def test_nms_curve_draws_the_jump_with_two_points():
    from app import psm_analysis as P

    # TC = C = 10: 0,70 at 10 and on from there, nothing to add
    r = P.nms([_case(10, 10, 20, 30, 5, 3)] * 2, [10, 20, 30], W)
    assert r["curve"]["prices"] == [10, 20, 30] and r["curve"]["trial"] == pytest.approx([0.7, 0.3, 0.0])
    # at 20 the person with C = E comes from 0,70 below and is at 0 from 20 on: 0,50 (from the left), then 0,15
    r = P.nms([_case(10, 20, 20, 30, 5, 1), _case(10, 20, 30, 40, 3, 5)], [10, 20, 30, 40], W)
    assert r["curve"]["prices"] == [10, 20, 20, 30, 40]
    assert r["curve"]["trial"] == pytest.approx([0.0, 0.5, 0.15, 0.35, 0.0])
    assert r["trial"] == pytest.approx([0.0, 0.15, 0.35, 0.0])                          # the table: values at the prices
    assert r["curve"]["revenue"] == pytest.approx([p * t for p, t in zip(r["curve"]["prices"], r["curve"]["trial"])])


# --- R-2: Rechenwert in decimal --------------------------------------------------------------------------------------


@pytest.mark.parametrize("op,a,fixed,places,want", [("mul", 0.15, 3, 1, 0.5), ("mul", 0.35, 7, 1, 2.5), ("mul", 0.35, 3, 1, 1.1),
                                                    ("add", 0.35, 0.1, 1, 0.5), ("div", 1.005, 1, 2, 1.01),
                                                    ("pct", 1, 8, 1, 12.5), ("sub", 2.5, 0, 0, 3)])
def test_calc_rounds_half_up_what_the_numbers_give(app, client, survey, op, a, fixed, places, want):
    from app.extensions import db
    from app.models import Item
    from tests.analysis.test_calc import build

    build(app, client)
    with app.app_context():
        item = db.session.get(Item, 4)
        item.config = {**item.config, "op": op, "fixed": fixed, "places": places}
        value = item.kind.value(item, {2: a})
        assert value == want and type(value) is (int if not places else float)


def test_calc_chain_keeps_the_exact_decimal(app, client, survey):
    from decimal import Decimal

    from app.extensions import db
    from app.models import Item
    from tests.analysis.test_calc import build

    build(app, client)
    with app.app_context():
        year, rate = db.session.get(Item, 4), db.session.get(Item, 5)
        year.config = {**year.config, "op": "div", "fixed": 3, "places": 0}             # 2000 ÷ 3 reads 667 ...
        rate.config = {**rate.config, "a": "4", "b": None, "op": "mul", "fixed": 3, "places": 0}
        assert year.kind.value(year, {2: 2000}) == 667 and rate.kind.value(rate, {2: 2000}) == 2000   # ... times 3 is 2000
        assert isinstance(year.kind.value(year, {2: 2000}, exact=True), Decimal)
        year.config = {**year.config, "op": "mul", "fixed": 0.15}
        rate.config = {**rate.config, "places": 1}
        assert rate.kind.value(rate, {2: 1}) == 0.5                                     # 0,15 × 1 × 3 = 0,45 -> 0,5


# --- R-3, R-4: class edges --------------------------------------------------------------------------------------------


def test_class_of_puts_a_value_on_an_edge_into_the_upper_class():
    from app import units

    assert [units.class_of(k / 10, 0, 0.1) for k in range(11)] == list(range(11))
    assert [units.class_of(k / 10, 0, 0.1, 10) for k in range(11)] == list(range(10)) + [9]
    assert [units.class_of(round(-1 + k / 10, 1), -1, 0.2, 10) for k in range(21)] == [min(k // 2, 9) for k in range(21)]
    assert units.class_of(-5, 0, 1) == -5 and units.class_of(-5, 0, 1, 10) == 0


def test_dashboard_classes_count_values_on_the_edges_right():
    from app import boards

    xs = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.6, 0.6, 0.7, 0.8, 0.9, 1.0] * 2
    bins, places = boards._classes(xs)
    # the grid of the results page (step 0.1, Ralf 2026-10-06): 0.6 counts in the class from 0.6 on
    assert (0.5, 0.6, 2) in bins and (0.6, 0.7, 6) in bins                          # edge 0.6000000000000001 once took them
    assert all(a is None or b is None or a == round(a, 3) for a, b, _ in bins)
    assert sum(c for _, _, c in bins) == len(xs)


def test_live_classes_of_a_slider_and_their_labels():
    from app import live, units

    width = 1 / live.BINS
    assert [units.class_of(k / 10, 0, width, live.BINS) for k in range(11)] == list(range(10)) + [9]   # 0,3 in "0,3–0,4"
    assert live._classes(0, 0.1)[3] == "0,3–0,4"
    assert live._classes(-1, 0.2)[:2] == ["−1,0 – −0,8", "−0,8 – −0,6"]                  # minus sign, spaced dash
    assert live._classes(0, 1.5, "en")[1] == "1.5–3.0"


# --- R-5, R-6: the live screen and the quiz ----------------------------------------------------------------------------


def test_live_means_round_half_up():
    from app import live

    assert live._mean([2, 2.5]) == (2.3, 2)                                            # round() gave 2.2
    board = [(1, "A", 750, "rot"), (2, "B", 751, "rot"), (3, "C", 1000, "blau")]
    assert live.team_board(board) == [("blau", 1000, 1), ("rot", 751, 2)]               # 750,5 -> 751, round() gave 750


def test_quiz_places_are_shared_by_the_same_points():
    from app import live

    assert live.shared_places([1000, 1000, 750, 500, 500, 0]) == [1, 1, 3, 4, 4, 6]
    assert live.shared_places([]) == []


# --- R-7: no "−0" ---------------------------------------------------------------------------------------------------


def test_numbers_rounding_to_zero_carry_no_sign():
    from app import boards, units

    assert units.number(-0.4, 0) == "0" and units.number(-0.004, 2) == "0,00"
    assert units.number(-0.006, 2) == "−0,01" and units.number(0.004, 2, signed=True) == "0,00"
    assert units.fixed(-0.004, 2) == "0.00" and units.fixed(-0.004, 2, signed=True) == "0.00"
    assert units.fixed(0.35, 2, signed=True) == "+0.35" and units.fixed(-0.35, 2) == "-0.35"
    assert boards._signed(-0.004) == "0,00" and boards._signed(-0.35) == "−0,35"
