"""The evaluation of a structural equation model in two steps (two-step check 2026-10-26): first the measurement model
(loadings, α, ρ_A, CR, AVE, HTMT, formative checks), then the structural model (VIF, paths, R² in words, PLSpredict);
total effects with their bootstrap interval; t beside p (Hair et al., 2022; Dijkstra & Henseler, 2015)."""
import io

import numpy as np
from werkzeug.datastructures import MultiDict

from app import sem, sem_model
from tests.analysis.test_sem_survey import _survey


def test_rho_a_lies_between_alpha_and_cr():
    rng = np.random.default_rng(1)
    f, g = rng.normal(size=500), rng.normal(size=500)
    x = np.column_stack([f * l + rng.normal(scale=0.6, size=500) for l in (0.9, 0.8, 0.7, 0.6)])
    y = np.column_stack([(0.5 * f + g) * 0.8 + rng.normal(scale=0.6, size=500) for _ in range(3)])
    cols = [f"x{i}" for i in range(4)] + [f"y{i}" for i in range(3)]
    r = sem.fit_pls(np.column_stack([x, y]), cols, {"latent": {"F": cols[:4], "G": cols[4:]}, "observed": [],
                                                    "paths": [("F", "G")], "indirect": []})
    q = r["quality"]
    for lv in ("F", "G"):
        assert q["alpha"][lv] - 0.02 <= q["rho_a"][lv] <= q["cr"][lv] + 1e-9


def test_total_effects_over_the_routes():
    paths = [("a", "m"), ("m", "y"), ("a", "y"), ("y", "z")]
    routes = sem.total_routes(paths)
    assert routes == [("a", "y"), ("a", "z"), ("m", "z")]
    found = sem.total_effects({("a", "m"): 0.5, ("m", "y"): 0.4, ("a", "y"): 0.2, ("y", "z"): 0.5}, routes)
    assert abs(found[("a", "y")]["total"] - 0.4) < 1e-12 and abs(found[("a", "z")]["indirect"] - 0.2) < 1e-12
    assert sem_model.r2_word(0.8) == "substanziell" and sem_model.r2_word(0.3) == "schwach"


def test_two_steps_on_the_results_page(app, client, survey, monkeypatch):
    _survey(app, client, n=300, plan=False)
    form = {"research_question": "TB", "goal": "correlation", "design": "cross",
            "h_id": ["", "", ""], "h_text": ["TB wirkt auf Einstellung", "Einstellung wirkt auf Absicht", "TB über Einstellung"],
            "h_kind": ["effect", "effect", "mediation"], "h_dir": ["pos", "pos", "pos"],
            "h_iv": ["tb", "einst", "tb"], "h_dv": ["einst", "absicht", "absicht"], "h_mv": ["", "", "einst"],
            "h_analysis": ["sem", "sem", ""], "sem_method": "pls"}
    client.post("/surveys/1/researchdesign", data=MultiDict(form))
    monkeypatch.setattr(sem_model, "BOOT_DRAWS", 150)
    app.config["HB_SYNC"] = True
    page = client.get("/surveys/1/results?go=1").data.decode()
    part = page[page.index('id="sem"'):]
    one, two, measure = part.index('id="sem-step1"'), part.index('id="sem-step2"'), part.index('id="sem-measure"')
    assert one < measure < two < part.index('id="sem-diagram"')
    assert "Ladungen ab 0,70:" in part and "AVE ab 0,50:" in part and "Pfade: Bootstrap fehlt" in part
    assert "ρ<sub>A</sub>" in part and "Gesamteffekte" in part and "Dijkstra" in page
    client.post("/surveys/1/results/sem-bootstrap")
    page = client.get("/surveys/1/results").data.decode()
    part = page[page.index('id="sem"'):]
    assert "Pfade mit p &lt; ,05:" in part or "Pfade mit p < ,05:" in part
    assert 'title="t = ' in part
    totals = part[part.index("Gesamteffekte"):]
    assert "Bootstrap fehlt" not in totals[:totals.index("</table>")]
    assert "mittel" in part or "schwach" in part or "substanziell" in part
    word = client.get("/surveys/1/results/sem.docx")
    from docx import Document

    table = Document(io.BytesIO(word.data)).tables[0]
    assert [c.text for c in table.rows[0].cells][-3:] == ["t", "p", "f²"]
