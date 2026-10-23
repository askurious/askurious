"""UI audit 2026-10-12: U10 (one fold button), U18 (quota conditions as ticks, the × at the condition),
U34 (weekday chips, time fields with a pattern), U38 (federal states as a list on phones); tab groups without headings."""
import os
import re

from app import conditions, quotas
from app.extensions import db
from app.models import Item, Quota, QuotaCell, Survey
from tests.conftest import add
from tests.diary.test_diary import _new as new_diary
from tests.quotas.test_quotas import Q, questionnaire
from tests.exports.test_transfer import _file, _upload

STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), os.pardir, "app", "static")
CHOICE = {"kind": "choice", "options": [("1", "weiblich"), ("2", "männlich"), ("3", "divers"), ("4", "keine Angabe")],
          "multi": False, "ordered": False}


def _css(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


# --- U18 ---------------------------------------------------------------------------------------------------------

def test_an_ordered_comparison_on_answers_without_an_order_becomes_their_codes():
    cond = {"item": 25, "op": "ge", "value": "3"}
    assert conditions.as_codes(cond, CHOICE) == {"item": 25, "op": "in", "value": "3, 4"}
    assert conditions.as_codes({"item": 25, "op": "ge", "value": "4"}, CHOICE) == {"item": 25, "op": "eq", "value": "4"}
    assert conditions.as_codes({"item": 25, "op": "lt", "value": "3"}, CHOICE)["value"] == "1, 2"
    assert conditions.as_codes({"item": 25, "op": "between", "value": "2..3"}, CHOICE)["value"] == "2, 3"
    assert conditions.as_codes({"item": 25, "op": "ge", "value": "3"}, {**CHOICE, "multi": True})["op"] == "in"


def test_what_is_not_such_a_comparison_stays_as_it_is():
    for cond, info in [({"item": 25, "op": "ge", "value": "3"}, {**CHOICE, "ordered": True}),        # ordinal: "ab" stays
                       ({"item": 25, "op": "eq", "value": "3"}, CHOICE),
                       ({"item": 25, "op": "ge", "value": "9"}, CHOICE),                                # takes in nothing
                       ({"item": 25, "op": "ge", "value": "x"}, CHOICE),
                       ({"item": 25, "op": "ge", "value": "3"}, {**CHOICE, "options": [("a", "A"), ("3", "B")]}),
                       ({"item": 3, "op": "ge", "value": "50"}, {"kind": "number", "options": []}),
                       ({"item": 3, "op": "ge", "value": "50"}, None)]:
        assert conditions.as_codes(cond, info) == cond


def _ab_cell(app):
    """A quota on gender (2: 1 männlich, 2 weiblich) with a cell "ab 2", as an older file stored it."""
    with app.app_context():
        q = Quota(survey_id=1, code="g", name="Geschlecht", mode="hard")
        q.cells = [QuotaCell(code="w", name="weiblich", conds=[{"item": 2, "op": "ge", "value": "2"}], target=10)]
        db.session.add(q)
        db.session.commit()
        return q.id


def test_the_quota_page_ticks_the_answers_of_such_a_condition(app, client, survey):
    questionnaire(client)
    qid = _ab_cell(app)
    with app.app_context():
        described = quotas.describe(db.session.get(Survey, 1))
        assert described["2"]["ordered"] is False
        view = quotas.cond_view({"item": 2, "op": "ge", "value": "2"}, described)
        assert (view["special"], view["mode"], view["codes"]) == (False, "is", ["2"])
    page = client.get(Q + "/").get_data(as_text=True)
    assert qid
    assert 'name="c0_k0_codes" value="2" checked' in page and "ab weiblich" not in page.split('class="cond-line"', 1)[1][:3000]
    # the × of the condition comes right after its question, before the answers, with its words for the eye
    line = page[page.index('class="cond-line"'):]
    assert line.index("data-cond-remove") < line.index("data-cond-value") < line.index('name="c0_k0_codes"')
    assert ".quota-page .cond-line > [data-cond-remove]::after { content: attr(title); }" in _css("app.css")


def test_an_ordinal_question_keeps_its_order(app, client, survey):
    questionnaire(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**(item.config or {}), "level": "ordinal"}
        db.session.commit()
        assert quotas.describe(db.session.get(Survey, 1))["2"]["ordered"] is True


def test_a_survey_file_brings_such_a_condition_as_ticks(app, client, survey):
    questionnaire(client)
    _ab_cell(app)
    data = _file(client)
    assert data["quotas"][0]["cells"][0]["conds"] == [{"item": 2, "op": "ge", "value": "2"}]
    _upload(client, data, title="Kopie")
    with app.app_context():
        new = db.session.scalars(db.select(Survey).order_by(Survey.id.desc())).first()
        assert new.id != 1
        gender = next(i for b in new.blocks for i in b.items if i.code == "gender")
        assert new.quotas[0].cells[0].conds == [{"item": gender.id, "op": "eq", "value": "2"}]


# --- U19 ---------------------------------------------------------------------------------------------------------

def test_the_schedule_has_weekday_chips_and_times_with_a_pattern(app, client):
    code = new_diary(app, client, running=False)
    page = client.get(f"/diary/{code}/schedule").get_data(as_text=True)
    assert 'class="weekday-row weekday-chips"' in page and page.count('name="m1_weekdays"') == 7
    times = re.search(r'<input name="m1_times"[^>]*>', page).group(0)
    assert 'pattern="' in times and 'placeholder="09:00, 14:00, 19:00"' in times
    pattern = re.search(r'pattern="([^"]+)"', times).group(1)
    assert re.fullmatch(pattern, "09:00, 14:00, 19:00") and not re.fullmatch(pattern, "9 Uhr")
    windows = re.search(r'<input name="m1_windows"[^>]*>', page).group(0)
    pattern = re.search(r'pattern="([^"]+)"', windows).group(1).replace("\\-", "-")
    assert re.fullmatch(pattern, "09:00-12:00, 14:00-24:00") and not re.fullmatch(pattern, "09:00")
    assert "0 = läuft nie ab" in page and ".weekday-chips .weekday:has(input:checked)" in _css("app.css")


# --- U38 ---------------------------------------------------------------------------------------------------------

def test_on_a_phone_the_states_are_a_list_and_the_map_only_shows_the_choice():
    css = _css("participant.css")
    wide = css[css.index("@media (min-width: 600px) {"):]
    assert wide.index(".p-state.has-map [data-state-option]") < wide.index("}")              # hidden only from 600 px
    narrow = css[css.index("@media (max-width: 599.98px) {"):]
    narrow = narrow[:narrow.index("\n}")]
    assert ".p-state-map svg { pointer-events: none; }" in narrow and ".p-state-label { display: none; }" in narrow


# --- U10 ---------------------------------------------------------------------------------------------------------

def test_one_fold_button_on_the_settings_and_the_data_collection_page(app, client, survey):
    for url in ("/surveys/1", "/surveys/1/field"):
        page = client.get(url).get_data(as_text=True)
        assert page.count("data-fold-all") == 1, url
        assert 'data-label-open="Alle aufklappen"' in page and 'data-label-close="Alle zuklappen"' in page
        assert 'data-fold-all="open"' not in page and 'aria-expanded="true"' in page
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert "btn.dataset.foldAction = closed ? \"open\" : \"close\";" in js and "btn.hidden = hide" not in js


def test_the_tab_groups_have_no_visible_headings(app, client, survey):
    """Ralf, 2026-10-12: no headings Planen / Erheben / Auswerten over the tabs; the groups keep their names
    for screen readers (aria-label) and as option groups of the list on phones."""
    page = client.get("/surveys/1").get_data(as_text=True)
    nav = page[page.index('<nav class="survey-tabs"'):page.index("</nav>", page.index('<nav class="survey-tabs"'))]
    assert "tab-group-label" not in page and ">Planen<" not in nav
    assert nav.count('class="tab-group"') == 3 and 'aria-label="Planen"' in nav
    assert '<optgroup label="Planen">' in page
    # a line after "Einstellungen" again, inside the group (Ralf, 2026-10-17)
    assert re.search(r'Einstellungen</a>\s*<span class="tab-group-sep" aria-hidden="true"></span>', nav) and nav.count("tab-group-sep") == 1
    assert ".tab-group-label" not in _css("app.css")
