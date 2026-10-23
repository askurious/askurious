"""AI assistant of a live vote (app/live_ai.py): a description in, five questions out, the ticked ones added."""
import json

from app import ai
from app.extensions import db
from app.models import Survey

ANSWER = {"questions": [
    {"type": "rating", "question": "Wie passte das Tempo?", "style": "smileys", "why": "Tempo"},
    {"type": "single_choice", "question": "Hat die Übung geholfen?", "options": ["ja", "teils", "nein"], "why": "Übung"},
    {"type": "multiple_choice", "question": "Was war unklar?", "options": ["Begriffe", "Beispiele", "Ablauf"], "why": ""},
    {"type": "text_input", "question": "Ein Wort zur Vorlesung", "why": "Stimmung"},
    {"type": "single_choice", "question": "Nur eine Antwort", "options": ["x"], "why": "unbrauchbar"},  # too few options
    {"type": "slider", "question": "Kein Live-Typ"},
]}


def test_describe_suggest_and_take_over(app, client, monkeypatch):
    seen = {}

    def fake(instructions, prompt, purpose="text", item_type=None):
        seen.update(instructions=instructions, prompt=prompt)
        return "Hier: " + json.dumps(ANSWER)

    monkeypatch.setattr(ai, "generate_text", fake)
    client.post("/presenter/new", data={"title": "MTI", "language": "de-du"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(title="MTI"))
        code, sid = survey.code, survey.id
    assert f"/presenter/{code}/ai" in client.get(f"/presenter/{code}").data.decode()
    page = client.post(f"/presenter/{code}/ai", data={"description": "Vorlesung, 120 Studierende; Tempo und Übung?"}).data.decode()
    assert "du" in seen["instructions"] and "120 Studierende" in seen["prompt"]
    assert "Wie passte das Tempo?" in page and "Kein Live-Typ" not in page and "Nur eine Antwort" not in page
    r = client.post(f"/presenter/{code}/ai", data={
        "action": "apply", "take": ["0", "1", "3"],
        "q0_type": "rating", "q0_question": "Wie passte das Tempo heute?", "q0_style": "smileys",
        "q1_type": "single_choice", "q1_question": "Hat die Übung geholfen?", "q1_options": "ja\nteils\nnein",
        "q2_type": "multiple_choice", "q2_question": "Was war unklar?", "q2_options": "Begriffe\nBeispiele",
        "q3_type": "text_input", "q3_question": "Ein Wort zur Vorlesung"})
    assert "/presenter/" in r.headers["Location"] and "item=" in r.headers["Location"]
    with app.app_context():
        added = [i for b in db.session.get(Survey, sid).blocks for i in b.items]
        assert [(i.type, i.text("question")) for i in added] == [
            ("rating", "Wie passte das Tempo heute?"), ("single_choice", "Hat die Übung geholfen?"),
            ("text_input", "Ein Wort zur Vorlesung")]
        assert added[0].config["style"] == "smileys" and added[0].config["points"] == 5
        choice = added[1]
        assert [choice.texts["de-du"]["options"][o["id"]] for o in choice.config["options"]] == ["ja", "teils", "nein"]
        assert len({i.code for i in added}) == 3
    assert client.post(f"/surveys/{sid}/editor/undo").status_code in (302, 303)      # one step back
    with app.app_context():
        assert not [i for b in db.session.get(Survey, sid).blocks for i in b.items]


def test_nothing_ticked_or_no_description(app, client, monkeypatch):
    monkeypatch.setattr(ai, "generate_text", lambda *a, **k: "kein JSON")
    client.post("/presenter/new", data={"title": "Leer"})
    with app.app_context():
        code = db.session.scalar(db.select(Survey).filter_by(title="Leer")).code
    assert "Bitte beschreiben" in client.post(f"/presenter/{code}/ai", data={"description": " "}, follow_redirects=True).data.decode()
    assert "nicht lesbar" in client.post(f"/presenter/{code}/ai", data={"description": "Workshop"}).data.decode()
    page = client.post(f"/presenter/{code}/ai", data={"action": "apply", "q0_type": "text_input", "q0_question": "Wort?"}).data.decode()
    assert "nichts übernommen" in page and "Wort?" in page


def test_the_wizard_starts_with_the_assistant(app, client, monkeypatch):
    monkeypatch.setattr(ai, "generate_text", lambda *a, **k: json.dumps(ANSWER))
    client.post("/presenter/new", data={"title": "Assistiert", "language": "de-du"})
    with app.app_context():
        code = db.session.scalar(db.select(Survey).filter_by(title="Assistiert")).code
    html = client.get(f"/presenter/{code}/setup/question").data.decode()
    assert "Folien vorschlagen" in html and "Ich möchte meine Folien lieber selbst festlegen." in html
    assert html.index("Folien vorschlagen") < html.index("lieber selbst festlegen")      # own questions: a way out below
    html = client.post(f"/presenter/{code}/setup/question", data={"wizard": "ai", "description": "Seminar"}).data.decode()
    assert "Wie passte das Tempo?" in html and "Ausgewählte übernehmen und weiter" in html
    r = client.post(f"/presenter/{code}/setup/question", data={
        "wizard": "ai_apply", "take": ["0", "3"], "q0_type": "rating", "q0_question": "Wie passte das Tempo?", "q0_style": "stars",
        "q3_type": "text_input", "q3_question": "Ein Wort zur Vorlesung"})
    assert r.headers["Location"].endswith(f"/presenter/{code}/setup/done")
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(code=code))
        assert [i.text("question") for b in survey.blocks for i in b.items] == ["Wie passte das Tempo?", "Ein Wort zur Vorlesung"]
        assert survey.setup_step == "fertig"


def test_the_questions_there_go_along(app, client, monkeypatch):
    seen = {}

    def fake(instructions, prompt, purpose="text", item_type=None):
        seen["prompt"] = prompt
        return json.dumps(ANSWER)

    monkeypatch.setattr(ai, "generate_text", fake)
    client.post("/presenter/new", data={"title": "Mit Fragen", "language": "de"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(title="Mit Fragen"))
        code, block = survey.code, survey.blocks[0].id
    client.post(f"/surveys/{code}/editor/blocks/{block}/items", data={"type": "rating"})
    with app.app_context():
        item = db.session.scalar(db.select(Survey).filter_by(code=code)).blocks[0].items[0]
        item.texts = {"de": {**item.texts.get("de", {}), "question": "Wie war das Tempo?"}}
        db.session.commit()
    client.post(f"/presenter/{code}/ai", data={"description": "Vorlesung"})
    assert "already has" in seen["prompt"] and "- Wie war das Tempo? (Bewertungsskala)" in seen["prompt"]

