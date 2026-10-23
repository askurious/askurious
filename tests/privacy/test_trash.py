from datetime import timedelta

from werkzeug.datastructures import MultiDict

from app import trash
from app.extensions import db
from app.models import Item, Survey, TrashedItem
from tests.conftest import add


def _likert(client):
    add(client, "likert")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "TA"), ("question", "Wie sehr?"), ("points", "5"), ("labeling", "full"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Aussage A"),
    ]))


def test_deleted_question_goes_to_trash_and_comes_back(app, client, survey):
    _likert(client)
    add(client, "text")                                            # item 3 behind it
    client.post("/surveys/1/editor/items/2/delete")
    with app.app_context():
        assert db.session.get(Item, 2) is None
        entry = db.session.get(Survey, 1).trash[0]
        assert entry.data["code"] == "TA" and entry.data["position"] == 0
    page = client.get("/surveys/1/editor/").data.decode()
    assert 'id="trash"' in page and "Wie sehr?" in page

    add(client, "text", code="x")                                  # takes no code of the deleted item
    r = client.post(f"/surveys/1/editor/trash/{entry.id}/restore")
    assert r.status_code == 302
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not s.trash
        first = s.blocks[1].items[0]
        assert first.code == "TA" and first.kind.statements(first, "de")[0]["text"] == "Aussage A"
    assert 'class="area-empty">leer</li>' in client.get("/surveys/1/editor/").data.decode()  # trash always shown, now empty


def test_restore_renames_taken_code_and_destroy_removes_for_good(app, client, survey):
    _likert(client)
    client.post("/surveys/1/editor/items/2/delete")
    _likert(client)                                                # new item 3 (renamed to "TA" below)
    with app.app_context():
        new = db.session.execute(db.select(Item).where(Item.type == "likert")).scalar_one()
        new.code = "TA"
        db.session.commit()
        entry_id = db.session.get(Survey, 1).trash[0].id
    client.post(f"/surveys/1/editor/trash/{entry_id}/restore")
    with app.app_context():
        codes = sorted(i.code for b in db.session.get(Survey, 1).blocks for i in b.items if i.type == "likert")
        assert codes == ["TA", "TA_2"]

    client.post("/surveys/1/editor/items/3/delete")
    with app.app_context():
        entry_id = db.session.get(Survey, 1).trash[0].id
    client.post(f"/surveys/1/editor/trash/{entry_id}/destroy")
    with app.app_context():
        assert db.session.get(TrashedItem, entry_id) is None


def test_block_delete_keeps_questions_and_trash_expires(app, client, survey):
    _likert(client)
    add(client, "page_break")                                      # page breaks are not kept
    client.post("/surveys/1/editor/blocks/2/delete")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [e.data["code"] for e in s.trash] == ["TA"]
        s.trash[0].deleted_at -= timedelta(days=trash.TRASH_DAYS, minutes=1)
        db.session.commit()
    client.get("/surveys/1/editor/")                               # opening the editor purges old entries
    with app.app_context():
        assert not db.session.get(Survey, 1).trash


def test_questions_of_a_deleted_block_come_back_together(app, client, survey):
    """UI audit F21: the block is gone, but its questions return into one new block with its name."""
    client.post("/surveys/1/editor/blocks")                        # block 3
    client.post("/surveys/1/editor/blocks/3", data={"title": "Technik"})
    add(client, "text_input", block=3)
    add(client, "rating", block=3)
    client.post("/surveys/1/editor/blocks/3/delete")               # not asked about: "Rückgängig" (tests/editor/test_undo.py)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert len(s.blocks) == 2
        entries = sorted(s.trash, key=lambda e: e.data["position"])
        ids, codes = [e.id for e in entries], [e.data["code"] for e in entries]
        assert all(e.data["block_titles"] == {"de": "Technik"} for e in s.trash)
    r = client.post(f"/surveys/1/editor/trash/{ids[1]}/restore", follow_redirects=True).data.decode()
    assert "Der frühere Block ist gelöscht; die Frage steht in einem neuen Block „Technik“ am Ende." in r
    r = client.post(f"/surveys/1/editor/trash/{ids[0]}/restore", follow_redirects=True).data.decode()
    assert "Der frühere Block ist gelöscht" not in r               # the second one finds the new block
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert len(s.blocks) == 3 and s.blocks[-1].title("de") == "Technik"
        assert [i.code for i in s.blocks[-1].items] == codes        # in their old order
        from app import flow

        assert s.blocks[-1].id in flow.block_order(flow.tree(s))    # and it has its place in the flow


def test_small_things_for_touch_keyboard_and_formats(app):
    """UI audit F36, F38, F41: row tools are not dimmed on touch, the HB status shows local time, placeholder
    chips work by keyboard and say when copying fails, the construct search says when it is not reachable."""
    from app import timeutil

    assert timeutil.clock("2026-07-01T10:05:00+00:00") == "12:05" and timeutil.clock("2026-01-01T10:05:00") == "11:05"
    assert timeutil.clock(None) == ""
    read = lambda p: open(p, encoding="utf-8").read()
    assert 'de_en("gestartet {t} Uhr.", "started at {t}.", t=h.marker.started|localclock)' in read("app/templates/surveys/_hb_running.html")
    assert "@media (hover: none) { .item-row .toolbar, .fl-tools { opacity: 1 !important; } }" in read("app/static/app.css")
    assert 'role="button" tabindex="0"' in read("app/templates/editor/loop.html")
    script = read("app/static/app.js")
    assert 'if (chip) { e.preventDefault(); copyChip(chip); }' in script    # Enter / space, delegated (code review 2026-10-07, F1)
    assert "Kopieren nicht möglich. Der Text ist markiert" in script
    assert "Die Suche in der Sammlung ist gerade nicht erreichbar" in script


def test_files_of_a_destroyed_entry_go_only_with_the_commit(app, client, survey):
    """Code review 2026-10-15, D5: destroying a trash entry removed its image files at once; a failed commit then left
    an entry whose files were gone. Now the files go once the session commits; a rollback keeps them."""
    import os

    from app import media

    with app.app_context():
        s = db.session.get(Survey, 1)
        os.makedirs(media.upload_folder(s.uid), exist_ok=True)
        path = os.path.join(media.upload_folder(s.uid), "bild.svg")
        open(path, "w").close()
        s.trash.append(TrashedItem(data={"type": "image", "assets": [{"filename": "bild.svg"}]}))
        db.session.commit()
        trash.destroy(s, s.trash[0])
        assert os.path.exists(path)
        db.session.rollback()                                            # the commit failed: entry and file stay
        s = db.session.get(Survey, 1)
        assert os.path.exists(path) and len(s.trash) == 1
        trash.destroy(s, s.trash[0])
        db.session.commit()
        assert not os.path.exists(path)
