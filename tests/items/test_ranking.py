from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Survey
from tests.conftest import add


def setup(client):
    add(client, "ranking")                                          # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "prio"), ("question", "Was ist Ihnen am wichtigsten?"), ("answer_mode", "required"), ("randomize", "1"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Sicherheit"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Kosten"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Komfort")]))


def test_ranking_parse_required_and_export(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == [] and item.config["randomize"]
        order = MultiDict([("i2", "3"), ("i2", "1"), ("i2", "2")])
        try:  # untouched start order does not count as an answer
            item.kind.parse_answer(item, order)
            assert False
        except AnswerError:
            pass
        order.add("i2_touched", "1")
        assert item.kind.parse_answer(item, order) == ["3", "1", "2"]
        assert item.kind.export_values(item, ["3", "1", "2"]) == {"prio_1": 2, "prio_2": 3, "prio_3": 1}
        assert [c for c, _ in item.kind.export_columns(item)] == ["prio_1", "prio_2", "prio_3"]
        bad = MultiDict([("i2", "3"), ("i2", "3"), ("i2", "2"), ("i2_touched", "1")])   # not a permutation
        try:
            item.kind.parse_answer(item, bad)
            assert False
        except AnswerError:
            pass


def test_ranking_random_start_and_preview(app, client, survey):
    setup(client)
    starts = set()
    with app.app_context():
        from app import runtime
        import random

        s = db.session.get(Survey, 1)
        for seed in range(12):
            starts.add(tuple(runtime.build_layout(s, random.Random(seed))["stmt"]["2"]))
        assert len(starts) > 1                                        # the start order varies
        from app import dummy

        dummy.generate(s, 3, seed=1)
        db.session.commit()
        from app.models import Response

        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert all(sorted(a) == ["1", "2", "3"] for a in answers)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-rank="2"' in html and "Reihenfolge passt so" in html and "Sicherheit" in html
