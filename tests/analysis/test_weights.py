"""Weighting by raking (app/weights.py): the procedure against an own computation, the cap, Kish's effective sample
size, weighted shares and means, the setting on the page "Quoten", the switch on the results page and the export."""
import csv
import io
from datetime import datetime, timedelta

import numpy as np
from werkzeug.datastructures import MultiDict

from app import duplicate, export, results, transfer, weights
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import fill_consent
from tests.quotas.test_quotas import Q, build

W = Q + "/weighting"


# --- the procedure --------------------------------------------------------------------------------------


def own_ipf(a, b, ta, tb, rounds=1000):
    """Plain iterative proportional fitting of a two-way table of counts to known margins (shares)."""
    table = np.zeros((len(ta), len(tb)))
    for x, y in zip(a, b):
        table[x, y] += 1
    fitted = table.copy()
    n = table.sum()
    for _ in range(rounds):
        fitted *= (np.asarray(ta) * n / fitted.sum(axis=1))[:, None]
        fitted *= (np.asarray(tb) * n / fitted.sum(axis=0))[None, :]
    return fitted / table  # the weight of a person in each cell


def test_rake_matches_iterative_proportional_fitting_of_the_table():
    rng = np.random.default_rng(7)
    a = rng.choice(3, size=400, p=[0.5, 0.3, 0.2])
    b = rng.choice(2, size=400, p=[0.7, 0.3])
    ta, tb = [0.3, 0.4, 0.3], [0.5, 0.5]
    w, info = weights.rake([a, b], [ta, tb], low=None, tolerance=1e-10, rounds=1000)
    expected = own_ipf(a, b, ta, tb)
    assert info["converged"] and info["deviation"] < 1e-9
    assert np.allclose(w, expected[a, b], atol=1e-8)                      # the same weights as the fitted table
    assert abs(w.mean() - 1) < 1e-12
    for codes, target in ((a, ta), (b, tb)):                              # the margins are the targets
        assert np.allclose(np.bincount(codes, weights=w) / w.sum(), target, atol=1e-9)


def test_rake_stops_at_the_tolerance_and_counts_rounds():
    a = [0] * 60 + [1] * 40
    b = [0] * 30 + [1] * 30 + [0] * 10 + [1] * 30
    w, info = weights.rake([a, b], [[0.5, 0.5], [0.5, 0.5]])
    assert info["converged"] and 1 < info["rounds"] < weights.MAX_ROUNDS and info["deviation"] < weights.TOLERANCE
    assert abs((w[np.array(a) == 0].sum() / w.sum()) - 0.5) < weights.TOLERANCE


def test_people_without_a_group_keep_their_weight_in_that_step():
    # K12: -1 = fits no group of the attribute; the others are adjusted among themselves
    a = [0, 0, 0, 1, -1, -1]
    w, info = weights.rake([a], [[0.5, 0.5]], low=None)
    assert info["converged"]
    assert abs(w[0] * 3 - w[3]) < 1e-9                                     # 50:50 among those with a group
    assert w[4] == w[5]


def test_weights_are_capped_and_adjusted_again():
    # one person stands for a group that should be half: uncapped weight 25
    a = [0] * 49 + [1]
    uncapped, _ = weights.rake([a], [[0.5, 0.5]], low=None)
    assert uncapped.max() > 20
    w, info = weights.rake([a], [[0.5, 0.5]])
    assert w.min() >= weights.LOW - 1e-12 and w.max() <= weights.HIGH + 1e-12
    assert not info["converged"] and info["rounds"] == weights.MAX_ROUNDS     # the cap keeps it from the target
    # a cap that does not bind changes nothing
    b = [0] * 30 + [1] * 20
    w1, _ = weights.rake([b], [[0.5, 0.5]], low=None)
    w2, _ = weights.rake([b], [[0.5, 0.5]])
    assert np.allclose(w1, w2)


def test_effective_sample_size_after_kish():
    assert weights.n_eff([1, 1, 1, 1]) == 4
    assert abs(weights.n_eff([1, 1, 2, 2]) - 36 / 10) < 1e-12
    assert weights.n_eff([]) == 0


def test_weighted_shares_mean_and_sd():
    values, w = [1, 1, 2, None], [1.0, 1.0, 2.0, 5.0]
    assert weights.shares(values, w) == {1: 0.5, 2: 0.5}
    assert weights.mean(values, w) == 1.5
    assert abs(weights.sd([1, 2, 3, 4], [1, 1, 1, 1]) - np.std([1, 2, 3, 4], ddof=1)) < 1e-12   # all 1: the sample SD
    x, ws = [1.0, 2.0, 4.0], [0.5, 1.0, 1.5]
    m = np.average(x, weights=ws)
    # for sampling weights (Lumley, 2010): n / (n - 1) * sum w (x - m)^2 / sum w (audit 2026-10-12, S-10)
    assert abs(weights.sd(x, ws) - np.sqrt(3 / 2 * np.sum(np.array(ws) * (np.array(x) - m) ** 2) / sum(ws))) < 1e-12
    # the results functions: shares and M, SD weighted; the counts stay the numbers of answers
    freq = results.nominal_stats(["1", "1", "2"], {"1": "a", "2": "b"}, weights=[1.0, 1.0, 4.0])
    assert [(r["value"], r["count"], r["share"]) for r in results.by_count(freq["rows"])] == [("2", 1, 4 / 6), ("1", 2, 2 / 6)]
    assert freq["n"] == 3 and [m["value"] for m in freq["mode"]] == ["2"]
    num = results.numeric_stats([1.0, 3.0, None], weights=[3.0, 1.0, 1.0])
    assert num["n"] == 2 and num["mean"] == 1.5 and num["weighted"]
    plain = results.numeric_stats([1.0, 3.0])
    assert plain["mean"] == 2 and "weighted" not in plain
    # intervals: with the linearization SE (audit 2026-10-05, K-7), Wilson with n = p(1 - p) / SE^2; t with the
    # degrees of freedom of Kish's effective sample size
    from app import intervals

    values = ["1"] * 10 + ["2"] * 10
    ws = [0.5] * 10 + [1.5] * 10
    freq = results.nominal_stats(values, {"1": "a", "2": "b"}, weights=ws)
    n_eff = weights.n_eff(ws)
    assert abs(freq["n_eff"] - 16) < 1e-9 and freq["n"] == 20
    row = next(r for r in freq["rows"] if r["value"] == "2")
    x = np.array([0.0] * 10 + [1.0] * 10)
    se2 = 20 / 19 * np.sum(np.array(ws) ** 2 * (x - 0.75) ** 2) / sum(ws) ** 2
    n_lin = 0.75 * 0.25 / se2
    assert np.allclose(row["ci"], intervals.wilson(0.75 * n_lin, n_lin)) and row["ci"] != intervals.wilson(15, 20)
    num = results.numeric_stats([float(v) for v in values], weights=ws)
    assert num["ci"] == intervals.mean_ci(num["mean"], num["sd"], n_eff, se=weights.mean_se([float(v) for v in values], ws))
    ordinal = results.ordinal_stats([1, 2, 3], {"1": "", "2": "", "3": ""}, weights=[3.0, 1.0, 1.0])
    assert ordinal["median"]["value"] == "1" and abs(ordinal["rows"][0]["cum"] - 0.6) < 1e-12


# --- in a survey ----------------------------------------------------------------------------------------


def people(app, genders):
    """Completed real participations with these answers to the gender question (item 2) and age 30."""
    t0 = datetime(2026, 9, 1, 10)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for g in genders:
            s.responses.append(Response(status=Response.COMPLETED, source="real", answers={"2": g, "3": "30"},
                                        started_at=t0, completed_at=t0 + timedelta(seconds=300)))
        db.session.commit()


def quota_survey(app, client):
    """A quota by gender with the targets 1 (men) to 3 (women); 2 men and 2 women took part: the men weigh 0.5 and
    the women 1.5, n_eff = 16 / (2 * 0.25 + 2 * 2.25) = 3.2."""
    build(app, client, cells=[("Männer", "m", 25, [(2, "eq", "1")]), ("Frauen", "f", 75, [(2, "eq", "2")])])
    people(app, ["1", "1", "2", "2"])


def test_setting_is_saved_by_ticking_and_taken_back(app, client, survey):
    quota_survey(app, client)
    html = client.get(Q + "/").data.decode()
    assert 'id="weighting"' in html and "Soll-Anteile der eigenen Quoten" in html and "Deming, W. E." in html
    assert "Baker, R." in html                                            # the sources of the page, in one fold
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.weighting == {"kind": "quotas", "quotas": ["qa"], "census": []} and weights.active(s)
        assert weights.margins(s)[0][0]["groups"][1]["share"] == 0.75
    html = client.get(Q + "/").data.decode()
    assert 'name="w_quota" value="qa" checked' in html and "Frauen 75 %" in html
    client.post("/surveys/1/editor/undo")                                 # a step of its own
    with app.app_context():
        assert db.session.get(Survey, 1).weighting is None
    client.post(W, data={"kind": ""})
    with app.app_context():
        assert not weights.active(db.session.get(Survey, 1))


def test_weighted_results_switch_and_counts(app, client, survey):
    quota_survey(app, client)
    html = client.get("/surveys/1/results").data.decode()
    assert 'name="gewichtet"' not in html                                 # no weighting set: no switch
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    html = client.get("/surveys/1/results").data.decode()
    assert 'name="gewichtet"' in html and "checked data-submit-on-change" not in html and "effektive Fallzahl" not in html
    html = client.get("/surveys/1/results?gewichtet=1").data.decode()
    assert "checked data-submit-on-change" in html
    assert "n<sub>eff</sub> = 3" in html and "größtes Gewicht 1,50, kleinstes 0,50" in html
    assert "nicht repräsentativ" in html and "Deming, W. E." in html and "Kish, L. (1965)" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        data = results.compute(s, weighted=True)
        gender = next(c for e in data["items"] for c in e["columns"] if c["code"] == "gender")
        shares = {r["value"]: (r["count"], r["share"]) for r in gender["rows"]}
        assert shares["1"][0] == 2 and abs(shares["1"][1] - 0.25) < 1e-9 and abs(shares["2"][1] - 0.75) < 1e-9
        age = next(c for e in data["items"] for c in e["columns"] if c["code"] == "age")
        assert age["weighted"] and age["mean"] == 30
        assert data["weighting"]["n"] == 4 and abs(data["weighting"]["n_eff"] - 3.2) < 1e-9
        unweighted = results.compute(s)
        gender = next(c for e in unweighted["items"] for c in e["columns"] if c["code"] == "gender")
        assert {r["value"]: r["share"] for r in gender["rows"]}["1"] == 0.5 and unweighted["weighting"] is None


def test_kept_weighted_results_have_their_own_key(app, client, survey):
    from app import results_store

    quota_survey(app, client)
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        pool = {"sources": ["real"], "all": False, "drop": True, "speeder": 50, "speed_real": True}
        plain, weighted = results_store.fingerprint(s, pool), results_store.fingerprint(s, {**pool, "weighted": True})
        assert plain != weighted and results_store._path(s) != results_store._path(s, {"weighted": True})
        s.quotas[0].cells[1].target = 50                                  # other targets: other weighted results
        assert results_store.fingerprint(s, {**pool, "weighted": True}) != weighted
        assert results_store.fingerprint(s, pool) == plain


def test_export_has_the_weight_column_with_codebook_entry(app, client, survey):
    quota_survey(app, client)
    with app.app_context():
        assert "weight" not in [c for c, _ in export.columns(db.session.get(Survey, 1))]
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.responses.append(Response(status=Response.ACTIVE, source="real", answers={"2": "2"}, started_at=datetime(2026, 9, 1)))
        db.session.commit()
    body = client.get("/surveys/1/export/data.csv?all=1&source=real").data.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(body)))
    assert [r["weight"] for r in rows] == ["0.5", "0.5", "1.5", "1.5", ""]   # unfinished: no weight
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    line = next(l for l in book.splitlines() if l.startswith("weight,"))
    assert "Raking" in line and "Frauen 75,0 %" in line and "gekappt auf 0,2 bis 5,0" in line
    with app.app_context():
        cols, data, meta = export._spss_dictionary(db.session.get(Survey, 1), False, ("real",), None, True, None)
        assert meta["weight"]["kind"] == "num" and meta["weight"]["level"] == "SCALE"
        from app import methods

        f = methods.facts(db.session.get(Survey, 1))
        assert "per Raking (Deming & Stephan, 1940) nach Geschlecht x Alter an die Soll-Anteile der Quoten" in methods.draft(f)
        assert "effektive Fallzahl nach Kish, 1965: 3 von 4" in methods.draft(f)
        assert "Kish (1965) effective sample size 3 of 4" in methods.facts_text(f)


def test_duplicate_and_file_keep_the_setting(app, client, survey):
    quota_survey(app, client)
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert "weighting" in duplicate.SETTINGS
        dumped = transfer.dump(s, with_files=False)["survey"]
        assert dumped["weighting"] == {"kind": "quotas", "quotas": ["qa"], "census": []}
        assert transfer._settings(dumped, "Kopie")["weighting"] == dumped["weighting"]
    assert weights.clean({"kind": "quotas", "quotas": ["qa", 3, "qa"], "census": ["age", "x"]}) == \
        {"kind": "quotas", "quotas": ["qa"], "census": ["age"]}
    assert weights.clean({"kind": "evil"}) is None and weights.clean("x") is None


def test_census_targets_by_the_assistants_questions(app, client, survey):
    from tests.quotas.test_census import A

    fill_consent(client)
    client.post(A, data={"n": "1000"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = weights.census_questions(s)
        age, gender = found["age"].id, found["gender"].id
        t0 = datetime(2026, 9, 1, 10)
        # 3 men, 1 woman, 1 "divers" (no group: weight 1 in that step); all 30 years old
        for g in ("2", "2", "2", "1", "3"):
            s.responses.append(Response(status=Response.COMPLETED, source="real", answers={str(age): "30", str(gender): g},
                                        started_at=t0, completed_at=t0 + timedelta(seconds=300)))
        db.session.commit()
    client.post(W, data=MultiDict([("kind", "census"), ("w_census", "gender")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        table = export.rows(s)
        found = weights.compute(s, table)
        assert found["attributes"][0]["missing"] == 1 and found["attributes"][0]["name"] == "Geschlecht"
        by_gender = {}
        for r in table:
            by_gender.setdefault(r["gender"], []).append(r["weight"])
        men, women = sum(by_gender["2"]), sum(by_gender["1"])
        share = next(g["share"] for g in found["attributes"][0]["groups"] if g["name"] == "männlich")
        assert abs(men / (men + women) - share) < 0.001 and 0.45 < share < 0.52   # the official share of men
        assert by_gender["3"] == [1.0]                                    # divers: carried with weight 1
    html = client.get(Q + "/").data.decode()
    assert 'name="w_census" value="age"' in html and "keine passende Frage" not in html


def test_missed_targets_are_marked_at_the_figures_not_in_a_banner(app, client, survey):
    """Audit Datenspeicherung + Berechnungen 2026-10-21, C5: a target missed by 2 percentage points or more (here
    the cap 0.2 keeps the men at 10 % instead of 5 %) is marked "Prüfen" at the weighting figures and named in the
    method sentence; no warning of its own (Ralf). Within the threshold nothing is said."""
    build(app, client, cells=[("Männer", "m", 5, [(2, "eq", "1")]), ("Frauen", "f", 95, [(2, "eq", "2")])])
    people(app, ["1", "1", "2", "2"])
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    html = client.get("/surveys/1/results?gewichtet=1").data.decode()
    assert '<strong class="weight-check">Prüfen:</strong> Zielwerte nicht erreicht' in html and "bis zu 5,0 Prozentpunkte" in html
    assert "näherungsweise" not in html
    with app.app_context():
        found = results.compute(db.session.get(Survey, 1), weighted=True)["weighting"]
        assert found["check"] and not found["hints"]
        assert "Die Zielwerte wurden nicht erreicht" in weights.method_sentence(db.session.get(Survey, 1), found)
        assert "targets were not reached" in weights.method_sentence_en(db.session.get(Survey, 1), found)


def test_reached_targets_say_nothing_more(app, client, survey):
    quota_survey(app, client)
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    html = client.get("/surveys/1/results?gewichtet=1").data.decode()
    assert "weight-check" not in html
    with app.app_context():
        found = results.compute(db.session.get(Survey, 1), weighted=True)["weighting"]
        assert not found["check"] and "nicht erreicht" not in weights.method_sentence(db.session.get(Survey, 1), found)
