"""UI/UX audit 2026-10-07, package C: questionnaire and element editors (W24 to W41, W29 deferred)."""
import re
from pathlib import Path

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add, fill_consent
from tests.analysis.test_acbc import acbc_form
from tests.analysis.test_conjoint import conjoint_form, setup

E = "/surveys/1/editor"
ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")


def _section(html, start):
    return html.split(start, 1)[1]


def test_w25_same_tabs_in_the_three_choice_editors(app, client, survey):
    fill_consent(client)
    for kind in ("conjoint", "acbc", "maxdiff"):
        add(client, kind)
    with app.app_context():
        ids = {i.type: i.id for i in db.session.scalars(db.select(Item))}
    pages = {k: client.get(f"{E}/items/{ids[k]}").data.decode() for k in ("conjoint", "acbc", "maxdiff")}
    for html in pages.values():
        assert ">1 · Inhalt<" in html and ">2 · Ablauf<" in html and "Items &amp; Aufgabe" not in html
    assert "Aufgaben je Person" in pages["acbc"] and "Ablauf je Person" not in pages["acbc"]


def test_w26_design_hints_once_in_the_design_box(app, client, survey):
    setup(client)  # 4 tasks: fewer than usual for HB
    html = client.get(E + "/items/2").data.decode()
    holdouts = html.split('class="ho-editor"', 1)[1].split("</fieldset>", 1)[0]
    design = html.split('id="design" data-cj-tab="design"', 1)[1].split("</section>", 1)[0]
    assert "Auswahlaufgaben je Person" not in holdouts
    assert design.count("Auswahlaufgaben je Person") == 1
    # W24: a change of the display or of the level mode reloads the image box
    assert re.search(r'<select name="display" data-show-for data-reload-saved', html)
    assert re.search(r'name="level_mode" value="replace" data-reload-saved', html)
    with app.app_context():
        item = db.session.get(Item, 2)
        warnings = item.kind.check_warnings(item)  # the pre-launch check still gets all of them
        assert item.kind.design_warnings(item)[0] in warnings


def test_w26_no_sample_size_without_an_estimable_design(app, client, survey, monkeypatch):
    from app.items.conjoint import Conjoint

    setup(client)
    monkeypatch.setattr(Conjoint, "design_efficiency", lambda self, item: 0)
    html = client.get(E + "/items/2").data.decode()
    assert "erst ein schätzbarer Versuchsplan" in html


def test_w27_no_survey_chosen_beforehand(app, client, survey):
    from app import duplicate
    from app.models import EditMark, User, utcnow

    add(client, "slider")
    for title in ("Alt", "Neu"):
        client.post("/surveys/new", data={"title": title, "languages": ["de"], "default_language": "de"})
    html = client.get(E + "/items/2").data.decode()
    select = html.split('name="target"', 1)[1].split("</select>", 1)[0]
    # not "required": linked without a survey only makes the question linkable (Ralf, 2026-10-08)
    assert re.match(r' data-copy-target>\s*<option value="" selected>– Befragung wählen –</option>', select)
    assert "data-copy-go" in html and "data-copy-target" in JS
    with app.app_context():
        old = db.session.scalar(db.select(Survey).filter_by(title="Alt"))
        user = db.session.get(User, old.members[0].user_id)
        item = db.session.get(Item, 2)
        assert [s.title for s in duplicate.item_targets(user, item)] == ["Neu", "Alt"]  # newest first
        db.session.add(EditMark(survey_id=old.id, key="item:1", user_id=user.id, saved_at=utcnow()))
        db.session.commit()
        assert [s.title for s in duplicate.item_targets(user, item)] == ["Alt", "Neu"]  # last saved in first


def test_w28_reset_and_apply_without_a_question(app, client, survey):
    html = client.get("/surveys/1/design").data.decode()
    reset = html.split('name="reset" value="1"', 1)[1].split(">", 1)[0]
    assert "data-confirm" not in reset and "Die jetzigen Farben gehen verloren" not in html
    page = client.post("/surveys/1/design", data={"reset": "1"}, follow_redirects=True).data.decode()
    assert "Erscheinungsbild auf Standard gesetzt." in page and 'class="flash-undo"' in page


def test_w30_image_pages_keep_the_frame_of_the_survey(app, client, survey):
    setup(client, display="both", visual=("a1", "a3"))
    for weg in ("hochladen", "ki"):
        html = client.get(E + f"/items/2/images?weg={weg}").data.decode()
        assert 'class="survey-tabs"' in html and 'class="undo-bar"' in html
        assert "Conjoint (CBC) · cbc</a>" in html


def test_w31_answer_texts_wide_price_fields_locked_while_off(app, client, survey):
    fill_consent(client)
    add(client, "acbc")
    client.post(E + "/items/2", data=acbc_form())
    html = client.get(E + "/items/2").data.decode()
    for name in ("nogo_none", "must_yes", "must_no", "price_none"):
        assert re.search(rf'name="{name}" value="[^"]*" class="field-name"', html)
    assert 'data-needs="price_on"' in html and "[data-needs]" in JS
    assert ".scale-labels:has(> li:nth-child(7))" in CSS


def test_w32_rows_ordered_the_same_way(app, client, survey):
    setup(client)
    html = client.get(E + "/items/2").data.decode()
    levels = html.split("data-cj-levels", 1)[1].split("</tbody>", 1)[0]
    assert levels.startswith(" data-row-list>")
    assert 'class="drag-handle row-drag"' in levels and 'data-row="up"' in levels and 'data-row="remove"' in levels
    fill_consent(client)
    add(client, "maxdiff")
    with app.app_context():
        md = db.session.scalar(db.select(Item).filter_by(type="maxdiff")).id
    html = client.get(f"{E}/items/{md}").data.decode()
    rows = html.split("data-md-rows", 1)[1].split("</tbody>", 1)[0]
    assert 'class="drag-handle row-drag"' in rows and "data-md-up" not in rows
    assert ".row-tools .icon-btn" in CSS or ".icon-btn {" in CSS  # 32 × 32 px buttons, at least 24 (WCAG 2.5.8)
    assert "X_ICON" in JS


def test_w33_likert_names_its_range(app, client, survey):
    html = client.get(E + "/").data.decode()  # the palette
    assert "Skala mit 2 bis 10 Stufen (üblich 5 oder 7)" in html and "4- bis 7-stufig" not in html


def test_w34_none_text_only_with_the_option_and_auto_next_in_the_display(app, client, survey):
    setup(client)
    html = client.get(E + "/items/2").data.decode()
    assert 'name="none_label"' not in html
    display = html.split('<fieldset data-cj-tab="display">', 1)[1].split("Erklärung vorab", 1)[0]
    assert 'name="auto_next"' in display
    client.post(E + "/items/2", data=MultiDict(list(conjoint_form().items(multi=True)) + [("none_mode", "option")]))
    with app.app_context():
        assert db.session.get(Item, 2).config["none"]
    html = client.get(E + "/items/2").data.decode()
    assert re.search(r'name="none_label" value="[^"]*" placeholder="Keine davon"', html)
    client.post(E + "/items/2", data=MultiDict(list(conjoint_form().items(multi=True)) + [("none_mode", "option"), ("none_label", "Keins")]))
    client.post(E + "/items/2", data=conjoint_form())  # "immer eine Option wählen": no field, the text stays
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"]["none_label"] == "Keins"


def test_w35_short_column_heads(app, client, survey):
    setup(client)
    html = client.get(E + "/items/2").data.decode()
    assert "Texte auf Deutsch (Sie)" in html and "Stufe (Deutsch (Sie))" not in html
    assert "Wie der Roboter aussieht" not in html
    assert ".list-table:where(.is-edit) th .hint { display: block;" in CSS


def test_w36_loop_example_is_grammatical(app, client, survey):
    tpl = (ROOT / "app" / "templates" / "editor" / "loop.html").read_text(encoding="utf-8")
    assert "einem humanoiden Roboter" in tpl and "an animal-like robot" in tpl
    assert "humanoider Roboter vertrauen" not in tpl and "a animal" not in tpl


def test_w37_several_images_dropped_on_the_page(app, client, survey):
    setup(client, display="both", visual=("a1", "a3"))
    html = client.get(E + "/items/2/images?weg=hochladen").data.decode()
    box = html.split("data-multi-upload", 1)[1]
    assert '<label class="dropzone multi-dropzone">' in box and "oder hierher ziehen" in box


def test_w38_dashboard_fields_with_titles_above(app, client, survey):
    client.post("/surveys/1/dashboards/new", data={"template": "results"})
    html = client.get("/surveys/1/dashboards/1").data.decode()
    head = html.split('class="panel board-name stack"', 1)[1].split("</section>", 1)[0]
    assert head.count('class="label-title"') == 2
    assert "Math.max(0.5, Math.min(1, box.clientWidth / WIDE))" in JS   # scaled to the box, no sideways scroll (X66)


def test_w39_one_empty_preview_line(app, client, survey):
    fill_consent(client)
    add(client, "maxdiff")
    frame = client.get(E + "/items/2/frame").data.decode()
    assert 'class="preview-empty"' in frame and "sobald der Versuchsplan erzeugt ist" in frame and "#tab-design" in frame
    assert "p-image-missing" not in frame
    add(client, "likert")
    with app.app_context():
        lk = db.session.scalar(db.select(Item).filter_by(type="likert"))
        lk.config = {**lk.config, "statements": []}
        db.session.commit()
        lk_id = lk.id
    frame = client.get(f"{E}/items/{lk_id}/frame").data.decode()
    assert "sobald die erste Aussage eingetragen ist" in frame


def test_w40_w41_palette_and_block_head(app, client, survey):
    assert "--left-w: 280px" in CSS and "def: 280" in JS and "-webkit-line-clamp: 2" in CSS
    html = client.get(E + "/").data.decode()
    assert "🔁 Schleife um diesen Block" in html  # since U21 of 2026-10-12 in the block's settings
    assert ".block-head > .block-form ~ .toolbar { grid-column: 1; grid-row: 2; flex-wrap: wrap; }" in CSS
