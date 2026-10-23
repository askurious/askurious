"""The AI text of a vignette without factors says so in the reader's language (security and code audit 2026-10-13,
CF-2): it was the only error message of the app in German only."""
import pytest
from flask import g

from app import ai, vignettes_ai


def test_without_factors_in_english(app):
    with app.test_request_context():
        g.ui_lang = "en"
        with pytest.raises(ai.AIError) as e:
            vignettes_ai.suggest_text(None, {"attributes": []})
    assert str(e.value) == "Add factors first."
