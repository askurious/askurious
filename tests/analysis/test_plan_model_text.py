"""Pre-test 2026-10-20 (15, 16) of the drawing of the model in the study plan (Hypothesen > Modell, also in a diary
study): "Modell als Text" opened empty when no hypothesis was drawn yet; now an empty model says so and names the
variables without a relation and the hypotheses not in the picture (tests/model_text_cases.js, under Node). The list
of variables says where new ones come from: a new question in the questionnaire, a scale as an ellipse."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.analysis.test_plan_model import PLAN
from tests.analysis.test_sem_survey import _survey

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="Node is not installed")
def test_the_model_as_text_is_never_empty():
    out = subprocess.run([NODE, str(ROOT / "tests" / "model_text_cases.js"), str(ROOT / "app" / "static" / "model.js")],
                         capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    result = json.loads(out.stdout)
    empty = result["empty"]
    assert empty["lines"][0].startswith("Noch keine Variablen im Modell. Ziehen Sie Variablen")
    assert empty["lines"][1] == "Nicht im Bild: H1 (eine Variable fehlt noch); H2 (eine Variable fehlt noch)"
    assert empty["outside"] == "Nicht im Bild: H1 (eine Variable fehlt noch); H2 (eine Variable fehlt noch)"
    assert result["empty_readonly"]["lines"] == ["Noch keine Variablen im Modell."]
    assert result["placed"]["lines"] == ["H1: Skala x ↔ Skala y (+)", "Ohne Beziehung: Wie fühlen Sie sich gerade?",
                                         "Nicht im Bild: H2 (eine Variable fehlt noch)"]


def test_the_list_of_variables_says_where_new_ones_come_from(app, client, survey):
    _survey(app, client, n=10, plan=False)
    page = client.get(PLAN).data.decode()
    note = page.split('class="small muted pm-vars-note"', 1)[1].split("</p>", 1)[0]
    assert "Die Variablen sind die Fragen des Fragebogens" in note and 'href="/surveys/1/editor/"' in note
    assert "Ellipse (latente Variable)" in note
