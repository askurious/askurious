"""The figure of the DDI Lifecycle 4.0 export in the handbook (tools/ddi_figure.py, page /docs/metadata) keeps up with
the export (Ralf 2026-10-27: a change to the metadata export updates the figure too)."""
import importlib.util
from pathlib import Path

from app import ddi

ROOT = Path(__file__).resolve().parents[2]


def figure():
    spec = importlib.util.spec_from_file_location("ddi_figure", ROOT / "tools" / "ddi_figure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_kind_of_item_the_export_writes_is_in_the_figure():
    missing = set(ddi.CONTAINER_ORDER) - figure().words()
    assert not missing, f"tools/ddi_figure.py: add {sorted(missing)} to the figure and run it again"


def test_the_svg_files_are_the_script_s_output():
    f = figure()
    for lang in ("de", "en"):
        path = ROOT / "app" / "static" / "handbook" / lang / "ddi-structure.svg"
        assert path.read_text(encoding="utf-8") == f.svg(lang), f"run python tools/ddi_figure.py ({path.name}, {lang})"


def test_the_handbook_shows_the_figure_inline_in_the_page_colours(app):
    import re

    for lang, word in (("de", "Wiederverwendbare Metadaten"), ("en", "Reusable metadata")):
        html = app.test_client().get("/docs/metadata", headers={"Accept-Language": lang}).get_data(as_text=True)
        assert '<figure class="handbook-figure"><svg class="ddi-figure"' in html and word in html
        assert "InterviewerInstructionScheme" in html
        style = html.split('<svg class="ddi-figure"')[1].split("</style>")[0]
        # every colour comes from a variable of app.css (a fixed value only as its fallback), for light and dark
        assert not re.findall(r"(?:fill|stroke):#", style)
