"""Figures of the results pages as PNG (Ralf, 2026-10-16): a small icon beside every chart, map and word cloud saves it
in the browser (app/static/figure_png.js). On the results pages of every survey type and on the live screen, not on
dashboards (they keep their own button "Bild", board.js)."""

import re
import shutil
from pathlib import Path

import pytest

from tests.helpers.bounded_run import run
from tests.interview.test_interview import make as make_guide
from tests.live.test_live import make as make_live

APP = Path(__file__).resolve().parents[2] / "app"
SCRIPT = APP / "static" / "figure_png.js"
NODE = shutil.which("node")


def test_results_pages_load_the_saving_script(app, client, survey):
    assert "figure_png.js" in client.get("/surveys/1/results").data.decode()
    code, _ = make_guide(app, client)
    assert "figure_png.js" in client.get(f"/guide/{code}/interviews/analysis").data.decode()


def test_the_live_screen_marks_its_result(app, client):
    code, _, _ = make_live(app, client, "Vorlesung Bild")
    for view in ("screen", "speaker"):
        page = client.get(f"/presenter/{code}/{view}").data.decode()
        assert 'data-fig-live="Vorlesung Bild"' in page and "figure_png.js" in page


def test_dashboards_have_no_icon():
    boards = list((APP / "templates" / "boards").rglob("*.html")) + [APP / "static" / "board.js"]
    assert not [p.name for p in boards if "figure_png" in p.read_text(encoding="utf-8")]


def test_every_kind_of_figure_still_exists():
    """The script finds the figures by their classes: a renamed class would quietly lose its icon."""
    source = SCRIPT.read_text(encoding="utf-8")
    listed = re.search(r"var FIGURES = \[(.*?)\]\.join", source, re.S).group(1)
    classes = set(re.findall(r"\.([a-z][\w-]*)", listed))
    code = "\n".join(p.read_text(encoding="utf-8") for p in APP.rglob("*")
                     if p.suffix in (".html", ".js") and p.name != SCRIPT.name)
    assert not [c for c in sorted(classes) if c not in code]


def test_no_page_reload_and_the_icon_of_the_app():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "location" not in source and "submit" not in source       # saved where it is (Ralf: never a reload)
    icons = (APP / "templates" / "_icons.html").read_text(encoding="utf-8")
    path = re.search(r'"download": \'(.*?)\'', icons).group(1)
    assert path in source                                              # the same icon as everywhere


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_script_parses():
    assert run([NODE, "--check", str(SCRIPT)], timeout=60).returncode == 0


def test_one_icon_for_the_wide_and_narrow_part_worth_bars():
    """A box with the wide and the narrow form of the same bars (pw-wide / pw-narrow) gets one icon, and saving takes
    the form that is shown (Ralf, 2026-10-22: two icons, both "Als PNG speichern")."""
    js = SCRIPT.read_text(encoding="utf-8")
    assert 'outer.parentElement.classList.contains("fig-wrap")' in js
    assert "getClientRects().length" in js
