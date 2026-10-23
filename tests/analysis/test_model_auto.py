"""Audit 2026-10-11 (N15): "Automatisch anordnen" in "Modell zeichnen" (app/static/model.js, auto) counts crossings
faster and stops after BUDGET steps, so a large model no longer holds the browser for seconds; models whose columns
hold up to five variables get exactly the layout they got before (tests/model_auto_cases.js, under Node)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="Node is not installed")
def test_auto_layout_is_the_same_up_to_five_and_bounded_beyond():
    out = subprocess.run([NODE, str(ROOT / "tests" / "model_auto_cases.js"), str(ROOT / "app" / "static" / "model.js")],
                         capture_output=True, text=True, timeout=120, check=True)
    result = json.loads(out.stdout)
    assert result["compared"] > 100 and result["differ"] == 0
    assert max(result["heavy_ms"]) < 150  # about 25 ms on a desktop; before: 0.2 and 0.9 s


def test_auto_layout_has_a_budget():
    src = (ROOT / "app" / "static" / "model.js").read_text(encoding="utf-8")
    assert "var BUDGET = " in src and "spent += steps;" in src and "round < 6 && spent < BUDGET" in src
