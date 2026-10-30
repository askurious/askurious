"""The app dialog (askConfirm, app/static/app.js) answers with the click itself: "Ja", "Abbrechen", Escape and a click
beside it settle the question at once, the dialog's "close" event is only a fallback, and each question is answered
once. In the test browser of the desktop app the "close" event never came, so "Ja" did nothing and every later
question waited behind the first (Ralf 2026-10-30). tests/confirm_dialog_cases.js runs the dialog in a stand-in DOM."""
import json
import shutil
from pathlib import Path

import pytest

from tests.helpers.bounded_run import run

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_dialog_answers_with_the_click_also_without_a_close_event():
    root = Path(__file__).parents[2]
    out = run([NODE, str(root / "tests" / "confirm_dialog_cases.js")], timeout=60, cwd=root)
    assert out.returncode == 0, out.stderr
    cases = json.loads(out.stdout)
    want = {"yes": True, "no": False, "beside": False, "escape": False}
    plain = [c for c in cases if c["how"] in want]
    assert len(plain) == 8                                                   # with and without a "close" event
    for c in plain:
        assert c["got"] is want[c["how"]] and not c["open"], c
    once = next(c for c in cases if c["how"] == "once")
    assert once["got"] == [True]                                             # a late "close" with "no" changes nothing
    nxt = next(c for c in cases if c["how"] == "next")
    assert nxt["opened"] and nxt["got"] is False                             # the queue goes on
