"""Group comparisons on the results page (app/compare.py) and their tests (app/group_tests.py)."""
import re
import math
from datetime import datetime, timedelta

import numpy as np
import pytest
from werkzeug.datastructures import MultiDict

from app import compare
from app import group_tests as gt
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add


def close(a, b, tol=1e-6):
    return abs(a - b) < tol


# --- distributions against tabled values -----------------------------------------------------------------------

def test_distributions_match_tables():
    assert close(gt.chi2_sf(3.841459, 1), 0.05)            # chi-square tables: 95 % points
    assert close(gt.chi2_sf(5.991465, 2), 0.05)
    assert close(gt.chi2_sf(18.307038, 10), 0.05)
    assert close(gt.chi2_sf(6.634897, 1), 0.01)
    assert close(gt.chi2_sf(0.5, 3), 0.918891, 1e-5)
    assert gt.chi2_sf(0, 2) == 1.0
    assert close(gt.chi2_sf(2, 2), math.exp(-1))           # df 2: exactly exp(-x/2)
    assert close(gt.t_quantile(0.975, 10), 2.228139, 1e-5)  # t tables
    assert close(gt.t_quantile(0.975, 1), 12.706205, 1e-4)
    assert close(gt.t_sf2(2.228139, 10), 0.05)
    assert close(gt.f_sf(4.964603, 1, 10), 0.05)            # F tables
    assert close(gt.f_sf(3.098391, 3, 20), 0.05)
    assert close(gt.f_sf(4.0, 1, 10), gt.t_sf2(2.0, 10))     # F(1, df) = t(df)^2
    assert close(gt.norm_sf2(1.959964), 0.05)


# --- tests against hand computations and textbook examples ----------------------------------------------------------

def test_chi2_test_and_cramers_v():
    # 2 x 2: chi2 = N (ad - bc)^2 / (row and column totals) without continuity correction
    a, b, c, d = 20, 10, 5, 15
    r = gt.chi2_test([[a, b], [c, d]])
    n = a + b + c + d
    expected = n * (a * d - b * c) ** 2 / ((a + b) * (c + d) * (a + c) * (b + d))
    assert close(r["chi2"], expected) and r["df"] == 1 and r["n"] == 50
    assert close(r["v"], math.sqrt(expected / n)) and not r["cochran"]
    assert close(r["p"], gt.chi2_sf(expected, 1))
    # empty rows and columns are left out; small expected counts are flagged (Cochran)
    r = gt.chi2_test([[3, 1, 0], [0, 0, 0], [1, 4, 0]])
    assert r["df"] == 1 and r["cochran"]
    assert gt.chi2_test([[5, 5]]) is None and gt.chi2_test([[5, 5]], weights=[1]) is None


def test_mann_whitney_and_kruskal_wallis():
    # no overlap: U = 0, z = -2.165 (with continuity correction, audit 2026-10-12, S-9); 8 cases without ties:
    # the exact p 2 / 70 = 0.02857, not the normal approximation (audit 2026-10-05)
    r = gt.mann_whitney([1, 2, 3, 4], [5, 6, 7, 8])
    assert r["u"] == 0 and close(r["z"], -2.165064) and close(r["p"], 2 / 70) and r["r"] == -1 and r["exact"]
    # ties: the variance is corrected by sum(t^3 - t)
    x, y = [1, 1, 2, 3], [2, 3, 3, 4, 4]
    r = gt.mann_whitney(x, y)
    n1, n2, n = 4, 5, 9
    ties = 3 * (2 ** 3 - 2) + (3 ** 3 - 3)                # 1, 2 and 4 twice, 3 three times
    u = sum(1 if a > b else 0.5 if a == b else 0 for a in x for b in y)
    var = n1 * n2 / 12 * (n + 1 - ties / (n * (n - 1)))
    assert close(r["u"], u) and close(r["z"], (u - n1 * n2 / 2 + 0.5) / math.sqrt(var))  # continuity correction (S-9)
    assert close(r["r"], 2 * u / (n1 * n2) - 1)
    # Kruskal-Wallis: three groups without ties, H = 7.2, p = 0.0273 (chi-square, 2 df)
    r = gt.kruskal_wallis([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
    assert close(r["h"], 7.2) and r["df"] == 2 and close(r["p"], math.exp(-3.6)) and close(r["e2"], 7.2 / 8)
    # with ties the correction raises H
    plain = gt.kruskal_wallis([[1, 2, 2], [3, 3, 4], [5, 5, 6]])
    assert plain["h"] > 0 and plain["p"] < 0.1


def test_welch_t_and_delta_star_against_numpy():
    x = np.array([5.1, 4.8, 6.0, 5.5, 5.9, 4.2, 5.0])
    y = np.array([6.5, 7.1, 5.9, 6.8, 7.4, 6.0, 7.9, 6.6, 5.8])
    r = gt.welch_t(list(x), list(y))
    v1, v2 = x.var(ddof=1) / len(x), y.var(ddof=1) / len(y)
    t = (x.mean() - y.mean()) / np.sqrt(v1 + v2)
    df = (v1 + v2) ** 2 / (v1 ** 2 / (len(x) - 1) + v2 ** 2 / (len(y) - 1))
    assert close(r["t"], t) and close(r["df"], df)
    assert close(r["p"], gt.t_sf2(t, df))
    # delta* in the average SD with the variance of Bonett (2008) (math audit 2026-10-06, L-2)
    v1, v2, n1, n2 = x.var(ddof=1), y.var(ddof=1), len(x), len(y)
    s = (v1 + v2) / 2
    d = (x.mean() - y.mean()) / np.sqrt(s)
    se = np.sqrt(d ** 2 * (v1 ** 2 / (n1 - 1) + v2 ** 2 / (n2 - 1)) / (8 * s ** 2) + (v1 / (n1 - 1) + v2 / (n2 - 1)) / s)
    assert close(r["d_raw"], d) and close(r["d_ci"][0], d - 1.959964 * se, 1e-5) and close(r["d_ci"][1], d + 1.959964 * se, 1e-5)
    assert r["d"] < 0                                       # the first group has the lower mean
    # one textbook case, Welch's formula by hand for 1:5 against 2, 4, ..., 12: t = -2.3764, df = 6.9723, p = 0.04928
    r = gt.welch_t([1, 2, 3, 4, 5], [2, 4, 6, 8, 10, 12])
    assert close(r["t"], -2.3764, 1e-4) and close(r["df"], 6.9723, 1e-4) and close(r["p"], 0.04928, 1e-5)
    assert gt.welch_t([1, 1], [1, 1]) is None and gt.welch_t([1, 2], [3, 4], weights=[1, 1, 1, 1]) is None


def test_welch_anova_and_omega_squared_against_numpy():
    groups = [np.array([1, 2, 3, 4, 5.0]), np.array([2, 4, 6, 8, 10, 12.0]), np.array([3, 3, 4, 5.0])]
    r = gt.welch_anova([list(g) for g in groups])
    k = len(groups)
    n = np.array([len(g) for g in groups])
    m = np.array([g.mean() for g in groups])
    v = np.array([g.var(ddof=1) for g in groups])
    w = n / v
    mw = (w * m).sum() / w.sum()
    tmp = ((1 - w / w.sum()) ** 2 / (n - 1)).sum()
    f = ((w * (m - mw) ** 2).sum() / (k - 1)) / (1 + 2 * (k - 2) / (k ** 2 - 1) * tmp)
    assert close(r["f"], f) and r["df1"] == 2 and close(r["df2"], (k ** 2 - 1) / (3 * tmp))
    assert close(r["p"], gt.f_sf(f, 2, (k ** 2 - 1) / (3 * tmp)))
    allv = np.concatenate(groups)
    ssb = (n * (m - allv.mean()) ** 2).sum()
    ssw = ((n - 1) * v).sum()
    msw = ssw / (len(allv) - k)
    assert close(r["omega2"], max(0, (ssb - (k - 1) * msw) / (ssb + ssw + msw)))
    # equal means: omega squared is not negative
    assert gt.welch_anova([[1, 2, 3], [1, 2, 3], [1, 2, 3]])["omega2"] == 0


def test_weights_change_the_figures_not_the_tests():
    d = gt.describe([1, 2, 3], [1, 1, 2])
    assert close(d["mean"], 9 / 4) and d["ci"] is None
    plain = gt.describe([1, 2, 3])
    assert close(plain["sd"], 1) and plain["ci"] is None             # as on the page: an interval from 10 values on
    ten = gt.describe(list(range(10)))
    assert close(ten["ci"][0], 4.5 - gt.t_quantile(0.975, 9) * ten["sd"] / math.sqrt(10))
    assert gt.count(["a", "b", "a", None], [1, 2, 0.5, 9]) == {"a": 1.5, "b": 2}


# --- the results page ------------------------------------------------------------------------------------------------

def setup(app, client, groups):
    """A single choice question (item 2, three answers: the groups) and a metric Likert scale with two statements
    (item 3). groups: [(group code, s1, s2, seconds)] per participation."""
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "grp"), ("question", "Gruppe?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Ost"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "West"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Nord")]))
    add(client, "likert")
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "TA"), ("points", "5"), ("labeling", "full"), ("level", "metric"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for g, a, b, sec in groups:
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=sec),
                                        answers={"2": g, "3": {"s1": a, "s2": b}}))
        db.session.commit()


ROWS = ([("1", a, b, 300) for a, b in ((1, 2), (2, 2), (2, 3), (1, 1), (3, 2), (2, 1))]
        + [("2", a, b, 300) for a, b in ((4, 5), (5, 4), (4, 4), (3, 4), (5, 5), (4, 3))]
        + [("3", 3, 3, 300), ("3", 2, 3, 300)]       # two only: not shown on their own
        + [("1", 5, 5, 20)])                         # a speeder: marked, so left out of the comparison too


def test_choice_offered_and_comparison_shown(app, client, survey):
    setup(app, client, ROWS)
    html = client.get("/surveys/1/results").data.decode()
    assert "Nach Gruppen aufteilen" in html and '<option value="2" ' in html and "Nach Gruppen (" not in html
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    assert 'value="2" selected' in html
    assert "Nord (2, zu wenige für einen eigenen Ausweis)" in html and "Ost (6)" in html and "West (6)" in html
    assert "explorativ" in html and "nicht für mehrfaches Testen korrigiert" in html
    assert "Nach Gruppen: Gruppe?" in html and "Welch t(" in html and "δ*" in html
    assert 'id="scales-groups"' in html
    # the sources of what is shown, in the one fold of the page
    assert "Welch, B. L. (1947)" in html and "Bonett, D. G. (2008)" in html
    assert "Kruskal, W. H." not in html and "Welch, B. L. (1951)" not in html
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        f = {"sources": ["real"], "all": False, "drop": True, "speeder": 50}
        table = compare.table_of(s, f)
        data = results.compute(s, drop_speeders=True)
        cmp = compare.compute(s, compare.chosen(s, "2"), data, table)
        assert [(g["label"], g["n"], g["few"]) for g in cmp["groups"]] == [("Ost", 6, False), ("West", 6, False), ("Nord", 2, True)]
        s1 = next(b for b in cmp["items"][3] if b["code"] == "TA_1")
        assert s1["kind"] == "metric" and [c for c, _ in s1["groups"]] == ["1", "2"]
        assert close(s1["per"][0]["mean"], 11 / 6) and close(s1["per"][1]["mean"], 25 / 6)
        assert s1["test"]["t"] < 0 and s1["test"]["p"] < 0.01
        scale = cmp["scales"][0]
        assert scale["kind"] == "scale" and close(scale["per"][0]["mean"], (11 / 6 + 11 / 6) / 2)
        assert 2 not in cmp["items"]                  # the grouping question is not compared with itself


def test_nominal_and_ordinal_comparisons(app, client, survey):
    setup(app, client, ROWS)
    add(client, "single_choice")                     # 4: a nominal question with a residual answer
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "farbe"), ("question", "Farbe?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Rot"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Blau"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "weiß nicht")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for i, r in enumerate(s.responses):
            r.answers = {**r.answers, "4": "3" if i < 3 else ("2" if i % 2 else "1")}
        item = next(i for b in s.blocks for i in b.items if i.code == "TA")
        item.config = {**item.config, "level": "ordinal"}
        db.session.commit()
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    assert "χ²" in html and "Cramérs V" in html and "Mann-Whitney U" in html and "rangbiseriale Korrelation" in html
    assert "Cramér, H. (1946)" in html and "Mann, H. B., &amp; Whitney" in html
    # per answer one bar per group in the style of the whole sample; N of the test whole
    assert 'class="res-group-bars res-group-shares"' in html and "N = " in html
    assert not re.search(r"N = \d+\.0\)", html)
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        table = compare.table_of(s, {"sources": ["real"], "all": False, "drop": True, "speeder": 50})
        cmp = compare.compute(s, compare.chosen(s, "2"), results.compute(s, drop_speeders=True), table)
        (cross,) = cmp["items"][4]
        assert len(cross["rows"][0]["ci"]) == len(cross["groups"])   # one Wilson interval (or None) per group
        assert cross["kind"] == "nominal" and cross["rows"][-1]["label"] == "weiß nicht"   # residual answers last
        counts = [r["count"] for r in cross["rows"][:-1]]
        assert counts == sorted(counts, reverse=True)
        assert all(close(sum(r["shares"][j] for r in cross["rows"]), 1) for j in range(2))   # column percentages
        assert cross["test"]["cochran"]                     # 12 people in six cells
        ordinal = cmp["items"][3][0]
        assert ordinal["kind"] == "ordinal" and ordinal["test"]["r"] < 0 and ordinal["per"][0]["median"] == "2"


def test_comparison_kept_under_its_own_key(app, client, survey, monkeypatch):
    from app import results_store

    setup(app, client, ROWS)
    client.get("/surveys/1/results")                                      # the results are kept
    calls = []
    real = compare.compute
    monkeypatch.setattr(compare, "compute", lambda *a, **k: calls.append(1) or real(*a, **k))
    first = client.get("/surveys/1/results?gruppen=2").data.decode()
    again = client.get("/surveys/1/results?gruppen=2").data.decode()
    assert len(calls) == 1 and "Welch t(" in again and "Welch t(" in first
    with app.app_context():
        import os

        s = db.session.get(Survey, 1)
        assert os.path.exists(results_store._path(s, name="compare")) and os.path.exists(results_store._path(s))
    assert "Welch t(" not in client.get("/surveys/1/results").data.decode()   # without a grouping: as before
    assert "Nach Gruppen (" not in client.get("/surveys/1/results?gruppen=99").data.decode()   # unknown: ignored


def test_too_few_groups_no_comparison(app, client, survey):
    setup(app, client, [("1", 1, 2, 300)] * 6 + [("2", 3, 3, 300)] * 3)
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    assert "Weniger als zwei Gruppen haben mindestens 5 Personen: kein Vergleich." in html
    assert "Nach Gruppen (" not in html and "Welch, B. L." not in html


def test_wait_page_keeps_the_grouping(app, client, survey):
    setup(app, client, ROWS)
    app.config["RESULTS_WAIT_PAGE"] = True
    client.get("/surveys/1/results?go=1")                                 # the results are kept
    html = client.get("/surveys/1/results?gruppen=2").data.decode()       # the comparison is not yet: computed first
    assert "Die Ergebnisse werden berechnet" in html and '<input type="hidden" name="gruppen" value="2">' in html
    assert "Welch t(" in client.get("/surveys/1/results?gruppen=2&go=1").data.decode()
    assert "Die Ergebnisse werden berechnet" not in client.get("/surveys/1/results?gruppen=2").data.decode()


def test_diary_study_compares_persons_without_tests(app, client, survey):
    """Code review 2026-10-15, C4: in a diary study every entry counted as an independent case, so 30 entries of one
    person were 30 cases and the p values far too small. The comparison now weighs every person once (as the results
    page) and shows no test, with the reason."""
    setup(app, client, ROWS)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.sampling = {"modules": []}  # what makes a survey a diary study for the results
        db.session.commit()
        from app import results

        f = {"sources": ["real"], "all": False, "drop": True, "speeder": 50}
        table = compare.table_of(s, f)
        cmp = compare.compute(s, compare.chosen(s, "2"), results.compute(s, drop_speeders=True), table,
                              [1.0] * len(table), diary=True)
        assert cmp["diary"] and all(b["diary"] and not b.get("test") for bs in cmp["items"].values() for b in bs)
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    assert "Welch t(" not in html and "Tagebuchstudie: keine Tests" in html
    assert "In einer Tagebuchstudie sind die Einträge einer Person nicht unabhängig" in html


def test_overview_corrects_the_picked_option_after_holm():
    """Code review 2026-10-15, C11: the option of a multiple choice with the largest effect was shown with its own p,
    though it was picked from k options."""
    item = type("I", (), {"id": 7, "code": "mc"})()
    tests = [{"chi2": 9.0, "df": 1, "n": 100, "v": 0.3, "p": 0.01, "shape": (2, 2)},
             {"chi2": 1.0, "df": 1, "n": 100, "v": 0.1, "p": 0.3, "shape": (2, 2)},
             {"chi2": 0.5, "df": 1, "n": 100, "v": 0.07, "p": 0.5, "shape": (2, 2)},
             {"chi2": 0.1, "df": 1, "n": 100, "v": 0.03, "p": 0.8, "shape": (2, 2)},
             {"chi2": 0.0, "df": 1, "n": 100, "v": 0.0, "p": 0.99, "shape": (2, 2)}]
    block = {"kind": "multi", "groups": [("1", "A"), ("2", "B")], "code": "mc",
             "rows": [{"label": f"O{n}", "test": t} for n, t in enumerate(tests)]}
    out = compare.overview({"items": {7: [block]}}, [{"item": item, "question": "Q"}])
    row = out["rows"][0]
    assert row["option"]["label"] == "O0" and row["p"] == pytest.approx(0.05)
    assert compare._holm_of([0.04, 0.01, None], 0) == pytest.approx(0.08)
