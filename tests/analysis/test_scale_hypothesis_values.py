import math
import pytest
from app import hypotheses, scales
from app.extensions import db
from app.models import Item, Survey, Response, utcnow
from tests.conftest import add

def test_multidimensional_scale_is_not_combined_in_hypothesis(app, client, survey):
    add(client, "likert")
    with app.app_context():
        survey = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        item.code = "battery"
        item.config = {"points": 5, "level": "metric", "statements": [
            {"id": "a", "code": "a", "subscale": "A"}, {"id": "b", "code": "b", "subscale": "A"},
            {"id": "c", "code": "c", "subscale": "B"}, {"id": "d", "code": "d", "subscale": "B"}]}
        db.session.commit()
        row = {"a": 1, "b": 1, "c": 5, "d": 5}
        actual, level = hypotheses._values(survey, "battery", [row])
        subs = [(name, scales.row_score(item, codes, row)) for name, codes in scales.groups(item)]
        print("SUBSCALES", {"reported_subscales": subs, "hypothesis_value": actual, "level": level})
        assert subs == [("A", 1.0), ("B", 5.0)]
        assert actual is None and level is None
        result = hypotheses._evaluate_one(survey, {"text":"Relation", "kind":"correlation", "iv":"battery", "dv":"other", "analysis":"r"}, [row])
        assert result["blocked"] == "subscales" and result["notes"] and result.get("p") is None


@pytest.mark.parametrize("factor", [1e-300, 1e-100, 1.0, 1e100, 1e307])
def test_pearson_is_scale_invariant(factor):
    r, n, p = scales.pearson([v*factor for v in [1,2,3]], [v*factor for v in [2,1,4]])
    # Centered sums are sxy=2, sxx=2, syy=14/3.
    assert r == pytest.approx(math.sqrt(3/7))
    assert n == 3
    assert p == pytest.approx(0.54562894834299)


def test_semantic_pairs_are_scored_and_missing_rows_stay_missing(app, client, survey):
    add(client, "semantic")
    with app.app_context():
        item = db.session.get(Item, 2)
        item.code = "battery"
        item.config = {"points": 7, "level": "metric", "pairs": [{"id":"a", "code":"a"}, {"id":"b", "code":"b"}]}
        values, level = hypotheses._values(db.session.get(Survey, 1), "battery", [{"a":2,"b":4}, {"a":0,"b":0}, {"a":2}])
        assert values == [3.0, 0.0, None]
        assert level == "metric"


def test_nonfinite_pairs_are_missing_and_constant_values_have_no_correlation():
    assert scales.pearson([1, 2, float("nan")], [2, 3, 4]) == (None, 2, None)
    assert scales.pearson([0, 0, 0], [2, 3, 4]) == (None, 3, None)
    assert scales.pearson([], []) == (None, 0, None)


def test_tiny_valid_numbers_do_not_abort_results_route(app, client, survey):
    add(client, "text_input")
    add(client, "text_input")
    with app.app_context():
        survey = db.session.get(Survey, 1)
        for number, code in ((2, "tinyx"), (3, "tinyy")):
            item = db.session.get(Item, number)
            item.code = code
            item.config = {**item.config, "validation": "decimal"}
        survey.study_plan = {"hypotheses": [{"id":"h1", "text":"Test", "kind":"correlation",
                                            "iv":"tinyx", "dv":"tinyy", "analysis":"r"}]}
        for x, y in zip([1,2,3,4,5,6,7,8,9,10], [2,1,4,3,7,6,5,10,8,9]):
            db.session.add(Response(survey_id=survey.id, source="real", is_preview=False,
                status=Response.COMPLETED, answers={"2":x*1e-100,"3":y*1e-100},
                started_at=utcnow(), completed_at=utcnow()))
        db.session.commit()
    assert client.get("/surveys/1/results?go=1").status_code == 200
