"""UI/UX audit 2026-10-09, package B (questionnaire and element editors): X3, X11, X18 to X29, X31, S14, S15."""
import io
import re
from pathlib import Path

from werkzeug.datastructures import MultiDict

from app import items, study_plan
from app.editor import soft_hyphens
from app.extensions import db
from app.models import Asset, EditStep, Item, Survey
from tests.conftest import add, fill_consent
from tests.items.test_side_by_side import editor_form, setup as sbs_setup
from tests.analysis.test_stimulus import _media, _upload

E = "/surveys/1/editor"
ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"


# ---- X19, X18: "Liste einfügen" -------------------------------------------------------------------------------------

def test_a_pasted_list_starts_at_code_1_and_says_how_many(app, client, survey):
    """X19: the two empty options of a new single choice go, so the list starts at 1; X18: the page says what came in."""
    add(client, "single_choice")                                                # item 2, options 1 and 2 without labels
    with app.app_context():
        empty = [(o["id"], o["code"]) for o in db.session.get(Item, 2).config["options"]]
    assert [code for _, code in empty] == ["1", "2"]
    data = [("code", "stadt"), ("question", "Wo wohnen Sie?"), ("answer_mode", "optional")]
    for oid, code in empty:  # the editor sends the two rows as they stand
        data += [("option_id", oid), ("option_code", code), ("option_label", "")]
    data += [("options_paste", "Aachen\nBonn\nKöln\nMünchen\nBerlin\n")]
    client.post(f"{E}/items/2", data=MultiDict(data + [("autosave", "1")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        labels = item.texts["de"]["options"]
        assert [(o["code"], labels[o["id"]]) for o in item.config["options"]] == [
            ("1", "Aachen"), ("2", "Bonn"), ("3", "Köln"), ("4", "München"), ("5", "Berlin")]
    page = client.get(f"{E}/items/2").data.decode()
    assert "5 Optionen aus der Liste übernommen." in page


def test_a_labelled_option_stays_when_a_list_is_pasted(app, client, survey):
    add(client, "single_choice")
    client.post(f"{E}/items/2", data=MultiDict([("code", "s"), ("question", "?"), ("option_id", "a"), ("option_code", "1"),
                                                  ("option_label", "Aachen"), ("option_id", "b"), ("option_code", "2"),
                                                  ("option_label", ""), ("options_paste", "Bonn")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        labels = item.texts["de"]["options"]
        assert [(o["code"], labels[o["id"]]) for o in item.config["options"]] == [("1", "Aachen"), ("2", "Bonn")]


def test_reload_after_a_pause_in_typing_too():
    """X18, S15: a field marked data-reload-saved loads the page anew after a save from typing (input), not only
    after leaving the field (change)."""
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    handler = js[js.index('document.addEventListener("input", function (e) {\n      if (!mine_(e.target)) return;'):]
    handler = handler[:handler.index("timer = setTimeout")]
    assert 'hasAttribute("data-reload-saved")) reloadSaved = true' in handler


# ---- X20: Q-Sort grid ----------------------------------------------------------------------------------------------

def test_q_sort_grid_is_drawn_anew_and_uses_a_real_minus(app, client, survey):
    add(client, "q_sort")
    page = client.get(f"{E}/items/2").data.decode()
    assert 'data-live="qsort-grid-2"' in page
    head = re.search(r'data-live="qsort-grid-2">.*?</thead>', page, re.S).group(0)
    assert "<th>−3</th>" in head and "<th>+3</th>" in head and "<th>-3</th>" not in head


# ---- X21, X27, X28: side-by-side ------------------------------------------------------------------------------------

def test_side_by_side_column_is_removed_with_x_and_named_in_the_history(app, client, survey):
    item_id = sbs_setup(client)
    page = client.get(f"{E}/items/{item_id}").data.decode()
    assert 'type="checkbox" name="col_remove_' not in page                       # no tick any more
    assert 'name="col_remove_c2" value="" data-remove-value' in page and 'data-remove-flag="[data-sbs-col-editor]"' in page
    assert 'data-undo-what="Spalte 2 entfernt"' in page
    assert 'class="sbs-col-title"' in page                                       # X28: the question over the full width
    form = editor_form(item_id)
    form.add("col_remove_c2", "1")
    form.add("autosave", "1")
    form.add("what", "Spalte 2 entfernt")
    form.add("what_done", "1")
    client.post(f"{E}/items/{item_id}", data=form)
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert [c["id"] for c in item.config["columns"]] == ["c1", "c3", "c4", "c5"]
        step = db.session.scalars(db.select(EditStep).order_by(EditStep.id.desc())).first()
        assert step.label == "„mob“: Spalte 2 entfernt"
    client.post(f"{E}/undo", follow_redirects=True)
    with app.app_context():
        assert [c["id"] for c in db.session.get(Item, item_id).config["columns"]] == ["c1", "c2", "c3", "c4", "c5"]


def test_condition_answers_have_no_frame_of_their_own():
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    assert "fieldset.sbs-when-src { border: 0;" in css and ".sbs-cols > .sbs-col { border: 0; border-top:" in css


# ---- X22: hot spot without dragging ---------------------------------------------------------------------------------

def test_hot_spot_areas_have_number_fields(app, client, survey):
    add(client, "image_mark")
    client.post(f"{E}/items/2", data={"code": "hs", "question": "?", "mode": "areas"})
    page = client.get(f"{E}/items/2").data.decode()
    for part in range(4):
        assert f'data-hot-num="{part}"' in page
    template = (TEMPLATES / "items" / "image_mark" / "editor.html").read_text(encoding="utf-8")
    assert "Ohne Ziehen: Lage und Größe in der Zeile in Prozent des Bildes eintragen." in template


# ---- S14, X23, X11, X25: keywords, palette, /itemtypes --------------------------------------------------------------

def test_every_type_has_keywords_and_the_searches_find_them(app, client, survey):
    for kind in items.all_types():
        assert kind.fact_sheet.keywords, kind.key
    for word, key in (("hot spot", "image_mark"), ("suchfeld", "single_choice"), ("autovervollständigung", "single_choice"),
                      ("nps", "rating"), ("ranking", "ranking")):
        kind = items.get(key)
        assert word in (kind.label + " " + kind.description + " " + kind.search_words).lower(), word
    editor = client.get(f"{E}/").data.decode()
    row = re.search(r'<li class="palette-row" data-search="([^"]*)">\s*<div class="palette-item" data-type="image_mark"', editor)
    assert row and "hot spot" in row.group(1)
    assert 'data-block-palette' in editor                                        # X29: "+ Element hinzufügen" per block
    for q, key in (("NPS", "rating"), ("Hot Spot", "image_mark"), ("Autovervollständigung", "single_choice")):
        page = client.get("/itemtypes/", query_string={"q": q}).data.decode()
        assert f'id="{key}"' in page, q


def test_itemtypes_page_is_folded(app, client, survey):
    page = client.get("/itemtypes/").data.decode()
    assert '<details class="construct-card element-type" id="single_choice">' in page   # folded, no "open"
    assert '<span class="tag">' not in page                                      # plain facts without a frame
    few = client.get("/itemtypes/", query_string={"q": "NPS"}).data.decode()
    assert '<details class="construct-card element-type" id="rating" open>' in few


def test_soft_hyphens_in_long_compounds():
    assert soft_hyphens("Zuordnungsaufgabe") == "Zuordnungs­aufgabe"
    assert soft_hyphens("Allgemeines Umweltbewusstsein") == "Allgemeines Umwelt­bewusst­sein"
    assert soft_hyphens("Einfachauswahl") == "Einfachauswahl"


# ---- X26: the language once, not in every column head ---------------------------------------------------------------

def test_no_language_in_brackets_in_the_editors(app, client, survey):
    for path in (TEMPLATES / "items").rglob("*.html"):
        assert "({{ languages[lang] }})" not in path.read_text(encoding="utf-8"), path
    for kind in ("single_choice", "card_sort", "q_sort", "image_mark", "drill_down"):
        add(client, kind)
    with app.app_context():
        ids = [i.id for i in db.session.scalars(db.select(Item).filter(Item.type != "consent"))]
    for item_id in ids:
        assert "(Deutsch (Sie))" not in client.get(f"{E}/items/{item_id}").data.decode()


# ---- X24: rows on a phone --------------------------------------------------------------------------------------------

def test_option_rows_stack_on_a_phone(app, client, survey):
    add(client, "single_choice")
    page = client.get(f"{E}/items/2").data.decode()
    assert 'class="list-table is-edit stack-phone"' in page and 'data-label="fehlend"' in page
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    assert ".list-table.stack-phone tr { display: flex;" in css


# ---- X31: video / audio file removed with ×, back with undo ---------------------------------------------------------

def test_media_file_is_removed_and_comes_back(app, client, survey):
    _media(app, client)
    _upload(client)
    page = client.get(f"{E}/items/2").data.decode()
    assert 'name="file_remove_" value="" data-remove-value' in page and "Datei entfernen" in page
    assert 'badge-exp">experimentell' not in page.split('<legend>Datei')[1][:80]   # no second mark at the fieldset
    client.post(f"{E}/items/2", data={"code": "spot", "question": "Bitte ansehen", "seek": "back", "advance": "any",
                                     "share": "90", "file_remove_": "1", "autosave": "1", "what": "Datei entfernt",
                                     "what_done": "1"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.asset_for(item) is None and "noch keine Datei hochgeladen" in item.kind.validate(item)
        assert db.session.scalars(db.select(Asset).filter_by(item_id=2)).first() is not None   # kept for undo
    assert "Noch keine Datei." in client.get(f"{E}/items/2").data.decode()
    client.post(f"{E}/undo", follow_redirects=True)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.asset_for(item) is not None and "noch keine Datei hochgeladen" not in item.kind.validate(item)


# ---- X3: a scale as "groups" ----------------------------------------------------------------------------------------

def test_a_metric_variable_as_groups_is_warned_and_greyed_out(app, client, survey):
    add(client, "text_input")                                                   # 2: a number
    client.post(f"{E}/items/2", data={"code": "minuten", "question": "Minuten?", "validation": "integer"})
    add(client, "text_input")                                                   # 3: another number
    client.post(f"{E}/items/3", data={"code": "alter", "question": "Alter?", "validation": "integer"})
    client.post("/surveys/1/researchdesign", data=MultiDict({
        "research_question": "?", "goal": "difference", "design": "cross", "h_id": [""], "h_text": ["A unterscheidet B"],
        "h_kind": ["difference"], "h_dir": [""], "h_iv": ["alter"], "h_dv": ["minuten"], "h_analysis": [""],
        "sample_target": "", "recruitment": "", "sample_note": "", "rules": ""}), follow_redirects=True)
    with app.app_context():
        s = db.session.get(Survey, 1)
        h = study_plan.plan_of(s)["hypotheses"][0]
        warning = study_plan.group_mismatch(s, h)
        assert "„alter“ ist eine Zahl, keine Gruppenfrage" in warning and warning in study_plan.reasons(s, h)
        assert any("„alter“ ist eine Zahl" in f.message for f in study_plan.findings(s))
        assert study_plan.metric_codes(s) >= {"alter", "minuten"}
        assert not study_plan.group_mismatch(s, {**h, "kind": "correlation"})
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert re.search(r'<option value="minuten"[^>]*data-metric', page)


# ---- S15: the list of recent changes says the day ------------------------------------------------------------------

def test_recent_changes_show_day_and_time_for_older_steps():
    template = (TEMPLATES / "editor" / "_undo.html").read_text(encoding="utf-8")
    assert '<span class="undo-time">{{ undo_when(step) }}</span>' in template
