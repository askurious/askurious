import json
import re
from datetime import datetime

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import add, fill_table


def setup(client):
    add(client, "single_choice")                                    # 2: Q
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "pref"), ("question", "Welche Farbe?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Rot"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Grün"), ("option_id", "c"), ("option_code", "3"),
        ("option_label", "Blau")]))
    add(client, "rating")                                           # 3: second question, 1..5
    client.post("/surveys/1/editor/items/3", data={"code": "like", "question": "Wie sehr?", "style": "numbers",
                                                   "min": "1", "max": "5"})
    add(client, "sample_crowd")                                     # 4: crowd for Q2
    client.post("/surveys/1/editor/items/4", data={"code": "crowd1", "question": "Andere", "source": "2",
                                                   "show_own": "1", "avatars": "40"})
    add(client, "sample_crowd")                                     # 5: crowd for Q3
    client.post("/surveys/1/editor/items/5", data={"code": "crowd2", "question": "Andere", "source": "3",
                                                   "show_own": "1", "avatars": "40"})


def _people(app, n):
    with app.app_context():
        s = db.session.get(Survey, 1)
        for i in range(n):
            s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 1),
                                        answers={"2": "123"[i % 3], "3": 1 + i % 5}))
        db.session.commit()


def _crowd(item, own=None):
    return item.kind.crowd(item, "de", own or {})


def test_invented_below_five_topped_up_below_n_sampled_above(app, client, survey):
    setup(client)
    _people(app, 4)
    with app.app_context():
        c1 = db.session.get(Item, 4)
        a = _crowd(c1)
        assert c1.kind.validate(c1) == [] and not a["enough"] and a["n"] == 4
        assert len(a["avatars"]) == 40 and a["simulated"] == 40                   # only invented figures
    _people(app, 8)                                                               # 12 real
    with app.app_context():
        c1, c2 = db.session.get(Item, 4), db.session.get(Item, 5)
        a, b = _crowd(c1), _crowd(c2)
        assert a["enough"] and a["n"] == 12 and len(a["avatars"]) == 40 and a["simulated"] == 28
        # Real people cannot be joined across questions by IDs or list position (TN-4).
        real_a = {x["id"] for x in a["avatars"] if not x["id"].startswith("sim")}
        real_b = {x["id"] for x in b["avatars"] if not x["id"].startswith("sim")}
        assert real_a.isdisjoint(real_b)
        assert [x["id"] for x in _crowd(c1)["avatars"]] == [x["id"] for x in a["avatars"]]
        xs = [x["x"] for x in a["avatars"]]
        assert all(0 < x < 1 for x in xs) and len(set(xs)) > 10                   # jitter within 3 bins
        assert _crowd(c1, {2: "3"})["own"] == (2 + 0.5) / 3
        assert _crowd(c2, {3: 5})["own"] == (4 + 0.5) / 5
    _people(app, 50)                                                              # 62 real > 40
    with app.app_context():
        c1 = db.session.get(Item, 4)
        one = c1.kind.crowd(c1, "de", {}, seed=1)
        two = c1.kind.crowd(c1, "de", {}, seed=2)
        assert one["simulated"] == 0 and len(one["avatars"]) == 40
        assert {x["id"] for x in one["avatars"]}.isdisjoint({x["id"] for x in two["avatars"]})
        assert [x["id"] for x in c1.kind.crowd(c1, "de", {}, seed=1)["avatars"]] == [x["id"] for x in one["avatars"]]


def test_preview_renders_crowd_with_live_field(app, client, survey):
    setup(client)
    _people(app, 6)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    m = re.search(r"data-crowd='([^']+)'", html)
    assert m, "crowd not rendered"
    data = json.loads(m.group(1).replace("&#34;", '"'))
    assert data["bins"] == 3 and data["live"]["name"] == "i2" and len(data["avatars"]) == 40
    assert 'name="i4_n_2" value="6"' in html and 'name="i4_sim" value="34"' in html and "34 simulierte Figuren" in html


def test_crowd_inside_a_loop_uses_the_same_pass(app, client, survey):
    """A question in a loop block: the crowd in the same block shows the others' answers of the same row."""
    setup(client)
    r = client.post("/surveys/1/editor/blocks/2/loop")              # loop around block 2 with two rows
    fill_table(client, r.headers["Location"].split("?")[0], robot=["Pflegeroboter", "Chatbot"])
    with app.app_context():
        from app import loops as lp

        rows = [row["id"] for row in lp.config(db.session.get(Survey, 1).blocks[1])["rows"]]
    with app.app_context():
        from app import loops

        s = db.session.get(Survey, 1)
        block = s.blocks[1]
        crowd = db.session.get(Item, 4)
        assert loops.active(block) and 2 in [c.id for c in crowd.kind.candidates(crowd)]
        for i in range(6):
            s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 1),
                                        answers={f"2#{rows[0]}": "1", f"2#{rows[1]}": "3"}))
        db.session.commit()
        a = crowd.kind.crowd(crowd, "de", {}, row=rows[0])
        b = crowd.kind.crowd(crowd, "de", {}, row=rows[1])
        assert a["n"] == 6 and b["n"] == 6
        assert all(x["x"] < 1 / 3 for x in a["avatars"] if not x["id"].startswith("sim"))    # row A: "Rot"
        assert all(x["x"] > 2 / 3 for x in b["avatars"] if not x["id"].startswith("sim"))    # row B: "Blau"
        # the other live comparisons share the pool: answers of the pass stand under the plain item id
        pool = crowd.kind.pool(crowd, row=rows[1])
        assert [a.get("2") for a in pool[-6:]] == ["3"] * 6


def test_previous_positions_option(app, client, survey):
    setup(client)
    with app.app_context():
        assert db.session.get(Item, 5).config["keep_previous"] is False       # saved without the box: off
    client.post("/surveys/1/editor/items/5", data={"code": "crowd2", "question": "Andere", "source": "3",
                                                   "show_own": "1", "avatars": "40", "keep_previous": "1"})
    with app.app_context():
        assert db.session.get(Item, 5).config["keep_previous"] is True
    assert "vorige Positionen sofort zeigen" in client.get("/surveys/1/editor/items/5").data.decode()
    add(client, "sample_crowd")                                            # new elements: on by default
    with app.app_context():
        assert max(db.session.scalars(db.select(Item)), key=lambda i: i.id).config["keep_previous"] is True
