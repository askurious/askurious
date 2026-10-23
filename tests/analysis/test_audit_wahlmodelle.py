"""Audit of data and computations 2026-10-05, package H (choice models and experimental designs): W-1 to W-10,
H-8, H-11, M-13, M-14, S22, S23."""
import json
import os

import numpy as np
import pytest
from werkzeug.datastructures import MultiDict

from app import choice_model as cm
from app import choice_predict, conjoint_design, conjoint_hb, hb, hb_convergence, maxdiff_hb, simulator
from app.extensions import db
from app.models import Item, Response, Survey

LEVELS = [3, 3, 2, 4]                      # attribute 3: the price, linear
PRICES = [10.0, 20.0, 30.0, 40.0]
CENTER = sum(PRICES) / len(PRICES)
TRUE_PW = [np.array([0.8, 0.0, -0.8]), np.array([0.5, -0.2, -0.3]), np.array([0.4, -0.4])]


def simulate_cbc(n=150, tasks=10, sd=0.8, seed=11):
    """Choices of n persons with known individual utilities (part-worths spread with sd, price -0.05, "none" -0.5)."""
    rng = np.random.default_rng(seed)
    design, _ = conjoint_design.generate(LEVELS, tasks, 3, 10, n_starts=2, seed=seed)
    coding = cm.Coding(LEVELS, cm.price_scale(PRICES), asc=True, linear=(3,), price_center=CENTER)
    betas, persons = [], []
    for p in range(n):
        pw = []
        for t in TRUE_PW:
            d = rng.normal(0, sd, len(t))
            pw.append(t + d - d.mean())
        b = {"pw": pw, "price": -0.05, "asc": -0.5 + rng.normal(0, sd)}
        betas.append(b)
        sets = []
        for t in range(tasks):
            profiles = design[p % len(design)][t]
            u = [sum(b["pw"][a][pr[a]] for a in range(3)) + b["price"] * (PRICES[pr[3]] - CENTER) for pr in profiles]
            u.append(b["asc"])
            chosen = int(np.argmax(np.array(u) + rng.gumbel(size=len(u))))
            X = [coding.row(pr, PRICES[pr[3]]) for pr in profiles] + [coding.asc_row()]
            sets.append((np.array(X).tolist(), chosen))
        persons.append({"pid": f"p{p}", "sets": sets})
    inp = {"key": "k", "data_key": "d", "type": "conjoint", "settings": {"burn": 1000, "draws": 1000, "thin": 5, "seed": 1},
           "coding": {"levels": coding.levels, "price_scale": coding.price_scale, "asc": True, "linear": [3],
                      "price_center": coding.price_center},
           "price_range": (min(PRICES), max(PRICES)), "persons": persons}
    return inp, betas


def true_shares(betas, profiles, prices):
    U = np.array([[sum(b["pw"][a][p[a]] for a in range(3)) + b["price"] * (x - CENTER) for p, x in zip(profiles, prices)]
                  + [b["asc"]] for b in betas])
    P = np.exp(U - U.max(axis=1, keepdims=True))
    P /= P.sum(axis=1, keepdims=True)
    F = (U == U.max(axis=1, keepdims=True)).astype(float)
    return P.mean(axis=0), F.mean(axis=0)


# --- W-1: shares over the kept draws, not from the shrunken posterior means ---------------------------------------

def test_w1_shares_over_draws_lean_less_towards_the_majority(monkeypatch):
    # the simulated data follow the generated design; without the level balance term (math audit C-3) it is the
    # design these thresholds were set on
    monkeypatch.setattr(conjoint_design, "W_BAL", 0.0)
    inp, betas = simulate_cbc()
    res = conjoint_hb.run(inp)
    assert res["format"] == conjoint_hb.FORMAT and res["draws"]["shape"] == [100, 150, res["draws"]["shape"][2]]
    profiles, prices = [[0, 0, 0, 1], [1, 1, 1, 0], [2, 0, 1, 2]], [20.0, 12.5, 30.0]
    sop_true, fc_true = true_shares(betas, profiles, prices)
    # as before: from the posterior means
    models = choice_predict.hb_models(res)
    U = np.array([choice_predict.task_utilities(models[p], profiles, prices, True) for p in res["pids"]])
    sop_mean = np.array([choice_predict.shares(list(u)) for u in U]).mean(axis=0)
    fc_mean = np.array([choice_predict.first_choice(list(u)) for u in U]).mean(axis=0)
    # now: per person over the stored draws
    Ud = choice_predict.draw_utilities(conjoint_hb.draw_parameters(res), res["spans"], profiles, prices, True, CENTER)
    P, F = choice_predict.draw_shares(Ud)
    sop_draws, fc_draws = P.mean(axis=0), F.mean(axis=0)
    assert np.allclose(P.sum(axis=1), 1) and np.allclose(F.sum(axis=1), 1)
    # the leading product: the posterior means overstate it, the draws much less
    assert sop_mean[0] - sop_true[0] > 0.02 and fc_mean[0] - fc_true[0] > 0.05
    assert abs(sop_draws[0] - sop_true[0]) < 0.5 * abs(sop_mean[0] - sop_true[0])
    assert abs(fc_draws[0] - fc_true[0]) < 0.5 * abs(fc_mean[0] - fc_true[0])


def test_w1_draws_are_packed_small_and_read_back():
    B = np.random.default_rng(1).normal(size=(100, 30, 7)) * 3
    packed = hb.pack_draws(B)
    back = hb.unpack_draws(packed)
    assert back.shape == B.shape and np.abs(back - B).max() < 0.01        # float16: three significant digits
    assert len(json.dumps(packed)) < 100 * 30 * 7 * 2 * 1.4 + 200
    assert hb.unpack_draws(None) is None and hb.pack_draws(None) is None


def test_w1_simulator_interval_is_labelled_as_spread_over_persons():
    groups = simulator.sources({1: {"basis": "hb", "rows": [1]}})
    text = " ".join(g for g, _ in groups)
    assert "nur die Stichprobenstreuung über Personen" in text and "behaltenen Ziehungen" in text
    assert any(hb.ROSSI in cited for _, cited in groups)
    assert "nur die Stichprobenstreuung über Personen" in simulator.DOCX["de"]["hb"]
    assert "sampling variation over people only" in simulator.DOCX["en"]["hb"]


# --- W-2: the sampler ---------------------------------------------------------------------------------------------

def test_w2_step_sizes_per_person_and_group_moves():
    inp, _ = simulate_cbc(n=60, tasks=10, seed=3)
    coding = conjoint_hb._coding(inp["coding"])
    sets = [[cm.ChoiceSet(X, c) for X, c in p["sets"]] for p in inp["persons"]]
    r = hb.estimate(sets, coding.k, burn=600, draws=1200, thin=2, seed=2, prior_cov=conjoint_hb._prior(coding))
    assert hb.ACCEPT_OK[0] < r["acceptance"] < hb.ACCEPT_OK[1]               # Langevin steps near hb.TARGET
    assert 0.1 < r["shift_acceptance"] < 0.95
    assert r["draws"].shape == (100, 60, coding.k) and r["kept"] == 600
    # mixing: the old random walk reached an ESS of 15 to 70 per 1000 kept draws on such data (thinned by 10)
    ess = [hb_convergence.ess(r["mu_trace"][:, j]) for j in range(coding.k)]
    assert min(ess) > 60, ess


def test_w2_gradient_and_information_match_numerical_derivatives():
    inp, _ = simulate_cbc(n=20, tasks=6, seed=6)
    coding = conjoint_hb._coding(inp["coding"])
    sets = [[cm.ChoiceSet(X, c) for X, c in p["sets"]] for p in inp["persons"]]
    data = hb.Data(sets, coding.k)
    B = np.random.default_rng(0).normal(size=(20, coding.k)) * 0.5
    ll, G = data.loglik_grad(B)
    assert np.allclose(ll, data.loglik(B))
    H = data.information(B)
    for j in range(coding.k):
        e = np.zeros(coding.k)
        e[j] = 1e-6
        assert np.allclose((data.loglik(B + e) - data.loglik(B - e)) / 2e-6, G[:, j], atol=1e-4)
        assert np.allclose((data.loglik_grad(B + e)[1] - data.loglik_grad(B - e)[1]) / 2e-6, -H[:, :, j], atol=1e-4)


def test_w2_convergence_also_judges_the_shown_means():
    rng = np.random.default_rng(5)
    good = rng.standard_normal((1000, 2))
    stuck = np.cumsum(rng.standard_normal(1000)) * 0.1                       # a random walk: hardly any ESS
    d = hb_convergence.diagnose(good, rng.standard_normal(1000), shown=np.column_stack([good[:, 0], stuck]))
    assert d["ess_shown"] < 100 and d["ess_min"] == d["ess_shown"] and not d["diag_ok"]
    assert hb_convergence.diagnose(good, rng.standard_normal(1000), shown=good)["diag_ok"]
    assert "Geweke-artiger Vergleich" in hb_convergence.warning({"diag_ok": False, "geweke_ok": False})


def test_w2_acceptance_warning_uses_the_new_target():
    assert hb.acceptance_warning(0.55) is None
    text = hb.acceptance_warning(0.2)
    assert "0,20" in text and f"{hb.TARGET:.2f}".replace(".", ",") in text and text.endswith("mischt schlecht.")


# --- W-3: vignettes with a factor only between persons ------------------------------------------------------------

def test_w3_deck_figures_only_over_the_factors_within_persons():
    import random

    from app import doe, vignettes

    node = {"attributes": [{"id": "a", "code": "g", "between": True, "levels": [{"id": "1"}, {"id": "2"}]},
                           {"id": "b", "code": "x", "levels": [{"id": "1"}, {"id": "2"}, {"id": "3"}]},
                           {"id": "c", "code": "y", "levels": [{"id": "1"}, {"id": "2"}]}],
            "plan": {"method": "doptimal", "per_person": 3, "decks": 4}}
    levels = [2, 3, 2]
    profiles, decks, n = vignettes._split_plan(node, levels, [], node["plan"], random.Random(1))
    ordered = [profiles[i] for d in decks for i in d]
    rows = [list(range(t * 3, (t + 1) * 3)) for t in range(n)]
    old = doe.stats(ordered, levels, rows)
    assert old["deck_eff"] == 0.0 and old["decks_missing_level"] == 4      # what editor and methods said before
    new = doe.stats(ordered, levels, rows, between=[0])
    assert new["deck_within"] and new["deck_eff"] > 90 and new["decks_missing_level"] == 0
    assert new["deck_eff"] == round(doe.deck_efficiency([[r[1], r[2]] for r in ordered], [3, 2], 3), 1)
    # a plan stored before (without "deck_within") is figured anew
    node["plan"].update(profiles=ordered, decks_rows=[[f"p{i}" for i in r] for r in rows], stats=old)
    assert vignettes.plan_stats(node)["deck_eff"] == new["deck_eff"]


def test_w3_editor_codebook_and_methods_name_the_within_factors(app, client, survey):
    from app import vignettes
    from tests.analysis.test_vignettes import FORM, _add, _node

    _add(client)
    node = _node(app)
    page = f"/surveys/1/flow/vignettes/{node['id']}"
    client.post(page, data={**FORM, "attr_between": ["a3"], "per_person": "3", "decks": "4"})
    client.post(page, data={"action": "generate"})
    node = _node(app)
    stats = node["plan"]["stats"]
    assert stats["deck_within"] and stats["deck_eff"] > 50
    html = client.get(page).get_data(as_text=True)
    assert "D-Effizienz in den Decks (Faktoren innerhalb der Personen)" in html
    assert "Fedorov, V. V. (1972). <i>Theory of optimal experiments</i>. Academic Press." in html  # W-10, APA (#499)
    assert 'href="#ref-kuhfeld1994-efficient">Kuhfeld et al., 1994</a>' in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert "(nur Faktoren innerhalb der Personen)" in vignettes.codebook_note(s, node)
        text = vignettes.methods_text(s, node)
        assert "berechnet über die Faktoren innerhalb der Personen" in text and "Decks 0,0" not in text
        assert "over the within-person factors" in vignettes.methods_text_en(s, node)


# --- W-4: notes of the model in the simulator ---------------------------------------------------------------------

def test_w4_simulator_repeats_the_model_notes():
    pooled = {"model": {"price": {"beta": 0.001}, "wtp_ok": False, "converged": True,
                        "attributes": [{"label": "Aussehen", "loose": True}, {"label": "Ort", "loose": False}]}}
    notes = simulator._notes(pooled, "pooled")
    assert any("nicht deutlich negativ" in n for n in notes) and any("Aussehen" in n and "Ridge" in n for n in notes)
    assert not any("Ort" in n for n in notes)
    assert simulator._notes({"model": {"price": {"beta": -0.05}, "wtp_ok": True, "attributes": []}}, "pooled") == []
    hb_entry = {"hb": {"result": {"warnings": ["Die Kette ist möglicherweise nicht konvergiert."],
                                  "price": {"negative": 0.9}}}}
    notes = simulator._notes(hb_entry, "hb")
    assert notes[0].startswith("Die Kette") and "Bei 10,0 % der Personen ist der Preiskoeffizient positiv" in notes[1]


# --- W-5, H-11: pre-launch check ----------------------------------------------------------------------------------------

def test_w5_h11_check_warns_on_d_efficiency_zero_and_few_tasks(app, client, survey):
    from app import design
    from tests.analysis.test_conjoint import setup

    setup(client)                                     # 4 tasks x 1 version x 2 alternatives for 5 part-worths
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.design_efficiency(item) == 0
        warnings = item.kind.check_warnings(item)
        assert any(w.startswith("D-Effizienz 0") for w in warnings)
        assert any("Nur 4 Auswahlaufgaben je Person" in w for w in warnings)
        found = [f.message for f in design.check(db.session.get(Survey, 1))]
        assert any("D-Effizienz 0" in m for m in found)


def test_h11_hb_block_warns_on_few_tasks_per_person():
    inp, _ = simulate_cbc(n=30, tasks=4, seed=4)
    inp["settings"] = {"burn": 100, "draws": 100, "thin": 2, "seed": 1}
    res = conjoint_hb.run(inp)
    res.update(created="2026-10-05T12:00:00+00:00", pool={})

    class Kind:
        @staticmethod
        def attributes(item, lang=None):
            return [{"code": c, "label": c, "level_labels": [f"{c}{i}" for i in range(n)]}
                    for c, n in zip("ABCP", LEVELS)]

        @staticmethod
        def price_attribute(item):
            return 3

        @staticmethod
        def level_numbers(item, a):
            return [10.0 * (k + 1) for k in range(LEVELS[a])]

    class FakeItem:
        type = "conjoint"
        kind = Kind()

    view = conjoint_hb.summary(FakeItem(), res, "de")
    assert any("nur 4,0 Auswahlaufgaben je Person" in w for w in view["warnings"])
    assert len(view["price_levels"]["levels"]) == LEVELS[3]  # the linear price as bars too (2026-10-22)
    assert view["target"] == hb.TARGET


# --- W-6: weighted results page -------------------------------------------------------------------------------------

def test_w6_hb_stays_current_on_the_weighted_page(app, client, survey, monkeypatch):
    from app import results
    from tests.analysis.test_simulator import _hb

    _hb(app, client)

    def half(survey, table):
        ids = [r["resp_id"] for r in table]
        return {"weights": {i: 2.0 for i in ids[::2]}}

    monkeypatch.setattr(results, "_weighting", half)
    with app.app_context():
        data = results.compute(db.session.get(Survey, 1), drop_speeders=True, weighted=True)
        entry = next(e for e in data["items"] if e["item"].id == 2)["cbc"]
        assert data["weighting"]["left_out"] == 20
        assert entry["hb"]["state"] == "current"                # was "stale": the key came from the weighted rows
        sims = simulator.page(data, MultiDict(), weighted=True)
        assert sims[2]["basis"] == "hb" and sims[2]["weighted"] and sims[2]["persons"] == 20


# --- W-7: price levels with a dot and three decimals -----------------------------------------------------------------

def test_w7_dot_with_three_decimals_follows_the_house_rule():
    # decisions: "1.000" is a thousand, as in conditions and computed values; a leading zero can only be decimals
    from app.items.attributes import parse_number, price_problem

    assert parse_number("0.500 €") == 0.5 and parse_number("1.000 €") == 1000
    assert price_problem("1.000 €") is None and price_problem("0.500 €") is None and price_problem("1000 €") is None


# --- W-9: one run at a time -----------------------------------------------------------------------------------------

def test_w9_start_marker_is_claimed_exclusively(tmp_path):
    paths = (str(tmp_path / "item_1.json"), str(tmp_path / "item_1.run.json"))
    inp = {"key": "k"}
    assert maxdiff_hb.claim(paths, inp, {}) is True
    assert maxdiff_hb.claim(paths, inp, {}) is False                         # a run is marked: no second one
    os.remove(paths[1])
    open(paths[1] + ".lock", "w").close()                                     # another request is starting one
    assert maxdiff_hb.claim(paths, inp, {}) is False
    os.utime(paths[1] + ".lock", (1_000_000_000, 1_000_000_000))              # left over from a killed process
    assert maxdiff_hb.claim(paths, inp, {}) is True and not os.path.exists(paths[1] + ".lock")
    with open(paths[1], "w", encoding="utf-8") as f:                          # a failed run may be started again
        json.dump({"state": "error", "started": "2026-10-05T10:00:00+00:00"}, f)
    assert maxdiff_hb.claim(paths, inp, {}) is True


# --- M-14: dummy data from a utility model --------------------------------------------------------------------------

def test_m14_cbc_dummies_follow_a_utility_model(app, client, survey):
    from app import dummy
    from tests.analysis.test_cbc_results import setup

    setup(client, price_attr=True, none=True, tasks=12, versions=6)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 150, seed=3) == 150
        db.session.commit()
        item = db.session.get(Item, 2)
        pa = item.kind.price_attribute(item)
        numbers = item.kind.level_numbers(item, pa)
        cheaper = chance = total = 0
        for r in db.session.scalars(db.select(Response).filter_by(survey_id=1, source="dummy")):
            v = r.layout["cj"]["2"]["version"]
            for t in range(12):
                choice = r.answers["2"][f"t{t}"]
                if not choice:
                    continue
                prices = [numbers[p[pa]] for p in item.config["design"]["versions"][v][t]]
                if len(set(prices)) > 1:
                    total += 1
                    cheaper += prices[choice - 1] == min(prices)
                    chance += prices.count(min(prices)) / len(prices)    # what a random choice among them would give
        # every dummy dislikes higher prices: the cheapest option wins clearly more often than random choices would
        assert total > 750 and cheaper / total > chance / total + 0.05  # the design itself is drawn at random


def test_m14_acbc_dummies_keep_their_utilities(app, client, survey):
    import random

    from app import dummy
    from app.items.conjoint import dummy_person
    from tests.analysis.test_acbc import setup as acbc_setup

    acbc_setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 8, seed=2) == 8
        db.session.commit()
        item = db.session.get(Item, 2)
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(survey_id=1))]
        assert all(a and a.get("s") for a in answers)
        rng = random.Random(1)
        a, b = dummy_person(item.kind, item, rng, 1), dummy_person(item.kind, item, rng, 1)
        assert a is b and dummy_person(item.kind, item, rng, 2) is not a


# --- S22, S23, H-8, M-13, W-10 ---------------------------------------------------------------------------------------

def test_s22_display_settings_leave_the_hb_key_alone(app, client, survey):
    from tests.analysis.test_simulator import _hb

    _hb(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert conjoint_hb.current(item) is not None
        item.config = {**item.config, "display": "both", "shuffle_alts": True, "auto_next": True, "stack_mobile": True}
        assert conjoint_hb.current(item) is not None
        item.config = {**item.config, "alternatives": item.config["alternatives"]}  # structure untouched: still current
        assert conjoint_hb.current(item) is not None


def test_s23_hb_columns_only_for_a_fitting_pool_and_speeder_rule_in_codebook(app, client, survey):
    import csv
    import io

    from tests.analysis.test_maxdiff_hb import with_dummies

    with_dummies(app, client)
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "drop": "1"})
    real = client.get("/surveys/1/export/data.csv?source=real").data.decode("utf-8-sig").splitlines()[0]
    assert "md_hb_util1" not in real                                          # the estimate holds no real persons
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?source=dummy").data.decode("utf-8-sig"))))
    assert rows and rows[0].get("md_hb_util1") not in (None, "")
    book = client.get("/surveys/1/export/codebook.csv?source=dummy").data.decode("utf-8-sig")
    line = next(l for l in book.splitlines() if l.startswith("md_hb_rlh"))
    assert "ohne markierte Teilnahmen (Qualitätssicherung; Speeder:" in line
    book_real = client.get("/surveys/1/export/codebook.csv?source=real").data.decode("utf-8-sig")
    assert "md_hb_rlh" not in book_real


def test_h8_codebook_names_the_price_range_of_all_persons():
    res = {"type": "acbc", "has_price_importance": True, "settings": {"burn": 1, "draws": 1, "thin": 1, "seed": 1},
           "created": "2026-10-05T12:00:00+00:00", "pids": ["a"], "pool": {}}

    class FakeItem:
        code = "ac"
        survey = None

    _, _, notes = conjoint_hb.codebook_info(FakeItem(), "ac_hb_imp_price", res)
    assert "aller gezeigten Konzepte aller Personen" in notes[1]


def test_m13_disconnected_maxdiff_names_the_anchor():
    from app import maxdiff_analysis

    versions = [[[0, 1], [0, 1]], [[2, 3], [2, 3]]]                          # two groups that never meet
    answers = [(0, {0: (0, 1), 1: (1, 0)}), (1, {0: (2, 3), 1: (3, 2)})]
    with_anchor = maxdiff_analysis.mnl(4, versions, answers, [[1, 0, 1, 0], [0, 1, 0, 1]], list("ABCD"), 2)
    assert with_anchor["disconnected"] and "Auch die Ankerfrage verbindet die Gruppen hier nicht" in with_anchor["warnings"][0]
    without = maxdiff_analysis.mnl(4, versions, answers, [None, None], list("ABCD"), 2)
    assert "Ankerfrage" not in without["warnings"][0]


def test_w10_sources_on_the_results_page(app, client, survey):
    from tests.analysis.test_simulator import _hb

    _hb(app, client)
    html = client.get("/surveys/1/results").data.decode()
    assert "Geweke, J. (1992). Evaluating the accuracy of sampling-based approaches" in html
    assert "Roberts, G. O., Gelman, A., &amp; Gilks, W. R. (1997)" in html and "10.1214/aoap/1034625254" in html
    assert "Roberts, G. O., &amp; Rosenthal, J. S. (1998)" in html and "Liu, J. S., &amp; Sabatti, C. (2000)" in html
    assert "1,2 × Zufall für mögliche Zufallsklicker ist eine Festlegung von Askurious" in html
    assert "Rossi, P. E., Allenby, G. M., &amp; McCulloch, R. (2005)" in html
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert "Herleitung von Askurious" in page


def test_w10_ridge_and_icc_sources():
    from app import choice_model, results, vignettes

    groups = results.page_sources({"items": [{"cbc": {"model": {"ridge": True, "attributes": [{}]}}}],
                                   "vignettes": [{}]})
    cited = [c for _, cs in groups for c in cs]
    assert choice_model.ALBERT_ANDERSON in cited and "10.1093/biomet/71.1.1" in choice_model.ALBERT_ANDERSON
    assert vignettes.SHROUT_FLEISS in cited and vignettes.DONNER in cited


def test_new_sources_read_as_apa_and_cited_short_in_the_text(app, client, survey):
    """PR #499: short references via cite(), full citations in the APA list."""
    from app import apa, choice_model, doe, hb, vignettes
    from tests.analysis.test_simulator import _hb

    expected = {choice_model.ALBERT_ANDERSON: "Albert & Anderson, 1984", hb.GEWEKE: "Geweke, 1992",
                doe.FEDOROV: "Fedorov, 1972", vignettes.SHROUT_FLEISS: "Shrout & Fleiss, 1979",
                vignettes.DONNER: "Donner, 1986", hb.ROSSI: "Rossi et al., 2005",
                hb_convergence.VEHTARI: "Vehtari et al., 2021", hb.ROBERTS_GELMAN_GILKS: "Roberts et al., 1997",
                hb.ROBERTS_ROSENTHAL: "Roberts & Rosenthal, 1998", hb.LIU_SABATTI: "Liu & Sabatti, 2000"}
    for source, short in expected.items():
        assert apa.ref(source)["short"] == short
    _hb(app, client)
    html = client.get("/surveys/1/results?sim=2").data.decode()
    assert 'class="flash flash-warning sim-notes"' in html and "Hinweise des Modells" in html  # W-4; 40 persons: "Nur 40"
    assert "(Vehtari et al., 2021; Geyer, 1992)" not in html and "Spektraldichten; Geweke" not in html
    assert '>Vehtari et al., 2021</a>' in html and '>Geweke, 1992</a>' in html          # convergence: cite()
    assert '>Roberts &amp; Rosenthal, 1998</a>' in html                                  # sampler sentence
    assert '>Rossi et al., 2005</a>' in html and '>Efron &amp; Tibshirani, 1993</a>' in html  # simulator
