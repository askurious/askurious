"""Marks, headings and controls of the researcher pages (UI audit 2026-10-12, package d: U11, U13, U16, U20, U21, U33,
U36, U48, U49, U55, U56, U57)."""
from pathlib import Path

from app.extensions import db
from app.models import Survey
from tests.conftest import experiments_on
from tests.interview.test_interview import make

CSS = (Path(__file__).resolve().parents[2] / "app" / "static" / "app.css").read_text(encoding="utf-8")


def _rule(selector):
    return CSS.split("\n" + selector + " {", 1)[1].split("}", 1)[0]


def test_side_panels_are_written_in_normal_case():
    """U11: "Hinzufügen", "Startprüfung", "Kennzahlen" as on the flow page, not in capitals."""
    for sel in (".sidebar h2", ".pg-head", ".quota-table th", ".fl-slot-label"):
        assert "uppercase" not in _rule(sel), sel


def test_figures_fill_their_grid_without_a_gap(app, client, survey):
    """U13: no tile alone in its row: only the durations span both columns, the rest come in pairs."""
    for url in ("/surveys/1/editor/", "/surveys/1/flow/"):
        figures = client.get(url).data.decode().split('<dl class="figures">')[1].split("</dl>")[0]
        tiles = figures.count("<div")
        assert (tiles - figures.count('class="fig-wide"')) % 2 == 0, url


def test_palette_shows_a_short_text_per_type(app, client, survey):
    """U20: a few words per type in the palette; the whole description stays in the title."""
    from app import items

    for t in items.all_types():
        if t.key != "guide_question":
            assert 0 < len(t.short) <= 40, t.key
    page = client.get("/surveys/1/editor/").data.decode()
    row = page.split('data-type="single_choice"', 1)[1].split("</div>", 1)[0]
    assert '<span class="desc">Eine Antwort aus mehreren</span>' in row and "Suchfeld mit Vorschlägen" in page


def test_loop_button_is_in_the_block_settings_and_the_row_tools_behind_a_menu_on_phones(app, client, survey):
    """U21: no "Schleife um diesen Block" in the block head; U56: "⋯" for the tools of a row on a phone."""
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "text_input"})
    page = client.get("/surveys/1/editor/").data.decode()
    head = page.split('id="block-2"', 1)[1].split('<ol class="item-list"', 1)[0]
    toolbar = head.split('<div class="toolbar">', 1)[1]
    assert "Schleife um diesen Block" not in toolbar and 'id="loop-form-2"' in toolbar
    assert "data-row-menu" in page and ".item-row.tools-open > .toolbar" in CSS


def test_structure_is_not_coloured_as_a_warning():
    """U48: conditions, the speeder slider and "Pflicht" keep out of the warning and link colours."""
    assert "warn" not in _rule(".fl-node.fl-branch") and "--struct" in _rule(".fl-node.fl-branch")
    assert "var(--accent)" in _rule(".speed-slider input")
    assert "accent" not in _rule(".mode-badge.mode-required")


def test_orcid_sign_up_says_why_it_has_a_button(app, client):
    """U16: the one form with a button says when it takes effect."""
    page = client.get("/admin/people").data.decode()
    assert "Übernehmen (gilt sofort für neue Anmeldungen)" in page


def test_interview_view_has_a_heading_and_a_neutral_core_mark(app, client):
    """U33, U55: the title is the h1; a core question says "Kern", not a red "K"."""
    code, _ = make(app, client)
    page = client.get(f"/guide/{code}/conversation").data.decode()
    assert '<h1 class="iv-title">' in page and ">Kern</span>" in page and ">K</span>" not in page
    assert "--interview" not in _rule(".iv-core-mark")


def test_diary_says_experimental_and_not_template_for_the_ticks(app, client):
    """U36: "Vorlage" is the word of the survey templates; the diary study is marked as experimental."""
    experiments_on(client, "diary")
    new = client.get("/diary/new?start=leer").data.decode()
    assert "<legend>Mit anlegen</legend>" in new and "badge-exp" in new
    dash = client.get("/dashboard/").data.decode()
    assert 'href="/diary/new">' in dash and dash.split('href="/diary/new">', 1)[1].split("</a>", 1)[0].count("experimentell") == 1
    client.post("/diary/new", data={"title": "Lernen", "language": "de"})
    with app.app_context():
        code = db.session.scalar(db.select(Survey.code).filter_by(title="Lernen"))
    assert code
    page = client.get("/surveys/1/editor/").data.decode()
    assert page.split("<h1", 1)[1].split("</div>", 1)[0].count("experimentell") == 1


def test_design_templates_put_the_note_under_the_name(app, client, survey):
    """U49: name in line 1, the note in line 2."""
    page = client.get("/surveys/1/design").data.decode()
    assert '<span class="design-preset-text"><span class="design-preset-name">' in page
    assert "flex-direction: column" in _rule(".design-preset-text")


def test_the_feedback_dialog_comes_after_the_page(app, client, survey):
    """U55: the h2 of the feedback dialog no longer stands before the page's h1."""
    page = client.get("/surveys/1/editor/").data.decode()
    assert page.index("<h1") < page.index('id="feedback-title"')


def test_the_sign_in_page_does_not_repeat_the_sign_in_in_its_head(app):
    """U57: on /auth/sign-in the head has no sign-in buttons; elsewhere it keeps them."""
    app.config['ORCID_CLIENT_ID'] = 'configured-client'
    c = app.test_client()
    page = c.get("/auth/sign-in").data.decode()
    head = page.split("<header", 1)[1].split("</header>", 1)[0]
    assert "Mit E-Mail anmelden" not in head and "Mit ORCID anmelden" not in head
    assert "Mit ORCID anmelden" in page  # on the page itself
    other = c.get("/").data.decode().split("<header", 1)[1].split("</header>", 1)[0]
    assert "Mit E-Mail anmelden" in other
