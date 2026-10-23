"""Rules that keep the participant pages accessible (accessibility concept 2026-10-06, D1, D3, D6, D8, D9; E6: own
rules in the tests, no axe-core). One test renders every element type with a message of a failed "Next" and checks:
no message in a <label>, every field with a name, every reference (aria-labelledby, aria-describedby, label for) to
an element that is there, every display the script fills with aria-live. Another computes the contrast of the edges
of controls in the standard look, in every built-in design and in designs of one's own."""
from tests.static_sources import participant_js

import re
from html.parser import HTMLParser

import pytest

from app.extensions import db
from app.models import Survey
from tests.conftest import add, experiments_on, fill_questions

ERROR = "FEHLERTEXT-A11Y"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# displays the script fills while the person answers: a screen reader hears them only with aria-live (or a role
# that implies it). Timed tasks (assign, discounting, IAT) and the recording time are package D7. Marking an image
# announces through its own status line (data-mark-status, role="status", D2); its count stays silent, so a screen
# reader does not hear every spot twice. The constant sum speaks through its own line once the sliders rest
# (data-csum-say), its visible total stays silent (UI/UX audit 2026-10-16, 100).
LIVE_MARKERS = ("data-csum-say", "data-hl-count", "data-mark-status", "data-map-count", "data-rank-said", "data-acbc-total")


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, dict(attrs), parent, [], ""

    def all(self):
        yield self
        for c in self.children:
            yield from c.all()

    def full_text(self):
        return self.text + "".join(c.full_text() for c in self.children)

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent


class Tree(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {}, None)
        self.cur = self.root
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(Node("#text", {}, self.cur))
        self.cur.children[-1].text = data


def fields(tree):
    """The controls a person operates: no hidden inputs, nothing taken out of the page (aria-hidden, hidden)."""
    for n in tree.root.all():
        if n.tag not in ("input", "select", "textarea") or n.attrs.get("type") == "hidden":
            continue
        if any(a.attrs.get("aria-hidden") == "true" or "hidden" in a.attrs for a in [n, *n.ancestors()]):
            continue
        yield n


def has_name(n, ids, labels_for):
    if (n.attrs.get("aria-label") or "").strip():
        return True
    if n.attrs.get("aria-labelledby"):
        return all(i in ids for i in n.attrs["aria-labelledby"].split())
    if n.attrs.get("id") in labels_for:
        return True
    return any(a.tag == "label" and a.full_text().strip() for a in n.ancestors()) or (n.attrs.get("title") or "").strip() != ""


def all_items(app, client):
    """Every element type once in block 2 (the experimental ones too), each with a question."""
    from app import experimental, items

    experiments_on(client, *[key for key, *_ in experimental.FEATURES])
    keys = [t.key for t in items.all_types() if t.key not in ("consent", "page_break") and not getattr(t, "interview_only", False)]
    for key in keys:
        add(client, key)
    fill_questions(app)
    from app.models import Item

    with app.app_context():  # options as a researcher would name them (new ones start without a text)
        for item in db.session.scalars(db.select(Item)):
            options = (item.config or {}).get("options")
            if isinstance(options, list) and options and all(isinstance(o, dict) and "id" in o for o in options):
                texts = dict(item.texts or {})
                texts["de"] = {**texts.get("de", {}), "options": {o["id"]: f"Antwort {n}" for n, o in enumerate(options, 1)}}
                item.texts = texts
        db.session.commit()
    return keys


def render(app, item_id, errors=True):
    from flask import render_template_string

    from app.models import Item

    with app.test_request_context("/surveys/1/editor/preview"):
        item = db.session.get(Item, item_id)
        return render_template_string(
            '{% include "participant/_item.html" %}', item=item, survey=item.survey, lang="de", answers={},
            errors={item.id: ERROR} if errors else {}, variables={}, preview=True, all_rows=True)


def test_every_element_type_follows_the_rules(app, client, survey):
    """E6: per element type, rendered with the message of a failed "Next"."""
    from app.models import Item

    keys = all_items(app, client)
    with app.app_context():
        rendered = {i.type: i.id for i in db.session.scalars(db.select(Item).filter(Item.id > 1))}
    assert set(keys) <= set(rendered), set(keys) - set(rendered)
    problems = []
    for key in keys:
        html = render(app, rendered[key])
        tree = Tree(html)
        ids = {n.attrs["id"] for n in tree.root.all() if n.attrs.get("id")}
        labels_for = {n.attrs.get("for") for n in tree.root.all() if n.tag == "label" and n.attrs.get("for")}
        # 1 the message of a failed "Next" is no part of a field's name
        for n in tree.root.all():
            if n.tag == "label" and ERROR in n.full_text():
                problems.append(f"{key}: Fehlermeldung steht in einem <label>")
        # 2 every field has a name
        for n in fields(tree):
            if not has_name(n, ids, labels_for):
                problems.append(f"{key}: Feld ohne Namen: <{n.tag} {n.attrs}>")
        # 3 every reference points to an element on the page
        for n in tree.root.all():
            for attr in ("aria-labelledby", "aria-describedby", "aria-controls"):
                for ref in (n.attrs.get(attr) or "").split():
                    if ref not in ids:
                        problems.append(f"{key}: {attr}=\"{ref}\" ohne Element")
            if n.tag == "label" and n.attrs.get("for") and n.attrs["for"] not in ids:
                problems.append(f"{key}: <label for=\"{n.attrs['for']}\"> ohne Feld")
        # 4 the displays the script fills are announced
        for n in tree.root.all():
            if any(m in n.attrs for m in LIVE_MARKERS) and not (
                    n.attrs.get("aria-live") or n.attrs.get("role") in ("status", "alert", "log")
                    or any(a.attrs.get("aria-live") for a in n.ancestors())):
                problems.append(f"{key}: Live-Anzeige ohne aria-live: <{n.tag} {n.attrs}>")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("multiline", [False, True])
def test_text_question_names_the_field_by_the_question_alone(app, client, survey, multiline):
    """D3: only the question is in the <label>; message, valid range and units describe the field, written by the
    server (no JavaScript needed), and aria-invalid marks the error."""
    add(client, "text_input")                                                           # 2
    client.post("/surveys/1/editor/items/2", data={"code": "age", "question": "Wie alt sind Sie?", "validation": "integer",
                                                   "min": "18", "max": "99", "suffix": "Jahre", "multiline": "1" if multiline else ""})
    html = render(app, 2)
    tree = Tree(html)
    label = next(n for n in tree.root.all() if n.tag == "label")
    assert label.attrs.get("for") == "t2" and "Wie alt sind Sie?" in label.full_text()
    assert ERROR not in label.full_text() and "18" not in label.full_text() and "Jahre" not in label.full_text()
    field = next(fields(tree))
    assert field.attrs["id"] == "t2" and field.attrs.get("aria-invalid") == "true"
    described = field.attrs["aria-describedby"].split()
    assert described[0] == "msg2" and "suf2" in described
    # without an error: no aria-invalid, no reference to a message that is not there
    plain = next(fields(Tree(render(app, 2, errors=False))))
    assert "aria-invalid" not in plain.attrs and "msg2" not in (plain.attrs.get("aria-describedby") or "")


def test_postal_code_names_the_field_by_the_question_alone(app, client, survey):
    add(client, "postal_code")                                                          # 2
    fill_questions(app)
    tree = Tree(render(app, 2))
    label = next(n for n in tree.root.all() if n.tag == "label")
    field = next(fields(tree))
    assert label.attrs["for"] == field.attrs["id"] and ERROR not in label.full_text()
    assert field.attrs["aria-describedby"] == "msg2" and field.attrs["aria-invalid"] == "true"


def test_script_adds_the_message_to_what_the_server_wrote():
    """D3: participant.js no longer replaces aria-describedby (hints and units would be lost)."""
    from pathlib import Path

    js = participant_js()
    assert 'input.setAttribute("aria-describedby", message.id)' not in js
    assert "[message.id].concat(ids)" in js


def test_choice_tasks_name_each_option_after_its_column(app, client, survey):
    """D6: every radio button of a choice task is "Option n auswählen" and is described by the cells of its column."""
    from tests.analysis.test_cbc_results import setup

    setup(client, tasks=4, versions=2)                                                  # item 2, three options
    html = render(app, 2, errors=False)
    assert "Versuchsplan noch nicht erzeugt" not in html
    tree = Tree(html)
    ids = {n.attrs["id"] for n in tree.root.all() if n.attrs.get("id")}
    radios = [n for n in fields(tree) if n.attrs.get("type") == "radio" and n.attrs.get("value") != "0"]
    assert radios
    for r in radios:
        number = re.fullmatch(r"Option (\d) auswählen", r.attrs["aria-label"]).group(1)
        head = next(n for n in tree.root.all() if n.tag == "th" and n.full_text().strip() == f"Option {number}")
        cells = r.attrs["aria-describedby"].split()
        assert cells and all(c in ids and c.startswith(head.attrs["id"] + "-") for c in cells)


def test_acbc_groups_carry_the_number_of_their_column():
    """D6: the screening groups of the ACBC were named "Variante <concept number>", the header "Variante <place>"."""
    from pathlib import Path

    t = (Path(__file__).resolve().parents[2] / "app" / "templates" / "items" / "acbc" / "participant.html").read_text(encoding="utf-8")
    assert 'aria-label="{{ alt_label }} {{ a }}"' not in t
    assert 'aria-labelledby="cj{{ item.id }}-{{ a }}"' in t and "ui('%s auswählen', 'Choose %s')" in t


def test_side_by_side_rows_and_cells_are_named_by_row_and_column(app, client, survey):
    """Side-by-side matrix: every row is a fieldset with the row as legend, every choice cell an inner fieldset with the
    question of its column as legend, drop-down, number and text a <label> with the question; the visible head is
    hidden from screen readers (they hear "Bus, Wie zufrieden?, eher unzufrieden")."""
    from tests.items.test_side_by_side import setup

    item_id = setup(client)
    tree = Tree(render(app, item_id, errors=False))
    rows = [n for n in tree.root.all() if n.tag == "fieldset" and "p-sbs-row" in n.attrs.get("class", "").split()]
    legends = [next(c for c in r.children if c.tag == "legend") for r in rows]  # "1 von 3" (a block of its own), the row
    assert [l.children[-1].full_text().strip() for l in legends] == ["Bus", "Bahn", "Fahrrad"]
    head = next(n for n in tree.root.all() if "p-sbs-head" in n.attrs.get("class", "").split())
    assert head.attrs.get("aria-hidden") == "true"
    for row in rows:
        cells = [n for n in row.children if n.tag in ("fieldset", "div")]
        assert len(cells) == 5
        for cell in cells:
            if cell.tag == "fieldset":
                legend = next(c for c in cell.children if c.tag == "legend")
                # a required column says so to screen readers too (items/_required.html; UI/UX audit 2026-10-16, 94)
                assert legend.full_text().strip() in ("Genutzt? * (Pflichtfrage)", "Wie zufrieden?")
            else:
                label = next(c for c in cell.all() if c.tag == "label")
                field = next(c for c in cell.all() if c.tag in ("input", "select"))
                assert label.attrs["for"] == field.attrs["id"] and label.full_text().strip()


def test_slider_ends_describe_the_slider(app, client, survey):
    """D8: the words at the ends of a slider reach screen readers (the visible ones are hidden from them)."""
    add(client, "slider")                                                               # 2
    client.post("/surveys/1/editor/items/2", data={"code": "s", "question": "Wie sehr?", "left": "gar nicht", "right": "sehr",
                                                   "min": "0", "max": "100", "step": "1",
                                                   "stmt_id": "", "stmt_code": "s1", "stmt_text": ""})
    tree = Tree(render(app, 2, errors=False))
    rng = next(n for n in tree.root.all() if n.tag == "input" and n.attrs.get("type") == "range")
    target = next(n for n in tree.root.all() if n.attrs.get("id") == rng.attrs["aria-describedby"])
    assert target.full_text() == "von gar nicht bis sehr"


def test_rating_slider_ends_describe_the_slider(app, client, survey):
    add(client, "rating")                                                               # 2
    client.post("/surveys/1/editor/items/2", data={"code": "r", "question": "Wie gut?", "style": "slider", "left": "schlecht",
                                                   "right": "gut", "min": "0", "max": "10", "step": "1", "points": "5"})
    html = render(app, 2, errors=False)
    if 'type="range"' not in html:
        pytest.skip("rating not shown as a slider")
    assert 'aria-describedby="ep2"' in html and ">von schlecht bis gut</span>" in html


def test_geolocation_can_put_the_point_in_the_centre(app, client, survey):
    """D8: the point of "Standort" moves without dragging."""
    experiments_on(client, "geolocation")
    add(client, "geolocation")                                                          # 2
    from app.models import Item

    with app.app_context():
        item = db.session.get(Item, 2)
        if item is None or item.type != "geolocation":
            pytest.skip("geolocation not available")
        item.config = {**item.config, "correct": True}
        db.session.commit()
    html = render(app, 2, errors=False)
    assert "data-geo-center" in html and "Punkt in die Kartenmitte" in html


def test_image_without_alt_text_is_a_hint_in_the_editor(app, client, survey):
    """D8: the check of the draft and the element's form point out an image without an alternative text."""
    from app.models import Item

    add(client, "image")                                                                # 2
    empty = client.get("/surveys/1/editor/items/2?lang=de").data.decode()
    assert "data-alt-missing" in empty and 'aria-describedby="alt-missing"' in empty
    with app.app_context():
        item = db.session.get(Item, 2)
        hints = item.kind.check_warnings(item)
        assert hints and "Alternativtext" in hints[0] and "DE, EN" in hints[0]
        texts = dict(item.texts or {})
        for lang in ("de", "en"):
            texts[lang] = {**texts.get(lang, {}), "alt": "Ein Roboter"}
        item.texts = texts
        db.session.commit()
        assert item.kind.check_warnings(db.session.get(Item, 2)) == []
    editor = client.get("/surveys/1/editor/items/2?lang=de").data.decode()
    assert "data-alt-missing" not in editor and "aria-describedby=\"alt-missing\"" not in editor


def test_feedback_says_the_own_position_in_words(app, client, survey):
    """D8: the crowd and the 2-D map say where the person stands, not only by colour."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app" / "templates" / "items"
    assert "crowd.own_text" in (root / "sample_crowd" / "participant.html").read_text(encoding="utf-8")
    assert "scatter.own_text" in (root / "sample_map" / "participant.html").read_text(encoding="utf-8")
    from app.items.sample_crowd import SampleCrowd

    kind = SampleCrowd()
    scale = {"bins": ["nie", "", "oft"], "lo": 1}

    class Src:
        type = "likert"

    assert kind.own_text(Src, scale, {"s1": 1}, "de") == "nie"
    assert kind.own_text(Src, scale, {"s1": 2}, "de") == "2 von 3"
    assert kind.own_text(Src, scale, {"s1": 2}, "en") == "2 of 3"


# --- D1: edges of controls ---------------------------------------------------------------------------------------

EDGES = ("--control-edge", "--track-edge", "--focus-ring")
OWN_COLOURS = [  # designs of one's own: light, tinted, mid-grey, dark, saturated
    {"accent": "#2a5db0", "background": "#ffffff", "text": "#1d1d1b", "surface": "#ffffff"},
    {"accent": "#a4442a", "background": "#f4eee2", "text": "#2a241c", "surface": "#fffcf5"},
    {"accent": "#ffd400", "background": "#777777", "text": "#ffffff", "surface": "#6a6a6a"},
    {"accent": "#7ea6e6", "background": "#000000", "text": "#ffffff", "surface": "#111111"},
    {"accent": "#ffffff", "background": "#003366", "text": "#ffffff", "surface": "#0a3d73"},
    {"accent": "#c2185b", "background": "#fde7ef", "text": "#3b0a1d", "surface": "#ffffff"},
    {"accent": "#1f6f5c", "background": "#e6efe9", "text": "#17302a", "surface": "#d9e6dd"},
]


def test_edges_of_controls_reach_3_to_1_in_every_design():
    """WCAG 1.4.11 (E1): the edge of input fields, of a slider and the focus ring have 3:1 on the page and the edge of
    a field also on the field's own surface, in the standard look, every built-in design and designs of one's own."""
    from app import theme

    looks = [("Standard", theme.tokens(theme.standard()))]
    looks += [(key, theme.tokens({**theme.standard(), "preset": key, "colors": dict(b["colors"])})) for key, b in theme.BUILTIN.items()]
    looks += [(f"eigen {c['background']}", theme.tokens({**theme.standard(), "preset": "custom", "colors": c})) for c in OWN_COLOURS]
    low = []
    for name, t in looks:
        for token in EDGES:
            ratio = theme.contrast(t[token], t["--bg"])
            if ratio < 3.0:
                low.append(f"{name}: {token} {t[token]} auf {t['--bg']} = {ratio:.2f}")
        if theme.contrast(t["--control-edge"], t["--surface"]) < 3.0:
            low.append(f"{name}: --control-edge auf --surface = {theme.contrast(t['--control-edge'], t['--surface']):.2f}")
    assert not low, "\n".join(low)
    assert theme.standard_tokens()["--control-edge"] == "#858379"


def test_fields_use_the_strong_edge_and_answer_cards_keep_the_light_one():
    """D1/E2: the edges of fields one types in or picks from use --control-edge; the cards of answer options keep
    --border (the radio button or tick in them is the control)."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / "app" / "static" / "participant.css").read_text(encoding="utf-8")

    def rule(selector):
        return re.search(re.escape(selector) + r" \{([^}]*)\}", css).group(1)

    for sel in (".p-text input, .p-text textarea", ".p-date select", ".p-csum-num input", ".p-number span",
                ".p-map-search input", ".p-assoc-word input"):
        assert "var(--control-edge)" in rule(sel), sel
    assert "var(--border)" in rule(".p-option")
    assert "box-shadow: inset 0 0 0 1px var(--track-edge)" in rule(".p-range::-moz-range-track")


def test_design_check_lists_the_edge_of_input_fields():
    from app import theme

    rows = {c["key"]: c for c in theme.checks(theme.standard())}
    assert rows["control-edge"]["ok"] and rows["control-edge"]["need"] == 3.0
    weak = theme.checks({**theme.standard(), "preset": "custom", "colors": OWN_COLOURS[0]})
    assert all(c["ok"] for c in weak if c["key"] == "control-edge")


# --- D9: accessibility statement ---------------------------------------------------------------------------------

def test_accessibility_statement_is_linked_from_every_participant_page(app, client, survey):
    """The footer has one link, "Datenschutz & Impressum"; the privacy page links the statement (Philipp,
    2026-10-13)."""
    from app.models import Survey

    with app.app_context():
        uid = db.session.get(Survey, 1).public_key
    page = client.get("/surveys/1/editor/preview", follow_redirects=True).data.decode()
    assert f"/s/{uid}/privacy" in page and "Datenschutz &amp; Impressum" in page
    assert f"/s/{uid}/accessibility" not in page and "p-footer-a11y" not in page
    privacy = client.get(f"/s/{uid}/privacy").data.decode()
    assert f"/s/{uid}/accessibility" in privacy and "Erklärung zur Barrierefreiheit" in privacy
    # the operator's law and enforcement body come from the installation (tests/privacy/test_legal_pages_operator.py)
    for lang, title in (("de", "Erklärung zur Barrierefreiheit"), ("en", "Accessibility statement")):
        html = client.get(f"/s/{uid}/accessibility?lang={lang}").data.decode()
        assert title in html and "WCAG" in html and "<h1" in html and "BITV NRW" not in html


def test_the_platform_accessibility_page_is_linked_from_the_footer_and_the_landing_page(app, client):
    """The researchers' footer links /accessibility, a page of its own, which points to the statement of each survey and
    says that the researchers' pages are not assessed yet. The landing page ("Erheben": "Barrierefrei, wo möglich")
    links it directly; "Impressum & Datenschutz" has a section "Barrierefreiheit" that links it too."""
    landing = client.get("/").data.decode()
    assert 'href="/accessibility"' in landing and "Barrierefrei, wo möglich" in landing
    imprint = client.get("/imprint").data.decode()
    assert 'id="barrierefreiheit"' in imprint and 'href="/accessibility"' in imprint
    html = client.get("/accessibility").data.decode()
    assert "(WCAG) 2.1" in html and "Seiten für Forschende" in html and "Datenschutz &amp; Impressum" in html
    assert 'href="/accessibility"' in html  # the footer of the researchers' pages


def test_marked_image_without_alt_text_is_a_hint_in_the_start_check(app, client, survey):
    """UI/UX audit 2026-10-17, W-H02: "Bild markieren" (points, lines, hot spot) gets the same hint for a missing
    alternative text in the check before the start as the plain image."""
    from app import design
    from app.models import Item

    add(client, "image_mark")                                                           # 2
    with app.app_context():
        item = db.session.get(Item, 2)
        for mode in ("points", "areas"):
            item.config = {**item.config, "mode": mode}
            hints = item.kind.check_warnings(item)
            assert hints and "Alternativtext" in hints[0] and "DE, EN" in hints[0]
        db.session.commit()
        found = [f.message for f in design.check(db.session.get(Survey, 1)) if "Alternativtext" in str(f.message)]
        assert found
        texts = dict(item.texts or {})
        for lang in ("de", "en"):
            texts[lang] = {**texts.get(lang, {}), "alt": "Ein Stadtplan"}
        item.texts = texts
        db.session.commit()
        assert not [w for w in item.kind.check_warnings(db.session.get(Item, 2)) if "Alternativtext" in w]


def test_narrow_screen_with_large_text_keeps_everything_inside(app):
    """UI/UX audit 2026-10-17, W-H01, measured live at 320 px with 200 % text in German and Arabic: a long word, the
    2-D field with long axis texts, the three piles of the Q-sort and the page list of the preview ran past the clipped
    edge of the page (body.participant overflow-x: clip). The rules that keep them inside stay."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / "app" / "static" / "participant.css").read_text(encoding="utf-8")

    def rule(selector):
        return css.split("\n" + selector + " {", 1)[1].split("}", 1)[0]

    assert "overflow-wrap: break-word" in rule(".p-container")
    assert "min-width: 0" in rule(".p-option > span")
    assert "minmax(min(100%, 4.5em), 1fr)" in rule(".p-sort-pre-btns")
    assert "min-width: min(340px, 92vw)" in rule(".p-jump-list")
    assert "fit-content(30%) minmax(0, 1fr)" in rule(".p-pos-frame")
    assert "container-type: inline-size" in rule(".p-pos")
    assert "@container (max-width: 15em)" in css and "max-width: 100%" in rule(".p-pos-chip")
