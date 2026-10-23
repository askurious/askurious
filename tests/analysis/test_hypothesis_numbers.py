"""Hypothesis tests read numbers as the results page does (audit 2026-10-23, M3): "nan", "inf" and numbers beyond
MAX_NUMBER from old data are missing, not values that break the statistics."""
from app import hypotheses
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add


def test_numeric_drops_non_finite_and_huge_values():
    assert [hypotheses._numeric(v) for v in ("0", "1", "nan", "inf", "-inf", "1e16", 2.5, None)] == \
        [0.0, 1.0, None, None, None, None, 2.5, None]


def test_correlation_with_non_finite_answers_does_not_crash(app, client, survey):
    add(client, "text_input")
    add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        for number, code in ((2, "x"), (3, "y")):
            item = db.session.get(Item, number)
            item.code = code
            item.config = {**item.config, "validation": "decimal"}
        db.session.commit()
        table = [{"x": str(i), "y": str((i * 7) % 11)} for i in range(20)]
        table += [{"x": v, "y": "1"} for v in ("nan", "inf", "1e16")]
        values, level = hypotheses._values(s, "x", table)
        assert level == "metric" and values[-3:] == [None, None, None]
        res = hypotheses._evaluate_one(s, {"text": "H", "kind": "correlation", "iv": "x", "dv": "y", "analysis": "r",
                                           "direction": ""}, table)
        assert res["n"] == 20 and res["p"] is not None
