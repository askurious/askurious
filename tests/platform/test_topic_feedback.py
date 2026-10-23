"""Topic map and topic profile (app/items/topic_feedback.py; experimental until 2026-10-11): after a loop, one's own
evaluations of every topic, and on request the sample's (Philipp, 2026-10-07)."""
from werkzeug.datastructures import MultiDict

from app import flow
from app.extensions import db
from app.models import Item, Response, Survey, utcnow
from tests.conftest import add

TOPICS = ("Pflegeroboter", "Autonomes Fahren", "KI in der Medizin")


def build(app, client):
    """Block 2 in a loop over three topics with risk (2) and benefit (3) as ratings; block 3 after the loop holds
    the topic map (4) and the topic profile (5)."""
    add(client, "rating", code="risk")                                                    # 2
    add(client, "rating", code="benefit")                                                 # 3
    client.post("/surveys/1/editor/blocks")                                               # block 3
    add(client, "topic_map", block=3)                                                     # 4
    add(client, "topic_profile", block=3)                                                 # 5
    with app.app_context():
        s = db.session.get(Survey, 1)
        rest = [n for n in flow.tree(s)["children"] if not (n["t"] == "block" and n["block"] in (1, 2))]
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1},
            {"t": "loop", "id": "l1", "name": "Themen", "code": "topic", "columns": [{"id": "c1", "name": "topic", "hidden": False}],
             "rows": [{"id": f"r{n}", "values": {"c1": {"de": t, "en": t}}} for n, t in enumerate(TOPICS, start=1)],
             "mode": "all", "k": 1, "order": "fixed", "balanced": True, "children": [{"t": "block", "block": 2}]}] + rest}
        flow.normalize(s)
        for item_id in (2, 3):
            item = db.session.get(Item, item_id)
            item.texts = {**item.texts, "de": {**item.texts.get("de", {}), "question": "Risiko" if item_id == 2 else "Nutzen"}}
        db.session.commit()
    client.post("/surveys/1/editor/items/4", data={"code": "karte", "question": "Ihre Themen", "source": "2", "source_y": "3",
                                                   "show_sample": "1"})
    client.post("/surveys/1/editor/items/5", data=MultiDict([("code", "profil"), ("question", "Im Mittel"), ("sources", "2"),
                                                             ("sources", "3"), ("show_sample", "1")]))


def answers(risk, benefit):
    """Answers of one person: (risk, benefit) per topic r1..r3; None leaves the topic out."""
    out = {}
    for n, (r, b) in enumerate(zip(risk, benefit), start=1):
        if r is not None:
            out[f"2#r{n}"], out[f"3#r{n}"] = r, b
    return out


def people(app, rows, source="real"):
    with app.app_context():
        for risk, benefit in rows:
            db.session.add(Response(survey_id=1, source=source, status=Response.COMPLETED, completed_at=utcnow(),
                                    answers=answers(risk, benefit)))
        db.session.commit()


def test_topic_map_puts_each_topic_on_two_evaluations(app, client, survey):
    build(app, client)
    with app.app_context():
        item = db.session.get(Item, 4)
        assert item.config["sources"] == [2, 3] and item.kind.validate(item) == []
        assert [t for _, t in item.kind.topics(item, "de")] == list(TOPICS)
        own = {"2#r1": 4, "3#r1": 2, "2#r2": 1, "3#r2": 5}                              # the third topic not rated
        m = item.kind.map(item, "de", own)
        assert [(p["name"], p["own"]) for p in m["points"]] == [("Pflegeroboter", (4.0, 2.0)), ("Autonomes Fahren", (1.0, 5.0))]
        assert m["few"] and not m["sample_shown"] and m["n"] == 0 and "Pflegeroboter" in m["svg"]
    people(app, [((2, 3, 5), (4, 4, 1))] * 4)                                             # four: below the threshold
    people(app, [((4, 1, 5), (2, 2, None))])                                              # a fifth; topic 3 incomplete
    with app.app_context():
        item = db.session.get(Item, 4)
        m = item.kind.map(item, "de", own)
        by = {p["name"]: p for p in m["points"]}
        assert by["Pflegeroboter"]["sample"] == (2.4, 3.6) and by["Pflegeroboter"]["n"] == 5
        assert "KI in der Medizin" not in by                    # not rated by oneself, 4 complete pairs: left out
        assert m["sample_shown"] and m["n"] == 5 and 'class="p-topic-link"' in m["svg"]
        item.config = {**item.config, "show_sample": False}
        assert not any(p["sample"] for p in item.kind.map(item, "de", own)["points"])


def test_topic_profile_contrasts_own_mean_with_the_mean_of_means(app, client, survey):
    build(app, client)
    with app.app_context():
        item = db.session.get(Item, 5)
        assert item.config["sources"] == [2, 3] and item.kind.validate(item) == []
    # four people rate all three topics, one only the first: each person counts once, with their own mean
    people(app, [((1, 2, 3), (5, 5, 5))] * 4 + [((5, None, None), (1, None, None))])
    with app.app_context():
        item = db.session.get(Item, 5)
        pr = item.kind.profile(item, "de", {"2#r1": 4, "3#r1": 2, "2#r2": 2, "3#r2": 4})
        risk, benefit = pr["rows"]
        assert risk["own"] == 3.0 and benefit["own"] == 3.0
        assert abs(risk["sample"] - (2 * 4 + 5) / 5) < 1e-9 and abs(benefit["sample"] - (5 * 4 + 1) / 5) < 1e-9
        assert pr["n"] == 5 and risk["own_text"] == "3,0" and "Stichprobe 2,6" in pr["svg"] and "Sie 3,0" in pr["svg"]
        assert item.kind.parse_answer(item, {"i5_n": "5"}) == {"n": 5}
        assert item.kind.export_values(item, {"n": 5}) == {"profil_n": 5}


def test_topic_feedback_belongs_after_the_loop_and_is_experimental(app, client, survey):
    build(app, client)
    with app.app_context():
        item = db.session.get(Item, 5)
        item.block_id = 2                                                                 # moved into the loop
        db.session.commit()
        assert any("hinter die Schleife" in p for p in item.kind.validate(item))
        assert item.kind.candidates(item) == []
    html = client.get("/surveys/1/editor/items/4").data.decode()
    assert '<select name="source_y"' in html and "mit der Stichprobe vergleichen" in html


def test_topic_map_in_the_participant_view(app, client, survey):
    build(app, client)
    people(app, [((2, 3, 5), (4, 4, 1))] * 5, source="test")
    with app.app_context():
        item = db.session.get(Item, 4)
        assert item.kind.map(item, "de", {})["n"] == 0                                    # test data: only in the preview
        assert item.kind.map(item, "de", {}, preview=True)["n"] == 5
        from flask import render_template

        with app.test_request_context():
            html = render_template("items/topic_map/participant.html", item=item, kind=item.kind, lang="de",
                                   answers={"2#r1": 4, "3#r1": 2}, preview=True, ui=lambda de, en: de)
            assert 'class="p-map p-topic-map"' in html and "Pflegeroboter: Sie 4,0 / 2,0" in html
            assert 'name="i4_n" value="5"' in html
            item5 = db.session.get(Item, 5)
            html = render_template("items/topic_profile/participant.html", item=item5, kind=item5.kind, lang="de",
                                   answers={"2#r1": 4, "3#r1": 2}, preview=True, ui=lambda de, en: de)
            assert 'class="p-profile"' in html and "Durchschnitt aller Teilnehmenden" in html


def test_labels_show_a_gap_for_placeholders(app, client, survey):
    """A question that names the topic ({{topic}}) labels an axis or row for all topics and all participants: the
    placeholder stands there as "…" (or its text for an empty answer), not as {{topic}}."""
    from app.items.sample_compare import label_text

    assert label_text("Wie riskant finden Sie „{{topic}}“?") == "Wie riskant finden Sie …?"
    assert label_text("How useful is “{{ topic }}”?") == "How useful is …?"
    assert label_text("Wie gefällt Ihnen {{produkt|Ihr Produkt}}?") == "Wie gefällt Ihnen Ihr Produkt?"
    build(app, client)
    with app.app_context():
        risk = db.session.get(Item, 2)
        risk.texts = {**risk.texts, "de": {**risk.texts["de"], "question": "Wie riskant ist „{{topic}}“?"}}
        db.session.commit()
        item = db.session.get(Item, 4)
        m = item.kind.map(item, "de", {"2#r1": 4, "3#r1": 2})
        assert m["x"][2] == "Wie riskant ist …?" and "{{" not in m["svg"]
        item5 = db.session.get(Item, 5)
        assert item5.kind.profile(item5, "de", {})["rows"][0]["name"] == "Wie riskant ist …?"


def test_scale_profile_shows_each_big_five_dimension(app, client, survey):
    """The scale profile: one row per chosen variable, here the five subscales of the BFI-10, one's own value and
    the sample's mean (from 5 persons)."""
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})     # 2
    add(client, "page_break")                                                              # 3
    add(client, "scale_profile")                                                           # 4
    editor = client.get("/surveys/1/editor/items/4").data.decode()
    assert all(f'value="2:{d}"' in editor for d in "EACNO")
    client.post("/surveys/1/editor/items/4", data=MultiDict([("code", "profil"), ("question", "Ihr Profil")]
                                                            + [("sources", f"2:{d}") for d in "EACNO"] + [("show_sample", "1")]))
    with app.app_context():
        item = db.session.get(Item, 4)
        assert item.config["sources"] == [2] * 5 and item.config["subscales"] == list("EACNO")
        assert item.kind.validate(item) == []
        statements = db.session.get(Item, 2).config["statements"]
        own = {2: {s["id"]: 1 if s.get("reverse") else 5 for s in statements}}          # 5 on every dimension
        for _ in range(5):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": {s["id"]: 3 for s in statements}}))
        db.session.commit()
        pr = item.kind.profile(item, "de", own)
        assert [r["name"][-3:] for r in pr["rows"]] == ["(E)", "(A)", "(C)", "(N)", "(O)"]
        assert all(r["own"] == 5.0 and r["sample"] == 3.0 for r in pr["rows"]) and pr["n"] == 5
        assert "Sie 5,0" in pr["svg"] and "Stichprobe 3,0" in pr["svg"]
        assert item.kind.export_values(item, {"n": 5}) == {"profil_n": 5}


def test_profile_labels_stay_inside_at_the_ends():
    from app.items.topic_feedback import _profile_svg

    row = {"name": "Technik", "lo": 1.0, "hi": 5.0, "low": "", "high": "", "own": 5.0, "sample": 1.0,
           "own_text": "5,0", "sample_text": "1,0"}
    svg = str(_profile_svg([row], "de"))
    assert 'text-anchor="end">Sie 5,0' in svg and 'text-anchor="start">Stichprobe 1,0' in svg


def test_feedback_types_are_for_everyone(app, client, survey):
    """No longer experimental (Ralf, 2026-10-11): the editor offers them without a switch."""
    from app import experimental, items

    assert "topic_maps" not in experimental.KEYS
    for key in ("topic_map", "topic_profile", "scale_profile"):
        assert getattr(items.REGISTRY[key], "experimental", None) is None
        assert client.post("/surveys/1/editor/blocks/2/items", data={"type": key}).status_code == 302
