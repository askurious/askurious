"""Undo and redo in the editor (app/undo.py): the last changes of each person to a draft questionnaire."""
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Block, EditStep, Item, Survey, SurveyMember
from tests.conftest import add, fill_consent

OTHER = "0000-0002-1825-0097"
E = "/surveys/1/editor"


def layout(app):
    with app.app_context():
        return [[i.id for i in b.items] for b in db.session.get(Survey, 1).blocks]


def labels(app, undone=False):
    """The labels of the steps that can be taken back (or redone), newest first."""
    with app.app_context():
        rows = db.session.scalars(db.select(EditStep).filter_by(survey_id=1, undone=undone).order_by(EditStep.id.desc()))
        return [s.label for s in rows]


def undo(client, **data):
    return client.post(f"{E}/undo", data=data, follow_redirects=True).data.decode()


def redo(client, **data):
    return client.post(f"{E}/redo", data=data, follow_redirects=True).data.decode()


def rand_node(app):
    with app.app_context():
        return next(n for n in db.session.get(Survey, 1).flow["children"] if n["t"] == "rand")


def colleague(app, role="admin"):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role=role, position=1))
        db.session.commit()
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    c.post("/terms/", data={"accept": "1"})
    return c


def test_adding_is_taken_back_and_redone(app, client, survey):
    add(client, "text_input")                                              # item 2
    assert layout(app) == [[1], [2]] and labels(app) == ["„Q1“ hinzugefügt (Freitext)"]
    html = undo(client)
    assert "Zurückgenommen: „Q1“ hinzugefügt (Freitext)." in html
    assert layout(app) == [[1], []]
    with app.app_context():
        assert not db.session.get(Survey, 1).trash                         # a question just added simply goes
    html = redo(client)
    assert "Wiederholt: „Q1“ hinzugefügt" in html and layout(app) == [[1], [2]]   # under its old number
    with app.app_context():
        assert db.session.get(Item, 2).code == "Q1"
    assert "Es gibt nichts mehr zu wiederholen." in redo(client)
    undo(client)
    assert "Es gibt nichts mehr zurückzunehmen." in undo(client)


def test_deleted_question_comes_back_at_its_place_and_leaves_the_trash(app, client, survey):
    for t in ("info", "text_input", "info"):                               # items 2, 3, 4
        add(client, t)
    client.post(f"{E}/items/3", data={"code": "alter", "question": "Wie alt?"})
    html = client.post(f"{E}/items/3/delete", follow_redirects=True).data.decode()
    # the message carries the button
    assert "„alter“ liegt im Papierkorb" in html and 'class="flash-undo"' in html and f'action="{E}/undo"' in html
    assert layout(app) == [[1], [2, 4]]
    html = undo(client)
    assert "Zurückgenommen: „alter“ gelöscht." in html and 'id="item-3"' in html
    assert layout(app) == [[1], [2, 3, 4]]
    with app.app_context():
        item = db.session.get(Item, 3)
        assert item.code == "alter" and item.texts["de"]["question"] == "Wie alt?" and not db.session.get(Survey, 1).trash
    redo(client)
    with app.app_context():
        assert layout(app) == [[1], [2, 4]] and db.session.get(Survey, 1).trash[0].data["code"] == "alter"


def test_moves_and_block_changes(app, client, survey):
    client.post(f"{E}/blocks")                                             # block 3
    for t in ("info", "text_input", "info"):                               # items 2, 3, 4 in block 2
        add(client, t)
    client.post(f"{E}/items/4/move/up")
    client.post(f"{E}/items/2/place", data={"block_id": 3, "position": 0})
    assert layout(app) == [[1], [4, 3], [2]]
    client.post(f"{E}/items/4/move/up")                                    # already on top: no step
    assert labels(app)[:2] == ["„Q1“ verschoben", "„Q3“ verschoben"]
    undo(client)
    assert layout(app) == [[1], [2, 4, 3], []]
    undo(client)
    assert layout(app) == [[1], [2, 3, 4], []]
    redo(client), redo(client)
    assert layout(app) == [[1], [4, 3], [2]]

    client.post(f"{E}/blocks/2", data={"title": "Szenario A", "show_title": "1"})
    client.post(f"{E}/blocks/3/move/up")
    with app.app_context():
        assert [b.id for b in db.session.get(Survey, 1).blocks] == [1, 3, 2]
    assert labels(app)[:2] == ["Block „Block 2“ verschoben", "Block in „Szenario A“ umbenannt"]
    undo(client), undo(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [b.id for b in s.blocks] == [1, 2, 3]
        assert s.blocks[1].titles["de"] != "Szenario A" and not s.blocks[1].show_title


def test_deleted_block_comes_back_whole(app, client, survey):
    """Page breaks, the block's ticks and its place in the flow come back too (the trash alone loses them)."""
    client.post(f"{E}/blocks"), client.post(f"{E}/blocks")                 # blocks 3, 4
    for t in ("info", "page_break", "text_input"):                         # items 2, 3, 4 in block 3
        add(client, t, block=3)
    client.post(f"{E}/blocks/3", data={"title": "Mitte", "randomize_items": "1"})
    client.post(f"{E}/flow/add", data={"kind": "rand"})
    rid = rand_node(app)["id"]
    client.post(f"{E}/flow/k3/place", data={"target": rid, "mode": "into"})
    assert rand_node(app)["children"] == [{"t": "block", "block": 3}]
    with app.app_context():
        before = db.session.get(Survey, 1).flow
    page = client.get(f"{E}/").data.decode()
    assert "data-confirm" not in page.split("/blocks/3/delete")[1].split("</form>")[0]   # no question: it can be taken back
    html = client.post(f"{E}/blocks/3/delete", follow_redirects=True).data.decode()
    assert "„Mitte“ gelöscht. 2 Fragen liegen zusätzlich 7 Tage im Papierkorb" in html and 'class="flash-undo"' in html
    assert layout(app) == [[1], [], []]
    assert client.post(f"{E}/undo").headers["Location"].endswith("#block-3")   # lands at the block that is back
    with app.app_context():
        s = db.session.get(Survey, 1)
        block = db.session.get(Block, 3)
        assert [i.id for i in block.items] == [2, 3, 4] and block.items[1].type == "page_break"
        assert block.titles["de"] == "Mitte" and block.randomize_items
        assert s.flow == before and not s.trash
    redo(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert db.session.get(Block, 3) is None and len(s.trash) == 2


def test_last_block_and_consent(app, client, survey):
    add(client, "info")                                                    # item 2 in block 2
    client.post(f"{E}/blocks/2/delete")                                    # a stand-in block appears with it
    assert layout(app) == [[1], []]
    undo(client)
    with app.app_context():
        assert [b.id for b in db.session.get(Survey, 1).blocks] == [1, 2] and layout(app) == [[1], [2]]
    fill_consent(client)
    client.post(f"{E}/items/1/delete")
    assert layout(app) == [[2]]
    undo(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert layout(app) == [[1], [2]] and s.consent_item.id == 1 and s.consent_item.texts["de"]["body"] == "Text"


def test_saved_element_and_landing(app, client, survey):
    add(client, "text_input")
    client.post(f"{E}/items/2", data={"code": "alter", "question": "Wie alt sind Sie?", "answer_mode": "required"})
    client.post(f"{E}/items/2", data={"code": "alter", "question": "Ihr Alter?", "answer_mode": "required"})
    client.post(f"{E}/items/2", data={"code": "alter", "question": "Ihr Alter?", "answer_mode": "required"})   # nothing new
    assert labels(app) == ["„alter“ bearbeitet", "„alter“ bearbeitet", "„Q1“ hinzugefügt (Freitext)"]
    r = client.post(f"{E}/undo")
    assert r.headers["Location"].endswith("/editor/items/2?lang=de")      # where the change shows
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"]["question"] == "Wie alt sind Sie?"
    r = client.post(f"{E}/undo")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.code == "Q1" and item.answer_mode == "optional"
    r = client.post(f"{E}/undo")                                           # the question is gone: its block
    assert r.headers["Location"].endswith("/editor/?lang=de") and layout(app) == [[1], []]


def test_flow_elements(app, client, survey):
    client.post(f"{E}/flow/add", data={"kind": "rand"})
    rid = rand_node(app)["id"]
    client.post(f"{E}/flow/{rid}/update", data={"name": "Varianten", "code": "var", "k": "1"})
    page = client.get("/surveys/1/flow/").data.decode()
    assert "data-confirm" not in page.split(f"/flow/{rid}/delete")[1].split("</form>")[0]
    html = client.post(f"{E}/flow/{rid}/delete", follow_redirects=True).data.decode()
    assert "Randomisierung „Varianten“ entfernt." in html and 'class="flash-undo"' in html
    assert labels(app)[0] == "Ablauf: Randomisierung „Varianten“ entfernt"
    r = client.post(f"{E}/undo")
    assert f"at={rid}" in r.headers["Location"] and "/flow/" in r.headers["Location"]
    assert rand_node(app)["id"] == rid and rand_node(app)["name"] == "Varianten"
    undo(client), undo(client)
    with app.app_context():
        assert all(n["t"] in ("block", "split") for n in db.session.get(Survey, 1).flow["children"])


def test_list_several_steps_and_limits(app, client, survey):
    for _ in range(4):
        add(client, "info")                                                # items 2..5
    page = client.get(f"{E}/").data.decode()
    assert "Meine letzten Änderungen" in page and page.count('name="to"') == 4
    assert 'title="Rückgängig: „Q4“ hinzugefügt (Text) (Strg+Z)"' in page
    with app.app_context():
        second = db.session.scalars(db.select(EditStep).order_by(EditStep.id)).all()[1].id
    html = undo(client, to=second)                                         # everything down to the second step
    assert "Zurückgenommen: 3 Schritte, zuletzt „„Q2“ hinzugefügt" in html
    assert layout(app) == [[1], [2]] and len(labels(app, undone=True)) == 3
    page = client.get(f"{E}/").data.decode()
    assert 'title="Wiederholen: „Q2“ hinzugefügt (Text) (Strg+Y)"' in page
    redo(client)
    assert layout(app) == [[1], [2, 3]]
    add(client, "info")                                                    # a new change: nothing to redo any more
    assert labels(app, undone=True) == [] and layout(app) == [[1], [2, 3, 6]]
    for _ in range(25):
        client.post(f"{E}/items/6/move/up"), client.post(f"{E}/items/6/move/down")
    assert len(labels(app)) == 20                                          # the last 20 are kept
    page = client.get(f"{E}/").data.decode()
    assert page.count("data-undo-older") == 15 and "15 ältere anzeigen" in page


def test_only_own_steps_and_nothing_of_others_is_overwritten(app, client, survey):
    add(client, "text_input")                                              # item 2
    client.post(f"{E}/items/2", data={"code": "Q1", "question": "Meine Fassung"})
    other = colleague(app)
    assert "Es gibt nichts mehr zurückzunehmen." in undo(other)            # my steps are not hers
    other.post(f"{E}/items/2", data={"code": "Q1", "question": "Fassung der Kollegin"})
    html = undo(client)
    assert ("„„Q1“ bearbeitet“ lässt sich nicht zurücknehmen: Die Frage „Q1“ wurde seitdem von Kollegin um" in html
            and "aus dem Verlauf genommen" in html)
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"]["question"] == "Fassung der Kollegin"
    assert labels(app) == ["„Q1“ bearbeitet", "„Q1“ hinzugefügt (Freitext)"]   # hers, and my first one
    undo(other)                                                            # she takes hers back: mine stands again
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"]["question"] == "Meine Fassung"


def test_question_deleted_for_good_does_not_come_back(app, client, survey):
    add(client, "text_input")
    client.post(f"{E}/items/2/delete")
    with app.app_context():
        entry = db.session.get(Survey, 1).trash[0].id
    client.post(f"{E}/trash/{entry}/destroy")
    assert "wurde endgültig aus dem Papierkorb gelöscht" in undo(client)
    assert layout(app) == [[1], []]


def test_restore_from_trash_is_a_step(app, client, survey):
    add(client, "text_input")
    client.post(f"{E}/items/2/delete")
    with app.app_context():
        entry = db.session.get(Survey, 1).trash[0].id
    client.post(f"{E}/trash/{entry}/restore")
    assert labels(app)[0] == "„Q1“ wiederhergestellt" and layout(app) == [[1], [2]]
    undo(client)                                                           # back into the trash, not gone
    with app.app_context():
        assert layout(app) == [[1], []] and db.session.get(Survey, 1).trash[0].data["id"] == 2


def test_only_drafts_and_only_editors(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post(f"{E}/items/2", data={"code": "Q1", "question": "Frage"})
    viewer = colleague(app, role="viewer")
    assert "data-undo" not in viewer.get(f"{E}/").data.decode()
    viewer.post(f"{E}/undo")
    assert layout(app) == [[1], [2]]
    assert "data-undo" in client.get(f"{E}/").data.decode()
    client.post("/surveys/1/start", data={})
    with app.app_context():
        assert db.session.get(Survey, 1).status == "running"
    assert labels(app) == []                                               # the start clears the history
    # in the field what may change there is a step and can be taken back: a text correction (UI audit R16)
    assert "data-undo" in client.get(f"{E}/").data.decode()
    client.post(f"{E}/items/2", data={"question": "Korrigiert"})
    assert len(labels(app)) == 1
    undo(client)
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"]["question"] == "Frage"


def test_steps_go_with_the_survey(app, client, survey):
    add(client, "info")
    client.post("/surveys/1/archive", data={"archived": "1"})
    client.post("/surveys/1/delete", data={"confirm": "1"})
    with app.app_context():
        assert db.session.get(Survey, 1) is None and db.session.scalar(db.select(db.func.count(EditStep.id))) == 0


def test_options_survive_a_round_trip(app, client, survey):
    add(client, "single_choice")
    client.post(f"{E}/items/2", data=MultiDict([
        ("code", "farbe"), ("question", "Lieblingsfarbe?"),
        ("option_id", ""), ("option_code", "1"), ("option_label", "Rot"),
        ("option_id", ""), ("option_code", "2"), ("option_label", "Blau"),
    ]))
    with app.app_context():
        item = db.session.get(Item, 2)
        config, texts = item.config, item.texts
    client.post(f"{E}/items/2/delete")
    undo(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config == config and item.texts == texts and item.type == "single_choice"


# --- stage 1b: translations, images, where the arrows are, every writing route -------------------------


def test_typed_translation_is_a_step(app, client, survey):
    add(client, "text_input")
    client.post(f"{E}/items/2", data={"code": "Q1", "question": "Wie alt?"})
    client.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "item:2", "path": "question", "text": "How old?"})
    assert labels(app)[0] == "Übersetzung (English) geändert: Q1 · Freitext"
    r = client.post(f"{E}/undo")
    assert "/translation/" in r.headers["Location"] and "lang=en" in r.headers["Location"]
    with app.app_context():
        assert not db.session.get(Item, 2).texts.get("en", {}).get("question")
    undo(client)                                                           # the step before it is not refused
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"].get("question") != "Wie alt?"


def test_ai_translation_in_chunks_is_one_step(app, client, survey, monkeypatch):
    import json

    from app import ai, translations

    for n in range(3):
        add(client, "text_input")
        client.post(f"{E}/items/{n + 2}", data={"code": f"Q{n + 1}", "question": f"Frage Nummer {n + 1}?"})
    monkeypatch.setattr(translations, "CHUNK_CHARS", 20)                   # one request per text

    def fake(instructions, prompt, purpose="text", item_type=None):
        entries = json.loads(prompt.split("Texts (JSON):\n", 1)[1])
        return json.dumps({e["id"]: "EN " + e["text"] for e in entries})

    monkeypatch.setattr(ai, "generate_text", fake)
    before = len(labels(app))
    calls = 0
    while client.post("/surveys/1/translation/ai?lang=en").json["left"]:
        calls += 1
    assert calls >= 2 and len(labels(app)) == before + 1 and labels(app)[0] == "KI-Übersetzung (English)"
    undo(client)
    with app.app_context():
        assert not any((db.session.get(Item, n).texts.get("en") or {}).get("question") for n in (2, 3, 4))


def test_steps_that_belong_together_join(app, client, survey):
    """done(merge=...): the same key continues the last step; another key, or a step in between, starts a new one."""
    from flask_login import login_user

    from app import undo as undo_mod
    from app import item_texts
    from app.models import User

    add(client, "text_input")

    def retitle(text, key):
        with app.test_request_context():
            login_user(db.session.scalar(db.select(User)))
            s = db.session.get(Survey, 1)
            step = undo_mod.begin(s)
            item = db.session.get(Item, 2)
            item.texts = {**item.texts, "de": {**item.texts["de"], "question": text}}
            item_texts.tidy(item)  # as the editor stores it: an empty question is the standard, not stored (S9)
            step.done(f"Fragetext: {text}", item=2, merge=key)
            db.session.commit()

    retitle("A", "item:2:question"), retitle("Ab", "item:2:question"), retitle("Abc", "item:2:question")
    assert labels(app) == ["Fragetext: Abc", "„Q1“ hinzugefügt (Freitext)"]
    retitle("Abc!", "item:2:other")                                        # another field: its own step
    assert len(labels(app)) == 3
    undo(client), undo(client)                                             # both steps back: the text from before "A"
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"].get("question") not in ("A", "Ab", "Abc", "Abc!")
    retitle("X", "k"), retitle("", "k")                                    # back where the step began: no step left
    with app.app_context():
        was = db.session.get(Item, 2).texts["de"].get("question", "")
    assert was == "" and labels(app)[0] != "Fragetext: "


def test_image_prompt_is_a_step_and_arrows_only_where_the_survey_is_edited(app, client, survey):
    add(client, "image")
    client.post(f"{E}/items/2/generate", data={"prompt": "Ein Roboter mit {{farbe}} Hut"})
    assert labels(app)[0] == "Bild-Prompt von „Q1“ geändert"
    undo(client)
    with app.app_context():
        assert not db.session.get(Item, 2).config.get("prompt")
    for path, there in (("/editor/", True), ("/flow/", True), ("/translation/", True), ("", True), ("/flow/quotas/", True),
                        ("/field", True), ("/data", False), ("/results", True), ("/quality", True), ("/dashboards/", True)):
        page = client.get(f"/surveys/1{path}", follow_redirects=True)
        assert page.status_code == 200, path
        assert ("data-undo" in page.data.decode()) == there, path


# Writing routes of the editor that leave no step, each with its reason. A new route that changes blocks,
# questions or the flow belongs to the steps (undo.begin / step.done), not into this list.
NO_STEP = {
    "editor.preview_survey": "answers a test run", "editor.preview_comment": "comment", "editor.preview_comment_delete": "comment",
    "editor.handle_comment": "comment", "editor.presence": "who has a page open", "editor.item_frame": "trial, rolled back",
    "editor.undo": "takes steps back", "editor.redo": "redoes steps", "editor.ai_ideas": "suggestions only",
    "editor.flow_ws_test": "a test call of a web service, nothing stored",
    "editor.item_images_generate": "image files", "editor.item_images_approve_all": "image files",
    "editor.item_images_upload": "image files", "editor.item_media_upload": "video or audio file (outside the history)",
    "editor.item_images_review": "suggestions, applied by item_images_review_apply", "editor.review_asset": "image files",
    "editor.image_rights": "a rights declaration kept with the image file (Asset.rights), not the questionnaire",
    "editor.text_review_item": "suggestions, applied by text_review_apply", "editor.text_review_survey": "suggestions, applied by text_review_apply",
    "translations.confirm": "marks translations as checked, changes no text",
    "flow_page.quota_steer": "steers a running survey: no history there",
    "flow_page.quota_recount": "counts", "flow_page.quota_share_add": "links", "flow_page.quota_share_revoke": "links",
}


def test_every_writing_route_of_the_editor_leaves_a_step_or_says_why_not(app):
    import inspect

    missing, stale = [], []
    for rule in app.url_map.iter_rules():
        if "POST" not in rule.methods or rule.endpoint.split(".")[0] not in ("editor", "flow_page", "translations"):
            continue
        steps = "undo.begin" in inspect.getsource(app.view_functions[rule.endpoint])
        if not steps and rule.endpoint not in NO_STEP:
            missing.append(rule.endpoint)
        if steps and rule.endpoint in NO_STEP:
            stale.append(rule.endpoint)
    assert not missing, "ohne Schritt und ohne Begründung in NO_STEP: " + ", ".join(sorted(set(missing)))
    assert not stale, "legt Schritte an, steht aber noch in NO_STEP: " + ", ".join(sorted(set(stale)))
