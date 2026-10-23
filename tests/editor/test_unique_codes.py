"""Variable names stay unique and renamings keep what others saved: the names taken are read from the database
under the survey's row (edit_guard.taken_codes), not from
the questionnaire a request loaded before; a renaming holds the survey first, then the question, then the questions
whose texts follow the new name, and an editor of one of those opened before gets a conflict. Undo/redo and the
block routes hold their rows before reading too."""
from flask import request
from werkzeug.datastructures import MultiDict

from app import edit_guard
from app.editor import elements
from app.extensions import db
from app.models import Block, Item, Survey
from tests.conftest import add
from tests.editor.test_autosave import E, auto, form_of
from tests.editor.test_edit_guard_lock import _second_person


def _spy(monkeypatch):
    """Record what edit_guard.lock holds, in order: (class name, id)."""
    real, held = edit_guard.lock, []

    def lock(obj):
        held.append((type(obj).__name__, obj.id))
        return real(obj)

    monkeypatch.setattr(edit_guard, "lock", lock)
    return held


def _code(app, item_id):
    with app.app_context():
        return db.session.get(Item, item_id).code


def test_a_name_taken_in_the_database_but_not_in_the_loaded_questionnaire_is_refused(app, client, survey, monkeypatch):
    """Another member renamed item 2 to "alter" after this request loaded the questionnaire: renaming item 3 to "alter"
    is refused, though the request's own objects still show the old name."""
    add(client, "text_input")
    add(client, "text_input")
    form = form_of(client, 3)
    real = elements._get_item

    def renamed_meanwhile(survey, item_id):
        item = real(survey, item_id)
        if request.method == "POST":  # a commit of someone else: the row changes, the loaded objects do not
            db.session.execute(db.update(Item).where(Item.id == 2).values(code="alter")
                               .execution_options(synchronize_session=False))
            assert db.session.get(Item, 2).code != "alter"
        return item

    monkeypatch.setattr(elements, "_get_item", renamed_meanwhile)
    form["code"] = "alter"
    r = auto(client, 3, form)
    assert r.get_json()["status"] == "saved"
    assert "schon vergeben" in r.get_json()["code_problem"]
    assert _code(app, 3) != "alter" and _code(app, 2) == "alter"


def test_taken_codes_reads_the_database_and_holds_the_survey(app, client, survey, monkeypatch):
    add(client, "text_input")
    held = []
    monkeypatch.setattr(edit_guard, "hold", lambda obj: held.append((type(obj).__name__, obj.id)))
    with app.app_context():
        s = db.session.get(Survey, 1)
        loaded = {i.code for b in s.blocks for i in b.items}
        db.session.execute(db.update(Item).where(Item.id == 2).values(code="neu")
                           .execution_options(synchronize_session=False))
        taken = edit_guard.taken_codes(s)
        assert "neu" in taken and "neu" not in loaded
        assert "neu" not in edit_guard.taken_codes(s, but={2})
        new = Item(type="text_input", code="frisch", position=9)
        s.blocks[-1].items.append(new)  # not stored yet: counts too
        assert "frisch" in edit_guard.taken_codes(s)
        db.session.rollback()
    assert held and held[0] == ("Survey", 1)


def test_a_new_question_takes_a_name_given_meanwhile_into_account(app, client, survey, monkeypatch):
    """_unique_code (palette, AI help, live, diary) reads under the survey's row: a "Q2" stored by someone else after
    the questionnaire was loaded is not given a second time."""
    add(client, "text_input")
    with app.app_context():
        first = db.session.get(Item, 2).code
        s = db.session.get(Survey, 1)
        _ = [i.code for b in s.blocks for i in b.items]  # loaded
        db.session.execute(db.update(Item).where(Item.id == 2).values(code="Q2")
                           .execution_options(synchronize_session=False))
        from app.editor import _unique_code

        assert _unique_code(s, "Q") != "Q2"
        db.session.rollback()
    assert first


def test_renaming_holds_survey_then_question_then_the_texts_that_follow(app, client, survey, monkeypatch):
    add(client, "text_input")
    add(client, "info")
    old = _code(app, 2)
    with app.app_context():
        info = db.session.get(Item, 3)
        info.texts = {**(info.texts or {}), "de": {**(info.texts or {}).get("de", {}), "question": "Sie sagten {{" + old + "}}."}}
        db.session.commit()
    other = _second_person(app)
    theirs = form_of(other, 3)                       # the other member has the info text open
    held = _spy(monkeypatch)
    mine = form_of(client, 2)
    mine["code"] = "alter"
    r = auto(client, 2, mine)
    assert r.get_json()["status"] == "saved" and not r.get_json()["code_problem"]
    order = [h for h in held if h in (("Survey", 1), ("Item", 2), ("Item", 3))]
    assert order[:3] == [("Survey", 1), ("Item", 2), ("Item", 3)]  # survey first, then the questions
    with app.app_context():
        assert "{{alter}}" in db.session.get(Item, 3).texts["de"]["question"]
    # the other member's page was opened before the renaming: its save does not put the old name back
    theirs["question"] = theirs["question"] + " Danke."
    r = other.post(f"{E}/items/3", data=MultiDict({**theirs.to_dict(flat=False), "autosave": ["1"]}),
                   headers={"X-Requested-With": "fetch"})
    assert r.get_json()["status"] == "conflict"
    with app.app_context():
        assert "{{alter}}" in db.session.get(Item, 3).texts["de"]["question"]


def test_undo_holds_the_survey_before_the_rows_of_its_step(app, client, survey, monkeypatch):
    add(client, "text_input")
    form = form_of(client, 2)
    form["code"] = "zurueck"
    assert auto(client, 2, form).get_json()["status"] == "saved"
    held = _spy(monkeypatch)
    client.post(f"{E}/undo")
    assert held[0] == ("Survey", 1) and ("Item", 2) in held
    assert held.index(("Survey", 1)) < held.index(("Item", 2))
    assert _code(app, 2) != "zurueck"


def test_block_routes_hold_their_rows(app, client, survey, monkeypatch):
    held = _spy(monkeypatch)
    client.post(f"{E}/blocks/2", data={"title": "Neu", "changed": "title"})
    assert ("Block", 2) in held
    with app.app_context():
        assert db.session.get(Block, 2).titles["de"] == "Neu"
    held.clear()
    client.post(f"{E}/blocks")
    assert held[:1] == [("Survey", 1)]
    held.clear()
    client.post(f"{E}/blocks/2/move/down")
    assert held[:1] == [("Survey", 1)]
