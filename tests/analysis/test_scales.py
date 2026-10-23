"""McDonald's omega, the overview of the scales with its Word table, and the scale scores in the export (app/scales.py)."""
import csv
import io
import math
import re
import zipfile
from datetime import datetime, timedelta

import numpy as np
from werkzeug.datastructures import MultiDict

from app import results, scales
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add


def test_omega_of_three_items_is_the_closed_form():
    # three statements: the one-factor model is just identified, the loadings follow from the covariances
    rng = np.random.default_rng(1)
    lam, psi = np.array([0.9, 0.6, 0.3]), np.array([0.3, 0.5, 0.8])
    z = rng.standard_normal(5000)
    x = z[:, None] * lam + rng.standard_normal((5000, 3)) * np.sqrt(psi)
    s = np.cov(x, rowvar=False)
    loads = [math.sqrt(s[0, 1] * s[0, 2] / s[1, 2]), math.sqrt(s[0, 1] * s[1, 2] / s[0, 2]),
             math.sqrt(s[0, 2] * s[1, 2] / s[0, 1])]
    unique = sum(s[i, i] - loads[i] ** 2 for i in range(3))
    assert abs(scales.mcdonald_omega(x.tolist()) - sum(loads) ** 2 / (sum(loads) ** 2 + unique)) < 1e-6


def test_omega_equals_alpha_when_every_item_measures_alike_and_exceeds_it_otherwise():
    rng = np.random.default_rng(2)
    z = rng.standard_normal(4000)
    same = z[:, None] * 0.7 + rng.standard_normal((4000, 5)) * 0.7          # tau-equivalent
    assert abs(scales.mcdonald_omega(same.tolist()) - results.cronbach_alpha(same.tolist())) < 1e-3
    mixed = z[:, None] * np.array([0.9, 0.8, 0.3, 0.2, 0.9]) + rng.standard_normal((4000, 5)) * 0.6
    assert scales.mcdonald_omega(mixed.tolist()) > results.cronbach_alpha(mixed.tolist()) + 0.01


def test_omega_needs_three_varying_items_and_enough_cases():
    assert scales.mcdonald_omega([[1, 2], [2, 3], [3, 3], [4, 5], [5, 5]]) is None         # two items
    assert scales.mcdonald_omega([[1, 2, 3], [2, 2, 4]]) is None                            # too few cases
    assert scales.mcdonald_omega([[1, 2, 3], [2, 2, 4], [3, 2, 3], [4, 2, 5], [5, 2, 4]]) is None  # no variance


# the worst case of the audit of 2026-10-03 (S-1): 26 people, three items; EM stopped after 20 000 steps at 0.441
WORST_K3 = [[3, 3, 3], [3, 3, 1], [3, 1, 4], [4, 2, 3], [3, 3, 4], [2, 4, 4], [2, 5, 3], [1, 2, 3], [3, 2, 3],
            [4, 3, 1], [3, 4, 2], [3, 2, 3], [3, 2, 3], [2, 4, 3], [3, 2, 4], [3, 3, 1], [3, 2, 2], [4, 4, 3],
            [4, 2, 3], [3, 5, 3], [4, 3, 3], [4, 3, 5], [4, 4, 4], [3, 3, 3], [3, 1, 3], [1, 1, 2]]


def _with_covariance(sigma, n=500, seed=0):
    """n rows whose sample covariance (n - 1) is exactly sigma: the one-factor fit then knows the true loadings."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, len(sigma)))
    x -= x.mean(axis=0)
    x = x @ np.linalg.inv(np.linalg.cholesky(np.cov(x, rowvar=False))).T
    return (x @ np.linalg.cholesky(sigma).T + 3).tolist()


def test_omega_of_three_items_does_not_stop_halfway():
    assert round(scales.mcdonald_omega(WORST_K3), 3) == 0.204


def test_omega_finds_known_loadings_for_three_and_six_items():
    for lam, psi in ((np.array([0.8, 0.6, 0.4]), np.array([0.4, 0.6, 0.8])),
                     (np.array([0.9, 0.7, 0.5, 0.4, 0.3, -0.2]), np.array([0.3, 0.5, 0.6, 0.7, 0.8, 0.9]))):
        sigma = np.outer(lam, lam) + np.diag(psi)
        true = lam.sum() ** 2 / (lam.sum() ** 2 + psi.sum())
        assert abs(scales.mcdonald_omega(_with_covariance(sigma)) - true) < 1e-6


def test_omega_on_the_boundary_and_not_estimable():
    # Heywood case: r12 = r13 = .8, r23 = .4 would need lambda_1^2 = 1.6 > 1; the maximum likelihood has psi_1 = 0,
    # loadings (1, .8, .8): omega = 2.6^2 / (2.6^2 + .72)
    r = np.array([[1, 0.8, 0.8], [0.8, 1, 0.4], [0.8, 0.4, 1]])
    assert abs(scales.mcdonald_omega(_with_covariance(r)) - 2.6 ** 2 / (2.6 ** 2 + 0.72)) < 1e-9
    # the same pattern inside four items: still the boundary solution, found by the general fit
    r4 = np.array([[1, 0.8, 0.8, 0.8], [0.8, 1, 0.4, 0.64], [0.8, 0.4, 1, 0.64], [0.8, 0.64, 0.64, 1]])
    lam, psi = np.array([1, 0.8, 0.8, 0.8]), np.array([0, 0.36, 0.36, 0.36])
    true = lam.sum() ** 2 / (lam.sum() ** 2 + psi.sum())
    assert abs(scales.mcdonald_omega(_with_covariance(r4)) - true) < 1e-6
    # three items with signs no single factor produces (two positive covariances, one negative): not estimable
    assert scales.mcdonald_omega(_with_covariance(np.array([[1, 0.3, 0.3], [0.3, 1, -0.2], [0.3, -0.2, 1]]))) is None


def test_omega_is_quick_for_many_items():
    import time
    rng = np.random.default_rng(4)
    z = rng.standard_normal(80)
    x = np.clip(np.round(z[:, None] * rng.uniform(0.2, 0.9, 20) + rng.standard_normal((80, 20)) + 3), 1, 5)
    start = time.process_time()
    assert 0 < scales.mcdonald_omega(x.tolist()) < 1
    assert time.process_time() - start < 1


def test_pearson_with_its_p_value():
    r, n, p = scales.pearson([1, 2, 3, 4, 5, None], [2, 1, 4, 3, 5, 9])
    assert n == 5 and abs(r - 0.8) < 1e-9
    assert abs(scales._betainc(5, 0.5, 10 / 14) - 0.0734) < 1e-4                           # t = 2, df = 10
    assert abs(p - 0.1041) < 1e-3                                                           # r = .8, n = 5
    assert scales.pearson([1, 2], [1, 2]) == (None, 2, None)


def setup(app, client):
    """A scale of three statements (s3 reversed) and a second one of three; six people, one of them a speeder."""
    add(client, "likert")                                                                   # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "TA"), ("points", "5"), ("labeling", "full"), ("level", "metric"), ("question", "Technik"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"), ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B"),
        ("stmt_id", "s3"), ("stmt_code", ""), ("stmt_text", "C"), ("stmt_reverse", "s3")]))
    add(client, "likert")                                                                   # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "VT"), ("points", "5"), ("labeling", "full"), ("level", "metric"), ("question", "Vertrauen"),
        ("stmt_id", "v1"), ("stmt_code", ""), ("stmt_text", "A"), ("stmt_id", "v2"), ("stmt_code", ""), ("stmt_text", "B"),
        ("stmt_id", "v3"), ("stmt_code", ""), ("stmt_text", "C")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        people = [((1, 2, 5), (2, 1, 2), 300), ((2, 2, 4), (2, 3, 2), 320), ((3, 4, 3), (3, 3, 4), 310),
                  ((4, 4, 2), (5, 4, 4), 330), ((5, 4, 1), (4, 5, 5), 305), ((1, 5, 1), (1, 1, 1), 20)]
        for ta, vt, sec in people:
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=sec),
                                        answers={"2": dict(zip(("s1", "s2", "s3"), ta)), "3": dict(zip(("v1", "v2", "v3"), vt))}))
        db.session.commit()


def test_results_show_omega_and_the_overview(app, client, survey):
    setup(app, client)
    html = client.get("/surveys/1/results").data.decode()
    assert "McDonalds ω = " in html and 'id="scales"' in html and "Skalen im Überblick" in html
    assert "Hayes, A. F., &amp; Coutts, J. J. (2020)" in html and "McDonald, R. P. (1999)" in html   # the sources, folded
    with app.app_context():
        r = results.compute(db.session.get(Survey, 1), drop_speeders=True)                  # speeder left out
        ov = r["overview"]
        assert [row["code"] for row in ov["rows"]] == ["TA", "VT"] and ov["rows"][0]["n"] == 5
        ta = next(e for e in r["items"] if e["item"].code == "TA")["scale"]
        assert ta["omega"] == ov["rows"][0]["omega"] and ta["omega"] is not None
        # TA scores (s3 recoded: 6 - raw): 2.33, 2.67, 3.67, 4.33, 5.0; VT: 1.67, 2.33, 3.33, 4.33, 4.67
        r_ta_vt, n, p = ov["corr"][1][0]
        assert n == 5 and r_ta_vt > 0.95 and p < 0.05


def test_word_table_in_both_languages(app, client, survey):
    setup(app, client)
    for lang, title, number in (("de", "Deskriptive Statistiken", ","), ("en", "Descriptive Statistics", ".")):
        resp = client.get(f"/surveys/1/results/scales.docx?lang={lang}")
        assert resp.status_code == 200 and resp.headers["Content-Type"].endswith("wordprocessingml.document")
        xml = zipfile.ZipFile(io.BytesIO(resp.data)).read("word/document.xml").decode()
        assert title in xml and "1. Technik" in xml and "2. Vertrauen" in xml and "N = 5" in xml
        assert f">3{number}07<" in xml                                                     # M of TA
    assert re.search(r">\.9\d\*{1,2}<", xml)                                              # r without the zero (English)


def test_omega_not_estimable_is_named_on_the_page_and_in_the_table(app, client, survey, monkeypatch):
    setup(app, client)
    monkeypatch.setattr(scales, "mcdonald_omega", lambda matrix: None)
    html = client.get("/surveys/1/results").data.decode()
    assert "McDonalds ω = –</strong> <span class=\"hint\">nicht schätzbar" in html
    xml = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/results/scales.docx?lang=de").data)).read(
        "word/document.xml").decode()
    assert "nicht schätzbar" in xml


def _csv(client, url):
    text = client.get(url).data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def test_scale_scores_only_on_request(app, client, survey):
    setup(app, client)
    plain = _csv(client, "/surveys/1/export/data.csv")
    assert "TA_m" not in plain[0]
    rows = _csv(client, "/surveys/1/export/data.csv?scales=1")
    cols = list(rows[0])
    assert cols.index("TA_m") == cols.index("TA_3") + 1 and "VT_m" in cols
    assert float(rows[0]["TA_m"]) == round((1 + 2 + 1) / 3, 4)                         # s3 = 5 recoded to 1
    book = _csv(client, "/surveys/1/export/codebook.csv?scales=1")
    entry = next(r for r in book if r["variable"] == "TA_m")
    assert entry["type"] == "Skalenwert" and "TA_1, TA_2, TA_3" in entry["notes"]
    html = client.get("/surveys/1/data").data.decode()
    assert 'name="scales" value="1" id="export-scales" form="export-form"' in html
    sav = client.get("/surveys/1/export/data.sav?scales=1")
    assert sav.status_code == 200 and b"TA_m" in sav.data


def test_missing_answer_leaves_the_score_empty_and_subscales_get_their_own(app, client, survey):
    setup(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                    answers={"2": {"s1": 3, "s2": 3}, "3": {"v1": 1, "v2": 1, "v3": 1}}))
        db.session.commit()
        from app.models import Item

        item = db.session.get(Item, 3)
        item.config = {**item.config, "statements": [{**st, "subscale": "Kern" if st["id"] != "v3" else "Rand"}
                                                     for st in item.config["statements"]]}
        db.session.commit()
    rows = _csv(client, "/surveys/1/export/data.csv?scales=1")
    assert rows[-1]["TA_m"] == "" and float(rows[-1]["VT_kern_m"]) == 1 and "VT_m" not in rows[-1]
    assert "VT_rand_m" in rows[-1]


def test_two_items_report_spearman_brown_and_a_negative_alpha_is_named(app, client, survey):
    """Mathe-Audit S-8/X-3, S-9 and S-11: a subscale of two statements shows the Spearman-Brown coefficient next to
    alpha (page, Word table, method section); a negative alpha says it is not interpretable; the note on recoding
    stands wherever a statement is reversed, also in an own question."""
    setup(app, client)
    html = client.get("/surveys/1/results").data.decode()
    assert "umgepolte Aussagen sind rekodiert" in html                       # own Likert question with s3 reversed
    with app.app_context():
        from app.models import Item

        item = db.session.get(Item, 3)
        item.config = {**item.config, "statements": [{**st, "subscale": "Kern" if st["id"] != "v3" else "Rand"}
                                                     for st in item.config["statements"]]}
        ta = db.session.get(Item, 2)
        ta.config = {**ta.config, "statements": [{**st, "reverse": False} for st in ta.config["statements"]]}
        db.session.commit()
        r = results.compute(db.session.get(Survey, 1), drop_speeders=True)
        kern = next(e for e in r["items"] if e["item"].code == "VT")["subscales"][0]
        # VT_1, VT_2 of the five kept: (2,1), (2,3), (3,3), (5,4), (4,5); r = .742 -> 2r / (1 + r) = .852
        v1, v2 = [2, 2, 3, 5, 4], [1, 3, 3, 4, 5]
        rr = np.corrcoef(v1, v2)[0, 1]
        assert kern["k"] == 2 and abs(kern["sb"] - 2 * rr / (1 + rr)) < 1e-12
        alpha = next(e for e in r["items"] if e["item"].code == "TA")["scale"]["alpha"]
        assert alpha < 0                                                    # s3 runs against s1 and s2, not recoded
    html = client.get("/surveys/1/results").data.decode()
    assert "Spearman-Brown = " in html and "α negativ: nicht interpretierbar" in html
    assert "Eisinga, R., te Grotenhuis, M., &amp; Pelzer, B. (2013)" in html
    assert "Negatives α (Technik) ist nicht interpretierbar" in html and "Spearman-Brown-Koeffizient: Vertrauen · Kern" in html
    xml = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/results/scales.docx?lang=de").data)).read(
        "word/document.xml").decode()
    assert "Spearman-Brown-Koeffizient: Vertrauen: Kern" in xml and "Negatives α (Technik)" in xml
    # the note under alpha and omega of a partial scale names both
    source = open(app.root_path + "/templates/surveys/results.html", encoding="utf-8").read()
    assert '; Cronbachs α", "' in source and '{% if s.k > 2 %}{{ de_en(" und McDonalds ω"' in source


def test_alpha_in_a_loop_is_marked_as_pooled_over_passes(app, client, survey):
    """Mathe-Audit S-7: in a loop every pass of a person is a case; alpha and omega say so and cite Geldhof et al."""
    from app import loops
    from app.models import Block
    from tests.editor.test_loops import setup as loop_setup

    loop_setup(client)
    add(client, "likert")                                                   # 3, in the loop block
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "LK"), ("points", "5"), ("labeling", "full"), ("level", "metric"), ("question", "Gefallen"),
        ("stmt_id", "a"), ("stmt_code", ""), ("stmt_text", "A"), ("stmt_id", "b"), ("stmt_code", ""), ("stmt_text", "B"),
        ("stmt_id", "c"), ("stmt_code", ""), ("stmt_text", "C")]))
    with app.app_context():
        rows = [r["id"] for r in loops.config(db.session.get(Block, 2))["rows"]]
        s = db.session.get(Survey, 1)
        for n in range(6):
            s.responses.append(Response(source="real", status=Response.COMPLETED, completed_at=datetime(2026, 9, 1),
                                        answers={f"3#{row}": {"a": (n + i) % 5 + 1, "b": (n + i) % 5 + 1, "c": (n * i) % 5 + 1}
                                                 for i, row in enumerate(rows)}))
        db.session.commit()
        entry = next(e for e in results.compute(s)["items"] if e["item"].code == "LK")
        assert entry["passes"] and entry["scale"]["pooled"] is True
    html = client.get("/surveys/1/results").data.decode()
    assert "gepoolt über die Durchgänge: Durchgänge derselben Person sind nicht unabhängig" in html
    assert "Geldhof, G. J., Preacher, K. J., &amp; Zyphur, M. J. (2014)" in html


def test_subscales_leave_out_attention_checks_and_carried_statements(app, client, survey):
    """Mathe-Audit S-12: a check row or a carried statement with a subscale (possible from a survey file) is no part
    of the subscale's alpha, omega and score."""
    setup(app, client)
    with app.app_context():
        from app.models import Item

        item = db.session.get(Item, 3)
        sts = item.config["statements"]
        item.config = {**item.config, "statements": [{**sts[0], "subscale": "K"}, {**sts[1], "subscale": "K"},
                                                     {**sts[2], "subscale": "K", "check": 3}]}
        db.session.commit()
        assert scales.groups(item) == [("K", ["VT_1", "VT_2"])]


def test_intervals_and_item_analysis_on_the_page_and_in_the_word_table(app, client, survey):
    """Concept statistik-erweiterungen, stage 1: alpha with its Feldt interval, omega with a bootstrap interval, the
    scale mean with a t interval, a folded item analysis per scale, the intervals of r in the overview and the same in
    the Word table, with a second table of item statistics."""
    from app import dummy, manuscript_tables

    setup(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        dummy.generate(s, 40, seed=3)
        db.session.commit()
        r = results.compute(s, sources=("dummy",))
        ta = next(e for e in r["items"] if e["item"].code == "TA")["scale"]
        assert ta["n"] == 40 and ta["alpha_ci"][0] < ta["alpha"] < ta["alpha_ci"][1]
        assert ta["mean_ci"][0] < ta["mean"] < ta["mean_ci"][1]
        stats = ta["item_stats"]
        assert [row["code"] for row in stats] == ["TA_1", "TA_2", "TA_3"] and [row["reverse"] for row in stats] == [False, False, True]
        assert stats[0]["text"] == "A" and stats[2]["text"] == "C"                    # without "(rekodiert)"
        assert all(row["low_rit"] == (row["rit"] is not None and row["rit"] < 0.30) for row in stats)
        assert all(row["high_without"] == (row["alpha_without"] > ta["alpha"]) for row in stats)
        ov = r["overview"]
        assert ov["rows"][0]["alpha_ci"] == ta["alpha_ci"] and ov["rows"][0]["item_stats"] == stats
        assert len(ov["corr_ci"]) == 2 and ov["corr_ci"][1][0] is not None
        xml = zipfile.ZipFile(io.BytesIO(manuscript_tables.build(s, r, "de"))).read("word/document.xml").decode()
        assert "Tabelle 2" in xml and "Itemkennwerte der Skalen" in xml and "TA_3 C (umgepolt)" in xml
        assert re.search(r">−?\d,\d\d \[−?\d,\d\d; −?\d,\d\d\]<", xml)                   # alpha [lo; hi]
        assert "95-%-Konfidenzintervalle (α nach Feldt et al., 1987" in xml
        en = zipfile.ZipFile(io.BytesIO(manuscript_tables.build(s, r, "en"))).read("word/document.xml").decode()
        assert "Table 2" in en and "Item Statistics of the Scales" in en and re.search(r"\[−?\.\d\d, −?\.\d\d\]", en)
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert re.search(r"Cronbachs α = −?\d,\d\d</strong> <span class=\"res-ci\"[^>]*>\[−?\d,\d\d; −?\d,\d\d\]", html)  # minus sign (V70)
    assert "<summary>Itemanalyse</summary>" in html and "(umgepolt)" in html and "r<sub>it</sub>" in html
    assert "Feldt, L. S., Woodruff, D. J., &amp; Salih, F. A. (1987)" in html
    assert "Lienert, G. A., &amp; Raatz, U. (1998)" in html and "Festlegung von Askurious" in html
    # fewer than 10 cases: brackets without numbers (the real ones only: a draft would pool its dummies)
    html = client.get("/surveys/1/results?source=real").data.decode()
    assert '[–]</span>' in html


def test_word_table_gives_the_n_of_m_and_sd_where_items_were_shown_partly():
    """Code review 2026-10-15, C10: with statements shown only to some, M and SD rest on more participants than the
    complete cases of n; the table wrote one n for both. The n of M and SD now stands in brackets."""
    from app import manuscript_tables

    row = {"label": "Vertrauen", "sub": None, "k": 3, "n": 12, "n_mean": 20, "partial": True, "mean": 3.1, "sd": 0.8,
           "alpha": 0.8, "omega": 0.81}
    data = {"n_rows": 20, "overview": {"rows": [row], "corr": [[]], "corr_ci": [[]]}}
    xml = zipfile.ZipFile(io.BytesIO(manuscript_tables.build(None, data, "de"))).read("word/document.xml").decode()
    assert ">12 (20)<" in xml and "ihr n steht in Klammern" in xml
    full = {**row, "partial": False, "n_mean": None}
    xml = zipfile.ZipFile(io.BytesIO(manuscript_tables.build(None, {**data, "overview": {"rows": [full], "corr": [[]]}},
                                                             "de"))).read("word/document.xml").decode()
    assert ">12<" in xml and "(20)" not in xml


def test_methods_section_calls_a_negative_spearman_brown_not_interpretable(app, client, survey, monkeypatch):
    """Audit 2026-10-21, E4: two items correlating negatively give 2r/(1+r) below -1; the methods section said
    "Spearman-Brown = -18,00" while it already caught a negative alpha."""
    from app import methods

    assert scales.spearman_brown([[1, 5], [2, 4], [3, 3], [4, 1], [5, 2]]) < -1
    real = methods.facts

    def facts(*args, **kwargs):
        f = real(*args, **kwargs)
        f["constructs"] = [{"name": "Zwei", "items": 2, "alpha": None, "omega": None, "sb": -18.0, "mean": None,
                            "sd": None, "subscales": []},
                           {"name": "Mit Subskalen", "items": 4, "alpha": None, "omega": None, "sb": None, "mean": None,
                            "sd": None, "subscales": [{"name": "A", "k": 2, "alpha": None, "omega": None, "sb": -3.0,
                                                       "mean": None, "sd": None},
                                                      {"name": "B", "k": 2, "alpha": None, "omega": None, "sb": 0.81,
                                                       "mean": None, "sd": None}]}]
        return f

    monkeypatch.setattr(methods, "facts", facts)
    page = client.get("/surveys/1/methods").data.decode()
    assert page.count("Spearman-Brown negativ (nicht interpretierbar)") == 2
    assert "−18" not in page and "-18" not in page and "Spearman-Brown = 0,81" in page
