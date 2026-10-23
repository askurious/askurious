"""The drawing of the model in the study plan (experimental feature "sem"; concept modell-zeichnen-konzept.md, Ralf
2026-10-11: F1 to F10 as recommended): the places of the variables in the plan, the kind "Einfluss", a relation
without a direction as a covariance in the model (F4), the drawn arrangement on the results page (F5), the page
and the line under the drawing."""
import json
import random

import numpy as np
from werkzeug.datastructures import MultiDict

from app import plan_model, power, sem, sem_model, study_plan
from app.extensions import db
from app.models import Survey
from tests.conftest import experiments_on
from tests.analysis.test_sem_survey import _survey

PLAN = "/surveys/1/researchdesign"


def _plan(client, kinds, ivs, dvs, analyses, layout=None, mvs=None):
    data = {"h_id": [""] * len(kinds), "h_text": [f"H {k}" for k in range(len(kinds))], "h_kind": kinds,
            "h_dir": ["pos"] * len(kinds), "h_iv": ivs, "h_dv": dvs, "h_mv": mvs or [""] * len(kinds),
            "h_analysis": analyses, "sem_method": "cb"}
    if layout is not None:
        data["plan_layout"] = json.dumps(layout)
    return client.post(PLAN, data=MultiDict(data))


def test_the_places_are_kept_cleaned_and_follow_a_renamed_variable(app, client, survey):
    experiments_on(client, "sem")
    _survey(app, client, n=10, plan=False)
    _plan(client, ["effect"], ["tb"], ["einst"], [""],
          layout={"v": 1, "nodes": {"tb": [100.4, 80], "einst": ["x", 1], "far": [99999, 0], "": [1, 1], "ok": [8, 16]}})
    with app.app_context():
        s = db.session.get(Survey, 1)
        plan = study_plan.plan_of(s)
        assert plan["layout"] == {"v": 1, "nodes": {"tb": [100, 80], "ok": [8, 16]}}
        # "Einfluss" is a path only the model tests
        assert plan["hypotheses"][0]["kind"] == "effect" and plan["hypotheses"][0]["analysis"] == "sem"
        assert study_plan.suggest(s, plan["hypotheses"][0]) == "sem"
        study_plan.rename_codes(s, {"tb": "tech"})
        db.session.commit()
        assert study_plan.plan_of(s)["layout"]["nodes"] == {"tech": [100, 80], "ok": [8, 16]}
        # a survey file keeps them (app/transfer.py)
        assert study_plan.clean({"layout": {"nodes": {"a": [1, 2]}}, "hypotheses": [
            {"text": "x", "kind": "effect", "iv": "a", "dv": "b", "analysis": "r"}]})["hypotheses"][0]["analysis"] == "sem"
        assert study_plan.clean({"layout": {"nodes": {"a": [1, 2]}}})["layout"] == {"v": 1, "nodes": {"a": [1, 2]}}
    # a form without the field (the setup wizard) keeps the stored places; moving them is no deviation
    _plan(client, ["effect"], ["tech"], ["einst"], [""])
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert study_plan.plan_of(s)["layout"]["nodes"] == {"tech": [100, 80], "ok": [8, 16]}
        before = study_plan.plan_of(s)
        after = {**before, "layout": {"v": 1, "nodes": {"tb": [0, 0]}}}
        assert study_plan._changes(before, after) == []


def test_a_covariance_between_residuals_is_estimated_and_refused_beside_a_path():
    rng = np.random.default_rng(3)
    n = 2000
    x = rng.normal(size=n)
    e = rng.multivariate_normal([0, 0], [[1, 0.4], [0.4, 1]], size=n)
    f = {"x": x, "y": 0.5 * x + e[:, 0], "z": 0.3 * x + e[:, 1]}
    cols, data = [], []
    for name, values in f.items():
        for k in range(3):
            cols.append(f"{name}{k}")
            data.append(0.8 * values + rng.normal(scale=0.6, size=n))
    data = np.column_stack(data)
    spec = {"latent": {v.upper(): [f"{v}{k}" for k in range(3)] for v in f}, "observed": [],
            "paths": [("X", "Y"), ("X", "Z")], "indirect": [], "cov": [("Y", "Z")]}
    with_cov = sem.fit_ml(data, sem.columns(spec), spec)
    without = sem.fit_ml(data, sem.columns(spec), {**spec, "cov": []})
    assert with_cov["df"] == without["df"] - 1 and with_cov["chi2"] < without["chi2"] / 5
    row = next(c for c in with_cov["covariances"] if {c["a"], c["b"]} == {"Y", "Z"})
    assert abs(row["std"] - 0.4) < 0.07 and row["p"] < 0.001 and row["ci"][0] < row["std"] < row["ci"][1]
    pls = sem.fit_pls(data, sem.columns(spec), spec)
    assert pls["covariances"][0]["std"] > 0.3
    boot = sem.bootstrap(data[:300], sem.columns(spec), spec, "cb", draws=40)
    assert boot["cov"][("Y", "Z")]["ci"][0] < boot["cov"][("Y", "Z")]["ci"][1]
    try:
        sem.check({**spec, "cov": [("Y", "X")]})
    except sem.SemError as e:
        assert e.reason == "covpath"
    else:
        raise AssertionError("a path and a covariance between the same two")
    # a model without covariances keeps its form, so a stored bootstrap of it stays valid
    assert "cov" not in sem.check({**spec, "cov": []})


def test_a_relationship_in_the_model_is_a_covariance_with_its_verdict(app, client, survey):
    experiments_on(client, "sem")
    _survey(app, client, plan=False)
    _plan(client, ["effect", "effect", "correlation"], ["tb", "tb", "einst"], ["einst", "absicht", "absicht"],
          ["", "", "sem"])
    with app.app_context():
        s = db.session.get(Survey, 1)
        model = sem_model.build(s)
        assert model["spec"]["paths"] == [("tb", "einst"), ("tb", "absicht")]
        assert model["spec"]["cov"] == [("einst", "absicht")] and model["hyps"][3]["cov"] == [("einst", "absicht")]
        h = study_plan.plan_of(s)["hypotheses"][2]
        assert any("ohne Richtung" in r for r in study_plan.reasons(s, h))
        # planned like a correlation r
        h["effect"] = 0.3
        assert study_plan.power_of(s, h)["n"] == power.needed("r", 0.3, 0.8, 0.05)
        text = sem_model.r_script(s, "daten.rds")
        assert "F_einst ~~ F_absicht" in text
    md = client.get("/surveys/1/export/methods-section.md").data.decode()
    assert "Kovarianz der Residuen" in md
    page = client.get("/surveys/1/results?go=1").data.decode()
    hyp = page[page.index('id="hypothesen"'):page.index('id="sem"')]
    assert hyp.count("Unterstützt") == 3 and "r = 0," in hyp and "Korrelation der Residuen" in hyp
    part = page[page.index('id="sem"'):]
    assert "↔" in part and "Bogen mit zwei Spitzen" in part and 'marker-start="url(#sem-arrow)"' in part


def test_a_relationship_beside_a_path_between_the_same_two_gets_no_result(app, client, survey):
    experiments_on(client, "sem")
    _survey(app, client, n=10, plan=False)
    _plan(client, ["effect", "correlation"], ["tb", "einst"], ["einst", "tb"], ["", "sem"])
    with app.app_context():
        model = sem_model.build(db.session.get(Survey, 1))
        assert model["hyps"][2]["errors"] == ["both"] and "cov" not in model["spec"]
        note = sem_model._error_note(model["hyps"][2]["errors"])
        assert "schon ein Pfad" in note


def test_the_results_draw_the_arrangement_of_the_plan():
    result = {"nodes": {"a": ("a", "a"), "b": ("b", "b"), "c": ("c", "c")}, "labels": {},
              "spec": {"paths": [("a", "c")], "latent": {}, "cov": [("a", "b")]},
              "fit": {"admissible": True, "r2": {}, "paths": [{"from": "a", "to": "c", "std": 0.3, "p": 0.01}],
                      "covariances": [{"a": "a", "b": "b", "std": 0.2, "p": 0.04}]},
              "layout": {"a": [100, 100], "b": [100, 400], "c": [700, 100]}}
    d = sem_model.diagram(result)
    pos = {n["name"]: (n["x"], n["y"]) for n in d["nodes"]}
    assert pos["a"][0] == pos["b"][0] and pos["a"][1] == pos["c"][1] and pos["b"][1] - pos["a"][1] == 300
    assert [e.get("cov", False) for e in d["edges"]] == [False, True] and d["edges"][1]["d"].count("Q") == 1
    # a path that would run through a variable bends around it
    result["layout"] = {"a": [100, 100], "b": [400, 100], "c": [700, 100]}
    result["spec"]["cov"], result["fit"]["covariances"] = [], []
    d = sem_model.diagram(result)
    assert "Q" in d["edges"][0]["d"]
    # one variable without a place: the automatic arrangement
    result["layout"] = {"a": [100, 100]}
    assert sem_model._drawn(result, ["a", "b", "c"], 200, 64) is None


def test_the_page_shows_the_drawing_to_everyone(app, client, survey):
    _survey(app, client, n=10, plan=False)
    page = client.get(PLAN).data.decode()
    assert "data-plan-model" in page and "model.js" in page and "Einfluss (gerichteter Pfad" in page
    _plan(client, ["effect", "mediation"], ["tb", "tb"], ["einst", "absicht"], ["", ""], mvs=["", "einst"])
    page = client.get(PLAN).data.decode()
    assert "data-plan-model" in page and "model.js" in page and 'name="plan_layout"' in page
    assert "Einfluss (gerichteter Pfad im Modell)" in page and "Bedienung mit der Tastatur" in page
    data = json.loads(page.split('data-model-data>', 1)[1].split("</script>", 1)[0])
    assert {v["code"] for v in data["vars"]} >= {"tb", "einst", "absicht"} and data["editable"] and data["max"] == 20
    tb = next(v for v in data["vars"] if v["code"] == "tb")
    assert tb["shape"] == "latent" and tb["sem"] and tb["info"] == "4 Aussagen" and len(tb["statements"]) == 4
    assert [k for k, _ in data["dirs"]["effect"]] == ["", "pos", "neg"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        line = plan_model.status(s)
        assert line["tested"] == 2 and line["q"] == sem_model.free_parameters(sem_model.build(s)["spec"])
    assert "Im Strukturgleichungsmodell: 2 Hypothesen" in page
    # not in the setup wizard
    assert "data-plan-model" not in client.get("/surveys/1/setup/researchdesign").data.decode()


def test_a_variable_that_cannot_enter_says_why(app, client, survey):
    from tests.conftest import add
    from tests.analysis.test_sem_survey import E, _scale

    experiments_on(client, "sem")
    _scale(client, 2, "tb", 4)
    add(client, "single_choice")
    client.post(f"{E}/items/3", data=MultiDict({"code": "gruppe", "question": "Gruppe?", "option_id": ["a", "b", "c"],
                                                "option_code": ["1", "2", "3"], "option_label": ["A", "B", "C"]}))
    with app.app_context():
        found = {v["code"]: v for v in plan_model.variables(db.session.get(Survey, 1))}
    # stage 3: more than two groups take part (dummies)
    assert found["gruppe"]["level"] == "nominal" and found["gruppe"]["groups"] == 3 and found["gruppe"]["sem"]
    assert found["gruppe"]["info"] == "3 Gruppen"
    random.seed(1)
