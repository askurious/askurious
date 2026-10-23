import json

from app import ai, ai_scales
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add

DESC = ("Wir untersuchen, wie Menschen Pflegeroboter im Alltag wahrnehmen und unter welchen Bedingungen sie ihnen "
        "vertrauen. Sie sehen kurze Szenarien und beantworten Fragen dazu.")


def _describe(client):
    from werkzeug.datastructures import MultiDict

    client.post("/surveys/1/meta", data=MultiDict([("title", "Pflege"), ("description", DESC),
                                                   ("research_question", "Technikvertrauen erhöht die Akzeptanz."),
                                                   ("languages", "de"), ("languages", "en"), ("default_language", "de")]))


def test_research_question_saved_and_in_context(app, client, survey):
    _describe(client)
    add(client, "single_choice")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.research_question == "Technikvertrauen erhöht die Akzeptanz."
        ctx = ai_scales.context(s)
        assert "Research question" in ctx and "Einfachauswahl" in ctx and ai_scales.enough_context(s)
    assert "Forschungsfrage" in client.get("/surveys/1/researchdesign", follow_redirects=True).data.decode()


def test_ideas_need_context_and_come_as_json(app, client, survey, monkeypatch):
    r = client.post("/surveys/1/editor/ai/ideas").get_json()
    assert "fehlt noch Kontext" in r["error"]
    _describe(client)
    monkeypatch.setattr(ai, "generate_text", lambda i, p, **kw: 'Hier: [{"name": "Vertrauen in Technik", "why": "zentral"}]')
    r = client.post("/surveys/1/editor/ai/ideas").get_json()
    assert r["ideas"] == [{"name": "Vertrauen in Technik", "why": "zentral"}]


def test_ai_scale_is_marked_everywhere(app, client, survey, monkeypatch):
    _describe(client)
    seen = {}

    def fake(instructions, prompt, **kw):
        seen["prompt"] = prompt
        return json.dumps([{"reverse": False, "text": {"de": "Ich traue dem Roboter.", "en": "I trust the robot."}},
                           {"reverse": True, "text": {"de": "Ich bin skeptisch.", "en": "I am sceptical."}}])
    monkeypatch.setattr(ai, "generate_text", fake)
    r = client.post("/surveys/1/editor/blocks/2/ai-scale", data={"idea": "Vertrauen in Pflegeroboter", "n": "2", "reverse": "1"})
    assert r.status_code == 302 and "Construct idea (from the researcher): Vertrauen in Pflegeroboter" in seen["prompt"]
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.type == "likert" and item.config["ai"]["idea"] == "Vertrauen in Pflegeroboter"
        assert [s["reverse"] for s in item.config["statements"]] == [False, True]
        assert item.kind.statements(item, "en")[0]["text"] == "I trust the robot."
        assert all("KI-generiert" in label for _, label in item.kind.export_columns(item))
        from app import design

        assert any("KI-generierte Skala" in f.message for f in design.check(db.session.get(Survey, 1)))
    editor = client.get("/surveys/1/editor/").data.decode()
    assert "row-ai" in editor and "KI-generiert" in editor and "Skala mit KI entwerfen" in editor
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert "KI-generiert, nicht validiert" in page and "Weitere Items vorschlagen" in page
    client.post("/surveys/1/editor/items/2/ai-more", data={"n": "2", "reverse": "1"})
    with app.app_context():
        assert len(db.session.get(Item, 2).config["statements"]) == 4
    assert "Do not repeat" not in seen["prompt"]  # the avoid list goes into the instructions, not the context


def test_ai_unavailable_gives_message(app, client, survey, monkeypatch):
    _describe(client)

    def broken(i, p, **kw):
        raise ai.AIError("Kein OpenAI-Schlüssel konfiguriert.")
    monkeypatch.setattr(ai, "generate_text", broken)
    r = client.post("/surveys/1/editor/blocks/2/ai-scale", data={"idea": "x"}, follow_redirects=True)
    assert "KI nicht verfügbar" in r.data.decode()
    with app.app_context():
        assert len(db.session.get(Survey, 1).blocks[1].items) == 0


def test_half_reverse_keyed_whatever_the_model_returns(app, client, survey, monkeypatch):
    _describe(client)
    many = [{"reverse": r, "text": {"de": f"Aussage {n}", "en": f"Statement {n}"}}
            for n, r in enumerate([False] * 6 + [True] * 6)]
    monkeypatch.setattr(ai, "generate_text", lambda i, p, **kw: json.dumps(many))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for n, rev in ((6, 3), (5, 2), (3, 1), (2, 1)):
            items = ai_scales.draft_items(s, "Vertrauen", n)
            assert len(items) == n and sum(i["reverse"] for i in items) == rev
        assert [i["reverse"] for i in ai_scales.draft_items(s, "Vertrauen", 4)] == [False, True, False, True]
        assert not any(i["reverse"] for i in ai_scales.draft_items(s, "Vertrauen", 3, reverse=False))
