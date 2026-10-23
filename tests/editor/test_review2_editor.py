"""Code review 2026-10-03, package "Editor" (E-1 to E-10): regression tests from the review's reproductions."""
import re

from werkzeug.datastructures import MultiDict

from app import design, flow, items, linked, loops
from app.extensions import db
from app.models import Asset, Block, Construct, EditStep, Item, Response, Survey
from tests.conftest import add, fill_consent
from tests.editor.test_autosave import auto, form_of
from tests.editor.test_linked import _linked_question, _second_survey, choice
from tests.editor.test_loops import setup as loop_setup

E = "/surveys/1/editor"


def _tree(app):
    with app.app_context():
        return [(n["t"], flow.node_id(n)) for n, _, _ in flow.walk(flow.tree(db.session.get(Survey, 1)))]


def test_e1_refused_drop_keeps_the_dragged_element(app, client, survey):
    client.post(f"{E}/blocks")  # block 3
    client.post(f"{E}/flow/add", data={"kind": "rand", "target": "k2", "mode": "wrap"})
    before = _tree(app)
    rid = next(nid for kind, nid in before if kind == "rand")
    # the target was deleted meanwhile by someone else
    r = client.post(f"{E}/flow/{rid}/place", data={"target": "rgone00", "mode": "after"})
    assert r.status_code == 302 and _tree(app) == before
    # "into" a block (not a group)
    client.post(f"{E}/flow/{rid}/place", data={"target": "k3", "mode": "into"})
    assert _tree(app) == before
    with app.app_context():  # nothing was stored as a step either
        assert not [s for s in db.session.scalars(db.select(EditStep.label)) if "verschoben" in s]


def test_e2_running_loop_keeps_the_cells_of_the_default_language(app, client, survey):
    url = loop_setup(client)
    add(client, "image")  # 3
    with app.app_context():
        item = db.session.get(Item, 3)
        item.config = {**item.config, "source": "ai", "prompt": "A {{robot}} in a living room"}
        for k in item.kind.required_keys(item):
            item.assets.append(Asset(key=k, filename="x.png", source="ai", status="approved"))
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
        loop = loops.config(db.session.get(Block, 2))
        r0, c0 = loop["rows"][0]["id"], loop["columns"][0]["id"]
    page = client.get(url).data.decode()
    assert re.search(rf'name="cell_{r0}_{c0}"[^>]*readonly', page) and "Die Tabelle ist fest" in page
    assert ">Speichern</button>" not in page
    client.post(url, data={f"cell_{r0}_{c0}": "Pflegeroboter (neu)"})
    with app.app_context():
        item = db.session.get(Item, 3)
        variables = loops.variables(db.session.get(Block, 2), r0, "de")
        assert variables["robot"] == "Pflegeroboter" and item.kind.asset_for(item, variables) is not None
    # translations of the cells stay correctable
    page = client.get(url + "?lang=en").data.decode()
    assert not re.search(rf'name="cell_{r0}_{c0}"[^>]*readonly', page)
    client.post(url + "?lang=en", data={f"cell_{r0}_{c0}": "care robot"})
    with app.app_context():
        assert loops.variables(db.session.get(Block, 2), r0, "en")["robot"] == "care robot"


def test_e3_malformed_numbers_give_a_message_not_500(app, client, survey):
    add(client, "likert")      # 2
    add(client, "semantic")    # 3
    add(client, "rating")      # 4
    add(client, "multiple_choice")  # 5
    cases = [(2, {"code": "L", "points": "abc"}), (3, {"code": "S", "points": "²"}),
             (4, {"code": "R", "style": "stars", "points": "inf"}), (4, {"code": "R", "style": "stars", "points": "nan"}),
             (5, {"code": "M", "min": "²", "max": "99999999999999999999"})]
    for iid, data in cases:
        assert client.post(f"{E}/items/{iid}", data=data).status_code == 302, (iid, data)
    assert client.post(f"{E}/text-review/apply", data={"take": "²|x"}).status_code == 302
    with app.app_context():
        assert db.session.get(Item, 5).config.get("min") is None


def test_e3_number_fields_of_every_type(app, client, survey):
    """Every number field (type="number") of every element editor, with values int() cannot take."""
    types = [t for t in items.REGISTRY if t != "consent" and not getattr(items.REGISTRY[t], "interview_only", False)]
    for t in types:
        add(client, t)
    bad = ["²", "inf", "nan", "1e400", "99999999999999999999"]
    for iid in range(2, len(types) + 2):
        html = client.get(f"{E}/items/{iid}").data.decode()
        names = sorted(set(re.findall(r'<input[^>]*type="number"[^>]*name="([^"]+)"', html)
                           + re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="number"', html)))
        for name in names:
            for value in bad:
                r = client.post(f"{E}/items/{iid}", data={name: value})
                assert r.status_code < 500, (iid, name, value)


def test_e3_central_catch_answers_autosave_with_json(app, client, survey, monkeypatch):
    add(client, "text_input")
    form = form_of(client, 2)

    def broken(*args, **kwargs):
        raise ValueError("invalid literal for int()")

    monkeypatch.setattr(type(items.get("text_input")), "update_from_form", broken)
    d = auto(client, 2, form).get_json()
    assert d["status"] == "invalid" and "Zahl" in d["message"]
    r = client.post(f"{E}/items/2", data={"question": "x"}, headers={"Referer": f"http://localhost{E}/items/2"})
    assert r.status_code == 302


def test_e4_nan_and_infinity_are_no_bounds(app, client, survey):
    add(client, "slider")   # 2
    add(client, "rating")   # 3
    client.post(f"{E}/items/2", data=MultiDict([("code", "S"), ("question", "q"), ("min", "nan"), ("max", "inf"),
                                                ("step", "-inf"), ("stmt_id", ""), ("stmt_code", "S_1"), ("stmt_text", "a")]))
    client.post(f"{E}/items/3", data={"code": "R", "question": "q", "style": "slider", "min": "0", "max": "nan", "step": "inf"})
    with app.app_context():
        assert {k: db.session.get(Item, 2).config[k] for k in ("min", "max", "step")} == {"min": 0, "max": 100, "step": 1}
        assert {k: db.session.get(Item, 3).config[k] for k in ("min", "max", "step")} == {"min": 0, "max": 100, "step": 1}


def test_e5_second_semantic_construct_has_own_row_names(app, client, survey):
    add(client, "construct:kamb")
    add(client, "construct:kamb")
    with app.app_context():
        s = db.session.get(Survey, 1)
        sem = [i for b in s.blocks for i in b.items if i.type == "semantic"]
        first, second = ([p["code"] for p in i.config["pairs"]] for i in sem)
        assert not set(first) & set(second) and second[0] == f"{sem[1].code}_1"
        assert not [f for f in design.check(s) if "mehrfach" in f.message]


def test_e5_copy_to_another_survey_renames_rows(app, client, survey):
    add(client, "construct:kamb")  # 2
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": "construct:kamb"})
    with app.app_context():
        code = db.session.get(Survey, 2).code
    client.post(f"{E}/items/2/copy-to", data={"target": code})
    with app.app_context():
        s = db.session.get(Survey, 2)
        codes = [p["code"] for b in s.blocks for i in b.items if i.type == "semantic" for p in i.config["pairs"]]
        assert len(codes) == len(set(codes)) and not [f for f in design.check(s) if "mehrfach" in f.message]


def test_e6_text_review_reaches_the_other_uses(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    client.post(f"{E}/text-review/apply", data={"take": "2|question", "text_2|question": "Neue Frage?"})
    with app.app_context():
        uses = linked.uses(linked.get(key))
        assert len(uses) == 2 and {i.texts["de"]["question"] for i, _ in uses} == {"Neue Frage?"}
        assert linked.get(key).spec["texts"]["de"]["question"] == "Neue Frage?"


def test_e7_undo_takes_back_making_an_element_and_redo_makes_it_again(app, client, survey):
    key = _linked_question(app, client)
    client.post(f"{E}/undo")
    with app.app_context():
        assert linked.get(key) is None and not linked.is_linked(db.session.get(Item, 2))
    client.post(f"{E}/redo")
    with app.app_context():
        c = linked.get(key)
        assert c is not None and c.name == "Vorwissen" and linked.is_linked(db.session.get(Item, 2))
        assert [i.id for i, _ in linked.uses(c)] == [2]


def test_e7_undo_keeps_an_element_that_others_use_meanwhile(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    client.post(f"{E}/undo")
    with app.app_context():
        assert linked.get(key) is not None and linked.is_linked(db.session.get(Item, 2))


def test_e7_undo_takes_back_a_new_version(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        db.session.add(Response(survey_id=2, source="real"))  # survey 2 freezes Fassung 1
        db.session.commit()
    client.post(f"{E}/items/2/unlock")
    choice(client, 1, 2, question="Fassung zwei?")
    client.post(f"{E}/items/2/new-version")
    with app.app_context():
        assert linked.get(key).seed_version == 2
    client.post(f"{E}/undo")
    with app.app_context():
        c = linked.get(key)
        assert c.seed_version == 1 and c.spec["texts"]["de"]["question"] == "Frage"
        assert linked.version_of(db.session.get(Item, 2)) == 1
    client.post(f"{E}/redo")
    with app.app_context():
        c = linked.get(key)
        assert c.seed_version == 2 and c.spec["texts"]["de"]["question"] == "Fassung zwei?"
        assert db.session.scalar(db.select(db.func.count(Construct.id)).filter_by(key=key)) == 1


def test_e8_minimum_above_the_number_of_options(app, client, survey):
    add(client, "multiple_choice")  # 2
    client.post(f"{E}/items/2", data=MultiDict([("code", "M"), ("min", "3"), ("option_id", "a"), ("option_code", "1"),
                                                ("option_label", "ja"), ("option_id", "b"), ("option_code", "2"),
                                                ("option_label", "nein")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["min"] == 3
        assert any("nur 2 Antwortoptionen" in p for p in item.kind.validate(item))
        codes = [o["code"] for o in item.config["options"]]
        # fewer options shown than the minimum: all of them are enough
        assert item.kind.parse_answer(item, MultiDict([("i2", c) for c in codes])) == codes


def test_e9_highlight_colours_fixed_while_running(app, client, survey):
    add(client, "highlight")  # 2
    client.post(f"{E}/items/2", data={"code": "H", "passage": "Ein kurzer Text", "ncats": "2",
                                      "cat_label": ["gut", "schlecht"], "cat_color": ["green", "red"]})
    with app.app_context():
        assert [c["color"] for c in db.session.get(Item, 2).config["cats"]] == ["green", "red"]
    client.post(f"{E}/items/2?lang=en", data={"cat_label": ["good", "bad"], "cat_color": ["yellow", "blue"]})
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    page = client.get(f"{E}/items/2").data.decode()
    assert re.search(r'name="cat_color"\s+disabled', page)
    client.post(f"{E}/items/2", data={"question": "Markieren", "cat_label": ["gut", "schlecht"], "cat_color": ["yellow", "blue"]})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert [c["color"] for c in item.config["cats"]] == ["green", "red"]
        assert item.texts["en"]["cats"] == ["good", "bad"]


def test_e10_autosave_after_start_answers_json(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post(f"{E}/items/2", data={"code": "Q1", "question": "Frage"})
    form = form_of(client, 2)  # the page opened in draft, still open after the start
    client.post("/surveys/1/start", data={})
    form["question"] = "Frage, korrigiert"
    d = auto(client, 2, form).get_json()
    assert d["status"] == "saved" and d["notes"] and d["problems"] is not None
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"]["question"] == "Frage, korrigiert"
