"""Package I of the data audit of 2026-10-05 (audit-daten-und-berechnungen-2026-10-05.md, section 3; detailed report
ergebnisse/befunde.md, R-x there = B-x here): figures, quality control and the method section."""
from tests.instrument_permissions import grant_instrument
from datetime import datetime, timedelta

import numpy as np
from werkzeug.datastructures import MultiDict

from app import intervals, methods, results, scales
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add

# two statements running against each other, the second not reversed: r = -0.77 (audit script r07)
OPPOSED = [[1, 5], [2, 4], [3, 3], [4, 2], [5, 2], [1, 4], [2, 5], [3, 2], [4, 1], [5, 1], [2, 3], [4, 4]]


def _likert(app, client, rows, code="TA"):
    """A metric Likert item (2) with one statement per column of rows, one completed participation per row."""
    form = [("code", code), ("points", "5"), ("labeling", "full"), ("level", "metric")]
    for j in range(len(rows[0])):
        form += [("stmt_id", f"s{j + 1}"), ("stmt_code", ""), ("stmt_text", f"Aussage {j + 1}")]
    add(client, "likert")
    client.post("/surveys/1/editor/items/2", data=MultiDict(form))
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for i, row in enumerate(rows):
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300 + i),
                                        answers={"2": {f"s{j + 1}": v for j, v in enumerate(row)}}))
        db.session.commit()


# --- B-2: a negative Spearman-Brown coefficient is not interpretable -----------------------------------------


def test_negative_spearman_brown_gets_the_sentence_of_a_negative_alpha():
    sb, alpha = scales.spearman_brown(OPPOSED), results.cronbach_alpha(OPPOSED)
    assert sb < -1 and alpha < 0
    assert intervals.spearman_brown(OPPOSED) is None                      # no transformed interval for r <= 0
    assert methods._reliability(2, alpha, None, sb) == [
        "Spearman-Brown-Koeffizient negativ, nicht interpretierbar (bei zwei Items statt α)"]
    assert methods._reliability(2, 0.8, None, 0.81) == ["Spearman-Brown-Koeffizient = 0,81 (bei zwei Items statt α)"]
    facts = methods.facts_text({**_facts(), "constructs": [{"name": "X", "authors": None, "year": None, "items": 2,
                                                            "points": 5, "modified": False, "subscales": [], "alpha": alpha,
                                                            "omega": None, "sb": sb, "mean": 3.0, "sd": 1.0,
                                                            "citation": None}]})
    assert "Spearman-Brown coefficient negative (not interpretable)" in facts and "-6.8" not in facts


def test_negative_spearman_brown_on_the_results_page_and_in_the_word_table(app, client, survey):
    _likert(app, client, OPPOSED)
    html = client.get("/surveys/1/results").data.decode()
    assert "α und Spearman-Brown negativ: nicht interpretierbar" in html
    assert "Negatives α bzw. ein negativer Spearman-Brown-Koeffizient" in html
    import io

    from docx import Document

    data = client.get("/surveys/1/results/scales.docx?lang=de").data
    text = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    assert "Negatives α bzw. ein negativer Spearman-Brown-Koeffizient" in text


def _facts():
    return {"start": None, "end": None, "running": False, "days": None, "started": 12, "declined": 0, "screenout": 0,
            "incomplete": 0, "completed": 12, "median_min": 5.25, "speeder_pct": 30, "cutoff_min": None, "speeders": 0,
            "final": 12, "age": None, "gender": [], "constructs": []}


# --- B-5: omega from a small sample says so -------------------------------------------------------------------


def test_omega_from_a_small_sample_is_marked(app, client, survey):
    assert scales.omega_small(29, 3) and not scales.omega_small(30, 3)
    assert scales.omega_small(100, 44) and not scales.omega_small(132, 44) and not scales.omega_small(0, 3)
    rng = np.random.default_rng(3)
    f = rng.normal(size=12)
    rows = [[int(min(5, max(1, round(3 + x + rng.normal(scale=.7))))) for _ in range(3)] for x in f]
    _likert(app, client, rows)
    with app.app_context():
        scale = next(e for e in results.compute(db.session.get(Survey, 1))["items"] if e["item"].code == "TA")["scale"]
    assert scale["omega"] is not None and scale["omega_small"]
    html = client.get("/surveys/1/results").data.decode()
    assert "kleine Stichprobe (unter 30 vollständigen Fällen): ω kann instabil sein" in html
    assert methods._reliability(3, 0.8, 0.81, None, small=True)[-1] == "ω = 0,81 (kleine Stichprobe, Schätzung instabil)"


# --- B-13: half up also in the facts for the AI ----------------------------------------------------------------


def test_facts_for_the_ai_round_half_up():
    facts = methods.facts_text({**_facts(), "constructs": [{"name": "X", "authors": None, "year": None, "items": 4,
                                                            "points": 5, "modified": False, "subscales": [], "alpha": 0.845,
                                                            "omega": 0.125, "sb": None, "mean": 2.675, "sd": 1.0,
                                                            "citation": None}]})
    assert "alpha 0.85" in facts and "omega 0.13" in facts and "M = 2.68" in facts
    assert "Median completion time: 5.3 minutes" in facts


def test_effective_sample_size_for_the_ai_rounds_half_up(monkeypatch):
    from app import weights

    monkeypatch.setattr(weights, "setting", lambda survey: {"kind": "quotas"})
    text = weights.method_sentence_en(None, {"attributes": [{"name": "Alter"}], "n_eff": 82.5, "n": 100})
    assert "effective sample size 83 of 100" in text                       # round() gave 82


# --- B-4: Welch's ANOVA with a group without variance, exact Mann-Whitney --------------------------------------


def test_brown_forsythe_where_a_group_does_not_vary():
    from app import group_tests as gt

    groups = [[1, 2, 3, 4, 5], [3, 4, 5, 6, 7], [4, 4, 4, 4, 4]]      # audit script r06: Welch gave None
    r = gt.welch_anova(groups)
    g = [np.array(x, dtype=float) for x in groups]
    n, m, v = np.array([len(x) for x in g]), np.array([x.mean() for x in g]), np.array([x.var(ddof=1) for x in g])
    big = n.sum()
    c = (1 - n / big) * v
    f = (n * (m - np.concatenate(g).mean()) ** 2).sum() / c.sum()
    df2 = 1 / ((c / c.sum()) ** 2 / (n - 1)).sum()
    assert r["method"] == "brown_forsythe" and r["constant"] == [2]
    assert abs(r["f"] - f) < 1e-9 and r["df1"] == 2 and abs(r["df2"] - df2) < 1e-9
    assert abs(r["p"] - gt.f_sf(f, 2, df2)) < 1e-12
    assert gt.welch_anova([[4, 4, 4], [5, 5, 5], [6, 6, 6]]) is None   # nothing varies: no test
    assert gt.welch_anova([[1, 2, 3], [2, 3, 5], [4, 5, 9]])["method"] == "welch"


def test_compare_names_the_group_without_variance():
    from app import compare

    values = [1, 2, 3, 4, 5, 3, 4, 5, 6, 7, 4, 4, 4, 4, 4]
    member = ["a"] * 5 + ["b"] * 5 + ["c"] * 5
    block = compare.metric(values, member, [("a", "A"), ("b", "B"), ("c", "Gruppe C")])
    assert block["test"]["method"] == "brown_forsythe" and block["flat"] == ["Gruppe C"]
    assert ("Mittelwertvergleich, wenn eine Gruppe nicht streut: Brown-Forsythe-F*", [compare.BROWN_FORSYTHE]) in \
        compare.sources({"items": {1: [block]}})


def test_mann_whitney_is_exact_for_small_groups_without_ties():
    from itertools import combinations

    from app import group_tests as gt

    x, y = [1, 2, 4, 6, 9], [3, 5, 7, 8, 10]
    r = gt.mann_whitney(x, y)
    # brute force: every split of the ten ranks into 5 and 5
    us = [sum(1 for a in pick for b in set(range(10)) - set(pick) if a > b) for pick in combinations(range(10), 5)]
    low, high = sum(u <= r["u"] for u in us) / len(us), sum(u >= r["u"] for u in us) / len(us)
    assert r["exact"] and abs(r["p"] - min(1, 2 * min(low, high))) < 1e-12
    assert gt.mann_whitney([1, 1, 2, 3, 4], [2, 5, 6, 7, 8])["exact"]               # ties too (math audit I-5)
    assert gt.mann_whitney(list(range(10)), list(range(10, 20)))["exact"]  # without ties: exact while both under 50 (S-9)
    assert not gt.mann_whitney(list(range(50)), list(range(50, 60)))["exact"]
    assert not gt.mann_whitney([1, 1, 2, 3, 4] * 4, [2, 5, 6, 7, 8])["exact"]  # ties: exact only under 20 cases


# --- B-8: weighted median, the same weighted SD in the comparison ----------------------------------------------


def test_weighted_median_and_the_weighted_sd_of_the_comparison():
    from app import group_tests as gt
    from app import intervals, weights

    assert results.weighted_median([1.0, 2.0, 3.0, 4.0], [1, 1, 1, 1]) == 2.5          # = statistics.median
    assert results.weighted_median([1.0, 2.0, 10.0, None], [1, 1, 5, 9]) == 10.0
    num = results.numeric_stats([1.0, 2.0, 10.0], weights=[1.0, 1.0, 5.0])
    assert num["median"] == 10.0 and results.numeric_stats([1.0, 2.0, 10.0])["median"] == 2.0
    xs = [float(v) for v in range(12)]
    ws = [0.5, 1.5] * 6
    d = gt.describe(xs, ws)
    assert abs(d["sd"] - weights.sd(xs, ws)) < 1e-12 and d["n"] == 12
    assert d["ci"] == intervals.mean_ci(d["mean"], d["sd"], weights.n_eff(ws), se=weights.mean_se(xs, ws))  # math audit K-7, I-1
    page = results.numeric_stats(xs, weights=ws)
    assert (page["mean"], page["sd"], page["ci"]) == (d["mean"], d["sd"], d["ci"])


# --- B-9: marked participations and the weighted view ------------------------------------------------------------


def test_weighted_view_says_that_marked_ones_stay_out(app, client, survey):
    from tests.analysis.test_weights import W, quota_survey

    quota_survey(app, client)
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        s.responses.append(Response(status=Response.COMPLETED, source="real", answers={"2": "1", "3": "30"},
                                    started_at=t0, completed_at=t0 + timedelta(seconds=20)))   # a speeder
        db.session.commit()
        data = results.compute(s, weighted=True, drop_speeders=False)
        assert data["weighting"]["marked_out"] == 1 and data["weighting"]["left_out"] == 0
    html = client.get("/surveys/1/results?gewichtet=1&markierte=1").data.decode()
    assert "In der gewichteten Ansicht bleiben Markierte ausgeschlossen (1)" in html
    assert "bleiben Markierte ausgeschlossen" not in client.get("/surveys/1/results?gewichtet=1").data.decode()


# --- sources (B-6, B-7, package K: Z-5, Whisper) ---------------------------------------------------------------


def test_sources_name_the_t_interval_and_the_random_sample(app, client, survey):
    rng = np.random.default_rng(5)
    _likert(app, client, [[int(v) for v in rng.integers(1, 6, 3)] for _ in range(12)])
    html = client.get("/surveys/1/results").data.decode()
    assert "95-%-Konfidenzintervalle der Mittelwerte: t-Verteilung" in html and "Student. (1908)" in html
    assert "Voraussetzung der Konfidenzintervalle und p-Werte: eine Zufallsstichprobe" in html
    assert "AAPOR Task Force on Non-probability Sampling" in html
    from app import weights

    merged = results.with_sampling_note([("95-%-Konfidenzintervalle der Anteile: Wilson-Intervall", ["W"]),
                                         ("Gewichtung und Repräsentativität nicht zufälliger Stichproben", [weights.AAPOR])])
    assert len(merged) == 2 and merged[1][0].startswith("Voraussetzung der Konfidenzintervalle")  # AAPOR cited once
    assert results.with_sampling_note([("Gini-Koeffizient", ["G"])]) == [("Gini-Koeffizient", ["G"])]


def test_live_feedback_names_its_source(app):
    from types import SimpleNamespace

    from app.items import get

    band = get("score_band")
    item = SimpleNamespace(id=4, type="score_band", config={"basis": "percentile"}, text=lambda key, lang=None: "",
                           kind=band)
    band_result = {"basis": "percentile", "enough": True, "band": 0, "bands": [{"label": "unten", "text": ""}],
                   "segments": [100.0], "marker": 50.0, "shown": 50.0, "n": 8, "lo": 0, "hi": 100}
    original = band.result
    band.result = lambda *a, **k: band_result
    try:
        with app.test_request_context():
            html = app.jinja_env.get_template("items/score_band/participant.html").render(
                item=item, kind=band, lang="de", answers={}, variables={}, preview=False)
    finally:
        band.result = original
    # shown open since the pre-test of 2026-10-20: folded behind "Quelle" it looked like a missing source
    assert '<p class="p-source">Quelle: Crocker, L., &amp; Algina, J. (1986)' in html


def test_whisper_is_cited_under_its_draft(app, client):
    from tests.interview.test_interview_data import make, start, switch_on

    code, sid = make(app, client)
    switch_on(app)
    rid = start(app, client, code)
    with app.app_context():
        row = db.session.get(Response, rid)
        row.interview = {**(row.interview or {}), "transcript": {"state": "done", "model": "whisper-1", "text": "I: Hallo."}}
        db.session.commit()
    page = client.get(f"/guide/{code}/interviews/{rid}/transcript").data.decode()
    assert "Automatische Transkription: Whisper" in page and "PMLR 202, 28492–28518" in page


# --- B-10: least denominators of qc_straight and qc_missing -----------------------------------------------------


def _matrices(app, client, count):
    for j in range(count):
        add(client, "likert")
        client.post(f"/surveys/1/editor/items/{2 + j}", data=MultiDict(
            [("code", f"m{j}"), ("points", "5"), ("labeling", "full"), ("answer_mode", "optional")]
            + [p for k in range(1, 7) for p in (("stmt_id", f"s{k}"), ("stmt_code", ""), ("stmt_text", f"A{k}"))]))


def _flags(app, answers):
    from app import export, quality

    with app.app_context():
        s = db.session.get(Survey, 1)
        db.session.add(Response(survey_id=1, status=Response.COMPLETED, answers=answers))
        db.session.commit()
        ctx = quality.context(s, export._items(s))
        r = db.session.scalars(db.select(Response).filter_by(survey_id=1).order_by(Response.id.desc())).first()
        return quality.values(s, r, ctx, detail=True)


def test_one_matrix_clicked_through_is_no_flag_two_are(app, client, survey):
    _matrices(app, client, 2)
    one = _flags(app, {"2": {f"s{k}": 3 for k in range(1, 7)}})                  # the second matrix not answered
    assert one["qc_straight"] == 1.0 and not one["_notable"]["qc_straight"] and one["qc_flags"] == 0
    two = _flags(app, {"2": {f"s{k}": 3 for k in range(1, 7)}, "3": {f"s{k}": 4 for k in range(1, 7)}})
    assert two["qc_straight"] == 1.0 and two["_notable"]["qc_straight"] and two["qc_flags"] >= 1


def test_missing_share_counts_from_four_optional_questions(app, client, survey):
    from app import export, quality

    for j in range(4):
        add(client, "text_input")
        client.post(f"/surveys/1/editor/items/{2 + j}", data={"code": f"t{j}", "question": f"Frage {j}", "answer_mode": "optional"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        ctx = quality.context(s, export._items(s))
        items = [i for i in export._items(s) if i.type == "text_input"]

        def shown(n):
            return [(i, str(i.id)) for i in items[:n]]

        # three shown, two left out: 0.67 but no flag; four shown, two left out: 0.5 and a flag
        quality.shown, original = (lambda survey, response, ctx: shown(response.params["n"])), quality.shown
        try:
            three = quality.values(s, Response(survey_id=1, params={"n": 3}, layout={"pages": [1]},
                                               answers={str(items[0].id): "ja"}), ctx, detail=True)
            four = quality.values(s, Response(survey_id=1, params={"n": 4}, layout={"pages": [1]},
                                              answers={str(items[0].id): "ja", str(items[1].id): "ja"}), ctx, detail=True)
        finally:
            quality.shown = original
    assert three["qc_missing"] == round(2 / 3, 3) and not three["_notable"]["qc_missing"]
    assert four["qc_missing"] == 0.5 and four["_notable"]["qc_missing"]


# --- B-11: the median rule of the tempo hint per version of a page ------------------------------------------------


def test_tempo_median_rule_measures_against_the_same_version(app, client, survey):
    from app import tempo
    from tests.editor.test_tempo import setup

    app.config["TEMPO_MEDIAN_SECONDS"] = 0
    setup(client, tempo_options_shown="1", tempo_rule="median")
    with app.app_context():
        for seconds, version, n in ((40.0, None, tempo.MEDIAN_MIN), (10.0, "ab12cd34", tempo.MEDIAN_MIN)):
            for _ in range(n):
                db.session.add(Response(survey_id=1, source="real", paradata={
                    "_t": {"2": seconds}, **({"_v": {"2": version}} if version else {})}))
        db.session.commit()
        s = db.session.get(Survey, 1)
        found = tempo.medians(s)
        assert found["2|ab12cd34"] == (10.0, tempo.MEDIAN_MIN) and found["2|"] == (40.0, tempo.MEDIAN_MIN)
        assert tempo.median_limit(s, "2", "ab12cd34") == 5.0       # half of its own version, not of the whole page
        assert tempo.median_limit(s, "2") == 20.0
        assert tempo.median_limit(s, "2", "ffffffff") == round(found["2"][0] * tempo.MEDIAN_SHARE, 1)  # too few: the page


# --- B-12: the time line takes no negative durations ---------------------------------------------------------------


def test_time_line_median_leaves_out_negative_durations(app, client, survey):
    from app import timeline

    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = s.created_at + timedelta(days=1)
        for minutes in (10, 20, -600):
            s.responses.append(Response(source="real", status=Response.COMPLETED, started_at=t0,
                                        completed_at=t0 + timedelta(minutes=minutes)))
        db.session.commit()
        assert timeline.build(s, now=t0 + timedelta(days=2))["median_minutes"] == 15


# --- B-15: PSM consistency as the weak order ----------------------------------------------------------------------


def test_psm_takes_equal_neighbouring_prices_and_shows_the_share_left_out():
    from types import SimpleNamespace

    from app import psm_analysis

    assert psm_analysis.consistent({"tc": 5, "c": 10, "e": 10, "te": 20})           # "cheap" = "expensive" at 10 €
    assert not psm_analysis.consistent({"tc": 5, "c": 12, "e": 10, "te": 20})
    rows = [{"p_tc": 5, "p_c": 10, "p_e": 10, "p_te": 20}, {"p_tc": 4, "p_c": 8, "p_e": 12, "p_te": 15},
            {"p_tc": 6, "p_c": 9, "p_e": 14, "p_te": 18}, {"p_tc": 9, "p_c": 5, "p_e": 14, "p_te": 18}]
    item = SimpleNamespace(code="p", kind=SimpleNamespace(nms=lambda item: False))
    a = psm_analysis.analyse(rows, item)
    assert (a["n"], a["n_inconsistent"], a["share_inconsistent"]) == (3, 1, 0.25)


# --- B-16: the median of times of day on the clock ------------------------------------------------------------------


def test_median_of_times_over_midnight_is_circular():
    from types import SimpleNamespace

    from app.items import date_time, get

    kind = get("date_time")
    item = SimpleNamespace(code="t", config={"parts": "time"})
    over = kind.results(item, [{"t": "23:00"}, {"t": "01:00"}])
    assert over["median"] == "00:00" and over["over_midnight"] and (over["min"], over["max"]) == ("23:00", "01:00")
    day = kind.results(item, [{"t": v} for v in ("08:00", "09:30", "17:00")])
    assert day["median"] == "09:30" and not day["over_midnight"] and (day["min"], day["max"]) == ("08:00", "17:00")
    bed = kind.results(item, [{"t": v} for v in ("22:30", "23:30", "00:30", "01:00")])
    assert bed["median"] == "00:00" and not bed["wide"]
    assert date_time.clock_order(["00:00", "06:00", "12:00", "18:00"])[2]          # spread over the whole day


# --- B-17: the omega bootstrap on weighted covariances, Fisher scoring for long scales ---------------------------


def test_faster_omega_bootstrap_gives_the_same_interval():
    import time

    def old_way(x, draws):
        """The bootstrap before: rows copied, each draw fitted with the differenced Hessian."""
        a = np.asarray(x, dtype=float)
        start = scales._one_factor(np.cov(a, rowvar=False))[1]
        saved, scales.SCORING_K = scales.SCORING_K, 10 ** 6
        try:
            rng = np.random.default_rng(intervals.SEED)
            return intervals.percentile([scales.mcdonald_omega(a[rng.integers(0, len(a), len(a))], start)
                                         for _ in range(draws)], draws)
        finally:
            scales.SCORING_K = saved

    rng = np.random.default_rng(17)
    for n, k in ((150, 14), (80, 5)):
        z = rng.standard_normal(n)
        x = np.clip(np.round(3 + z[:, None] * rng.uniform(0.4, 1.0, k) + rng.standard_normal((n, k))), 1, 5).tolist()
        new, old = intervals.omega(x, draws=40), old_way(x, 40)
        assert abs(new[0] - old[0]) < 1e-6 and abs(new[1] - old[1]) < 1e-6
    z = rng.standard_normal(10000)
    big = np.clip(np.round(3 + z[:, None] * rng.uniform(0.4, 1.0, 44) + rng.standard_normal((10000, 44))), 1, 5).tolist()
    # faster than the old way on the same machine at the same moment, not under a fixed time (about 0.6 s against
    # 2.3 s when idle; in a full parallel suite on PostgreSQL 2.3 s against 3.0 s: the matrix work of the new way
    # suffers more from busy cores). The best of up to three rounds of each counts, as with timeit; when idle the
    # first round decides
    new_times, old_times = [], []
    for _ in range(3):
        began = time.process_time()
        assert intervals.omega(big, draws=20) is not None
        new_times.append(time.process_time() - began)
        began = time.process_time()
        old_way(big, 20)
        old_times.append(time.process_time() - began)
        if min(new_times) < min(old_times) / 2:
            break
    assert min(new_times) < min(old_times), (new_times, old_times)


# --- B-18: two elements of one construct in one survey are pooled once ------------------------------------------


def test_pool_takes_one_element_per_survey_and_construct(app, client):
    import random

    from app import construct_pool
    from app.models import Construct
    from tests.analysis.test_construct_pool import _answers, _kut_survey

    rng = random.Random(3)
    first, second = _kut_survey(app, client, "Eins"), _kut_survey(app, client, "Zwei")
    with app.app_context():
        block = db.session.get(Survey, first).blocks[-1].id
    grant_instrument(client.application, "kut", survey_id=first)
    client.post(f"/surveys/{first}/editor/blocks/{block}/items", data={"type": "construct:kut"})   # a second one
    with app.app_context():
        for sid in (first, second):
            for item in [i for b in db.session.get(Survey, sid).blocks for i in b.items if i.type == "likert"]:
                _answers(item, rng, 40)
            db.session.get(Survey, sid).status = Survey.CLOSED
        db.session.commit()
        report = construct_pool.report(db.session.scalars(db.select(Construct).filter(Construct.key == "kut")).one())
    rows = report["scales"][0]["rows"]
    assert len(rows) == 3 and [r["in_pool"] for r in rows].count(True) == 2
    assert [r["same_people"] for r in rows].count(True) == 1 and report["scales"][0]["alpha"]["k"] == 2
