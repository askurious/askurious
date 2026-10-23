"""Limits and failures of the AI without a banner (app/ai_fetch.py; Ralf, 2026-10-10): a 429 of the API answers a
request of the page's script (header X-AI-Call) with what the dialog needs (message, models of the own access, the
platform), other failures with the sentence for the button; a failed call does not count on the daily limit.
Without the header the routes answer as before. No real AI call: the clients are fakes."""
from app import ai, ai_access, limits
from app.extensions import db
from app.models import AiAccess, AiUsage, Survey
from tests.accounts.test_own_ai_key import OTHER, FakeClient, _err, _store, _uid, other  # noqa: F401 (fixture)

AI_CALL = {"X-AI-Call": "1"}


def _live(app, client):
    client.post("/presenter/new", data={"title": "Live", "language": "de-du"})
    with app.app_context():
        return db.session.scalar(db.select(Survey).filter_by(title="Live")).code


def _refusing(status, code):
    def chat(kw):
        raise _err(status, code)
    return FakeClient(chat=chat, responses=chat)


def _count(app, uid):
    with app.app_context():
        return sum(r.count for r in db.session.scalars(db.select(AiUsage).filter_by(user_id=uid)))


def test_limit_kind_tells_quota_from_rate(app):
    assert ai.limit_kind(_err(429, "insufficient_quota")) == "quota"  # OpenAI
    assert ai.limit_kind(_err(429, "quota_exceeded")) == "quota"      # KI:connect
    assert ai.limit_kind(_err(429, "rate_limit_exceeded")) == "rate"
    assert "zu viele Anfragen" in ai_access.error_text("429 rate_limit_exceeded")
    assert "Kontingent" in ai_access.error_text("429 quota_exceeded")


def test_a_limit_opens_the_dialog_with_the_models(app, other, monkeypatch):
    code = _live(app, other)
    app.config["OPENAI_API_KEY"] = "sk-platform"
    uid = _store(app)
    with app.app_context():
        db.session.get(AiAccess, uid).models = ["gpt-oss-120b", "mistral-small-4-119b-2603"]
        db.session.commit()
    monkeypatch.setattr(ai_access, "client", lambda *a, **k: _refusing(429, "quota_exceeded"))
    r = other.post(f"/presenter/{code}/ai", data={"description": "Vorlesung, Tempo?"}, headers=AI_CALL)
    d = r.get_json()
    assert d["status"] == "limit" and d["code"] == "quota" and d["own"] is True
    assert "Ihr Kontingent beim KI-Anbieter ist erschöpft" in d["message"]
    assert d["models"] == ["gpt-oss-120b", "mistral-small-4-119b-2603"] and d["current"] == "gpt-oss-120b"
    assert d["platform"] is True and d["model_url"] == "/account/ai/model"
    # no banner left for the next page
    assert "Kontingent" not in other.get(f"/presenter/{code}/ai").data.decode()
    # the dialog's choice of the platform: the access keeps its list, the calls go over the installation
    assert other.post("/account/ai/model", data={"model": "", "autosave": "1"}).get_json()["status"] == "saved"
    with app.app_context():
        row = db.session.get(AiAccess, _uid(app))
        assert row.text_model == "" and row.models
    # without the script (no header): the page as before, with the sentence
    monkeypatch.setattr(ai, "_client", lambda: _refusing(429, "rate_limit_exceeded"))
    page = other.post(f"/presenter/{code}/ai", data={"description": "Vorlesung"}).data.decode()
    assert "Beim KI-Dienst gehen gerade zu viele Anfragen ein" in page


def test_a_rate_limit_of_the_platform_offers_no_models(app, other, monkeypatch):
    code = _live(app, other)
    app.config["OPENAI_API_KEY"] = "sk-platform"
    monkeypatch.setattr(ai, "_client", lambda: _refusing(429, "rate_limit_exceeded"))
    d = other.post(f"/presenter/{code}/ai", data={"description": "Vorlesung"}, headers=AI_CALL).get_json()
    assert d["status"] == "limit" and d["code"] == "rate" and d["own"] is False and d["models"] == []
    assert d["platform"] is False


def test_other_failures_come_back_as_a_line_for_the_button(app, other, monkeypatch):
    code = _live(app, other)
    _store(app)
    monkeypatch.setattr(ai_access, "client", lambda *a, **k: _refusing(404, "model_not_found"))
    d = other.post(f"/presenter/{code}/ai", data={"description": "Vorlesung"}, headers=AI_CALL).get_json()
    assert d["status"] == "error" and "Das gewählte Modell gibt es für Ihren Zugang nicht mehr" in d["message"]
    assert d["access_url"] == "/account/connections#ki-zugang"
    assert "nicht mehr" not in other.get(f"/presenter/{code}/ai").data.decode()  # no banner afterwards
    # a redirect becomes its address; the messages of success stay for the next page
    r = other.post("/presenter/new", data={"title": "Zwei", "language": "de-du"}, headers=AI_CALL)
    assert r.get_json()["status"] == "redirect" and "/presenter/" in r.get_json()["url"]


def test_a_failed_call_does_not_count(app, other, monkeypatch):
    """Only a call refused before processing is given back (decision 5a, 2026-10-11: 401/403/404/400, no
    connection); a 429 counts, as the provider took it in (tests/interview/test_audit11_ki.py has the other cases)."""
    uid = _uid(app)
    app.config["OPENAI_API_KEY"] = "sk-platform"
    app.config["AI_TEXTS_PER_DAY"] = 5
    code = _live(app, other)
    monkeypatch.setattr(ai, "_client", lambda: _refusing(401, "invalid_api_key"))
    for _ in range(7):  # more than the limit: none of them counts
        other.post(f"/presenter/{code}/ai", data={"description": "Vorlesung"}, headers=AI_CALL)
    assert _count(app, uid) == 0
    monkeypatch.setattr(ai, "_client", lambda: FakeClient())
    monkeypatch.setattr(ai, "ask", lambda *a, **k: ("Hallo", {}, None))
    with app.test_request_context():
        from flask_login import login_user

        from app.models import User

        login_user(db.session.get(User, uid))
        assert ai.generate_text("x", "y") == "Hallo"
        limits.refund_ai()  # after a call that worked there is nothing to give back (not even by mistake)
    assert _count(app, uid) == 1  # a call that worked counts
