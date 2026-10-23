"""Handwriting of participants goes to an AI only over the researchers' own AI access, never over the installation's
key (Ralf, 2026-10-21): without an own access the sheet page offers no AI reading, the confirmation lists no platform
recipient, and a form sent from before (even with an old confirmation for OpenAI) reads nothing."""
from app import ai
from app.extensions import db
from app.models import Survey
from tests.exports.test_paper import _all_pages, _field, _pdf, _run, _setup, _sheets, _upload, _yes


def test_no_ai_reading_over_the_platform_key(app, client, survey, monkeypatch):
    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    text = _field(s["plan"], "i5", k="write")
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]), writes=[text])))
    app.config["OPENAI_API_KEY"] = "sk-platform"
    called = []
    monkeypatch.setattr(ai, "generate_text", lambda *a, **k: called.append(1) or "{}")
    page = client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
    assert "Nur mit einem eigenen KI-Zugang" in page and "/account/connections?for=paper#ki-zugang" in page
    assert 'name="to" value="openai"' not in page and f"/sheets/{s['id']}/read" not in page
    client.post("/surveys/1/paper/ai-informed", data={"informed": "1", "to": "openai"})
    with app.app_context():
        assert not (db.session.get(Survey, 1).paper or {}).get("ai_ok")
        survey_row = db.session.get(Survey, 1)                 # a confirmation for OpenAI from before
        survey_row.paper = {**(survey_row.paper or {}), "ai_ok": {"to": {"openai": {"by": 1, "at": "2026-10-11",
                                                                                     "name": "OpenAI"}}}}
        db.session.commit()
    assert client.post(f"/surveys/1/paper/sheets/{s['id']}/read").status_code == 404
    assert not called
