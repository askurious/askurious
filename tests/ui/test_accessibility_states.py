"""States that must stay readable and reachable (UI/UX audit 2026-10-24, package C): a placed element of the 2D
positioning and the smileys not chosen keep their full contrast in every built-in design, fields without a visible
name get one, a message of the server is taken off a question again once it is answered, the live strip of the
field page has a text for screen readers, and the focus does not fall to the top of the page when the element that
had it goes away."""
import re
from pathlib import Path

from app import theme
from app.extensions import db
from tests.conftest import add
from tests.static_sources import participant_js

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"
TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates"


def _css():
    return (STATIC / "participant.css").read_text(encoding="utf-8")


def _rule(css, selector):
    return css.split("\n" + selector + " {", 1)[1].split("}", 1)[0]


def test_placed_chips_and_unchosen_faces_are_not_veiled():
    """ACC-01: no opacity over text or the features of a face; the state shows by edge, tick and word."""
    css = _css()
    placed = _rule(css, ".p-pos-chip.is-placed")
    assert "opacity" not in placed and "dashed var(--accent)" in placed
    assert ".p-pos-chip.is-placed .p-pos-placed { display: inline; }" in css
    assert "color: var(--fg)" in _rule(css, ".p-pos-placed")
    assert ".p-face:not(:has(input:checked)) svg { opacity" not in css
    assert ".p-face:has(input:checked) { box-shadow: inset 0 0 0 2px var(--chosen-edge, var(--accent)); }" in css


def test_placed_chip_text_and_edge_reach_their_contrast_in_every_design():
    """ACC-01: the text of a chip (also chosen, on the soft accent) 4.5:1, its dashed edge 3:1, in every built-in theme."""
    weak = []
    for key, entry in theme.BUILTIN.items():
        tok = theme.tokens({**theme.standard(), "preset": key, "colors": dict(entry["colors"])})
        for text, area, least in (("--fg", "--bg", theme.TEXT_MIN), ("--fg", "--accent-soft", theme.TEXT_MIN),
                                  ("--accent", "--bg", theme.PART_MIN)):
            if theme.contrast(tok[text], tok[area]) < least:
                weak.append((key, text, area, round(theme.contrast(tok[text], tok[area]), 2)))
    assert not weak, weak


def test_chip_says_placed_in_its_name_and_the_marker_takes_the_bare_name(app, client, survey):
    """ACC-01: the word sits inside the button (its accessible name once shown); the marker and its label read the
    element's own name, without that word."""
    from flask import render_template_string

    from app.models import Item

    add(client, "position2d")                                                           # 2
    for lang, word in (("de", "platziert"), ("en", "placed"), ("fr", "placé")):
        with app.test_request_context("/surveys/1/editor/preview"):
            item = db.session.get(Item, 2)
            html = render_template_string('{% include "participant/_item.html" %}', item=item, survey=item.survey,
                                          lang=lang, answers={}, errors={}, variables={}, preview=True, all_rows=True)
        assert 'class="p-pos-chip-name" data-pos-name>' in html
        assert f'<span class="p-pos-placed">{word}</span></button>' in html
    js = participant_js()
    assert "chip(code).textContent" not in js
    assert 'm.querySelector(".p-pos-name").textContent = name(code);' in js


def test_highlight_colour_select_and_vignette_draft_have_a_name(app, client, survey):
    """ACC-02: the colour of each category and the AI draft of the vignette text are named."""
    add(client, "highlight")                                                            # 2
    page = client.get("/surveys/1/editor/items/2?lang=de").data.decode()
    assert re.search(r'name="cat_color"\s+aria-label="Farbe der Kategorie 1"', page)
    vignettes = (TEMPLATES / "editor" / "vignettes.html").read_text(encoding="utf-8")
    assert ('<label>{{ de_en("Vignettentext prüfen und bearbeiten", "Check and edit the vignette text") }}\n'
            '        <textarea name="text"') in vignettes


def test_answered_question_loses_its_message_link_and_invalid_mark():
    """WEB-P01: mark() takes its message ids off aria-describedby (the hints stay), removes aria-invalid, and adds a
    new message to the hints instead of replacing them."""
    js = (STATIC / "participant.js").read_text(encoding="utf-8")
    body = js.split("  function mark(p, answer) {", 1)[1].split("\n  function show(target)", 1)[0]
    assert "gone.push(el.id); el.remove();" in body
    assert "gone.indexOf(x) < 0" in body and 'input.removeAttribute("aria-describedby")' in body
    assert 'input.removeAttribute("aria-invalid")' in body
    assert 'input.setAttribute("aria-describedby", msg.id)' not in body
    assert "[msg.id].concat(ids)" in body


def test_live_strip_has_a_text_for_screen_readers():
    """WEB-03: a polite live region beside the drawing, written only when its text changed."""
    status = (TEMPLATES / "surveys" / "_status.html").read_text(encoding="utf-8")
    assert '<p class="visually-hidden" data-live-summary aria-live="polite"></p>' in status
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    part = js.split('var box = document.querySelector("[data-live-strip]");', 1)[1].split("\n})();", 1)[0]
    assert "summary(data);" in part and "if (said.textContent !== text) said.textContent = text;" in part
    assert '"Nobody is in the survey right now."' in part and "thirds[Math.min(2, Math.floor(" in part


def test_focus_moves_on_when_its_element_goes():
    """ACC-H01: a marker put back, the hidden "remove all" button and a rebuilt list of spots hand the focus on."""
    js = (STATIC / "participant.js").read_text(encoding="utf-8")
    unset = js.split("    function unset(code) {", 1)[1].split("\n    }", 1)[0]
    assert "markers[code].contains(document.activeElement)" in unset and "if (back) chip(code).focus();" in unset
    hl = (STATIC / "participant-highlight.js").read_text(encoding="utf-8")
    clear = hl.split('clear.addEventListener("click", function () {', 1)[1].split("\n    });", 1)[0]
    assert "to.focus()" in clear
    mark = (STATIC / "participant-mark.js").read_text(encoding="utf-8")
    assert "if (inside && (!had.isConnected || had.disabled)) refocus(spot);" in mark
    assert "stage.focus();" in mark.split("function refocus(k) {", 1)[1]
