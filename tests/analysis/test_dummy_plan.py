"""Dummy answers drawn from the study plan (app/dummy_plan.py, Ralf 2026-10-07): the variables of its hypotheses
hang together as planned, so a structural equation model of dummies does not collapse."""
from werkzeug.datastructures import MultiDict

from app import dummy, dummy_plan, sem_model
from app.extensions import db
from app.models import Survey
from tests.conftest import experiments_on
from tests.analysis.test_sem_survey import _survey


def test_dummies_follow_the_plan_and_the_model_has_a_solution(app, client, survey):
    experiments_on(client, "sem")
    _survey(app, client, n=0)
    with app.app_context():
        s = db.session.get(Survey, 1)
        rows = dummy_plan.rows(s, 5, seed=1)
        assert rows and all(set(r) == {2, 3, 4} for r in rows)          # the three scales get planned answers
        assert all(1 <= int(v) <= 7 for r in rows for item in r.values() for v in item.values())
        made = dummy.generate(s, 300, seed=3)
        assert made == 300
        from app import export

        table = export.rows(s, False, ["dummy"], None, True, with_hb=False, with_quality=False)
        res = sem_model.estimate(s, table, "cb")
        assert not res.get("error"), res.get("error")
        assert res["fit"]["admissible"]
        paths = {(p["from"], p["to"]): p["std"] for p in res["fit"]["paths"]}
        assert paths[("tb", "einst")] > 0.15 and paths[("einst", "absicht")] > 0.15


def test_without_a_plan_the_dummies_stay_random(app, client, survey):
    _survey(app, client, n=0, plan=False)
    with app.app_context():
        assert dummy_plan.rows(db.session.get(Survey, 1), 5) is None


def test_the_planned_path_of_a_hypothesis():
    assert dummy_plan._beta({"effect": 0.5, "analysis": "sem"}) == 0.5
    assert dummy_plan._beta({"effect": 0.8, "analysis": "t"}) == dummy_plan.DEFAULT_BETA   # a d is no path
    assert dummy_plan._beta({"effect": 0.9, "analysis": "r", "direction": "neg"}) == -dummy_plan.MAX_BETA
