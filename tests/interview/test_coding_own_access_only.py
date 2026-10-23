"""The AI coding help runs only over the researchers' own AI access, never over the installation's key (Ralf,
2026-10-21): the page offers it only with an own access, the server refuses a call without one, the confirmation
lists only own accesses, and the participants' texts name the own provider (a survey confirmed for OpenAI before
keeps saying so)."""
import json

from app import ai
from app.extensions import db
from app.models import Survey
from tests.conftest import own_ai
from tests.interview.test_coding import _survey


def test_without_own_access_no_ai_help_even_with_the_platform_key(app, client, survey, monkeypatch):
    key = _survey(app, client)
    app.config["OPENAI_API_KEY"] = "sk-platform"
    called = []
    monkeypatch.setattr(ai, "generate_text", lambda *a, **k: called.append(1) or json.dumps({"categories": []}))
    page = client.get(f"/surveys/1/categorise/{key}").data.decode()
    # the button stays and leads to the form for the own access, with a hint there (Ralf, 2026-10-21)
    assert "Kategorien vorschlagen lassen" in page and "/account/connections?for=coding#ki-zugang" in page
    assert "/ai/scheme" not in page
    hint = client.get("/account/connections?for=coding").data.decode()
    assert "läuft deshalb nur über einen eigenen KI-Zugang" in hint
    assert "läuft deshalb nur" not in client.get("/account/connections").data.decode()
    assert 'name="to" value="openai"' not in page                       # no confirmation for the platform's access
    client.post(f"/surveys/1/categorise/{key}/ai/informed", data={"informed": "1", "to": "openai"})
    client.post(f"/surveys/1/categorise/{key}/ai/scheme")               # a form of before: refused
    assert not called
    with app.app_context():
        assert "openai" not in json.dumps((db.session.get(Survey, 1).codings or {}).get("_ai") or {})


def test_texts_name_the_own_provider_and_keep_openai_for_a_survey_confirmed_before(app, client, survey):
    from app.items.consent import ai_coding_sentence

    key = _survey(app, client)
    own_ai(app)
    client.post(f"/surveys/1/categorise/{key}/ai/informed", data={"informed": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        text = ai_coding_sentence("de", s)
        assert "llm.example.org" in text and "OpenAI" not in text
        assert "the terms of this provider apply" in ai_coding_sentence("en", s)
        s.codings = {**s.codings, "_ai": {"to": {**s.codings["_ai"]["to"], "openai": {"by": 1, "at": "2026-10-11"}}}}
        db.session.commit()
        assert "OpenAI" in ai_coding_sentence("de", db.session.get(Survey, 1))
