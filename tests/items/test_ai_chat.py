"""KI-Chat for participants (app/ai_chat.py, app/items/ai_chat.py; experimental "ai_chat", concept
ki-assistent-tagebuch-konzept.md): the study's own model access, the chat through the server with every message logged,
the limits, the consent tick, the export, the documentation and the deletion."""
from app import ai_access, ai_chat, packages, secrets_box
from app.extensions import db
from app.models import AiChatAccess, AiChatTurn, Response, Survey
from tests.conftest import CONSENT_OK, add, experiments_on, fill_consent

E = "/surveys/1/editor"
AGREE = {**CONSENT_OK, "i1_c_ai_chat": "1"}


def setup(app, client, monkeypatch, **extra):
    experiments_on(client, "ai_chat")
    fill_consent(client)
    add(client, "ai_chat")                                          # item 2
    client.post(E + "/items/2", data={"code": "chat", "question": "Schreiben Sie mit dem Assistenten.",
                                      "system": "Du bist ein freundlicher Assistent.", "greeting": "Hallo!",
                                      "max_messages": "3", "min_messages": "1", "max_chars": "200",
                                      "answer_mode": "required", **extra})
    monkeypatch.setattr(ai_access, "model_ids", lambda c: ["model-a", "model-b"])


def save_access(client, **extra):
    return client.post("/surveys/1/ai-chat", data={"provider": "openai", "key": "sk-study-1234", "model": "model-a",
                                                   "per_day": "50", **extra})


def start(client):
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de", "en"], "default_language": "de",
                                         "allow_back": "1"})
    return client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})


def participant(app):
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=AGREE)
    return p


def fake_model(monkeypatch, seen=None):
    def call(row, history):
        if seen is not None:
            seen.append(history)
        return f"Antwort {len(history)}", (len(history), 4)
    monkeypatch.setattr(ai_chat, "_call", call)


def test_the_access_belongs_to_the_survey_and_its_key_is_locked_away(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch)
    r = save_access(client)
    assert r.status_code == 302
    with app.app_context():
        row = db.session.get(AiChatAccess, 1)
        assert (row.provider, row.model, row.version, row.per_day, row.key_hint) == ("openai", "model-a", 1, 50, "1234")
        assert "sk-study" not in row.key_enc
        assert secrets_box.decrypt(row.key_enc, secrets_box.PARTICIPANT) == "sk-study-1234"
    page = client.get("/surveys/1/ai-chat").data.decode()
    assert "…1234" in page and "sk-study-1234" not in page
    assert "model-a" in client.get(E + "/items/2").data.decode()


def test_without_the_feature_the_page_is_closed(app, client, survey):
    assert client.get("/surveys/1/ai-chat").status_code == 404


def test_the_survey_cannot_start_without_an_access(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch)
    with app.app_context():
        item = db.session.get(Survey, 1).blocks[1].items[0]
        assert any("Modellzugang" in p for p in item.kind.validate(item))
    save_access(client)
    with app.app_context():
        item = db.session.get(Survey, 1).blocks[1].items[0]
        assert item.kind.validate(item) == []


def test_a_message_goes_to_the_model_and_both_sides_are_logged(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch)
    save_access(client)
    start(client)
    seen = []
    fake_model(monkeypatch, seen)
    p = participant(app)
    page = p.get("/s/1").data.decode()
    assert 'data-aichat="2"' in page and "ai_chat.js" in page and "nicht mit einem Menschen" in page
    r = p.post("/s/1/chat/2", json={"message": "Schreib an anna@example.org bitte"})
    assert r.status_code == 200, r.data
    assert r.json["reply"] == "Antwort 2" and r.json["left"] == 2 and not r.json["crisis"]
    # the model got the instructions and the masked message, never the address
    assert seen[0][0] == {"role": "system", "content": "Du bist ein freundlicher Assistent."}
    assert "anna@example.org" not in seen[0][1]["content"] and "[EMAIL]" in seen[0][1]["content"]
    p.post("/s/1/chat/2", json={"message": "Und weiter?"})
    assert [m["role"] for m in seen[1]] == ["system", "user", "assistant", "user"]
    with app.app_context():
        turns = db.session.scalars(db.select(AiChatTurn).order_by(AiChatTurn.id)).all()
        assert [(t.role, t.status) for t in turns] == [("user", "ok"), ("assistant", "ok")] * 2
        assert turns[0].text == "Schreib an anna@example.org bitte" and "[EMAIL]" in turns[0].masked
        assert turns[1].model == "model-a" and turns[1].access_version == 1 and turns[1].tokens_out == 4
    p.post("/s/1", data={"nav": "next"})
    with app.app_context():
        response = db.session.scalar(db.select(Response).filter_by(source="real"))
        assert response.answers["2"] == {"n": 2, "chars": 44, "replies": 2, "crisis": 0}
        survey_row = db.session.get(Survey, 1)
        item = survey_row.blocks[1].items[0]
        rows = item.kind.long_rows(item, response)
        assert [r["role"] for r in rows] == ["participant", "ai", "participant", "ai"]
        assert rows[0]["text"].startswith("Schreib an anna@") and rows[1]["masked"] == ""
        assert item.kind.export_values(item, response.answers["2"]) == {
            "chat_n": 2, "chat_chars": 44, "chat_replies": 2, "chat_crisis": 0}
        names = [name for name, _ in packages.long_files(survey_row)]
        assert "chat_chat_long.csv" in names
        assert "chat_chat_long.csv" not in [n for n, _ in packages.long_files(survey_row, open_answers=False)]


def test_a_required_chat_needs_a_message_before_next(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch)
    save_access(client)
    start(client)
    p = participant(app)
    r = p.post("/s/1", data={"nav": "next"})
    assert "mindestens 1 Nachricht" in r.data.decode()


def test_limits_per_chat_and_per_day(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch, max_messages="1")
    save_access(client, per_day="1")
    start(client)
    fake_model(monkeypatch)
    p = participant(app)
    assert p.post("/s/1/chat/2", json={"message": "eins"}).json["left"] == 0
    r = p.post("/s/1/chat/2", json={"message": "zwei"})
    assert r.status_code == 409 and "Zahl der Nachrichten" in r.json["error"]
    q = participant(app)  # the survey's messages of the day are used up: logged, not sent
    r = q.post("/s/1/chat/2", json={"message": "drei"})
    assert r.status_code == 200 and "nicht mehr verfügbar" in r.json["refused"]
    with app.app_context():
        assert db.session.scalar(db.select(AiChatTurn.status).filter_by(text="drei")) == "limit"
    assert p.post("/s/1/chat/2", json={"message": "x" * 201}).status_code in (400, 409)


def test_crisis_words_show_the_help_and_are_marked(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch)
    save_access(client)
    start(client)
    fake_model(monkeypatch)
    p = participant(app)
    assert 'data-aichat-crisis hidden' in p.get("/s/1").data.decode()
    r = p.post("/s/1/chat/2", json={"message": "Ich will nicht mehr leben"})
    assert r.json["crisis"] is True
    with app.app_context():
        assert db.session.scalar(db.select(AiChatTurn.status).filter_by(role="user")) == "crisis"


def test_a_failing_service_keeps_the_message_with_its_status(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch)
    save_access(client)
    start(client)

    def broken(row, history):
        raise RuntimeError("down")
    monkeypatch.setattr(ai_chat, "_call", broken)
    p = participant(app)
    r = p.post("/s/1/chat/2", json={"message": "Hallo"})
    assert r.status_code == 502 and r.json["error"]
    with app.app_context():
        assert [(t.role, t.status) for t in db.session.scalars(db.select(AiChatTurn))] == [("user", "error")]


def test_only_a_participation_under_way_may_chat(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch)
    save_access(client)
    start(client)
    fake_model(monkeypatch)
    assert app.test_client().post("/s/1/chat/2", json={"message": "Hallo"}).status_code == 409
    p = participant(app)
    assert p.post("/s/1/chat/1", json={"message": "Hallo"}).status_code == 404  # the consent is no chat


def test_after_the_start_only_a_new_key_may_come(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch)
    save_access(client)
    start(client)
    save_access(client, model="model-b", key="")
    with app.app_context():
        assert db.session.get(AiChatAccess, 1).model == "model-a"
    save_access(client, key="sk-study-9999")
    with app.app_context():
        row = db.session.get(AiChatAccess, 1)
        assert (row.version, row.key_hint, row.model) == (2, "9999", "model-a")
        assert row.history[0]["version"] == 1


def test_the_documentation_names_the_origin_never_the_key(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch)
    save_access(client)
    with app.app_context():
        files = dict(packages.documentation(db.session.get(Survey, 1), "de"))
        text = files[packages.AI_CHAT].decode()
        assert "model-a" in text and "OpenAI" in text and "Du bist ein freundlicher Assistent." in text
        assert "sk-study" not in text and "1234" not in text


def test_the_consent_asks_its_own_tick_and_names_the_service(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch)
    save_access(client)
    with app.app_context():
        consent = db.session.get(Survey, 1).consent_item
        assert "ai_chat" in consent.kind.confirmation_keys(consent)
        assert "model-a" in consent.kind.facts(consent, "de")["ai_chat"]
    start(client)
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=CONSENT_OK)  # without the tick of the chat
    assert 'data-aichat="2"' not in p.get("/s/1").data.decode()
    assert "KI-Assistenten schreiben" in app.test_client().get("/s/1/privacy").data.decode()


def test_messages_go_with_their_participation_and_the_survey(app, client, survey, monkeypatch):
    setup(app, client, monkeypatch)
    save_access(client)
    start(client)
    fake_model(monkeypatch)
    for _ in range(2):
        participant(app).post("/s/1/chat/2", json={"message": "Hallo"})
    with app.app_context():
        first = db.session.scalar(db.select(Response).order_by(Response.id))
        db.session.delete(first)
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(AiChatTurn.id))) == 2
        db.session.add(AiChatTurn(survey_id=1, response_id=99999, item_id=2, role="user", text="verwaist"))
        db.session.commit()
        assert ai_chat.sweep() == 1
        ai_chat.forget_survey(1, access_too=True)
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(AiChatTurn.id))) == 0
        assert db.session.get(AiChatAccess, 1) is None
