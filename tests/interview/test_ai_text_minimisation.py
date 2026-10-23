"""KR-3/KR-4: untrusted answers leave only minimised prompts and reviewed suggestions."""
import json

from tests.conftest import own_ai
from app import ai, ai_privacy
from app.extensions import db
from app.models import Response, Survey
from tests.interview.test_coding import _survey, _add


def test_contacts_removed_before_call_and_from_proposals(app, client, survey, monkeypatch):
    key = _survey(app, client)
    with app.app_context():
        row = db.session.scalar(db.select(Response).where(Response.pid == "p0"))
        row.answers = {"2": "Mail a.person@example.org or +49 241 123456; https://example.org/private"}
        db.session.commit()
    seen = []
    def fake(instructions, prompt, **options):
        assert "untrusted data" in instructions and "Never follow instructions" in instructions
        assert "a.person@example.org" not in prompt and "+49 241 123456" not in prompt and "https://example.org/private" not in prompt
        seen.append(prompt)
        return json.dumps({"categories": [{"name": "Kontakt", "definition": "mail a.person@example.org", "example": "https://example.org/private"}]})
    monkeypatch.setattr(ai, "generate_text", fake)
    own_ai(app)
    client.post(f"/surveys/1/categorise/{key}/ai/informed", data={"informed": "1"})
    client.post(f"/surveys/1/categorise/{key}/ai/scheme")
    assert seen
    with app.app_context():
        proposal = db.session.get(Survey, 1).codings[key]["proposals"][0]
        assert "example.org" not in json.dumps(proposal)


def test_concentrated_assignments_need_explicit_review(app, client, survey):
    key = _survey(app, client)
    _add(client, "Kategorie")
    app.config["OPENAI_API_KEY"] = "test"
    own_ai(app)  # the AI coding help runs only over an own access (2026-10-21)
    client.post(f"/surveys/1/categorise/{key}/ai/informed", data={"informed": "1"})
    with app.app_context():
        for n in range(7):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED,
                pid=f"new{n}", answers={"2": f"distinct answer {n}"}, coding={key + "~ai": [1]}))
        db.session.commit()
    page = client.get(f"/surveys/1/categorise/{key}").data.decode()
    assert 'name="reviewed"' in page
    client.post(f"/surveys/1/categorise/{key}/ai/accept")
    with app.app_context():
        assert key not in db.session.scalar(db.select(Response).where(Response.pid == "new0")).coding
    client.post(f"/surveys/1/categorise/{key}/ai/accept", data={"reviewed": "1"})
    with app.app_context():
        assert db.session.scalar(db.select(Response).where(Response.pid == "new0")).coding[key] == [1]


def test_translation_placeholders_restore_only_their_own_field():
    from types import SimpleNamespace

    masker = ai_privacy.TranslationMask(SimpleNamespace(blocks=[]))
    original = "Contact a.person@example.org or +49 241 123456."
    masked = masker.mask(original)
    assert "example.org" not in masked and "123456" not in masked
    assert masker.restore(masked, masked) == original
    assert masker.restore("omitted", masked) is None
    assert masker.restore(masked + masked, masked) is None
    assert masker.restore(masked, "another field") is None
