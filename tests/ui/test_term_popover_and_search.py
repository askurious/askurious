"""Keyboard ways of the term popup and the live list search (UI/UX audit 2026-10-29, A11Y-04, A11Y-05)."""
from pathlib import Path

JS = (Path(__file__).resolve().parents[2] / "app/static/app.js").read_text(encoding="utf-8")


def _part(start, end):
    a = JS.index(start)
    return JS[a:JS.index(end, a)]


def test_tab_leads_into_the_term_popup_and_on_after_the_term():
    part = _part('pop.id = "learn-pop"', "// Help folds as popups")
    assert 'e.key !== "Tab"' in part
    assert "link.focus()" in part and "owner.focus()" in part  # Tab into the popup, Shift+Tab back to the term
    assert "after(term)" in part  # Tab from the link goes on behind the term, not to the end of the page


def test_live_search_waits_for_an_input_method():
    part = _part('form[data-live-search]', "form.submit()")
    assert '"compositionstart"' in part and '"compositionend"' in part and "e.isComposing" in part
