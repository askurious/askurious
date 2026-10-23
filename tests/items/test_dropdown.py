"""Single choice as a drop-down list (editor: "als Dropdown-Liste anzeigen"), also for the types built on it."""
import pytest
from werkzeug.datastructures import MultiDict

from app import export, runtime
from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Survey
from tests.conftest import add, fill_consent

OPTIONS = [("option_id", "a"), ("option_code", "1"), ("option_label", "Auto"),
           ("option_id", "b"), ("option_code", "2"), ("option_label", "Rad < 5 km"),
           ("option_id", "c"), ("option_code", "3"), ("option_label", "Sonstiges, und zwar:"), ("option_text", "c")]


def _save(client, *extra, mode="optional"):
    client.post("/surveys/1/editor/items/2", data=MultiDict(
        [("code", "sc"), ("question", "Womit?"), ("answer_mode", mode)] + OPTIONS + list(extra)))


def _page(client):
    return client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()


def _list(html):
    """The drop-down list of item 2 (the preview page has a list of its own for comments)."""
    return html.split('<select id="s2"')[1].split("</select>")[0]


def test_radio_buttons_stay_the_default(app, client, survey):
    fill_consent(client)
    add(client, "single_choice")                                    # 2
    _save(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert "display" not in item.config and not item.kind.dropdown(item)
    html = _page(client)
    assert html.count('type="radio" name="i2"') == 3 and '<select id="s2"' not in html
    editor = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="presentation" value="dropdown"' in editor and 'value="dropdown" data-reload-saved checked' not in editor
    assert 'name="presentation" value="vertical" data-reload-saved checked' in editor


def test_dropdown_shows_a_list_with_the_same_fields_and_codes(app, client, survey):
    fill_consent(client)
    add(client, "single_choice")
    _save(client, ("display", "dropdown"), ("layout", "horizontal"), mode="required")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["display"] == "dropdown" and item.kind.dropdown(item) and item.kind.validate(item) == []
    assert 'name="presentation" value="dropdown" data-reload-saved checked' in client.get("/surveys/1/editor/items/2").data.decode()
    html = _page(client)
    assert 'type="radio"' not in html
    assert '<select id="s2" name="i2" autocomplete="off" aria-required="true">' in html
    assert 'id="q2" for="s2"' in html                               # the question is the label of the list
    assert '<option value="">Bitte auswählen</option>' in html      # nothing chosen at first
    assert '<option value="2" >Rad &lt; 5 km</option>' in html
    assert _list(html).count("<option") == 4 and " selected" not in _list(html)
    # the text field of "Sonstiges" follows the list under its usual name
    assert 'name="i2_t_c"' in html and 'data-select-text="3"' in html
    # unticking brings the radio buttons back and leaves no key behind
    _save(client)
    with app.app_context():
        assert "display" not in db.session.get(Item, 2).config
    assert 'type="radio" name="i2"' in _page(client)


def test_answers_are_read_as_with_radio_buttons(app, client, survey):
    add(client, "single_choice")
    _save(client, ("display", "dropdown"))
    with app.app_context():
        item = db.session.get(Item, 2)
        parse = lambda form, lang="de": item.kind.parse_answer(item, MultiDict(form), lang)  # noqa: E731
        assert parse({"i2": "2"}) == "2"
        assert parse({"i2": ""}) is None                             # "Bitte auswählen" is no answer
        assert parse({"i2": "3", "i2_t_c": "Bahn"}) == {"v": "3", "t": {"3": "Bahn"}}
        assert parse({"i2": "1", "i2_t_c": "Bahn"}) == "1"           # the hidden field of another option
        with pytest.raises(AnswerError, match="Ungültige Antwort"):
            parse({"i2": "9"})
        item.answer_mode = "required"
        with pytest.raises(AnswerError, match="Bitte wählen"):
            parse({"i2": ""})
        assert item.kind.export_columns(item)[0][0] == "sc" and item.kind.export_values(item, "2")["sc"] == "2"
        assert "- `sc` (Einfachauswahl, required, drop-down list): Womit?" in export.questionnaire_md(db.session.get(Survey, 1))


def test_chosen_answer_and_text_come_back(app, client, survey):
    fill_consent(client)
    add(client, "single_choice")
    _save(client, ("display", "dropdown"))
    with app.app_context():
        item = db.session.get(Item, 2)
        html = app.jinja_env.get_template("participant/_item.html").render(
            item=item, lang="en", answers={2: {"v": "3", "t": {"3": "Bahn"}}}, errors={}, variables={})
    assert '<option value="3" selected>' in html and 'value="Bahn"' in html
    assert ">Please select</option>" in html


def test_no_automatic_continue_on_a_list(app, client, survey):
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict(
        [("code", "sc"), ("question", "Womit?"), ("answer_mode", "optional")] + OPTIONS[:6]))
    with app.app_context():
        item = db.session.get(Item, 2)
        item.survey.auto_advance = True
        assert item.kind.one_click(item) and runtime.auto_advance_ok(item.survey, [item])
        item.config = {**item.config, "display": "dropdown"}
        assert not item.kind.one_click(item) and not runtime.auto_advance_ok(item.survey, [item])


def test_federal_state_as_a_list(app, client, survey):
    fill_consent(client)
    add(client, "federal_state")
    client.post("/surveys/1/editor/items/2", data={"code": "land", "question": "Ihr Bundesland?", "answer_mode": "required",
                                                   "abroad": "1", "display": "dropdown"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.dropdown(item) and len(item.config["options"]) == 17
    html = _page(client)
    assert _list(html).count("<option") == 18 and '<option value="9" >Bayern</option>' in html and 'type="radio"' not in html


def test_locked_construct_can_switch_and_keeps_its_options(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gender"})
    with app.app_context():
        item = db.session.get(Item, 2)
        before, code, question = item.config["options"], item.code, item.text("question", "de")
    client.post("/surveys/1/editor/items/2", data={"code": code, "question": question, "answer_mode": "optional",
                                                   "display": "dropdown"})
    with app.app_context():
        from app import constructs

        item = db.session.get(Item, 2)
        assert constructs.locked(item) and item.kind.dropdown(item) and item.config["options"] == before
        assert not constructs.info(item)["modifications"]            # the presentation is no deviation


def test_running_survey_keeps_its_display(app, client, survey):
    add(client, "single_choice")
    _save(client, ("display", "dropdown"))
    with app.app_context():
        db.session.get(Survey, 1).status = "running"
        db.session.commit()
    _save(client)                                                    # the tick is disabled and not sent
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.dropdown(item)
    assert 'name="presentation" value="dropdown" data-reload-saved checked disabled' in client.get("/surveys/1/editor/items/2").data.decode()


def test_multiple_choice_has_no_list(app, client, survey):
    add(client, "multiple_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict(
        [("code", "mc"), ("question", "Was?"), ("answer_mode", "optional"), ("display", "dropdown")] + OPTIONS))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert "display" not in item.config and not item.kind.dropdown(item)
    editor = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="display"' not in editor and 'value="dropdown"' not in editor


def test_live_preview_follows_the_tick(app, client, survey):
    add(client, "single_choice")
    _save(client)
    html = client.post("/surveys/1/editor/items/2/frame", data=MultiDict(
        [("code", "sc"), ("question", "Womit?"), ("answer_mode", "optional"), ("display", "dropdown")] + OPTIONS)).data.decode()
    assert "<select" in html and 'type="radio"' not in html
    with app.app_context():
        assert "display" not in db.session.get(Item, 2).config       # the trial is not saved


def test_one_field_presentation_sets_layout_list_and_images(app, client, survey):
    """"Darstellung" (V85): one choice of five instead of three ticks that could all be on at once."""
    add(client, "single_choice")
    _save(client, ("presentation", "horizontal"), ("images_shown", "1"))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["layout"] == "horizontal" and not item.kind.dropdown(item) and not item.config.get("images")
    _save(client, ("presentation", "dropdown"), ("images_shown", "1"))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["layout"] == "vertical" and item.kind.dropdown(item) and not item.config.get("images")
    _save(client, ("presentation", "images"), ("images_shown", "1"))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["images"] and not item.kind.dropdown(item)
    editor = client.get("/surveys/1/editor/items/2").data.decode()
    assert editor.count('name="presentation"') == 5 and 'value="images" data-reload-saved checked' in editor
    assert 'name="layout"' not in editor and 'name="images"' not in editor


def test_multiple_choice_presentation_has_no_list(app, client, survey):
    add(client, "multiple_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict(
        [("code", "mc"), ("question", "Was?"), ("answer_mode", "optional"), ("presentation", "horizontal"),
         ("images_shown", "1")] + OPTIONS))
    with app.app_context():
        assert db.session.get(Item, 2).config["layout"] == "horizontal"
    editor = client.get("/surveys/1/editor/items/2").data.decode()
    assert editor.count('name="presentation"') == 3 and 'value="horizontal" data-reload-saved checked' in editor
