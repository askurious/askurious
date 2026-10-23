from tests.instrument_permissions import grant_instrument
import base64
import io
import re

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Block, Item, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def layout(app):
    with app.app_context():
        s = db.session.get(Survey, 1)
        return [[i.id for i in b.items] for b in s.blocks]


def test_all_item_types_render(client, survey):
    from app import items

    types = [t for t in items.REGISTRY if t != "consent" and not getattr(items.REGISTRY[t], "interview_only", False)
             and not getattr(items.REGISTRY[t], "experimental", None)]
    for t in types:
        add(client, t)
    assert client.get("/surveys/1/editor/").status_code == 200
    for item_id in range(1, len(types) + 2):  # + the consent (item 1)
        for lang in ("de", "en"):
            assert client.get(f"/surveys/1/editor/items/{item_id}?lang={lang}").status_code == 200
    r = add(client, "consent")                           # a second consent is refused
    assert r.status_code == 302 and "Es gibt bereits eine Einwilligung" in client.get("/surveys/1/editor/").data.decode()

def test_insert_at_position_and_drag(app, client, survey):
    client.post("/surveys/1/editor/blocks")                 # block 3
    for t in ("info", "text_input", "info"):                # items 2, 3, 4 in block 2
        add(client, t)
    add(client, "page_break", position=1)                   # item 5
    assert layout(app) == [[1], [2, 5, 3, 4], []]
    client.post("/surveys/1/editor/items/4/place", data={"block_id": 3, "position": 0})
    assert layout(app) == [[1], [2, 5, 3], [4]]
    client.post("/surveys/1/editor/items/2/place", data={"block_id": 2, "position": 99})
    assert layout(app) == [[1], [5, 3, 2], [4]]
    client.post("/surveys/1/editor/items/3/move/next_block")
    assert layout(app) == [[1], [5, 2], [4, 3]]
    client.post("/surveys/1/editor/items/3/move/prev_block")
    assert layout(app) == [[1], [5, 2, 3], [4]]
    with app.app_context():
        assert db.session.get(Block, 2).pages() == [[db.session.get(Item, 2), db.session.get(Item, 3)]]

def test_drop_between_blocks_makes_a_new_block(app, client, survey):
    """An element dropped between two blocks (new_block = before/after, next to block_id) gets a block of its own
    at that place: an existing element by /place, a new one from the palette by /blocks/<id>/items."""
    E = "/surveys/1/editor"
    client.post(E + "/blocks")                              # block 3
    for t in ("info", "text_input"):                        # items 2, 3 in block 2
        add(client, t)
    order = lambda: [b.id for b in db.session.get(Survey, 1).blocks]
    page = client.get(E + "/").data.decode()
    assert 'data-item-id="2" data-drag' in page and 'draggable="true"' not in page  # dragging is the editor's own (app.js)
    client.post(E + "/items/3/place", data={"block_id": 2, "new_block": "after"})
    assert layout(app) == [[1], [2], [3], []]               # block 4 between blocks 2 and 3
    with app.app_context():
        assert order() == [1, 2, 4, 3] and db.session.get(Block, 4).titles["de"]
        assert [n.get("block") for n in db.session.get(Survey, 1).flow["children"]][:4] == [1, 2, 4, 3]
    html = client.post(E + "/undo", follow_redirects=True).data.decode()        # one step: the block goes, the element is back
    assert layout(app) == [[1], [2, 3], []] and "Zurückgenommen: „Q2“ in einen neuen Block verschoben." in html
    client.post(E + "/redo")
    assert layout(app) == [[1], [2], [3], []]
    with app.app_context():
        assert order() == [1, 2, 4, 3]
    client.post(E + "/items/2/place", data={"block_id": 1, "new_block": "before"})  # also before the consent block
    assert layout(app) == [[2], [1], [], [3], []]
    r = client.post(E + "/blocks/3/items", data={"type": "info", "new_block": "after"})  # from the palette
    assert r.status_code == 302 and layout(app) == [[2], [1], [], [3], [], [4]]
    client.post(E + "/undo")                               # again one step: block and element
    assert layout(app) == [[2], [1], [], [3], []]
    client.post(E + "/redo")
    assert layout(app) == [[2], [1], [], [3], [], [4]]
    with app.app_context():
        assert order() == [5, 1, 2, 4, 3, 6]
    client.post(E + "/items/1/place", data={"block_id": 3, "new_block": "after"})   # the consent stays in its block
    assert layout(app) == [[2], [1], [], [3], [], [4]]
    assert client.post(E + "/blocks/3/items", data={"type": "nope", "new_block": "after"}).status_code == 400
    assert len(layout(app)) == 6                            # an unknown type leaves no empty block behind
    with app.app_context():                                 # between the blocks of a randomizer: the new block joins it
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 5}, {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "rand", "id": "r1", "name": "Bild", "code": "bild", "k": 1,
             "children": [{"t": "block", "block": 4}, {"t": "block", "block": 3}]}, {"t": "block", "block": 6}]}
        db.session.commit()
    client.post(E + "/items/4/place", data={"block_id": 4, "new_block": "after"})
    with app.app_context():
        rand = next(n for n in db.session.get(Survey, 1).flow["children"] if n["t"] == "rand")
        assert [n["block"] for n in rand["children"]] == [4, 7, 3] and db.session.get(Item, 4).block_id == 7
    assert "Der neue Block gehört zu: 🎲 Bild" in client.get(E + "/").data.decode()


def test_answer_modes(app, client, survey):
    add(client, "text_input")
    for mode in ("nudge", "required", "optional", "bogus"):
        client.post("/surveys/1/editor/items/2", data={"code": "Q1", "answer_mode": mode})
        with app.app_context():
            assert db.session.get(Item, 2).answer_mode == (mode if mode != "bogus" else "optional")


def test_likert_reverse_coding(app, client, survey):
    add(client, "likert")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "TA"), ("points", "5"), ("labeling", "full"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B"), ("stmt_reverse", "s2"),
    ]))
    with app.app_context():
        i = db.session.get(Item, 2)
        assert i.kind.export_values(i, {"s1": 2, "s2": 2}) == {"TA_1": 2, "TA_2": 4}
        assert i.kind.is_missing(i, {"s1": 2})


def test_likert_answer_scale_presets(app, client, survey):
    add(client, "likert")
    base = [("code", "TA"), ("points", "4"), ("labeling", "full"), ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A")]
    client.post("/surveys/1/editor/items/2", data=MultiDict(base + [("anchors", "likely")]))
    with app.app_context():
        i = db.session.get(Item, 2)
        assert i.config["anchors"] == "likely"
        assert i.kind.labels(i, "de") == ["sehr unwahrscheinlich", "eher unwahrscheinlich", "eher wahrscheinlich",
                                          "sehr wahrscheinlich"]
        assert i.kind.labels(i, "en")[-1] == "very likely"
    # own wording: labels from the form are kept
    own = [("label", x) for x in ("nie", "selten", "oft", "ständig")]
    client.post("/surveys/1/editor/items/2", data=MultiDict(base + [("anchors", "custom")] + own))
    with app.app_context():
        i = db.session.get(Item, 2)
        assert i.config["anchors"] == "custom" and i.kind.labels(i, "de")[-1] == "ständig"


def test_translation_view_is_text_only_with_fallback(app, client, survey):
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/2?lang=de", data=MultiDict([
        ("code", "g"), ("question", "Frage"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Nein"),
    ]))
    client.post("/surveys/1/editor/items/2?lang=en", data=MultiDict([
        ("question", "Question"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Yes"),
    ]))
    with app.app_context():
        i = db.session.get(Item, 2)
        assert len(i.config["options"]) == 2
        assert [o["label"] for o in i.kind.options(i, "en")] == ["Yes", "Nein"]


def test_running_survey_accepts_text_fixes_only(app, client, survey):
    fill_consent(client)
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "g"), ("question", "Frage"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Nein"),
    ]))
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "HACK"), ("question", "Frage?"), ("option_id", "a"), ("option_code", "9"), ("option_label", "Jawohl"),
    ]))
    with app.app_context():
        i = db.session.get(Item, 2)
        assert i.code == "g" and [o["code"] for o in i.config["options"]] == ["1", "2"]
        assert i.texts["de"]["question"] == "Frage?" and i.texts["de"]["options"]["a"] == "Jawohl"
    add(client, "info")
    assert layout(app) == [[1], [2]]

def test_image_upload_and_preview(client, survey):
    add(client, "image")
    r = client.post("/surveys/1/editor/items/2/upload", data={"file": (io.BytesIO(b"<svg/>"), "x.svg")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "Nur PNG" in r.data.decode()
    client.post("/surveys/1/editor/items/2/upload", data={"file": (io.BytesIO(PNG), "x.png")},
                content_type="multipart/form-data")
    page = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    url = re.search(r'/media/[^"]+', page).group(0)
    assert client.get(url).mimetype == "image/webp"


def test_generate_without_key_fails_gracefully(client, survey):
    add(client, "image")
    r = client.post("/surveys/1/editor/items/2/generate", data={"prompt": "a robot", "size": "1024x1024"},
                    follow_redirects=True)
    assert "Kein OpenAI-Schlüssel" in r.data.decode()


def test_editor_stats(app, client, survey):
    for t in ("info", "text_input", "single_choice"):
        add(client, t)
    client.post("/surveys/1/editor/blocks/2", data={"title": "B", "randomize_items": "1"})
    from app import design

    with app.app_context():
        st = design.stats(db.session.get(Survey, 1))
    # questions: text, choice and the consent; pages: consent + one content page
    assert st["paths"] == 1 and st["orders"] == 6 and st["questions"] == 3 and st["pages"] == 2
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})  # 10 statements, random order
    with app.app_context():
        st = design.stats(db.session.get(Survey, 1))
    assert st["paths"] == 1 and st["orders"] == 24     # statement order inside a scale does not count

def test_likert_label_display_once(app, client, survey):
    add(client, "likert")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "TA"), ("points", "5"), ("labeling", "full"), ("label_display", "once"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
    ]))
    page = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    assert "p-likert-once" in page


def test_text_validation(app, client, survey):
    import pytest
    from app.items.base import AnswerError

    add(client, "text_input")
    cases = {
        ("integer", "18", "99"): [("42", 42), ("17", AnswerError), ("4.5", AnswerError), ("abc", AnswerError), ("", None)],
        ("decimal", "0", "10"): [("2,5", 2.5), ("2.5", 2.5), ("10", 10.0), ("11", AnswerError),
                                ("nan", AnswerError), ("inf", AnswerError)],
        ("email", "", ""): [("a@b.de", "a@b.de"), ("nope", AnswerError)],
        ("plz_de", "", ""): [("52062", "52062"), ("5206", AnswerError)],
        ("regex", "", ""): [("AB1234", "AB1234"), ("ab1234", AnswerError)],
    }
    for (kind, lo, hi), checks in cases.items():
        client.post("/surveys/1/editor/items/2", data={"code": "Q1", "validation": kind, "min": lo, "max": hi,
                                                        "pattern": r"[A-Z]{2}\d{4}", "validation_message": "Format AB1234"})
        with app.app_context():
            item = db.session.get(Item, 2)
            assert item.kind.validate(item) == []
            for value, expected in checks:
                if expected is AnswerError:
                    with pytest.raises(AnswerError):
                        item.kind.parse_answer(item, {"i2": value})
                else:
                    assert item.kind.parse_answer(item, {"i2": value}) == expected, (kind, value)
    with app.app_context():
        item = db.session.get(Item, 2)
        with pytest.raises(AnswerError, match="Format AB1234"):
            item.kind.parse_answer(item, {"i2": "x"})
    client.post("/surveys/1/editor/items/2", data={"code": "Q1", "validation": "regex", "pattern": "([a-z"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert "ungültig" in item.kind.validate(item)[0]
    client.post("/surveys/1/editor/items/2", data={"code": "Q1", "validation": "integer", "min": "18", "max": "99"})
    page = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    assert 'inputmode="numeric"' in page and "von 18 bis 99" in page


def test_rating_styles(app, client, survey):
    import pytest
    from app.items.base import AnswerError

    add(client, "rating")
    for style, extra, ok, bad, rev in [
        ("smileys", {"points": "7"}, "6", "8", (6, 2)),
        ("stars", {"points": "10"}, "10", "0", (10, 1)),
        ("numbers", {"min": "0", "max": "10"}, "0", "11", (0, 10)),
        ("slider", {"min": "0", "max": "100", "step": "0.5"}, "37.5", "101", (37.5, 62.5)),
    ]:
        client.post("/surveys/1/editor/items/2", data={"code": "R", "style": style, "reverse": "1", **extra})
        with app.app_context():
            item = db.session.get(Item, 2)
            assert item.config["style"] == style and item.kind.validate(item) == []
            v = item.kind.parse_answer(item, {"i2": ok})
            assert item.kind.export_values(item, v) == {"R": rev[1]}, style
            with pytest.raises(AnswerError):
                item.kind.parse_answer(item, {"i2": bad})
            assert item.kind.parse_answer(item, {"i2": ""}) is None
        page = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
        assert f"p-rating-{style}" in page
    client.post("/surveys/1/editor/items/2", data={"code": "R", "style": "numbers", "min": "0", "max": "50"})
    with app.app_context():  # from the slider: its range does not come along (Ralf, 2026-10-21)
        assert db.session.get(Item, 2).config["max"] == 10
    client.post("/surveys/1/editor/items/2", data={"code": "R", "style": "numbers", "min": "0", "max": "50"})
    with app.app_context():  # typed on a row of numbers: kept and named as a problem
        item = db.session.get(Item, 2)
        assert "höchstens 21" in item.kind.validate(item)[0]


def test_a_row_of_numbers_does_not_take_over_the_range_of_0_to_100(app, client, survey):
    """Ralf, 2026-10-21 (interview sjur42): a rating switched from the start (smileys, range 0 to 100 kept in the
    config) to "Zahlenreihe" had 101 buttons, drawn as a chain of digits. The row gets the standard 0 to 10, and a row
    longer than the editor allows is drawn as a slider."""
    add(client, "rating")
    client.post("/surveys/1/editor/items/2", data={"code": "R", "style": "numbers", "min": "0", "max": "100"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert (item.config["min"], item.config["max"]) == (0, 10) and item.kind.validate(item) == []
        item.config = {**item.config, "max": 100.0}  # as stored before the fix
        db.session.commit()
    page = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    assert "p-rating-slider" in page and 'class="p-range" min="0" max="100.0"' in page and "p-number" not in page


def test_whole_survey_preview(client, survey):
    client.post("/surveys/1/editor/blocks")                  # block 3
    add(client, "info", block=2)                             # 2
    client.post("/surveys/1/editor/items/2", data={"code": "a", "question": "Eins"})
    add(client, "page_break", block=2)                       # 3
    add(client, "info", block=2)                             # 4
    client.post("/surveys/1/editor/items/4", data={"code": "b", "question": "Zwei"})
    add(client, "info", block=3)                             # 5
    client.post("/surveys/1/editor/items/5", data={"code": "c", "question": "Drei"})
    for page, text in ((1, "Einwilligung"), (2, "Eins"), (3, "Zwei"), (4, "Drei")):
        html = client.get(f"/surveys/1/editor/preview?page={page}", follow_redirects=True).data.decode()
        assert text in html and f"Seite {page}/4" in html
    assert "/editor/preview" in client.get("/surveys/1/editor/").data.decode()

def test_preview_runs_all_checks(app, client, survey):
    from app.models import Response

    P = "/surveys/1/editor/preview"
    fill_consent(client)
    add(client, "text_input")        # 2 required, integer 18-99
    add(client, "single_choice")     # 3 nudge
    add(client, "page_break")        # 4
    add(client, "info")              # 5
    client.post("/surveys/1/editor/items/2", data={"code": "age", "question": "Alter", "answer_mode": "required",
                                                    "validation": "integer", "min": "18", "max": "99"})
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "g"), ("question", "G"), ("answer_mode", "nudge"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "A"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "B")]))
    client.post("/surveys/1/editor/items/5", data={"code": "end", "question": "Letzte Seite"})

    assert "Seite 1/3" in client.get(P).data.decode()
    assert client.post(P, data=CONSENT_OK).status_code == 302
    assert "Seite 2/3" in client.get(P).data.decode()
    # required + validation: blocked, typed value shown again
    html = client.post(P, data={"nav": "next", "i2": "12"}).data.decode()
    assert "von 18 bis 99" in html and 'value="12"' in html and "has-error" in html
    # valid age but nudge item open: soft warning, same page
    html = client.post(P, data={"nav": "next", "i2": "42"}).data.decode()
    assert "has-nudge" in html and 'name="nudge_ack"' in html and "Seite 2/3" in html
    # second click continues
    assert client.post(P, data={"nav": "next", "i2": "42", "nudge_ack": "1"}).status_code == 302
    html = client.get(P).data.decode()
    assert "Letzte Seite" in html and "Absenden" in html and "Zurück" in html
    # back shows the stored answer
    client.post(P, data={"nav": "back"})
    assert 'value="42"' in client.get(P).data.decode()
    client.post(P, data={"nav": "next", "i2": "42", "i3": "2"})
    client.post(P, data={"nav": "next"})
    html = client.get(P).data.decode()
    assert "Vorschau beendet" in html and "<code>age</code>: 42" in html
    with app.app_context():
        r = db.session.get(Response, 1)
        assert r.is_preview and r.status == "completed" and r.answers == {"1": "1", "2": 42, "3": "2"}
        assert db.session.get(Survey, 1).completed_count() == 0  # previews never count
    assert "Seite 1/3" in client.get(P + "?restart=1").data.decode()

def test_preview_back_button_setting_and_consent_decline(app, client, survey):
    P = "/surveys/1/editor/preview"
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de"], "default_language": "de"})  # allow_back off
    add(client, "text_input")        # 2: a screening question before the consent
    client.post("/surveys/1/editor/blocks/2/move/up")   # content block above the consent block
    add(client, "info", block=2)     # 3
    html = client.get(P + "?page=2", follow_redirects=True).data.decode()
    assert 'class="p-nav"' not in html  # consent page: its own buttons, and no Back (setting off)
    client.get(P + "?page=1")
    assert "Zurück" not in client.get(P).data.decode()
    client.post(P, data={"nav": "next", "i2": "Screening-Antwort"})
    # agreeing without ticks is refused, declining ends the run and discards earlier answers
    assert "alle Punkte" in client.post(P, data={"i1": "1"}).data.decode()
    client.post(P, data={"i1": "0"})
    assert "nicht teilnehmen möchten" in client.get(P).data.decode()
    from app.models import Response

    with app.app_context():
        assert db.session.get(Response, 1).answers == {"1": "0"}

def test_mobile_preview_and_button_order(client, survey):
    add(client, "info")
    add(client, "page_break")
    add(client, "info")
    P = "/surveys/1/editor/preview"
    client.get(P + "?page=2")
    html = client.get(P + "?device=mobile").data.decode()
    assert "data-device-frame" in html and "frame=1" in html
    # the narrowest common phone, the usual ones and a tablet (Ralf, 2026-10-07)
    assert all(f'value="{v}"' in html for v in ("360x780", "375x667", "390x844", "412x915", "430x932", "820x1180"))
    inner = client.get(P + "?frame=1").data.decode()
    assert "p-preview-bar" not in inner and 'data-page="2"' in inner
    # navigating inside the frame keeps the frame
    r = client.post(P + "?frame=1", data={"nav": "next"})
    assert "frame=1" in r.headers["Location"]
    client.get(P + "?device=desktop")
    html = client.get(P).data.decode()
    assert "p-preview-bar" in html
    # Enter submits the first button: Next must come before Back in the source
    nav = html.split('class="p-nav"')[1]
    assert nav.index('value="next"') < nav.index('value="back"')

def test_preview_validation_toggle(client, survey):
    P = "/surveys/1/editor/preview"
    add(client, "text_input")        # 2
    add(client, "page_break")
    add(client, "info")
    client.post("/surveys/1/editor/items/2", data={"code": "age", "answer_mode": "required", "validation": "integer"})
    client.get(P + "?page=2")
    assert "has-error" in client.post(P, data={"nav": "next", "i2": "abc"}).data.decode()
    html = client.get(P + "?validate=0").data.decode()
    box = html.split('class="p-validate"')[1].split("</label>")[0]
    assert "data-preview-param=\"validate\"" in box and "checked>" not in box and " checked\n" not in box
    assert client.post(P, data={"nav": "next", "i2": "abc"}).status_code == 302  # passes unchecked
    assert "Seite 3/3" in client.get(P).data.decode()
    client.get(P + "?validate=1")
    client.get(P + "?page=2")
    assert "has-error" in client.post(P, data={"nav": "next"}).data.decode()

def test_phone_preview_link(app, client, survey):
    import re

    add(client, "info")
    add(client, "page_break")
    add(client, "info")
    html = client.get("/surveys/1/editor/preview?device=mobile").data.decode()
    assert "<svg" in html and "Smartphone" in html
    link = re.search(r'value="(https?://[^"]+/p/[^"]+)"', html).group(1)
    path = link.split("://", 1)[1].split("/", 1)[1]
    phone = app.test_client()  # not logged in
    page = phone.get("/" + path).data.decode()
    assert "Seite 1/3" in page and "p-device" not in page
    phone.get("/" + path + "?page=2")
    assert phone.post("/" + path, data={"nav": "next"}).status_code == 302
    assert "Seite 3/3" in phone.get("/" + path).data.decode()
    assert phone.get("/p/forged-token").status_code == 404
    # expired
    import time
    with app.app_context():
        from app import editor
        s = editor._share_serializer()
        old = s.dumps({"s": 1, "u": "0000-0000-0000-001X"})
    real_time = time.time
    try:
        time.time = lambda: real_time() + 25 * 3600
        assert phone.get(f"/p/{old}").status_code == 410
    finally:
        time.time = real_time


def test_image_style_applies_to_prompts(app, client, survey):
    add(client, "image")
    client.post("/surveys/1/image-style", data={"key": "watercolor"})
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "source": "ai", "prompt": "A robot hands a glass of water"}
        db.session.commit()
        prompt = item.kind.full_prompt(item)
        assert prompt.startswith("Style: Light, soft ink drawing") and "Scene: A robot" in prompt and "no text" in prompt
    html = client.get("/surveys/1").data.decode()
    assert "styles/watercolor_care.jpg" in html and re.search(r'value="watercolor"[^>]*checked', html)
    client.post("/surveys/1/image-style", data={"key": "custom", "custom": "Pastel pencil"})
    with app.app_context():
        from app import image_styles

        s = db.session.get(Survey, 1)
        assert image_styles.style_prompt(s) == "Pastel pencil" and image_styles.style_id(s).startswith("custom:")


def test_auto_advance_only_on_one_click_pages(app, client, survey):
    from app import runtime

    add(client, "single_choice")     # 2
    add(client, "page_break")
    add(client, "rating")            # 4 smileys by default
    add(client, "page_break")
    add(client, "text_input")        # 6
    with app.app_context():
        s = db.session.get(Survey, 1)
        pages = [p for b in s.blocks for p in b.pages()]
        assert [runtime.auto_advance_ok(s, p) for p in pages] == [False, False, False, False]  # setting off
        s.auto_advance = True
        db.session.commit()
        assert [runtime.auto_advance_ok(s, p) for p in pages] == [False, True, True, False]  # consent, choice, rating, text
        db.session.get(Item, 4).config = {**db.session.get(Item, 4).config, "style": "slider"}
        db.session.commit()
        assert runtime.auto_advance_ok(s, pages[2]) is False
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "data-auto-advance" in html
    html = client.get("/surveys/1/editor/preview?page=4", follow_redirects=True).data.decode()
    assert "data-auto-advance" not in html
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de", "en"], "default_language": "de"})
    with app.app_context():
        assert db.session.get(Survey, 1).auto_advance is False  # unchecked in the form -> off


def test_edited_preset_style_becomes_custom(app, client, survey):
    from app.image_styles import IMAGE_STYLES

    client.post("/surveys/1/image-style", data={"key": "flat", "custom": IMAGE_STYLES["flat"][2]})
    with app.app_context():
        assert db.session.get(Survey, 1).image_style["key"] == "flat"
    html = client.get("/surveys/1").data.decode()
    assert IMAGE_STYLES["flat"][2][:40] in html.split("data-style-text")[1]
    client.post("/surveys/1/image-style", data={"key": "flat", "custom": IMAGE_STYLES["flat"][2] + " Warm colors."})
    with app.app_context():
        st = db.session.get(Survey, 1).image_style
        assert st["key"] == "custom" and st["custom"].endswith("Warm colors.")


def test_semantic_differential(app, client, survey):
    import pytest
    from app.items.base import AnswerError

    add(client, "semantic")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "GS"), ("answer_mode", "required"), ("points", "5"), ("numbers", "1"),
        ("pair_id", "p1"), ("pair_left", "unfreundlich"), ("pair_right", "freundlich"), ("pair_code", ""),
        ("pair_id", "p2"), ("pair_left", "kompetent"), ("pair_right", "inkompetent"), ("pair_code", ""), ("pair_reverse", "p2")]))
    with app.app_context():
        i = db.session.get(Item, 2)
        assert i.kind.validate(i) == []
        assert i.kind.export_values(i, {"p1": 4, "p2": 2}) == {"GS_1": 4, "GS_2": 4}  # p2 reverse coded
        with pytest.raises(AnswerError):
            i.kind.parse_answer(i, {"i2_p1": "3"})
    page = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "unfreundlich" in page and "p-sd-row" in page and 'name="i2_p2"' in page
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "1 = inkompetent; 5 = kompetent" in book and "rekodiert" in book   # recoded, so the poles flip


def test_construct_toolbox(app, client, survey):
    from app.export import codebook_csv, readme_md

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})
    grant_instrument(client.application, "kut")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kut"})
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gender"})
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})  # second copy
    with app.app_context():
        bfi, kut, gender, bfi2 = (db.session.get(Item, n) for n in (2, 3, 4, 5))
        assert (bfi.code, kut.code, gender.code, bfi2.code) == ("BFI", "KUT", "gender", "BFI1")
        assert [s["code"] for s in bfi2.config["statements"]][:2] == ["BFI1_1", "BFI1_2"]
        assert all(i.kind.validate(i) == [] for i in (bfi, kut, gender))
        assert [s["reverse"] for s in kut.config["statements"]] == [False, True, True, False, True, True, False, False]
        assert bfi.kind.export_values(bfi, {bfi.config["statements"][0]["id"]: 2})["BFI_1"] == 4  # reverse coded
        assert gender.texts["en"]["question"] == "What is your gender?"
        sid = kut.config["statements"][0]["id"]
    # locked: structure cannot change, wording can
    page = client.get("/surveys/1/editor/items/3").data.decode()
    assert "Validiertes Konstrukt" in page and "Beier" in page
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "KUT"), ("answer_mode", "required"), ("points", "7"),
        ("stmt_id", sid), ("stmt_code", "X"), ("stmt_text", "Korrigiert.")]))
    with app.app_context():
        kut = db.session.get(Item, 3)
        assert kut.config["points"] == 5 and len(kut.config["statements"]) == 8 and kut.answer_mode == "required"
        assert kut.texts["de"]["statements"][sid] == "Korrigiert."
        book = codebook_csv(kut.survey)
        assert "VERÄNDERTES Konstrukt Kontrollüberzeugung" in book and "Wortlaut (DE): Fragetext, Aussage KUT_1" in book  # typo fix counts
        assert "Konstrukt Big Five" in book and "VERÄNDERTES Konstrukt Big Five" not in book
    client.post("/surveys/1/editor/items/3/unlock")
    with app.app_context():
        s = db.session.get(Item, 3).survey
        assert "## Instruments" in readme_md(s) and "`KUT` (modified)" in readme_md(s) and "`BFI` (modified)" not in readme_md(s)
    assert "Konstrukte durchsuchen" in client.get("/surveys/1/editor/").data.decode()



def test_block_names(app, client, survey):
    r = client.post("/surveys/1/editor/blocks")
    assert r.headers["Location"].endswith("#rename-3")          # the new block's name is selected for typing
    client.post("/surveys/1/editor/blocks/3", data={"title": "Szenario A: passive Nutzung"})
    page = client.get("/surveys/1/editor/").data.decode()
    assert 'value="Szenario A: passive Nutzung"' in page and "Namen auch Teilnehmenden zeigen" in page
    assert "Szenario A: passive Nutzung" in client.get("/surveys/1/flow/").data.decode()


def test_loop_page_guided_and_not_on_consent(app, client, survey):
    from app import loops

    editor = client.get("/surveys/1/editor/").data.decode()
    assert editor.count('🔁 Schleife um diesen Block') == 1  # only the question block, not the consent
    # in the block's settings, not in its head (UI audit 2026-10-12, U21)
    assert 'form="loop-form-2"' in editor.split('<details class="block-settings">')[1].split("</details>")[0]
    r = client.post("/surveys/1/editor/blocks/2/loop")              # puts block 2 into a new loop
    url = r.headers["Location"].split("?")[0]
    assert "/flow/loop/" in url
    page = client.get(url).data.decode()
    assert "So funktioniert es" in page and "Tabelle ausfüllen" in page and "nur intern" in page
    assert 'name="col_id" value="cstart"' in page                    # a ready column and two rows to fill in
    client.post(url, data=MultiDict([
        ("name", "Roboter"), ("col_id", "cstart"), ("col_name", "robot"),
        ("row_id", "rstart1"), ("cell_rstart1_cstart", "humanoid"), ("row_id", "rstart2"), ("cell_rstart2_cstart", ""),
        ("mode", "all"), ("order", "random"), ("code", "loop2")]))
    with app.app_context():
        loop = loops.config(db.session.get(Block, 2))
        assert loop["enabled"] and loop["name"] == "Roboter" and [c["name"] for c in loop["columns"]] == ["robot"]
        assert len(loop["rows"]) == 1
    assert "{{robot}}" in client.get(url).data.decode()
    assert "🔁 Roboter" in client.get("/surveys/1/editor/").data.decode()
    r = client.get("/surveys/1/editor/blocks/1/loop")                # consent: never a loop
    assert r.status_code == 302 and "/editor/" in r.headers["Location"]


def test_consent_defaults_open_data_and_ethics(app, client, survey):
    from app.export import readme_md

    with app.app_context():
        consent = db.session.get(Survey, 1).consent_item
        assert consent.config["open_data"] is True and consent.config["ethics"] is False   # X1: ethics vote unticked
    page = client.get("/surveys/1/editor/items/1").data.decode()               # the element only sums the facts up
    assert "in den Einstellungen ändern" in page and 'name="ethics"' not in page and 'name="repository"' not in page
    page = client.get("/surveys/1").data.decode()                              # they are kept in the settings
    assert 'id="consent-facts"' in page and "Ethikvotum liegt vor" in page and "falls schon bekannt" in page
    client.post("/surveys/1/consent-facts", data={
        "min_age": "18", "researchers": "0000-0000-0000-001X", "open_data": "1", "ethics": "1",
        "ethics_ref": "Ethikkommission der RWTH Aachen, EK 123/26"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        consent = s.consent_item
        assert consent.config["ethics"] and consent.config["ethics_ref"].endswith("EK 123/26")
        body = consent.kind.template_body(consent, "de")
        assert "Ethische Prüfung" in body and "EK 123/26" in body and "Open Data" in body
        assert "Ethics: approved by an ethics committee (Ethikkommission der RWTH Aachen, EK 123/26)." in readme_md(s)


def test_answer_mode_badge_on_every_question(app, client, survey):
    for t in ("single_choice", "text_input", "info"):
        add(client, t)                                             # 2, 3, 4
    client.post("/surveys/1/editor/items/2", data={"code": "a", "answer_mode": "required"})
    client.post("/surveys/1/editor/items/3", data={"code": "b", "answer_mode": "nudge"})
    page = client.get("/surveys/1/editor/").data.decode()
    assert 'mode-required" title="Ohne Antwort geht es nicht weiter">Pflicht' in page
    assert 'mode-nudge' in page and ">Nachfragen<" in page
    assert page.count("mode-badge") == 2                           # a and b; info has no answer, consent its own block
    client.post("/surveys/1/editor/items/2", data={"code": "a", "answer_mode": "optional"})
    assert ">Freiwillig<" in client.get("/surveys/1/editor/").data.decode()
    assert "<span>Freiwillig</span>" in client.get("/surveys/1/editor/items/2").data.decode()


def test_text_element_formatting(app, client, survey):
    add(client, "info")                                             # 2
    client.post("/surveys/1/editor/items/2", data={"code": "t", "question":
        "**Wichtig:** bitte *genau* lesen.\n\n<small>Hinweis klein</small> <script>x</script>"})
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "<strong>Wichtig:</strong> bitte <em>genau</em> lesen." in html
    assert "<small>Hinweis klein</small>" in html and "&lt;script&gt;" in html and "<script>x" not in html
    assert ">Text<" in client.get("/surveys/1/editor/").data.decode()


def test_block_name_shown_on_first_page_of_the_block(app, client, survey):
    add(client, "text_input")                                       # block 2, after the consent page
    add(client, "page_break")
    add(client, "text_input")
    client.post("/surveys/1/editor/blocks/2", data={"title": "Szenario A", "show_title": "1"})
    first = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    second = client.get("/surveys/1/editor/preview?page=3", follow_redirects=True).data.decode()
    assert "Szenario A</h1>" in first and "Szenario A</h1>" not in second


def test_preview_bar_jumps_to_blocks(client, survey):
    from tests.participation.test_participate import build

    build(client)                                   # block 1: consent, block 2: two pages
    client.post("/surveys/1/editor/blocks")         # block 3 (empty blocks have no pages)
    html = client.get("/surveys/1/editor/preview", follow_redirects=True).data.decode()
    assert 'class="p-jump"' in html and "?lang=de&page=1" in html and "?lang=de&page=2" in html
    assert html.count('aria-current="true"') >= 1


def test_eye_icon_opens_the_block_after_a_loop(app, client, survey):
    from tests.editor.test_loops import setup

    setup(client)                                   # block 2 in a loop: 4 passes = 4 pages
    client.post("/surveys/1/editor/blocks")         # block 3 after the loop
    add(client, "info", block=3)
    with app.app_context():
        info = db.session.get(Survey, 1).blocks[-1].items[0]
        info.texts = {"de": {"question": "Hier ist Block drei"}}
        db.session.commit()
    html = client.get("/surveys/1/editor/blocks/3/preview", follow_redirects=True).data.decode()
    assert "Hier ist Block drei" in html and "Seite 6/6" in html


def test_jump_list_shows_only_the_loop_entry(client, survey):
    from tests.editor.test_loops import setup

    setup(client)                                   # 4 passes of the loop
    html = client.get("/surveys/1/editor/preview", follow_redirects=True).data.decode()
    assert html.count("· Schleife") == 1 and "Durchlauf 2/4</span>" not in html.split('class="p-jump-list"')[1]


def test_free_text_prefix_and_suffix(app, client, survey):
    add(client, "text_input")                                                   # 2
    client.post("/surveys/1/editor/items/2", data={"code": "height", "question": "Ihre Körpergröße",
                                                    "validation": "decimal", "prefix": "ca.", "suffix": "m"})
    client.post("/surveys/1/editor/items/2?lang=en", data={"question": "Your height", "suffix": "metres"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.text("prefix", "de") == "ca." and item.text("suffix", "de") == "m"
        assert item.kind.result_column(item, "height")[1] == "m"
    assert 'value="ca."' in client.get("/surveys/1/editor/items/2").data.decode()
    html = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    box = html.split('class="p-affixed has-affix"')[1].split("</span>\n  </span>")[0]
    assert '<span class="p-affix" id="pre2">ca.</span>' in box and 'class="p-affix" id="suf2">m' in box and box.index('ca.') < box.index('name="i2"') < box.index('>m')


def test_live_preview_renders_unsaved_values_and_keeps_nothing(app, client, survey):
    add(client, "single_choice")                                                # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "q"), ("question", "Gespeichert"), ("option_id", "a"), ("option_code", "1"), ("option_label", "ja")]))
    frame = client.get("/surveys/1/editor/items/2/frame").data.decode()
    assert "Gespeichert" in frame and "participant.css" in frame
    live = client.post("/surveys/1/editor/items/2/frame", data=MultiDict([
        ("code", "q_neu"), ("question", "Noch nicht gespeichert"), ("option_id", "a"), ("option_code", "1"),
        ("option_label", "vielleicht")])).data.decode()
    assert "Noch nicht gespeichert" in live and "vielleicht" in live
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.code == "q" and item.text("question") == "Gespeichert"      # rolled back
    assert "data-live-frame" in client.get("/surveys/1/editor/items/2").data.decode()



def test_likert_2_to_10_steps_and_own_labels_survive_a_new_length(app, client, survey):
    from app.items.likert import anchor_labels, resize_labels

    assert anchor_labels("de", 2) == ["stimme überhaupt nicht zu", "stimme voll und ganz zu"]
    assert len(anchor_labels("de", 3)) == 3 and all(anchor_labels("de", 10))
    assert resize_labels(["nie", "a", "b", "immer"], 6) == ["nie", "a", "b", "", "", "immer"]
    assert resize_labels(["nie", "a", "b", "immer"], 3) == ["nie", "a", "immer"]
    add(client, "likert")                                                       # 2
    own = ["gar nicht", "wenig", "mittel", "viel", "sehr viel"]
    base = [("code", "L"), ("anchors", "custom"), ("labeling", "full"), ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A")]
    client.post("/surveys/1/editor/items/2", data=MultiDict(base + [("points", "5")] + [("label", l) for l in own]))
    client.post("/surveys/1/editor/items/2?lang=en", data=MultiDict([("label", l) for l in ["none", "little", "some", "much", "very much"]]))
    client.post("/surveys/1/editor/items/2", data=MultiDict(base + [("points", "10")] + [("label", l) for l in
                                                            resize_labels(own, 10)]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["points"] == 10
        assert item.text("labels", "de")[0] == "gar nicht" and item.text("labels", "de")[-1] == "sehr viel"
        en = item.text("labels", "en")
        assert len(en) == 10 and en[0] == "none" and en[-1] == "very much" and en[1] == "little"   # not overwritten


def _palette(client, survey_id=1, fresh=True):
    """The element types of the palette as listed, per group (the AI scale is not a type and stays last). fresh: as
    after a new sign-in (the order is kept for a login session, editor.palette_counts)."""
    if fresh:
        with client.session_transaction() as sess:
            sess.pop("palette_counts", None)
    html = client.get(f"/surveys/{survey_id}/editor/").data.decode()
    out = {}
    for group in ("items", "feedback"):
        part = html.split(f'data-palette-group="{group}"')[1].split("</section>")[0]
        out[group] = [k for k in re.findall(r'class="icon-btn palette-add[^"]*" data-type="([^"]+)"', part)]
    return out


def test_palette_lists_the_most_used_types_first(app, client, survey):
    from app import items

    fixed = {c: [k for t in items.all_types() if t.category == c and t.key != "consent" and not getattr(t, "interview_only", False)
                 and not getattr(t, "experimental", None) and not getattr(t, "requires", None)  # IAT-Ergebnis: only with an IAT
                 for k in [t.key] + (["image_choice"] if t.key == "single_choice" else [])] for c in ("items", "feedback")}
    assert _palette(client) == {"items": fixed["items"] + ["ai_scale"], "feedback": fixed["feedback"]}   # nothing used yet
    for t in ("text_input", "text_input", "text_input", "page_break", "page_break", "slider", "likert"):
        add(client, t)
    rest = [k for k in fixed["items"] if k not in ("text_input", "page_break", "likert", "slider")]
    # by frequency; likert and slider are equally often used and keep their order, as do the never used ones
    assert _palette(client)["items"] == ["text_input", "page_break", "likert", "slider"] + rest + ["ai_scale"]
    assert _palette(client)["feedback"] == fixed["feedback"]                    # the groups stay apart
    add(client, fixed["feedback"][-1])
    assert _palette(client)["feedback"][0] == fixed["feedback"][-1]
    # a second survey of the same person counts too, also when the first one is archived
    client.post("/surveys/new", data={"title": "Zweite", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        block = db.session.scalar(db.select(Block.id).filter_by(survey_id=2).order_by(Block.id.desc()))
    for _ in range(4):
        client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": "ranking"})
    client.post("/surveys/1/archive", data={"archived": "1"})
    assert _palette(client, 2)["items"][:3] == ["ranking", "text_input", "page_break"]
    # a question in the trash no longer counts
    with app.app_context():
        gone = [i.id for i in db.session.scalars(db.select(Item).filter_by(type="ranking"))][:2]
    for item_id in gone:
        client.post(f"/surveys/2/editor/items/{item_id}/delete")
    assert _palette(client, 2)["items"][:3] == ["text_input", "ranking", "page_break"]


def test_palette_order_holds_for_the_login_session(app, client, survey):
    """V102: one element added does not reorder the palette at once; the next session takes the new counts."""
    before = _palette(client)["items"]
    for _ in range(3):
        add(client, "ranking")
    assert _palette(client, fresh=False)["items"] == before
    assert _palette(client)["items"][0] == "ranking"


def test_palette_offers_a_missing_consent_first(app, client, survey):
    for _ in range(3):
        add(client, "text_input")
    assert "consent" not in _palette(client)["items"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        db.session.delete(s.consent_item)
        db.session.commit()
    assert _palette(client)["items"][:2] == ["consent", "text_input"]


def test_palette_counts_surveys_shared_with_the_own_groups(app, client, survey):
    from app import editor
    from app.models import User, WorkGroup

    other_orcid = "0000-0002-1825-0097"
    for t in ("slider", "slider", "ranking"):
        add(client, t)
    client.post("/surveys/new", data={"title": "Nicht geteilt", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        block = db.session.scalar(db.select(Block.id).filter_by(survey_id=2).order_by(Block.id.desc()))
    for _ in range(5):
        client.post(f"/surveys/2/editor/blocks/{block}/items", data={"type": "rating"})
    client.post("/groups/new", data={"name": "Lehrstuhl"})
    with app.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id))
    client.post(f"/groups/{gid}/members", data={"orcid": other_orcid, "name": "Kollegin"})
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={other_orcid}&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})

    def counts():
        with app.app_context():
            return editor.type_counts(db.session.scalar(db.select(User).filter_by(orcid=other_orcid)))

    assert counts() == {}                                   # in the group, but nothing is shared with it
    client.post("/surveys/1/access-groups", data={"group": gid, "role": "editor"})
    assert counts() == {"consent": 1, "slider": 2, "ranking": 1}   # survey 2 stays out: not hers, not her group's
    client.post("/surveys/1/members", data={"orcid": other_orcid, "name": "Kollegin", "role": "editor"})  # groups only look
    assert _palette(other)["items"][:2] == ["slider", "ranking"]
    assert _palette(client)["items"][:3] == ["rating", "slider", "ranking"]


def test_ai_review_of_the_texts(app, client, survey, monkeypatch):
    """One question and the whole questionnaire: only reviewable texts in the source language are sent;
    ticked suggestions go into the texts as one step; validated constructs and the consent stay out."""
    import json as _json

    from app import ai
    from app.models import Item, Survey

    sent = []

    def fake(instructions, prompt, **kw):
        sent.append((instructions, _json.loads(prompt)))
        return _json.dumps({"remarks": ["Frage 2 fragt zwei Dinge."], "parts": [
            {"item": "nutz", "id": "question", "suggestion": "Wie nützlich finden Sie die App?", "why": "eine Sache je Frage"},
            {"item": "nutz", "id": "options/o1", "suggestion": "gar nicht", "why": "parallel"},
            {"item": "nutz", "id": "options/zzz", "suggestion": "x", "why": "gibt es nicht"},
            {"item": "nutz", "id": "options/o2", "suggestion": "sehr", "why": "unverändert"}]})

    monkeypatch.setattr(ai, "generate_text", fake)
    add(client, "single_choice")                                           # item 2
    with app.app_context():
        item = db.session.get(Item, 2)
        item.code = "nutz"
        item.config = {**item.config, "options": [{"id": "o1", "code": "1"}, {"id": "o2", "code": "2"}]}
        item.texts = {"de": {"question": "Wie nützlich und einfach finden Sie die App?", "options": {"o1": "nicht", "o2": "sehr"}},
                      "en": {"question": "How useful and easy?"}}
        db.session.commit()
    assert "Element mit KI prüfen" in client.get(E + "/items/2").data.decode()
    page = client.post(E + "/items/2/text-review").data.decode()
    assert "Frage 2 fragt zwei Dinge." in page and "Wie nützlich finden Sie die App?" in page
    assert 'value="2|options/o1"' in page and "zzz" not in page and 'value="2|options/o2"' not in page
    instructions, facts = sent[-1]
    assert "formal: Sie" in instructions and "{{name}}" in instructions and "How useful" not in _json.dumps(facts)
    assert [p["id"] for p in facts["parts"]] == ["question", "options/o1", "options/o2"]
    # the whole questionnaire: the consent is not sent
    page = client.post(E + "/text-review").data.decode()
    assert "Zum Fragebogen insgesamt" in page and 'href="/surveys/1/editor/items/2' in page
    assert all(e["variable"] != "consent" for b in sent[-1][1]["blocks"] for e in b["elements"])
    client.post(E + "/text-review/apply", data={"item": "2", "take": "2|question",
                                                 "text_2|question": "Wie nützlich finden Sie die App?",
                                                 "text_2|options/o1": "nicht ticked"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.texts["de"]["question"] == "Wie nützlich finden Sie die App?" and item.texts["de"]["options"]["o1"] == "nicht"
        assert item.texts["en"]["question"] == "How useful and easy?"
    client.post(E + "/undo")
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"]["question"] == "Wie nützlich und einfach finden Sie die App?"
    # a locked construct has no button and is refused
    client.post(E + "/blocks/2/items", data={"type": "construct:bfi10"})
    with app.app_context():
        locked = max(i.id for b in db.session.get(Survey, 1).blocks for i in b.items)
    assert "Element mit KI prüfen" not in client.get(f"{E}/items/{locked}").data.decode()
    assert client.post(f"{E}/items/{locked}/text-review").status_code == 404


def test_pretest_link_lives_its_days_and_can_be_revoked(app, client, survey):
    # UI audit 2026-10-04, R11: a pretest link from Datenerhebung › Testen, valid 7 days, revoked all at once
    import re
    import time

    from tests.conftest import add

    add(client, "info")
    page = client.get("/surveys/1/field?pretest=7").data.decode()
    url = re.search(r'id="pretest-url"[^>]*value="([^"]+)"', page).group(1)
    path = url.split("/p/", 1)[1]
    tester = app.test_client()
    real_time = time.time
    try:
        time.time = lambda: real_time() + 3 * 24 * 3600
        assert tester.get(f"/p/{path}").status_code == 200
        time.time = lambda: real_time() + 8 * 24 * 3600
        assert tester.get(f"/p/{path}").status_code == 410
    finally:
        time.time = real_time
    assert tester.get(f"/p/{path}").status_code == 200
    client.post("/surveys/1/pretest/revoke")
    assert tester.get(f"/p/{path}").status_code == 404
    assert client.get("/surveys/1/field?pretest=99").data.decode().count('id="pretest-url"') == 0   # only 1, 7 or 30 days


def test_a_pretest_link_ends_when_its_maker_may_no_longer_see_the_survey_type(app, client, survey):
    """Audit 2026-10-18, AUTHZ-04: the link checked the maker's role without the cap of the survey type's level
    (app/kind_access.py), so links went on working after an admin set the type to "off" for that person."""
    from app import kind_access
    from app.models import User
    from tests.conftest import add, allow

    OTHER = "0000-0002-1825-0097"
    add(client, "info")
    allow(app, OTHER)
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "editor"})
    maker = app.test_client()
    maker.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    maker.post("/terms/", data={"accept": "1"})
    page = maker.get("/surveys/1/field?pretest=7").data.decode()
    path = re.search(r'id="pretest-url"[^>]*value="([^"]+)"', page).group(1).split("/p/", 1)[1]
    tester = app.test_client()
    assert tester.get(f"/p/{path}").status_code == 200

    def level(value):
        with app.app_context():
            kind_access.store(db.session.scalar(db.select(User).filter_by(orcid=OTHER)), {"survey": value}, log=False)
            db.session.commit()

    level("view")
    assert tester.get(f"/p/{path}").status_code == 200  # "view" still sees the survey
    level("off")
    assert tester.get(f"/p/{path}").status_code == 404
