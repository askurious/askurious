from app.extensions import db
from app.models import Survey
from tests.conftest import add


def test_duration_follows_flow(app, client, survey):
    from app import duration

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})   # block 2
    client.post("/surveys/1/editor/blocks")                                               # block 3
    client.post("/surveys/1/editor/blocks")                                               # block 4
    add(client, "text_input", block=3)
    client.post("/surveys/1/editor/items/3", data={"code": "long", "question": "Bitte beschreiben Sie ausführlich",
                                                    "multiline": "1", "validation": "none"})
    add(client, "single_choice", block=4)
    with app.app_context():
        s = db.session.get(Survey, 1)
        est = duration.estimate(s)
        assert est["low"] == est["typical"] == est["high"] > 60           # no randomization: one path
        per = est["per_block"]
        assert per[2] > per[4] and per[3] > 30                            # 10 Likert items; long free text
        # randomize: show 1 of blocks 3 and 4 -> range between the shorter and the longer one
        from app import flow

        flow.normalize(s)
        root = flow.tree(s)
        kids = [n for n in root["children"] if n["t"] == "block" and n["block"] in (3, 4)]
        root["children"] = [n for n in root["children"] if n not in kids] + [
            {"t": "rand", "id": "r1", "code": "cond", "k": 1, "balanced": True, "children": kids}]
        s.flow = root
        db.session.commit()
        est2 = duration.estimate(s)
        assert est2["low"] < est2["typical"] < est2["high"] and est2["has_random"]
        assert round(est2["high"] - est2["low"]) == round(abs(per[3] - per[4]))
        assert duration.label(est2).startswith("ca. ")
    assert "Geschätzte Dauer" in client.get("/surveys/1/editor/").data.decode()
    assert "Schätzung:" in client.get("/surveys/1/editor/items/1").data.decode()
    assert "Geschätzte Bearbeitungszeit" in client.get("/surveys/1/field").data.decode()


def test_if_else_counts_exactly_one_branch(app, client, survey):
    from app import duration, flow

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})   # block 2: long
    client.post("/surveys/1/editor/blocks")                                               # block 3: short
    add(client, "single_choice", block=3)
    with app.app_context():
        s = db.session.get(Survey, 1)
        flow.normalize(s)
        per = duration.estimate(s)["per_block"]
        consent = per[1]
        branch = flow.add_node(s, "branch")
        flow.place(s, "k2", branch["id"], "into")
        flow.add_else(s, branch["id"])
        other = next(n for n, _, _ in flow.walk(flow.tree(s)) if n["t"] == "else")
        flow.place(s, "k3", other["id"], "into")
        db.session.commit()
        est = duration.estimate(s)
        assert round(est["low"]) == round(consent + per[3])        # the shorter branch, not none
        assert round(est["high"]) == round(consent + per[2])       # the longer branch, not both
        assert est["low"] < est["typical"] < est["high"]
        short = {"low": 35, "typical": 95, "high": 95}
        assert duration.label(short) == "ca. 1,6 Min. (0,6–1,6)"   # short surveys with one decimal (U14)
        assert duration.label({"low": 33, "typical": 44, "high": 52}) == "ca. 45 s (35–50)"  # under a minute: seconds
        assert duration.label({"low": 300, "typical": 420, "high": 540}) == "ca. 7 Min. (5–9)"


def test_minutes_round_half_up():
    """D-11: 150 s read 3 minutes (round() gave 2), 2:30 to the 5 seconds stays."""
    from app import duration

    assert duration.minutes(150) == 3 and duration.minutes(90) == 2 and duration.minutes(20) == 1
    assert duration.short(12.5) == "13 s" and duration.short(152.5) == "2:33 Min."


def test_estimate_short_one_way_of_writing():
    """UI audit 2026-10-12, U14: an estimate is "55 s" or "1,2 Min.", never "1:09 Min." beside "5 Min."."""
    from app import duration

    assert duration.estimate_short(55) == "55 s"
    assert duration.estimate_short(69) == "1,2 Min."
    assert duration.estimate_short(153) == "2,6 Min."


def test_an_end_inside_a_branch_splits_the_rest(monkeypatch):
    """Mathe-Audit Q-12: block 1 (60 s); a branch without ELSE with [block 2 (30 s), end]; block 3 (300 s). Either the
    branch applies and the survey ends after 90 s, or it does not and block 3 follows (360 s); 390 s is no way."""
    from types import SimpleNamespace

    from app import duration

    monkeypatch.setattr(duration, "block_seconds", lambda block, lang: block.s)
    blocks = {1: SimpleNamespace(id=1, s=60), 2: SimpleNamespace(id=2, s=30), 3: SimpleNamespace(id=3, s=300)}
    nodes = [{"t": "block", "block": 1},
             {"t": "branch", "id": "b", "children": [{"t": "block", "block": 2}, {"t": "end"}]},
             {"t": "block", "block": 3}]
    assert duration._sequence(nodes, blocks, "de", {}) == (90.0, 225.0, 360.0, False)
    # IF with ELSE, the IF ends the survey: 60 + 30, or 60 + 10 (ELSE) + 300
    blocks[4] = SimpleNamespace(id=4, s=10)
    nodes = [{"t": "block", "block": 1},
             {"t": "branch", "id": "b", "children": [{"t": "block", "block": 2}, {"t": "end"}]},
             {"t": "else", "of": "b", "children": [{"t": "block", "block": 4}]},
             {"t": "block", "block": 3}]
    assert duration._sequence(nodes, blocks, "de", {}) == (90.0, 230.0, 370.0, False)
    # without any end: as before (low without the branch, typical and high with it)
    nodes = [{"t": "block", "block": 1}, {"t": "branch", "id": "b", "children": [{"t": "block", "block": 2}]}]
    assert duration._sequence(nodes, blocks, "de", {}) == (60.0, 90.0, 90.0, False)


def test_design_check_warns_when_the_consent_names_another_duration(app, client, survey):
    from app import design

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        consent = s.consent_item
        consent.config = {**consent.config, "duration": 30}            # typed long before the questionnaire existed
        db.session.commit()
        assert any("Genannt sind etwa 30 Minuten" in f.message for f in design.check(s))
        from app import duration

        consent.config = {**consent.config, "duration": duration.minutes(duration.estimate(s)["typical"])}
        db.session.commit()
        assert not any("genannt sind etwa" in f.message for f in design.check(s))


def test_a_number_from_memory_takes_twelve_seconds(app, client, survey):
    """Pre-test 2026-10-23 (E1): "Wie viele Minuten haben Sie heute ...?" asks to remember and add up; it counts
    12 s of answering, not the 6 s of a short typed answer."""
    from app import duration
    from app.models import Item

    add(client, "text_input")
    with app.app_context():
        item_id = db.session.scalar(db.select(db.func.max(Item.id)).where(Item.type == "text_input"))
    client.post(f"/surveys/1/editor/items/{item_id}", data={"code": "minuten", "question": "x", "validation": "integer"})
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.config.get("validation") == "integer"
        as_number = duration.item_seconds(item, "de")
        item.config = {**item.config, "validation": "none"}
        assert abs(as_number - duration.item_seconds(item, "de") - (12 - 6)) < 1e-6
        assert duration.on_phone("m1") and not duration.on_phone("entry") and not duration.on_phone("exit")
