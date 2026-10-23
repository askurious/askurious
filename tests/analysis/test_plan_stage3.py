"""Stage 3 of the drawing of the model (modell-zeichnen-konzept.md, F7 b and F9 b, Ralf 2026-10-11): variables with
more than two groups (dummies against the first, a joint test; as moderator compared in all groups), moderated
mediation (index with bootstrap interval), moderation with subscales and of variables explained in the model."""
import math
import random

import numpy as np
from werkzeug.datastructures import MultiDict

from app import sem, sem_model, study_plan
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add
from tests.analysis.test_sem_survey import E, _scale

PLAN = "/surveys/1/researchdesign"


def _point(v):
    return max(1, min(7, int(round(4 + 1.2 * v))))


def _items(rng, f, k):
    return {f"s{i}": _point(0.75 * f + math.sqrt(1 - 0.5625) * rng.gauss(0, 1)) for i in range(1, k + 1)}


def _survey(app, client, n, answer):
    """tb (2), einst (3), absicht (4) as scales, fach (5) with three answers; answer(rng) gives (tb, einst,
    absicht, fach)."""
    for number, code, k in ((2, "tb", 4), (3, "einst", 4), (4, "absicht", 3)):
        _scale(client, number, code, k)
    add(client, "single_choice")
    client.post(f"{E}/items/5", data=MultiDict({"code": "fach", "question": "Fach?", "option_id": ["a", "b", "c"],
                                                "option_code": ["1", "2", "3"], "option_label": ["Jura", "Medizin", "Technik"]}))
    rng = random.Random(5)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for _ in range(n):
            tb, einst, absicht, fach = answer(rng)
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers={
                "2": _items(rng, tb, 4), "3": _items(rng, einst, 4), "4": _items(rng, absicht, 3), "5": str(fach)}))
        db.session.commit()


def _post(client, rows, method="cb"):
    keys = ("h_text", "h_kind", "h_dir", "h_iv", "h_dv", "h_mv", "h_wv", "h_analysis")
    data = {k: [r.get(k[2:], "") if k != "h_text" else r.get("text", "H") for r in rows] for k in keys}
    data["h_id"] = [""] * len(rows)
    data["sem_method"] = method
    return client.post(PLAN, data=MultiDict(data))


def test_more_than_two_groups_explain_with_one_path_each_and_a_joint_test(app, client, survey):
    def answer(rng):
        fach = rng.choice([1, 2, 3])
        absicht = {1: 0.0, 2: 0.5, 3: 0.9}[fach] + 0.8 * rng.gauss(0, 1)
        return rng.gauss(0, 1), rng.gauss(0, 1), absicht / 1.2, fach
    _survey(app, client, 450, answer)
    _post(client, [{"text": "Das Fach wirkt auf die Absicht", "kind": "difference", "iv": "fach", "dv": "absicht",
                    "analysis": "sem"}])
    with app.app_context():
        s = db.session.get(Survey, 1)
        info = sem_model.variable(s, "fach")
        assert [n for n, _, _ in info["dummies"]] == ["fach:2", "fach:3"] and info["groups"] == ["1", "2", "3"]
        model = sem_model.build(s)
        assert set(model["spec"]["paths"]) == {("fach:2", "absicht"), ("fach:3", "absicht")}
        text = sem_model.r_script(s, "d.rds")
        assert 'daten$fach_2 <- ifelse(is.na(daten$fach), NA, as.numeric(as.character(daten$fach) == "2"))' in text
    page = client.get("/surveys/1/results?go=1").data.decode()
    hyp = page[page.index('id="hypothesen"'):page.index('id="sem"')]
    assert "Unterstützt" in hyp and "χ²(2) = " in hyp


def test_more_than_two_groups_cannot_be_explained_or_mediate(app, client, survey):
    _survey(app, client, 10, lambda rng: (0, 0, 0, 1))
    _post(client, [{"kind": "effect", "iv": "tb", "dv": "fach"},
                   {"kind": "mediation", "iv": "tb", "mv": "fach", "dv": "absicht"}])
    with app.app_context():
        model = sem_model.build(db.session.get(Survey, 1))
        assert model["hyps"][1]["errors"] == [("fach", "groups_role")]
        assert model["hyps"][2]["errors"] == [("fach", "groups_role")]


def test_a_moderator_with_three_groups(app, client, survey):
    def answer(rng):
        fach = rng.choice([1, 2, 3])
        b = {1: 0.1, 2: 0.4, 3: 0.7}[fach]
        tb = rng.gauss(0, 1)
        return tb, rng.gauss(0, 1), b * tb + math.sqrt(1 - b * b) * rng.gauss(0, 1), fach
    _survey(app, client, 600, answer)
    _post(client, [{"kind": "moderation", "iv": "tb", "dv": "absicht", "wv": "fach"}])
    with app.app_context():
        s = db.session.get(Survey, 1)
        g = sem_model.build(s)["groups"][1]
        assert g["w"] == "fach" and g["w_nodes"] == ["fach:2", "fach:3"] and g["spec"]["paths"] == [("tb", "absicht")]
        text = sem_model.r_script(s, "d.rds")
        assert 'group.label = c("1", "2", "3")' in text and "lavTestWald(fit_h1" in text
    page = client.get("/surveys/1/results?go=1").data.decode()
    hyp = page[page.index('id="hypothesen"'):page.index('id="sem"')]
    assert "Unterstützt" in hyp and "β(Jura)" in hyp and "χ²(2) = " in hyp and "gleicher Pfad in allen 3 Gruppen" in hyp


def test_three_groups_in_the_model_of_groups():
    rng = np.random.default_rng(7)

    def data(n, b):
        x = rng.normal(size=n)
        y = b * x + np.sqrt(1 - b * b) * rng.normal(size=n)
        return np.column_stack([0.8 * f + 0.6 * rng.normal(size=n) for f in (x, x, x, y, y, y)])

    spec = {"latent": {"X": ["x0", "x1", "x2"], "Y": ["y0", "y1", "y2"]}, "observed": [], "paths": [("X", "Y")],
            "indirect": []}
    r = sem.fit_ml_groups([data(300, 0.2), data(300, 0.4), data(300, 0.6)], sem.columns(spec), spec)
    assert r["joint"][("X", "Y")]["df"] == 2 and r["joint"][("X", "Y")]["p"] < 0.01
    assert len(r["diff"][("X", "Y")]["b"]) == 3
    ps = [sem.fit_ml_groups([data(250, 0.4), data(250, 0.4), data(250, 0.4)], sem.columns(spec), spec)["joint"][("X", "Y")]["p"]
          for _ in range(30)]
    assert sum(p < 0.05 for p in ps) <= 5  # about 5 % under H0


def test_moderated_mediation_with_its_index(app, client, survey):
    def answer(rng):
        tb, w = rng.gauss(0, 1), rng.gauss(0, 1)
        einst = 0.4 * tb + 0.3 * tb * w + 0.8 * rng.gauss(0, 1)
        absicht = 0.5 * einst + 0.8 * rng.gauss(0, 1)
        return tb, einst, absicht, 1 if w < -0.4 else 2 if w < 0.4 else 3
    _survey(app, client, 500, answer)
    _scale(client, 6, "w", 3)
    with app.app_context():  # a construct for the moderator, from the same w
        s = db.session.get(Survey, 1)
        rng = random.Random(9)
        for r in s.responses:
            r.answers = {**r.answers, "6": _items(rng, {"1": -1.0, "2": 0.0, "3": 1.0}[r.answers["5"]] + 0.3 * rng.gauss(0, 1), 3)}
        db.session.commit()
    _post(client, [{"kind": "mediation", "iv": "tb", "mv": "einst", "dv": "absicht"},
                   {"kind": "moderation", "iv": "tb", "dv": "einst", "wv": "w", "dir": "pos"}])
    with app.app_context():
        s = db.session.get(Survey, 1)
        model = sem_model.build(s)
        assert model["spec"]["modmed"] == [("a", "tb", "einst", "absicht", "tb×w")]
        assert model["hyps"][2]["mediation"] == 1
    app.config["HB_SYNC"] = True
    page = client.get("/surveys/1/results?go=1").data.decode()
    assert "Index der moderierten Vermittlung" in page and "braucht den Bootstrap" in page
    client.post("/surveys/1/results/sem-bootstrap")
    page = client.get("/surveys/1/results").data.decode()
    hyp = page[page.index('id="hypothesen"'):page.index('id="sem"')]
    assert hyp.count("Unterstützt") == 2 and "(Bootstrap)" in hyp and "bedingte indirekte Effekte" in hyp


def test_a_moderator_of_a_variable_explained_in_the_model(app, client, survey):
    def answer(rng):
        tb, w = rng.gauss(0, 1), rng.gauss(0, 1)
        einst = 0.5 * tb + 0.8 * rng.gauss(0, 1)
        return tb, einst, 0.4 * einst + 0.25 * einst * w + 0.8 * rng.gauss(0, 1), 1 if w < 0 else 2
    _survey(app, client, 400, answer)
    _post(client, [{"kind": "effect", "iv": "tb", "dv": "einst"},
                   {"kind": "moderation", "iv": "einst", "dv": "absicht", "wv": "tb"}])
    with app.app_context():
        model = sem_model.build(db.session.get(Survey, 1))
        assert model["hyps"][2]["errors"] == [] and ("einst×tb", "absicht") in model["spec"]["paths"]
