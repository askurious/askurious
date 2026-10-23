import json

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Survey
from tests.conftest import add


def setup(client):
    add(client, "assign")                                              # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "sort"), ("question", "Gut oder schlecht?"), ("answer_mode", "required"), ("randomize", "1"), ("keys", "1"),
        ("cat_L_label", "gut"), ("cat_L_code", "1"), ("cat_R_label", "schlecht"), ("cat_R_code", "2"),
        ("el_id", "a"), ("el_code", "robot"), ("el_label", "Pflegeroboter"),
        ("el_id", "b"), ("el_code", "nurse"), ("el_label", "Pflegekraft"),
        ("el_id", "c"), ("el_code", "app"), ("el_label", "Gesundheits-App")]))


def test_assign_parse_export_and_required(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == [] and [c["code"] for c in item.config["categories"]] == ["1", "2"]
        raw = [{"s": "c", "c": "2", "rt": 812}, {"s": "a", "c": "1", "rt": 640.4}, {"s": "b", "c": "1", "rt": 99999999}]
        answer = item.kind.parse_answer(item, MultiDict([("i2", json.dumps(raw))]))
        assert answer == [{"s": "c", "c": "2", "rt": 812}, {"s": "a", "c": "1", "rt": 640}, {"s": "b", "c": "1", "rt": None}]
        values = item.kind.export_values(item, answer)
        assert values["sort_robot"] == "1" and values["sort_robot_rt"] == 640 and values["sort_app"] == "2"
        assert values["sort_order"] == "app|robot|nurse" and values["sort_nurse_rt"] is None
        try:  # incomplete while required
            item.kind.parse_answer(item, MultiDict([("i2", json.dumps(raw[:2]))]))
            assert False
        except AnswerError:
            pass
        # tampering: unknown element, wrong category and duplicates are dropped
        bad = [{"s": "x", "c": "1", "rt": 1}, {"s": "a", "c": "9", "rt": 1}, {"s": "b", "c": "2", "rt": 5}, {"s": "b", "c": "1", "rt": 5}]
        item.answer_mode = "optional"
        assert item.kind.parse_answer(item, MultiDict([("i2", json.dumps(bad))])) == [{"s": "b", "c": "2", "rt": 5}]
        cols = [c for c, _ in item.kind.export_columns(item)]
        assert cols == ["sort_robot", "sort_robot_rt", "sort_robot_swap", "sort_nurse", "sort_nurse_rt", "sort_nurse_swap",
                        "sort_app", "sort_app_rt", "sort_app_swap", "sort_order"]  # sides drawn per person


def test_assign_preview_and_dummy(app, client, survey):
    setup(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-assign="2"' in html and "Pflegeroboter" in html and "<kbd>E</kbd>" in html
    with app.app_context():
        from app import dummy
        from app.models import Response

        dummy.generate(db.session.get(Survey, 1), 3, seed=2)
        db.session.commit()
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert all(len(a) == 3 and all(d["rt"] >= 450 for d in a) for a in answers)


def test_assign_gets_its_own_page(app, client, survey):
    setup(client)                                         # 2: assignment task in block 2
    add(client, "info")                                   # 3: info text on the same page
    with app.app_context():
        import random

        from app import runtime

        s = db.session.get(Survey, 1)
        block = db.session.get(Item, 2).block
        assert [[i.id for i in p] for p in block.pages()] == [[2, 3]]
        layout = runtime.build_layout(s, random.Random(3))
        assert [p["items"] for p in layout["pages"] if p.get("block") == block.id] == [[2], [3]]


def test_sides_random_per_person_or_element(app, client, survey):
    setup(client)                                                      # no "sides" in the form: per person
    with app.app_context():
        item = db.session.get(Item, 2)
        ids = [e["id"] for e in item.config["elements"]]
        assert item.config["sides"] == "person"
        per_person = [item.kind.swapped(item, seed, ids) for seed in range(40)]
        assert all(len(set(s.values())) == 1 for s in per_person)          # one side assignment per person ...
        assert {next(iter(s.values())) for s in per_person} == {True, False}  # ... that differs between people
        assert item.kind.swapped(item, 7, ids) == item.kind.swapped(item, 7, ids)  # stable on reload
        item.config = {**item.config, "sides": "item"}
        assert any(len(set(item.kind.swapped(item, seed, ids).values())) == 2 for seed in range(40))
        item.config = {**item.config, "sides": "fixed"}
        assert not any(any(item.kind.swapped(item, seed, ids).values()) for seed in range(10))
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "sort"), ("question", "Gut oder schlecht?"), ("sides", "item"),
        ("cat_L_label", "gut"), ("cat_L_code", "1"), ("cat_R_label", "schlecht"), ("cat_R_code", "2"),
        ("el_id", "a"), ("el_code", "robot"), ("el_label", "Pflegeroboter"),
        ("el_id", "b"), ("el_code", "nurse"), ("el_label", "Pflegekraft")]))
    with app.app_context():
        assert db.session.get(Item, 2).config["sides"] == "item"
    html = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    assert "data-assign-swap" in html and 'data-assign-side="left"' in html
