"""Latent class segments: engine (app/latent_class.py), storage, page, choice and export (app/segments.py)."""
import csv
import zlib
import io
import os
import re

import numpy as np

from app.choice_model import ChoiceSet, Coding
from app.extensions import db
from app.models import Item, Survey

LC_FAST = {"LC_SYNC": True, "LC_STARTS": 4, "LC_KMAX": 3}


def synthetic(n, truth, shares, tasks=12, seed=3):
    """n persons of len(truth) classes; per task three random profiles of Coding([3, 3, 2]) and "none"."""
    rng = np.random.default_rng(seed)
    coding = Coding([3, 3, 2], asc=True)
    cls = rng.choice(len(shares), n, p=shares)
    person_sets = []
    for p in range(n):
        sets = []
        for _ in range(tasks):
            X = np.array([coding.row([rng.integers(L) for L in coding.levels]) for _ in range(3)] + [coding.asc_row()])
            v = X @ truth[cls[p]]
            prob = np.exp(v - v.max())
            sets.append(ChoiceSet(X, int(rng.choice(4, p=prob / prob.sum()))))
        person_sets.append(sets)
    return person_sets, coding, cls


TRUTH = np.array([[2.0, -1.0, 1.5, 0.0, 0.8, 0.0],
                  [-2.0, 1.0, -1.0, 1.0, -0.8, 0.5]])


def test_engine_finds_two_classes_with_criteria_and_standard_errors():
    from app import latent_class as lc

    sets, coding, cls = synthetic(160, TRUTH, [0.6, 0.4])
    r = lc.estimate(sets, coding.k, coding.penalty(), kmax=3, starts=4)
    sols = {s["K"]: s for s in r["solutions"]}
    assert sorted(sols) == [1, 2, 3] and r["persons"] == 160 and r["sets"] == 160 * 12
    assert lc.suggest(r["solutions"]) == 2
    two = sols[2]
    assert two["bic"] < sols[1]["bic"] and two["bic"] < sols[3]["bic"]
    assert two["pi"][0] >= two["pi"][1] and abs(two["pi"][0] - 0.6) < 0.1     # segment 1 = the largest
    assert two["entropy"] > 0.8 and two["found"] >= 2 and two["converged"]
    assert abs(two["pi"].sum() - 1) < 1e-9 and np.allclose(two["tau"].sum(axis=1), 1)
    modal = two["tau"].argmax(axis=1)
    major = np.bincount(cls).argmax()
    assert ((modal == 0) == (cls == major)).mean() > 0.9
    assert np.corrcoef(two["B"][0], TRUTH[major])[0, 1] > 0.9
    # the parameters of every class and the shares have standard errors; the interval holds the share
    assert two["cov"] is not None and two["cov"].shape == (2 * coding.k + 1,) * 2
    assert np.all(np.diag(two["cov"]) > 0)
    lo, hi = two["share_ci"][0]
    assert lo < two["pi"][0] < hi
    # criteria as defined
    p = 2 * coding.k + 1
    assert two["params"] == p and abs(two["bic"] - (-2 * two["ll"] + p * np.log(160))) < 1e-6
    assert abs(two["aic3"] - (-2 * two["ll"] + 3 * p)) < 1e-6
    assert sols[1]["entropy"] is None and sols[1]["ll"] < two["ll"]


def test_engine_resumes_with_the_same_result_and_stays_finite_under_separation():
    from app import latent_class as lc

    sets, coding, _ = synthetic(90, TRUTH, [0.5, 0.5], tasks=8, seed=5)
    whole = lc.estimate(sets, coding.k, coding.penalty(), kmax=3, starts=3, sandwich=False)
    seen = []
    first = lc.estimate(sets, coding.k, coding.penalty(), kmax=2, starts=3, sandwich=False, on_solution=seen.append)
    assert [s["K"] for s in seen] == [1, 2]
    rest = lc.estimate(sets, coding.k, coding.penalty(), kmax=3, starts=3, sandwich=False, finished=first["solutions"])
    assert [s["K"] for s in rest["solutions"]] == [1, 2, 3]
    assert abs(rest["solutions"][2]["ll"] - whole["solutions"][2]["ll"]) < 1e-6   # own random stream per K
    # a level nobody ever chooses: without the prior its part-worth would run away
    for person in sets:
        for s in person:
            if s.X[s.chosen][0] == 1.0:                    # attribute 1, level 1 chosen: take "none" instead
                s.chosen = len(s.X) - 1
    r = lc.estimate(sets, coding.k, coding.penalty(), kmax=2, starts=3, sandwich=False)
    assert all(np.all(np.abs(s["B"]) < 15) for s in r["solutions"])
    assert lc.k_max(60, 6) == 2 and lc.k_max(1000, 6) == 6 and lc.k_max(20, 6) == 1


def cbc_with_segments(app, client, n=120, seed=7):
    """A CBC with price and "none" answered by n persons of two segments. The same data every run: the design and
    the pids are drawn with fixed seeds (the design was drawn anew each run and the random pids set the order of the
    persons, so the estimate and every number after it changed from run to run and some runs failed)."""
    from unittest import mock

    from app import conjoint_design
    from app.models import CODE_ALPHABET, PID_LENGTH
    from tests.analysis.test_cbc_results import setup
    from tests.analysis.test_conjoint_hb import simulate_segments

    generate = conjoint_design.generate
    with mock.patch.object(conjoint_design, "generate", lambda *a, **kw: generate(*a, **{**kw, "seed": seed})):
        setup(client, price_attr=True, none=True, tasks=12, versions=6)
    app.config.update(LC_FAST)
    rng = np.random.default_rng(seed)
    with mock.patch("tests.analysis.test_conjoint_hb.new_pid",
                    lambda code: f"{code}-" + "".join(rng.choice(list(CODE_ALPHABET), PID_LENGTH))):
        return simulate_segments(app, n, none_asc=-0.5, seed=seed)


def wide(client, query=""):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv" + query).data.decode("utf-8-sig"))))


def test_cbc_segments_button_page_choice_and_export(app, client, survey):
    truth = cbc_with_segments(app, client)
    html = client.get("/surveys/1/results").data.decode()
    assert "Segmente (latente Klassen)" in html and "Segmente schätzen" in html
    assert "cbc_lk" not in client.get("/surveys/1/export/data.csv").data.decode()          # nothing yet
    with app.app_context():
        from app import conjoint_hb, export

        item = db.session.get(Item, 2)
        hb_key = conjoint_hb.model_input(item, export.rows(db.session.get(Survey, 1), with_hb=False), {"x": 1})["key"]
    r = client.post("/surveys/1/results/lc/2", data={"source": "real"})
    assert r.status_code == 302 and r.location.endswith("#lc-2")
    with app.app_context():
        from app import segments

        item = db.session.get(Item, 2)
        res = segments.load(item)
        assert res["type"] == "conjoint" and len(res["pids"]) == 120 and res["settings"]["starts"] == 4
        assert os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], "hb", item.block.survey.uid, "item_2.lc.json"))
        assert not os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], "hb", item.block.survey.uid,
                                               "item_2.lc.part.json"))
        assert [s["K"] for s in res["solutions"]] == [1, 2, 3] and res["suggested"] == 2
        tau = np.array(segments.solution(res, 2)["tau"])
        seg = np.array([truth[pid] for pid in res["pids"]])
        agree = ((tau.argmax(axis=1) == 0) == (seg == 0)).mean()
        assert max(agree, 1 - agree) > 0.85                  # the two tastes of "look" are found
        view = segments.summary(item, res, "de")
        assert view["K"] == 2 and len(view["segments"]) == 2 and view["attributes"][0]["label"]
        look = view["attributes"][0]["levels"][0]["values"]
        assert look[0]["beta"] * look[1]["beta"] < 0          # opposite signs in the two segments
        assert all(v["se"] is not None for v in look)
        assert view["price"] is not None and all(b < 0 for b in view["price"]["beta"])
    html = client.get("/surveys/1/results").data.decode()
    assert "<strong>fertig</strong>" in html and "Segmente schätzen" not in html
    assert "lc-solutions" in html and "Vorschlag" in html and "Teilnutzen je Segment" in html
    assert "Kamakura, W. A., &amp; Russell" in html                          # the sources of the page
    rows = wide(client)
    assert {"cbc_lk", "cbc_lk_p1", "cbc_lk_p2"} <= set(rows[0]) and "cbc_lk_p3" not in rows[0]
    assert {r["cbc_lk"] for r in rows} == {"1", "2"}
    assert all(abs(float(r["cbc_lk_p1"]) + float(r["cbc_lk_p2"]) - 1) < 1e-3 for r in rows)
    # the researcher shows three segments and names them: the export follows, the questionnaire does not change
    r = client.post("/surveys/1/results/lc/2/choose", data={"source": "real", "lc_k": "3", "lc_name_0": "Klassisch",
                                                            "lc_name_1": "Futuristen; laut", "lc_name_2": ""})
    assert r.status_code == 302
    rows = wide(client)
    assert "cbc_lk_p3" in rows[0]
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    line = next(x for x in book.splitlines() if x.startswith("cbc_lk,"))
    assert "1 = Klassisch" in line and "2 = Futuristen, laut" in line and "3 = Segment 3" in line
    with app.app_context():
        from app import conjoint_hb, export, segments

        item = db.session.get(Item, 2)
        assert segments.box(item)["k"] == 3
        assert conjoint_hb.model_input(item, export.rows(db.session.get(Survey, 1), with_hb=False), {"x": 1})["key"] == hb_key
        assert segments.current(item) is not None                            # the choice leaves the result current
        from app import methods                                              # the method draft names the shown solution

        text = methods.facts(db.session.get(Survey, 1))["choice"][0]["text_de"]
        assert "gewählt wurden 3 Segmente (BIC = " in text and "abweichend vom BIC-Vorschlag von 2 Segmenten" in text
    html = client.get("/surveys/1/results").data.decode()
    assert "Klassisch" in html and "Rückgängig" in html
    client.post("/surveys/1/results/lc/2/undo", data={"source": "real"})
    with app.app_context():
        from app import segments

        assert segments.box(db.session.get(Item, 2)).get("k") is None       # back to the suggestion
    assert "cbc_lk_p3" not in wide(client)[0]


def test_cbc_segments_need_enough_persons_and_go_stale(app, client, survey):
    cbc_with_segments(app, client, n=40)
    html = client.get("/surveys/1/results").data.decode()
    assert "Für Segmente fehlen Daten (mindestens 60 Personen" in html
    r = client.post("/surveys/1/results/lc/2", data={"source": "real"}, follow_redirects=True)
    assert "Für Segmente fehlen Daten" in r.data.decode()
    from tests.analysis.test_conjoint_hb import simulate_segments

    simulate_segments(app, 40, none_asc=-0.5, seed=9)
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    with app.app_context():
        from app import segments

        assert segments.current(db.session.get(Item, 2)) is not None
    simulate_segments(app, 2, none_asc=-0.5, seed=11)                       # new answers: the result is stale
    html = client.get("/surveys/1/results").data.decode()
    assert "keine aktuelle Segmentschätzung" in html
    with app.app_context():
        from app import segments

        assert segments.current(db.session.get(Item, 2)) is None
    assert "cbc_lk" not in client.get("/surveys/1/export/data.csv").data.decode()


def test_interrupted_run_resumes_from_the_finished_solutions(app, client, survey):
    cbc_with_segments(app, client, n=80)
    with app.app_context():
        from app import export, latent_class, maxdiff_hb, segments

        item = db.session.get(Item, 2)
        table = maxdiff_hb.pool_table(db.session.get(Survey, 1), {"sources": ["real"], "drop": True})
        inp = segments.model_input(item, table)
        paths = segments._paths(item)
        inp["partial"] = segments._part_path(paths)
        os.makedirs(os.path.dirname(paths[0]), exist_ok=True)  # as claim() makes it; never made anew by the run (C3)
        calls = []
        real = latent_class.estimate

        def stop_after_two(*args, **kw):
            seen = kw["on_solution"]

            def keep(sol):
                seen(sol)
                calls.append(sol["K"])
                if sol["K"] == 2:
                    raise RuntimeError("process killed")
            return real(*args, **{**kw, "on_solution": keep})

        latent_class.estimate = stop_after_two
        try:
            try:
                segments.run(inp)
            except RuntimeError:
                pass
        finally:
            latent_class.estimate = real
        assert calls == [1, 2] and segments.pending(item, inp["key"]) == 2
        html_state = segments.view(item, table, "de")
        assert html_state["pending"] == 2
        resumed = []

        def count(*args, **kw):
            resumed.append(len(kw["finished"]))
            return real(*args, **kw)

        latent_class.estimate = count
        try:
            out = segments.run(inp)
        finally:
            latent_class.estimate = real
        assert resumed == [2] and [s["K"] for s in out["solutions"]] == [1, 2, 3]
        assert not os.path.exists(inp["partial"])
        assert export  # imported for the pool table


def test_maxdiff_segments(app, client, survey):
    from tests.analysis.test_maxdiff import setup

    setup(client, anchor=True, versions=2)
    app.config.update(LC_FAST)
    with app.app_context():
        from app import dummy

        assert dummy.generate(db.session.get(Survey, 1), 100, seed=5) == 100
        db.session.commit()
    r = client.post("/surveys/1/results/lc/2", data={"source": "dummy"})
    assert r.status_code == 302 and "source=dummy" in r.location
    with app.app_context():
        from app import segments

        item = db.session.get(Item, 2)
        res = segments.load(item)
        assert res["type"] == "maxdiff" and res["anchored"] and len(res["pids"]) >= 60  # without the marked dummies
        view = segments.summary(item, res, "de")
        rows = view["statements"]
        assert len(rows) == len(res["est"])
        for c in range(view["K"]):
            assert abs(sum(r["values"][c]["score"] for r in rows) - 100) < 1e-6   # scores sum 100 per segment
        overall = [r["overall"] for r in rows]
        assert overall == sorted(overall, reverse=True)
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    # one segment shows no tables of its own (UI/UX audit 2026-10-05, V65)
    assert "Segmente (latente Klassen)" in html and ("Wahrscheinlichkeits-Scores je Segment" in html if view["K"] > 1
                                                     else "keine Gruppen mit unterschiedlichen Vorlieben" in html)


def test_saved_choice_from_a_survey_file_is_cleaned():
    from app.segments import clean

    assert clean({"k": 3, "names": {"3": ["A=B", "x" * 80, None], "9": ["z"]}}) == \
        {"k": 3, "names": {"3": ["A-B", "x" * 40, ""]}}
    assert clean({"k": 99}) == {"k": None, "names": {}} and clean("nonsense") == {}


# --- stage 2: in place, profiles, hit rates, HB means ----------------------------------------------------------------

FETCH = {"X-Requested-With": "fetch"}


def test_choice_and_undo_in_place_without_computing_the_page(app, client, survey):
    from tests.conftest import add

    truth = cbc_with_segments(app, client, n=120)
    add(client, "single_choice")                                      # item 3: describes the segments
    with app.app_context():
        from app.models import Response

        item = db.session.get(Item, 3)
        codes = [str(o["code"]) for o in item.config["options"]]
        for r in db.session.scalars(db.select(Response)):
            # segment 0 answers mostly the first option, segment 1 mostly the second
            first = truth[r.pid] == 0
            r.answers = {**r.answers, "3": codes[0] if (zlib.crc32(r.pid.encode()) % 10 < 8) == first else codes[1]}
        db.session.commit()
    r = client.post("/surveys/1/results/lc/2", data={"source": "real"}, headers=FETCH)
    assert r.is_json and r.json["error"] is False and "gestartet" in r.json["message"]
    client.get("/surveys/1/results?go=1")                             # the page computes and keeps its results
    part = client.get("/surveys/1/results?lc_part=2", headers=FETCH)
    html = part.data.decode()
    assert part.status_code == 200
    assert 'class="lc-part"' in html and "Ergebnisse werden berechnet" not in html and "<html" not in html
    assert "Segmente beschreiben" in html and "lc-profile" in html     # the single choice question by default
    r = client.post("/surveys/1/results/lc/2/choose", data={"lc_k": "3"}, headers=FETCH)
    assert r.json == {"message": "Übernommen.", "error": False}
    html = client.get("/surveys/1/results?lc_part=2", headers=FETCH).data.decode()
    assert html.count('name="lc_name_') == 3
    # the page itself still comes from the kept results (no wait page), with the new choice
    page = client.get("/surveys/1/results").data.decode()
    assert "Ergebnisse werden berechnet" not in page and page.count('name="lc_name_') == 3
    r = client.post("/surveys/1/results/lc/2/undo", data={}, headers=FETCH)
    assert r.json["message"] == "Letzte Änderung zurückgenommen."
    html = client.get("/surveys/1/results?lc_part=2", headers=FETCH).data.decode()
    assert html.count('name="lc_name_') == 2
    # without the script: back to the results with the pool the page named, none for the saved one
    r = client.post("/surveys/1/results/lc/2/choose", data={"lc_k": "3"})
    assert r.status_code == 302 and "source=" not in r.location and r.location.endswith("#lc-2")
    page = client.get("/surveys/1/results?source=real").data.decode()
    assert 'type="hidden" name="source" value="real"' in page


def test_profiles_hit_rates_and_hb_means(app, client, survey, monkeypatch):
    from tests.conftest import add
    from tests.analysis.test_conjoint_hb import HB_FAST

    truth = cbc_with_segments(app, client, n=120)
    add(client, "single_choice")
    with app.app_context():
        from app.models import Response

        codes = [str(o["code"]) for o in db.session.get(Item, 3).config["options"]]
        for r in db.session.scalars(db.select(Response)):
            r.answers = {**r.answers, "3": codes[0] if truth[r.pid] == 0 else codes[1]}
        db.session.commit()
    app.config.update(HB_FAST)
    client.post("/surveys/1/results/hb/2", data={"source": "real"})
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    with app.app_context():
        from app import holdouts, maxdiff_hb, segments

        item = db.session.get(Item, 2)
        stored = segments.load(item)
        table = maxdiff_hb.pool_table(db.session.get(Survey, 1), {"sources": ["real"], "drop": True})
        # a holdout of a humanoid and an animal-like robot: segment 0 takes the first, segment 1 the second
        monkeypatch.setattr(holdouts, "cbc_active", lambda it: [(0, [[0, 1, 0], [2, 1, 0], [1, 1, 1]])])
        rows = [{"pid": pid, "cbc_ho1": 1 if truth[pid] == 0 else 2} for pid in stored["pids"]]
        hits = segments._hits(item, stored, rows)
        assert hits[2] > 0.9 and hits[1] < 0.75                       # two segments predict, one cannot
        view = segments.view(item, table, "de")
        data = {"items": [{"item": db.session.get(Item, 3), "columns": [], "multi": False}]}
        from app import results as res

        page = res.compute(db.session.get(Survey, 1), ("real",), False, True)
        entries = page["items"]
        segments.enrich(item, view, entries, table)
        assert view["profile_ids"] == [3]
        block = view["profile"][0]["blocks"][0]
        assert block["kind"] == "shares"
        first = next(r for r in block["rows"] if r["shares"][0] > 0.5)    # one answer marks one segment
        assert first["shares"][1] < 0.2
        means = view["hb_means"]
        assert means["kind"] == "cbc" and means["persons"] == 120
        look = means["rows"][0]["levels"]
        assert look[0]["values"][0] * look[0]["values"][1] < 0          # HB and segments agree on "look"
        assert data


def test_simulator_per_segment(app, client, survey):
    from werkzeug.datastructures import MultiDict

    from tests.analysis.test_conjoint_hb import HB_FAST

    cbc_with_segments(app, client, n=120)
    app.config.update(HB_FAST)
    client.post("/surveys/1/results/hb/2", data={"source": "real"})
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    with app.app_context():
        from app import results as res, simulator

        page = res.compute(db.session.get(Survey, 1), ("real",), False, True)
        entry = next(e for e in page["items"] if e["item"].id == 2)
        item = entry["item"]
        # a humanoid against an animal-like robot, same price and privacy
        scenario = [("sim", "2"), ("sim_n", "2"), ("sim_0_0", "0"), ("sim_1_0", "2"), ("sim_0_2", "0"), ("sim_1_2", "0"),
                    ("sim_0_price", "100"), ("sim_1_price", "100")]
        everyone = simulator.view(item, entry["cbc"], MultiDict(scenario), "de")
        seg = everyone["segments"]
        assert seg["filter"] and len(seg["options"]) == 2 and seg["selected"] == 0
        table = seg["table"]["rows"]
        humanoid = next(r for r in table if r["label"]["name"] == "Produkt A")
        a, b = (float(x.replace(",", ".")) for x in humanoid["shares"])
        assert max(a, b) > 75 and min(a, b) < 25                          # the segments choose oppositely
        first = simulator.view(item, entry["cbc"], MultiDict(scenario + [("sim_seg", "1")]), "de")
        assert first["segments"]["selected"] == 1
        share = {r["label"]["name"]: float(r["sop"].replace(",", ".")) for r in first["rows"]}
        assert abs(share["Produkt A"] - a) < 1.0                         # the filter gives the table's share
        assert "_P" not in first and "_pids" not in first
    html = client.get("/surveys/1/results?" + "&".join(f"{k}={v}" for k, v in scenario + [("sim_seg", "2")])).data.decode()
    assert "Anteile je Segment" in html and 'name="sim_seg"' in html


def test_simulator_segments_without_hb_from_the_segment_parameters(app, client, survey):
    from werkzeug.datastructures import MultiDict

    cbc_with_segments(app, client, n=120)
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    with app.app_context():
        from app import results as res, simulator

        page = res.compute(db.session.get(Survey, 1), ("real",), False, True)
        entry = next(e for e in page["items"] if e["item"].id == 2)
        scenario = MultiDict([("sim", "2"), ("sim_n", "2"), ("sim_0_0", "0"), ("sim_1_0", "2"), ("sim_0_2", "0"),
                              ("sim_1_2", "0"), ("sim_0_price", "100"), ("sim_1_price", "100"), ("sim_seg", "1")])
        v = simulator.view(entry["item"], entry["cbc"], scenario, "de")
        assert v["basis"] == "pooled" and not v["segments"]["filter"] and v["segments"]["selected"] == 0
        rows = v["segments"]["table"]["rows"]
        a, b = (float(x.replace(",", ".")) for x in next(r for r in rows if r["label"]["name"] == "Produkt A")["shares"])
        assert max(a, b) > 75 and min(a, b) < 25


# --- stage 3: ACBC, BCH tests -----------------------------------------------------------------------------------------

def test_bch_weights_and_wald_test():
    from app import group_tests as gt
    from app.segments import bch_weights, wald

    rng = np.random.default_rng(4)
    n = 400
    cls = rng.integers(2, size=n)
    T = np.where(cls[:, None] == np.arange(2), 0.85, 0.15) + rng.uniform(-0.1, 0.1, (n, 1)) * [1, -1]
    T = T / T.sum(axis=1, keepdims=True)
    B = bch_weights(T)
    assert B.shape == (n, 2)
    # the BCH weights of one person sum to 1 over the segments (rows of D^-1 sum to 1, as those of D)
    assert np.allclose(B.sum(axis=1), 1)
    differ = wald((cls + rng.normal(0, 1, n))[:, None], B)
    same = wald(rng.normal(0, 1, n)[:, None], B)
    assert differ["p"] < 0.001 and differ["df"] == 1 and differ["n"] == n and differ["df2"] == n - 2
    assert abs(differ["p"] - gt.f_sf(differ["F"], 1, n - 2)) < 1e-12   # referred to F(df, n - K) (S-2)
    assert same["p"] > 0.01
    # a category nobody in the data has: one degree of freedom less, no error
    Y = np.column_stack([cls == 1, np.zeros(n)]).astype(float)
    assert wald(Y, B)["df"] == 1
    assert bch_weights(np.full((10, 2), 0.5)) is None                     # nobody told apart: no correction


def test_profile_blocks_carry_bch_tests(app, client, survey):
    from tests.conftest import add

    truth = cbc_with_segments(app, client, n=120)
    add(client, "single_choice")
    with app.app_context():
        from app.models import Response

        codes = [str(o["code"]) for o in db.session.get(Item, 3).config["options"]]
        for r in db.session.scalars(db.select(Response)):
            r.answers = {**r.answers, "3": codes[0] if truth[r.pid] == 0 else codes[1]}
        db.session.commit()
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    html = client.get("/surveys/1/results?go=1").data.decode()
    assert "Unterschied zwischen den Segmenten (BCH)" in html and "Bakk, Z., Tekle" in html
    row = re.search(r'<tr class="lc-test">.*?</tr>', html, re.S).group(0)              # reported as F (S-2)
    assert re.search(r"\(BCH\): F\(1, 118\) = [\d.,]+, N = 120, .*&lt; ,001", row, re.S) and "χ²" not in row


def test_acbc_segments(app, client, survey):
    from tests.analysis import test_acbc_results as acbc

    acbc.setup(client, screens=5, per_screen=4, price=True)
    app.config.update(LC_FAST)
    with app.app_context():
        acbc.add_responses(db.session.get(Survey, 1), acbc.simulate(db.session.get(Item, 2), 70, price_beta=-0.01))
    html = client.get("/surveys/1/results").data.decode()
    # 70 persons are too few for two segments of this ACBC: the page names the number instead of the button (S-6)
    assert "Segmente schätzen" not in html and "Für 2 Segmente sind mindestens" in html
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    with app.app_context():
        from app import segments

        item = db.session.get(Item, 2)
        res = segments.load(item)
        assert res["type"] == "acbc" and len(res["pids"]) == 70
        view = segments.summary(item, res, "de")
        assert any(w.startswith("ACBC:") for w in view["warnings"])
        assert segments._hits(item, res, []) is None
    html = client.get("/surveys/1/results").data.decode()
    assert "Segmente (latente Klassen)" in html and "<strong>fertig</strong>" in html


# --- covariates of the class shares (Dayton & Macready, 1988) -------------------------------------------------------

def test_engine_with_covariates_finds_who_belongs_where():
    from app import latent_class as lc

    rng = np.random.default_rng(8)
    n = 200
    x = rng.integers(2, size=n)
    shares = np.where(x[:, None] == 1, [0.85, 0.15], [0.15, 0.85])
    sets, coding, _ = synthetic(n, TRUTH, [1.0, 0.0], seed=9)        # everyone as class 1
    # put every person in the class its covariate makes likely: rebuild the choices of the other class
    cls = np.array([rng.choice(2, p=s) for s in shares])
    sets2, _, _ = synthetic(n, TRUTH[::-1], [1.0, 0.0], seed=10)   # everyone as class 2
    person_sets = [a if c == 0 else b for a, b, c in zip(sets, sets2, cls)]
    Z = np.column_stack([np.ones(n), x])
    plain = lc.estimate(person_sets, coding.k, coding.penalty(), kmax=2, starts=4)["solutions"][1]
    r = lc.estimate(person_sets, coding.k, coding.penalty(), kmax=2, starts=4, Z=Z)
    two = r["solutions"][1]
    assert two["G"].shape == (2, 2) and np.allclose(two["G"][:, 0], 0)
    assert two["params"] == 2 * coding.k + 2 and two["ll"] > plain["ll"]
    # the slope says: with x = 1 one is much less likely in segment 2 than in segment 1 (or the other way round)
    assert abs(two["G"][1, 1]) > 2
    m = 2 * coding.k
    se = np.sqrt(np.diag(two["cov"]))[m:]
    assert se.shape == (2,) and abs(two["G"][1, 1]) / se[1] > 3
    assert two["share_ci"] is None and abs(two["pi"].sum() - 1) < 1e-9


def test_covariates_chosen_estimated_shown_and_described(app, client, survey):
    from tests.conftest import add

    truth = cbc_with_segments(app, client, n=120)
    add(client, "single_choice")                                      # item 3: says who is in which segment
    with app.app_context():
        from app.models import Response

        codes = [str(o["code"]) for o in db.session.get(Item, 3).config["options"]]
        for r in db.session.scalars(db.select(Response)):
            r.answers = {**r.answers, "3": codes[0] if (truth[r.pid] == 0) == (zlib.crc32(r.pid.encode()) % 10 < 9) else codes[1]}
        db.session.commit()
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    client.get("/surveys/1/results?go=1")
    html = client.get("/surveys/1/results?lc_part=2", headers=FETCH).data.decode()
    assert "Kovariaten wählen" in html and 'name="lc_cov" value="3"' in html
    r = client.post("/surveys/1/results/lc/2/choose", data={"lc_cov_sent": "1", "lc_cov": "3"}, headers=FETCH)
    assert "neu geschätzt" in r.json["message"]
    with app.app_context():
        from app import segments

        item = db.session.get(Item, 2)
        res = segments.load(item)
        assert [m["item"] for m in res["covariates"]] == [3]
        assert segments.current(item) is not None
        two = segments.solution(res, 2)
        assert two["G"] is not None and len(two["G"]) == 2
        view = segments.summary(item, res, "de")
        cell = view["covariates"]["rows"][0]["cells"][0]
        assert cell["se"] is not None and cell["p"] < 0.01 and (cell["or"] > 3 or cell["or"] < 1 / 3)
        assert "Kovariaten der Segmentanteile" in segments.describe(res, item.survey, 2)
    html = client.get("/surveys/1/results?lc_part=2", headers=FETCH).data.decode()
    # the list of sources is on the page; only a fold "Warum?" of the Lernhilfen carries its own
    outside_folds = re.sub(r'<details class="learn-why">.*?</details>', "", html, flags=re.S)
    assert "Wer gehört wohin" in html and "Dayton, C. M." not in outside_folds
    page = client.get("/surveys/1/results").data.decode()
    assert "Dayton, C. M., &amp; Macready" in page
    # without covariates again: back to the shares that are the same for everyone
    client.post("/surveys/1/results/lc/2/choose", data={"lc_cov_sent": "1"}, headers=FETCH)
    with app.app_context():
        from app import segments

        assert segments.load(db.session.get(Item, 2)).get("covariates") is None


def test_saved_scenario_keeps_its_segment(app, client, survey):
    from tests.analysis.test_conjoint_hb import HB_FAST

    cbc_with_segments(app, client, n=120)
    app.config.update(HB_FAST)
    client.post("/surveys/1/results/hb/2", data={"source": "real"})
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    form = {"sim": "2", "sim_n": "2", "sim_0_0": "0", "sim_1_0": "2", "sim_0_2": "0", "sim_1_2": "0",
            "sim_0_price": "100", "sim_1_price": "100", "sim_seg": "2", "sim_name": "Nur Segment 2"}
    client.post("/surveys/1/results/sim/2/save", data=form)
    with app.app_context():
        from app import simulator

        item = db.session.get(Item, 2)
        assert simulator.saved(item)[0]["segment"] == 2
        assert simulator.clean({"scenarios": simulator.saved(item)})["scenarios"][0]["segment"] == 2
    html = client.get("/surveys/1/results?sim=2&sim_load=0").data.decode()
    assert '<option value="2" selected>' in html and "nur „Segment 2“" in html
    # the list in the address wins over the saved one
    html = client.get("/surveys/1/results?sim=2&sim_load=0&sim_seg=0").data.decode()
    assert '<option value="0" selected>Alle Personen' in html


# --- math audit 2026-10-05: profiles with BCH, small cells, covariate reference, "Anderes", minimum size ----------------

def _calibrated(n, pi, mu, rng):
    """Classes and exactly calibrated posteriors from a normal indicator (as the audit's scripts, segmente/s3)."""
    c = rng.choice(len(pi), size=n, p=pi)
    x = rng.normal(np.asarray(mu)[c], 1.0)
    A = np.log(pi)[None, :] - 0.5 * (x[:, None] - np.asarray(mu)[None, :]) ** 2
    T = np.exp(A - A.max(axis=1, keepdims=True))
    return c, T / T.sum(axis=1, keepdims=True)


def test_bch_profile_values_undo_the_misclassification():
    """S-1: proportional assignment (tau as weights) shrinks a true difference of 1.00 to about 0.55 at an entropy
    of 0.5, the BCH-weighted means keep it; BCH shares outside [0, 1] are set to the edge and marked."""
    from app.segments import _weighted_mean, _weighted_shares, bch_weights

    rng = np.random.default_rng(11)
    proportional, bch = [], []
    for _ in range(20):
        c, T = _calibrated(400, [0.6, 0.4], [0.0, 1.6], rng)
        y = [float(v) for v in rng.normal(c * 1.0, 1.0)]
        B = bch_weights(T)
        p = _weighted_mean(y, T)[0]
        b = _weighted_mean(y, B)[0]
        proportional.append(p[1]["mean"] - p[0]["mean"])
        bch.append(b[1]["mean"] - b[0]["mean"])
    assert np.mean(proportional) < 0.75                                      # understated
    assert abs(np.mean(bch) - 1.0) < 0.1                                     # corrected
    # a share outside [0, 1]: clipped and marked
    W = np.array([[0.9, 0.1], [0.6, -0.4], [0.1, 0.9]])
    rows, _ = _weighted_shares(["a", "a", "b"], W, [("a", "A"), ("b", "B")])
    assert rows[0]["shares"][1] == 0.0 and rows[0]["clipped"][1] is True  # -0.3 / 0.6: below 0
    assert rows[0]["shares"][0] == 1.5 / 1.6 and rows[0]["clipped"][0] is False and rows[0]["count"] == 2


def test_bch_test_merges_rare_answers_and_keeps_its_size():
    """S-2: with five answers (40/25/15/10/10 %) and a segment of 10 % among 300 persons the Wald test rejected a
    true null hypothesis in 26 % of the samples (audit, segmente/s3b); merging rare answers until every cell expects 5
    persons brought it near 10 %, the F version with the variance under H0 near 5 %. Too small even merged: no
    test."""
    from app.segments import _category_test, _test_groups, bch_weights

    rng = np.random.default_rng(77)
    order = [(j, str(j)) for j in range(5)]
    rejected = done = 0
    for _ in range(300):
        _, T = _calibrated(300, [0.9, 0.1], [0.0, 2.5], rng)
        B = bch_weights(T)
        if B is None:
            continue
        values = [int(v) for v in rng.choice(5, size=300, p=[0.4, 0.25, 0.15, 0.1, 0.1])]
        test = _category_test(values, order, B, False)
        if test and not test.get("small"):
            done += 1
            rejected += test["p"] < 0.05
            assert test["merged"]                                            # rare answers taken together
    assert done > 250 and rejected / done < 0.09                            # with the F version near 5 %
    # ordered answers join a neighbour, never a far one
    B = np.column_stack([np.r_[np.ones(60), np.zeros(60)], np.r_[np.zeros(60), np.ones(60)]])
    values = [0] * 50 + [1] * 2 + [2] * 50 + [3] * 18
    assert _test_groups(values, [0, 1, 2, 3], B, True) == [[0, 1], [2], [3]]
    # one segment of 4 persons: no test at all
    B = np.column_stack([np.r_[np.ones(116), np.zeros(4)], np.r_[np.zeros(116), np.ones(4)]])
    assert _category_test([0, 1] * 60, [(0, "a"), (1, "b")], B, False) == {"small": True}


def test_bch_test_as_f_test_keeps_its_size_and_tests_an_answer_that_follows_the_segment():
    """S-2 rest: after merging rare answers the Wald test (variance around the segment means, chi-square) still
    rejected a true null hypothesis in 9 to 10 % of the samples in the situations of the audit (here: 9.9 % for five
    answers and a segment of 10 % among 300 persons, 9.5 % for a number with 80 persons). With the variance around
    the common mean and F(df, n - K) both stay near 5 %. An answer that follows the segment exactly left no variance
    within the segments and no test; now it is clearly significant."""
    from app.segments import _category_test, bch_weights, wald

    rng = np.random.default_rng(2026)
    order = [(j, str(j)) for j in range(5)]
    rejected = done = 0
    for _ in range(1000):
        _, T = _calibrated(300, [0.9, 0.1], [0.0, 2.5], rng)
        B = bch_weights(T)
        test = _category_test([int(v) for v in rng.choice(5, size=300, p=[0.4, 0.25, 0.15, 0.1, 0.1])], order, B, False)
        if test and not test.get("small"):
            done += 1
            rejected += test["p"] < 0.05
            assert test["df2"] == 298 and test["F"] >= 0
    assert done > 950 and 0.03 < rejected / done < 0.075
    rejected = done = 0
    for _ in range(1000):
        _, T = _calibrated(80, [0.85, 0.15], [0.0, 2.0], rng)
        B = bch_weights(T)
        if B is None:
            continue
        test = wald(rng.normal(size=80)[:, None], B)
        done += 1
        rejected += test["p"] < 0.05
    assert done > 950 and 0.02 < rejected / done < 0.075
    # the answer is the segment: no variance within the segments, the test still stands
    cls = np.r_[np.zeros(60, dtype=int), np.ones(60, dtype=int)]
    B = bch_weights(np.where(cls[:, None] == np.arange(2), 1.0, 0.0))
    test = _category_test([int(c) for c in cls], [(0, "a"), (1, "b")], B, False)
    assert test["df"] == 1 and test["df2"] == 118 and test["p"] < 1e-10


def _profile_answers(app, truth, rest_for=()):
    """Item 3 (single choice, four options, the fourth with a text field "Anderes, und zwar"): segment 0 answers 1,
    segment 1 answers 2, the persons in rest_for the fourth option."""
    with app.app_context():
        from sqlalchemy.orm.attributes import flag_modified

        from app.models import Response

        item = db.session.get(Item, 3)
        options = [dict(o) for o in item.config["options"]][:2] + [{"id": "o3", "code": "3"},
                                                                   {"id": "o4", "code": "4", "text": True}]
        item.config = {**item.config, "options": options}
        flag_modified(item, "config")
        for r in db.session.scalars(db.select(Response)):
            r.answers = {**r.answers, "3": "4" if r.pid in rest_for else ("1" if truth[r.pid] == 0 else "2")}
        db.session.commit()


def test_profile_shows_bch_values_the_other_answer_and_merged_cells(app, client, survey):
    """S-1, S-2, S-5 on the page: the profile table shows the BCH-weighted shares the test uses; "Anderes" is a row
    of its own and, with few persons, merged for the test; the codebook no longer recommends the probabilities as
    weights."""
    from tests.conftest import add

    truth = cbc_with_segments(app, client, n=120)
    add(client, "single_choice")
    rest = sorted(truth)[:3]
    _profile_answers(app, truth, rest)
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    with app.app_context():
        from app import maxdiff_hb, results as res, segments

        item = db.session.get(Item, 2)
        stored = segments.load(item)
        table = maxdiff_hb.pool_table(db.session.get(Survey, 1), {"sources": ["real"], "drop": True})
        view = segments.view(item, table, "de")
        page = res.compute(db.session.get(Survey, 1), ("real",), False, True)
        segments.enrich(item, view, page["items"], table)
        assert view["bch"]
        block = view["profile"][0]["blocks"][0]
        labels = [r["label"] for r in block["rows"]]
        assert len(labels) == 3 and block["rows"][-1]["count"] == 3          # "Anderes" last, with its 3 persons
        # the shares are the BCH-weighted ones (not the membership probabilities)
        tau = {pid: t for pid, t in zip(stored["pids"], segments.solution(stored, segments.shown_k(item, stored))["tau"])}
        rows = [r for r in table if r.get("pid") in tau]
        T = np.array([tau[r["pid"]] for r in rows])
        B = segments.bch_weights(T)
        code3 = db.session.get(Item, 3).code
        answer = np.array([str(r.get(code3)) for r in rows])
        for key in ("1", "2"):
            expect = np.clip(B[answer == key].sum(axis=0) / B.sum(axis=0), 0, 1)
            proportional = T[answer == key].sum(axis=0) / T.sum(axis=0)
            gap = float(np.abs(expect - proportional).max())
            assert gap > 5e-5                    # the (fixed) data tell BCH and proportional shares apart
            assert any(np.allclose(r["shares"], expect, rtol=0, atol=1e-12) for r in block["rows"])
            assert not any(np.allclose(r["shares"], proportional, rtol=0, atol=gap / 2) for r in block["rows"])
        # 3 persons with "Anderes": too few for a cell of their own, merged for the test
        assert block["test"]["merged"] and "+" in block["test"]["merged"][0]
    html = client.get("/surveys/1/results?go=1").data.decode()
    assert "korrigiert sind die BCH-Werte" in html and "verwischt Unterschiede weniger" not in html
    assert "seltene Antworten für den Test zusammengefasst" in html
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    line = next(x for x in book.splitlines() if x.startswith("cbc_lk,"))
    assert "korrigieren die Fehlzuordnung nicht" in line and "besser die Wahrscheinlichkeiten" not in line
    assert "BCH-Gewichte" in next(x for x in book.splitlines() if x.startswith("cbc_lk_p1,"))


def test_covariate_reference_is_the_first_answer_somebody_gave(app, client, survey):
    """S-3: nobody answered the first option (e.g. screened out): the dummies of all given answers plus the intercept
    were collinear, the odds ratios against the empty group arbitrary. Now the first given answer is the reference
    and Z has full column rank."""
    from tests.conftest import add

    truth = cbc_with_segments(app, client, n=120)
    add(client, "single_choice")
    _profile_answers(app, truth)                                     # answers 1 and 2 only
    with app.app_context():
        from sqlalchemy.orm.attributes import flag_modified

        from app.models import Response

        item3 = db.session.get(Item, 3)
        options = [dict(o) for o in item3.config["options"]]
        item3.config = {**item3.config, "options": [{"id": "o0", "code": "9"}] + options}  # a first option nobody gave
        flag_modified(item3, "config")
        db.session.commit()
        from app import compare, maxdiff_hb, segments

        item = db.session.get(Item, 2)
        item.config = {**item.config, "lc": {"covariates": [3]}}
        db.session.commit()
        table = maxdiff_hb.pool_table(db.session.get(Survey, 1), {"sources": ["real"], "drop": True})
        pids = [r["pid"] for r in table if r.get("pid")]
        Z, meta = segments.covariate_design(item, table, pids)
        Z = np.array(Z)
        assert np.linalg.matrix_rank(Z) == Z.shape[1] == 2                 # intercept and one dummy
        groups = compare.groups_of(compare.question(item.survey, 3))
        given = [label for code, label in groups if code in ("1", "2")]
        assert groups[0][0] == "9" and meta[0]["reference"] == given[0] and meta[0]["label"] == given[1]
        assert Response  # imported for the answers above


def test_too_few_persons_for_two_segments_named_instead_of_the_button(app, client, survey, monkeypatch):
    """S-6: K <= N / (5 k): below 10 k persons the run could only give one segment; the page says how many are needed
    instead of offering it."""
    from app import latent_class

    cbc_with_segments(app, client, n=120)
    monkeypatch.setattr(latent_class, "PER_PARAMETER", 50)
    html = client.get("/surveys/1/results").data.decode()
    assert "Segmente schätzen" not in html
    m = re.search(r"Für 2 Segmente sind mindestens (\d+) Personen nötig \(5 Personen je Parameter und Segment, hier "
                  r"(\d+) Parameter je Segment\); mit 120 Personen", html)
    assert m and int(m.group(1)) == 2 * 50 * int(m.group(2))


def test_simulator_without_hb_mixes_the_segments_and_word_keeps_the_segment(app, client, survey):
    """S-4: without HB the bars come from the pooled logit; the table per segment gets "Gesamt (Segmente)" =
    sum_c pi_c P_cj and the note that the pooled bars are not their mean. M-3, M-5: with HB a saved scenario's segment
    weights its Word table as on the page, and the page names the size of the segment."""
    from werkzeug.datastructures import MultiDict

    from tests.analysis.test_conjoint_hb import HB_FAST

    cbc_with_segments(app, client, n=120)
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    scenario = [("sim", "2"), ("sim_n", "2"), ("sim_0_0", "0"), ("sim_1_0", "2"), ("sim_0_2", "0"), ("sim_1_2", "0"),
                ("sim_0_price", "100"), ("sim_1_price", "100")]
    with app.app_context():
        from app import results as res, simulator

        page = res.compute(db.session.get(Survey, 1), ("real",), False, True)
        entry = next(e for e in page["items"] if e["item"].id == 2)
        v = simulator.view(entry["item"], entry["cbc"], MultiDict(scenario), "de")
        t = v["segments"]["table"]
        pi = [s["share"] for s in entry["cbc"]["lc"]["result"]["segments"]]
        for row in t["rows"]:
            shares = [float(x.replace(",", ".")) for x in row["shares"]]
            assert abs(float(row["mixture"].replace(",", ".")) - float(np.dot(pi, shares))) < 0.15
        assert len(t["sizes"]) == 2 and abs(sum(float(z["sum"].replace(",", ".")) for z in t["sizes"]) - 120) < 0.2
    html = client.get("/surveys/1/results?" + "&".join(f"{k}={v}" for k, v in scenario)).data.decode()
    assert "Gesamt (Segmente)" in html and "nicht das Mittel der Segmentspalten" in html
    # with HB: the segment of a saved scenario in Word, and its size on the page
    app.config.update(HB_FAST)
    client.post("/surveys/1/results/hb/2", data={"source": "real"})
    client.post("/surveys/1/results/lc/2", data={"source": "real"})
    form = dict(scenario, sim_seg="2", sim_name="Nur zwei")
    client.post("/surveys/1/results/sim/2/save", data=form)
    client.post("/surveys/1/results/sim/2/save", data=dict(scenario, sim_seg="6", sim_name="Weg"))
    with app.app_context():
        from app import conjoint_hb, results as res, segments, simulator

        item = db.session.get(Item, 2)
        page = res.compute(db.session.get(Survey, 1), ("real",), False, True)
        entry = next(e for e in page["items"] if e["item"].id == 2)
        v = simulator.view(item, entry["cbc"], MultiDict(scenario + [("sim_seg", "2")]), "de")
        stored = segments.load(item)
        tau2 = [t[1] for t in segments.solution(stored, segments.shown_k(item, stored))["tau"]]
        size = v["segment_size"]
        assert abs(float(size["sum"].replace(",", ".")) - sum(tau2)) < 0.06
        w = np.array(tau2)
        assert abs(float(size["effective"].replace(",", ".")) - w.sum() ** 2 / (w ** 2).sum()) < 0.06
        on_page = {r["label"]["name"]: r["sop"] for r in v["rows"]}
        tables = simulator.word_tables(page)
        mine = next(x for x in tables if x[1] == "Nur zwei")
        assert {r["label"]["name"]: r["sop"] for r in mine[4]} == on_page        # the numbers of the page
        assert mine[6]["state"] == "weighted" and mine[6]["name"] == "Segment 2"
        gone = next(x for x in tables if x[1] == "Weg")
        assert gone[6]["state"] == "gone" and gone[3] == len(conjoint_hb.load(item)["pids"])
    from docx import Document

    doc = Document(io.BytesIO(client.get("/surveys/1/results/scales.docx?lang=de").data))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Szenario „Nur zwei“, Segment „Segment 2“" in text and "Summe der Zugehörigkeiten =" in text
    assert "Segment 6 ist nicht mehr vorhanden: alle Personen." in text
    html = client.get("/surveys/1/results?" + "&".join(f"{k}={v}" for k, v in scenario + [("sim_seg", "2")])).data.decode()
    assert "Segmentgröße ≈ Summe der Wahrscheinlichkeiten" in html
    # the effective number is the precision of the weighted mean, not persons (audit 2026-10-06, L-5)
    assert "effektive Fallzahl des gewichteten Mittels" in html and "effektive Fallzahl des gewichteten Mittels" in text


def test_smallest_segment_is_five_percent_or_fifty_persons_whichever_is_more():
    from app import latent_class as lc

    assert lc.too_small(0.15, 300) and not lc.too_small(0.17, 300)          # 50 persons count at 300
    assert lc.too_small(0.049, 2000) and not lc.too_small(0.05, 2000)       # 5 % count at 2000
    sols = [{"K": 1, "pi": np.array([1.0]), "tau": np.zeros((300, 1)), "found": 1, "runs": 1, "bic": 10.0},
            {"K": 2, "pi": np.array([0.85, 0.15]), "tau": np.zeros((300, 2)), "found": 3, "runs": 3, "bic": 5.0}]
    assert lc.suggest(sols) == 1                                          # 45 persons: too small to be suggested
    assert lc.STARTS == 50 and lc.K_MAX == 6


def test_latent_class_data_leaves_out_choices_outside_the_set():
    """A chosen index of -1 or past the alternatives would be read by NumPy as another alternative; it is left out,
    as in hb.Data (audit data storage and calculations 2026-10-21, D7)."""
    from app import latent_class as lc
    from app.choice_model import ChoiceSet

    X = [[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]
    good = [ChoiceSet(X, 0), ChoiceSet(X, 2)]
    data = lc.Data([good + [ChoiceSet(X, -1), ChoiceSet(X, 3)], good], 2)
    assert list(data.sets) == [2.0, 2.0]
    ll = data.person_ll(np.array([[0.5, -0.5]]))
    assert abs(ll[0, 0] - ll[1, 0]) < 1e-12
