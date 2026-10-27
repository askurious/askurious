"""AI assistant of a diary survey (app/diary_ai.py, Ralf 2026-10-27): D1 design, D2 moment questions, D3 check, each
with a fake text model; nothing changes until the ticked suggestions are taken over (one step of the history); only
design data goes to the model."""
import json

from app import ai, diary
from app.extensions import db
from app.models import Survey
from tests.conftest import experiments_on

DESIGN = {"scheme": "signal", "per_day": 5, "days": 10, "window": ["09:00", "21:00"], "why": "Stimmung schwankt"}
QUESTIONS = {"questions": [
    {"construct": "", "type": "rating", "question": "Wie gestresst fühlen Sie sich gerade?", "why": "Zustand"},
    {"construct": "", "type": "single_choice", "question": "Wo sind Sie gerade?", "options": ["zu Hause", "Uni", "unterwegs"], "why": "Kontext"},
    {"construct": "", "type": "single_choice", "question": "Nur eine", "options": ["x"]},  # too few options
    {"construct": "erfunden", "type": "", "question": ""},  # no such construct
    {"construct": "", "type": "slider", "question": "Kein Typ des Assistenten"},
]}
NOTES = {"notes": [{"topic": "burden", "text": "Fünf Abfragen über zehn Tage sind viel; drei reichen."},
                   {"topic": "nonsense", "text": "Abschlussbefragung: nach der Belastung fragen."}]}


def _new(app, client):
    experiments_on(client, "diary")
    client.post("/diary/new", data={"title": "Stress im Studium", "language": "de", "description": "Wie erleben Studierende Stress?",
                                    "demographics": "1", "exit": "1"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Stress im Studium"))
        s.research_question = "Wann fühlen sich Studierende gestresst?"
        db.session.commit()
        return s.id


def test_design_questions_and_check(app, client, monkeypatch):
    sid = _new(app, client)
    seen = []

    def fake(instructions, prompt, purpose="text", item_type=None):
        seen.append({"instructions": instructions, "prompt": prompt, "purpose": purpose})
        return json.dumps({"diary_design": DESIGN, "diary_questions": QUESTIONS, "diary_check": NOTES}[purpose])

    monkeypatch.setattr(ai, "generate_text", fake)
    url = f"/diary/{sid}/ai"
    with app.app_context():
        code = db.session.get(Survey, sid).code
    url = f"/diary/{code}/ai"
    assert "Mit KI planen" in client.get(f"/diary/{code}/schedule").data.decode()
    page = client.get(url).data.decode()
    assert "Design vorschlagen" in page and "Bolger" in page
    # D1: a suggestion, nothing changed yet
    page = client.post(url, data={"step": "design", "action": "design"}).data.decode()
    assert 'value="signal" selected' in page and "Stimmung schwankt" in page
    assert "Wann fühlen sich Studierende gestresst?" in seen[0]["prompt"] and "Moment" in seen[0]["prompt"]
    assert "Bolger & Laurenceau" in seen[0]["instructions"]
    with app.app_context():
        assert diary.modules(db.session.get(Survey, sid))[0]["trigger"]["t"] == "fixed"
    r = client.post(url, data={"step": "design", "action": "apply_design", "scheme": "signal", "per_day": "4", "days": "10",
                               "start": "08:00", "end": "20:00", "times": ""})
    assert r.headers["Location"].endswith("step=fragen")
    with app.app_context():
        s = db.session.get(Survey, sid)
        trigger = diary.modules(s)[0]["trigger"]
        assert (trigger["t"], trigger["per_day"], trigger["windows"]) == ("random", 4, [["08:00", "20:00"]])
        assert s.sampling["period_days"] == 10
    # D2: only the questions that fit; the ticked drafts go into the short survey, marked as AI drafts
    page = client.post(url, data={"step": "fragen", "action": "questions"}).data.decode()
    assert "Wie gestresst" in page and "Wo sind Sie gerade?" in page and "Nur eine" not in page and "Kein Typ" not in page
    assert seen[-1]["purpose"] == "diary_questions" and "Toolbox constructs" in seen[-1]["prompt"]
    r = client.post(url, data={"step": "fragen", "action": "apply_questions", "take": ["0", "1"],
                               "q0_type": "rating", "q0_question": "Wie gestresst fühlen Sie sich gerade?",
                               "q1_type": "single_choice", "q1_question": "Wo sind Sie gerade?",
                               "q1_options": "zu Hause\nUni\nunterwegs"})
    assert r.headers["Location"].endswith("step=pruefen")
    with app.app_context():
        s = db.session.get(Survey, sid)
        block = next(b for b in s.blocks if (b.settings or {}).get("part") == "m1")
        added = [i for i in block.items if (i.config or {}).get("ai")]
        assert [i.type for i in added] == ["rating", "single_choice"]
        assert added[1].texts["de"]["options"] and added[0].code.startswith("KI")
    # D3: counted burden on the page, the AI's notes with a known topic
    page = client.post(url, data={"step": "pruefen", "action": "check"}).data.decode()
    assert "Fünf Abfragen" in page and "Belastung:" in page and "Sonstiges:" in page
    assert "40 insgesamt" in page  # 4 a day for 10 days, counted by Askurious
    # nothing about participants or answers goes along
    assert all("resp_id" not in s["prompt"] and "@" not in s["prompt"] for s in seen)


def test_no_assistant_without_ai_or_for_other_kinds(app, client, survey, monkeypatch):
    sid = _new(app, client)
    with app.app_context():
        code = db.session.get(Survey, sid).code
    from app.models import User

    with app.app_context():  # the switch "Generative KI nutzen" on "KI-Nutzung" (E4)
        for u in db.session.scalars(db.select(User)):
            u.ai_enabled = False
        db.session.commit()
    assert client.get(f"/diary/{code}/ai").status_code == 404
    assert "Mit KI planen" not in client.get(f"/diary/{code}/schedule").data.decode()
    assert client.get("/diary/1/ai").status_code == 404  # a questionnaire has no diary assistant


def test_wizard_overview_of_a_diary_offers_the_assistant(app, client):
    sid = _new(app, client)
    with app.app_context():
        code = db.session.get(Survey, sid).code
    done = client.get(f"/surveys/{code}/setup/done").data.decode()
    assert "Mit dem KI-Assistenten Design und Fragen planen" in done
    r = client.post(f"/surveys/{code}/setup/done", data={"wizard": "ai"})
    assert r.headers["Location"].endswith(f"/diary/{code}/ai")
