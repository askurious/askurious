"""The small findings of the code review of 2026-10-02 in the editor (code-review-2026-10-02.md, K14 to K19, D1, D2)."""
import csv
import io
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from app import accounts, design, flow, undo
from app.extensions import db
from app.models import (Block, EditMark, EditStep, IdFloor, Item, PreviewComment, Quota, QuotaCell, Survey, SurveyGroup,
                        SurveyMember, TrashedItem, User, WorkGroup, WorkGroupMember)
from tests.quotas import test_quotas
from tests.editor import test_tempo
from tests.conftest import account_ref, add
from tests.editor.test_undo import colleague, labels, redo
from tests.editor.test_undo import undo as take_back

E = "/surveys/1/editor"
Q = "/surveys/1/flow/quotas"
ME = "0000-0000-0000-001X"       # the signed-in researcher of the fixtures (conftest), "Philipp"
OTHER = "0000-0002-1825-0097"    # tests.editor.test_undo.colleague, "Kollegin"
NODE = shutil.which("node")
SCRIPT = Path(__file__).parents[2] / "app" / "static" / "app.js"


def _codes(app):
    with app.app_context():
        return [[(i.id, i.code) for i in b.items] for b in db.session.get(Survey, 1).blocks]


def _block(app, block_id=2):
    with app.app_context():
        b = db.session.get(Block, block_id)
        return b.title("de"), b.show_title, b.randomize_items, (b.settings or {}).get("tempo_hint")


# --- K14: a block's name and ticks are saved one by one -------------------------------------------


def test_a_page_opened_a_while_ago_does_not_put_the_ticks_of_a_block_back(app, client, survey):
    test_tempo.setup(client, tempo_hint="blocks")
    # another tab ticked all three; this page still shows them unticked and sends them so
    client.post(E + "/blocks/2", data={"title": "Block", "show_title": "1", "randomize_items": "1", "tempo_hint": "1"})
    assert _block(app) == ("Block", True, True, True)
    client.post(E + "/blocks/2", data={"title": "Neuer Name", "changed": "title"})
    assert _block(app) == ("Neuer Name", True, True, True)
    assert labels(app)[0] == "Block in „Neuer Name“ umbenannt"
    # a tick from a page that still shows the old name: the tick is saved, the name stays
    client.post(E + "/blocks/2", data={"title": "Block", "show_title": "1", "tempo_hint": "1", "changed": "randomize_items"})
    assert _block(app) == ("Neuer Name", True, False, True)
    assert labels(app)[0] == "Einstellung am Block „Neuer Name“ geändert"
    client.post(E + "/blocks/2", data={"title": "Block", "changed": "tempo_hint"})
    assert _block(app) == ("Neuer Name", True, False, False)
    # a tick clicked while the name was being typed: both are named
    client.post(E + "/blocks/2", data=MultiDict([("title", "Zwei"), ("changed", "title"), ("changed", "show_title")]))
    assert _block(app) == ("Zwei", False, False, False)
    # without JavaScript the button saves the whole form, as before
    client.post(E + "/blocks/2", data={"title": "Alles", "randomize_items": "1"})
    assert _block(app) == ("Alles", False, True, False)
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'mark.name = "changed"' in script and "save(box)" in script and "save(input)" in script


# --- K15: undo never makes two questions with one variable name -----------------------------------


def test_a_question_that_comes_back_by_undo_gets_a_free_name(app, client, survey):
    add(client, "text_input")                                              # item 2 = Q1
    client.post(E + "/blocks")                                             # block 3
    other = colleague(app, "editor")
    client.post(E + "/items/2/delete")
    other.post(E + "/blocks/3/items", data={"type": "text_input"})         # the name Q1 is free: item 3 gets it
    assert _codes(app) == [[(1, "consent")], [], [(3, "Q1")]]
    html = take_back(client)
    assert "Zurückgenommen: „Q1“ gelöscht." in html
    assert "Der Variablenname „Q1“ ist inzwischen an eine andere Frage vergeben: Die Frage heißt jetzt „Q1_2“." in html
    assert _codes(app) == [[(1, "consent")], [(2, "Q1_2")], [(3, "Q1")]]
    with app.app_context():
        assert not [f for f in design.check(db.session.get(Survey, 1)) if "Q1" in f.message and "mehr" in f.message]
    # the step knows the new name: it can be redone and taken back again
    assert "Wiederholt: „Q1“ gelöscht." in redo(client)
    assert _codes(app) == [[(1, "consent")], [], [(3, "Q1")]]
    assert "lässt sich nicht" not in take_back(client)
    assert _codes(app) == [[(1, "consent")], [(2, "Q1_2")], [(3, "Q1")]]


def test_a_name_that_comes_back_by_undo_gets_a_number_if_taken(app, client, survey):
    add(client, "text_input")                                              # item 2 = Q1
    other = colleague(app, "editor")
    client.post(E + "/items/2", data={"code": "alter", "question": "Wie alt?"})
    other.post(E + "/blocks/2/items", data={"type": "text_input"})         # item 3 = Q1
    html = take_back(client)
    assert "Die Frage heißt jetzt „Q1_2“" in html
    with app.app_context():
        assert sorted(i.code for i in db.session.get(Block, 2).items) == ["Q1", "Q1_2"]


# --- K16: undo and deleted quotas ---------------------------------------------------------------------


def test_undo_brings_no_deleted_quota_back_into_the_flow(app, client, survey):
    test_quotas.questionnaire(client)
    client.post(Q + "/add")
    client.post(Q + "/1", data=test_quotas.cells_form([("Männer", "m", 50, [(2, "eq", "1")]),
                                                      ("Frauen", "f", 50, [(2, "eq", "2")])]))
    client.post(E + "/flow/add", data={"kind": "quota"})
    client.post(E + "/flow/add", data={"kind": "end"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        qid, eid = flow.walk_nodes(s, "quota")[0]["id"], flow.walk_nodes(s, "end")[0]["id"]
        s.flow = flow.tree(s) | {"children": [
            {**n, "quotas": [1]} if n.get("id") == qid else
            {**n, "cases": [{"id": "c1", "when": "quotafull", "quotas": [1], "rule": "any", "text": {}},
                            {"id": "c2", "when": "cellfull", "quota": 1, "cells": ["m"], "text": {}}]} if n.get("id") == eid
            else n for n in flow.tree(s)["children"]]}
        db.session.commit()
    client.post(E + f"/flow/{eid}/delete")
    client.post(E + f"/flow/{qid}/delete")
    with app.app_context():  # the quota goes without a step of this person (someone else deleted it)
        s = db.session.get(Survey, 1)
        s.quotas.remove(s.quotas[0])
        db.session.commit()
    assert "lässt sich nicht" not in take_back(client) and "lässt sich nicht" not in take_back(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not s.quotas
        assert flow.walk_nodes(s, "quota")[0]["quotas"] == []
        cases = flow.walk_nodes(s, "end")[0]["cases"]
        assert cases[0]["quotas"] == [] and cases[1]["quota"] is None and cases[1]["cells"] == []
        assert any("Quote" in f.message for f in design.check(s))       # asks for a quota, as for a new element
    # both ways still work: the steps hold what was really put back
    assert "lässt sich nicht" not in redo(client) and "lässt sich nicht" not in take_back(client)
    assert client.get("/surveys/1/flow/").status_code == 200


def test_a_new_quota_never_gets_the_id_of_a_deleted_one(app, client, survey):
    test_quotas.questionnaire(client)
    client.post(Q + "/add")
    client.post(Q + "/1", data=test_quotas.cells_form([("Männer", "m", 50, [(2, "eq", "1")]),
                                                      ("Frauen", "f", 50, [(2, "eq", "2")])]))
    with app.app_context():
        assert sorted(c.id for c in db.session.get(Quota, 1).cells) == [1, 2]
    client.post(Q + "/1/delete")
    client.post(Q + "/add")
    with app.app_context():
        quota = db.session.scalar(db.select(Quota))
        assert quota.id == 2 and db.session.get(IdFloor, "quota").highest == 1
        quota.cells = [QuotaCell(code="a", name="A", conds=[], target=1)]
        db.session.commit()
        assert quota.cells[0].id == 3 and db.session.get(IdFloor, "quota_cell").highest == 2


# --- K17: a question into another draft ---------------------------------------------------------------


def _choice(client):
    add(client, "single_choice")                                           # item 2 in survey 1 (de, en)
    client.post(E + "/items/2", data=MultiDict([
        ("code", "wahl"), ("question", "Deutsche Frage"), ("answer_mode", "required"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "nein")]))
    client.post(E + "/items/2?lang=en", data={"question": "English question"})


def _target(app, client, languages, default):
    client.post("/surveys/new", data={"title": "Ziel", "languages": languages, "default_language": default})
    with app.app_context():
        return db.session.scalar(db.select(Survey.code).filter_by(title="Ziel"))


def _copied(app):
    with app.app_context():
        target = db.session.scalar(db.select(Survey).filter_by(title="Ziel"))
        item = next(i for b in target.blocks for i in b.items if i.type == "single_choice")
        return {lang: (t.get("question"), sorted((t.get("options") or {}).values())) for lang, t in item.texts.items()}


def test_a_copied_question_has_texts_in_the_languages_of_the_target(app, client, survey):
    _choice(client)
    code = _target(app, client, ["fr"], "fr")
    html = client.post(E + "/items/2/copy-to", data={"target": code}, follow_redirects=True).data.decode()
    # no French in the source: the German texts stand in for it, and the person is told
    copied = _copied(app)
    assert list(copied) == ["fr"] and copied["fr"][0] == "Deutsche Frage"
    assert "Die Frage hat keine Texte auf Français" in html and "Bitte übersetzen Sie sie." in html
    assert "Deutsche Frage" in html


def test_a_copied_question_keeps_only_the_languages_of_the_target(app, client, survey):
    _choice(client)
    code = _target(app, client, ["en"], "en")
    html = client.post(E + "/items/2/copy-to", data={"target": code}, follow_redirects=True).data.decode()
    copied = _copied(app)
    assert list(copied) == ["en"] and copied["en"][0] == "English question"
    assert "Bitte übersetzen Sie sie." not in html


def test_copying_is_only_offered_to_who_may_copy_and_never_for_the_consent(app, client, survey):
    add(client, "text_input")                                              # 2
    add(client, "page_break")                                              # 3
    code = _target(app, client, ["de"], "de")
    assert "copy-to" in client.get(E + "/items/2").data.decode()
    with app.app_context():
        for sid, role in ((1, "viewer"), (2, "editor")):
            db.session.add(SurveyMember(survey_id=sid, orcid=OTHER, name="K", role=role, position=1))
        db.session.commit()
    viewer = app.test_client()
    viewer.get(f"/auth/dev-login?orcid={OTHER}&name=K")
    viewer.post("/terms/", data={"accept": "1"})
    page = viewer.get(E + "/items/2")
    assert page.status_code == 200 and "copy-to" not in page.data.decode()   # "Ansehen" here: the POST would be refused
    assert client.post(E + "/items/1/copy-to", data={"target": code}).status_code == 400   # the consent
    assert client.post(E + "/items/3/copy-to", data={"target": code}).status_code == 400   # a page break
    assert client.post(E + "/items/2/copy-to", data={"target": code}).status_code == 302


# --- K18: the list of steps is read without their states ----------------------------------------------


def test_the_history_is_listed_without_the_states_of_the_steps(app, client, survey):
    add(client, "text_input")
    add(client, "text_input")
    with app.app_context():
        steps = undo.history(db.session.get(Survey, 1), db.session.scalar(db.select(User).filter_by(orcid=ME)))["undo"]
        assert [s.label for s in steps] == ["„Q2“ hinzugefügt (Freitext)", "„Q1“ hinzugefügt (Freitext)"]
        assert all("data" in db.inspect(s).unloaded for s in steps)
    statements = []
    with app.app_context():
        from sqlalchemy import event

        listen = lambda conn, cursor, statement, *rest: statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", listen)
        try:
            assert "Rückgängig: „Q2“ hinzugefügt" in client.get(E + "/").data.decode()
        finally:
            event.remove(db.engine, "before_cursor_execute", listen)
    listed = [s for s in statements if "FROM edit_step" in s]
    # of the data only the English name of each step (undo.label), never the states; the reading of that one key
    # is JSON_EXTRACT(...) on SQLite and "->>" on PostgreSQL
    only_label = re.compile(r"JSON_EXTRACT\(edit_step\.data, \?\)|edit_step\.data ->> %\(\w+\)s")
    assert listed and not any("edit_step.data" in only_label.sub("", s) for s in listed)
    assert "Zurückgenommen: „Q2“ hinzugefügt" in take_back(client)              # the step applied reads its own


# --- K19: preview page number, presence keys, the confirm dialog -------------------------------------------


def test_a_page_that_is_no_number_is_no_page(app, client, survey):
    add(client, "info")
    add(client, "page_break")
    add(client, "info")
    assert client.get(E + "/preview?page=abc", follow_redirects=True).status_code == 200
    html = client.get(E + "/preview?device=mobile").data.decode()
    path = "/" + re.search(r'value="(https?://[^"]+/p/[^"]+)"', html).group(1).split("://", 1)[1].split("/", 1)[1]
    phone = app.test_client()                                              # not signed in
    assert phone.get(path + "?page=abc", follow_redirects=True).status_code == 200
    phone.get(path + "?page=2")
    assert "Seite 2/3" in phone.get(path).data.decode()                    # a number still jumps


def test_presence_only_for_parts_of_the_survey(app, client, survey):
    add(client, "text_input")                                              # item 2
    client.post(E + "/flow/add", data={"kind": "loop"})
    client.post("/surveys/new", data={"title": "Zwei", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        loop = flow.walk_nodes(db.session.get(Survey, 1), "loop")[0]["id"]
        foreign = db.session.scalar(db.select(Item.id).join(Block).filter(Block.survey_id == 2))
    viewer = colleague(app, "viewer")
    for key in ("x", "item:999", f"item:{foreign}", "item:²", "item:abc", "loop:nix", "settings:nix", "block:2",
                *(f"made-up-{n}" for n in range(20))):
        assert viewer.post(E + "/presence", data={"key": key}).status_code == 204, key
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(EditMark.id))) == 0
    known = ["item:2", "item:1", f"loop:{loop}", f"flow:{loop}", "settings", "settings:theme", "settings:meta"]
    for key in known:
        r = viewer.post(E + "/presence", data={"key": key})
        assert r.status_code == 200 and r.get_json() == {"others": []}, key
    with app.app_context():
        assert sorted(db.session.scalars(db.select(EditMark.key))) == sorted(known)
    assert client.post(E + "/presence", data={"key": "item:2"}).get_json() == {"others": ["Kollegin"]}


# A stand-in for the browser: a <dialog> whose "close" event comes after close() returned, as in browsers
# (the event is queued). Nothing else of the page is needed by the dialog's script.
_DIALOG_HARNESS = r"""
const source = require("fs").readFileSync(process.argv[2], "utf8").replace(/\r\n/g, "\n");
const start = source.indexOf("(function () {\n  var dialog = null");
const end = source.indexOf("})();", start) + 5;
function element() {
  const listeners = {};
  const el = {
    open: false, returnValue: "", style: {}, dataset: {}, offsetHeight: 100, hidden: false,
    classList: { toggle() {} }, setAttribute() {}, appendChild() {}, focus() {},
    parts: {},
    querySelector(sel) { return this.parts[sel] || (this.parts[sel] = element()); },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    fire(type) { (listeners[type] || []).forEach(fn => fn({ target: el })); },
    showModal() { if (el.open) throw new Error("InvalidStateError: already open"); el.open = true; shown.push(el.parts["#confirm-title"].textContent); },
    close(value) { if (!el.open) return; el.open = false; if (value !== undefined) el.returnValue = value; setTimeout(() => el.fire("close"), 0); },
  };
  return el;
}
const shown = [];
let dialog = null;
const window = { HTMLDialogElement: function () {}, innerWidth: 1000 };
const document = { createElement() { return dialog = element(); }, body: { appendChild() {} } };
new Function("window", "document", "de_en", source.slice(start, end))(window, document, (de, en) => de);  // de_en: app.js, the page language
const click = which => dialog.parts[which].fire("click");
const later = () => new Promise(r => setTimeout(r, 5));
(async () => {
  const answers = [];
  const first = window.askConfirm({ title: "eins" }).then(a => answers.push(["eins", a]));
  const second = window.askConfirm({ title: "zwei" }).then(a => answers.push(["zwei", a]));   // while the first is open
  await later();
  const open_after_both = shown.slice();
  click("[data-yes]");                 // the answer to the question on screen
  await later();
  const after_first = { answers: answers.slice(), shown: shown.slice() };
  click("[data-no]");
  await Promise.all([first, second]);
  const p = window.askConfirm({ title: "drei" });
  await later();
  click("[data-yes]");
  process.stdout.write(JSON.stringify({ open_after_both, after_first, answers, third: await p, shown }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs Node to run the dialog script of app/static/app.js")
def test_a_second_question_waits_for_the_answer_to_the_first(tmp_path):
    import json

    harness = tmp_path / "dialog.js"
    harness.write_text(_DIALOG_HARNESS, encoding="utf-8")
    done = subprocess.run([NODE, str(harness), str(SCRIPT)], capture_output=True, check=True, timeout=60)
    result = json.loads(done.stdout.decode("utf-8"))
    assert result["open_after_both"] == ["eins"]                           # one question at a time
    assert result["after_first"] == {"answers": [["eins", True]], "shown": ["eins", "zwei"]}
    assert result["answers"] == [["eins", True], ["zwei", False]]          # each gets its own answer
    assert result["third"] is True and result["shown"] == ["eins", "zwei", "drei"]


# --- D1: comments from test runs do not keep the name of a deleted account ----------------------------


def _comment(client, text, sid=1, block=2):
    r = client.post(f"/surveys/{sid}/editor/preview/comment", data={"block_id": str(block), "text": text})
    assert r.status_code == 200
    return r.json["id"]


def test_comments_lose_the_name_of_a_deleted_account(app, client, survey):
    add(client, "info")
    other = colleague(app, "editor")                                       # "Kollegin", co-owner of survey 1
    theirs, mine = _comment(other, "Von der Kollegin"), _comment(client, "Von Philipp")
    other.post(f"{E}/comments/{mine}", data={"status": "resolved"})
    # a survey the colleague cannot open, with a comment by someone of the same name; and a test person who
    # typed that name on the phone link of survey 1
    client.post("/surveys/new", data={"title": "Fremd", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        block = db.session.scalar(db.select(Block.id).filter_by(survey_id=2).order_by(Block.id.desc()))
        db.session.add(PreviewComment(survey_id=2, block_id=block, text="Namensvetterin", author="Kollegin",
                                      handled_by="Kollegin", via="editor"))
        db.session.add(PreviewComment(survey_id=1, block_id=2, text="Am Telefon", author="Kollegin", via="phone"))
        # a survey the colleague opens through a group only
        group = WorkGroup(name="Team")
        group.members.append(WorkGroupMember(orcid=OTHER, name="Kollegin", role="member"))
        group.members.append(WorkGroupMember(orcid=ME, name="Philipp", role="lead"))
        db.session.add(group)
        db.session.flush()
        db.session.add(SurveyGroup(survey_id=2, group_id=group.id, role="editor"))
        db.session.commit()
    via_group = _comment(other, "Über die Gruppe", sid=2, block=block)
    with app.app_context():
        accounts.delete(db.session.scalar(db.select(User).filter_by(orcid=OTHER)), "self")
        rows = {c.text: (c.author, c.handled_by) for c in db.session.scalars(db.select(PreviewComment))}
    assert rows["Von der Kollegin"] == ("", "") and rows["Von Philipp"] == ("Philipp", "")
    assert rows["Über die Gruppe"] == ("", "")
    assert rows["Am Telefon"] == ("Kollegin", "")                         # typed by a test person, not the account
    # survey 2 was shared with the group: there the name could be this person's, so it goes there too; a survey
    # the person could never open keeps it
    assert rows["Namensvetterin"] == ("", "")
    page = client.get(f"{E}/comments?show=all").data.decode()
    assert "Testperson · Vorschau im Editor" in page and "erledigt von ?" in page
    exported = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/comments.csv").data.decode("utf-8-sig"))))
    assert {r["text"]: (r["author"], r["handled_by"]) for r in exported}["Von Philipp"] == ("Philipp", "")
    assert theirs and via_group


def test_a_namesake_in_a_survey_the_person_never_saw_keeps_the_name(app, client, survey):
    add(client, "info")
    client.post("/surveys/new", data={"title": "Fremd", "languages": ["de"], "default_language": "de"})
    other = colleague(app, "editor")
    _comment(other, "Von der Kollegin")
    with app.app_context():
        block = db.session.scalar(db.select(Block.id).filter_by(survey_id=2))
        db.session.add(PreviewComment(survey_id=2, block_id=block, text="Namensvetterin", author="Kollegin",
                                      handled_by="Kollegin", via="editor"))
        db.session.commit()
        accounts.delete(db.session.scalar(db.select(User).filter_by(orcid=OTHER)), "self")
        rows = {c.text: (c.author, c.handled_by) for c in db.session.scalars(db.select(PreviewComment))}
    assert rows == {"Von der Kollegin": ("", ""), "Namensvetterin": ("Kollegin", "Kollegin")}


# --- D2: the key of a person in consents that are kept in the trash and in steps ---------------------


def _researchers(doc):
    """Every list named "researchers" in a JSON document."""
    if isinstance(doc, dict):
        return [v for k, v in doc.items() if k == "researchers"] + [r for v in doc.values() for r in _researchers(v)]
    return [r for v in doc for r in _researchers(v)] if isinstance(doc, list) else []


def _consent_everywhere(app, client, key):
    """Survey 1 with `key` named in the consent: live, in a step of Philipp, and (after deleting it) in the trash."""
    with app.app_context():
        key = account_ref(key) if key[:1].isdigit() else key  # an iD: its account's reference (since 2b)
        consent = db.session.get(Item, 1)
        consent.config = {**consent.config, "researchers": [account_ref(ME), key]}
        db.session.commit()
    client.post(E + "/items/1", data={"code": "consent", "body": "Neuer Text"})   # a step with the consent's state
    client.post(E + "/items/1/delete")                                           # a step, and the consent in the trash
    with app.app_context():
        assert [key in r for e in db.session.scalars(db.select(TrashedItem)) for r in _researchers(e.data)] == [True]
        assert any(key in r for s in db.session.scalars(db.select(EditStep)) for r in _researchers(s.data))


def _stored(app):
    """The researchers named in trash, steps and consents, as iDs (references by account since 2b)."""
    def ids(refs):
        return [(db.session.get(User, int(r[1:])).orcid if r[:1] == "u" and r[1:].isdigit() else r) for r in refs]

    with app.app_context():
        return ([ids(r) for e in db.session.scalars(db.select(TrashedItem)) for r in _researchers(e.data)],
                [ids(r) for s in db.session.scalars(db.select(EditStep)) for r in _researchers(s.data)],
                [ids(r) for i in db.session.scalars(db.select(Item).filter_by(type="consent")) for r in _researchers(i.config)])


def test_the_key_of_a_deleted_account_leaves_trash_steps_and_consents(app, client, survey):
    colleague(app, "editor")
    _consent_everywhere(app, client, OTHER)
    take_back(client)                                                      # the consent is live again, the step stays
    client.post(E + "/items/1/delete")
    with app.app_context():
        accounts.delete(db.session.scalar(db.select(User).filter_by(orcid=OTHER)), "self")
    trash, steps, _ = _stored(app)
    assert trash == [[ME]] and steps and all(r == [ME] for r in steps)
    with app.app_context():
        raw = db.session.execute(db.text("SELECT data FROM trashed_item UNION ALL SELECT data FROM edit_step")).scalars().all()
        assert raw and not any(OTHER in doc for doc in raw)
    assert "lässt sich nicht" not in take_back(client)                     # Philipp's steps still fit the questionnaire
    assert _stored(app)[2] == [[ME]]


def test_the_key_of_a_deleted_account_leaves_a_consent_that_is_in_place(app, client, survey):
    colleague(app, "editor")
    with app.app_context():
        consent = db.session.get(Item, 1)
        consent.config = {**consent.config, "researchers": [account_ref(ME), account_ref(OTHER)]}
        db.session.commit()
        accounts.delete(db.session.scalar(db.select(User).filter_by(orcid=OTHER)), "self")
    assert _stored(app)[2] == [[ME]]


def test_connecting_an_orcid_puts_the_id_into_trash_and_steps(app, client, survey):
    with app.app_context():
        user = accounts.create_local("Lokale Person", "lokal@example.org", "", True,
                                     db.session.scalar(db.select(User).filter_by(orcid=ME)))
        db.session.add(SurveyMember(survey_id=1, orcid=user.orcid, user=user, name=user.name, role="editor", position=1))
        db.session.commit()
        uid = user.id
    key = f"u{uid}"  # an account made since 2b has no key: consents name it by account (items/consent.member_ref)
    _consent_everywhere(app, client, key)
    with app.app_context():
        accounts.link_orcid(db.session.get(User, uid), OTHER)
        db.session.commit()
    trash, steps, _ = _stored(app)
    assert trash == [[ME, OTHER]] and steps and all(r == [ME, OTHER] for r in steps if len(r) > 1)  # by account
    assert "lässt sich nicht" not in take_back(client)                     # the consent comes back, by account
    assert _stored(app)[2] == [[ME, OTHER]]
