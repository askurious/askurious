"""The prompt of an AI image (app/items/image.py): its {{variables}} filled per loop row."""
from app.extensions import db
from app.models import Item
from tests.conftest import add


def test_prompt_placeholders_with_fallback_are_filled(app, client, survey):
    """Code review 2026-10-15, K3: {{var|text}} stayed in the prompt, so every paid variant got the same prompt."""
    add(client, "image")
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "source": "ai", "prompt": "A {{ animal|cat }} on a {{colour}} {{thing|sofa}}, {{other}}"}
        db.session.commit()
        prompt = item.kind.full_prompt(item, {"animal": "dog", "colour": "red", "thing": ""})
        assert "A dog on a red sofa, {{other}}" in prompt
        assert "A cat on a  sofa" in item.kind.full_prompt(item, {"colour": ""})
