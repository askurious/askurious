"""Autosave must not swallow a button with an address of its own (the "Testaufruf" of a web service did nothing, 2026-11-05:
it has no name, so the form only saved)."""
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[2] / "app" / "static" / "app.js"


def test_a_button_with_its_own_address_goes_through_the_save():
    js = APP_JS.read_text(encoding="utf-8")
    at = js.index('if (e.submitter && e.submitter.name) return;')
    part = js[at:at + 1200]
    assert 'hasAttribute("formaction")' in part and "save(null).then" in part and "out.submit()" in part and "form.requestSubmit(" not in part
