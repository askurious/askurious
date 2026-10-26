"""Versions of the structural equation model (model versions 2026-10-26, app/sem_versions.py): one per new set of
model settings, frozen figures, the main model from the pre-registration (else the first), exploratory versions named
in the methods draft and the Word tables, two versions compared, settings adopted, statements left out with a reason."""
import io

from werkzeug.datastructures import MultiDict

from app import sem_model, sem_versions, study_plan
from app.extensions import db
from app.models import SemVersion, Survey
from tests.analysis.test_sem_formative_check import _survey


def _versions(app):
    with app.app_context():
        return [(v.number, v.name, v.key) for v in sem_versions.versions(db.session.get(Survey, 1))]


def test_versions_from_the_results_page(app, client, survey, monkeypatch):
    form = _survey(app, client)
    monkeypatch.setattr(sem_model, "BOOT_DRAWS", 80)
    app.config["HB_SYNC"] = True
    page = client.get("/surveys/1/results?go=1").data.decode()
    assert len(_versions(app)) == 1
    part = page[page.index('id="sem"'):]
    assert "Hauptmodell · Version 1" in part and 'id="sem-versions"' in part and "erste Version" in part
    client.get("/surveys/1/results")  # the same settings: no new version
    assert len(_versions(app)) == 1
    client.post("/surveys/1/results/sem-bootstrap")  # the intervals of the same data come into version 1
    client.get("/surveys/1/results")
    with app.app_context():
        (v1,) = sem_versions.versions(db.session.get(Survey, 1))
        assert v1.figures["boot"] == 80 and all(p[3] is not None for p in v1.figures["paths"])
        assert v1.figures["bic"] and v1.figures["predict"]
    # other settings: tb formative -> version 2, exploratory
    form.update(sem_mode_tb="formative")
    client.post("/surveys/1/researchdesign", data=MultiDict(form))
    page = client.get("/surveys/1/results?go=1").data.decode()
    assert [n for n, _, _ in _versions(app)] == [1, 2]
    part = page[page.index('id="sem"'):]
    assert "Version 2 · explorativ" in part and "formativ: +tb" in part
    md = client.get("/surveys/1/export/methods-section.md").data.decode()
    assert "Version 2" in md and "explorativ" in md
    from docx import Document

    doc = Document(io.BytesIO(client.get("/surveys/1/results/sem.docx").data))
    assert "Explorativ: Version 2" in "\n".join(p.text for p in doc.paragraphs)
    # comparison of two versions
    with app.app_context():
        ids = [v.id for v in sem_versions.versions(db.session.get(Survey, 1))]
    page = client.get(f"/surveys/1/results?va={ids[0]}&vb={ids[1]}").data.decode()
    part = page[page.index('id="sem-compare-versions"'):]
    assert "Vergleich V1 ↔ V2" in part and "BIC" in part and "BIC-Gewicht" in part and "R²" in part
    # leaving a statement out: only with a reason
    client.post("/surveys/1/results/sem-version", data={"action": "drop", "code": "einst_3", "reason": ""})
    with app.app_context():
        assert study_plan.plan_of(db.session.get(Survey, 1))["sem"]["dropped"] == {}
    client.post("/surveys/1/results/sem-version", data={"action": "drop", "code": "einst_3", "reason": "doppelt gemessen"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert study_plan.plan_of(s)["sem"]["dropped"] == {"einst_3": "doppelt gemessen"}
        assert sem_model.build(s)["spec"]["latent"]["einst"] == ["einst_1", "einst_2"]
    page = client.get("/surveys/1/results?go=1").data.decode()
    assert "einst_3 weggelassen · Grund: „doppelt gemessen“" in page and len(_versions(app)) == 3
    assert "Weggelassen: einst_3" in page
    client.post("/surveys/1/results/sem-version", data={"action": "undrop", "code": "einst_3"})
    with app.app_context():
        assert study_plan.plan_of(db.session.get(Survey, 1))["sem"]["dropped"] == {}
    # adopting version 1 brings back its settings: no new version, the main model again
    client.post("/surveys/1/results/sem-version", data={"action": "adopt", "version": str(ids[0])})
    page = client.get("/surveys/1/results?go=1").data.decode()
    assert len(_versions(app)) == 3 and "Hauptmodell · Version 1" in page
    # rename, delete (not the main model), the current data as a new version
    client.post("/surveys/1/results/sem-version", data={"action": "name", "version": str(ids[1]), "name": "TB formativ"})
    assert _versions(app)[1][1] == "TB formativ"
    client.post("/surveys/1/results/sem-version", data={"action": "delete", "version": str(ids[0])})
    assert len(_versions(app)) == 3
    client.post("/surveys/1/results/sem-version", data={"action": "delete", "version": str(ids[1])})
    assert [n for n, _, _ in _versions(app)] == [1, 3]
    client.post("/surveys/1/results/sem-version", data={"action": "refresh"})
    found = _versions(app)
    assert [n for n, _, _ in found] == [1, 3, 4] and found[2][2] == found[0][2]


def test_the_main_model_comes_from_the_pre_registration_and_versions_are_limited(app, client, survey, monkeypatch):
    form = _survey(app, client)
    client.get("/surveys/1/results?go=1")
    form.update(sem_mode_tb="formative")
    client.post("/surveys/1/researchdesign", data=MultiDict(form))
    client.get("/surveys/1/results?go=1")
    with app.app_context():
        s = db.session.get(Survey, 1)
        plan = dict(s.study_plan)
        from app import prereg

        monkeypatch.setattr(prereg, "listing", lambda survey: [("p.zip", 1, None, "")])
        monkeypatch.setattr(prereg, "frozen_definition", lambda survey, name: {"study_plan": plan})
        assert sem_versions.main_of(s).number == 2  # the pre-registered settings are those of version 2
        assert sem_versions.page(s)["exploratory"] is False
        monkeypatch.setattr(sem_versions, "MAX", 3)
        for k in range(4):
            v = sem_versions.versions(s)[-1]
            sem_versions.record(s, {"fit": {"paths": [], "r2": {}}, "n": 300 + k, "method": "pls"}, force=True)
        numbers = [v.number for v in sem_versions.versions(s)]
        assert len(numbers) == 3 and 2 in numbers  # the main model stays
        from app import lifecycle

        lifecycle.delete(s, force=True)
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(SemVersion.id))) == 0  # gone with the survey


def test_a_viewer_cannot_change_versions(app, client, survey):
    _survey(app, client)
    client.get("/surveys/1/results?go=1")
    from app.models import SurveyMember, User

    with app.app_context():
        u = User(orcid="0000-0002-1825-0097", name="Gast")
        db.session.add(u)
        db.session.flush()
        db.session.add(SurveyMember(survey_id=1, user_id=u.id, orcid=u.orcid, role="viewer"))
        db.session.commit()
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Gast")
    other.post("/terms/", data={"accept": "1"})
    response = other.post("/surveys/1/results/sem-version", data={"action": "refresh"})
    assert response.status_code in (302, 303, 403)
    assert len(_versions(app)) == 1


def test_figures_are_plain_json_without_nan():
    """PostgreSQL refuses NaN in a JSON column (a version of a saturated model broke the results page, 2026-10-26):
    NaN and infinity become None, numpy numbers floats."""
    import numpy as np

    out = sem_versions._plain({"a": float("nan"), "b": [np.float64(0.5), float("inf")], "c": {1: np.int64(3)}, "d": True})
    assert out == {"a": None, "b": [0.5, None], "c": {"1": 3.0}, "d": True}
    import json

    json.dumps(out, allow_nan=False)


def test_the_simulation_buttons_of_the_plan_are_actions(app, client, survey):
    """The autosave of the plan form keeps a submit without a name to itself (app.js): the buttons that start a
    simulation carry a name, so that the browser sends them to their own route."""
    from pathlib import Path

    text = Path("app/templates/surveys/_fields_plan.html").read_text(encoding="utf-8")
    assert 'name="sem_power" value="1" formaction=' in text and 'name="panel_power" value="1" formaction=' in text
