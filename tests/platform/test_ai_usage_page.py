"""The page "KI-Nutzung" (app/ai_access.py; ki-nutzung-seite-konzept.md, E1-E9 as recommended, Ralf 2026-10-10):
the switch "Generative KI nutzen" (off: no AI function or note shows, nothing goes to an AI service), the person's
calls of the last 30 days, the platform's models, the menu entry and the old address. No real AI call."""
import os
import re
from datetime import timedelta

import pytest

from app import ai, ai_access
from app.extensions import db
from app.models import AiCall, Survey, User, utcnow
from tests.conftest import add

ME = "0000-0000-0000-001X"  # site admin (tests/conftest.py)
TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "app", "templates")
# pages that are AI as a whole: the server answers them with "ausgeschaltet" (ai_access._ai_off_page); and the own
# access, which shows on "KI-Nutzung" only while AI is on
GUARDED = ("ainstein/", "interview/ai_", "live/assistant.html", "live/_ai_suggestions.html", "accounts/_ai_access.html")


def _uid(app, orcid=ME):
    with app.app_context():
        return db.session.scalar(db.select(User.id).filter_by(orcid=orcid))


def _off(client):
    r = client.post("/account/ai/switch", data={"autosave": "1"})
    assert r.get_json()["status"] == "saved"


def test_every_template_with_an_ai_button_asks_ai_on():
    """A new AI button must not show while generative AI is off: every template with one asks ai_on() (or ai_on,
    the flag its route passes), unless the whole page is guarded on the server."""
    missing = []
    for root, _dirs, files in os.walk(TEMPLATES):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, TEMPLATES).replace(os.sep, "/")
            if rel.startswith(GUARDED) or rel == "_macros.html":
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            # ai_on, or a flag of its route that includes ai_on() (coding_ai_on, paper_ai_on, the blog's ai_ready)
            if re.search(r"button-ai|data-ai-call|ai_access_line\(|ai_recipient\(", text) and not re.search(r"ai_on|ai_ready", text):
                missing.append(rel)
    assert not missing, missing


def test_the_page_and_the_menu(app, client):
    page = client.get("/account/ai/").data.decode()
    for words in ("Generative KI nutzen", "Gerade benutzt", 'data-moved-anchor="ki-zugang"', "Ihre KI-Aufrufe", "Modelle der Plattform",
                  "Was darf ich damit?", "Keine Rechtsberatung"):
        assert words in page, words
    assert "experimentell" not in page
    menu = client.get("/dashboard/").data.decode()
    assert 'href="/account/ai/"' in menu and "KI-Nutzung" in menu
    assert menu.index('href="/account/"') < menu.index('href="/account/ai/"')
    # the own access is on "Verbindungen" since Konto-Seiten B2; old links on "Konto" and "KI-Nutzung" go there (app.js)
    assert 'id="ki-zugang"' not in page and 'id="ki-zugang"' in client.get("/account/connections").data.decode()
    assert 'data-moved-anchor="ki-zugang"' in client.get("/account/").data.decode()
    assert 'id="ki-zugang"' not in client.get("/account/").data.decode()


def test_the_platform_models_come_from_the_configuration(app, client):
    app.config.update(OPENAI_API_KEY="sk-x", OPENAI_TEXT_MODEL="gpt-6-luna", OPENAI_IMAGE_MODEL="gpt-image-x",
                      WHISPER_URL="https://whisper.example.org")
    page = client.get("/account/ai/").data.decode()
    assert "gpt-6-luna" in page and "gpt-image-x" in page and "Transkription von Interviews" in page
    assert "Data controls in the OpenAI platform" in page


def test_switched_off_nothing_of_ai_shows_and_nothing_is_sent(app, client, survey, monkeypatch):
    app.config["OPENAI_API_KEY"] = "sk-x"
    add(client, "image")
    client.post("/presenter/new", data={"title": "Live", "language": "de-du"})
    with app.app_context():
        from app.models import Item

        live = db.session.scalar(db.select(Survey).filter_by(title="Live")).code
        image = db.session.scalar(db.select(Item.id).filter_by(type="image"))
    pages = ["/surveys/1/editor/", "/surveys/1/editor/items/1", f"/surveys/1/editor/items/{image}", f"/presenter/{live}",
             f"/presenter/{live}/setup/question", "/surveys/1/methods"]
    for url in pages:  # on: the AI buttons are there
        assert client.get(url).status_code == 200, url
    assert "button-ai" in client.get("/surveys/1/editor/").data.decode()
    _off(client)
    with app.app_context():
        assert db.session.get(User, _uid(app)).ai_enabled is False
    for url in pages:
        page = client.get(url).data.decode()
        assert "button-ai" not in page and "data-ai-ideas" not in page and "ai-access-line" not in page, url
        assert "geht an OpenAI" not in page and "go to OpenAI" not in page, url
    # the pages that are AI as a whole say so; the page "KI-Nutzung" keeps only the switch
    assert "Generative KI ist für Sie ausgeschaltet" in client.get(f"/presenter/{live}/ai").data.decode()
    page = client.get("/account/ai/").data.decode()
    assert "Generative KI ist für Sie ausgeschaltet" in page and "Ihre KI-Aufrufe" not in page
    # nothing goes to an AI service, also when a request comes in anyway
    monkeypatch.setattr(ai, "_client", lambda: pytest.fail("called the AI with the switch off"))
    with app.test_request_context():
        from flask_login import login_user

        login_user(db.session.get(User, _uid(app)))
        with pytest.raises(ai.AIError, match="ausgeschaltet"):
            ai.generate_text("x", "y")
        with pytest.raises(ai.AIError, match="ausgeschaltet"):
            ai.generate_image("Haus")
    # on again: the buttons come back
    assert client.post("/account/ai/switch", data={"ai_enabled": "1", "autosave": "1"}).get_json()["status"] == "saved"
    assert "button-ai" in client.get("/surveys/1/editor/").data.decode()


def test_the_calls_of_the_last_30_days(app, client, monkeypatch):
    uid = _uid(app)
    now = utcnow()
    with app.app_context():
        rows = [("translation", True, "platform", 100), ("translation", False, "platform", 0),
                ("translation", True, "own", 50), ("consent", True, "platform", 10)]
        for purpose, ok, source, tokens in rows:
            db.session.add(AiCall(at=now, kind="text", purpose=purpose, model="m", input_tokens=tokens,
                                  output_tokens=0, ok=ok, source=source, user_id=uid))
        db.session.add(AiCall(at=now - timedelta(days=31), kind="text", purpose="consent", model="m", ok=True, user_id=uid))
        db.session.add(AiCall(at=now, kind="text", purpose="consent", model="m", ok=True, user_id=uid + 99))
        db.session.add(AiCall(at=now, kind="image", purpose="image", model="i", ok=True, images=1, user_id=uid))
        db.session.commit()
        with app.test_request_context():
            calls, sums = ai_access.usage(db.session.get(User, uid))
    assert [c["key"] for c in calls] == ["translation", "consent", "image"]  # most used first
    assert calls[0]["calls"] == 3 and calls[0]["failed"] == 1 and calls[0]["own"] == 1 and calls[0]["tokens"] == 150
    assert sums == {"calls": 5, "failed": 1, "own": 1, "platform": 4, "tokens": 160, "images": 1}
    page = client.get("/account/ai/").data.decode()
    assert "Übersetzung" in page and "60 % (3)" in page.replace(" ", " ").replace("&nbsp;", " ")
    assert "ohne Tageslimit" in page  # a site admin


def test_calls_name_the_person_and_lose_it_with_the_account(app, client, monkeypatch):
    from app.accounts import delete

    app.config["OPENAI_API_KEY"] = "sk-x"
    monkeypatch.setattr(ai, "_client", lambda: object())
    monkeypatch.setattr(ai, "ask", lambda *a, **k: ("Hallo", {}, None))
    uid = _uid(app)
    with app.test_request_context():
        from flask_login import login_user

        login_user(db.session.get(User, uid))
        assert ai.generate_text("x", "y", purpose="translation") == "Hallo"
    with app.app_context():
        assert db.session.scalar(db.select(AiCall.user_id).where(AiCall.purpose == "translation")) == uid
        with app.test_request_context():
            delete(db.session.get(User, uid), by="self")
        assert db.session.scalar(db.select(AiCall.user_id).where(AiCall.purpose == "translation")) is None
        assert db.session.scalar(db.select(db.func.count(AiCall.id))) == 1  # the call stays, without a person
