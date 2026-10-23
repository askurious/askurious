"""Unterfragen in interview guides (app/interview.py, outline; concept unterfragen, Ralf 2026-10-18: A, F, E1-E6 as
recommended): a Leitfrage indented under the one above, with "Wann stellen?", shown indented in the guide, on its
Leitfrage's page of the interview view, in print, Word, the category system and the results."""
import xml.etree.ElementTree as ET

from app.extensions import db
from app.models import Item, Survey
from tests.interview.test_interview import block_of, docx_text, make
from tests.interview.test_interview_ai import fake_ai


def guide_with_questions(app, client, n=3):
    """A guide whose first Themenblock after "Einstieg" holds `n` Leitfragen. (code, survey id, block id, [item ids])"""
    code, sid = make(app, client)
    with app.app_context():
        from app import interview

        block = interview.guide_blocks(db.session.get(Survey, sid))[1]
        bid = block.id
    for _ in range(n - 1):
        client.post(f"/surveys/{sid}/editor/blocks/{bid}/items", data={"type": "guide_question"})
    with app.app_context():
        items = [i.id for b in db.session.get(Survey, sid).blocks if b.id == bid for i in b.items]
    return code, sid, bid, items


def act(client, code, action, item):
    return client.post(f"/guide/{code}/control", data={"action": action, "item": item})


def shape(app, bid):
    """[(item id, id of its Leitfrage or None)] of a block."""
    from app import interview
    from app.models import Block

    with app.app_context():
        return [(i.id, m.id if m else None) for i, m in interview.outline(db.session.get(Block, bid))]


def test_indent_and_outdent(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    act(client, code, "indent", a)                                  # the first of a block has nothing to belong to
    assert shape(app, bid) == [(a, None), (b, None), (c, None)]
    with app.app_context():
        item = db.session.get(Item, b)
        item.config = {**item.config, "core": True}
        db.session.commit()
    r = act(client, code, "indent", b)
    assert r.status_code == 302 and f"item={b}" in r.headers["Location"]
    act(client, code, "indent", c)
    assert shape(app, bid) == [(a, None), (b, a), (c, a)]
    with app.app_context():
        from app import interview
        from app.models import Block

        item = db.session.get(Item, b)
        assert item.config["sub"] is True and item.config["core"] is False   # never a Kernfrage (E3)
        assert item.kind.minutes(item) == 0                                  # counts towards its Leitfrage
        assert interview.planned_minutes(db.session.get(Block, bid)) == db.session.get(Item, a).kind.minutes(db.session.get(Item, a))
    page = client.get(f"/guide/{code}").data.decode()
    assert page.count(' guide-sub" id="item-') == 2 and "+ Unterfrage" in page and "2 Unterfragen" in page
    act(client, code, "outdent", b)                                 # c now belongs to b
    assert shape(app, bid) == [(a, None), (b, None), (c, b)]


def test_when_to_ask_only_for_a_sub_question(app, client):
    code, sid, bid, (a, b, _) = guide_with_questions(app, client)
    act(client, code, "indent", b)
    page = client.get(f"/surveys/{sid}/editor/items/{b}?embed=1").data.decode()
    assert "Wann stellen?" in page and 'value="falls zutreffend"' in page and 'name="core"' not in page
    # a Leitfrage has no "Wann stellen?" of its own (its Nachfragen under the concrete questions have, bedingungen-leitfaden)
    assert 'name="when"' not in client.get(f"/surveys/{sid}/editor/items/{a}?embed=1").data.decode()
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Wie war das mit den Kindern?", "when": "falls zutreffend",
                                                           "core": "1", "minutes": "30"})
    client.post(f"/surveys/{sid}/editor/items/{a}", data={"question": "Erzählen Sie.", "when": "nie", "minutes": "20"})
    with app.app_context():
        sub, main = db.session.get(Item, b), db.session.get(Item, a)
        assert sub.text("when") == "falls zutreffend" and not sub.config.get("core") and sub.config["minutes"] == 10
        assert main.text("when") == "" and main.config["minutes"] == 20


def test_add_a_sub_question(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    act(client, code, "indent", b)
    r = act(client, code, "add_sub", b)                             # to the Leitfrage of the chosen Unterfrage
    assert "neu=1" in r.headers["Location"]
    new = shape(app, bid)[2][0]
    assert shape(app, bid) == [(a, None), (b, a), (new, a), (c, None)]
    with app.app_context():
        assert db.session.get(Item, new).type == "guide_question" and db.session.get(Item, new).config["sub"] is True


def test_moving_keeps_groups_together(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    act(client, code, "indent", b)
    act(client, code, "item_down", a)                               # a with b below c
    assert shape(app, bid) == [(c, None), (a, None), (b, a)]
    act(client, code, "item_up", b)                                  # an Unterfrage stays in its group
    assert shape(app, bid) == [(c, None), (a, None), (b, a)]
    act(client, code, "add_sub", a)
    new = shape(app, bid)[3][0]
    act(client, code, "item_up", new)
    assert shape(app, bid) == [(c, None), (a, None), (new, a), (b, a)]


def test_deleting_a_leitfrage_keeps_its_sub_questions(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    act(client, code, "indent", b)
    act(client, code, "indent", c)
    r = act(client, code, "delete_item", a)
    assert r.status_code == 302
    assert shape(app, bid) == [(b, None), (c, None)]                 # E4: they stand as Leitfragen
    with app.app_context():
        assert not db.session.get(Item, b).config["sub"] and not db.session.get(Item, c).config["sub"]


def test_drag_and_drop(app, client):
    code, sid, bid, (a, b, c, d) = guide_with_questions(app, client, 4)
    act(client, code, "indent", b)                                   # a > b, c, d

    def place(item, pos, block=bid):
        return client.post(f"/guide/{code}/items/{item}/place", data={"block_id": block, "position": pos})

    place(d, 1)                                                      # between a and its Unterfrage b: an Unterfrage
    assert shape(app, bid) == [(a, None), (d, a), (b, a), (c, None)]
    place(d, 3)                                                      # after c: a Leitfrage again
    assert shape(app, bid) == [(a, None), (b, a), (c, None), (d, None)]
    place(a, 3)                                                      # a takes b along (positions among the other rows)
    assert shape(app, bid) == [(c, None), (d, None), (a, None), (b, a)]
    other = block_of(app, sid, "guide")                              # "Einstieg": an info and a Leitfrage
    place(a, 0, other)                                               # into another block, at the top
    with app.app_context():
        from app import interview
        from app.models import Block

        moved = [(i.id, m.id if m else None) for i, m in interview.outline(db.session.get(Block, other))]
    assert moved[:2] == [(a, None), (b, a)]
    short = block_of(app, sid, "short")
    assert place(c, 0, short).status_code == 302 and shape(app, short)[0][0] != c   # a Leitfrage stays among the Themenblöcke


def test_drop_onto_a_leitfrage(app, client):
    """A Leitfrage dropped onto the middle of another one becomes its Unterfrage, after its last one; onto an
    Unterfrage, right after it. Its own Unterfragen come along (one level); Rückgängig restores the old order (Ralf
    2026-10-18)."""
    code, sid, bid, (a, b, c, d) = guide_with_questions(app, client, 4)
    act(client, code, "indent", b)                                   # a > b, c, d

    def onto(item, on, block=bid):
        return client.post(f"/guide/{code}/items/{item}/place", data={"block_id": block, "position": 0, "onto": on})

    assert onto(d, a).status_code == 302                             # d onto a: after b
    assert shape(app, bid) == [(a, None), (b, a), (d, a), (c, None)]
    act(client, code, "outdent", d)
    onto(d, b)                                                       # d onto the Unterfrage b: right after b
    assert shape(app, bid) == [(a, None), (b, a), (d, a), (c, None)]
    act(client, code, "outdent", d)
    act(client, code, "item_down", d)                                # a > b, c, d
    act(client, code, "indent", d)                                   # a > b, c > d
    onto(c, a)                                                       # c takes d along: both under a
    assert shape(app, bid) == [(a, None), (b, a), (c, a), (d, a)]
    with app.app_context():
        from app.models import Item as It

        assert not db.session.get(It, c).config.get("core")
    client.post(f"/surveys/{sid}/editor/undo")
    assert shape(app, bid) == [(a, None), (b, a), (c, None), (d, c)]
    assert onto(a, b).status_code == 400                             # never onto its own Unterfrage
    assert onto(a, a).status_code == 400
    other = block_of(app, sid, "guide")                              # "Einstieg": its Leitfrage
    with app.app_context():
        from app import interview
        from app.models import Block

        first = next(i.id for i, m in interview.outline(db.session.get(Block, other)) if i.type == "guide_question")
    onto(c, first, other)                                            # into another block: c and d under its Leitfrage
    assert [(i, m) for i, m in shape(app, other) if i in (c, d)] == [(c, first), (d, first)]
    assert shape(app, bid) == [(a, None), (b, a)]


def test_guide_rows_say_where_a_drop_nests(app, client):
    code, sid, bid, (a, b) = guide_with_questions(app, client, 2)
    act(client, code, "indent", b)
    page = client.get(f"/guide/{code}").get_data(as_text=True)
    assert f'data-onto="{a}" data-onto-main="{a}"' in page and f'data-onto="{b}" data-onto-main="{a}"' in page


def test_interview_view_shows_sub_questions_on_their_leitfrage(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Wie war das mit den Kindern?"})
    act(client, code, "indent", b)
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Wie war das mit den Kindern?", "when": "falls zutreffend"})
    with app.app_context():
        from app import interview

        steps = interview.steps(db.session.get(Survey, sid))
        assert b not in [s["item"].id for s in steps if s["item"]]   # no page of its own (E1)
        assert [s["subs"] for s in steps if s["item"] and s["item"].id == a][0][0].id == b
    page = client.get(f"/guide/{code}/conversation").data.decode()
    assert f'data-iv-sub="{b}"' in page and f'data-iv-sub-mark="{b}"' in page and f'data-iv-state="{b}"' in page
    assert "falls zutreffend" in page and "Stand der Unterfrage" in page
    assert '<span>Anlass</span>' in page                             # the tick's text beside its box, not below it


def test_documents_category_system_and_results(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    client.post(f"/surveys/{sid}/editor/items/{a}", data={"question": "Erzählen Sie vom Alltag."})
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Wie war das mit den Kindern?"})
    act(client, code, "indent", b)
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Wie war das mit den Kindern?", "when": "falls zutreffend"})
    page = client.get(f"/guide/{code}/print").data.decode()
    assert 'class="guide-print-sub"' in page and "falls zutreffend" in page
    assert "↳ Wie war das mit den Kindern?" in docx_text(client.get(f"/guide/{code}/export/leitfaden.docx").data)
    assert "falls zutreffend: Wie war das mit den Kindern?" in docx_text(client.get(f"/guide/{code}/export/interviewkarte.docx").data)
    root = ET.fromstring(client.get(f"/guide/{code}/export/category-system.qdc").data)
    ns = {"q": root.tag.split("}")[0][1:]}
    main = next(e for e in root.iter(f"{{{ns['q']}}}Code") if e.get("name") == "Erzählen Sie vom Alltag.")
    assert "Wie war das mit den Kindern?" in [e.get("name") for e in main.findall("q:Code", ns)]   # a code under it (E5)
    with app.app_context():
        from app import interview_results

        rows = [r for g in interview_results.coverage(db.session.get(Survey, sid), []) for r in g["rows"]]
        assert [r["sub"] for r in rows if r["item"].id in (a, b)] == [False, True]                 # E6


def test_ai_sees_and_suggests_sub_questions(app, client, monkeypatch):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    act(client, code, "indent", b)
    calls = fake_ai(monkeypatch, {"interview_extend": {"aspects": [], "keep": [], "probe": [],
                                                       "subs": [{"text": "Wie ist es mit Kindern?", "when": "falls zutreffend", "why": "fehlt"}]}})
    page = client.post(f"/guide/{code}/ai/guide-question/{a}?embed=1").data.decode()
    assert 'value="Wie ist es mit Kindern?"' in page and "Suggest sub-questions too." in calls[0]["prompt"]
    client.post(f"/guide/{code}/ai/guide-question/{a}?embed=1", data={"action": "apply", "take_subs": ["0"],
                                                                     "subs0": "Wie ist es mit Kindern?", "subs_when0": "falls zutreffend"})
    rows = shape(app, bid)
    assert [m for _, m in rows] == [None, a, a, None]
    with app.app_context():
        new = db.session.get(Item, rows[2][0])
        assert new.text("question") == "Wie ist es mit Kindern?" and new.text("when") == "falls zutreffend"
    page = client.post(f"/guide/{code}/ai/guide-question/{b}?embed=1").data.decode()   # an Unterfrage gets none of its own
    assert "Do not suggest sub-questions" in calls[-1]["prompt"] and 'name="take_subs"' not in page
    with app.app_context():
        from app import interview_ai

        assert f"sub-question of [{db.session.get(Item, a).code}]" in interview_ai.guide_lines(db.session.get(Survey, sid))


def test_survey_file_keeps_sub_questions(app, client):
    code, sid, bid, (a, b, c) = guide_with_questions(app, client)
    act(client, code, "indent", b)
    client.post(f"/surveys/{sid}/editor/items/{b}", data={"question": "Kinder?", "when": "falls zutreffend"})
    with app.app_context():
        from app import transfer

        data = transfer.dump(db.session.get(Survey, sid), with_files=False)
    items = [i for blk in data["blocks"] for i in blk["items"] if i["type"] == "guide_question"]
    sub = next(i for i in items if (i.get("config") or {}).get("sub"))
    assert sub["texts"]["de"]["when"] == "falls zutreffend"
