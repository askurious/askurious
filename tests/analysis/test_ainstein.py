"""AInstein (app/ainstein.py): from name and idea through the canvas and the conversation to hypotheses, constructs
and a survey. The text model is replaced by fixed answers."""
import json

from app import ai
from app.extensions import db
from app.models import ResearchProject, Survey
from tests.conftest import experiments_on, may_sign_in

FETCH = {"X-Requested-With": "fetch"}


def fake_ai(monkeypatch, answers):
    """The model answers by purpose (a list: one answer per call); every call is kept."""
    calls = []

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None):
        calls.append({"instructions": instructions, "prompt": prompt, "purpose": purpose, "schema": schema})
        answer = answers[purpose]
        if isinstance(answer, list):
            answer = answer.pop(0)
        return "Antwort: " + json.dumps(answer)

    monkeypatch.setattr(ai, "generate_text", fake)
    return calls


def switch_on(client):
    experiments_on(client, "ainstein")


def project(app, pid=1):
    with app.app_context():
        p = db.session.get(ResearchProject, pid)
        return {"name": p.name, "canvas": p.canvas, "chat": p.chat, "plan": p.plan, "survey_id": p.survey_id, "code": p.code}


def url(app, pid=1):
    return f"/ainstein/{project(app, pid)['code']}"


TURN = {"reply": "Willkommen! Sie fragen, ob Menschen **KI-Diagnosen** vertrauen.\n\n- Laien?\n- Fachleute?",
        "canvas": {"frage": "Wie sehr vertrauen Laien KI-Diagnosen?", "unbekannt": "x"}, "focus": "stichprobe"}


def test_hidden_until_switched_on(app, client):
    assert client.get("/ainstein/").status_code == 404
    assert "ainstein-btn" not in client.get("/").get_data(as_text=True)
    switch_on(client)
    page = client.get("/").get_data(as_text=True)
    assert "ainstein-btn" in page and page.index("ainstein-btn") < page.index('class="nav-areas"')  # left of the areas
    assert "Mit AInstein beginnen" in client.get("/ainstein/").get_data(as_text=True)


def test_from_idea_through_conversation_to_canvas(app, client, monkeypatch):
    switch_on(client)
    calls = fake_ai(monkeypatch, {"ainstein_chat": [TURN, {"reply": "Gut. <b>Und</b> wie groß?", "canvas": {
        "stichprobe": "Laien, Panelstichprobe"}, "focus": "stichprobe"}]})
    assert client.post("/ainstein/new", data={"idea": "  "}).status_code == 400            # the idea is needed
    r = client.post("/ainstein/new", data={"name": "Vertrauen in KI", "idea": "Vertrauen Menschen KI-Diagnosen? (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    assert r.status_code == 302 and r.headers["Location"].endswith(url(app))
    assert "Vertrauen in KI" in calls[0]["prompt"] and "KI-Diagnosen?" in calls[0]["prompt"]
    assert "start of the conversation" in calls[0]["prompt"] and calls[0]["purpose"] == "ainstein_chat"
    assert "- Interpersonales Vertrauen (KUSIV3)" in calls[0]["instructions"]           # the toolbox, to recommend from
    p = project(app)
    # AInstein writes into the canvas at once (Philipp, 2026-10-05: no input boxes, refine through the conversation)
    assert p["chat"][0]["role"] == "ai" and p["canvas"]["frage"]["text"].startswith("Wie sehr")
    assert "unbekannt" not in p["canvas"]
    page = client.get(url(app)).get_data(as_text=True)
    assert "<strong>KI-Diagnosen</strong>" in page and "<li>Laien?</li>" in page
    assert 'data-box-text>Wie sehr vertrauen Laien KI-Diagnosen?</div>' in page and "<textarea name=\"text\"" not in page
    assert 'aria-current="step"><span class="ainstein-num">1</span> Gespräch und Canvas' in page

    body = client.post(url(app) + "/chat", data={"message": "Laien, über ein Panel."}, headers=FETCH).get_json()
    assert "&lt;b&gt;Und&lt;/b&gt;" in body["reply"]                                    # the model's HTML is escaped
    assert body["updates"] == {"stichprobe": "Laien, Panelstichprobe"} and body["focus"] == "stichprobe"
    assert "Researcher: Laien, über ein Panel." in calls[1]["prompt"]
    assert project(app)["canvas"]["stichprobe"]["text"] == "Laien, Panelstichprobe"


def test_ai_failure_takes_the_message_back(app, client, monkeypatch):
    switch_on(client)
    fake_ai(monkeypatch, {"ainstein_chat": [TURN]})
    client.post("/ainstein/new", data={"name": "P", "idea": "Idee (eine erste grobe Idee mit genug Wörtern für AInstein)"})

    def broken(*a, **k):
        raise ai.AIError("Der KI-Dienst ist gerade nicht erreichbar.")

    monkeypatch.setattr(ai, "generate_text", broken)
    r = client.post(url(app) + "/chat", data={"message": "Hallo"}, headers=FETCH)
    assert r.status_code == 502 and "nicht erreichbar" in r.get_json()["error"]
    # the page puts the text back into the field to send again: stored once only (code review 2026-10-07, G7)
    assert [m["text"] for m in project(app)["chat"] if m["role"] == "user"] == []
    assert r.get_json()["messages"] == len(project(app)["chat"])


def test_plan_and_survey(app, client, monkeypatch):
    switch_on(client)
    calls = fake_ai(monkeypatch, {
        "ainstein_chat": [TURN],
        "ainstein_plan": {"hypotheses": [{"text": "Je höher das Vertrauen in andere, desto höher das Vertrauen in KI-Diagnosen.",
                                          "kind": "correlation", "constructs": ["Interpersonales Vertrauen", "Vertrauen in KI-Diagnosen", "Gibt es nicht"]},
                                         {"text": "", "kind": "causal"}],
                          "constructs": [{"name": "Interpersonales Vertrauen", "role": "iv", "definition": "Vertrauen in andere",
                                          "measure": "kusiv3", "n": 3},
                                         {"name": "Vertrauen in KI-Diagnosen", "role": "dv", "definition": "Bereitschaft, sich zu verlassen",
                                          "measure": "erfunden", "n": 12},
                                         {"name": "Erklärung der KI", "role": "iv", "definition": "Manipulation", "measure": ""}]},
        "scale_items": {"constructs": [{"construct": 1, "items": [
            {"reverse": False, "text": "Ich verlasse mich auf KI-Diagnosen.", "facet": "Verlass"},
            {"reverse": True, "text": "KI-Diagnosen sind mir unheimlich.", "facet": "Unbehagen"}]}]},
    })
    client.post("/ainstein/new", data={"name": "Vertrauen in KI", "idea": "Vertrauen Menschen KI-Diagnosen? (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    r = client.post(url(app) + "/plan/derive")
    assert r.headers["Location"].endswith(url(app) + "/design#plan")                   # on to step 2
    assert "kusiv3:" in calls[-1]["instructions"]                                       # the toolbox is named
    plan = project(app)["plan"]
    assert [c["measure"] for c in plan["constructs"]] == ["kusiv3", "ai", ""]          # unknown keys become an AI draft
    assert plan["constructs"][1]["n"] == 10 and len(plan["hypotheses"]) == 1
    assert plan["hypotheses"][0]["constructs"] == ["Interpersonales Vertrauen", "Vertrauen in KI-Diagnosen"]
    page = client.get(url(app) + "/design").get_data(as_text=True)
    assert 'value="Interpersonales Vertrauen"' in page and "von AInstein ausgefüllt" in page and "data-auto-derive" not in page
    assert page.index('id="plan"') < page.index('id="konstrukte"')                 # constructs in a panel of their own
    first = client.get(url(app)).get_data(as_text=True)                                 # page 1: refill the canvas, on
    assert "Neu ableiten</button>" in first and "/canvas/reconcile" in first and "Weiter ›" in first

    r = client.post(url(app) + "/survey", data={"languages": "de+en"})
    assert r.status_code == 302 and "/editor" in r.headers["Location"]
    assert "Construct 1: Vertrauen in KI-Diagnosen" in calls[-1]["prompt"] and "Construct 2" not in calls[-1]["prompt"]
    p = project(app)
    with app.app_context():
        survey = db.session.get(Survey, p["survey_id"])
        assert survey.title == "Vertrauen in KI" and survey.languages == ["de", "en"] and survey.setup_step == "sprachen"
        assert survey.research_question.startswith("Wie sehr vertrauen Laien KI-Diagnosen?")
        assert "H1: Je höher das Vertrauen in andere" in survey.research_question
        assert survey.consent_item is not None
        titles = [b.title() for b in survey.blocks]
        assert "Demographische Angaben" in titles and titles[-2:] == ["Einflussgrößen", "Zielgrößen"]
        iv, dv = survey.blocks[-2].items, survey.blocks[-1].items
        assert len(iv) == 1 and iv[0].config["construct"]["key"] == "kusiv3"
        assert dv[0].type == "likert" and dv[0].config["ai"]["idea"].startswith("Vertrauen in KI-Diagnosen")
        assert dv[0].code == "VertInKiDiag" and len(dv[0].config["statements"]) == 2
        # in the wording of the conversation in every language, to be translated (the page says so)
        assert dv[0].texts["en"]["statements"][dv[0].config["statements"][1]["id"]] == "KI-Diagnosen sind mir unheimlich."


def test_saving_the_edited_plan(app, client, monkeypatch):
    switch_on(client)
    fake_ai(monkeypatch, {"ainstein_chat": [TURN]})
    client.post("/ainstein/new", data={"name": "P", "idea": "Idee (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    client.post(url(app) + "/plan", data={
        "h0_text": "H eins", "h0_kind": "difference", "h0_constructs": "A, B", "h1_text": "weg", "h1_delete": "1",
        "c0_name": "A", "c0_role": "dv", "c0_measure": "kusiv3", "c0_n": "4", "c1_name": "B", "c1_measure": "<script>",
        "c1_n": "99", "c2_name": ""})
    plan = project(app)["plan"]
    assert plan["hypotheses"] == [{"text": "H eins", "kind": "difference", "constructs": ["A", "B"]}]
    assert plan["constructs"][0] == {"name": "A", "role": "dv", "definition": "", "when": "after", "measure": "kusiv3",
                                     "spec": "", "n": 4, "facets": [], "exclude": "", "points": 5, "reverse": "none",
                                     "items": []}
    assert plan["constructs"][1]["measure"] == "ai" and plan["constructs"][1]["n"] == 10 and len(plan["constructs"]) == 2


def test_only_the_owner_sees_a_project(app, client, monkeypatch):
    switch_on(client)
    fake_ai(monkeypatch, {"ainstein_chat": [TURN]})
    client.post("/ainstein/new", data={"name": "Geheim", "idea": "Idee (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Other")
    other.post("/terms/", data={"accept": "1"})
    experiments_on(other, "ainstein", orcid="0000-0002-1825-0097")
    assert other.get(url(app)).status_code == 404
    assert other.post(url(app) + "/chat", data={"message": "x"}).status_code == 404
    assert "Geheim" not in other.get("/ainstein/").get_data(as_text=True)
    assert client.post(url(app) + "/delete").status_code == 302
    with app.app_context():
        assert db.session.get(ResearchProject, 1) is None


def test_the_model_sees_the_whole_conversation_and_its_earlier_proposals(app, client, monkeypatch):
    """The Borg scale agreed early on is still in view 80 messages later; a proposal not yet taken is shown to the model
    so the next one keeps it (Philipp, 2026-10-05: points of a box got lost, decided things were asked again)."""
    switch_on(client)
    calls = fake_ai(monkeypatch, {"ainstein_chat": [TURN] + [{"reply": "ok", "canvas": {}, "focus": ""}] * 3})
    client.post("/ainstein/new", data={"name": "Spiel", "idea": "Ein Bewegungsspiel für Ältere (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    with app.app_context():
        p = db.session.get(ResearchProject, 1)
        p.chat = p.chat + [{"role": "user", "text": "Die Borg-Skala CR10 ist super."}] + \
            [{"role": "user" if n % 2 else "ai", "text": f"Nachricht {n}"} for n in range(80)]
        db.session.commit()
    client.post(url(app) + "/chat", data={"message": "Weiter"}, headers=FETCH)
    mentor = next(c for c in reversed(calls) if "Never ask again about something" in c["instructions"])
    prompt = mentor["prompt"]
    assert "Borg-Skala CR10" in prompt and "Nachricht 0" in prompt and "Researcher: Weiter" in prompt
    assert "[frage] Forschungsfrage & Relevanz: Wie sehr vertrauen Laien" in prompt          # the canvas as it is


def test_the_canvas_is_brought_in_step_with_the_whole_conversation(app, client, monkeypatch):
    switch_on(client)
    calls = fake_ai(monkeypatch, {"ainstein_chat": [TURN, {"canvas": {
        "design": "Vorher-Nachher-Messung bei denselben Personen, ohne Kontrollgruppe",
        "konstrukte": "Anstrengung: Borg CR10 (0–10), vorher und nachher", "nicht": "x"}}]})
    client.post("/ainstein/new", data={"name": "Spiel", "idea": "Ein Bewegungsspiel für Ältere (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    client.post(url(app) + "/canvas/reconcile")
    assert "EVERY box" in calls[-1]["instructions"]
    canvas = project(app)["canvas"]
    assert canvas["design"]["text"].startswith("Vorher-Nachher")                     # written into the canvas
    assert "Borg CR10" in canvas["konstrukte"]["text"] and "nicht" not in canvas
    page = client.get(url(app)).get_data(as_text=True)                               # and lit up once
    assert 'class="ainstein-box is-filled is-updated" id="box-design"' in page
    assert 'class="ainstein-box is-filled" id="box-design"' in client.get(url(app)).get_data(as_text=True)


def test_pre_post_with_an_agreed_instrument_and_a_choice_experiment(app, client, monkeypatch):
    """Measured before and after an intervention (with "tb": the instruments of constructs.PERMISSION_REQUIRED stop
    the survey from being built, PR #1187): blocks Vorher, Intervention, Nachher, variables _pre and _post; the
    agreed instruments (Borg CR10, pain in five body parts) are built as agreed, not as invented scales; a choice
    experiment becomes its element; goal, design and sample go into the study plan."""
    switch_on(client)
    calls = fake_ai(monkeypatch, {
        "ainstein_chat": [TURN],
        "ainstein_plan": {
            "goal": "causal", "design": "panel", "sample": {"target": "30", "recruitment": "convenience", "note": "ab 60"},
            "intervention": "Spielen des Bewegungsspiels",
            "hypotheses": [{"text": "Die Anstrengung ist nach dem Spiel höher als davor.", "kind": "change",
                            "constructs": ["Anstrengung"]},
                           {"text": "Welche Spieleigenschaften werden bevorzugt?", "kind": "preference", "constructs": ["Präferenz"]}],
            "constructs": [
                {"name": "Anstrengung", "role": "dv", "when": "both", "measure": "agreed", "spec": "Borg CR10, 0 bis 10"},
                {"name": "Schmerz", "role": "dv", "when": "both", "measure": "agreed",
                 "spec": "Arme, Beine, Kopf, Hüfte, Bauch, je 1 bis 5"},
                {"name": "Technikbereitschaft", "role": "control", "when": "before", "measure": "tb"},
                {"name": "Präferenz", "role": "dv", "when": "after", "measure": "conjoint",
                 "spec": "Dauer: 10 Minuten; 20 Minuten; 30 Minuten\nMusik: mit Musik, ohne Musik\nkein Attribut"},
                {"name": "Ohne Absprache", "role": "dv", "when": "after", "measure": "agreed", "spec": ""},
                {"name": "Verbesserungen", "role": "other", "when": "after", "measure": "agreed", "spec": "zwei offene Fragen"}]},
        "scale_items": [
            {"constructs": [{"construct": 4, "items": [{"reverse": False, "text": "Das Spiel macht Spaß.", "facet": "Spaß"}]}]},
            {"items": [{"construct": 0, "type": "rating", "question": {"de": "Wie anstrengend war es?"}, "min": 0, "max": 10,
                        "left": {"de": "gar nicht"}, "right": {"de": "maximal"}},
                       {"construct": 1, "type": "likert", "question": {"de": "Wie stark sind Ihre Schmerzen?"}, "points": 5,
                        "labels": [{"de": "keine"}, {"de": ""}, {"de": ""}, {"de": ""}, {"de": "sehr stark"}],
                        "rows": [{"de": "Arme"}, {"de": "Beine"}, {"de": "Kopf"}, {"de": "Hüfte"}, {"de": "Bauch"}, {"de": "arme"}]},
                       {"construct": 5, "type": "open", "questions": [{"de": "Was sollte besser werden?"}, {"de": "Was hat gestört?"}]}]}]})
    client.post("/ainstein/new", data={"name": "Bewegungsspiel", "idea": "Ein Bewegungsspiel für Ältere (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    client.post(url(app) + "/plan/derive")
    plan = project(app)["plan"]
    assert plan["intervention"] == "Spielen des Bewegungsspiels" and plan["sample"]["target"] == 30
    assert 'not "panel"' in calls[-1]["instructions"]               # before and after in one session is no panel
    assert [c["measure"] for c in plan["constructs"]] == ["agreed", "agreed", "tb", "conjoint", "ai", "agreed"]  # agreed needs a spec
    assert [h["kind"] for h in plan["hypotheses"]] == ["change", "preference"]
    page = client.get(url(app) + "/design").get_data(as_text=True)
    assert "Borg CR10, 0 bis 10" in page and "vorher und nachher" in page and "Spielen des Bewegungsspiels" in page

    client.post(url(app) + "/survey", data={"languages": "de"})
    built = next(c for c in calls if "exactly as specified" in c["instructions"])
    assert "Borg CR10, 0 bis 10" in built["prompt"] and "Conversation so far" in built["prompt"]   # with the conversation
    choice = calls[-1]                                              # the choice experiment, given exactly
    assert "given exactly, keep as they are" in choice["prompt"] and "Dauer: 10 Minuten; 20 Minuten; 30 Minuten" in choice["prompt"]
    with app.app_context():
        survey = db.session.get(Survey, project(app)["survey_id"])
        titles = [b.title() for b in survey.blocks]
        assert titles[-3:] == ["Vorher", "Intervention", "Nachher"]
        before, middle, after = ({i.code: i for i in b.items} for b in survey.blocks[-3:])
        assert set(before) == {"Anst_pre", "Schm_pre", "TB"}
        assert set(after) == {"Anst_post", "Schm_post", "Praf", "OhneAbsp", "Verb", "Verb_2"}
        assert after["Verb_2"].type == "text_input" and after["Verb_2"].config["multiline"]
        assert after["Verb_2"].texts["de"]["question"] == "Was hat gestört?"
        assert before["Anst_pre"].type == "rating" and before["Anst_pre"].config["max"] == 10
        assert before["Anst_pre"].config["style"] == "numbers" and before["Anst_pre"].texts["de"]["right"] == "maximal"
        pain = after["Schm_post"]
        assert pain.type == "likert" and pain.config["points"] == 5 and pain.texts["de"]["labels"][0] == "keine"
        assert [pain.texts["de"]["statements"][st["id"]] for st in pain.config["statements"]] == ["Arme", "Beine", "Kopf", "Hüfte", "Bauch"]
        assert pain.config["ai"]["idea"].startswith("Wie vereinbart: Schmerz")                     # marked for review
        assert before["TB"].config["construct"]["key"] == "tb" and after["Praf"].type == "conjoint"
        cbc = after["Praf"]                                            # attributes and levels from the plan, in its wording
        assert [cbc.texts["de"]["attributes"][a["id"]] for a in cbc.config["attributes"]] == ["Dauer", "Musik"]
        assert [cbc.texts["de"]["levels"][l["id"]] for l in cbc.config["attributes"][0]["levels"]] == ["10 Minuten", "20 Minuten", "30 Minuten"]
        assert [cbc.texts["de"]["levels"][l["id"]] for l in cbc.config["attributes"][1]["levels"]] == ["mit Musik", "ohne Musik"]
        assert "Spielen des Bewegungsspiels" in middle["intervention"].texts["de"]["question"]
        sp = survey.study_plan
        assert sp["goal"] == "causal" and sp["design"] == "panel" and sp["sample"]["target"] == 30
        assert [h["kind"] for h in sp["hypotheses"]] == ["difference", "open"]
        assert (sp["hypotheses"][0]["iv"], sp["hypotheses"][0]["dv"]) == ("Anst_pre", "Anst_post")   # before explains after


def test_projects_are_addressed_by_a_random_code(app, client, monkeypatch):
    switch_on(client)
    fake_ai(monkeypatch, {"ainstein_chat": [TURN, TURN]})
    client.post("/ainstein/new", data={"name": "A", "idea": "Idee (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    client.post("/ainstein/new", data={"name": "B", "idea": "Idee (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    a, b = project(app, 1)["code"], project(app, 2)["code"]
    assert len(a) == 8 and int(a, 16) >= 0 and a != b
    assert client.get("/ainstein/1").status_code == 404 and client.get(f"/ainstein/{a}").status_code == 200


def test_own_scales_are_worded_by_facet_kept_and_completed(app, client, monkeypatch):
    """An own scale: facets, exclusion, points and reversal first (the reversal decided per scale, not a fixed rule),
    then items worded from the conversation; deleted items are replaced, the others kept and shown to the model;
    hints on the items; the survey uses exactly these items with the chosen number of points."""
    switch_on(client)
    calls = fake_ai(monkeypatch, {
        "ainstein_chat": [TURN],
        "ainstein_plan": {"constructs": [{"name": "Gefallen am Spiel", "role": "dv", "definition": "Wie gut das Spiel gefällt",
                                          "measure": "ai", "n": 3, "facets": ["Spielspaß", "Verständlichkeit", "x", "y", "z"],
                                          "exclude": "Benutzerfreundlichkeit", "points": 7, "reverse": "none"}]},
        "scale_items": [
            {"constructs": [{"construct": 0, "items": [
                {"text": "Das Spiel hat mir Spaß gemacht.", "facet": "Spielspaß", "reverse": False},
                {"text": "Ich habe nicht verstanden, was ich tun soll und wie.", "facet": "Verständlichkeit", "reverse": True},
                {"text": "Das Spiel hat mir Spaß gemacht.", "facet": "Spielspaß"},          # the same again: dropped
                {"text": "Die Aufgaben waren klar.", "facet": "Verständlichkeit"}]}]},
            {"constructs": [{"construct": 0, "items": [{"text": "Die Regeln waren leicht zu verstehen.", "facet": "Verständlichkeit"},
                                                       {"text": "eins zu viel", "facet": "x"}]}]}]})
    client.post("/ainstein/new", data={"name": "Spiel", "idea": "Ein Bewegungsspiel für Ältere (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    client.post(url(app) + "/plan/derive")
    c = project(app)["plan"]["constructs"][0]
    assert c["facets"] == ["Spielspaß", "Verständlichkeit", "x", "y"] and c["points"] == 7 and c["reverse"] == "none"
    assert "reversed items help against agreeing" in calls[0]["instructions"]               # the mentor asks about it
    client.post(url(app) + "/scales", data={"s0_points": "7", "s0_reverse": "none", "s0_n": "3",
                                            "s0_facets": "Spielspaß\nVerständlichkeit", "s0_exclude": "Benutzerfreundlichkeit",
                                            "draft": "0"})
    prompt = calls[-1]["prompt"]
    assert "facets: Spielspaß; Verständlichkeit" in prompt and "reversal: none" in prompt and "new items to write: 3" in prompt
    assert "Target group:" in prompt and "Conversation so far" in prompt                    # the conversation, too
    items = project(app)["plan"]["constructs"][0]["items"]
    assert [i["text"] for i in items] == ["Das Spiel hat mir Spaß gemacht.", "Ich habe nicht verstanden, was ich tun soll und wie.",
                                          "Die Aufgaben waren klar."]
    page = client.get(url(app) + "/design").get_data(as_text=True)
    assert "Verneinung" in page and "zwei Aussagen?" in page and "Fehlende Items entwerfen" in page
    # the second item is deleted, the first edited: one new item, the others kept and shown to the model
    client.post(url(app) + "/scales", data={"s0_points": "7", "s0_reverse": "none", "s0_n": "3", "s0_facets": "Spielspaß\nVerständlichkeit",
                                            "s0_0_text": "Das Spiel hat mir viel Spaß gemacht.", "s0_0_facet": "Spielspaß",
                                            "s0_1_text": "x", "s0_1_delete": "1", "s0_2_text": "Die Aufgaben waren klar.",
                                            "s0_2_facet": "Verständlichkeit", "draft": "0"})
    assert "new items to write: 1" in calls[-1]["prompt"] and "Das Spiel hat mir viel Spaß gemacht." in calls[-1]["prompt"]
    items = project(app)["plan"]["constructs"][0]["items"]
    assert [i["text"] for i in items] == ["Das Spiel hat mir viel Spaß gemacht.", "Die Aufgaben waren klar.",
                                          "Die Regeln waren leicht zu verstehen."]
    n_calls = len(calls)
    client.post(url(app) + "/survey", data={"languages": "de"})
    assert len(calls) == n_calls                                     # nothing left to word: no AI call
    with app.app_context():
        survey = db.session.get(Survey, project(app)["survey_id"])
        item = survey.blocks[-1].items[0]
        assert item.type == "likert" and item.config["points"] == 7 and len(item.texts["de"]["labels"]) == 7
        assert [item.texts["de"]["statements"][st["id"]] for st in item.config["statements"]][0] == "Das Spiel hat mir viel Spaß gemacht."


def test_the_question_of_a_reply_stands_apart():
    """What the answer settles, what follows, and the next question in paragraphs of their own (Philipp, 2026-10-05)."""
    from app.ainstein import question_apart, reply_html

    one = ("Damit ist die Zielgruppe festgelegt: ältere Menschen ab 60. Ohne Kontrollgruppe bleibt die Wirkung offen. "
           "Wie möchten Sie rekrutieren? Und wie viele Personen erreichen Sie?")
    assert question_apart(one) == ("Damit ist die Zielgruppe festgelegt: ältere Menschen ab 60. Ohne Kontrollgruppe bleibt "
                                   "die Wirkung offen.\n\nWie möchten Sie rekrutieren? Und wie viele Personen erreichen Sie?")
    assert str(reply_html(question_apart(one))).count("<p>") == 2
    already = "Festgelegt: 30 Personen.\n\nWie rekrutieren Sie?"
    assert question_apart(already) == already                               # paragraphs as the model wrote them
    assert question_apart("Welche Zielgruppe?") == "Welche Zielgruppe?"     # only a question: nothing to split
    assert question_apart("Gut so. Weiter.") == "Gut so. Weiter."           # no question at the end


def test_an_unreadable_answer_is_asked_once_more_and_explained_on_hover(app, client, monkeypatch):
    """A reply in paragraphs has line breaks inside the JSON text: read leniently. An answer that still cannot be read
    is asked for once more; if that fails too, the reason is on hover ("Details")."""
    switch_on(client)
    answers = iter(['{"reply": "Festgelegt.\n\nWie weiter?", "canvas": {}, "focus": ""}',
                    '{"reply": "abgebrochen', '{"reply": "wieder abgebrochen'])

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None):
        return next(answers)

    monkeypatch.setattr(ai, "generate_text", fake)
    client.post("/ainstein/new", data={"name": "P", "idea": "Idee (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    assert project(app)["chat"][-1]["text"] == "Festgelegt.\n\nWie weiter?"          # line breaks inside the JSON text
    body = client.post(url(app) + "/chat", data={"message": "Hallo"}, headers=FETCH).get_json()
    assert body["error"].startswith("Die KI-Antwort war nicht lesbar") and "kein JSON-Objekt" in body["detail"]
    assert "Anfang: {" in body["detail"]


def test_a_choice_experiment_written_as_prose_is_converted(app, client, monkeypatch):
    """A conjoint plan as prose (as in Philipp's first CBC run, 2026-10-05: attributes and levels worked out, but an
    empty element in the survey): the AI turns it into attributes and levels, which the element then has; MaxDiff
    statements one per line are taken as they are."""
    switch_on(client)
    calls = fake_ai(monkeypatch, {
        "ainstein_chat": [TURN],
        "ainstein_plan": [{"constructs": [
            {"name": "Präferenz", "role": "dv", "measure": "conjoint",
             "spec": "Attribute sind der Preis (5, 10 oder 15 Euro) und die Dauer (kurz oder lang)."},
            {"name": "Wichtigkeit", "role": "dv", "measure": "maxdiff", "spec": "Spaß\nGesundheit\nGeselligkeit\nPreis"}]},
            {"experiments": [
                {"construct": 0, "explain_intro": "Gleich sehen Sie mehrere Angebote. Wählen Sie spontan.", "scene": "a living room",
                 "attributes": [
                     {"name": "Preis", "description": "Was das Spiel kostet.", "visual": False, "levels": [
                         {"label": "5 €", "definition": "Fünf Euro im Monat.", "hint": ""},
                         {"label": "10 €", "definition": "Zehn Euro im Monat.", "hint": ""},
                         {"label": "15 €", "definition": "Fünfzehn Euro im Monat.", "hint": ""}]},
                     {"name": "Dauer", "description": "Wie lange eine Runde dauert.", "visual": True, "levels": [
                         {"label": "kurz", "definition": "Etwa zehn Minuten.", "hint": "an elderly woman playing briefly"},
                         {"label": "lang", "definition": "Etwa eine halbe Stunde.", "hint": "an elderly woman exercising for a long time"}]}]},
                {"construct": 1, "explain_intro": "Wählen Sie jeweils das Wichtigste und das Unwichtigste."}]}]})
    client.post("/ainstein/new", data={"name": "Spiel", "idea": "Idee (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    client.post(url(app) + "/plan/derive")
    client.post(url(app) + "/survey", data={"languages": "de"})
    assert "Experiment 0 (conjoint, Präferenz), described, take from it" in calls[-1]["prompt"]
    assert "Experiment 1 (maxdiff, Wichtigkeit), given exactly" in calls[-1]["prompt"]
    with app.app_context():
        items = {i.type: i for b in db.session.get(Survey, project(app)["survey_id"]).blocks for i in b.items}
        cbc, md = items["conjoint"], items["maxdiff"]
        assert [cbc.texts["de"]["attributes"][a["id"]] for a in cbc.config["attributes"]] == ["Preis", "Dauer"]
        price, duration = cbc.config["attributes"]
        assert cbc.texts["de"]["attr_desc"][price["id"]] == "Was das Spiel kostet." and duration["visual"] and not price["visual"]
        assert cbc.texts["de"]["level_defs"][duration["levels"][1]["id"]] == "Etwa eine halbe Stunde."
        assert duration["levels"][0]["hint"] == "an elderly woman playing briefly"            # for the image model only
        assert cbc.config["explain"] and cbc.texts["de"]["explain_intro"].startswith("Gleich sehen Sie")
        assert cbc.config["scene"] == "a living room"
        assert md.config["explain"] and md.texts["de"]["explain_intro"].startswith("Wählen Sie jeweils")
        assert [md.texts["de"]["statements"][st["id"]] for st in md.config["statements"]] == ["Spaß", "Gesundheit", "Geselligkeit", "Preis"]
        code, ids = db.session.get(Survey, project(app)["survey_id"]).code, (cbc.id, md.id)
    for item_id in ids:                                    # the elements open in the editor like any other
        page = client.get(f"/surveys/{code}/editor/items/{item_id}")
        assert page.status_code == 200 and "Preis" in page.get_data(as_text=True)


def test_every_25_messages_the_earlier_ones_become_notes(app, client, monkeypatch):
    """The conversation is condensed every 25 messages into notes for the model (Philipp, 2026-10-05: "kurz, prägnant,
    nur dem Modell helfen"); the newest 10 messages stay word for word."""
    from app import ainstein

    switch_on(client)
    calls = []

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None):
        calls.append({"instructions": instructions, "prompt": prompt})
        if instructions.startswith("You keep compact working notes"):
            return "zielgruppe: ab 60\nanstrengung: Borg CR10 vorher+nachher"
        return json.dumps({"reply": "ok", "canvas": {}, "focus": ""})

    monkeypatch.setattr(ai, "generate_text", fake)
    client.post("/ainstein/new", data={"name": "P", "idea": "Idee (eine erste grobe Idee mit genug Wörtern für AInstein)"})          # 1 message
    for n in range(17):                                                       # + 34: 35 messages
        client.post(url(app) + "/chat", data={"message": f"Antwort {n}"}, headers=FETCH)
    notes = [c for c in calls if c["instructions"].startswith("You keep compact working notes")]
    assert len(notes) == 1 and "Antwort 0" in notes[0]["prompt"] and "Antwort 16" not in notes[0]["prompt"]
    with app.app_context():
        p = db.session.get(ResearchProject, 1)
        assert p.memory["upto"] == 25 and "Borg CR10" in p.memory["text"]
    client.post(url(app) + "/chat", data={"message": "Und jetzt?"}, headers=FETCH)
    last = next(c for c in reversed(calls) if "Never ask again about something" in c["instructions"])["prompt"]
    assert "Notes on the first 25 messages" in last and "Borg CR10 vorher+nachher" in last
    assert "Antwort 0\n" not in last and "Researcher: Und jetzt?" in last              # the old ones only as notes


def test_sharing_reading_commenting(app, client, monkeypatch):
    """Shared to read and comment, a person sees conversation, canvas and plan but cannot talk or change anything;
    shared to work on it, they can; comments reach AInstein; only the owner shares and deletes."""
    switch_on(client)
    calls = fake_ai(monkeypatch, {"ainstein_chat": [TURN] + [{"reply": "ok", "canvas": {}, "focus": ""}] * 3})
    client.post("/ainstein/new", data={"name": "Dissertation", "idea": "Idee (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Betreuerin")
    other.post("/terms/", data={"accept": "1"})
    experiments_on(other, "ainstein", orcid="0000-0002-1825-0097")
    assert other.get(url(app)).status_code == 404
    may_sign_in(app, "0000-0002-1825-0097")  # sharing needs someone who can sign in (H4)
    client.post(url(app) + "/share", data={"orcid": "0000-0002-1825-0097", "name": "Betreuerin", "role": "view"})
    page = other.get(url(app)).get_data(as_text=True)
    assert "Dissertation" in page and "geteilt: lesen und kommentieren" in page and "data-send" not in page
    assert "Dissertation" in other.get("/ainstein/").get_data(as_text=True)
    assert other.post(url(app) + "/chat", data={"message": "x"}).status_code == 404       # reading only
    assert other.post(url(app) + "/canvas", data={"box": "frage", "text": "x"}).status_code == 404
    assert other.post(url(app) + "/share", data={"remove": "0"}).status_code == 404
    assert other.post(url(app) + "/delete").status_code == 404
    other.post(url(app) + "/comment", data={"text": "Bitte die Stichprobe begründen.", "box": "stichprobe"})
    client.post(url(app) + "/chat", data={"message": "Weiter"}, headers=FETCH)
    assert "Betreuerin on [stichprobe]: <comment>Bitte die Stichprobe begründen.</comment>" in calls[-1]["prompt"]
    client.post(url(app) + "/share", data={"orcid": "0000-0002-1825-0097", "name": "Betreuerin", "role": "edit"})
    assert len(project(app)["chat"]) == 3
    other.post(url(app) + "/chat", data={"message": "Von mir"}, headers=FETCH)            # works on it now
    assert project(app)["chat"][-2]["text"] == "Von mir"
    client.post(url(app) + "/share", data={"remove": "0"})
    assert other.get(url(app)).status_code == 404


def test_an_experiment_gets_its_conditions_and_the_study_plan_its_variables(app, client, monkeypatch):
    """A manipulation: one block per condition with a placeholder for the stimulus, a randomization so that each
    person gets one (balanced); the study plan names the randomization as what explains and the outcome as what is
    explained, with the analysis it suggests; the needed sample is shown and given to AInstein."""
    switch_on(client)
    calls = fake_ai(monkeypatch, {
        "ainstein_chat": [TURN, {"reply": "ok", "canvas": {}, "focus": ""}],
        "ainstein_plan": {"sample": {"target": 60},
                          "hypotheses": [{"text": "Eine Erklärung erhöht das Vertrauen.", "kind": "causal",
                                          "constructs": ["Erklärung", "Vertrauen"]},
                                         {"text": "Vertrauen hängt mit Technikbereitschaft zusammen.", "kind": "correlation",
                                          "constructs": ["Technikbereitschaft", "Vertrauen"]}],
                          "constructs": [{"name": "Erklärung", "role": "iv", "measure": "manipulation",
                                          "spec": "Ohne Erklärung: Diagnose allein\nKurze Erklärung: ein Satz\nAusführlich: ein Absatz"},
                                         {"name": "Vertrauen", "role": "dv", "measure": "ai", "n": 3},
                                         {"name": "Technikbereitschaft", "role": "control", "measure": "tb"}]},
        "scale_items": {"constructs": [{"construct": 1, "items": [{"text": "Ich vertraue der Diagnose."},
                                                                  {"text": "Die Diagnose überzeugt mich."},
                                                                  {"text": "Ich würde mich darauf verlassen."}]}]}})
    client.post("/ainstein/new", data={"name": "Erklärbare KI", "idea": "Idee (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    client.post(url(app) + "/plan/derive")
    page = client.get(url(app) + "/design").get_data(as_text=True)
    assert "etwa <strong>158</strong> Teilnehmende (3 Gruppen, f = 0,25)" in page and "geplant 60" in page
    client.post(url(app) + "/chat", data={"message": "Reicht die Stichprobe?"}, headers=FETCH)
    assert "Needed sample (approximation" in calls[-1]["prompt"] and "H1: 158" in calls[-1]["prompt"]
    client.post(url(app) + "/survey", data={"languages": "de"})
    with app.app_context():
        from app import flow

        survey = db.session.get(Survey, project(app)["survey_id"])
        rands = [n for n, _, _ in flow.walk(flow.tree(survey)) if n["t"] == "rand"]
        assert len(rands) == 1 and rands[0]["k"] == 1 and rands[0]["balanced"] and rands[0]["code"] == "Erkl"
        titles = {b.id: b.title() for b in survey.blocks}
        assert [titles[c["block"]] for c in rands[0]["children"]] == ["Ohne Erklärung", "Kurze Erklärung", "Ausführlich"]
        order = [b.title() for b in survey.blocks]
        assert order.index("Ausführlich") < order.index("Zielgrößen")       # the conditions before the outcome
        stimulus = next(b for b in survey.blocks if b.title() == "Kurze Erklärung").items[0]
        assert stimulus.type == "info" and "Kurze Erklärung" in stimulus.texts["de"]["question"]
        h1, h2 = survey.study_plan["hypotheses"]
        # three groups, a scale without a construct: ordinal, so Kruskal-Wallis (audit 2026-10-23, M2)
        assert (h1["iv"], h1["dv"], h1["analysis"]) == ("Erkl", "Vert", "kw")
        assert (h2["iv"], h2["dv"]) == ("TB", "Vert") and h2["analysis"] == "rho"  # Vert ordinal (M2)


def test_the_project_starts_from_the_idea_and_ainstein_names_it(app, client, monkeypatch):
    """No name to type: the first answer gives a concise working title (Philipp, 2026-10-05), changeable later."""
    switch_on(client)
    calls = fake_ai(monkeypatch, {"ainstein_chat": [{**TURN, "name": "„Vertrauen in KI-Diagnosen“"}]})
    page = client.get("/ainstein/").get_data(as_text=True)
    assert 'name="name"' not in page
    client.post("/ainstein/new", data={"idea": "Vertrauen Menschen Diagnosen einer KI? (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    assert project(app)["name"] == "Vertrauen in KI-Diagnosen"
    assert '"name" in the' in calls[0]["prompt"] and "no name yet" in calls[0]["prompt"]
    client.post(url(app) + "/canvas", data={"name": "Mein Titel"})
    assert project(app)["name"] == "Mein Titel"


def test_a_conjoint_split_into_one_construct_per_attribute_is_merged(app, client, monkeypatch):
    """The model made a Conjoint construct per attribute (Philipp, 2026-10-05: four Conjoints with one attribute each):
    they become one Conjoint with all attributes, hypotheses name it; visible attributes go into an AI image."""
    switch_on(client)
    calls = fake_ai(monkeypatch, {
        "ainstein_chat": [TURN],
        "ainstein_plan": [
            {"hypotheses": [{"text": "Welche Cobots werden bevorzugt?", "kind": "preference",
                             "constructs": ["Sicherheit", "Aussehen", "Größe"]}],
             "constructs": [{"name": "Sicherheit", "role": "iv", "measure": "conjoint", "spec": "Zaun; Sensoren; Kraftbegrenzung"},
                            {"name": "Aussehen", "role": "iv", "measure": "conjoint", "spec": "schlicht; freundlich; menschenähnlich"},
                            {"name": "Größe", "role": "iv", "measure": "conjoint", "spec": "kompakt; groß"}]},
            {"experiments": [{"construct": 0, "scene": "an assembly line with a worker and a cobot", "attributes": [
                {"name": "Aussehen", "visual": True, "levels": [{"label": "schlicht"}, {"label": "freundlich"}, {"label": "menschenähnlich"}]}]}]}]})
    client.post("/ainstein/new", data={"idea": "Cobots in der Montage (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    client.post(url(app) + "/plan/derive")
    assert "never one construct" in calls[-1]["instructions"]
    page = client.get(url(app) + "/design").get_data(as_text=True)
    assert 'value="Präferenzen (Conjoint)"' in page and 'value="Sicherheit"' not in page
    client.post(url(app) + "/survey", data={"languages": "de"})
    with app.app_context():
        survey = db.session.get(Survey, project(app)["survey_id"])
        cbcs = [i for b in survey.blocks for i in b.items if i.type == "conjoint"]
        assert len(cbcs) == 1
        names = [cbcs[0].texts["de"]["attributes"][a["id"]] for a in cbcs[0].config["attributes"]]
        assert names == ["Sicherheit", "Aussehen", "Größe"]
        assert cbcs[0].config["display"] == "both" and [a["visual"] for a in cbcs[0].config["attributes"]] == [False, True, False]
        assert cbcs[0].config["versions"] == 6        # blocking: several versions, not 8 x 2 = 16 profiles for everybody
    assert "already asks age and gender" in calls[0]["instructions"]                     # no need to plan them


def test_step_two_fills_itself_the_first_time_and_says_when_the_canvas_changed(app, client, monkeypatch):
    """No plan yet: step 2 asks AInstein as it opens (data-auto-derive, answered as JSON); later, when the canvas
    has changed since, it says so and offers to fill the page anew. "Befragung erstellen" is purple only when the
    AI is asked while creating the survey."""
    switch_on(client)
    fake_ai(monkeypatch, {"ainstein_chat": [TURN, {"reply": "ok", "canvas": {"design": "Online-Experiment"}, "focus": ""}],
                          "ainstein_plan": {"constructs": [{"name": "Kontrollüberzeugung", "role": "iv", "measure": "kut"}]}})
    client.post("/ainstein/new", data={"idea": "Technik im Alter (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    page = client.get(url(app) + "/design").get_data(as_text=True)
    assert "data-auto-derive=" in page and "AInstein füllt diese Seite aus" in page
    assert client.post(url(app) + "/plan/derive", headers=FETCH).get_json() == {"ok": True}
    page = client.get(url(app) + "/design").get_data(as_text=True)
    assert "data-auto-derive" not in page and "Der Canvas hat sich geändert" not in page
    assert 'class="button" ' in client.get(url(app) + "/completion").get_data(as_text=True)   # a toolbox scale: no AI
    client.post(url(app) + "/chat", data={"message": "Doch ein Experiment"}, headers=FETCH)  # the canvas changes
    assert "Der Canvas hat sich geändert" in client.get(url(app) + "/design").get_data(as_text=True)


def test_the_notes_are_sorted_by_the_boxes_of_the_canvas():
    from app import ainstein

    assert '"konstrukte" (every construct with its measure' in ainstein.MEMORY and '"verworfen"' in ainstein.MEMORY


def test_the_mentor_knows_the_designs_and_every_element_type_without_leaning_to_one(app, client, monkeypatch):
    """The design cards (app/ainstein_designs.py) and the element types from the registry are in the instructions;
    experimental types only when switched on; no praise that pushes toward one method (Philipp, 2026-10-06)."""
    from app import items

    switch_on(client)
    calls = fake_ai(monkeypatch, {"ainstein_chat": [TURN, TURN]})
    client.post("/ainstein/new", data={"idea": "Wie sehen Laien Pflegeroboter? (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    text = calls[-1]["instructions"]
    for design in ("Cross-sectional survey", "Public perception survey", "Framing / message experiment", "A/B test",
                   "Repeated measures", "Vignette study", "Choice experiment", "Longitudinal", "Real-time Delphi",
                   "Qualitative interview"):
        assert design in text
    assert "the method follows from the research question" in text and "strength of Askurious" not in text
    shown = [t for t in items.all_types() if not getattr(t, "experimental", None) and not getattr(t, "interview_only", False)]
    assert all(f"- {t.label}: " in text for t in shown)                          # every element type, German name only
    assert "(likert)" not in text                                                # no keys
    assert "- Leitfrage: " not in text                                          # interviews only
    assert "- Impliziter Assoziationstest (IAT): " in text and "- IAT-Ergebnis: " in text  # no longer experimental
    assert "- Sprachnachricht: " in text                                     # for everyone since 2026-10-16


def test_the_chat_asks_for_its_answer_in_a_fixed_shape(app, client, monkeypatch):
    """Structured Outputs: the chat sends a strict schema (every box, null where unchanged; focus from the boxes);
    null boxes change nothing (Philipp, 2026-10-06)."""
    from app.ainstein import BOXES, MENTOR_SCHEMA

    switch_on(client)
    turn = {"reply": "Gut.", "canvas": {k: None for k in BOXES} | {"frage": "Vertrauen Laien KI-Diagnosen?"},
            "focus": "stichprobe", "name": "Vertrauen in KI"}
    calls = fake_ai(monkeypatch, {"ainstein_chat": [turn]})
    client.post("/ainstein/new", data={"idea": "Vertrauen Menschen KI-Diagnosen? (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    schema = calls[-1]["schema"]
    assert schema is MENTOR_SCHEMA and schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])                  # strict: every property required
    assert schema["properties"]["canvas"]["required"] == BOXES
    p = project(app)
    assert p["name"] == "Vertrauen in KI" and p["canvas"]["frage"]["text"] == "Vertrauen Laien KI-Diagnosen?"
    assert all(p["canvas"][k]["text"] == "" for k in BOXES if k != "frage")      # null: unchanged


def test_the_conversation_counts_its_messages(app, client, monkeypatch):
    """A counter in the head of the chat and in the project list; the chat answer says the new number (Philipp,
    2026-10-06)."""
    switch_on(client)
    fake_ai(monkeypatch, {"ainstein_chat": [TURN, TURN]})
    client.post("/ainstein/new", data={"idea": "Vertrauen Menschen KI-Diagnosen? (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    assert "1 Nachricht<" in client.get(url(app)).get_data(as_text=True)          # AInstein's welcome
    r = client.post(url(app) + "/chat", data={"message": "Laien"}, headers={"X-Requested-With": "fetch"})
    assert r.get_json()["messages"] == 3
    assert "3 Nachrichten<" in client.get(url(app)).get_data(as_text=True)
    assert "· 3 Nachrichten" in client.get("/ainstein/").get_data(as_text=True)


def test_an_implicit_attitude_becomes_an_iat_with_the_agreed_words(app, client, monkeypatch):
    """"IAT" in the dropdown of the measures (for everyone since 2026-10-10); the plan asks for the four categories as
    lines; the survey gets an IAT with those concepts and words, marked as drafted (Philipp, 2026-10-06)."""
    switch_on(client)
    spec = ("A: Roboter: Cobot; Maschine; Greifarm; Automat\nB: Menschen: Kollege; Kollegin; Mensch; Monteur\n"
            "X: gut: sicher; hilfreich; angenehm; verlässlich\nY: schlecht: gefährlich; störend; kalt; bedrohlich")
    calls = fake_ai(monkeypatch, {"ainstein_chat": [TURN], "ainstein_plan": [
        {"constructs": [{"name": "Implizite Einstellung zu Cobots", "role": "dv", "measure": "iat", "spec": spec}]}] * 2})
    client.post("/ainstein/new", data={"idea": "Implizite Einstellung zu Cobots (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    assert "Impliziter Assoziationstest (IAT)</option>" in client.get(url(app) + "/design").get_data(as_text=True)
    client.post(url(app) + "/plan/derive")
    assert '5b. "iat"' in calls[-1]["instructions"]
    client.post(url(app) + "/survey", data={"languages": "de"})
    with app.app_context():
        iat = next(i for b in db.session.get(Survey, project(app)["survey_id"]).blocks for i in b.items if i.type == "iat")
        assert iat.texts["de"]["categories"] == {"A": "Roboter", "B": "Menschen", "X": "gut", "Y": "schlecht"}
        words = {s["cat"]: [] for s in iat.config["stimuli"]}
        for s in iat.config["stimuli"]:
            words[s["cat"]].append(iat.texts["de"]["stimuli"][s["id"]])
        assert words["A"] == ["Cobot", "Maschine", "Greifarm", "Automat"] and words["Y"][-1] == "bedrohlich"
        assert iat.kind.validate(iat) == [] and "AInstein" in iat.config["ai"]["idea"]


def test_ai_drafts_are_marked_after_their_type(app, client, monkeypatch):
    """The scale note ("nicht validiert", more items, reliability) only on a scale (ItemType.ai_scale); a Conjoint
    drafted by AInstein says that its texts are to be checked; a design "conjoint" from the model becomes "cross"."""
    switch_on(client)
    calls = fake_ai(monkeypatch, {"ainstein_chat": [TURN], "ainstein_plan": [
        {"design": "conjoint", "constructs": [
            {"name": "Präferenz", "role": "dv", "measure": "conjoint", "spec": "Preis: 5 €; 10 €\nDauer: kurz; lang"}]},
        {"experiments": [{"construct": 0, "explain_intro": "Wählen Sie.", "attributes": [
            {"name": "Preis", "description": "Kosten.", "visual": False, "levels": [
                {"label": "5 €", "definition": "Fünf.", "hint": ""}, {"label": "10 €", "definition": "Zehn.", "hint": ""}]},
            {"name": "Dauer", "description": "Länge.", "visual": False, "levels": [
                {"label": "kurz", "definition": "Kurz.", "hint": ""}, {"label": "lang", "definition": "Lang.", "hint": ""}]}]}]}]})
    client.post("/ainstein/new", data={"idea": "Spiel (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    client.post(url(app) + "/plan/derive")
    assert "not a method" in calls[-1]["instructions"] and project(app)["plan"]["design"] == "cross"
    client.post(url(app) + "/survey", data={"languages": "de"})
    with app.app_context():
        survey = db.session.get(Survey, project(app)["survey_id"])
        cbc = next(i for b in survey.blocks for i in b.items if i.type == "conjoint")
        assert cbc.config.get("ai") and not cbc.kind.ai_scale
        code, cbc_id = survey.code, cbc.id
    page = client.get(f"/surveys/{code}/editor/items/{cbc_id}").get_data(as_text=True)
    assert "Von einer KI entworfen" in page and "nicht validiert" not in page and "Weitere Items vorschlagen" not in page
    assert client.post(f"/surveys/{code}/editor/items/{cbc_id}/ai-more", data={"n": "3"}).status_code == 302  # refused
    with app.app_context():
        from app import items
        assert [k for k, t in items.REGISTRY.items() if t.ai_scale] == ["likert"]


def test_an_idea_needs_at_least_ten_words(app, client, monkeypatch):
    switch_on(client)
    fake_ai(monkeypatch, {"ainstein_chat": [TURN]})
    r = client.post("/ainstein/new", data={"idea": "Vertrauen in KI"})
    assert r.status_code == 400 and "mindestens 10 Wörtern" in r.get_data(as_text=True)
    assert 'data-min-words="10"' in client.get("/ainstein/").get_data(as_text=True)


def test_open_points_in_the_canvas_start_in_bold():
    from app.ainstein import box_html

    assert str(box_html("Design: Experiment (offen: Bewertungsdimensionen; <Relevanz>)")) == \
        "Design: Experiment (<strong>offen:</strong> Bewertungsdimensionen; &lt;Relevanz&gt;)"
    assert "(<strong>open:</strong> sample)" in str(box_html("x (open: sample)"))


def test_the_start_names_the_five_points_and_ainstein_asks_about_the_open_ones(app, client, monkeypatch):
    """Marks for what the idea names (guessed in the browser); the first answer names what is still open and asks
    about it (KICKOFF)."""
    switch_on(client)
    page = client.get("/ainstein/").get_data(as_text=True)
    assert all(f'data-aspect="{k}"' in page for k in ("topic", "who", "aim", "method", "sample"))
    calls = fake_ai(monkeypatch, {"ainstein_chat": [TURN]})
    client.post("/ainstein/new", data={"idea": "Vertrauen Menschen KI-Diagnosen? (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    assert "the expected sample. Name in one short sentence which of them are still" in calls[-1]["prompt"]


# --- code review 2026-10-15: K2, K6, K7, I6 ---------------------------------------------------------------------------

def test_answers_of_the_wrong_type_give_no_500(app, client, monkeypatch):
    """K2: a list where a key belongs, a number or a text where a list belongs: left out, no 500, no plan in pieces."""
    from app import ainstein

    plan = ainstein._clean_plan({"constructs": 5, "hypotheses": "Hypothese", "goal": ["a"], "design": {"x": 1},
                                 "sample": {"recruitment": ["panel"]}}, set())
    assert plan["constructs"] == [] and plan["hypotheses"] == [] and plan["goal"] == "" and plan["design"] == ""
    plan = ainstein._clean_plan({"constructs": [{"name": "A", "role": ["iv"], "when": {}, "reverse": ["some"], "items": "Item",
                                                 "measure": "ai"}],
                                 "hypotheses": [{"text": "H", "kind": ["x"], "constructs": "A"}]}, set())
    c = plan["constructs"][0]
    assert (c["role"], c["when"], c["reverse"], c["items"]) == ("other", "after", "none", [])
    assert plan["hypotheses"][0] == {"text": "H", "kind": "correlation", "constructs": []}

    monkeypatch.setattr(ainstein, "_context", lambda project, chat=True: "")
    answers = {}
    monkeypatch.setattr(ainstein, "_ask", lambda *a, **k: answers["now"])
    plan = {"intervention": "", "sample": {}, "constructs": [
        {"name": "Skala", "definition": "", "facets": [], "exclude": "", "points": 5, "reverse": "none", "items": [], "n": 3,
         "measure": "agreed", "spec": "eine Frage"},
        {"name": "Auto", "measure": "conjoint", "spec": "Preis: 1; 2\nFarbe: rot; blau"},
        {"name": "Wichtig", "measure": "maxdiff", "spec": "nur Prosa"}]}
    with app.test_request_context():
        monkeypatch.setattr(ainstein, "canvas_of", lambda project: {"stichprobe": {"text": ""}})
        answers["now"] = {"constructs": [{"construct": [0], "items": []}, {"construct": 0.0, "items": []},
                                         {"construct": True, "items": []}, {"construct": 0, "items": "Ein Item"},
                                         {"construct": 0, "items": [{"text": "Gut", "facet": ["x"]}]}]}
        assert ainstein._draft_items(None, plan, {0: 3}) == {0: [{"text": "Gut", "facet": "['x']", "reverse": False}]}
        answers["now"] = {"experiments": [{"construct": [1]}, {"construct": 2, "statements": "abc"},
                                          {"construct": 1, "attributes": 7}]}
        built = ainstein._build_choices(None, plan)
        assert 2 not in built and [a["name"] for a in built[1]["attributes"]] == ["Preis", "Farbe"]
        answers["now"] = {"items": [{"construct": [0], "type": "open"}, {"construct": 0, "type": "open", "questions": "abc"},
                                    {"construct": 0, "type": "likert", "rows": 5, "labels": "x"}]}
        assert ainstein._build_agreed(None, plan, ["de"]) == {}
        answers["now"] = {"items": "x"}
        assert ainstein._build_agreed(None, plan, ["de"]) == {}


def test_the_notes_prompt_has_a_limit(app, client, monkeypatch):
    """K6: after failed calls the messages not yet in the notes piled up into one prompt without a limit."""
    from app import ainstein

    prompts = []

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None):
        prompts.append(prompt)
        raise ai.AIError("weg")

    monkeypatch.setattr(ai, "generate_text", fake)
    chat = [{"role": "user" if n % 2 else "ai", "text": f"Nachricht {n} " + "x" * 3000} for n in range(400)]
    p = ResearchProject(name="P", idea="I", chat=chat, memory={})
    with app.test_request_context():
        assert ainstein.remember(p) is False
    assert len(prompts[0]) < ainstein.MAX_CONTEXT + 10000
    assert "Nachricht 389 " in prompts[0] and "Nachricht 0 " not in prompts[0] and "(earlier messages left out)" in prompts[0]


def test_overview_loads_only_projects_shared_with_the_researcher(app, client, monkeypatch):
    """K7: the overview loaded every shared project of every user with its conversation."""
    from app import ainstein
    from app.models import User

    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(is_admin=True))
        other = User(orcid="0000-0002-1825-0097", name="Other")
        db.session.add(other)
        db.session.flush()
        for name, shares in (("Mir", [{"kind": "person", "user_id": me.id, "role": "view"}]),
                             ("Fremd", [{"kind": "person", "user_id": other.id + 1000, "role": "edit"}])):
            db.session.add(ResearchProject(user_id=other.id, name=name, idea="", chat=[{"role": "user", "text": "x"}], shares=shares))
        db.session.commit()
        db.session.expire_all()
        loaded = []
        from sqlalchemy import event

        listen = lambda target, context: loaded.append(target.name)  # noqa: E731
        event.listen(ResearchProject, "load", listen)
        try:
            assert [p.name for p in ainstein.visible_projects(me)] == ["Mir"]
        finally:
            event.remove(ResearchProject, "load", listen)
        assert "Fremd" not in loaded


def test_drafting_items_keeps_what_a_co_author_changed_meanwhile(app, client, monkeypatch):
    """I6: the scale draft wrote the plan read before the AI call back, over what a co-author saved in the meantime."""
    from app import ainstein

    switch_on(client)
    fake_ai(monkeypatch, {"ainstein_chat": [TURN],
                          "ainstein_plan": {"constructs": [{"name": "Gefallen", "role": "dv", "measure": "ai", "n": 3},
                                                           {"name": "Zweite", "role": "iv", "measure": "ai", "n": 3}]}})
    client.post("/ainstein/new", data={"name": "Spiel", "idea": "Ein Bewegungsspiel für Ältere (eine erste grobe Idee mit genug Wörtern für AInstein)"})
    client.post(url(app) + "/plan/derive")

    def meanwhile(project, plan, wanted):  # a co-author saves an item of the other scale during the call
        with app.app_context():
            p = db.session.get(ResearchProject, 1)
            stored = json.loads(json.dumps(p.plan))
            stored["constructs"][1]["items"] = [{"text": "Von der Kollegin", "facet": "", "reverse": False}]
            p.plan = stored
            db.session.commit()
        return {0: [{"text": "Neu von AInstein", "facet": "", "reverse": False}]}

    monkeypatch.setattr(ainstein, "_draft_items", meanwhile)
    client.post(url(app) + "/scales", data={"s0_points": "5", "s0_reverse": "none", "s0_n": "3", "draft": "0"})
    constructs = project(app)["plan"]["constructs"]
    assert [i["text"] for i in constructs[0]["items"]] == ["Neu von AInstein"]
    assert [i["text"] for i in constructs[1]["items"]] == ["Von der Kollegin"]
