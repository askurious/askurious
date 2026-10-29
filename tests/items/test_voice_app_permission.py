"""A voice question inside the diary app names the app's settings, not a browser (UI/UX audit 2026-10-29, AND-09)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_voice_texts_follow_where_the_page_runs():
    html = (ROOT / "app/templates/items/voice/participant.html").read_text(encoding="utf-8")
    assert "{% if in_app_view %} data-in-app{% endif %}" in html
    assert "in den Einstellungen der App erlauben" in html and "den Zugriff im Browser erlauben" in html
    js = (ROOT / "app/static/participant-voice.js").read_text(encoding="utf-8")
    assert 'box.hasAttribute("data-in-app")' in js and "Einstellungen der App" in js and "Browserfreigabe" in js
