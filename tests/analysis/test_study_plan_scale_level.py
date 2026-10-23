"""The level of a Likert scale in the research plan (audit 2026-10-23, M2, Ralf: "a").

A matrix of two or more statements counts with its score (the mean of its statements) at the level of the matrix:
ordinal unless set metric (or a toolbox construct). An ordinal score is tested with ranks (Mann-Whitney U, Kruskal-
Wallis, Spearman) and described by the median; a metric one as before (Welch, Pearson, mean)."""
from werkzeug.datastructures import MultiDict

from app import hypotheses, scales, study_plan
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add


def _setup(app, client):
    add(client, "single_choice")                                                # 2: two groups
    client.post("/surveys/1/editor/items/2", data=MultiDict({"code": "gruppe", "question": "Gruppe?", "option_id": ["a", "b"],
                                                             "option_code": ["1", "2"], "option_label": ["A", "B"]}))
    add(client, "likert")                                                       # 3: an ordinal scale (the default)
    add(client, "likert")                                                       # 4: a scale set metric
    with app.app_context():
        for number, code, level in ((3, "ord", None), (4, "met", "metric")):
            item = db.session.get(Item, number)
            item.code = code
            config = {**item.config, "statements": [{"id": f"{code}{k}", "code": f"{code}{k}", "reverse": False}
                                                    for k in (1, 2, 3)]}
            if level:
                config["level"] = level
            item.config = config
        db.session.commit()


def _h(kind, iv, dv):
    return {"text": "H", "kind": kind, "iv": iv, "dv": dv, "direction": ""}


def test_matrix_without_metric_level_is_ordinal_and_tested_by_ranks(app, client, survey):
    _setup(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert study_plan._level(db.session.get(Item, 3)) == "ordinal"
        assert study_plan._level(db.session.get(Item, 4)) == "metric"
        assert study_plan.suggest(s, _h("difference", "gruppe", "ord")) == "u"
        assert study_plan.suggest(s, _h("difference", "gruppe", "met")) == "t"
        assert study_plan.suggest(s, _h("correlation", "ord", "met")) == "rho"
        # a scale score forms no groups, ordinal or not
        assert {"ord", "met"} <= study_plan.metric_codes(s)
        assert study_plan.group_mismatch(s, _h("difference", "ord", "met"))
        assert study_plan.suggest(s, _h("difference", "ord", "met")) == "rho"
        assert "als ordinal behandelt" in study_plan.reasons(s, _h("difference", "gruppe", "ord"))[0]


def test_ordinal_scale_score_gets_rank_test_and_median(app, client, survey):
    _setup(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        codes = scales.scored_codes(db.session.get(Item, 3))
        table = []
        for i in range(30):
            group = "1" if i < 15 else "2"
            base = 2 if group == "1" else 4
            table.append({"gruppe": group, **{c: base + (i + k) % 2 for k, c in enumerate(codes)}})
        values, level = hypotheses._values(s, "ord", table)
        assert level == "ordinal" and all(isinstance(v, float) for v in values)
        res = hypotheses._evaluate_one(s, _h("difference", "gruppe", "ord"), table)
        assert res["tested"] == "u" and res["center"] == "Md"
        assert all("median_text" not in g and g["median"] is not None for g in res["groups"])
        described = hypotheses._evaluate_one(s, _h("descriptive", "", "ord"), table)
        assert described["sentence"].startswith("Mdn = ") and "M = " not in described["sentence"]
