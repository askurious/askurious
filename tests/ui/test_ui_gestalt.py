"""Gestalt and form layout of the researchers' pages (UI/UX audit of 2026-10-04, package I, R87 to R94)."""
from pathlib import Path

from tests.conftest import add

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"


def test_one_autosave_line_per_page_beside_the_arrows(client, survey):
    """R89, for the pages of a survey replaced by S2 (UI/UX audit 2026-10-07): the line stands in the head with ↶ ↷;
    app.js quiets the lines of the sections (tests/ui/test_ui8_s2_head.py)."""
    html = client.get("/surveys/1").data.decode()
    assert "data-autosave-head" in html and html.count("data-autosave-bar") > 1
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'classList.add("is-quiet")' in js
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".autosave-bar.is-quiet:not(:has(" in css


def test_results_tables_and_figures_centred():
    """Tables and figures stand in the middle of their panel (Ralf, 2026-10-03; again 2026-10-05 after R90)."""
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".results-page .res-scroll { width: fit-content; max-width: 100%; margin-inline: auto; }" in css
    assert ".results-page .sd-profile { margin-inline: auto; }" in css


def test_quota_rows_delete_with_a_bin_and_lists_search_while_typing(client, survey):
    """R91: a Teilquote is deleted with a bin named after it, apart from the × of a condition; R94: the long lists
    search while typing."""
    tpl = (Path(__file__).resolve().parents[2] / "app" / "templates")
    quotas = (tpl / "editor" / "quotas.html").read_text(encoding="utf-8")
    assert """aria-label="{{ de_en('Teilquote „{name}“ löschen', 'Delete sub-quota “{name}”', name=cell.name) }}">{{ icon("trash", 16) }}""" in quotas
    assert "data-live-search" in (tpl / "_listing.html").read_text(encoding="utf-8")
    html = client.get("/admin/people").data.decode()
    assert "data-live-search" in html


def test_rare_row_moves_wait_for_the_pointer(client, survey):
    """R92: into another block (questionnaire) and into or out of a group (flow) only on pointing or focus."""
    add(client, "single_choice")
    html = client.get("/surveys/1/editor/").data.decode()
    assert "row-tool-more" in html
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".row-tool-more { visibility: hidden; }" in css
