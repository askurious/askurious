"""A recording that breaks off while it records starts again by itself (Ralf 2026-10-30): the phone's recording
page (app/static/recorder.js) run in a fake browser (tests/recorder_restart_cases.js), and the same rule in the
interview view (app/static/interview.js)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
STATIC = Path(__file__).parents[2] / "app" / "static"


def _run(failing):
    script = Path(__file__).parents[1] / "recorder_restart_cases.js"
    out = subprocess.run([NODE, str(script), str(failing)], capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_phone_records_on_as_a_new_part_without_warning():
    seen = _run(0)
    assert "⚠" not in seen["during"] and "wieder verbunden" in seen["during"]
    assert seen["after"] == "nimmt auf" and "Pause" in seen["toggle"]
    assert seen["recorders"] == 2 and seen["recording"] == 1 and seen["firstStopped"]
    assert seen["pieces"] == ["1:0", "1:1", "2:0"]  # the first part's pieces once, the new part from its start


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_phone_tries_again_and_gives_up_after_four_tries():
    assert _run(2)["after"] == "nimmt auf"  # two failed tries, the third opens the microphone
    seen = _run(9)
    assert seen["opens"] == 5 and seen["recording"] == 0
    assert seen["after"].startswith("⚠ Mikrofon getrennt") and "alles übertragen" in seen["after"]


def test_interview_view_has_the_same_rule():
    js = (STATIC / "interview.js").read_text(encoding="utf-8")
    assert "RESTART_MS = [1000, 3000, 10000, 30000]" in js and "function reopen(reason)" in js
    assert "if (restartTimer) { clearTimeout(restartTimer); restartTimer = null; }" in js  # Stopp ends the waiting
    assert "stream = sharing ? mix(s, shared) : s;" in js  # the conference sound stays in the mix
