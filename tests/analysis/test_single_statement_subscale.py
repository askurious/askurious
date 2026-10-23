"""A subscale of one statement has a value, the answer itself, but no reliability (audit 2026-10-17, O2): the hint in
the editor says so instead of claiming there is no score."""
from app import scales
from app.extensions import db
from app.models import Item
from tests.conftest import add


def test_hint_and_score_of_a_one_statement_subscale(app, client, survey):
    add(client, "likert")
    with app.app_context():
        item = db.session.get(Item, 2)
        item.code = "battery"
        item.config = {"points": 5, "level": "metric", "statements": [
            {"id": "a", "code": "a", "subscale": "A"}, {"id": "b", "code": "b", "subscale": "B"},
            {"id": "c", "code": "c", "subscale": "B"}]}
        hints = [str(h) for h in scales.subscale_warnings(item)]
        assert len(hints) == 1 and "„A“" in hints[0] and "diese eine Antwort" in hints[0]
        assert "Reliabilität lässt sich nicht schätzen" in hints[0]
        assert scales.answer_score(item, {"a": 4, "b": 2, "c": 2}, "A") == 4
