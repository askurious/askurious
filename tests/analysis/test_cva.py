"""Ratings-based conjoint (app/items/cva.py, app/cva_design.py, app/cva_analysis.py; concept aca-cva-konzept.md)."""
import csv
import io
import random

import numpy as np
import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Response, Survey, new_pid
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"
ATTRS = {"a1": ("miete", "Miete", ["700 €", "900 €", "1100 €"]),
         "a2": ("lage", "Lage", ["Zentrum", "Stadtrand", "Land"]),
         "a3": ("balkon", "Balkon", ["ja", "nein"])}


def cva_form(form="single", points=11, slider=False, tasks="", versions=1, explain=False):
    rows = [("code", "wo"), ("form", form), ("points", str(points)), ("tasks", str(tasks)),
            ("versions", str(versions)), ("display", "table")]
    if slider:
        rows.append(("slider", "1"))
    if explain:
        rows.append(("explain", "1"))
    for aid, (code, label, levels) in ATTRS.items():
        rows += [("attr_id", aid), ("attr_code", code), ("attr_label", label)]
        for n, lv in enumerate(levels):
            rows += [(f"lvl_{aid}_id", f"{aid}l{n}"), (f"lvl_{aid}_label", lv), (f"lvl_{aid}_hint", ""),
                     (f"lvl_{aid}_def", "")]
    return MultiDict(rows)


def setup(client, **kw):
    fill_consent(client)
    add(client, "cva")                                         # item 2
    client.post(E + "/items/2", data=cva_form(**kw))
    client.post(E + "/items/2/conjoint/design")


TRUE = [[1.5, 0.0, -1.5], [1.0, 0.2, -1.2], [0.5, -0.5]]       # part-worths of the simulated persons


def rating(profile, true=TRUE, noise=0.0, rng=None):
    u = 5 + sum(true[a][lv] for a, lv in enumerate(profile))
    return max(0, min(10, round(u + (rng.gauss(0, noise) if rng else 0))))


# --- design --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("form", ["single", "pairs"])
def test_design_estimates_every_part_worth(form):
    from app import cva_design, doe

    levels = [3, 3, 2, 4]
    n = cva_design.suggested(levels)
    assert n == 2 * cva_design.n_params(levels) == 18
    plans = cva_design.generate(levels, n, form, 2, forbidden=[((0, 0), (1, 2))], seed=3)
    assert len(plans) == 2 and all(len(p) == n for p in plans)
    coder = doe._Coder(levels)
    for plan in plans:
        profiles = [p for t in plan for p in (t if form == "pairs" else [t])]
        assert not any(p[0] == 0 and p[1] == 2 for p in profiles)                  # the prohibition holds
        if form == "pairs":
            assert all(sum(x != y for x, y in zip(a, b)) >= 2 for a, b in plan)
            assert len({tuple(sorted((tuple(a), tuple(b)))) for a, b in plan}) == n
            X = np.array([np.r_[1, coder.row(a)[1:] - coder.row(b)[1:]] for a, b in plan])
        else:
            assert len({tuple(t) for t in plan}) == n
            X = coder.rows(plan)
        assert np.linalg.matrix_rank(X) == cva_design.n_params(levels)
    assert cva_design.efficiency(plans, levels, form) > 70
    assert cva_design.generate(levels, n, form, 2, forbidden=[((0, 0), (1, 2))], seed=3) == plans    # seeded
    with pytest.raises(ValueError):
        cva_design.pairs(levels, 5)                                              # fewer than the parameters


def test_regression_per_person_recovers_part_worths():
    from app import cva_analysis, cva_design

    levels = [3, 3, 2]
    plan = cva_design.generate(levels, 14, "single", 1, seed=1)[0]
    rows = [([1.0] + cva_analysis.effects_row(p, levels), 5 + sum(TRUE[a][lv] for a, lv in enumerate(p))) for p in plan]
    fit = cva_analysis.fit_person(rows, levels)
    assert fit["r2"] == pytest.approx(1.0) and fit["constant"] == pytest.approx(5.0)
    for got, want in zip(fit["pw"], TRUE):
        assert got == pytest.approx(want)
    assert fit["importance"][0] == pytest.approx(3.0 / 6.2) and sum(fit["importance"]) == pytest.approx(1)
    assert cva_analysis.fit_person(rows[:5], levels) is None                    # fewer tasks than parameters + 1
    flat = cva_analysis.fit_person([(x, 4.0) for x, _ in rows], levels)
    assert flat["flat"] and flat["r2"] is None
    # pairs: the difference of the two offers plus a pull to the first side
    pairs = cva_design.generate(levels, 14, "pairs", 1, seed=2)[0]
    rows = [([1.0] + [p - q for p, q in zip(cva_analysis.effects_row(a, levels), cva_analysis.effects_row(b, levels))],
             0.3 + sum(TRUE[k][a[k]] - TRUE[k][b[k]] for k in range(3))) for a, b in pairs]
    fit = cva_analysis.fit_person(rows, levels)
    assert fit["constant"] == pytest.approx(0.3) and fit["pw"][1] == pytest.approx(TRUE[1])


# --- editor --------------------------------------------------------------------------------------------------------


def test_editor_design_and_checks(app, client, survey):
    fill_consent(client)
    add(client, "cva")
    html = client.get(E + "/items/2").data.decode()
    assert "1 · Inhalt" in html and "Antwortform" in html and "Welche Conjoint-Art?" in html
    client.post(E + "/items/2", data=cva_form())
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.n_params(item) == 6 and item.kind.n_tasks(item) == 12
        assert "veraltet" in item.kind.validate(item)[0]
    html = client.post(E + "/items/2/conjoint/design", follow_redirects=True).get_data(as_text=True)
    assert "12 Aufgaben je Person" in html
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.design_current(item) and item.kind.validate(item) == [] and item.kind.design_warnings(item) == []
        assert item.config["design"]["eff"] > 70 and len(item.config["design"]["versions"][0]) == 12
    client.post(E + "/items/2", data=cva_form(tasks=8))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert not item.kind.design_current(item) and "1,5-Fache" in item.kind.design_warnings(item)[0]
    client.post(E + "/items/2", data=cva_form(tasks=6))
    assert "Mindestens 7 Aufgaben" in client.post(E + "/items/2/conjoint/design", follow_redirects=True).get_data(as_text=True)
    client.post(E + "/items/2", data=cva_form(form="pairs"))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.form(item) == "pairs" and item.config["points"] == 9
        assert item.kind.scale_points(item) == [4, 3, 2, 1, 0, -1, -2, -3, -4]
        assert item.text("question") == "Welches der beiden Angebote sagt Ihnen mehr zu?"


def test_parse_answer(app, client, survey):
    setup(client, points=7)
    with app.app_context():
        item = db.session.get(Item, 2)
        meta = {"task": 3, "v": 0}

        def parse(value, lang="de"):
            return item.kind.parse_answer(item, MultiDict({"i2_t3": value}), lang, meta=meta)

        assert parse("1") == {"t3": 1, "v": 0} and parse("7") == {"t3": 7, "v": 0} and parse("") == {"t3": None, "v": 0}
        for bad in ("0", "8", "x"):
            with pytest.raises(Exception, match="Ungültige"):
                parse(bad)
        item.config = {**item.config, "slider": True}
        assert parse("0") == {"t3": 0, "v": 0} and parse("100") == {"t3": 100, "v": 0}
        db.session.rollback()


# --- participation, export, results ----------------------------------------------------------------------------------


def test_participation_exports_and_results(app, client, survey):
    setup(client, explain=True)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    assert "Sie sehen gleich 12 Angebote nacheinander" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={"nav": "next"})
    with app.app_context():
        plan = db.session.get(Item, 2).config["design"]["versions"][0]
    for t, profile in enumerate(plan):
        html = p.get(f"/s/{uid}").data.decode()
        assert f"Aufgabe {t + 1} von 12" in html and f'name="i2_t{t}"' in html and "sehr wahrscheinlich" in html
        assert ATTRS["a2"][2][profile[1]] in html
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{t}": str(rating(profile))})
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert wide["wo_version"] == "1" and wide["wo_t1"] == str(rating(plan[0]))
    assert float(wide["wo_r2"]) > 0.9 and float(wide["wo_pw_miete_1"]) > 1 > -1 > float(wide["wo_pw_miete_3"])
    assert float(wide["wo_imp_miete"]) > float(wide["wo_imp_balkon"])
    long = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/conjoint/2.csv").data.decode("utf-8-sig"))))
    assert len(long) == 12 and list(long[0])[2:] == ["version", "task", "side", "miete", "lage", "balkon", "miete_label",
                                                     "lage_label", "balkon_label", "answer"]
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "wo_pw_miete_1" in book and "Effektkodierung" in book
    assert "Bewertungs-Conjoint (CVA) „wo“ (CSV)" in client.get("/surveys/1/data").data.decode()


def test_results_page_and_methods(app, client, survey):
    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        plan = db.session.get(Item, 2).config["design"]["versions"][0]
        rng = random.Random(4)
        for _ in range(9):
            answer = {"v": 0, **{f"t{t}": rating(prof, noise=0.8, rng=rng) for t, prof in enumerate(plan)}}
            s.responses.append(Response(pid=new_pid(s.code), answers={"2": answer}, layout={"cj": {"2": {"version": 0}}}, status=Response.COMPLETED))
        s.responses.append(Response(pid=new_pid(s.code), answers={"2": {"v": 0, **{f"t{t}": 5 for t in range(12)}}},
                                    layout={"cj": {"2": {"version": 0}}}, status=Response.COMPLETED))
        db.session.commit()
        from app import results

        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 2)
        cv = entry["cva"]
        assert entry["answered"] == 10 and cv["estimated"] == 10 and cv["flat"] == 1
        m = cv["model"]
        assert m["persons"] == 9 and m["t_df"] == 8
        miete = m["attributes"][0]
        assert miete["levels"][0]["beta"] == pytest.approx(1.5, abs=0.4) and miete["levels"][0]["lo"] < 1.5
        assert miete["importance"] > m["attributes"][2]["importance"] and len(m["charts"]) == 3
    html = client.get("/surveys/1/results").data.decode()
    assert "Wichtigkeit und Teilnutzen" in html and "R² je Person" in html and "Green, P. E., &amp; Srinivasan" in html
    methods = client.get("/surveys/1/methods").data.decode()
    assert "war eine Bewertungs-Conjoint" in methods and "Regression der Antworten" in methods


def test_pairs_page_and_dummy_data(app, client, survey):
    setup(client, form="pairs", versions=2)
    with app.app_context():
        from app import dummy, results

        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 30, seed=5) == 30
        db.session.commit()
        answers = [r.answers["2"] for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert all(all(-4 <= a[f"t{t}"] <= 4 for t in range(12)) for a in answers)
        m = next(e for e in results.compute(s, sources=("dummy",))["items"] if e["item"].id == 2)["cva"]["model"]
        assert m and m["persons"] >= 25
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    html = p.get(f"/s/{uid}").data.decode()
    assert "Welches der beiden Angebote sagt Ihnen mehr zu?" in html and "Angebot 1 viel lieber" in html
    assert 'value="-4"' in html and "Angebot 2" in html


def test_duration_timing_tempo_and_fact_sheet(app, client, survey):
    setup(client, explain=True)
    with app.app_context():
        from app import duration, tempo

        item = db.session.get(Item, 2)
        assert duration.item_seconds(item, "de") >= 12 * 5
        assert [c for c, _, _ in item.kind.timing_pages(item)] == ["_x"] + [f"_t{n}" for n in range(1, 13)]
        assert not item.kind.allowed_in_loop and item.kind.long_export == "cva" and item.kind.fact_sheet.group == "choice"
        assert tempo.task_words(item, "de", 0, 0) > 3
        assert tempo.page_kind([item], {"task": 0, "v": 0}) == "task"


def test_market_simulator_on_the_part_worths_per_person(app, client, survey):
    """C6: the simulator on the regression per person, first choice and share of preference with an exponent."""
    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        plan = db.session.get(Item, 2).config["design"]["versions"][0]
        rng = random.Random(7)
        for _ in range(12):
            answer = {"v": 0, **{f"t{t}": rating(prof, noise=0.8, rng=rng) for t, prof in enumerate(plan)}}
            s.responses.append(Response(pid=new_pid(s.code), answers={"2": answer}, layout={"cj": {"2": {"version": 0}}},
                                        status=Response.COMPLETED))
        db.session.commit()
        from werkzeug.datastructures import ImmutableMultiDict

        from app import results, simulator

        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 2)
        item = entry["item"]
        best = {"sim": "2", "sim_n": "2", "sim_0_0": "0", "sim_0_1": "0", "sim_0_2": "0",
                "sim_1_0": "2", "sim_1_1": "2", "sim_1_2": "1"}
        view = simulator.view(item, entry["cva"], ImmutableMultiDict(best), "de")
        assert view["basis"] == "persons" and view["persons"] == 12 and view["exp"] == "1"
        a, b = view["rows"]
        assert float(a["fc"].replace(",", ".")) == 100 and float(a["sop"].replace(",", ".")) > 90
        sharper = simulator.view(item, entry["cva"], ImmutableMultiDict({**best, "sim_exp": "0,3"}), "de")
        assert sharper["exp"] == "0.3" and float(sharper["rows"][0]["sop"].replace(",", ".")) < float(a["sop"].replace(",", "."))
        assert sharper["rows"][0]["fc"] == a["fc"]                       # first choice does not depend on the exponent
        assert ("sim_exp", "0.3") in sharper["state_params"]
        opt = simulator.view(item, entry["cva"], ImmutableMultiDict({**best, "sim_act": "opt", "sim_fixset": "1"}), "de")
        assert opt["opt"]["rows"][0]["levels"][:2] == ["700 €", "Zentrum"]
    html = client.get("/surveys/1/results").data.decode()
    assert "Marktsimulator" in html and "Teilnutzen je Person aus der Regression" in html and 'name="sim_exp"' in html


def test_dashboard_tile_and_quality_criterion(app, client, survey):
    """C8: the tile (importances, share of people whose best level it is) and the criterion qc_cva."""
    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        plan = item.config["design"]["versions"][0]
        rng = random.Random(9)
        for _ in range(8):
            answer = {"v": 0, **{f"t{t}": rating(prof, noise=0.5, rng=rng) for t, prof in enumerate(plan)}}
            s.responses.append(Response(pid=new_pid(s.code), answers={"2": answer}, layout={"cj": {"2": {"version": 0}}},
                                        status=Response.COMPLETED))
        db.session.commit()
        from app import export, quality
        from app.boards import base as board_base, tiles

        assert board_base.shape_of(item)[0] == "conjoint"
        tile = tiles._cva(item, export.rows(s), "de")
        miete = tile["attributes"][0]
        assert tile["n"] == 8 and tile["method"] == "persons" and tile["importances"][0]["label"] == "Miete"
        assert miete["levels"][0]["_share"] == pytest.approx(1.0) and miete["levels"][2]["_share"] == 0
        good = {"v": 0, **{f"t{t}": rating(prof) for t, prof in enumerate(plan)}}
        flat = {"v": 0, **{f"t{t}": 5 for t in range(12)}}
        noise = {"v": 0, **{f"t{t}": random.Random(t).randint(0, 10) for t in range(12)}}
        assert quality.cva_values({"2": good}, {2: item}) == 0
        assert quality.cva_values({"2": flat}, {2: item}) == 1
        assert quality.cva_values({"2": {"v": 0, "t0": 3}}, {2: item}) is None              # too few tasks answered
        assert ("qc_cva", quality.CVA_COLUMN[1]) in quality.columns([item])
        assert any(key == "cva" for key, _, _ in quality.FLAGS) and "cva" in quality.OPT_IN
        assert quality.cva_values({"2": noise}, {2: item}) in (0, 1)


# --- holdouts (stage 4) --------------------------------------------------------------------------------------------

HOLDOUTS = [[0, 0, 0], [2, 2, 1], [1, 0, 1]]                    # level index per attribute of each holdout offer


def holdout_form(form="single", holdouts=HOLDOUTS):
    rows = cva_form(form=form)
    rows.add("ho_count", str(len(holdouts)))
    for h, offer in enumerate(holdouts):
        sides = [offer, holdouts[(h + 1) % len(holdouts)]] if form == "pairs" else [offer]
        for j, profile in enumerate(sides):
            for (aid, _), lv in zip(ATTRS.items(), profile):
                rows.add(f"ho{h}_{j}_{aid}", f"{aid}l{lv}")
    return rows


def test_holdouts_editor_pages_export_and_results(app, client, survey):
    fill_consent(client)
    add(client, "cva")
    client.post(E + "/items/2", data=holdout_form())
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        assert kind.holdout_count(item) == 3 and kind.holdout_profiles(item, 1) == [[2, 2, 1]]
        assert kind.shown_tasks(item) == 15 and kind.validate(item) == []
        assert [p for _, p, _ in kind.timing_pages(item)][3] == "Holdout-Aufgabe 1"
    html = client.get(E + "/items/2").data.decode()
    assert "Holdout-Aufgaben" in html and 'name="ho2_0_a3"' in html and "ho0_1_a1" not in html
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
        plan = db.session.get(Item, 2).config["design"]["versions"][0]
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    order = [("t", 0), ("t", 1), ("t", 2), ("h", 0)] + [("t", t) for t in range(3, 12)] + [("h", 1), ("h", 2)]
    for n, (k, x) in enumerate(order, start=1):
        html = p.get(f"/s/{uid}").data.decode()
        assert f"Aufgabe {n} von 15" in html and f'name="i2_{k}{x}"' in html
        profile = plan[x] if k == "t" else HOLDOUTS[x]
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_{k}{x}": str(rating(profile))})
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.answers["2"]["h1"] == rating(HOLDOUTS[1]) and r.answers["2"]["t11"] == rating(plan[11])
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert wide["wo_ho1"] == str(rating(HOLDOUTS[0])) and float(wide["wo_r2"]) > 0.9
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "wo_ho2" in book and "Miete: 1100 €, Lage: Land, Balkon: nein" in book
    with app.app_context():
        from app import cva_analysis, results

        s = db.session.get(Survey, 1)
        rng = random.Random(2)
        for _ in range(8):
            answer = {"v": 0, **{f"t{t}": rating(prof, noise=0.6, rng=rng) for t, prof in enumerate(plan)},
                      **{f"h{h}": rating(prof, noise=0.6, rng=rng) for h, prof in enumerate(HOLDOUTS)}}
            s.responses.append(Response(pid=new_pid(s.code), answers={"2": answer}, layout={"cj": {"2": {"version": 0}}},
                                        status=Response.COMPLETED))
        db.session.commit()
        ho = next(e for e in results.compute(s)["items"] if e["item"].id == 2)["cva"]["holdout"]
        assert [row["n"] for row in ho["rows"]] == [9, 9, 9] and not ho["pairs"]
        assert ho["rows"][0]["mae"] < 1.5 and ho["rows"][0]["obs"] > ho["rows"][1]["obs"]
        assert ho["hit"]["n"] == 9 and ho["hit"]["hit"] > 0.8 and ho["hit"]["chance"] < 0.6
        fit = {"constant": 5.0, "pw": TRUE}
        assert cva_analysis.predict(fit, [[0, 0, 0]]) == pytest.approx(8.0)
        assert cva_analysis.predict(fit, [[0, 0, 0], [2, 2, 1]]) == pytest.approx(5.0 + 3.0 + 2.2 + 1.0)
    html = client.get("/surveys/1/results").data.decode()
    assert "Holdout-Aufgaben" in html and "Mittel vorhergesagt" in html and "Trefferquote" in html
    assert "Orme, B. K. (2009)" in html
    assert "feste Holdout-Aufgaben, die nicht in die Regression" in client.get("/surveys/1/methods").data.decode()


def test_holdouts_in_pairs_and_dummy_data(app, client, survey):
    fill_consent(client)
    add(client, "cva")
    client.post(E + "/items/2", data=holdout_form(form="pairs", holdouts=HOLDOUTS[:2]))
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        from app import dummy, results

        item = db.session.get(Item, 2)
        assert item.kind.holdout_profiles(item, 0) == [[0, 0, 0], [2, 2, 1]]
        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 25, seed=4) == 25
        db.session.commit()
        answers = [r.answers["2"] for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert all(-4 <= a["h0"] <= 4 and -4 <= a["h1"] <= 4 for a in answers)
        ho = next(e for e in results.compute(s, sources=("dummy",))["items"] if e["item"].id == 2)["cva"]["holdout"]
        assert ho["pairs"] and ho["hit"] is None and ho["rows"][0]["side"]["n"] > 0
    html = client.get(E + "/items/2").data.decode()
    assert 'name="ho1_1_a2"' in html and "Angebot 2" in html


# --- segments (stage 4) --------------------------------------------------------------------------------------------

MIRROR = [[-x for x in row] for row in TRUE]                     # the opposite taste


def test_latent_class_regression_finds_two_tastes():
    from app import latent_class as lc, lc_regression as lr

    rng = np.random.default_rng(3)
    truth = np.array([[5.0, 2.0, -1.0, 1.0, 0.5], [5.0, -2.0, 1.0, -0.5, 1.0]])
    persons, cls = [], []
    for n in range(150):
        c = int(n >= 90)
        X = np.column_stack([np.ones(12), rng.choice([-1, 0, 1], (12, 4))])
        persons.append({"X": X.tolist(), "y": (X @ truth[c] + rng.normal(0, 0.8, 12)).tolist()})
        cls.append(c)
    r = lr.estimate(persons, kmax=3, starts=10)
    assert [s["K"] for s in r["solutions"]] == [1, 2, 3] and lc.suggest(r["solutions"]) == 2
    two = r["solutions"][1]
    assert np.allclose(two["B"], truth, atol=0.2) and two["pi"][0] == pytest.approx(0.6, abs=0.02)
    assert ((np.asarray(two["tau"]).argmax(axis=1) == np.array(cls)).mean()) > 0.97
    assert two["sigma"][0] == pytest.approx(0.8, abs=0.1) and two["entropy"] > 0.9
    se = np.sqrt(np.diag(two["cov"]))[:5]
    assert np.all(se > 0) and np.all(se < 0.1) and two["cov"].shape == (2 * 5 + 1, 2 * 5 + 1)
    again = lr.estimate(persons, kmax=3, starts=10, finished=r["solutions"][:1])
    assert again["solutions"][1]["ll"] == pytest.approx(two["ll"])  # resumed after K = 1: the same result


def test_segments_page_export_simulator_and_methods(app, client, survey):
    from werkzeug.datastructures import MultiDict

    from tests.analysis.test_segments import LC_FAST

    setup(client)
    app.config.update(LC_FAST)
    with app.app_context():
        s = db.session.get(Survey, 1)
        plan = db.session.get(Item, 2).config["design"]["versions"][0]
        rng = random.Random(6)
        taste = {}
        for n in range(160):
            true = TRUE if n % 5 < 3 else MIRROR                      # 96 and 64: both above 50 persons
            pid = new_pid(s.code)
            taste[pid] = true is TRUE
            answer = {"v": 0, **{f"t{t}": rating(prof, true=true, noise=0.7, rng=rng) for t, prof in enumerate(plan)}}
            s.responses.append(Response(pid=pid, answers={"2": answer}, layout={"cj": {"2": {"version": 0}}},
                                        status=Response.COMPLETED))
        db.session.commit()
    html = client.get("/surveys/1/results").data.decode()
    assert "Segmente schätzen" in html and "Latent-Class-Regression der Bewertungen" in html
    r = client.post("/surveys/1/results/lc/2", data={"source": "real"})
    assert r.status_code == 302
    with app.app_context():
        from app import segments

        item = db.session.get(Item, 2)
        res = segments.load(item)
        assert res["type"] == "cva" and len(res["pids"]) == 160 and res["suggested"] == 2
        tau = np.array(segments.solution(res, 2)["tau"])
        first = np.array([taste[pid] for pid in res["pids"]])
        assert ((tau.argmax(axis=1) == 0) == first).mean() > 0.95      # the larger segment likes the cheap flat
        view = segments.summary(item, res, "de")
        rent = view["attributes"][0]["levels"][0]["values"]
        assert rent[0]["beta"] == pytest.approx(1.5, abs=0.3) and rent[1]["beta"] == pytest.approx(-1.5, abs=0.3)
        assert all(v["se"] is not None and v["se"] < 0.3 for v in rent) and len(view["sigma"]) == 2
    html = client.get("/surveys/1/results").data.decode()
    assert "Teilnutzen je Segment" in html and "Reststreuung je Segment" in html and "DeSarbo, W. S." in html
    assert "Latent-Class-Logit" not in html
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert {"wo_lk", "wo_lk_p1", "wo_lk_p2"} <= set(rows[0]) and {r["wo_lk"] for r in rows} == {"1", "2"}
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "Latent-Class-Regression" in book
    with app.app_context():
        from app import methods, results as res_mod, simulator

        page = res_mod.compute(db.session.get(Survey, 1), ("real",), False, True)
        entry = next(e for e in page["items"] if e["item"].id == 2)
        # the cheap flat in the centre against the dear one in the country
        scenario = [("sim", "2"), ("sim_n", "2"), ("sim_0_0", "0"), ("sim_0_1", "0"), ("sim_0_2", "0"),
                    ("sim_1_0", "2"), ("sim_1_1", "2"), ("sim_1_2", "0")]
        everyone = simulator.view(entry["item"], entry["cva"], MultiDict(scenario), "de")
        seg = everyone["segments"]
        assert seg["filter"] and seg["table"]["basis"] == "persons"
        a, b = (float(x.replace(",", ".")) for x in seg["table"]["rows"][0]["shares"])
        assert a > 70 and b < 30
        one = simulator.view(entry["item"], entry["cva"], MultiDict(scenario + [("sim_seg", "2")]), "de")
        assert one["segments"]["selected"] == 2 and one["segment_size"]
        text = methods.facts(db.session.get(Survey, 1))["choice"][0]["text_de"]
        assert "Latent-Class-Regression bestimmt (DeSarbo & Cron, 1988; n = 160" in text and "gewählt wurden 2 Segmente" in text


# --- ranking form (stage 4) ----------------------------------------------------------------------------------------


def test_ranking_page_answers_export_and_results(app, client, survey):
    setup(client, form="ranking", explain=True)
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        assert kind.form(item) == "ranking" and kind.design_form(item) == "single" and kind.n_tasks(item) == 12
        assert kind.scale_range(item) == (1, 12) and kind.timing_pages(item) == [("_x", "Erklärungsseite", ".x"),
                                                                                 ("_rk", "Rangfolge", "")]
        assert item.t("de")["question"].startswith("Bringen Sie die Angebote in eine Reihenfolge")
        plan = item.config["design"]["versions"][0]
        sig = item.config["design"]["sig"]
    html = client.get(E + "/items/2").data.decode()
    assert 'value="ranking" selected' in html and "Holdout-Aufgaben gibt es bei der Rangfolge nicht" in html
    client.post(E + "/items/2", data=cva_form(form="single", explain=True))   # back to single ratings: the same design
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.design_current(item) and item.config["design"]["sig"] == sig
    client.post(E + "/items/2", data=cva_form(form="ranking", explain=True))
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    assert "Sie sehen gleich 12 Angebote auf einer Seite" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={"nav": "next"})
    html = p.get(f"/s/{uid}").data.decode()
    assert html.count('class="p-rank-item"') == 12 and 'data-rank="2"' in html and "Miete: 700 €" in html
    order = sorted(range(12), key=lambda t: -sum(TRUE[a][lv] for a, lv in enumerate(plan[t])) + t * 1e-6)
    r = p.post(f"/s/{uid}", data=MultiDict([("nav", "next"), ("i2_touched", "1")] + [("i2", str(t)) for t in order]))
    assert r.status_code in (200, 302)
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        resp = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        a = resp.answers["2"]
        assert a[f"t{order[0]}"] == 1 and a[f"t{order[-1]}"] == 12 and a["v"] == 0
        assert item.kind.ranked_offers(item, 0, a)[0][0] == order[0]          # the page shows the order again
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert wide[f"wo_t{order[0] + 1}"] == "1" and float(wide["wo_r2"]) > 0.8
    assert float(wide["wo_pw_miete_1"]) > 0 > float(wide["wo_pw_miete_3"])
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "Rang des Angebots" in book
    methods = client.get("/surveys/1/methods").data.decode()
    assert "alle auf einer Seite in eine Reihenfolge zu bringen" in methods and "Regression der umgekehrten Ränge" in methods


def test_ranking_parse_dummy_and_paper(app, client, survey):
    setup(client, form="ranking")
    with app.app_context():
        from app import dummy, paper_pdf, results
        from app.items.base import AnswerError

        item = db.session.get(Item, 2)
        kind = item.kind
        meta = {"rank": True, "v": 0}
        item.answer_mode = "required"
        with pytest.raises(AnswerError):
            kind.parse_answer(item, MultiDict([("i2", str(t)) for t in range(12)]), "de", meta)   # never moved
        with pytest.raises(AnswerError):
            kind.parse_answer(item, MultiDict([("i2_touched", "1")] + [("i2", str(t)) for t in range(11)]), "de", meta)
        got = kind.parse_answer(item, MultiDict([("i2_touched", "1")] + [("i2", str(t)) for t in reversed(range(12))]),
                                "de", meta)
        assert got["t11"] == 1 and got["t0"] == 12 and got["v"] == 0
        item.answer_mode = "optional"
        assert kind.parse_answer(item, MultiDict(), "de", meta)["t0"] is None
        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 25, seed=2) == 25
        db.session.commit()
        answers = [r.answers["2"] for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert all(sorted(a[f"t{t}"] for t in range(12)) == list(range(1, 13)) for a in answers)
        cv = next(e for e in results.compute(s, sources=("dummy",))["items"] if e["item"].id == 2)["cva"]
        assert cv["model"] and cv["estimated"] == 25
        units = paper_pdf._cva_ranking(paper_pdf.Ctx(s, "de", {"cj": {"2": {"version": 0}}}), item, 0, None, meta)
        fields = [f for u in units for f in u.fields]
        assert len(units) == 13 and len(fields) == 12 and {f["rank"] for f in fields} == {str(t) for t in range(12)}
        assert all(f["f"] == "i2" and f["fmt"] == "int" for f in fields)
