"""S2 of the UI/UX audit of 2026-10-07 (W29, W30, W79): one head for every page of a survey. The saved state and
↶ ↷ stand beside "Vorschau" on every page of a survey, sub pages too; every form and script that saves by itself
reports there, each under its own key, and an error stays until that very form has saved again."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Survey, User
from tests.conftest import add

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
NODE = shutil.which("node")
E = "/surveys/1/editor"


def _head(page):
    """The span of the saved state in the head of the survey."""
    m = re.search(r'<span class="autosave-status autosave-page" data-autosave-head[^>]*>.*?</a>\s*</span>', page, re.S)
    assert m, "no saved state in the head"
    return m.group(0)


def _actions(page):
    return page[page.index('class="survey-head-actions"'):].split('<nav class="survey-tabs"')[0]


# --- the rules, without the page (Node) ---------------------------------------------------------------------------

_HARNESS = r"""
const source = require("fs").readFileSync(process.argv[2], "utf8").replace(/\r\n/g, "\n");
const start = source.indexOf("var SAVE_TROUBLE");
const end = source.indexOf("// end of the rules of the save head");
const lang = process.argv[3];
const de_en = (de, en) => lang === "en" ? en : de;
const api = new Function("de_en", source.slice(start, end) + "; return { next: saveHeadNext, summary: saveHeadSummary };")(de_en);
const src = () => ({ busy: false, trouble: null, ok: null, at: 0 });
let seq = 0;
const A = src(), B = src(), C = src(), list = [A, B, C];   // three forms in the order of the page
const say = () => { const s = api.summary(list); return s ? [s.kind, s.long, s.short, s.place === A ? "A" : s.place === B ? "B" : s.place === C ? "C" : null] : null; };
const out = {};
out.start = say();                                          // nothing saved on this page yet
api.next(A, "idle", "", ++seq); out.idle = say();
api.next(A, "busy", "", ++seq); out.busy = say();
api.next(A, "ok", "Gespeichert 10:01", ++seq); out.ok = say();
api.next(B, "error", "Nicht gespeichert: keine Verbindung", ++seq); out.error = say();
api.next(C, "ok", "Gespeichert 10:03", ++seq); out.other_ok = say();      // a good save elsewhere covers nothing
api.next(A, "conflict", "Nicht gespeichert: Kollegin hat das hier geändert", ++seq); out.two = say();
out.title = api.summary(list).title;
api.next(B, "busy", "", ++seq); out.retrying = say();      // trying again: still not saved
api.next(B, "ok", "Gespeichert 10:05", ++seq); out.b_fixed = say();
api.next(A, "ok", "Gespeichert 10:06", ++seq); out.all_fixed = say();
api.next(C, "warn", "Gespeichert bis auf den Variablennamen.", ++seq); out.warn = say();
api.next(C, "ok", "Gespeichert 10:08", ++seq); out.after_warn = say();
process.stdout.write(JSON.stringify(out));
"""


def _rules(tmp_path, lang):
    harness = tmp_path / "head.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    done = subprocess.run([NODE, str(harness), str(ROOT / "app" / "static" / "app.js"), lang],
                          capture_output=True, check=True, timeout=60)
    return json.loads(done.stdout.decode("utf-8"))


@pytest.mark.skipif(NODE is None, reason="needs Node to run the rules of the save head in app/static/app.js")
def test_the_head_keeps_the_state_of_each_form(tmp_path):
    r = _rules(tmp_path, "de")
    assert r["start"] is None and r["idle"] is None          # the template's words stay: no "gespeichert" without a save
    assert r["busy"][:3] == ["busy", "Wird gespeichert …", "speichert …"]
    assert r["ok"][:3] == ["ok", "Gespeichert 10:01", "gespeichert"]
    assert r["error"] == ["error", "Nicht gespeichert", "nicht gespeichert", "B"]
    assert r["other_ok"] == r["error"]                       # B's error stays when C saves well
    assert r["two"] == ["error", "2 nicht gespeichert", "2 Fehler", "A"]   # the link goes to the first place
    assert r["title"] == "Nicht gespeichert: Kollegin hat das hier geändert\nNicht gespeichert: keine Verbindung"
    assert r["retrying"][1] == "2 nicht gespeichert"
    assert r["b_fixed"] == ["conflict", "Nicht gespeichert: Jemand anderes hat geändert", "nicht gespeichert", "A"]
    assert r["all_fixed"][:2] == ["ok", "Gespeichert 10:06"]
    assert r["warn"] == ["warn", "Gespeichert, mit Hinweis", "Hinweis", "C"]
    assert r["after_warn"][:2] == ["ok", "Gespeichert 10:08"]


@pytest.mark.skipif(NODE is None, reason="needs Node to run the rules of the save head in app/static/app.js")
def test_the_head_speaks_english(tmp_path):
    r = _rules(tmp_path, "en")
    assert r["two"][1:3] == ["2 not saved", "2 not saved"] and r["error"][1] == "Not saved"
    assert r["busy"][1] == "Saving …" and r["warn"][1] == "Saved, with a note"


def test_every_script_with_a_line_of_its_own_reports_to_the_head():
    assert 'saveState(form, "idle", "", bar)' in JS and "saveState(form, kind || \"busy\", text, bar)" in JS   # forms
    assert "saveState(quizForm" in JS                                                            # live poll quiz boxes
    assert 'saveState(row, "busy", "", box)' in JS and 'saveState(row, "error"' in JS              # translation rows
    assert "window.saveState(area" in (ROOT / "app" / "static" / "summaries.js").read_text(encoding="utf-8")
    # the lines of the forms only show trouble; the element editor keeps "Fertig"
    assert ".autosave-bar.is-quiet .autosave-status:not(.is-error, .is-conflict, .is-invalid, .is-warn) { display: none; }" in CSS
    assert "[data-autosave-done])) { display: none; }" in CSS


# --- the head on the pages ----------------------------------------------------------------------------------------


def test_one_head_with_status_and_arrows_on_every_page(app, client, survey):
    add(client, "text_input")                                              # item 2
    client.post(f"{E}/blocks/2/loop")
    with app.app_context():
        nid = next(n for n in db.session.get(Survey, 1).flow["children"] if n["t"] == "loop")["id"]
    pages = ["/surveys/1", "/surveys/1/researchdesign", f"{E}/", f"{E}/items/2", "/surveys/1/flow/",
             f"/surveys/1/flow/loop/{nid}", "/surveys/1/flow/quotas/", "/surveys/1/flow/quotas/assistant",
             "/surveys/1/translation/", "/surveys/1/field", "/surveys/1/quality", "/surveys/1/design",
             "/surveys/1/dashboards/", "/surveys/1/duplicate"]
    for url in pages:
        page = client.get(url).data.decode()
        assert page.count("data-autosave-head") == 1 and page.count('class="undo-bar"') == 1, url
        actions = _actions(page)
        # Vorschau, then the saved state, then ↶ ↷
        assert actions.index("Vorschau") < actions.index("data-autosave-head") < actions.index('class="undo-bar"'), url
        head = _head(page)
        assert 'role="status" aria-live="polite"' in head and " hidden>" not in head.split(">")[0] + ">", url
        assert "data-autosave-page" not in page, url                    # the line of pages outside a survey


def test_the_head_claims_nothing_before_a_save(app, client, survey):
    head = _head(client.get("/surveys/1").data.decode())
    assert "Änderungen werden automatisch gespeichert." in head and "Alles gespeichert" not in head
    add(client, "text_input")                                              # a step of this person: its time
    head = _head(client.get("/surveys/1").data.decode())
    assert re.search(r'data-neutral="Ihre letzte Änderung: \d\d:\d\d"', head)
    assert re.search(r"<span class=\"autosave-long\" data-autosave-long>Ihre letzte Änderung: \d\d:\d\d</span>", head)
    assert '<span class="autosave-short" data-autosave-short></span>' in head   # on a phone: only ↶ ↷


def test_the_head_in_english(app, client, survey):
    with app.app_context():
        user = db.session.get(User, 1)
        user.display = {**(user.display or {}), "lang": "en"}
        db.session.commit()
    head = _head(client.get("/surveys/1").data.decode())
    assert "Changes are saved automatically." in head and 'aria-label="show"' in head
    add(client, "text_input")
    assert "Your last change: " in _head(client.get("/surveys/1").data.decode())


def test_sub_pages_lost_their_own_arrows(app, client, survey):
    """W29/W30: element, loop and image pages have ↶ ↷ in the head of the survey, not in their own title line."""
    add(client, "text_input")
    page = client.get(f"{E}/items/2").data.decode()
    assert page.count('class="undo-bar"') == 1 and 'class="undo-bar"' in _actions(page)
    tpl = ROOT / "app" / "templates"
    for name in ("editor/loop.html", "editor/vignettes.html", "items/images.html"):
        assert '{% include "editor/_undo.html" %}' not in (tpl / name).read_text(encoding="utf-8"), name
    assert '{% if in_live %}{% include "editor/_undo.html" %}{% endif %}' in (tpl / "editor/item.html").read_text(encoding="utf-8")


def test_export_shows_the_head_only_when_something_saves(client, survey):
    page = client.get("/surveys/1/data").data.decode()
    # invisible but in its place, so that "Vorschau" stands still (UI/UX audit 2026-10-09, X71); no arrows to press
    assert re.search(r'class="autosave-status autosave-page autosave-reserved" data-autosave-head', page)
    assert 'class="undo-bar undo-reserved" aria-hidden="true"' in page and "data-undo" not in page
    assert "scrollbar-gutter: stable" in CSS and ".autosave-reserved, .undo-reserved { visibility: hidden; }" in CSS


def test_pages_outside_a_survey_keep_their_one_line(client, survey):
    """W100: Konto and Designs are not part of S2."""
    for url in ("/account/", "/designs/"):
        page = client.get(url).data.decode()
        assert page.count("data-autosave-page") == 1 and "data-autosave-head" not in page, url
    assert 'var pageLine = !inHead && bars.length > 1 ? document.querySelector("[data-autosave-page]") : null;' in JS


def test_a_viewer_gets_no_head(app, client, survey):
    from tests.editor.test_undo import colleague

    viewer = colleague(app, "viewer")
    page = viewer.get("/surveys/1").data.decode()
    assert "data-autosave-head" not in page and 'class="undo-bar"' not in page


def test_on_a_phone_the_head_stays_on_one_row_and_the_pages_do_not_scroll_sideways():
    block = CSS[CSS.index("@media (max-width: 600px) {\n  .survey-head-actions { flex-wrap: wrap;"):][:900]
    assert ".survey-head-actions { gap: 4px; }" in block and ".survey-head-actions .icon-btn { width: 28px; }" in block
    assert ".autosave-page .autosave-long { display: none; }" in block and ".autosave-page .autosave-short { display: inline; }" in block
    # what made pages wider than 390 px (measured on every tab and sub page, German and English)
    assert "margin: 0; min-width: 0; }  /* min-width: a wide row inside" in CSS                  # fieldsets
    assert "@media (max-width: 600px) { .row { flex-wrap: wrap; } }" in CSS
    assert ".live-work > * { min-width: 0; }" in CSS
    assert "@media (max-width: 600px) { .export-row { flex-wrap: wrap; gap: 8px; } }" in CSS
    assert ".design-page { grid-template-columns: minmax(0, 1fr); }" in CSS
    assert "grid-template-columns: fit-content(45%) minmax(0, 1fr)" in CSS
    assert ".list-table:where(.is-edit) .narrow-col { width: auto; }" in CSS


def test_errors_are_shown_with_a_symbol_and_words():
    assert '.autosave-status.is-invalid::before { content: "⚠ "; color: var(--danger); }' in CSS
    assert ".autosave-page:is(.is-error, .is-conflict, .is-invalid) { color: var(--danger); }" in CSS
