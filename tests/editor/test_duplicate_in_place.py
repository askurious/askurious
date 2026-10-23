"""Duplicating a question or a block within its own survey (app/editor/duplicating.py, duplicate.twins/rewire;
concept konzept-duplizieren-2026-10-21.md, all as recommended, Ralf 2026-10-21): the copy stands right under the
original, gets a variable name of its own, keeps what points outside and points at its fellow copies inside, and
one undo takes it back."""
from app import flow
from app.extensions import db
from app.models import Block, Item, Survey
from tests.conftest import add

E = "/surveys/1/editor"


def _codes(app, block_id):
    with app.app_context():
        return [i.code for i in db.session.get(Block, block_id).items]


def test_a_question_right_below_with_a_name_of_its_own(app, client, survey):
    add(client, "single_choice")  # 2
    add(client, "text_input")     # 3
    with app.app_context():
        q = db.session.get(Item, 2)
        q.code = "alter"
        q.texts = {"de": {"question": "Wie alt?"}, "en": {"question": "How old?"}}
        db.session.commit()
    page = client.get(E + "/").data.decode()
    assert f'action="{E}/items/2/duplicate' in page and "„alter“ duplizieren" in page
    r = client.post(E + "/items/2/duplicate")
    assert r.status_code == 302 and "#item-" in r.headers["Location"]
    assert _codes(app, 2) == ["alter", "alter_2", "Q2"]
    with app.app_context():
        copy = db.session.get(Block, 2).items[1]
        assert copy.texts == {"de": {"question": "Wie alt?"}, "en": {"question": "How old?"}}
        assert copy.config == db.session.get(Item, 2).config and copy.id != 2
    client.post(E + "/items/2/duplicate")  # again: the next number
    assert _codes(app, 2) == ["alter", "alter_3", "alter_2", "Q2"]
    client.post(E + "/undo")
    client.post(E + "/undo")
    assert _codes(app, 2) == ["alter", "Q2"]


def test_neither_consent_nor_page_break(app, client, survey):
    add(client, "page_break")  # 2
    assert client.post(E + "/items/2/duplicate").status_code == 400
    assert client.post(E + "/items/1/duplicate").status_code == 400
    client.post(E + "/blocks/1/duplicate")  # the consent block: refused
    with app.app_context():
        assert sum(1 for b in db.session.get(Survey, 1).blocks for i in b.items if i.type == "consent") == 1


def test_a_block_right_below_with_references_inside_following(app, client, survey):
    """E5: in the copied block a condition and {{code}} on a question of the block point at its copy; what points
    at a question outside stays."""
    add(client, "single_choice")  # 2 in block 2
    add(client, "text_input")     # 3 in block 2
    client.post(E + "/blocks")    # block 3
    with app.app_context():
        s = db.session.get(Survey, 1)
        third = s.blocks[-1].id
    add(client, "single_choice", block=third)  # 4, outside
    with app.app_context():
        s = db.session.get(Survey, 1)
        b = db.session.get(Block, 2)
        b.titles = {"de": "Szenario A"}
        a, t, out = db.session.get(Item, 2), db.session.get(Item, 3), db.session.get(Item, 4)
        a.code, t.code, out.code = "a", "warum", "vorher"
        a.config = {**a.config, "when": {"item": 4, "op": "eq", "value": "1"}}   # outside: stays
        t.config = {**t.config, "when": {"item": 2, "op": "eq", "value": "1"}}   # inside: follows
        t.texts = {"de": {"question": "Warum {{a}}? Und {{vorher}}"}}
        db.session.commit()
    page = client.get(E + "/").data.decode()
    assert f'action="{E}/blocks/2/duplicate' in page and "Schleife um diesen Block" in page
    r = client.post(E + "/blocks/2/duplicate", follow_redirects=True)
    assert "Block „Szenario A“ dupliziert (2 Elemente)" in r.data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        order = flow.block_order(flow.tree(s))
        new = db.session.get(Block, order[order.index(2) + 1])
        assert new.id not in (2, third) and new.titles == {"de": "Szenario A (2)"}
        ca, ct = new.items
        assert (ca.code, ct.code) == ("a_2", "warum_2")
        assert ca.config["when"]["item"] == 4 and ct.config["when"]["item"] == ca.id
        assert ct.texts["de"]["question"] == "Warum {{a_2}}? Und {{vorher}}"
        assert db.session.get(Item, 3).config["when"]["item"] == 2  # the original unchanged
    client.post(E + "/undo")
    with app.app_context():
        assert len(db.session.get(Survey, 1).blocks) == 3


def test_not_in_the_field(app, client, survey):
    add(client, "text_input")  # 2
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    client.post(E + "/items/2/duplicate")
    client.post(E + "/blocks/2/duplicate")
    with app.app_context():
        s = db.session.get(Survey, 1)  # E7: only where new questions may be added
        assert len(s.blocks) == 2 and len(db.session.get(Block, 2).items) == 1


def test_guide_question_with_its_sub_questions(app, client):
    """E9: in an interview guide a Leitfrage comes with its Unterfragen; a Themenblock with all of them."""
    from app import interview
    from tests.interview.test_interview import make

    code, sid = make(app, client)
    with app.app_context():
        s = db.session.get(Survey, sid)
        block = next(b for p, b in interview.ordered_blocks(s) if p == "guide")
        lead = next(i for i in block.items if i.type == "guide_question")
        bid, lid, before = block.id, lead.id, len(block.items)
    client.post(f"/guide/{code}/control", data={"action": "add_sub", "item": lid})
    with app.app_context():
        block = db.session.get(Block, bid)
        unit = next(u for u in interview.units(block) if u[0].id == lid)
        assert len(unit) == 2
        n = len(block.items)
    page = client.get(f"/guide/{code}").data.decode()
    assert f"/surveys/{sid}/editor/items/{lid}/duplicate" in page and f"/surveys/{sid}/editor/blocks/{bid}/duplicate" in page
    r = client.post(f"/surveys/{sid}/editor/items/{lid}/duplicate")
    assert f"/guide/{code}" in r.headers["Location"]
    with app.app_context():
        block = db.session.get(Block, bid)
        assert len(block.items) == n + 2
        units = interview.units(block)
        i = next(k for k, u in enumerate(units) if u[0].id == lid)
        twin = units[i + 1]
        assert len(twin) == 2 and twin[0].type == "guide_question" and interview.is_sub(twin[1])
        blocks_before = len(db.session.get(Survey, sid).blocks)
    client.post(f"/surveys/{sid}/editor/blocks/{bid}/duplicate")
    with app.app_context():
        s = db.session.get(Survey, sid)
        guide = [b for p, b in interview.ordered_blocks(s) if p == "guide"]
        assert len(s.blocks) == blocks_before + 1
        k = [b.id for b in guide].index(bid)
        assert len(guide[k + 1].items) == n + 2
    with app.app_context():
        short = next((b for p, b in interview.ordered_blocks(db.session.get(Survey, sid)) if p == "short"), None)
    if short is not None:  # the Kurzfragebogen exists once
        assert client.post(f"/surveys/{sid}/editor/blocks/{short.id}/duplicate").status_code == 400
