"""AI assistant of an interview guide (app/interview_ai.py): SPSS steps (Helfferich), completing a Leitfrage, checking the guide,
Kurzfragebogen and Postskript, category definitions. The text model is replaced by fixed answers."""
import json
import re

from app import ai
from app.extensions import db
from app.models import Item, Survey
from tests.interview.test_interview import block_of, blocks, first, make


def fake_ai(monkeypatch, answers):
    """The model answers by purpose; every call is kept (instructions, prompt, purpose)."""
    calls = []

    def fake(instructions, prompt, purpose="text", item_type=None):
        calls.append({"instructions": instructions, "prompt": prompt, "purpose": purpose})
        return "Antwort: " + json.dumps(answers[purpose])

    monkeypatch.setattr(ai, "generate_text", fake)
    return calls


def data_of(page):
    raw = re.search(r'name="data" value="([^"]*)"', page).group(1)
    return raw.replace("&#34;", '"').replace("&quot;", '"').replace("&amp;", "&")


def test_develop_along_spss(app, client, monkeypatch):
    code, sid = make(app, client, template="leer")
    calls = fake_ai(monkeypatch, {
        "interview_collect": {"questions": [{"question": "Wie sieht ein typischer Tag aus?", "why": "Alltag fehlt"}]},
        "interview_check": {"checks": [{"n": 1, "issues": ["closed"], "suggestion": "Wie erleben Sie die Pflege?", "why": "ja/nein"},
                                       {"n": 2, "issues": [], "suggestion": "", "why": ""}, {"n": 9, "issues": ["closed"]}]},
        "interview_sort": {"blocks": [{"title": "Belastung", "questions": [1]}, {"title": "Alltag", "questions": [2, 2]}]},
        "interview_subsume": {"blocks": [{"n": 1, "prompt": "Erzählen Sie, wie Sie die Pflege erleben.", "aspects": ["Zeit", "Geld"],
                                          "keep": ["Und dann?"], "probe": ["Wer hilft?"], "minutes": 15}]},
    })
    assert "Sammeln" in client.get(f"/guide/{code}/ai/develop").data.decode()
    page = client.post(f"/guide/{code}/ai/develop", data={"action": "collect", "own": "Ist die Pflege belastend?", "data": "{}"}).data.decode()
    assert "Wie sieht ein typischer Tag aus?" in page and "Alltag fehlt" in page
    assert "Ist die Pflege belastend?" in calls[0]["prompt"] and "Wie erleben sie die Pflege?" in calls[0]["prompt"]   # research question
    assert "formal: Sie" in calls[0]["instructions"] or "Sie" in calls[0]["instructions"]
    page = client.post(f"/guide/{code}/ai/develop", data={"action": "to_check", "own": "Ist die Pflege belastend?",
                                                                 "keep_ai": ["1"], "data": data_of(page)}).data.decode()
    assert "geschlossen" in page and 'value="Wie erleben Sie die Pflege?"' in page
    page = client.post(f"/guide/{code}/ai/develop", data={"action": "to_sort", "data": data_of(page),
                                                                 "c0": "suggest", "s0": "Wie erleben Sie die Pflege?", "c1": "keep"}).data.decode()
    assert 'value="Belastung"' in page and 'value="Alltag"' in page
    page = client.post(f"/guide/{code}/ai/develop", data={"action": "to_subsume", "data": data_of(page),
                                                                 "title": ["Belastung", "Alltag"], "g0": "0", "g1": "1"}).data.decode()
    assert "Erzählen Sie, wie Sie die Pflege erleben." in page and "Wie sieht ein typischer Tag aus?" in page   # 2nd block without the AI's answer
    r = client.post(f"/guide/{code}/ai/develop", data={
        "action": "apply", "take": ["0", "1"],
        "b0_title": "Belastung", "b0_prompt": "Erzählen Sie, wie Sie die Pflege erleben.", "b0_aspects": "Zeit\nGeld", "b0_keep": "Und dann?",
        "b0_probe": "Wer hilft?", "b0_minutes": "15", "b1_title": "Alltag", "b1_prompt": "Wie sieht ein typischer Tag aus?", "b1_minutes": "10"})
    assert r.status_code == 302 and "/guide/" in r.headers["Location"]
    rows = blocks(app, sid)
    assert [t for p, t, _ in rows if p == "guide"] == ["Themenblock 1", "Belastung", "Alltag"]
    with app.app_context():
        survey = db.session.get(Survey, sid)
        new = next(b for b in survey.blocks if b.title() == "Belastung").items[0]
        assert new.kind.rows(new, "aspects")[1][1] == "Geld" and new.config["minutes"] == 15
    client.post(f"/surveys/{sid}/editor/undo")                                         # one step: both blocks go
    assert [t for p, t, _ in blocks(app, sid) if p == "guide"] == ["Themenblock 1"]


def test_skipping_every_step_needs_no_ai(app, client, monkeypatch):
    code, sid = make(app, client, template="leer")
    calls = fake_ai(monkeypatch, {})
    page = client.post(f"/guide/{code}/ai/develop", data={"action": "to_check_skip", "own": "Frage eins\nFrage zwei", "data": "{}"}).data.decode()
    page = client.post(f"/guide/{code}/ai/develop", data={"action": "to_sort_skip", "data": data_of(page)}).data.decode()
    page = client.post(f"/guide/{code}/ai/develop", data={"action": "to_subsume_skip", "data": data_of(page),
                                                                 "title": ["Themenblock 1"], "g0": "0", "g1": "0"}).data.decode()
    assert "Frage eins" in page and "Frage zwei" in page and calls == []


def test_complete_a_leitfrage(app, client, monkeypatch):
    code, sid = make(app, client)
    q = first(app, sid)
    fake_ai(monkeypatch, {"interview_extend": {"aspects": [{"text": "Anlass", "why": "schon da"}, {"text": "Familie", "why": "fehlt"}],
                                              "keep": [], "probe": [{"text": "Wer war beteiligt?", "why": ""}]}})
    assert "Leitfrage mit KI ergänzen" in client.get(f"/surveys/{sid}/editor/items/{q}?embed=1").data.decode()
    page = client.post(f"/guide/{code}/ai/guide-question/{q}?embed=1").data.decode()
    assert "Familie" in page and 'value="Anlass"' not in page and "site-header" not in page   # what it has is not suggested again
    r = client.post(f"/guide/{code}/ai/guide-question/{q}?embed=1", data={"action": "apply", "take_aspects": ["0"], "aspects0": "Familie",
                                                                         "take_probe": ["0"], "probe0": "Wer war beteiligt?"})
    assert "embed=1" in r.headers["Location"]
    with app.app_context():
        item = db.session.get(Item, q)
        assert [t for _, t in item.kind.rows(item, "aspects")][-1] == "Familie"
        assert [t for _, t in item.kind.rows(item, "probe")][-1] == "Wer war beteiligt?"


def test_review_the_guide(app, client, monkeypatch):
    code, sid = make(app, client)
    q = first(app, sid)
    with app.app_context():
        item = db.session.get(Item, q)
        code_q, rid = item.code, item.config["aspects"][0]
    calls = fake_ai(monkeypatch, {"interview_review": {"remarks": ["Der Einstieg ist gut."], "parts": [
        {"item": code_q, "id": "question", "suggestion": "Erzählen Sie doch einmal, wie alles anfing.", "why": "offener"},
        {"item": code_q, "id": f"aspects:{rid}", "suggestion": "Anlass der Pflege", "why": ""},
        {"item": "GIBTSNICHT", "id": "question", "suggestion": "x"}, {"item": code_q, "id": "aspects:zzz", "suggestion": "x"}]}})
    assert "Leitfaden mit KI prüfen" in client.get(f"/guide/{code}").data.decode()
    page = client.post(f"/guide/{code}/ai/check").data.decode()
    assert f"[{code_q}] question:" in calls[0]["prompt"] and "Der Einstieg ist gut." in page and "Anlass der Pflege" in page
    assert page.count('name="take"') == 2                                               # unknown parts are dropped
    client.post(f"/guide/{code}/ai/check", data={"action": "apply", "take": ["0"], "p0_item": q, "p0_id": "question",
                                                      "p0_text": "Erzählen Sie doch einmal, wie alles anfing.",
                                                      "p1_item": q, "p1_id": f"aspects:{rid}", "p1_text": "Anlass der Pflege"})
    with app.app_context():
        item = db.session.get(Item, q)
        assert item.text("question") == "Erzählen Sie doch einmal, wie alles anfing." and item.kind.rows(item, "aspects")[0][1] == "Anlass"


def test_suggest_kurzfragebogen(app, client, monkeypatch):
    code, sid = make(app, client)
    short = block_of(app, sid, "short")
    fake_ai(monkeypatch, {"interview_short": {"questions": [
        {"type": "single_choice", "question": "Wie lange pflegen Sie schon?", "options": ["unter 1 Jahr", "1 bis 5 Jahre", "länger"]},
        {"type": "rating", "question": "Wie belastet fühlen Sie sich?", "left": "gar nicht", "right": "sehr"},
        {"type": "single_choice", "question": "zu wenig Optionen", "options": ["a"]}, {"type": "likert", "question": "nicht erlaubt"}]}})
    page = client.post(f"/guide/{code}/ai/part/short").data.decode()
    assert "Wie lange pflegen Sie schon?" in page and "zu wenig Optionen" not in page and "nicht erlaubt" not in page
    client.post(f"/guide/{code}/ai/part/short", data={"action": "apply", "take": ["0", "1"],
        "q0_type": "single_choice", "q0_question": "Wie lange pflegen Sie schon?", "q0_options": "unter 1 Jahr\n1 bis 5 Jahre\nlänger",
        "q1_type": "rating", "q1_question": "Wie belastet fühlen Sie sich?", "q1_left": "gar nicht", "q1_right": "sehr"})
    with app.app_context():
        items = [i for i in next(b for b in db.session.get(Survey, sid).blocks if b.id == short).items]
        assert items[-2].text("question") == "Wie lange pflegen Sie schon?" and len(items[-2].config["options"]) == 3
        assert items[-1].type == "rating" and items[-1].text("right") == "sehr"
    assert client.post(f"/guide/{code}/ai/part/sonst").status_code == 404


def test_category_definitions_go_into_the_qdc(app, client, monkeypatch):
    code, sid = make(app, client)
    q = first(app, sid)
    with app.app_context():
        item = db.session.get(Item, q)
        code_q, rid = item.code, item.config["aspects"][0]
    fake_ai(monkeypatch, {"interview_categories": {"categories": [
        {"item": code_q, "definition": "Wie die Pflege begann.", "delimit": "Nicht: heutiger Alltag.", "aspects": {rid: "Der Auslöser.", "zzz": "x"}}]}})
    page = client.post(f"/guide/{code}/ai/categories").data.decode()
    assert "Wie die Pflege begann." in page and "Der Auslöser." in page
    client.post(f"/guide/{code}/ai/categories", data={"action": "apply", "take": ["0"], "c0_item": q,
                                                         "c0_definition": "Wie die Pflege begann.", "c0_delimit": "Nicht: heutiger Alltag.",
                                                         f"c0_a_{rid}": "Der Auslöser."})
    qdc = client.get(f"/guide/{code}/export/category-system.qdc").data.decode()
    assert "Definition: Wie die Pflege begann." in qdc and "Abgrenzung: Nicht: heutiger Alltag." in qdc and "Der Auslöser." in qdc


def test_ai_failure_and_roles(app, client, monkeypatch):
    code, sid = make(app, client)

    def broken(*a, **k):
        raise ai.AIError("Der KI-Dienst ist gerade nicht erreichbar.")

    monkeypatch.setattr(ai, "generate_text", broken)
    page = client.post(f"/guide/{code}/ai/check", follow_redirects=True).data.decode()
    assert "nicht erreichbar" in page
    from app.models import SurveyMember

    with app.app_context():
        db.session.get(Survey, sid).members[0].role = "viewer"
        db.session.commit()
    assert client.post(f"/guide/{code}/ai/check").status_code in (302, 403, 404)
    assert "Leitfaden mit KI prüfen" not in client.get(f"/guide/{code}").data.decode()
