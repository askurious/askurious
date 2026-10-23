"""Moderation in the structural equation model (stage 2 of the drawing, modell-zeichnen-konzept.md F2, Ralf
2026-10-11): an interaction term from product indicators (double mean centred matched pairs), PLS in two stages, a
moderator with two groups compared in two groups with equal loadings; planning, R script and the drawing's page."""
import math
import random

import numpy as np
from werkzeug.datastructures import MultiDict

from app import sem, sem_model, study_plan
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add, experiments_on
from tests.analysis.test_sem_survey import E, _scale

PLAN = "/surveys/1/researchdesign"


def _point(v):
    return max(1, min(7, int(round(4 + 1.2 * v))))


def _items(rng, f, k):
    return {f"s{i}": _point(0.75 * f + math.sqrt(1 - 0.5625) * rng.gauss(0, 1)) for i in range(1, k + 1)}


def _survey(app, client, n=400, group=False):
    """tb (2), einst (3), absicht (4) as scales, gruppe (5) with two answers; absicht = .3 tb + .2 einst + .25 tb·einst,
    or (group) the path tb -> absicht .1 in group 1 and .6 in group 2."""
    for number, code, k in ((2, "tb", 4), (3, "einst", 4), (4, "absicht", 3)):
        _scale(client, number, code, k)
    add(client, "single_choice")
    client.post(f"{E}/items/5", data=MultiDict({"code": "gruppe", "question": "Gruppe?", "option_id": ["a", "b"],
                                                "option_code": ["1", "2"], "option_label": ["A", "B"]}))
    rng = random.Random(3)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for _ in range(n):
            tb, einst = rng.gauss(0, 1), rng.gauss(0, 1)
            g = rng.random() < 0.5
            if group:
                b = 0.6 if g else 0.1
                absicht = b * tb + math.sqrt(1 - b * b) * rng.gauss(0, 1)
            else:
                absicht = 0.3 * tb + 0.2 * einst + 0.25 * tb * einst + 0.8 * rng.gauss(0, 1)
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers={
                "2": _items(rng, tb, 4), "3": _items(rng, einst, 4), "4": _items(rng, absicht / 1.1, 3),
                "5": "2" if g else "1"}))
        db.session.commit()


def _plan(client, wv, method="cb", direction="pos", effect=""):
    return client.post(PLAN, data=MultiDict({"h_effect": [effect],
        "h_id": [""], "h_text": ["Der Einfluss von TB auf die Absicht hängt ab"], "h_kind": ["moderation"],
        "h_dir": [direction], "h_iv": ["tb"], "h_dv": ["absicht"], "h_mv": [""], "h_wv": [wv], "h_analysis": [""],
        "sem_method": method}))


def test_an_interaction_of_two_constructs(app, client, survey):
    experiments_on(client, "sem")
    _survey(app, client)
    _plan(client, "einst", effect="0,15")
    with app.app_context():
        s = db.session.get(Survey, 1)
        h = study_plan.plan_of(s)["hypotheses"][0]
        assert h["kind"] == "moderation" and h["wv"] == "einst" and h["analysis"] == "sem"
        model = sem_model.build(s)
        name = "tb×einst"
        assert model["spec"]["products"][name]["kind"] == "ll"
        assert model["spec"]["latent"][name] == ["tb_1×einst_1", "tb_2×einst_2", "tb_3×einst_3", "tb_4×einst_4"] or \
            len(model["spec"]["latent"][name]) == 4
        assert set(model["spec"]["paths"]) == {("tb", "absicht"), ("einst", "absicht"), (name, "absicht")}
        assert model["hyps"][1]["paths"] == [(name, "absicht")]
        # planned with product indicators loading .49
        assert 200 < study_plan.power_of(s, h)["n"] < 5000
        text = sem_model.r_script(s, "daten.rds")
        assert "indProd(daten" in text and "doubleMC = TRUE" in text and "F_tb_einst =~" in text
    page = client.get("/surveys/1/results?go=1").data.decode()
    hyp = page[page.index('id="hypothesen"'):page.index('id="sem"')]
    assert "Unterstützt" in hyp and "einfache Steigungen" in hyp and "Produktindikatoren" in hyp
    part = page[page.index('id="sem"'):]
    assert "tb × einst" in part.replace("Skala ", "") or "×" in part
    # the interaction term is no construct of the measurement model
    assert "tb_1×einst_1" not in part
    # SEM extensions 2026-10-17, point 14: the Johnson-Neyman region in the note and under "Moderationen"
    assert "Johnson-Neyman: Der Pfad ist" in hyp and "Johnson-Neyman" in part


def test_pls_takes_the_interaction_in_two_stages(app, client, survey):
    experiments_on(client, "sem")
    _survey(app, client)
    _plan(client, "einst", method="pls")
    app.config["HB_SYNC"] = True
    client.post("/surveys/1/results?go=1")
    client.get("/surveys/1/results?go=1")
    client.post("/surveys/1/results/sem-bootstrap")
    page = client.get("/surveys/1/results").data.decode()
    hyp = page[page.index('id="hypothesen"'):page.index('id="sem"')]
    assert "zweistufig" in hyp and "Unterstützt" in hyp


def test_a_moderator_with_two_groups_is_compared_in_two_groups(app, client, survey):
    experiments_on(client, "sem")
    _survey(app, client, n=600, group=True)
    _plan(client, "gruppe")
    with app.app_context():
        s = db.session.get(Survey, 1)
        model = sem_model.build(s)
        assert ("tb", "absicht") in model["spec"]["paths"] and "gruppe" not in model["spec"]["observed"]
        g = model["groups"][1]
        assert g["w"] == "gruppe" and g["spec"]["paths"] == [("tb", "absicht")]
        h = study_plan.plan_of(s)["hypotheses"][0]
        assert sem_model.moderation_by_groups(s, h)
        h["effect"] = 0.3
        # planned in the model of the groups with loadings .70, the path at -.15 and +.15 (audit 2026-10-12, decision 9)
        assert study_plan.power_of(s, h)["n"] == sem.groups_n(g["spec"], {}, ("tb", "absicht"), [-0.15, 0.15])
        assert sem_model.moderation_groups(s, h) == 2
        text = sem_model.r_script(s, "daten.rds")
        assert 'group.equal = "loadings"' in text and "c(a1, b1)*F_tb" in text and "d1 := b1 - a1" in text
    page = client.get("/surveys/1/results?go=1").data.decode()
    hyp = page[page.index('id="hypothesen"'):page.index('id="sem"')]
    assert "Unterstützt" in hyp and "Δb = 0," in hyp and "Moderation durch Gruppen" in hyp


def test_two_groups_n_and_the_model_in_groups():
    # the planned moderation by groups (audit 2026-10-12, decision 9): the joint Wald test in the model of the groups.
    # Observed variables: about two independent correlations by Fisher (n per group (z/q)² · 2 + 3, here 175)
    plain = {"latent": {}, "observed": ["x", "y"], "paths": [("x", "y")], "indirect": []}
    q = 2 * math.atanh(0.15)
    fisher = 2 * math.ceil(2 * ((1.959964 + 0.841621) / q) ** 2 + 3)
    assert abs(sem.groups_n(plain, {}, ("x", "y"), [-0.15, 0.15]) - fisher) <= 10
    assert sem.groups_n(plain, {}, ("x", "y"), [0.1, 0.1]) is None
    # with loadings .70: an independent reference computation (Wald test on the population matrices, N = 100000)
    # gives the same noncentrality
    latent = {"latent": {"X": ["x1", "x2", "x3"], "Y": ["y1", "y2", "y3"]}, "observed": [], "paths": [("X", "Y")],
              "indirect": []}
    assert abs(sem.groups_noncentrality(latent, {}, ("X", "Y"), [-0.15, 0.15]) - 0.01207712) < 1e-7
    assert sem.groups_n(latent, {}, ("X", "Y"), [-0.15, 0.15]) == 650
    three = {"latent": {"X": ["x1", "x2", "x3"], "Y": ["y1", "y2", "y3"], "Z": ["z1", "z2", "z3"]}, "observed": [],
             "paths": [("X", "Y"), ("Z", "Y")], "indirect": []}
    assert abs(sem.groups_noncentrality(three, {("Z", "Y"): 0.3}, ("X", "Y"), [0.05, 0.2, 0.35]) - 0.0086423) < 1e-6
    assert sem.groups_n(three, {("Z", "Y"): 0.3}, ("X", "Y"), [0.05, 0.2, 0.35]) == 1116  # k - 1 = 2 df
    rng = np.random.default_rng(5)

    def data(n, b):
        x = rng.normal(size=n)
        y = b * x + np.sqrt(1 - b * b) * rng.normal(size=n)
        return np.column_stack([0.8 * f + 0.6 * rng.normal(size=n) for f in (x, x, x, y, y, y)])

    spec = {"latent": {"X": ["x0", "x1", "x2"], "Y": ["y0", "y1", "y2"]}, "observed": [], "paths": [("X", "Y")],
            "indirect": []}
    r = sem.fit_ml_groups([data(400, 0.2), data(400, 0.6)], sem.columns(spec), spec)
    assert r["df"] == 2 * 21 - (4 + 2 * 9) and r["p"] > 0.01
    d = r["diff"][("X", "Y")]
    assert 0.2 < d["est"] < 0.55 and d["p"] < 0.001
    same = sem.fit_ml_groups([data(400, 0.4), data(400, 0.4)], sem.columns(spec), spec)
    assert same["diff"][("X", "Y")]["p"] > 0.001


def test_what_cannot_be_moderated_yet(app, client, survey):
    experiments_on(client, "sem")
    _survey(app, client, n=20)
    client.post(PLAN, data=MultiDict({
        "h_id": ["", ""], "h_text": ["TB auf Einstellung", "Moderation"], "h_kind": ["effect", "moderation"],
        "h_dir": ["", ""], "h_iv": ["tb", "einst"], "h_dv": ["einst", "absicht"], "h_mv": ["", ""],
        "h_wv": ["", "absicht"], "h_analysis": ["", ""]}))
    with app.app_context():
        model = sem_model.build(db.session.get(Survey, 1))
        assert model["hyps"][2]["errors"] == ["roles"]  # the moderator is the outcome
    client.post(PLAN, data=MultiDict({
        "h_id": ["", ""], "h_text": ["TB auf Einstellung", "Moderation"], "h_kind": ["effect", "moderation"],
        "h_dir": ["", ""], "h_iv": ["tb", "einst"], "h_dv": ["einst", "absicht"], "h_mv": ["", ""],
        "h_wv": ["", "tb"], "h_analysis": ["", ""]}))
    with app.app_context():
        model = sem_model.build(db.session.get(Survey, 1))
        assert model["hyps"][2]["errors"] == []  # stage 3: a moderator explained in the model is fine


def test_the_page_offers_the_moderator(app, client, survey):
    experiments_on(client, "sem")
    _survey(app, client, n=5)
    _plan(client, "einst")
    page = client.get(PLAN).data.decode()
    assert "Moderation (verändert die Stärke eines Pfads)" in page and 'name="h_wv"' in page
    assert "positive Wechselwirkung (der Pfad wird mit dem Moderator größer)" in page
    with app.app_context():
        assert "Moderator einst" in study_plan.markdown(db.session.get(Survey, 1))
