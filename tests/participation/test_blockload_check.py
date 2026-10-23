"""Which blocks are loaded at once (app/blockload.py): a block is simple when nothing on one
of its pages reads an answer of an earlier page of the same block; the editor says per block why not (block-load
concept 2026-10-15, part 2)."""
from app import blockload, experimental
from app.extensions import db
from app.models import Block, Item, Survey
from tests.conftest import add, experiments_on, fill_consent


def build(client):
    """block 2: single choice q2 | page break | text q4 | page break | text q6; block 3: text q8"""
    fill_consent(client)
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data={"code": "q2", "question": "Erste"})
    add(client, "page_break")                                       # 3
    add(client, "text_input")                                       # 4
    client.post("/surveys/1/editor/items/4", data={"code": "q4", "question": "Zweite"})
    add(client, "page_break")                                       # 5
    add(client, "text_input")                                       # 6
    client.post("/surveys/1/editor/items/6", data={"code": "q6", "question": "Dritte"})
    client.post("/surveys/1/editor/blocks")                         # block 3
    add(client, "text_input", block=3)                              # 7
    client.post("/surveys/1/editor/items/7", data={"code": "q8", "question": "Vierte"})


def reasons(app, block_id=2):
    with app.app_context():
        s = db.session.get(Survey, 1)
        return blockload.reasons(s, db.session.get(Block, block_id))


def set_config(app, item_id, **changes):
    with app.app_context():
        item = db.session.get(Item, item_id)
        item.config = {**(item.config or {}), **changes}
        db.session.commit()


def set_text(app, item_id, text):
    with app.app_context():
        item = db.session.get(Item, item_id)
        item.texts = {**(item.texts or {}), "de": {**(item.texts or {}).get("de", {}), "question": text}}
        db.session.commit()


def test_block_without_dependencies_is_simple(app, client, survey):
    build(client)
    assert reasons(app) == []
    assert reasons(app, 3) == ["nur eine Seite"]  # nothing to gain
    assert "nur eine Seite" in reasons(app, 1)  # the consent block


def test_condition_on_an_earlier_page_of_the_block(app, client, survey):
    build(client)
    set_config(app, 4, when={"item": 2, "op": "eq", "value": "1"})
    found = reasons(app)
    assert len(found) == 1 and "q4 auf Seite 2" in found[0] and "q2" in found[0]


def test_joined_conditions_and_row_conditions(app, client, survey):
    build(client)
    set_config(app, 6, when={"rule": "all", "conds": [{"item": "p:x", "op": "eq", "value": "1"},
                                                     {"item": 2, "op": "eq", "value": "1"}]})
    assert any("q6 auf Seite 3" in r for r in reasons(app))
    set_config(app, 6, when=None)
    add(client, "likert")                                           # 8, on page 3
    client.post("/surveys/1/editor/items/8", data={"code": "lk", "question": "Matrix"})
    with app.app_context():
        item = db.session.get(Item, 8)
        rows = [{"id": "s1", "code": "", "when": {"item": 4, "op": "filled", "value": ""}}]
        item.config = {**item.config, "statements": rows}
        db.session.commit()
    assert any("lk" in r and "q4" in r for r in reasons(app))


def test_placeholder_and_rechenwert(app, client, survey):
    build(client)
    set_text(app, 6, "Sie sagten {{q4}}")
    assert any("q6" in r and "q4" in r for r in reasons(app))
    set_text(app, 6, "Dritte")
    add(client, "calc")                                             # 8: a Rechenwert of q4, on page 3
    set_config(app, 8, a="4", op="mul", fixed=12)
    with app.app_context():
        calc_code = db.session.get(Item, 8).code
    set_text(app, 6, "Im Jahr {{%s}}" % calc_code)
    assert any("q6" in r and "q4" in r for r in reasons(app))  # through the Rechenwert to what it reads


def test_carried_options_and_total(app, client, survey):
    build(client)
    add(client, "multiple_choice")                                  # 8, page 3
    client.post("/surveys/1/editor/items/8", data={"code": "mc", "question": "Welche"})
    set_config(app, 8, carry={"item": 2, "which": "chosen"})
    assert any("mc" in r and "q2" in r for r in reasons(app))


def test_dependency_on_an_earlier_block_is_fine(app, client, survey):
    build(client)
    add(client, "page_break", block=3)                              # 8
    add(client, "text_input", block=3)                              # 9
    client.post("/surveys/1/editor/items/9", data={"code": "q9", "question": "Sie sagten {{q4}}"})
    set_config(app, 9, when={"item": 2, "op": "eq", "value": "1"})
    assert reasons(app, 3) == []


def test_pages_that_need_the_server(app, client, survey):
    build(client)
    add(client, "assign")                                           # a task with a page of its own
    assert any("Aufgabe mit eigenen Seiten" in r for r in reasons(app))
    with app.app_context():
        db.session.delete(db.session.get(Item, 8))
        s = db.session.get(Survey, 1)
        s.tempo_hint = "all"
        db.session.commit()
    assert any("Tempo-Hinweis" in r for r in reasons(app))


def test_whole_survey_reasons_and_on(app, client, survey):
    build(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not blockload.on(s)
        s.block_load = True
        assert blockload.on(s) and blockload.simple(s, db.session.get(Block, 2))
        assert not blockload.simple(s, db.session.get(Block, 3))
        s.live = {}
        assert not blockload.on(s) and "Live-Abstimmung" in blockload.check(s)[2]


def test_setting_for_everyone_in_a_draft(app, client, survey):
    """For everyone since 2026-10-16 (no experimental feature any more): the tick is there without a switch."""
    build(client)
    experiments_on(client)
    page = client.get("/surveys/1").get_data(as_text=True)
    assert 'name="block_load"' in page and "badge-exp" not in page.split('name="block_load"', 1)[1][:400]
    client.post("/surveys/1/meta", data={"part": "teilnahme", "show_progress": "1", "block_load_shown": "1",
                                         "block_load": "1"})
    with app.app_context():
        assert db.session.get(Survey, 1).block_load is True
    assert "blockload" not in experimental.KEYS


def test_editor_says_per_block_whether_and_why(app, client, survey):
    build(client)
    html = client.get("/surveys/1/editor/").get_data(as_text=True)
    assert "am Stück" not in html and "seitenweise" not in html  # setting off: nothing
    with app.app_context():
        db.session.get(Survey, 1).block_load = True
        db.session.commit()
    set_config(app, 6, when={"item": 4, "op": "filled", "value": ""})
    html = client.get("/surveys/1/editor/").get_data(as_text=True)
    assert "seitenweise" in html and "q6 auf Seite 3 hängt von q4" in html
    set_config(app, 6, when=None)
    html = client.get("/surveys/1/editor/").get_data(as_text=True)
    assert "am Stück" in html
