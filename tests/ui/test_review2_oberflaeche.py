"""Code review of 2026-10-03, package 5 "Oberfläche" (U-1 to U-16, K-9): the dark design, uniform rounding, one
autosave module, the live editor's dragging, no inline handlers, texts participants read in their language,
errors that are shown, linked-element name and AI prices saving by themselves, labels on the row editors."""
from tests.static_sources import participant_js

import re
from pathlib import Path

from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add
from tests.editor.test_linked import _linked_question
from tests.live.test_live import add as live_add, make, send, steer

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


def read(name):
    if name == "participant.js":
        return participant_js()
    return (STATIC / name).read_text(encoding="utf-8")


def css_outside_root(name):
    """The rules of a style sheet without its token blocks (:root and the dark :root)."""
    return re.sub(r"(?ms)^:root[^{\n]*\{[ \t]*\r?\n.*?^\}", "", read(name))


# --- U-1, U-2, U-11: the dark design -----------------------------------------------------------------------------

def test_text_on_accent_danger_and_warn_follows_the_design():
    css = css_outside_root("app.css")
    # the periods of "KI-Verbrauch" are a .segmented now, without a filled accent surface (UI audit 2026-10-07, W81)
    for rule in (".facet-chip.is-on", ".reopen-box .button-secondary", ".sev-badge.sev-4"):
        body = css[css.index(rule + " {"):].split("}", 1)[0]
        assert "#fff" not in body and "var(--on-" in body, rule
    step = css[css.index(".step-no {"):].split("}", 1)[0]
    assert "#fff" not in step and "var(--on-accent)" in step
    root = read("app.css")
    assert root.count("--on-warn:") == 2                                     # light and dark


def test_no_fixed_light_brand_colours_outside_the_tokens():
    css = css_outside_root("app.css")
    assert not re.findall(r"rgba\((41, 87, 164|31, 107, 42|179, 38, 30|200, 150, 0),", css)
    placeholder = read("participant.css").split(".placeholder {", 1)[1].split("}", 1)[0]
    assert "color:" in placeholder                                           # dark ink on its light ground


def test_each_rule_written_once():
    css = read("app.css")
    assert len(re.findall(r"(?m)^\.fl-node\.fl-split \{", css)) == 1
    assert len(re.findall(r"(?m)^\.item-icon \{", css)) == 1
    assert len(re.findall(r"(?m)^\.split-divider \{", css)) == 1


# --- U-3: uniform rounding ------------------------------------------------------------------------------------------

def test_histogram_classes_of_the_screen_have_the_same_places():
    from app.live import _classes

    assert _classes(0, 1.5)[:3] == ["0,0–1,5", "1,5–3,0", "3,0–4,5"]       # not "0–1,5", "1,5–3"
    assert _classes(0, 10)[:2] == ["0–10", "10–20"]


def test_live_means_and_psm_prices_keep_their_places():
    live = read("live.js")
    num = live[live.index("function num(v)"):].split("\n  }", 1)[0]
    assert "toFixed(1)" in num and "String(v)" not in num
    app_js = read("app.js")
    psm = app_js[app_js.index("[data-psm-chart]"):]
    assert ".replace(/\\.?0+$/" not in psm[:3000]


# --- U-4, U-7, U-13: one autosave module, one flash, one esc ---------------------------------------------------------

def test_one_autosave_module_for_the_element_editor_and_the_other_forms():
    js = read("app.js")
    assert js.count('querySelectorAll("form[data-autosave-form], form[data-autosave]")') == 1
    assert 'document.querySelector("form[data-autosave]")' not in js
    assert js.count("function flash(") == 1 and js.count("function undoArrows(") == 1
    assert js.count("input[type=datetime-local]") == 1                    # the element editor has it too
    assert js.count("function esc(") == 1 and js.count("function ownText(") == 1
    assert js.count("""document.querySelector('meta[name="csrf-token"]')""") == 1
    assert "nothing is saved before" not in js


def test_the_element_editor_still_saves_by_itself(app, client, survey):
    add(client, "single_choice")
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert "data-autosave" in page and "data-autosave-status" in page
    # the fields of each answer row have names for screen readers
    assert 'name="option_code" aria-label="Code"' in page and 'name="option_label" aria-label="Beschriftung"' in page
    r = client.post("/surveys/1/editor/items/2", data={"code": "q1", "question": "Neu?", "autosave": "1", "what": "Frage"},
                    headers={"X-Requested-With": "fetch"})
    d = r.get_json()
    assert d["status"] == "saved" and d["code"] == "q1" and "problems" in d and d["at"]


def test_the_live_quiz_box_says_what_the_bar_says_and_enter_saves():
    js = read("app.js")
    box = js[js.index('querySelectorAll("[data-live-quiz]")'):][:2200]  # longer since requests wait for each other (F8)
    assert '"Gespeichert " +' in box and 'addEventListener("submit"' in box and "preventDefault" in box
    assert '"Gespeichert."' not in box


def test_quotes_are_escaped_everywhere():
    for name in ("live.js", "map_results.js", "postal_map.js"):
        body = read(name)
        esc = body[body.index("function esc("):][:300]
        assert "&#39;" in esc and "&quot;" in esc, name


# --- U-5: the live editor drags with the shared pointerDrag ----------------------------------------------------------

def test_live_editor_drags_with_pointer_drag():
    js = read("app.js")
    part = js[js.index("Live vote, editing page (live/manage.html)"):]
    part = part[:part.index("A question just added")]
    assert "pointerDrag({" in part and "setPointerCapture" not in part
    assert "actionFailed(" in part                                           # a refused move is said, not reloaded over


# --- U-6: no inline handlers on the researchers' pages ---------------------------------------------------------------

def test_no_inline_handlers_outside_the_participant_previews():
    found = []
    for path in TEMPLATES.rglob("*.html"):
        rel = path.relative_to(TEMPLATES).as_posix()
        # participant pages and previews have no app.js; the printable live slides stay with live.js
        if rel.startswith("participant/") or rel in ("editor/item_frame.html", "live/slides.html"):
            continue
        found += [f"{rel}: {m.group(0)}" for m in re.finditer(r'\son(?:click|change|submit|focus|input)="', path.read_text(encoding="utf-8"))]
    assert not found, "; ".join(found)


def test_busy_forms_and_choices_use_data_attributes():
    for name in ("items/conjoint/panel.html", "items/maxdiff/panel.html"):
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "data-busy=" in source and "Wird berechnet …" in source, name  # de_en('Wird berechnet …', …)
    js = read("app.js")
    assert "form[data-busy]" in js and 'addEventListener("pageshow"' in js
    # the select that saves its form at once: participant.js (also loaded wherever app.js is)
    assert 'matches("select[data-submit-on-change], input[data-submit-on-change]")' in read("participant.js")


# --- U-8: what participants read is in their language ----------------------------------------------------------------

def test_leaderboard_names_without_nickname_in_the_language_of_the_vote(app, client):
    from app import live

    code, sid, block = make(app, client, "Quiz")
    with app.app_context():
        s = db.session.get(Survey, sid)
        s.default_language, s.languages = "en", ["en"]
        db.session.commit()
    q = live_add(app, client, sid, block, "single_choice", question="Capital?", options=["Berlin", "Bonn"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "1"})
    p = app.test_client()
    p.get(f"/live/{code}")
    steer(client, code, action="start")
    send(p, code, {f"i{q}": "1"})
    with app.app_context():
        s = db.session.get(Survey, sid)
        names = [name for _, name, *_ in live._leaderboard(s, live.state(s))]  # a random English nickname, e.g. "Brave Fox"
        assert len(names) == 1 and names[0].split(" ")[-1] in {en for _, _, en in live.NICK_ANIMALS}


def test_scripts_take_their_words_from_the_page():
    assert 'uiT("zurücklegen", "put back")' in read("participant.js")
    assert '"put back"' in (ROOT / "app" / "ui_i18n.py").read_text(encoding="utf-8")
    comments = read("comments.js")
    assert "T.delete" in comments and "T.not_deleted" in comments
    board = read("board.js")
    assert "T.image_title ||" in board and "T.image_failed ||" in board


def test_the_comment_box_of_the_preview_in_the_researchers_language(app, client, survey):
    """The comment box speaks the researcher's language, not the survey's (Ralf, 2026-10-07)."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de", "en"]
        db.session.commit()
    add(client, "text_input")
    page = client.get("/surveys/1/editor/preview?lang=en&page=2", follow_redirects=True).data.decode()
    assert "data-comments" in page                                           # the comment box of the preview
    assert "Kommentare zu dieser Seite" in page and "Kommentar senden" in page and "Send comment" not in page
    assert "&#34;delete&#34;: &#34;delete&#34;" not in page                   # the words of comments.js


# --- U-9: the state map's popup uses the word of its legend ----------------------------------------------------------

def test_state_map_popup_and_legend_use_one_word():
    # the map is drawn on the server since V58; state_map.js went in the code review 2026-10-07 (F5)
    part = (TEMPLATES / "surveys" / "_state_results.html").read_text(encoding="utf-8")
    assert "unit_word" in part and "#ffffff" not in part     # an outline map without tiles since V58


# --- U-10: errors that are shown -------------------------------------------------------------------------------------

def test_failed_sends_are_said():
    live = read("live.js")
    assert "r.json().catch(function () { return {}; })" in live and "qa.dataset.fail" in live and "data-nick-fail" in live
    comments = read("comments.js")
    delete = comments[comments.index("/delete"):][:400]
    assert "r.ok" in delete and "catch" in delete
    join = (TEMPLATES / "live" / "join.html").read_text(encoding="utf-8")
    assert "data-nick-fail" in join and 'data-fail="' in join


# --- U-14: no save buttons where a form can save by itself -----------------------------------------------------------

def test_linked_element_name_saves_by_itself(app, client, survey):
    key = _linked_question(app, client)
    page = client.get(f"/constructs/linked/{key}").data.decode()
    form = page[page.index('value="rename"') - 300:page.index('value="rename"') + 1500]
    assert "data-autosave-form" in form and ">Speichern</button></noscript>" in form
    r = client.post(f"/constructs/linked/{key}", data={"action": "rename", "name": "Vorwissen neu", "autosave": "1"},
                    headers={"X-Requested-With": "fetch"})
    assert r.get_json()["status"] == "saved"
    empty = client.post(f"/constructs/linked/{key}", data={"action": "rename", "name": " ", "autosave": "1"},
                        headers={"X-Requested-With": "fetch"}).get_json()
    assert empty["status"] == "invalid"
    from app import linked

    with app.app_context():
        assert linked.get(key).name == "Vorwissen neu"


def test_ai_prices_save_by_themselves(app, client, survey):
    page = client.get("/admin/ai").data.decode()
    assert "data-autosave-form" in page and "Preise speichern" not in page
    d = client.post("/admin/ai?period=week", data={"text_model": ["gpt-x", ""], "text_in": ["2,00", ""], "text_out": ["8", ""],
                                                    "autosave": "1"}, headers={"X-Requested-With": "fetch"}).get_json()
    assert d["status"] == "saved"
    assert 'value="gpt-x"' in client.get("/admin/ai").data.decode()


# --- U-15: every field of the row editors has a name a screen reader reads out ---------------------------------------

def test_row_editor_fields_have_labels():
    files = ["items/_choice_editor.html", "items/likert/editor.html", "items/semantic/editor.html", "items/slider/editor.html",
             "items/ranking/editor.html", "items/position2d/editor.html", "items/constant_sum/editor.html",
             "items/assign/editor.html", "items/map_marker/editor.html", "items/_attributes/editor.html",
             "items/score_band/editor.html", "items/psm/editor.html", "admin/ai.html",
             "items/rating/participant.html", "items/slider/participant.html"]
    missing = []
    for f in files:
        text, depth = (TEMPLATES / f).read_text(encoding="utf-8"), 0
        for m in re.finditer(r"<(/?)(label|input|select|textarea)\b([^>]*)>", text):
            close, tag, attrs = m.groups()
            if tag == "label":
                depth += -1 if close else 1
            elif not close and 'type="hidden"' not in attrs and depth <= 0 and "aria-label" not in attrs:
                missing.append(f"{f}: {attrs.strip()[:60]}")
    assert not missing, "; ".join(missing)
