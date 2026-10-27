"""Multilevel models of a diary study on the results page (longitudinal and multilevel concept 2026-10-17, E8, E9):
a relationship of two scales of the entries tested with the mixed model (within and between persons, Satterthwaite),
the lagged and random-slope ticks of the plan, and the scales of the module as two-level factor analyses."""
import math
import random
import secrets
from datetime import timedelta

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Block, Enrolment, Response, Survey, utcnow
from tests.conftest import add


def _study(app, client, persons=60):
    client.post("/diary/new", data={"title": "Stimmung im Alltag", "language": "de"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        block = next(b for b in survey.blocks if (b.settings or {}).get("part") == "m1")
        block_id = block.id
    for code in ("x", "y"):
        add(client, "likert", block=block_id)
    with app.app_context():
        items = {i.code: i.id for b in db.session.get(Survey, 1).blocks for i in b.items if i.type == "likert"}
    for (old, item_id), code in zip(sorted(items.items(), key=lambda kv: kv[1]), ("x", "y")):
        client.post(f"/surveys/1/editor/items/{item_id}", data=MultiDict(
            [("code", code), ("question", f"Skala {code}"), ("points", "7"), ("labeling", "full"), ("level", "metric")]
            + [v for k in range(1, 4) for v in (("stmt_id", f"s{k}"), ("stmt_code", ""), ("stmt_text", f"{code} {k}"))]))
    client.post("/surveys/1/researchdesign", data=MultiDict({
        "research_question": "Hängt x mit y zusammen?", "goal": "correlation", "design": "cross",
        "h_id": [""], "h_text": ["Wer gerade mehr x erlebt, berichtet mehr y"], "h_kind": ["correlation"],
        "h_dir": ["pos"], "h_iv": ["x"], "h_dv": ["y"], "h_analysis": [""], "diary_shown": "1"}))
    rng = random.Random(9)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        ids = {i.code: i.id for b in survey.blocks for i in b.items}
        start = utcnow() - timedelta(days=20)
        for p in range(persons):
            person = Enrolment(survey_id=survey.id, secret_hash=secrets.token_hex(32), pid=f"pp{p}", status=Enrolment.ACTIVE,
                               source="real", tz="Europe/Berlin")
            db.session.add(person)
            db.session.flush()
            db.session.add(Response(survey_id=survey.id, enrolment_id=person.id, pid=f"pp{p}-e", source="real",
                                    status=Response.COMPLETED, occasion={"part": "entry"}, answers={}, layout={},
                                    started_at=start, qversion=1))
            level = rng.gauss(0, 0.8)
            ylevel = 0.3 * level + rng.gauss(0, 0.6)
            for k in range(rng.randint(6, 12)):
                xw = rng.gauss(0, 1)
                yw = 0.45 * xw + rng.gauss(0, math.sqrt(0.8))

                def stmts(f):
                    return {f"s{j}": max(1, min(7, int(round(4 + 0.9 * f + 0.5 * rng.gauss(0, 1))))) for j in (1, 2, 3)}
                db.session.add(Response(
                    survey_id=survey.id, enrolment_id=person.id, pid=f"pp{p}-{k}", source="real",
                    status=Response.COMPLETED, occasion={"part": "m1", "n": k + 1}, layout={}, qversion=1,
                    started_at=start + timedelta(hours=8 * k + p % 5),
                    answers={str(ids["x"]): stmts(level + xw), str(ids["y"]): stmts(ylevel + yw)}))
        db.session.commit()


def test_a_relationship_in_the_entries_is_tested_within_persons(app, client):
    _study(app, client)
    page = client.get("/surveys/1/results?go=1").get_data(as_text=True)
    assert "Mehrebenenmodell (Einträge in Personen)" in page and "innerhalb b = " in page and "Satterthwaite" in page
    assert "unterstützt" in page and "Tagebuchstudie: kein Test" not in page
    # the scales of the module in two levels, with omega within and between persons
    assert 'id="diary-scales"' in page and "Skalen in zwei Ebenen" in page and "erst ab 50 Personen" not in page
    # no single-level structural equation model over the entries
    assert 'id="sem"' not in page
    with app.app_context():
        from app import hypotheses, export

        survey = db.session.get(Survey, 1)
        table = export.rows(survey, with_hb=False, with_quality=False)
        res = hypotheses.evaluate(survey, table)[0]
        assert res["analysis"] == "mixed" and res["n"] == 60 and res["verdict"] == "unterstützt"
        assert 0.25 < res["effect"][1] < 0.7 and res["multilevel"]["entries"] > 300
        assert not res["multilevel"]["lagged"] and not res["multilevel"]["slope"]
        # the model as R code (lme4) and the sentence of the method section
        assert 'd$x <- rowMeans(d[, c("x_1", "x_2", "x_3")])' in res["code"]
        assert "fit <- lmer(y ~ x_innerhalb + x_zwischen + (1 | person), data = d)" in res["code"]
        from app import methods_text

        with app.test_request_context():
            text = " ".join(" ".join(ps) for _, ps in methods_text.sections(survey, None, None, None, "de"))
        assert "linearen gemischten Modellen" in text and "Strukturgleichungsmodell" not in text
    assert "Als R-Code (lme4)" in page


def test_lagged_and_random_slope_from_the_plan(app, client):
    _study(app, client)
    client.post("/surveys/1/researchdesign", data=MultiDict({
        "research_question": "Hängt x mit y zusammen?", "goal": "correlation", "design": "cross",
        "h_id": [""], "h_text": ["x wirkt auf das nächste y"], "h_kind": ["correlation"],
        "h_dir": ["pos"], "h_iv": ["x"], "h_dv": ["y"], "h_analysis": [""], "diary_shown": "1",
        "diary_lagged": "1", "diary_slope": "1"}))
    page = client.get("/surveys/1/researchdesign").get_data(as_text=True)
    assert 'name="diary_lagged" value="1" checked' in page
    with app.app_context():
        from app import export, hypotheses, study_plan

        survey = db.session.get(Survey, 1)
        assert study_plan.plan_of(survey)["diary"] == {"lagged": True, "slope": True}
        res = hypotheses.evaluate(survey, export.rows(survey, with_hb=False, with_quality=False))[0]
        assert res["multilevel"]["lagged"] and res["multilevel"]["slope"]
        assert "x_vorher" in res["code"] and "(1 + x_innerhalb | person)" in res["code"]
        # without a true effect of the entry before, the lagged effect is small
        assert abs(res["effect"][1]) < 0.2
