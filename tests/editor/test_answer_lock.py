"""What real answers mean stays on every way a question changes in a survey opened for editing again
(app/answer_lock.py; data audit 2026-10-05, T-32): undo and redo, carried options, the optional options of a
construct, linked elements and the quota assistant. The element editor itself: tests/editor/test_field_changes.py."""
import json

from werkzeug.datastructures import MultiDict

from app import answer_lock, census, undo
from app.extensions import db
from app.models import EditStep, Item, Response, Survey, User
from tests.conftest import add, fill_consent
from tests.editor.test_carry import build as carry_survey
from tests.editor.test_linked import _linked_question, _second_survey, choice

E = "/surveys/1/editor"


def _reopened_with_answers(app, answers):
    """The survey ran (real answers `answers`: {item id: answer}) and was opened for editing again."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.qversion = 2
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, pid="p1",
                                qversion=1, answers={str(k): v for k, v in answers.items()}, layout={}))
        db.session.commit()


def _likert(client, points="5", text="A"):
    return client.post(f"{E}/items/2", data=MultiDict([
        ("code", "L"), ("question", "Wie sehr?"), ("points", points), ("labeling", "full"),
        ("stmt_id", "s1"), ("stmt_code", "s1c"), ("stmt_text", text), ("stmt_id", "s2"), ("stmt_code", "s2c"),
        ("stmt_text", "B")]), follow_redirects=True).data.decode()


# --- the rule ---------------------------------------------------------------------------------------------------


def test_the_rule_asks_the_database_only_for_a_changed_shape(app, client, survey):
    add(client, "likert")
    _likert(client)
    _reopened_with_answers(app, {2: {"s1": 5}})
    with app.app_context():
        s, item = db.session.get(Survey, 1), db.session.get(Item, 2)
        before = answer_lock.shape(item)
        assert answer_lock.refused_shape(s, item, before, before) == []
        seven = answer_lock.shape_of(item, item.code, {**item.config, "points": 7})
        assert answer_lock.refused_shape(s, item, before, seven) == ["die Stufenzahl bzw. der Wertebereich"]
        assert answer_lock.refused_shape(s, item, before, answer_lock.shape_of(item, "L2", item.config)) == ["der Variablenname"]
        s.qversion = 1  # before the first start nothing is fixed
        assert answer_lock.refused_shape(s, item, before, seven) == []


# --- undo and redo ----------------------------------------------------------------------------------------------


def _step(app, label, item_id, before, after, undone=False):
    """A step in the history of the logged-in person: question `item_id` from state `before` to `after`."""
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        db.session.add(EditStep(survey_id=1, user_id=user.id, label=label, undone=undone, need="editor",
                                data={"parts": {f"item:{item_id}": [before, after]}, "fresh": [], "page": "item",
                                      "item": item_id}))
        db.session.commit()


def _item_state(app, item_id, **config):
    with app.app_context():
        state = json.loads(undo._state(db.session.get(Survey, 1))[f"item:{item_id}"])
    state["config"].update(config)
    return state


def test_undo_does_not_put_back_a_fixed_shape_and_keeps_the_rest(app, client, survey):
    """A step from before the rule held on every way (or from a page that checks nothing) would turn 5 points into 7
    by undo: it is refused, leaves the history, and the steps before it can still be taken back."""
    add(client, "likert")                                                   # 2
    _likert(client)
    add(client, "text_input")                                               # 3: an earlier step of its own
    _reopened_with_answers(app, {2: {"s1": 5, "s2": 1}})
    with app.app_context():
        db.session.execute(db.delete(EditStep))
        db.session.commit()
    client.post(f"{E}/items/3", data={"code": "frei", "question": "Noch etwas?"})
    now = _item_state(app, 2)
    _step(app, "„L“ geändert", 2, _item_state(app, 2, points=7), now)
    page = client.post(f"{E}/undo", follow_redirects=True).data.decode()
    assert "„L“ hat schon echte Antworten" in page and "lässt sich darum nicht zurücknehmen" in page
    with app.app_context():
        assert db.session.get(Item, 2).config["points"] == 5
        assert [s.label for s in db.session.scalars(db.select(EditStep))] == ["„frei“ bearbeitet"]
    assert "Zurückgenommen: „frei“ bearbeitet." in client.post(f"{E}/undo", follow_redirects=True).data.decode()
    with app.app_context():
        assert db.session.get(Item, 3).code != "frei"


def test_redo_does_not_put_back_a_fixed_shape(app, client, survey):
    add(client, "likert")
    _likert(client)
    _reopened_with_answers(app, {2: {"s1": 5}})
    now = _item_state(app, 2)
    renamed = {**_item_state(app, 2), "code": "L_neu"}
    _step(app, "„L“ umbenannt", 2, now, renamed, undone=True)
    page = client.post(f"{E}/redo", follow_redirects=True).data.decode()
    assert "der Variablenname" in page and "lässt sich darum nicht wiederholen" in page
    with app.app_context():
        assert db.session.get(Item, 2).code == "L"
        steps = db.session.scalars(db.select(EditStep)).all()
        assert steps and not [s for s in steps if s.undone]                 # what can be taken back stays


def test_undo_of_text_corrections_still_works(app, client, survey):
    add(client, "likert")
    _likert(client)
    _reopened_with_answers(app, {2: {"s1": 5}})
    _likert(client, text="A neu")
    assert "Zurückgenommen" in client.post(f"{E}/undo", follow_redirects=True).data.decode()
    with app.app_context():
        assert db.session.get(Item, 2).text("statements")["s1"] == "A"


# --- carried options --------------------------------------------------------------------------------------------


def _codes(app, item_id):
    with app.app_context():
        return [(o["id"], o["code"]) for o in db.session.get(Item, item_id).config["options"]]


def test_carried_rows_with_real_answers_stay(app, client, survey):
    """Question 4 shows the devices chosen in question 2. With real answers to it, the editor no longer takes its
    carried rows away (no source), and a new code or a removed option in question 2 does not reach it; a new
    option and new wording still do."""
    carry_survey(app, client)
    before = _codes(app, 4)
    assert before == [("cf_a", "1"), ("cf_b", "2"), ("cf_c", "3"), ("cf_d", "9"), ("x", "0")]
    _reopened_with_answers(app, {4: "2"})
    page = client.post(f"{E}/items/4", data=MultiDict([
        ("code", "meist"), ("question", "Welches am häufigsten?"), ("carry_item", ""),
        ("option_id", "x"), ("option_code", "0"), ("option_label", "keines davon")]), follow_redirects=True).data.decode()
    assert "„meist“ hat schon echte Antworten" in page
    with app.app_context():
        assert db.session.get(Item, 4).config["carry"] == {"item": 2, "which": "chosen"}
    assert _codes(app, 4) == before
    client.post(f"{E}/items/2", data=MultiDict([
        ("code", "geraete"), ("question", "Welche Geräte nutzen Sie?"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Handy"),
        ("option_id", "b"), ("option_code", "5"), ("option_label", "Tablet"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Laptop")]))
    assert _codes(app, 4) == before                                         # code 2 -> 5 and d removed: kept
    with app.app_context():
        assert db.session.get(Item, 4).text("options")["cf_a"] == "Handy"  # the wording follows
        assert [o["code"] for o in db.session.get(Item, 2).config["options"]] == ["1", "5", "3"]
    client.post(f"{E}/items/2", data=MultiDict([
        ("code", "geraete"), ("question", "Welche Geräte nutzen Sie?"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Handy"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Tablet"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Laptop"),
        ("option_id", "d"), ("option_code", "9"), ("option_label", "Anderes, und zwar"),
        ("option_id", "e"), ("option_code", "4"), ("option_label", "Uhr")]))
    assert _codes(app, 4) == [("cf_a", "1"), ("cf_b", "2"), ("cf_c", "3"), ("cf_d", "9"), ("cf_e", "4"), ("x", "0")]


# --- optional options of a construct ----------------------------------------------------------------------------


def test_optional_option_with_real_answers_stays(app, client, survey):
    client.post(f"{E}/blocks/2/items", data={"type": "construct:gender"})   # 2
    _reopened_with_answers(app, {2: "4"})
    with app.app_context():
        question = db.session.get(Item, 2).texts["de"]["question"]
    page = client.post(f"{E}/items/2", data={"code": "gender", "answer_mode": "optional", "question": question,
                                             "optional_options_shown": "1"}, follow_redirects=True).data.decode()
    assert "„gender“ hat schon echte Antworten" in page
    assert [o for o, _ in _codes(app, 2)] == ["o1", "o2", "o3", "o5", "o4"]


# --- linked elements --------------------------------------------------------------------------------------------


def test_linked_question_with_real_answers_keeps_its_fassung(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        block = _second_survey(client)
    client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
    with app.app_context():
        other_id = db.session.scalars(db.select(Item).filter(Item.config["construct"]["key"].as_string() == key,
                                                             Item.id != 2)).one().id
    _reopened_with_answers(app, {2: "1"})
    client.post(f"/surveys/2/editor/items/{other_id}/unlock")
    choice(client, 2, other_id, yes="Ja", code="g", extra=[("option_id", "c"), ("option_code", "3"), ("option_label", "?")])
    client.post(f"/surveys/2/editor/items/{other_id}/new-version")
    page = client.post(f"{E}/items/2/switch-version", follow_redirects=True).data.decode()
    assert "schon echte Teilnahmen" in page
    assert _codes(app, 2) == [("a", "1"), ("b", "2")]


# --- the quota assistant ----------------------------------------------------------------------------------------


def test_census_keeps_the_options_of_a_state_question_with_real_answers(app, client, survey):
    """The assistant gives its element Bundesland the option "abroad" and no "keine Angabe". With real answers the
    options stay; "abroad" (needed by the screen-out) is added."""
    fill_consent(client)
    add(client, "federal_state")                                            # 2
    with app.app_context():
        item = db.session.get(Item, 2)
        from app.items import federal_state

        item.config = {**item.config, "options": federal_state._options(False, True)}
        db.session.commit()
    _reopened_with_answers(app, {2: "99"})
    client.post("/surveys/1/flow/quotas/assistant", data={"n": "1000"})
    with app.app_context():
        codes = [o["code"] for o in db.session.get(Item, 2).config["options"]]
        assert codes[-2:] == ["99", "0"] and census.questions(db.session.get(Survey, 1))["state"].id == 2


def _answered_question(app, client):
    """Item 2 (text input "why") with a real answer in a survey opened for editing again; item 3 without answers in
    the same block."""
    fill_consent(client)
    add(client, "text_input")                                     # 2
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?"})
    add(client, "text_input")                                     # 3
    client.post(E + "/items/3", data={"code": "more", "question": "Noch etwas?"})
    _reopened_with_answers(app, {2: "Antwort 0"})


def test_deleting_a_question_with_real_answers_asks_first(app, client, survey):
    """Gesamtaudit 2026-10-18, FUNC-05: a question with real answers went without a word in a survey opened for
    editing again; seven days later its column was gone from data set, results and exports. Now the button asks
    (data-confirm), the server deletes only with the dialog's field, and the entry stays in the bin."""
    from datetime import timedelta

    from app import trash
    from app.models import utcnow

    _answered_question(app, client)
    page = client.get(E + "/").data.decode()
    form = page[page.index('action="/surveys/1/editor/items/2/delete'):]
    form = form[:form.index("</form>")]
    assert "data-confirm=" in form and f'name="{answer_lock.CONFIRM_FIELD}"' in form
    other = page[page.index('action="/surveys/1/editor/items/3/delete'):]
    assert "data-confirm=" not in other[:other.index("</form>")]  # no answers: no question
    r = client.post(E + "/items/2/delete", follow_redirects=True)
    assert "wurde nicht gelöscht" in r.data.decode()
    with app.app_context():
        assert db.session.get(Item, 2) is not None
    client.post(E + "/items/2/delete", data={answer_lock.CONFIRM_FIELD: "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert db.session.get(Item, 2) is None
        entry = next(e for e in s.trash if e.data.get("code") == "why")
        assert entry.data.get("answered") and trash.expires(entry) is None
        trash.purge(s, now=utcnow() + timedelta(days=trash.TRASH_DAYS + 1))
        db.session.commit()
        assert any(e.data.get("code") == "why" for e in db.session.get(Survey, 1).trash)  # restorable for good
    page = client.get(E + "/").data.decode()
    assert "bleibt, bis Sie es endgültig löschen" in page
    client.post(E + "/items/3/delete")  # a question without answers goes as before, for TRASH_DAYS
    with app.app_context():
        entry = next(e for e in db.session.get(Survey, 1).trash if e.data.get("code") == "more")
        assert trash.expires(entry) is not None


def test_deleting_a_block_with_answered_questions_asks_first(app, client, survey):
    """FUNC-05: the same for a block that holds such a question."""
    _answered_question(app, client)
    with app.app_context():
        bid = db.session.get(Item, 2).block_id
    page = client.get(E + "/").data.decode()
    form = page[page.index(f'action="/surveys/1/editor/blocks/{bid}/delete'):]
    assert "data-confirm=" in form[:form.index("</form>")]
    client.post(E + f"/blocks/{bid}/delete")
    with app.app_context():
        assert db.session.get(Item, 2) is not None
    client.post(E + f"/blocks/{bid}/delete", data={answer_lock.CONFIRM_FIELD: "1"})
    with app.app_context():
        assert db.session.get(Item, 2) is None
        assert next(e for e in db.session.get(Survey, 1).trash if e.data.get("code") == "why").data.get("answered")
