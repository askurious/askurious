"""Constructs of a higher order with PLS (higher-order constructs 2026-10-26): disjoint two-stage (Becker et al.,
2012; Sarstedt et al., 2019), an instrument with subscales or several scales together, reflective or formative."""
import math
import random

import numpy as np
from werkzeug.datastructures import MultiDict

from app import sem, sem_model, study_plan
from app.extensions import db
from app.models import Response, Survey
from tests.analysis.test_sem_survey import E, _scale, _survey
from tests.conftest import add


def _data(n=500, seed=2):
    rng = np.random.default_rng(seed)
    h = rng.normal(size=n)
    lows = [0.8 * h + 0.6 * rng.normal(size=n) for _ in range(3)]
    y = 0.5 * h + math.sqrt(0.75) * rng.normal(size=n)
    cols, data = [], []
    for j, f in enumerate(lows + [y]):
        for i in range(3):
            cols.append(f"v{j}_{i}")
            data.append(0.8 * f + 0.6 * rng.normal(size=n))
    spec = {"latent": {"L0": cols[0:3], "L1": cols[3:6], "L2": cols[6:9], "Y": cols[9:12]}, "observed": [],
            "higher": {"H": ["L0", "L1", "L2"]}, "paths": [("H", "Y")], "indirect": []}
    return np.column_stack(data), cols, spec


def test_disjoint_two_stage_reflective_and_formative():
    x, cols, spec = _data()
    r = sem.fit_pls(x, cols, spec)
    assert r["converged"] and set(r["loadings"]) == {"L0", "L1", "L2", "Y"}
    assert [row["item"] for row in r["higher"]["H"]] == ["L0", "L1", "L2"]
    assert all(row["std"] > 0.7 for row in r["higher"]["H"]) and r["higher_quality"]["H"]["cr"] > 0.8
    (path,) = r["paths"]
    assert 0.3 < path["std"] < 0.6  # the true relation of H and Y is .5, attenuated by the composites
    assert ("L0", "L1") not in r["quality"]["htmt"]  # two parts of one construct are meant to be related
    spec["formative"] = ["H"]
    r = sem.fit_pls(x, cols, spec)
    assert len(r["formative"]["H"]) == 3 and "H" in r["loadings"] and not r["higher"]
    boot = sem.bootstrap(x, cols, spec, "pls", draws=40)
    assert boot["failed"] == 0 and set(boot["weights"]) == {("H", "L0"), ("H", "L1"), ("H", "L2")}
    assert sem.pls_predict(x, cols, spec)["constructs"]["Y"] == "high"


def test_several_scales_as_one_construct_from_the_plan(app, client, survey, monkeypatch):
    """tb and einst together as "Haltung" (formative), a hypothesis naming tb then means Haltung -> absicht."""
    _survey(app, client, n=300, plan=False)
    form = {"research_question": "TB", "goal": "correlation", "design": "cross",
            "h_id": [""], "h_text": ["Haltung wirkt auf Absicht"], "h_kind": ["effect"], "h_dir": ["pos"],
            "h_iv": ["tb"], "h_dv": ["absicht"], "h_mv": [""], "h_analysis": ["sem"], "sem_method": "pls",
            "sem_shown": "1"}
    client.post("/surveys/1/researchdesign", data=MultiDict(form))
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert 'name="hoc_name_1"' in page and 'name="hoc_part_1" value="einst"' in page
    form.update(hoc_name_1="Haltung", hoc_part_1=["tb", "einst"], sem_hmode_hoc1="formative")
    client.post("/surveys/1/researchdesign", data=MultiDict(form))
    with app.app_context():
        s = db.session.get(Survey, 1)
        opts = study_plan.plan_of(s)["sem"]
        assert opts["hoc"] == [{"id": "hoc1", "name": "Haltung", "parts": ["tb", "einst"]}]
        assert opts["higher_formative"] == ["hoc1"]
        model = sem_model.build(s)
        assert model["spec"]["higher"] == {"hoc1": ["tb", "einst"]} and model["spec"]["paths"] == [("hoc1", "absicht")]
        assert model["spec"]["formative"] == ["hoc1"]
        assert "Haltung = tb + einst (formativ)" in study_plan.markdown(s, "de")
        assert 'higher_composite("F_hoc1", c("F_tb", "F_einst"), method = two_stage, weights = mode_B)' in \
            sem_model.r_script(s, "d.rds")
        study_plan.rename_codes(s, {"einst": "einstellung"})
        assert study_plan.plan_of(s)["sem"]["hoc"][0]["parts"] == ["tb", "einstellung"]
        study_plan.rename_codes(s, {"einstellung": "einst"})
    monkeypatch.setattr(sem_model, "BOOT_DRAWS", 100)
    app.config["HB_SYNC"] = True
    page = client.get("/surveys/1/results?go=1").data.decode()
    part = page[page.index('id="sem"'):]
    assert "Haltung" in part and "Formative Konstrukte" in part and "Becker" in page
    assert "Ohne Globalmaß, daher keine Redundanzanalyse: Haltung" in part
    assert 'id="sem-trace"' in part and "PLS-Algorithmus" in part and "zweistufig" in part
    client.post("/surveys/1/results/sem-bootstrap")
    page = client.get("/surveys/1/results").data.decode()
    hyp = page[page.index('id="hypothesen"'):page.index('id="sem"')]
    assert "Unterstützt" in hyp
    md = client.get("/surveys/1/export/methods-section.md").data.decode()
    assert "disjoint two-stage" in md
    # a global measure for the formative higher order: its redundancy analysis runs in two stages too
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert 'name="sem_global_hoc1"' in page
    form.update(sem_global_hoc1="absicht")
    client.post("/surveys/1/researchdesign", data=MultiDict(form))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert study_plan.plan_of(s)["sem"]["global"] == {"hoc1": "absicht"}
        (entry,) = sem_model.build(s)["redundancy"]
        assert entry["lv"] == "hoc1" and entry["parts"] == ["tb", "einst"]
    page = client.get("/surveys/1/results?go=1").data.decode()
    part = page[page.index('id="sem"'):]
    assert "1 Redundanzanalyse" in part and "Ohne Globalmaß" not in part


def test_an_instrument_with_subscales_as_higher_order_in_pls(app, client, survey):
    add(client, "likert")
    form = [("code", "tech"), ("question", "Technik"), ("points", "7"), ("labeling", "full"), ("level", "metric"),
            ("subscales_shown", "1"), ("checks_shown", "1"), ("subscales", "1")]
    for k in range(9):
        form += [("stmt_id", f"s{k + 1}"), ("stmt_code", ""), ("stmt_text", f"Aussage {k + 1}"),
                 ("stmt_subscale", "ABC"[k // 3])]
    client.post(f"{E}/items/2", data=MultiDict(form))
    _scale(client, 3, "absicht", 3)
    rng = random.Random(7)

    def point(v):
        return max(1, min(7, int(round(4 + 1.4 * v))))

    with app.app_context():
        s = db.session.get(Survey, 1)
        for _ in range(400):
            g = rng.gauss(0, 1)
            facets = [0.7 * g + math.sqrt(0.51) * rng.gauss(0, 1) for _ in range(3)]
            absicht = 0.5 * g + math.sqrt(0.75) * rng.gauss(0, 1)

            def items(f, k):
                return [point(0.75 * f + math.sqrt(1 - 0.5625) * rng.gauss(0, 1)) for _ in range(k)]

            tech = [v for f in facets for v in items(f, 3)]
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers={
                "2": {f"s{k + 1}": v for k, v in enumerate(tech)},
                "3": {f"s{k + 1}": v for k, v in enumerate(items(absicht, 3))}}))
        db.session.commit()
    plan = {"research_question": "Technik", "goal": "correlation", "design": "cross", "h_id": [""],
            "h_text": ["Technik wirkt auf Absicht"], "h_kind": ["effect"], "h_dir": ["pos"], "h_iv": ["tech"],
            "h_dv": ["absicht"], "h_mv": [""], "h_analysis": ["sem"], "sem_method": "pls", "sem_shown": "1",
            "sem_second": ["tech"]}
    client.post("/surveys/1/researchdesign", data=MultiDict(plan))
    with app.app_context():
        model = sem_model.build(db.session.get(Survey, 1))
        assert model["spec"]["higher"] == {"tech": ["tech:A", "tech:B", "tech:C"]}
    page = client.get("/surveys/1/results?go=1").data.decode()
    part = page[page.index('id="sem"'):]
    assert "(höhere Ordnung)" in part and "Schritt 1 · Messmodell" in part


def test_plspredict_and_redundancy_for_a_higher_order():
    """Trace 2026-10-26: PLSpredict through the parts of a higher order (exogenous and explained), and the redundancy
    analysis of a formative higher order in two stages."""
    x, cols, spec = _data()
    found = sem.pls_predict(x, cols, spec)
    assert found["constructs"]["Y"] == "high"
    explained = {"latent": spec["latent"], "observed": [], "higher": spec["higher"], "paths": [("Y", "H")], "indirect": []}
    found = sem.pls_predict(x, cols, explained)
    assert len(found["items"]) == 9 and all(i["q2"] > 0 for i in found["items"])
    rs = sem.redundancy_spec("H", None, "g", ["v3_0"], parts={l: spec["latent"][l] for l in ("L0", "L1", "L2")})
    got = sem.redundancy(x, cols, rs)
    assert got["converged"] and 0.2 < got["path"] < 0.6  # Y's first statement is no global measure of H: below 0.70
    r = sem.fit_pls(x, cols, spec)
    assert r["trace"]["changes"][-1] < sem.PLS_TOL
