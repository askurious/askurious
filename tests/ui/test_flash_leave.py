"""Messages after an action (app.js, flashControls): information and confirmations leave by themselves after
10 s, errors and warnings stay until closed; the clock stands under the pointer, with the focus and in a hidden tab
(Ralf, 2026-10-15)."""
import os
import re

JS = open(os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "app.js"), encoding="utf-8").read()


def _controls():
    return JS[JS.index("function flashControls(note)"):JS.index("// The arrows ↶ and ↷")]


def test_all_messages_leave_after_ten_seconds():
    assert re.search(r"var FLASH_SECONDS = 10;", JS)
    assert "setTimeout(leave, FLASH_SECONDS * 1000)" in _controls()
    assert "12000" not in JS and "setTimeout(leave, 8000)" not in JS     # the former two clocks are gone


def test_errors_and_warnings_stay():
    body = _controls()
    stay = body.index('note.classList.contains("flash-error") || note.classList.contains("flash-warning")) return;')
    assert stay < body.index("setTimeout(leave, FLASH_SECONDS")


def test_clock_stands_under_pointer_focus_and_hidden_tab():
    body = _controls()
    for event in ('"mouseenter", stop', '"focusin", stop', '"mouseleave", start', '"focusout"', '"visibilitychange"'):
        assert event in body
    assert "document.hidden" in body and 'note.matches(":hover")' in body


def test_server_and_script_messages_share_the_controls():
    assert 'document.querySelectorAll("[data-flashes] .flash").forEach(flashControls);' in JS
    flash = JS[JS.index("function flash(message, kind)"):JS.index("function pageError")]
    assert "flashControls(note);" in flash
