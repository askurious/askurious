"""UI/UX audit 2026-10-09, package C: participants, preview, languages and right to left (X33 to X51 without X32,
and S20: one way to sort and point on the phone)."""
from tests.static_sources import participant_js

import os
import re

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CSS = open(os.path.join(ROOT, "app/static/participant.css"), encoding="utf-8").read()
JS = participant_js()
E = "/surveys/1/editor"


def _template(name):
    return open(os.path.join(ROOT, "app/templates", name), encoding="utf-8").read()


def _start(app, client):
    with app.app_context():
        empty = len([i for b in db.session.get(Survey, 1).blocks for i in b.items]) < 2
    if empty:  # a question besides the consent, else the survey cannot start
        add(client, "text_input")
        with app.app_context():
            last = max(i.id for i in db.session.scalars(db.select(Item)))
        client.post(f"{E}/items/{last}", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


# --- X33: the comments of the preview never lie on a page grown to 1000 px ----------------------------------------


def test_x33_comments_beside_a_wide_page_open_from_the_button():
    rule = CSS[CSS.index("@media (min-width: 1280px) and (max-width: 1719px)"):]
    rule = rule[:rule.index("\n}\n")]
    assert "body:has(.p-sbs-wide) .p-comments:not(.is-stage) .p-comments-toggle { display: inline-block; }" in rule
    assert "body:has(.p-sbs-wide) .p-comments:not(.is-stage) .p-comments-panel { display: none;" in rule
    assert "body:has(.p-sbs-wide) .p-comments.is-open:not(.is-stage) .p-comments-panel { display: block; }" in rule


# --- side-by-side matrix: X34, X35, X50 ----------------------------------------------------------------------------


def test_x34_a_cell_with_a_condition_waits_without_a_word_before_the_answer():
    assert "p-sbs-cell-wait" in JS and 'cell.classList.toggle("p-sbs-cell-wait", wait)' in JS
    assert "var wait = got === null, off = !wait &&" in JS  # "entfällt" only once an answer rules it out
    assert ".p-sbs-cell-wait { opacity: 0.4; }" in CSS


def test_x35_messages_name_the_cells_and_only_they_are_marked(app, client, survey):
    from tests.items.test_side_by_side import setup

    fill_consent(client)
    item_id = setup(client, col_when_c4="c1:1")
    client.post(f"{E}/items/{item_id}", data=MultiDict(list(
        __import__("tests.items.test_side_by_side", fromlist=["editor_form"]).editor_form(item_id, col_when_c4="c1:1").items(multi=True))))
    uid = _start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.get(f"/s/{uid}")
    html = p.post(f"/s/{uid}", data={"nav": "next", f"i{item_id}_r1_c1": "1", f"i{item_id}_r1_c4": "zwei"}).data.decode()
    # the message names the bad cell and the open ones, with the number as before
    assert "Bitte geben Sie eine Zahl ein. („Kosten pro Monat“ bei Bus)" in html
    assert "noch offen: 2 – „Genutzt?“ in 2 Zeilen" in html
    # the number field carries its own message; the cell is marked, not the whole row
    bad = f"i{item_id}_r1_c4"
    assert re.search(rf'<input id="{bad}"[^>]*aria-invalid="true" aria-describedby="m{bad}"', html)
    assert f'<span class="p-sbs-cellmsg" id="m{bad}">Bitte geben Sie eine Zahl ein.</span>' in html
    assert 'class="p-sbs-row p-missing' not in html and "p-sbs-open" in html
    # a cell whose condition does not hold is never marked: Bahn and Fahrrad have no "Kosten" before "Genutzt? = ja"
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.kind.cell_marks(item, {}, "de") == {"c1": ""}
        assert set(item.kind.cell_marks(item, {}, "de", every=True)) == {"c1", "c2", "c3", "c5"}  # the nudge: every empty cell
        assert item.kind.cell_marks(item, {"c1": 1, "c4": "viel"}, "de")["c4"].startswith("Bitte geben Sie")


def test_x50_scale_heads_hyphenate_and_get_64_px_a_point(app, client, survey):
    from tests.items.test_side_by_side import setup

    item_id = setup(client)
    with app.app_context():
        item = db.session.get(Item, item_id)
        widths = {c["id"]: c["width"] for c in item.kind.layout(item, "de")["columns"]}
    assert widths["c2"] == 64 * 6  # five points and "weiß nicht"
    assert "overflow-wrap: break-word; }  /* hyphenated, not cut anywhere (X50) */" in CSS


# --- X36: drill-down after an error -------------------------------------------------------------------------------


def test_x36_the_chain_sent_stays_and_only_the_open_list_is_marked(app, client, survey):
    from tests.items.test_drill_down import ids, setup_drill

    fill_consent(client)
    setup_drill(client)
    uid = _start(app, client)
    with app.app_context():
        golf = ids(db.session.get(Item, 2), "VW", "Golf")
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    html = p.post(f"/s/{uid}", data={"nav": "next", "i2_0": golf[0], "i2_1": golf[1], "i2_2": ""}).data.decode()
    assert "Bitte wählen Sie noch etwas bei „Baujahr“." in html
    assert f'<option value="{golf[1]}" selected' in html  # the model chosen is sent back selected
    assert html.count("p-drill-level p-missing") == 1 and 'p-drill-level p-missing" data-drill-level="2"' in html
    # the script reads the choices before it takes the groups out of the lists (it lost the model before)
    assert "var start = selects.map(function (s) { return s.value; });" in JS and "from(1, true);" in JS


# --- X37: the search field says when typed text is no suggestion --------------------------------------------------


def test_x37_text_without_a_pick_is_said_and_asked_once():
    block = JS[JS.index('document.querySelectorAll("[data-search]")'):JS.index("// Video / audio stimulus")]
    assert "hint(unpicked())" in block and "asked = true" in block and "e.preventDefault();" in block
    assert 'by.name === "nav" && by.value === "back"' in block  # "Back" is never held
    assert "input.dir = \"auto\"" in block


# --- S20, X38, X39, X49: sorting on the phone ---------------------------------------------------------------------


def test_s20_nothing_is_picked_and_the_groups_stand_at_the_bottom(app, client, survey):
    block = open(os.path.join(ROOT, "app/static/participant-sort.js"), encoding="utf-8").read()
    assert "select(nextOnStack());" not in block and "select(nextOnStack())" not in block.split("function after")[0]
    assert "if (selected) { place(selected, section)" not in block  # "+ Neuer Stapel" lays no card (X38)
    assert 'uiT("Nächste Karte: {card}", "Next card: {card}"' in block
    assert ".p-sort-bar:not([hidden]) { display: block; position: sticky; bottom: 0;" in CSS
    fill_consent(client)
    add(client, "card_sort")                                                         # 2
    add(client, "q_sort")                                                            # 3
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de", "ar"]
        db.session.commit()
    page = client.get(f"{E}/preview?lang=de&page=2&device=desktop&validate=0", follow_redirects=True).data.decode()
    assert page.count("data-sort-bar hidden") == 2 and page.count('class="p-sort-said" aria-live="polite"') == 2
    assert "↶ rückgängig" in page and "↶ zurück" not in page                       # X49
    assert ".p-q-grid .p-sort-pick { padding: 6px; font-size: 0.95rem;" in CSS and "grid-template-rows: subgrid" in CSS


# --- X40: the values of a Q sort keep their sign right to left -----------------------------------------------------


def test_x40_q_values_are_isolated_left_to_right(app, client, survey):
    assert '[dir="rtl"] .p-q-value { direction: ltr; unicode-bidi: isolate; }' in CSS
    add(client, "q_sort")                                                            # 2
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de", "ar"]
        db.session.commit()
    page = client.get(f"{E}/preview?lang=ar&page=2&device=desktop&validate=0", follow_redirects=True).data.decode()
    assert '<bdi class="p-q-value" dir="ltr">-2</bdi>' in page and '<bdi class="p-q-value" dir="ltr">+2</bdi>' in page


# --- X42: the hot spot names its areas and shows the ratings in a list --------------------------------------------


def test_x42_the_list_of_areas_stays_as_the_overview():
    block = JS[JS.index("document.querySelectorAll(\"[data-hot]\")"):JS.index("// Mark an image")]
    assert "list.hidden = true" not in block and 'area.addEventListener("change"' in block and "is-said" in block
    assert ".p-hot-area.is-said::before { content: attr(data-name);" in CSS
    assert "repeating-linear-gradient(45deg" in CSS  # "gefällt nicht" differs by more than colour


# --- X43: the consent names the contact address and the deletion code as set now --------------------------------


def test_x43_contact_and_deletion_code_follow_the_settings(app, client, survey):
    fill_consent(client)  # an own text "Text", contact studie@example.org
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.erase_code = True
        consent = s.consent_item
        texts = dict(consent.texts)
        texts["de"] = {**texts["de"], "body": "**Kontakt:** Die Studie verantwortet Ralf.\n\n**Anonymität und Datenschutz:** "
                       "In dem Browser, in dem Sie teilnehmen, können Sie Ihre Antworten bis 7 Tage nach der Teilnahme über "
                       "die Datenschutzseite dieser Befragung selbst löschen. Danach lassen sie sich Ihnen nicht mehr "
                       "zuordnen und deshalb nicht mehr einzeln löschen."}
        consent.texts = texts
        db.session.commit()
        shown = consent.kind.shown_body(consent, "de")
        assert "**Kontakt:** Die Studie verantwortet Ralf. Bei Fragen erreichen Sie uns unter studie@example.org." in shown
        assert "Löschcode" in shown and "bis 7 Tage" not in shown
        s.erase_code = False  # and back: no promise of a code the survey does not give
        db.session.commit()
        assert "Löschcode" not in consent.kind.shown_body(db.session.get(Survey, 1).consent_item, "de")
    uid = _start(app, client)
    page = app.test_client().get(f"/s/{uid}").data.decode()
    assert "studie@example.org" in page


# --- X44: typed answers keep their own direction -------------------------------------------------------------------


def test_x44_text_fields_and_piped_answers_take_their_own_direction(app):
    from app.items.base import Typed, rich as render_rich

    for name in ("text_input", "single_choice", "multiple_choice", "association", "voice"):
        tpl = _template(f"items/{name}/participant.html")
        for tag in re.findall(r"<(?:textarea|input type=\"text\")[^>]*>", tpl):
            assert 'dir="auto"' in tag, (name, tag)
    assert '[data-pipe] { unicode-bidi: plaintext; }' in CSS
    with app.app_context():
        html = render_rich("Sie schrieben: {{q1}}.", {"q1": Typed("مزيد من الحافلات")})
        assert "<bdi>مزيد من الحافلات</bdi>" in html
        assert "<bdi>" not in render_rich("Hallo {{name}}!", {"name": Typed("Anna")})


# --- X45: the end page with a code: close the window after noting it ----------------------------------------------


def test_x45_closing_comes_after_the_code_with_a_link_to_the_privacy_page(app, client, survey):
    from tests.privacy.test_erasure import build, take

    uid = build(app, client)
    end = take(app.test_client(), uid)
    assert "Ihre Antworten wurden gespeichert." in end and "Sie können dieses Fenster jetzt schließen" not in end
    code_at, close_at = end.index('class="p-erase-code"'), end.index("Wenn Sie den Code notiert haben")
    assert code_at < close_at and f'href="/s/{uid}/privacy?lang=de"' in end


# --- X46: /privacy/delete in the frame and language of the participants -------------------------------------------


def test_x46_delete_with_a_code_in_arabic_without_the_researchers_head(app, client, survey):
    page = client.get("/privacy/delete?lang=ar").data.decode()  # signed in as a researcher: still no researcher head
    assert '<html lang="ar" dir="rtl"' in page and "حذف الإجابات باستخدام رمز الحذف" in page
    assert 'class="p-container"' in page and "Konstrukte" not in page and "Administration" not in page
    wrong = client.post("/privacy/delete?lang=ar", data={"code": "1-2222-2222-2222", "sure": "1"}).data.decode()
    assert "لا توجد مشاركة مرتبطة بهذا الرمز" in wrong
    assert 'lang="en"' in client.get("/privacy/delete", headers={"Accept-Language": "en-GB,en;q=0.9"}).data.decode()


# --- X47, X48: the preview bar on the phone and the scaled phone ---------------------------------------------------


def test_x47_tools_fold_on_the_phone_and_the_colour_says_as_device(app, client, survey):
    add(client, "info")
    page = client.get(f"{E}/preview?lang=de&page=1&device=desktop", follow_redirects=True).data.decode()
    assert "data-preview-more" in page and 'id="preview-tools"' in page
    with app.app_context():
        from app import theme

        auto = theme.is_auto(theme.of(db.session.get(Survey, 1)))
    if auto:
        assert ">wie Gerät</a>" in page and "Farbe:" in page
    assert ".p-preview-bar.has-more .p-preview-tools { display: none; }" in CSS


def test_x48_the_device_view_says_when_it_is_scaled_down():
    tpl = _template("participant/preview_device.html")
    assert "data-scaled=\"{{ de_en('verkleinert auf {p} %', 'scaled down to {p}%') }}\"" in tpl and "data-device-actual" in tpl
    assert 'caption.dataset.scaled.replace("{p}", pct)' in JS


# --- X51: whom to tell about a barrier ------------------------------------------------------------------------------


def test_x51_the_statement_names_the_survey_contact_the_provider_and_the_form(app, client, survey):
    fill_consent(client)
    uid = _start(app, client)
    page = app.test_client().get(f"/s/{uid}/accessibility").data.decode()
    assert '<a href="mailto:studie@example.org">studie@example.org</a>' in page
    assert "zur Plattform an den Betreiber von Askurious:" in page and f"/s/{uid}/complaint" in page
