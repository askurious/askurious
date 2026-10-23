"""Single choice as a search field with suggestions (config "display" = "search") and the editor's field
"Liste einfügen" that adds long lists of options at once (items/choice.py)."""
from werkzeug.datastructures import MultiDict

from app import export, runtime
from app.extensions import db
from app.items.choice import MAX_OPTIONS
from app.models import Item, Survey
from tests.conftest import add, fill_consent

OPTIONS = [("option_id", "a"), ("option_code", "1"), ("option_label", "Bäcker/in"),
           ("option_id", "c"), ("option_code", "99"), ("option_label", "nicht in der Liste, und zwar:"), ("option_text", "c")]


def _save(client, *extra, options=OPTIONS):
    client.post("/surveys/1/editor/items/2", data=MultiDict(
        [("code", "beruf"), ("question", "Ihr Beruf?"), ("answer_mode", "optional")] + options + list(extra)))


def _item():
    return db.session.get(Item, 2)


def test_search_field_keeps_the_list_and_codes(app, client, survey):
    fill_consent(client)
    add(client, "single_choice")                                    # 2
    _save(client, ("presentation", "search"))
    with app.app_context():
        item = _item()
        assert item.config["display"] == "search" and item.kind.search(item) and not item.kind.dropdown(item)
        assert not item.kind.one_click(item) and item.kind.validate(item) == []
        assert "- `beruf` (Einfachauswahl, optional, search field): Ihr Beruf?" in export.questionnaire_md(db.session.get(Survey, 1))
    editor = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="presentation" value="search" data-reload-saved checked' in editor
    assert 'name="options_paste"' in editor and 'data-table-file="paste2"' in editor
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    # the list stays the field that is sent (and what shows without JavaScript); the script adds the search field
    assert 'data-select data-search>' in html and '<select id="s2" name="i2"' in html and 'type="radio" name="i2"' not in html
    assert 'data-select-text="99"' in html
    # back to radio buttons leaves no key behind
    _save(client, ("presentation", "vertical"))
    with app.app_context():
        assert "display" not in _item().config


def test_answers_are_read_as_with_radio_buttons(app, client, survey):
    add(client, "single_choice")
    _save(client, ("presentation", "search"))
    with app.app_context():
        item = _item()
        parse = lambda form: item.kind.parse_answer(item, MultiDict(form), "de")  # noqa: E731
        assert parse({"i2": "1"}) == "1" and parse({"i2": ""}) is None
        assert parse({"i2": "99", "i2_t_c": "Imker"}) == {"v": "99", "t": {"99": "Imker"}}
        item.survey.auto_advance = True
        assert not runtime.auto_advance_ok(item.survey, [item])


def test_pasted_list_adds_options_with_codes(app, client, survey):
    add(client, "single_choice")
    paste = "Koch/Köchin\n7123;Konditor/in\n\nbäcker/IN\n1;Maurer/in\nTischler/in\n"
    _save(client, ("presentation", "search"), ("options_paste", paste))
    with app.app_context():
        item = _item()
        labels = item.texts["de"]["options"]
        got = [(o["code"], labels[o["id"]]) for o in item.config["options"]]
        # the line already there (other case) is left out, a code already used gets the next free number
        assert got == [("1", "Bäcker/in"), ("99", "nicht in der Liste, und zwar:"), ("100", "Koch/Köchin"),
                       ("7123", "Konditor/in"), ("7124", "Maurer/in"), ("7125", "Tischler/in")]


def test_pasted_tabs_from_a_spreadsheet(app, client, survey):
    add(client, "single_choice")
    _save(client, ("options_paste", "11\tAachen\n12\tKöln; Stadt\n"), options=[])
    with app.app_context():
        item = _item()
        labels = item.texts["de"]["options"]
        assert [(o["code"], labels[o["id"]]) for o in item.config["options"]] == [("11", "Aachen"), ("12", "Köln; Stadt")]


def test_long_list_is_capped(app, client, survey):
    add(client, "single_choice")
    _save(client, ("options_paste", "\n".join(f"Beruf {n}" for n in range(MAX_OPTIONS + 10))), options=[])
    with app.app_context():
        assert len(_item().config["options"]) == MAX_OPTIONS


def test_federal_state_offers_no_search(app, client, survey):
    add(client, "federal_state")
    client.post("/surveys/1/editor/items/2", data={"code": "land", "question": "Ihr Bundesland?", "answer_mode": "optional",
                                                   "presentation": "search"})
    with app.app_context():
        item = _item()
        assert "display" not in item.config and not item.kind.search(item)
    assert 'value="search"' not in client.get("/surveys/1/editor/items/2").data.decode()
