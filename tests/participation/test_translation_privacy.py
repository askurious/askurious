"""KR-4: actual translation prompts minimise known people and restore their exact local text."""
import json

from app import ai
from app.extensions import db
from app.models import Item, Survey, User
from tests.participation.test_translations import choice, fields


def test_provider_never_receives_known_people_or_contact_details(app, client, survey, monkeypatch):
    choice(client, question="Contact person@example.org or +49 241 123456.")
    with app.app_context():
        user = db.session.get(User, 1)
        user.name, user.affiliation = "Researcher Example", "Private Institute"
        study = db.session.get(Survey, 1)
        study.description = "Researcher Example at Private Institute: person@example.org"
        consent = db.session.get(Item, 1)
        consent.type, consent.config = "consent", {"researchers": [f"u{user.id}"]}
        consent.texts = {"de": {"body": "Researcher Example at Private Institute, person@example.org"}}
        db.session.commit()
    calls = []
    def fake(instructions, prompt, **options):
        for sensitive in ("Researcher Example", "Private Institute", "person@example.org", "+49 241 123456"):
            assert sensitive not in prompt
        calls.append(prompt)
        entries = json.loads(prompt.split("Texts (JSON):\n", 1)[1])
        return json.dumps({entry["id"]: "EN " + entry["text"] for entry in entries})
    monkeypatch.setattr(ai, "generate_text", fake)
    response = client.post("/surveys/1/translation/ai?lang=en")
    assert response.status_code == 200 and response.json["done"] and calls
    translated, _ = fields(app)
    assert "person@example.org" in translated[("item:2", "question")]["target"]
    with app.app_context():
        assert "Researcher Example" in db.session.get(Item, 1).texts["en"]["body"]
