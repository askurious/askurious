"""Answers, saved states and HB results hang on ids. SQLite gives the id of a deleted last row to the next one,
so a deleted question, block or survey must never hand its id (or its files) to a new one, and a question that
comes back from the trash must come back under its own id (audit 2026-10-01, S1 to S3, S16, S19)."""
import csv
import io
import os

from app import answer_lock, flow, lifecycle, snapshots
from app.extensions import db
from app.models import Block, ContactEntry, IdFloor, Item, Survey, TrashedItem
from tests.conftest import CONSENT_OK, add, fill_consent, split_node

E = "/surveys/1/editor"


def _text(client, code, block=2, **extra):
    add(client, "text_input", block=block)
    with client.application.app_context():
        item = db.session.scalar(db.select(db.func.max(Item.id)))
    client.post(f"{E}/items/{item}", data={"code": code, "question": code, **extra})
    return item


def _answer(app, **answers):
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=CONSENT_OK)
    p.post("/s/1", data={"nav": "next", **answers})
    return p


# a question with real answers in a reopened survey is deleted only with the dialog's field (audit 2026-10-18, FUNC-05)
ANSWERED = {answer_lock.CONFIRM_FIELD: "1"}


def _row(client):
    data = client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(data)))[0]


def _collected_and_reopened(app, client):
    fill_consent(client)
    first, second = _text(client, "erste"), _text(client, "zweite")
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    _answer(app, **{f"i{first}": "ALT-1", f"i{second}": "ALT-2"})
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    return first, second


def test_a_new_question_never_gets_the_id_of_a_deleted_one(app, client, survey):
    first, second = _collected_and_reopened(app, client)
    client.post(f"{E}/items/{second}/delete", data=ANSWERED)      # the newest question
    new = _text(client, "einkommen")
    assert new > second
    row = _row(client)
    assert row["erste"] == "ALT-1" and row["einkommen"] == ""      # not the answer to the deleted question
    with app.app_context():
        assert db.session.get(IdFloor, "item").highest == second


def test_ids_stay_apart_for_several_new_rows_blocks_and_surveys(app, client, survey):
    add(client, "text_input"), add(client, "text_input")          # items 2, 3
    client.post(f"{E}/blocks")                                    # block 3
    client.post(f"{E}/blocks/3/delete")
    client.post(f"{E}/items/3/delete"), client.post(f"{E}/items/2/delete")
    with app.app_context():
        block = db.session.get(Block, 2)
        block.items = [Item(type="text_input", code=c, position=n) for n, c in enumerate("abc")]  # one flush
        db.session.commit()
        assert sorted(i.id for i in block.items) == [4, 5, 6]
    client.post(f"{E}/blocks")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.max(Block.id))) == 4
        lifecycle.delete(db.session.get(Survey, 1))
        db.session.commit()
    client.post("/surveys/new", data={"title": "Zweite", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        new = db.session.scalar(db.select(Survey))
        assert new.id == 2 and min(b.id for b in new.blocks) > 4 and min(i.id for b in new.blocks for i in b.items) > 6


def test_a_restored_question_keeps_its_id_and_its_answers(app, client, survey):
    first, second = _collected_and_reopened(app, client)
    client.post(f"{E}/items/{first}/delete", data=ANSWERED)
    assert _row(client).get("erste") is None
    with app.app_context():
        entry = db.session.scalar(db.select(TrashedItem.id))
    client.post(f"{E}/trash/{entry}/restore")
    with app.app_context():
        assert {i.code: i.id for i in db.session.scalars(db.select(Item))}["erste"] == first
    row = _row(client)
    assert row["erste"] == "ALT-1" and row["zweite"] == "ALT-2"

    client.post(f"{E}/items/1/delete")                             # the consent as well
    with app.app_context():
        entry = db.session.scalar(db.select(TrashedItem.id))
    client.post(f"{E}/trash/{entry}/restore")
    with app.app_context():
        assert db.session.get(Survey, 1).consent_item.id == 1
    assert _row(client)["consent"] == "1"


def test_a_restored_choice_question_keeps_its_design_lock(app, client, survey):
    from tests.analysis.test_maxdiff import setup

    setup(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        sets = db.session.get(Item, 2).config["design"]["versions"][0]
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=CONSENT_OK)
    p.post("/s/1", data={"nav": "next", "i2_t0_b": str(sets[0][0]), "i2_t0_w": str(sets[0][1])})
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    client.post(f"{E}/items/2/delete")
    with app.app_context():
        entry = db.session.scalar(db.select(TrashedItem.id))
    client.post(f"{E}/trash/{entry}/restore")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item is not None and item.kind.design_locked(item)


def test_deleting_a_survey_removes_saved_states_and_hb_results(app, client, survey):
    _collected_and_reopened(app, client)
    base = app.config["UPLOAD_FOLDER"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        uid = s.uid
        assert len(snapshots.listing(s)) == 1
        os.makedirs(os.path.join(base, "hb", uid), exist_ok=True)
        open(os.path.join(base, "hb", uid, "item_2.json"), "w").close()
        lifecycle.delete(s, force=True)
        assert os.path.isdir(os.path.join(base, "snapshots", uid))  # only once the deletion is committed
        db.session.commit()
    assert not os.path.exists(os.path.join(base, "snapshots", uid)) and not os.path.exists(os.path.join(base, "hb", uid))

    client.post("/surveys/new", data={"title": "Zweite", "languages": ["de"], "default_language": "de"})
    assert 'id="snapshots"' not in client.get("/surveys/2/data").data.decode()


def test_a_failed_deletion_keeps_the_files(app, client, survey):
    base = app.config["UPLOAD_FOLDER"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder = os.path.join(base, s.uid)
        os.makedirs(folder, exist_ok=True)
        open(os.path.join(folder, "bild.png"), "w").close()
        lifecycle.delete(s)
        db.session.rollback()
        db.session.commit()                                        # a later commit removes nothing either
        assert os.path.isfile(os.path.join(folder, "bild.png")) and db.session.get(Survey, 1) is not None


def test_files_under_the_number_are_adopted_only_if_they_are_this_surveys(app, client, survey):
    """Before, saved states and HB results lay under the survey's number: the survey's own move to its folder,
    what a deleted survey with the same number left behind does not."""
    base = app.config["UPLOAD_FOLDER"]
    old = os.path.join(base, "snapshots", "survey_1")
    for name in ("v1_20200101-120000", "v1_20991231-120000"):      # before and after this survey was created
        os.makedirs(os.path.join(old, name))
        with open(os.path.join(old, name, "data.csv"), "w") as f:
            f.write("resp_id\n")
    hb_old = os.path.join(base, "hb", "survey_1")
    os.makedirs(hb_old)
    for n in (1, 2):
        open(os.path.join(hb_old, f"item_{n}.json"), "w").close()
    os.utime(os.path.join(hb_old, "item_1.json"), (1.0e9, 1.0e9))  # 2001: a leftover
    add(client, "text_input")
    with app.app_context():
        from app import maxdiff_hb

        s = db.session.get(Survey, 1)
        assert [n for n, _, _ in snapshots.listing(s)] == ["v1_20991231-120000"]
        assert snapshots.path(s, "v1_20200101-120000", "data.csv") is None
        assert os.listdir(old) == ["v1_20200101-120000"]
        consent, text = db.session.get(Item, 1), db.session.get(Item, 2)
        assert not os.path.exists(maxdiff_hb._paths(consent)[0]) and os.path.exists(maxdiff_hb._paths(text)[0])


def test_answers_to_a_removed_question_stay_in_the_contact_export(app, client, survey):
    fill_consent(client)
    _text(client, "erste")
    client.post(f"{E}/blocks")                                    # block 3, behind the split line
    mail = _text(client, "email", block=3, validation="email")
    client.post(f"{E}/flow/add", data={"kind": "split"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        node = split_node(s)["id"]
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                                            {"t": "split", "id": node}, {"t": "block", "block": 3}]}
        flow.normalize(s)
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    for n in range(5):
        p = _answer(app, i2=f"A{n}")
        p.get("/s/1")
        p.post("/s/1", data={"nav": "next", f"i{mail}": f"p{n}@example.org"})
    with app.app_context():
        __import__("app.contact", fromlist=["bundle"]).bundle()  # PostgreSQL: finished sections wait (K11)
        assert db.session.scalar(db.select(db.func.count()).select_from(ContactEntry)) == 5
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    client.post(f"{E}/items/{mail}/delete")
    after = client.get("/surveys/1/contact/export.csv").data.decode("utf-8-sig")
    assert after.splitlines()[0] == f"entfernt_{mail}" and after.count("@example.org") == 5
    with app.app_context():
        from app import contact

        assert len(contact.draw(db.session.get(Survey, 1), 2)) == 2
