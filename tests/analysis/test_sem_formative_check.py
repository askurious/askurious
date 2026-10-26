"""The check of formative constructs in PLS-SEM after the methods literature (formative check 2026-10-26): reflective or
formative per scale, the redundancy analysis with a global measure (Chin, 1998; Cheah et al., 2018), the VIF in two
levels, weights and loadings with the bootstrap and the verdict per statement (Cenfetelli & Bassellier, 2009; Hair et
al., 2022), the final bootstrap with more draws, the methods draft, the Word table and the R script."""
import io
import math
import random

import numpy as np
from werkzeug.datastructures import MultiDict

from app import sem, sem_model, study_plan
from app.extensions import db
from app.models import Response, Survey
from tests.analysis.test_sem_survey import E, _answers, _scale
from tests.conftest import add


def test_the_verdict_follows_the_decision_path():
    v = sem_model.formative_verdict
    assert v(None, 0.9, 0.0) is None  # without a bootstrap no verdict
    assert v(0.01, 0.1, 0.5) == "weight"
    assert v(0.2, 0.50, 0.5) == "loading"
    assert v(0.2, 0.3, 0.01) == "content"
    assert v(0.2, 0.3, 0.2) == "check"


def test_the_table_marks_vif_sign_and_many_statements():
    items = [f"x{k}" for k in range(1, 10)]
    result = {"nodes": {"F": ("f", "Index")},
              "fit": {"formative": {"F": [1.2, 3.4, 5.6] + [1.0] * 6},
                      "loadings": {"F": [{"item": i, "std": s, "weight": w} for i, s, w in
                                         zip(items, [0.8, 0.6, 0.3] + [0.4] * 6, [0.5, 0.1, -0.1] + [0.1] * 6)]}}}
    boot = {"weights": {("F", i): {"ci": (0.0, 1.0), "se": 0.1} for i in items},
            "loadings": {("F", i): {"ci": (0.0, 1.0), "se": 0.1} for i in items}}
    (c,) = sem_model.formative_table(result, boot)
    assert c["many"] and [i["vif_level"] for i in c["items"][:3]] == [None, "hint", "high"]
    assert [i["sign"] for i in c["items"][:3]] == [False, False, True]
    assert c["items"][0]["verdict"] == "weight"  # 0.5 / 0.1: z = 5
    assert c["items"][1]["verdict"] == "loading"  # weight 0.1 / 0.1: p = .32, loading 0.6
    assert c["items"][2]["verdict"] == "content"  # loading 0.3 / 0.1: p = .003
    rows = sem_model.redundancy_table({"nodes": {"F": ("f", "Index")}, "labels": {"g": "Global"},
                                       "redundancy": [{"lv": "F", "global": "g", "n": 300, "path": 0.72, "r2": 0.52}]},
                                      {"redundancy": {"F": {"ci": (0.65, 0.78), "se": 0.03}}})
    assert rows[0]["ok"] and rows[0]["ci"] == (0.65, 0.78) and rows[0]["global_label"] == "Global"


def test_the_redundancy_path_and_its_bootstrap():
    """A formative index that a global question measures with noise: the path comes near the true correlation."""
    rng = np.random.default_rng(3)
    x = rng.normal(size=(400, 3))
    index = 0.6 * x[:, 0] + 0.5 * x[:, 1] + 0.3 * x[:, 2]
    g = index / index.std() + rng.normal(scale=0.5, size=400)
    data = np.column_stack([x, g])
    cols = ["a1", "a2", "a3", "g"]
    spec = sem.redundancy_spec("A", cols[:3], "g", ["g"])
    found = sem.redundancy(data, cols, spec)
    assert found["converged"] and abs(found["path"] - 1 / math.sqrt(1.25)) < 0.05
    boot = sem.bootstrap(data, cols, spec, "pls", draws=100)
    lo, hi = boot["paths"][("A", "g")]["ci"]
    assert lo < found["path"] < hi
    # a reflective scale as the global measure: mode A
    spec2 = sem.redundancy_spec("A", cols[:3], "G", ["g", "g"])
    assert spec2["latent"]["G"] == ["g", "g"] and spec2["paths"] == [("A", "G")]


def _survey(app, client):
    """tb (four statements) as a formative index, tbg a global question about it (a slider), tb -> einst -> absicht."""
    for number, code, k in ((2, "tb", 4), (3, "einst", 3), (4, "absicht", 3)):
        _scale(client, number, code, k)
    add(client, "rating")
    client.post(f"{E}/items/5", data={"code": "tbg", "question": "Alles in allem: TB?", "style": "slider", "min": "0",
                                       "max": "100", "step": "1", "level": "metric"})
    rng = random.Random(5)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for answers in _answers(rng, 300):
            tb = sum(answers["2"].values()) / 4 - 4
            answers["5"] = round(50 + 20 * (tb + 0.4 * rng.gauss(0, 1)))
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers=answers))
        db.session.commit()
    form = {"research_question": "TB", "goal": "correlation", "design": "cross",
            "h_id": ["", ""], "h_text": ["TB wirkt auf Einstellung", "Einstellung wirkt auf Absicht"],
            "h_kind": ["effect", "effect"], "h_dir": ["pos", "pos"], "h_iv": ["tb", "einst"], "h_dv": ["einst", "absicht"],
            "h_mv": ["", ""], "h_analysis": ["sem", "sem"], "sem_method": "pls", "sem_shown": "1"}
    client.post("/surveys/1/researchdesign", data=MultiDict(form))
    return form


def test_formative_check_from_the_plan_to_the_results(app, client, survey, monkeypatch):
    form = _survey(app, client)
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert 'name="sem_mode_tb" value="reflective" checked' in page
    assert 'name="sem_global_tb"' in page and '<option value="tbg">' in page
    form.update(sem_mode_tb="formative", sem_mode_einst="reflective", sem_global_tb="tbg")
    client.post("/surveys/1/researchdesign", data=MultiDict(form))
    with app.app_context():
        s = db.session.get(Survey, 1)
        opts = study_plan.plan_of(s)["sem"]
        assert opts["formative"] == ["tb"] and opts["global"] == {"tb": "tbg"}
        model = sem_model.build(s)
        assert model["redundancy"] == [{"lv": "tb", "code": "tb", "global": "tbg", "node": "tbg", "glob_items": ["tbg"]}]
        assert "tbg" not in model["spec"]["observed"]  # the global measure stays out of the main model
        assert "Redundanzanalyse mit Globalmaß: tb → tbg" in study_plan.markdown(s, "de")
        text = sem_model.r_script(s, "d.rds")
        assert 'composite("G_tbg", single_item("tbg"))' in text and "pls_r1 <- estimate_pls(data = daten_r1" in text
        # renaming follows into the global measure
        study_plan.rename_codes(s, {"tbg": "tb_global"})
        assert study_plan.plan_of(s)["sem"]["global"] == {"tb": "tb_global"}
        study_plan.rename_codes(s, {"tb_global": "tbg"})
    monkeypatch.setattr(sem_model, "BOOT_DRAWS", 150)
    monkeypatch.setattr(sem_model, "BOOT_DRAWS_FINAL", 300)
    app.config["HB_SYNC"] = True
    page = client.get("/surveys/1/results?go=1").data.decode()
    part = page[page.index('id="sem"'):]
    assert "1 Redundanzanalyse (Konvergenzvalidität)" in part and "ausreichend (ab 0,70)" in part
    assert part.count("Bootstrap fehlt") >= 2
    client.post("/surveys/1/results/sem-bootstrap")
    page = client.get("/surveys/1/results").data.decode()
    part = page[page.index('id="sem"'):]
    assert "Bootstrap mit 150 Ziehungen" in part and "Endfassung: 300 Ziehungen" in part
    assert "behalten · Gewicht signifikant" in part
    assert "Cheah" in page and "Cenfetelli" in page  # the sources of the page list
    client.post("/surveys/1/results/sem-bootstrap", data={"draws": "final"})
    page = client.get("/surveys/1/results").data.decode()
    part = page[page.index('id="sem"'):]
    assert "Bootstrap mit 300 Ziehungen" in part and "Endfassung:" not in part
    md = client.get("/surveys/1/export/methods-section.md").data.decode()
    assert "Redundanzanalyse" in md and "Cenfetelli & Bassellier, 2009" in md
    word = client.get("/surveys/1/export/sem-tables.docx")
    if word.status_code == 200:
        from docx import Document

        text = "\n".join(p.text for p in Document(io.BytesIO(word.data)).paragraphs)
        assert "Formative Konstrukte" in text and "Redundanzanalyse" in text and "300 Ziehungen" in text


def test_without_a_global_measure_the_page_says_so(app, client, survey):
    form = _survey(app, client)
    form.update(sem_mode_tb="formative")
    client.post("/surveys/1/researchdesign", data=MultiDict(form))
    page = client.get("/surveys/1/results?go=1").data.decode()
    part = page[page.index('id="sem"'):]
    assert "Ohne Globalmaß, daher keine Redundanzanalyse" in part
