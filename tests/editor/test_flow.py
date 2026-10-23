import csv
import io
from collections import Counter


from app import flow
from app.extensions import db
from app.models import Survey
from tests.conftest import CONSENT_OK, add, fill_consent, split_node

E = "/surveys/1/editor"


def tree_labels(app):
    with app.app_context():
        s = db.session.get(Survey, 1)
        return ([(n["t"], depth, n.get("block")) for n, _, depth in flow.walk(flow.tree(s)) if n["t"] != "split"],
                [b.id for b in s.blocks])


def framing_survey(client):
    """consent | screen (age) | randomizer: image A (block 4) vs image B (block 5) | branch age<18 -> end | outro"""
    fill_consent(client)
    add(client, "text_input")                                       # 2 age in block 2
    client.post(E + "/items/2", data={"code": "age", "validation": "integer", "question": "Alter"})
    for title in ("Bild A", "Bild B", "Abschluss"):                  # blocks 3, 4, 5
        client.post(E + "/blocks")
    client.post(E + "/blocks/3", data={"title": "Bild A"})
    client.post(E + "/blocks/4", data={"title": "Bild B"})
    add(client, "info", block=3)                                     # 3
    client.post(E + "/items/3", data={"code": "a", "question": "Framing A"})
    add(client, "info", block=4)                                     # 4
    client.post(E + "/items/4", data={"code": "b", "question": "Framing B"})
    add(client, "info", block=5)                                     # 5
    client.post(E + "/items/5", data={"code": "outro", "question": "Ende der Befragung"})


def test_tree_follows_blocks_and_moves(app, client, survey):
    framing_survey(client)
    types, order = tree_labels(app)
    assert [t for t, _, _ in types] == ["block"] * 5 and order == [1, 2, 3, 4, 5]
    client.post(E + "/flow/add", data={"kind": "rand"})
    with app.app_context():
        rid = next(n["id"] for n, _, _ in flow.walk(flow.tree(db.session.get(Survey, 1))) if n["t"] == "rand")
    # move the randomizer up to sit after block 2, then pull blocks 3 and 4 into it
    for _ in range(3):
        client.post(f"{E}/flow/{rid}/up")
    client.post(f"{E}/flow/k3/up")          # block 3 above the randomizer? no: k3 is now after rand -> moves before it
    client.post(f"{E}/flow/k3/down")
    client.post(f"{E}/flow/k3/in")
    client.post(f"{E}/flow/k4/in")
    types, order = tree_labels(app)
    assert types == [("block", 0, 1), ("block", 0, 2), ("rand", 0, None), ("block", 1, 3), ("block", 1, 4),
                     ("block", 0, 5)]
    assert order == [1, 2, 3, 4, 5]
    # editor arrow on block 4 moves it within the randomizer only
    client.post(E + "/blocks/4/move/up")
    assert tree_labels(app)[1] == [1, 2, 4, 3, 5]
    html = client.get(E + "/").data.decode()
    assert "🎲 Randomisierung 1: 1 von 2" in html
    # removing the group keeps its blocks
    client.post(f"{E}/flow/{rid}/delete")
    types, _ = tree_labels(app)
    assert [t for t, _, _ in types] == ["block"] * 5


def setup_design(app, client):
    """consent | age | [rand framing: A, B] | [branch age < 18: end] | outro"""
    framing_survey(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1},
            {"t": "block", "block": 2},
            {"t": "rand", "id": "r1", "code": "framing", "name": "Bild", "k": 1, "balanced": True, "shuffle": False,
             "children": [{"t": "block", "block": 3}, {"t": "block", "block": 4}]},
            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "lt", "value": "18"},
             "children": [{"t": "end", "id": "e1", "text": {"de": "**Leider zu jung.**"}}]},
            {"t": "block", "block": 5},
        ]}
        flow.normalize(s)
        db.session.commit()
    return "r1"


def test_design_check_and_figures(app, client, survey):
    setup_design(app, client)
    from app import design

    with app.app_context():
        s = db.session.get(Survey, 1)
        tree = [(n["t"], d) for n, _, d in flow.walk(flow.tree(s)) if n["t"] != "split"]
        assert tree == [("block", 0), ("block", 0), ("rand", 0), ("block", 1), ("block", 1), ("branch", 0),
                        ("end", 1), ("block", 0)]
        st = design.stats(s)
        assert st["conditions"] == 2 and st["branches"] == 1
        assert not [f for f in design.check(s) if f.severity == "error"]


def run(app, uid, age):
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": str(age)})
    return p


def test_balanced_randomizer_branch_and_screenout(app, client, survey):
    setup_design(app, client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    seen = Counter()
    for n in range(6):
        p = run(app, uid, 30)
        html = p.get(f"/s/{uid}").data.decode()
        seen["A" if "Framing A" in html else "B"] += 1
        assert ("Framing A" in html) != ("Framing B" in html)  # exactly one condition
        p.post(f"/s/{uid}", data={"nav": "next"})
        assert "Ende der Befragung" in p.get(f"/s/{uid}").data.decode()
        p.post(f"/s/{uid}", data={"nav": "next"})
    assert seen == {"A": 3, "B": 3}  # balanced
    young = run(app, uid, 16)
    young.post(f"/s/{uid}", data={"nav": "next"})  # framing page
    assert "Leider zu jung" in young.get(f"/s/{uid}").data.decode()
    data = client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(data)))
    assert Counter(r["framing"] for r in rows if r["status"] == "completed") == {"Bild A": 3, "Bild B": 3}
    assert rows[-1]["status"] == "screenout"
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "framing" in book and "balanciert" in book


def test_condition_must_refer_to_earlier_question(app, client, survey):
    framing_survey(client)
    client.post(E + "/flow/add", data={"kind": "branch"})
    with app.app_context():
        bid = next(n["id"] for n, _, _ in flow.walk(flow.tree(db.session.get(Survey, 1))) if n["t"] == "branch")
    client.post(f"{E}/flow/{bid}/update", data={"item": "5", "op": "answered"})
    for _ in range(4):
        client.post(f"{E}/flow/{bid}/up")
    client.post(f"{E}/flow/k3/in")  # block 3 would be inside; not possible: k3 is before -> use down/in
    client.post(f"{E}/flow/{bid}/down")
    client.post(f"{E}/flow/k4/in")
    from app import design

    with app.app_context():
        s = db.session.get(Survey, 1)
        msgs = [f.message for f in design.check(s)]
    assert any("erst danach kommt" in m for m in msgs) or any("leer" in m for m in msgs)


def test_holds():
    assert flow.holds({"item": 1, "op": "eq", "value": "2"}, {1: "2"})
    assert flow.holds({"item": 1, "op": "ne", "value": "2"}, {1: "3"})
    assert flow.holds({"item": 1, "op": "contains", "value": "2"}, {1: ["1", "2"]})
    assert flow.holds({"item": 1, "op": "lt", "value": "18"}, {1: 16})
    assert not flow.holds({"item": 1, "op": "gt", "value": "18"}, {1: None})
    assert flow.holds({"item": 1, "op": "empty"}, {})
    assert flow.holds({"item": 1, "op": "answered"}, {1: {"s1": 3}})


def test_flow_page_and_drag_placement(app, client, survey):
    framing_survey(client)
    client.post(E + "/flow/add", data={"kind": "rand"})
    with app.app_context():
        rid = next(n["id"] for n, _, _ in flow.walk(flow.tree(db.session.get(Survey, 1))) if n["t"] == "rand")
    html = client.get("/surveys/1/flow/").data.decode()
    assert 'data-flow-page' in html and "Randomisierung" in html and 'aria-current="page"' in html
    nav = html.split('class="survey-tabs"')[1].split("</nav>")[0]
    assert nav.index("Fragebogen") < nav.index("Ablauf") < nav.index("Export")
    place = lambda nid, target, mode: client.post(f"{E}/flow/{nid}/place", data={"target": target, "mode": mode})
    place(rid, "k2", "after")                     # randomizer after block 2
    place("k3", rid, "into")
    place("k4", rid, "into")
    types, order = tree_labels(app)
    assert types == [("block", 0, 1), ("block", 0, 2), ("rand", 0, None), ("block", 1, 3), ("block", 1, 4),
                     ("block", 0, 5)]
    place("k4", "k3", "before")                   # reorder inside the group
    assert tree_labels(app)[1] == [1, 2, 4, 3, 5]
    place("k3", "k5", "after")                    # out of the group to the end
    assert tree_labels(app)[1] == [1, 2, 4, 5, 3]
    assert place(rid, "k4", "into").status_code == 302  # a group cannot go into itself
    with app.app_context():
        assert [t for t, _, _ in tree_labels(app)[0]] == ["block", "block", "rand", "block", "block", "block"]
    assert "Ablauf" in client.get(E + "/").data.decode()  # editor badges link to the flow page


def test_new_elements_are_dropped_into_the_flow(app, client, survey):
    """A card of "Hinzufügen" dragged into the flow (target, mode): between two nodes, into a group, or onto a
    block, which then goes into the new loop, randomizer or condition. One step each, which undo takes back."""
    framing_survey(client)
    add = lambda kind, target, mode: client.post(E + "/flow/add", data={"kind": kind, "target": target, "mode": mode})
    top = lambda: [(n["t"], [c.get("block") for c in n.get("children", [])] if n["t"] != "block" else n["block"])
                   for n in flow.tree(db.session.get(Survey, 1))["children"] if n["t"] != "split"]
    html = client.get("/surveys/1/flow/").data.decode()
    assert "data-flow-add" in html and html.count(" data-wrap") == 4      # every block but the consent takes a wrap
    r = add("end", "k2", "after")                                           # between two blocks
    with app.app_context():
        assert top() == [("block", 1), ("block", 2), ("end", []), ("block", 3), ("block", 4), ("block", 5)]
        eid = flow.tree(db.session.get(Survey, 1))["children"][2]["id"]
    assert r.headers["Location"].endswith(f"at={eid}")                      # the page comes back at the new element
    add("loop", "k3", "wrap")                                               # onto a block: the block goes into the loop
    add("branch", "k4", "wrap")                                             # ... into "dann" of the condition
    add("rand", "k5", "before")
    with app.app_context():
        assert top() == [("block", 1), ("block", 2), ("end", []), ("loop", [3]), ("branch", [4]), ("rand", []), ("block", 5)]
        rid = flow.tree(db.session.get(Survey, 1))["children"][5]["id"]
    add("end", rid, "into")                                                 # into a group
    add("rand", rid, "wrap")                                                # only a block is wrapped
    add("end", "k5", "wrap")                                                # an end holds nothing
    add("loop", "k1", "wrap")                                               # the consent is never repeated
    add("loop", "k99", "after")                                             # no such place
    page = client.get("/surveys/1/flow/").data.decode()
    assert "An dieser Stelle lässt sich das Element nicht einfügen" in page
    with app.app_context():
        assert top() == [("block", 1), ("block", 2), ("end", []), ("loop", [3]), ("branch", [4]), ("rand", [None]), ("block", 5)]
    assert "Ablauf: Schleife „Schleife 1“ hinzugefügt, Block „" in page   # the step names both
    for _ in range(5):                                                      # each drop is one step
        client.post(E + "/undo")
    with app.app_context():
        assert top() == [("block", 1), ("block", 2), ("block", 3), ("block", 4), ("block", 5)]
    client.post(E + "/flow/add", data={"kind": "rand"})                     # a click still adds at the end
    with app.app_context():
        assert top()[-1] == ("rand", [])


def test_else_group_shown_when_condition_fails_and_follows_its_branch(app, client, survey):
    framing_survey(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "lt", "value": "18"}, "children": [{"t": "block", "block": 3}]},
            {"t": "block", "block": 4}, {"t": "block", "block": 5}]}
        db.session.commit()
    client.post(E + "/flow/b1/else")                                   # "+ Sonst"
    client.post(E + "/flow/b1/else")                                   # only once
    with app.app_context():
        s = db.session.get(Survey, 1)
        root = flow.tree(s)
        kinds = [n["t"] for n in root["children"] if n["t"] != "split"]
        assert kinds == ["block", "block", "branch", "else", "block", "block"]
        xid = root["children"][3]["id"]
    client.post(E + "/flow/__NID__/place".replace("__NID__", "k4"), data={"target": xid, "mode": "into"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        entries, _, _ = flow.plan(s, __import__("random").Random(1))
        conds = {ref: c for kind, ref, c, _ in entries if kind == "block"}
        assert conds[3] == ["b1"] and conds[4] == ["!b1"] and conds[5] == []
        by_id = flow.conditions_by_id(s)
        assert flow.cond_holds(by_id, "b1", {2: "15"}) and not flow.cond_holds(by_id, "!b1", {2: "15"})
        assert flow.cond_holds(by_id, "!b1", {2: "30"}) and not flow.cond_holds(by_id, "b1", {2: "30"})
        assert "↳ sonst (nicht age: unter 18)" in flow.badges(s)[4]
    client.post(E + "/flow/b1/up")                                     # the else moves along with its branch
    with app.app_context():
        kinds = [n["t"] for n in flow.tree(db.session.get(Survey, 1))["children"]]
        assert kinds.index("else") == kinds.index("branch") + 1
    client.post(E + "/flow/b1/delete")                                 # branch gone: else dissolves, blocks stay
    with app.app_context():
        s = db.session.get(Survey, 1)
        types, _ = [n["t"] for n, _, _ in flow.walk(flow.tree(s))], None
        assert "else" not in types and "branch" not in types
        assert sorted(n["block"] for n, _, _ in flow.walk(flow.tree(s)) if n["t"] == "block") == [1, 2, 3, 4, 5]
    html = client.get("/surveys/1/flow/").data.decode()
    assert html.index("<strong>Schleife</strong>") < html.index("Randomisierung</strong>")


def test_dropping_into_the_empty_else_slot_creates_it(app, client, survey):
    framing_survey(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "lt", "value": "18"}, "children": [{"t": "block", "block": 3}]},
            {"t": "block", "block": 4}, {"t": "block", "block": 5}]}
        db.session.commit()
    page = client.get("/surveys/1/flow/").data.decode()
    assert 'data-parent="newelse:b1"' in page and "fl-slot-else" in page
    client.post(E + "/flow/k4/place", data={"target": "newelse:b1", "mode": "into"})
    with app.app_context():
        root = flow.tree(db.session.get(Survey, 1))
        other = root["children"][3]
        assert other["t"] == "else" and other["of"] == "b1" and other["children"] == [{"t": "block", "block": 4}]


def test_branch_name_and_nested_conditions(app, client, survey):
    """A condition can have a name; conditions can be nested in "dann" and "sonst"."""
    framing_survey(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "lt", "value": "18"}, "children": [{"t": "block", "block": 3}]},
            {"t": "else", "id": "x1", "of": "b1", "children": [
                {"t": "branch", "id": "b2", "cond": {"item": 2, "op": "gt", "value": "65"}, "children": [{"t": "block", "block": 4}]},
                {"t": "else", "id": "x2", "of": "b2", "children": [{"t": "block", "block": 5}]}]}]}
        db.session.commit()
    client.post(E + "/flow/b1/update", data={"name": "Altersfilter", "item": "2", "op": "lt", "value": "18"})
    page = client.get("/surveys/1/flow/").data.decode()
    assert "Altersfilter" in page and page.count("fl-slot-else") == 2
    with app.app_context():
        s = db.session.get(Survey, 1)
        entries, _, _ = flow.plan(s, __import__("random").Random(1))
        conds = {ref: c for kind, ref, c, _ in entries if kind == "block"}
        assert conds[4] == ["!b1", "b2"] and conds[5] == ["!b1", "!b2"]
        by_id = flow.conditions_by_id(s)
        shown = lambda age, b: all(flow.cond_holds(by_id, c, {2: age}) for c in conds[b])
        assert [shown("15", b) for b in (3, 4, 5)] == [True, False, False]
        assert [shown("70", b) for b in (3, 4, 5)] == [False, True, False]
        assert [shown("40", b) for b in (3, 4, 5)] == [False, False, True]


def test_block_in_a_loop_moves_across_the_split_with_its_loop(app, client, survey):
    framing_survey(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2}, {"t": "block", "block": 3},
            {"t": "split", "id": "s1"},
            {"t": "loop", "id": "l1", "name": "robot", "code": "robot", "columns": [], "rows": [], "mode": "all", "k": 1,
             "order": "random", "balanced": True, "children": [{"t": "block", "block": 4}]},
            {"t": "block", "block": 5}]}
        db.session.commit()
        assert flow.blocks_after_split(db.session.get(Survey, 1)) == [4, 5]
    page = client.get(E + "/").data.decode()
    assert 'class="split-divider"' in page and page.index('id="split"') < page.index('id="block-4"')
    client.post(E + "/blocks/4/move/up")                              # the loop moves along, out of the split
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert flow.blocks_after_split(s) == [5]
        assert [n["t"] for n in flow.tree(s)["children"]][3:5] == ["loop", "split"]
    client.post(E + "/blocks/4/move/down")                            # and back in
    with app.app_context():
        assert flow.blocks_after_split(db.session.get(Survey, 1)) == [4, 5]
    assert client.post(E + "/flow/s1/delete").status_code == 400      # the separated section cannot be removed
    with app.app_context():
        assert split_node(db.session.get(Survey, 1)) is not None


def test_split_is_a_fixed_divider(app, client, survey):
    framing_survey(client)
    with app.app_context():
        sid = split_node(db.session.get(Survey, 1))["id"]
    before = tree_labels(app)
    client.post(f"{E}/flow/{sid}/up")
    client.post(f"{E}/flow/{sid}/place", data={"target": "k2", "mode": "before"})
    assert tree_labels(app) == before                               # the divider does not move
    page = client.get("/surveys/1/flow/").data.decode()
    split_li = page.split('class="fl-node fl-split"')[1].split("</li>")[0]
    assert "draggable" not in page.split('class="fl-node fl-split"')[1].split(">")[0] and "flow/" + sid not in split_li


def test_flow_page_keeps_its_place_and_offers_every_move_as_a_button(app, client, survey):
    """UI audit F17, F18, F20: after an action the page returns to the element (its settings open after
    saving); moving into the group above has a button, impossible moves are off; a condition offers the
    answer codes of its question and the check reports a value that is none of them."""
    from werkzeug.datastructures import MultiDict

    from app import flow as flow_mod
    from app.models import Survey as S

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "single_choice"})   # item 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "fav"), ("question", "Farbe?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "rot"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "blau")]))
    client.post("/surveys/1/editor/blocks")                                           # block 3
    client.post("/surveys/1/editor/blocks/3/items", data={"type": "info"})            # item 3
    client.post("/surveys/1/editor/flow/add", data={"kind": "branch"})
    with app.app_context():
        s = db.session.get(S, 1)
        bid = next(n["id"] for n, _, _ in flow_mod.walk(flow_mod.tree(s)) if n["t"] == "branch")
        # consent, block 2, branch, block 3: block 3 stands right after the branch
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                                            next(n for n, _, _ in flow_mod.walk(flow_mod.tree(s)) if n["t"] == "branch"),
                                            {"t": "block", "block": 3}]}
        flow_mod.normalize(s)
        db.session.commit()
    html = client.get("/surveys/1/flow/").data.decode()

    def buttons(nid):
        tools = html.split(f'id="node-{nid}"')[1].split('class="fl-tools"')[1].split("</span>")[0]
        # "in" and "out" are only there when possible (UI audit R92); up and down always, disabled where impossible
        return {a: f"/flow/{nid}/{a}" in tools and "disabled" not in tools.split(f"/flow/{nid}/{a}")[1].split("</button>")[0]
                for a in ("up", "down", "in", "out")}

    # at the root, right after a group; "down" crosses the divider of the separated section, which always follows
    assert buttons("k3") == {"up": True, "down": True, "in": True, "out": False}
    assert buttons("k1") == {"up": False, "down": True, "in": False, "out": False}
    r = client.post("/surveys/1/editor/flow/k3/in")
    assert r.headers["Location"].endswith("at=k3")                                     # back at the moved element
    html = client.get("/surveys/1/flow/?at=k3").data.decode()
    assert 'data-at="k3"' in html and buttons("k3")["out"] and not buttons("k3")["in"]
    # the condition: the answers of the chosen question on offer, a wrong value reported
    assert '"options": [["1", "rot"], ["2", "blau"]]' in html
    r = client.post(f"/surveys/1/editor/flow/{bid}/update", data={"item": "2", "op": "eq", "value": "rot"})
    assert f"at={bid}" in r.headers["Location"] and f"open={bid}" in r.headers["Location"]
    html = client.get(r.headers["Location"]).data.decode()
    assert '<details class="fl-settings" open>' in html and '<input type="checkbox" name="c0_codes" value="1"  > rot' in html
    assert "ist kein Antwortcode von fav (vorhanden: 1, 2); so trifft sie nie zu." in html
    client.post(f"/surveys/1/editor/flow/{bid}/update", data={"item": "2", "op": "eq", "value": "2"})
    assert "ist kein Antwortcode" not in client.get("/surveys/1/flow/").data.decode()


def test_editor_drags_blocks_before_and_after_others(app, client, survey):
    framing_survey(client)
    place = lambda block, target, mode: client.post(f"{E}/blocks/{block}/place", data={"target": target, "mode": mode})
    page = client.get(E + "/").data.decode()
    assert page.count("data-block-handle") == 5 and "data-block-place-url" in page  # every block, the consent too
    place(5, 2, "before")
    assert tree_labels(app)[1] == [1, 5, 2, 3, 4]
    place(1, 4, "after")                                        # the consent block moves like the others
    assert tree_labels(app)[1] == [5, 2, 3, 4, 1]
    place(1, 5, "before")
    place(5, 4, "after")
    assert tree_labels(app)[1] == [1, 2, 3, 4, 5]
    assert place(3, 3, "after").status_code == 302 and tree_labels(app)[1] == [1, 2, 3, 4, 5]  # onto itself: stays
    assert place(3, 99, "after").status_code == 404
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "rand", "id": "r1", "name": "Bild", "code": "bild", "k": 1,
             "children": [{"t": "block", "block": 3}, {"t": "block", "block": 4}]},
            {"t": "loop", "id": "l1", "name": "robot", "code": "robot", "columns": [], "rows": [], "mode": "all", "k": 1,
             "order": "random", "balanced": True, "children": [{"t": "block", "block": 5}]}]}
        db.session.commit()
    place(2, 4, "before")                                       # between the randomizer's blocks: joins it, and says so
    types, order = tree_labels(app)
    assert order == [1, 3, 2, 4, 5] and ("block", 1, 2) in types
    assert "Der Block gehört jetzt zu: 🎲 Bild" in client.get(E + "/").data.decode()
    place(2, 1, "after")                                        # and out again
    assert tree_labels(app)[0][:2] == [("block", 0, 1), ("block", 0, 2)]
    assert "in keiner Gruppe mehr" in client.get(E + "/").data.decode()
    place(2, 5, "after")                                        # a block alone in its loop: next to the loop, not into it
    assert tree_labels(app)[0][-2:] == [("block", 1, 5), ("block", 0, 2)]
    place(5, 1, "after")                                        # ... and it takes its loop along
    assert tree_labels(app)[0][:3] == [("block", 0, 1), ("loop", 0, None), ("block", 1, 5)]
    place(1, 4, "before")                                       # the consent goes next to a group, never into it
    assert tree_labels(app)[0] == [("loop", 0, None), ("block", 1, 5), ("block", 0, 1), ("rand", 0, None),
                                   ("block", 1, 3), ("block", 1, 4), ("block", 0, 2)]


def test_translation_tab_only_with_more_than_one_language(app, client, survey):
    """The tab "Übersetzung" stands in the menu only when the survey has more than one language (Ralf, 2026-10-11)."""
    nav = lambda path: client.get(path).data.decode().split('class="survey-tabs"')[1].split("</nav>")[0]
    assert "/translation" in nav("/surveys/1/flow/")                  # de + en
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de"]
        db.session.commit()
    assert "/translation" not in nav("/surveys/1/flow/")
    assert "/translation" in nav("/surveys/1/translation/")           # the page itself still names its tab
