"""Math audit 2026-10-06, package F: inference, texts and the rest.

L-2  beside Welch's t the standardised difference delta* in the average SD with the interval of Bonett (2008)
     instead of Hedges' g with the pooled SD, whose interval held 72 to 75 % with unequal spread and groups
L-6  weighted results page: counts and aggregate model of the choice models on the persons of HB
E-9  reading aid: 0.41 is a small to medium difference
W-3  reading aid "Logit oder HB": importances may differ, the part-worths keep direction and mostly ranking
W-6  segments: what "50 starts" means, and why below 100 persons no suggestion above 1 segment is possible
N-1  pooled reliability of two surveys without an interval (decision 6)
N-3  three authors as "et al." on the pages; Simmons et al. (2011) with its full entry
"""
import math
import random
import re

import numpy as np

from app import construct_pool
from app import group_tests as gt
from app.extensions import db
from app.intervals import Z
from app.models import Construct, Item, Survey


def bonett(x, y):
    """delta* = (m1 - m2) / sqrt((s1^2 + s2^2) / 2) with Bonett's (2008) variance, as luecken/l2_hedges_welch.py."""
    n1, n2 = len(x), len(y)
    v1, v2 = np.var(x, ddof=1), np.var(y, ddof=1)
    s = math.sqrt((v1 + v2) / 2)
    d = (np.mean(x) - np.mean(y)) / s
    var = d * d * (v1 ** 2 / (n1 - 1) + v2 ** 2 / (n2 - 1)) / (8 * s ** 4) + v1 / (s * s * (n1 - 1)) + v2 / (s * s * (n2 - 1))
    return d, d - Z * math.sqrt(var), d + Z * math.sqrt(var)


# --- L-2 -------------------------------------------------------------------------------------------------------------

def test_l2_welch_shows_delta_star_with_bonetts_interval():
    rng = np.random.default_rng(5)
    x, y = rng.normal(1.5, 3, 10), rng.normal(0, 1, 40)
    r = gt.welch_t(list(x), list(y))
    d, lo, hi = bonett(x, y)
    assert math.isclose(r["d_raw"], d, rel_tol=1e-9) and math.isclose(r["d_ci"][0], lo, rel_tol=1e-9)
    assert math.isclose(r["d_ci"][1], hi, rel_tol=1e-9) and "g" not in r


def test_l2_interval_holds_its_95_percent_when_the_small_group_spreads_more():
    """The case of the audit: n 10/40, SD 3/1 (Hedges' g held 0.747); delta* = 0.5 is the target in both orders."""
    rng = np.random.default_rng(1)
    diff = 0.5 * math.sqrt((3 ** 2 + 1) / 2)
    for n1, n2 in ((10, 40), (40, 10)):
        runs, hit = 2000, 0
        for _ in range(runs):
            r = gt.welch_t(list(rng.normal(diff, 3, n1)), list(rng.normal(0, 1, n2)))
            hit += r["d_ci"][0] <= 0.5 <= r["d_ci"][1]
        assert 0.93 < hit / runs < 0.97, (n1, n2, hit / runs)


def test_l2_comparison_page_names_delta_star_and_bonett(app, client, survey):
    from tests.analysis.test_compare import ROWS, setup

    setup(app, client, ROWS)
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    assert "Welch t(" in html and "δ*" in html
    assert "Mittel beider Varianzen" in html and "Hedges’ g =" not in html
    assert "Bonett, D. G. (2008)" in html and "Hedges, L. V. (1981)" in html   # J on the point value (audit 2026-10-10, B-25)


def test_l2_hypothesis_sentence_reports_delta_star(app, client, survey):
    from app import hypotheses
    from tests.analysis.test_study_plan import _plan, _questions

    _questions(client, app)
    _plan(client)
    with app.app_context():
        table = [{"gruppe": "1", "minuten": str(40 + i % 5)} for i in range(40)] + \
                [{"gruppe": "2", "minuten": str(20 + i % 5)} for i in range(40)]
        [h1, _] = hypotheses.evaluate(db.session.get(Survey, 1), table)
    assert "δ* = " in h1["sentence"] and "g = " not in h1["sentence"] and h1["effect"][0] == "δ*"


# --- E-9, W-3: reading aids ------------------------------------------------------------------------------------------

def test_e9_w3_reading_aids(app):
    from app import learning

    with app.test_request_context():
        t = learning.term("g")
        assert t["symbol"] == "δ*" and "kleiner bis mittlerer Unterschied" in t["example"]
        assert "Mittel beider Varianzen" in t["text"]
        text = learning.reason("hb_or_logit")["text"]
        assert "Wichtigkeiten können deutlich abweichen" in text and "Reihenfolge und Richtung sollten" not in text
    assert "a small to medium difference" in learning.TERMS_EN["g"][3]
    assert "importances can differ markedly" in learning.REASONS_EN["hb_or_logit"][1]


# --- L-6: the weighted page --------------------------------------------------------------------------------------

def test_l6_weighted_view_keeps_the_aggregate_model_on_the_persons_of_hb(app, client, survey, monkeypatch):
    from app import results
    from tests.analysis.test_cbc_results import setup as cbc_setup
    from tests.analysis.test_conjoint_hb import simulate_segments

    cbc_setup(client, price_attr=True, none=True, tasks=12, versions=6)
    simulate_segments(app, 40, none_asc=-0.5)

    def half(survey, table):
        ids = [r["resp_id"] for r in table]
        return {"weights": {i: 2.0 for i in ids[::2]}}

    monkeypatch.setattr(results, "_weighting", half)
    with app.app_context():
        s = db.session.get(Survey, 1)
        plain = next(e for e in results.compute(s, drop_speeders=True)["items"] if e["item"].id == 2)["cbc"]
        data = results.compute(s, drop_speeders=True, weighted=True)
        weighted = next(e for e in data["items"] if e["item"].id == 2)["cbc"]
    assert data["weighting"]["left_out"] == 20                  # half of the persons have no weight
    assert weighted["counts"]["persons"] == plain["counts"]["persons"] == 40   # was 20 in the weighted view
    assert weighted["model"]["price"]["beta"] == plain["model"]["price"]["beta"]


# --- W-6: segments ---------------------------------------------------------------------------------------------------

def test_w6_segments_explain_starts_and_the_smallest_segment(app, client, survey):
    from tests.analysis.test_segments import cbc_with_segments

    cbc_with_segments(app, client, n=80)
    assert client.post("/surveys/1/results/lc/2", data={"source": "real"}).status_code == 302
    with app.app_context():
        from app import segments

        res = segments.load(db.session.get(Item, 2))
        assert len(res["pids"]) == 80 and max(s["K"] for s in res["solutions"]) > 1 and res["suggested"] == 1
    html = client.get("/surveys/1/results").data.decode()
    assert "die besten 3 bis zur Konvergenz gerechnet; stabil, wenn mindestens 2 dieser 3 dasselbe Optimum finden" in html
    assert "Kein Vorschlag über 1 Segment möglich, weil jedes Segment mindestens 50 Personen braucht" in html


# --- N-1: pooled reliability of two surveys ---------------------------------------------------------------------

def test_n1_two_surveys_pool_without_an_interval():
    """The case of the audit: alpha 0.70 (n = 100) and 0.88 (n = 400), 6 statements: [-62.29; 0.999] before."""
    two = construct_pool.meta([(0.70, construct_pool.bonett_se(0.70, 100, 6)), (0.88, construct_pool.bonett_se(0.88, 400, 6))])
    assert two["k"] == 2 and 0.7 < two["value"] < 0.88 and two["ci"] is None
    three = construct_pool.meta([(a, construct_pool.bonett_se(a, n, 6)) for a, n in ((0.70, 100), (0.88, 400), (0.8, 200))])
    assert three["ci"] is not None and three["ci"][0] < three["value"] < three["ci"][1]


def test_n1_page_says_no_interval_with_two_surveys(app, client):
    from tests.analysis.test_construct_pool import _answers, _kut_survey

    rng = random.Random(7)
    ids = [_kut_survey(app, client, name) for name in ("Erste", "Zweite")]
    with app.app_context():
        for sid in ids:
            item = next(i for b in db.session.get(Survey, sid).blocks for i in b.items if i.type == "likert")
            _answers(item, rng, 40)
            db.session.get(Survey, sid).status = Survey.CLOSED
        db.session.commit()
        r = construct_pool.report(db.session.scalars(db.select(Construct).filter(Construct.key == "kut")).one())
    assert r["scales"][0]["alpha"]["k"] == 2 and r["scales"][0]["alpha"]["ci"] is None
    detail = client.get("/admin/constructs/kut").get_data(as_text=True)
    assert "ohne Intervall" in detail and "t(1) = 12,71" in detail


# --- N-3: short references ------------------------------------------------------------------------------------------

def test_n3_three_authors_as_et_al_on_the_pages():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    shown = ["app/experimental.py", "app/export.py", "app/methods.py", "app/vignettes.py",
             "app/templates/items/discounting/editor.html", "app/templates/items/iat/editor.html",
             "app/templates/items/psm/editor.html", "app/templates/samplesize/index.html", "docs/elementtypen.md"]
    long = re.compile(r"Greenwald, Nosek|Du, Green &|Myerson, Green &|MacCallum, Browne|Newton, Miller (&|und)|"
                      r"Conrad, Tourangeau|Kuhfeld, Tobias")
    for name in shown:
        lines = [ln for ln in (root / name).read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
        assert not [ln for ln in lines if long.search(ln)], name


def test_n3_simmons_in_the_references_of_the_power_progress(app, client, survey):
    from app import dummy, study_plan
    from tests.analysis.test_power import _power_plan, _questions

    _questions(client, app)
    _power_plan(client)
    with app.app_context():
        dummy.generate(db.session.get(Survey, 1), 20, seed=3)
        db.session.commit()
    part = client.get("/surveys/1/researchdesign/state").data.decode()
    assert "Simmons et al., 2011" in part and "Simmons, J. P., Nelson, L. D., &amp; Simonsohn, U. (2011)" in part
    assert "10.1177/0956797611417632" in study_plan.SIMMONS
