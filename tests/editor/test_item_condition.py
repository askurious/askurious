"""A condition of a question of its own, "Nur zeigen, wenn …" (app/item_condition.py)."""
import json

from werkzeug.datastructures import MultiDict

from app import design, item_condition
from app.extensions import db
from app.models import Item, Response, Survey, User
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"


def build(app, client, cond=True):
    """Page 1: smoker (single choice, 1 = ja, 2 = nein); page 2: how often (text, required), only for smokers;
    page 3: a question for everybody."""
    fill_consent(client)
    for kind in ("single_choice", "page_break", "text_input", "page_break", "single_choice"):
        add(client, kind)                                           # 2, 3, 4, 5, 6
    client.post(f"{E}/items/2", data=MultiDict([
        ("code", "raucher"), ("question", "Rauchen Sie?"), ("answer_mode", "required"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Nein")]))
    client.post(f"{E}/items/4", data=MultiDict([
        ("code", "wieoft"), ("question", "Wie viele Zigaretten am Tag?"), ("answer_mode", "required")]
        + ([("item_when_item", "2"), ("item_when_mode", "is"), ("item_when_codes", "1")] if cond else [])))
    client.post(f"{E}/items/6", data=MultiDict([
        ("code", "gesund"), ("question", "Fühlen Sie sich gesund?"), ("answer_mode", "optional"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Nein")]))


def start(app, client):
    r = client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not s.is_editable, r.data.decode()[:300]
        uid = s.code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    return p, uid


def test_the_condition_is_stored_kept_and_removed_in_the_editor(app, client, survey):
    build(app, client)
    with app.app_context():
        item = db.session.get(Item, 4)
        assert item.config["when"]["item"] == 2 and item.config["when"]["value"] == "1"
        assert item_condition.text(item) == "raucher: Ja" and item_condition.problems(item) == []
    html = client.get(f"{E}/items/4").data.decode()
    assert "Nur zeigen, wenn …" in html and 'name="item_when_codes" value="1" checked' in html
    assert "nur wenn raucher: Ja" in html
    # the filter question itself is offered nothing: no question before it
    assert "noch keine Frage" in client.get(f"{E}/items/2").data.decode()
    assert "bedingt" in client.get(E + "/").data.decode()
    # a form without the fields (an older page, a script) keeps it; "immer zeigen" removes it
    client.post(f"{E}/items/4", data={"code": "wieoft", "question": "Wie oft?", "answer_mode": "required"})
    with app.app_context():
        assert db.session.get(Item, 4).config["when"]["item"] == 2
    client.post(f"{E}/items/4", data={"code": "wieoft", "question": "Wie oft?", "answer_mode": "required",
                                      "item_when_item": ""})
    with app.app_context():
        assert "when" not in db.session.get(Item, 4).config


def test_shown_only_to_smokers_and_skipped_otherwise(app, client, survey):
    build(app, client)
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "2"})            # no smoker: page 2 is skipped
    html = p.get(f"/s/{uid}").data.decode()
    assert 'name="i4"' not in html and 'name="i6"' in html
    p.post(f"/s/{uid}", data={"nav": "next", "i6": "1"})
    q = app.test_client()                                           # a smoker sees it, and it is required
    q.get(f"/s/{uid}")
    q.post(f"/s/{uid}", data=CONSENT_OK)
    q.post(f"/s/{uid}", data={"nav": "next", "i2": "1"})
    assert 'name="i4"' in q.get(f"/s/{uid}").data.decode()
    assert 'name="i4"' in q.post(f"/s/{uid}", data={"nav": "next", "i4": ""}, follow_redirects=True).data.decode()
    q.post(f"/s/{uid}", data={"nav": "next", "i4": "12"})
    q.post(f"/s/{uid}", data={"nav": "next", "i6": "2"})
    with app.app_context():
        from app import export

        s = db.session.get(Survey, 1)
        done = db.session.scalars(db.select(Response).filter_by(is_preview=False).order_by(Response.id)).all()
        assert [r.status for r in done] == [Response.COMPLETED] * 2
        assert "4" not in done[0].answers and done[1].answers["4"] == "12"
        rows = export.rows(s)
        assert rows[0]["wieoft"] in (None, "") and rows[1]["wieoft"] == "12"   # empty, not -77
        assert rows[0]["qc_missing"] == 0                           # not shown is not left out
        book = {r[0]: r for r in export.codebook(s)}
        assert "nur gezeigt, wenn raucher: Ja" in book["wieoft"][4] and "-77" not in book["wieoft"][3]
        assert "*Shown only if:* raucher: Ja" in export.questionnaire_md(s)
        assert export.paths_differ(s)
        from app import results

        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 4)
        assert entry["answered"] == 1                               # n: only the smoker who saw it


def test_going_back_and_changing_the_filter_drops_the_answer(app, client, survey):
    build(app, client)
    with app.app_context():
        db.session.get(Survey, 1).allow_back = True
        db.session.commit()
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "1"})
    p.post(f"/s/{uid}", data={"nav": "back", "i4": "20"})
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "2"})
    assert 'name="i6"' in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={"nav": "next", "i6": "1"})
    with app.app_context():
        response = db.session.scalars(db.select(Response).filter_by(is_preview=False)).one()
        assert response.status == Response.COMPLETED and "4" not in response.answers


def test_runtime_combines_the_condition_with_the_type(app, client, survey):
    build(app, client)
    with app.app_context():
        from app import runtime

        item = db.session.get(Item, 4)
        assert runtime.conditional(item)
        assert runtime.applies(item, {2: "1"}) and not runtime.applies(item, {2: "2"}) and not runtime.applies(item, {})
        assert runtime.present([item], {2: "2"}) == []
        # a URL parameter as the condition
        item.config = {**item.config, "when": {"item": "p:src", "op": "eq", "value": "panel"}}
        assert runtime.applies(item, {"p:src": "panel"}) and not runtime.applies(item, {"p:src": "x"})
        db.session.rollback()


def test_design_check_and_deleting_the_filter_question(app, client, survey):
    build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 4)
        assert not [f for f in design.check(s) if f.item_id == 4]
        item.config = {**item.config, "when": {"item": 6, "op": "eq", "value": "1"}}       # a later page
        assert any(f.severity == "error" and "früheren Seite" in f.message for f in design.check(s) if f.item_id == 4)
        item.config = {**item.config, "when": {"item": 2, "op": "in", "value": ""}}        # nothing ticked
        assert any("unvollständig" in f.message for f in design.check(s) if f.item_id == 4)
        db.session.rollback()
    page = client.post(f"{E}/items/2/delete", follow_redirects=True).data.decode()
    assert "wieoft" in page and "nicht mehr" in page                # said when the filter question goes
    with app.app_context():
        found = [f for f in design.check(db.session.get(Survey, 1)) if f.item_id == 4]
        assert any(f.severity == "error" and "nicht mehr gibt" in f.message for f in found)


def test_locked_in_a_running_survey(app, client, survey):
    build(app, client)
    start(app, client)
    client.post(f"{E}/items/4", data={"code": "wieoft", "question": "Wie viele Zigaretten pro Tag?",
                                      "answer_mode": "required", "item_when_item": ""})
    with app.app_context():
        item = db.session.get(Item, 4)
        assert item.config["when"]["item"] == 2                     # logic stays
        assert item.text("question") == "Wie viele Zigaretten pro Tag?"   # a text fix is taken
    assert "nur im Entwurf" in client.get(f"{E}/items/4").data.decode()


def test_dummies_duplicate_copy_and_survey_file(app, client, survey):
    build(app, client)
    with app.app_context():
        from app import dummy, duplicate, duration, transfer

        s = db.session.get(Survey, 1)
        dummy.generate(s, 12, seed=3)
        db.session.commit()
        dummies = list(db.session.scalars(db.select(Response).filter_by(source="dummy")))
        assert dummies and all(("4" in r.answers) == (r.answers.get("2") == "1") for r in dummies if r.answers.get("2"))
        est = duration.estimate(s)
        assert est["low"] < est["high"]                             # the conditioned question may be left out
        new, notes = duplicate.create(s, db.session.get(User, 1), "Welle 2")
        twin = {i.code: i for b in new.blocks for i in b.items}
        assert twin["wieoft"].config["when"]["item"] == twin["raucher"].id != 2 and not notes
        alone = duplicate.copy_item(db.session.get(Item, 4), new)  # without the question it depends on
        assert "when" not in alone.config and alone.cond_note
        data = json.dumps(transfer.dump(s)).encode()
    with app.test_request_context():
        other, notes = transfer.create(transfer.loads(data), db.session.get(User, 1), "Dritte")
        twin = {i.code: i for b in other.blocks for i in b.items}
        assert twin["wieoft"].config["when"]["item"] == twin["raucher"].id != 2
        # a file whose condition names a question that is not in it: read in without the condition, with a note
        raw = json.loads(data)
        for block in raw["blocks"]:
            block["items"] = [i for i in block["items"] if i["code"] != "raucher"]
        third, notes = transfer.create(raw, db.session.get(User, 1), "Vierte")
        twin = {i.code: i for b in third.blocks for i in b.items}
        assert "when" not in twin["wieoft"].config and any("Nur zeigen, wenn" in n for n in notes)


def test_word_codebook_names_the_condition(app, client, survey):
    build(app, client)
    with app.app_context():
        import io

        from docx import Document

        from app import questionnaire_docx

        doc = Document(io.BytesIO(questionnaire_docx.build(db.session.get(Survey, 1), "codebook", ui="de")))
        assert any("Nur gezeigt, wenn raucher: Ja" in p.text for p in doc.paragraphs)


def test_preview_marks_the_conditioned_question(app, client, survey):
    build(app, client)
    client.get(f"{E}/preview?restart=1")
    client.post(f"{E}/preview", data={"nav": "next", **CONSENT_OK})
    page = client.post(f"{E}/preview", data={"nav": "next", "i2": "1"}, follow_redirects=True).data.decode()
    assert 'name="i4"' in page and "Bedingt: nur gezeigt, wenn raucher: Ja" in page
