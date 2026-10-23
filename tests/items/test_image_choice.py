"""Answer options as images ("Bildauswahl", items/choice.py): the palette entry, the editor's tick and image
descriptions, one image per option from the image pipeline (upload or AI, approval), and the participant view."""
import base64
import re
import io

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item
from tests.conftest import add
from tests.editor.test_editor import PNG

E = "/surveys/1/editor"


def _item(app):
    with app.app_context():
        item = db.session.get(Item, 2)
        return item.type, dict(item.config), item.kind.required_keys(item), item.kind.validate(item)


def _save(client, images=True, prompts=None, **extra):
    data = MultiDict([("code", "rad"), ("question", "Welches Rad gefällt Ihnen?"), ("images_shown", "1"),
                      ("option_id", "a"), ("option_code", "1"), ("option_label", "Rennrad"),
                      ("option_id", "b"), ("option_code", "2"), ("option_label", "Lastenrad"),
                      ("option_id", "c"), ("option_code", "3"), ("option_label", "Anderes, und zwar:"), ("option_text", "c")])
    if images:
        data.add("images", "1")
    for oid, text in (prompts or {}).items():
        data.add(f"option_prompt_{oid}", text)
    for k, v in extra.items():
        data.add(k, v)
    client.post(f"{E}/items/2", data=data)


def test_the_palette_entry_is_a_single_choice_with_images(app, client, survey):
    page = client.get(E + "/").data.decode()
    assert 'data-type="image_choice"' in page and "Bildauswahl" in page
    client.post(f"{E}/blocks/2/items", data={"type": "image_choice"})
    kind, config, keys, problems = _item(app)
    assert kind == "single_choice" and config["images"] and len(keys) == 2
    assert "2 von 2 Bildern der Antwortoptionen fehlen oder sind nicht freigegeben" in problems
    html = client.get(f"{E}/items/2").data.decode()
    assert "Bildbeschreibung für die KI" in html and 'name="presentation" value="images" data-reload-saved checked' in html
    assert "0 von 2</strong> Bildern freigegeben" in html


def test_images_per_option_uploaded_or_generated(app, client, survey, monkeypatch):
    from app import ai

    calls = []
    monkeypatch.setattr(ai, "generate_image", lambda prompt, size="1024x1024", **kw: calls.append((prompt, size)) or base64.b64encode(PNG).decode())
    add(client, "single_choice")
    _save(client, prompts={"a": "a red racing bike, side view", "b": ""})
    kind, config, keys, problems = _item(app)
    assert keys == ["o:a", "o:b", "o:c"] and config["options"][0]["prompt"] == "a red racing bike, side view"
    assert "prompt" not in config["options"][1]
    page = client.get(f"{E}/items/2/images").data.decode()
    assert "Bilder der Antwortoptionen" in page and "3 Antwortoptionen" in page and "1: Rennrad" in page
    assert page.count('class="asset-upload"') == 3 and page.count("data-submit-on-change") == 3
    # upload for one option: approved at once, replaces what was there
    for _ in range(2):
        client.post(f"{E}/items/2/images/upload", data={"key": "o:b", "file": (io.BytesIO(PNG), "rad.png")},
                    content_type="multipart/form-data")
    assert client.post(f"{E}/items/2/images/upload", data={"key": "o:zz", "file": (io.BytesIO(PNG), "x.png")},
                       content_type="multipart/form-data").status_code == 302
    with app.app_context():
        item = db.session.get(Item, 2)
        assert [(a.key, a.status, a.source) for a in item.assets] == [("o:b", "approved", "upload")]
    # the AI: the option's own description, else its wording, in the style of the survey, square
    assert client.post(f"{E}/items/2/images/generate", data={"key": "o:a"}).json["ok"]
    assert client.post(f"{E}/items/2/images/generate", data={"key": "o:c"}).json["ok"]
    assert "a red racing bike, side view" in calls[0][0] and "Anderes, und zwar:" in calls[1][0]
    assert {size for _, size in calls} == {"1024x1024"}
    client.post(f"{E}/items/2/images/approve-all")
    assert _item(app)[3] == []
    # saving the form again keeps the descriptions, also without their fields (images switched off and on)
    _save(client, images=False)
    _save(client)
    assert _item(app)[1]["options"][0]["prompt"] == "a red racing bike, side view"
    # the participant view: tiles with the images, the same field names and codes
    frame = client.get(f"{E}/items/2/frame").data.decode()
    assert len(re.findall(r'class="p-image-option[ "]', frame)) == 3 and frame.count("<img src=") == 3
    assert 'type="radio" name="i2" value="2"' in frame and 'name="i2_t_c"' in frame
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.parse_answer(item, MultiDict({"i2": "3", "i2_t_c": "Liegerad"})) == {"v": "3", "t": {"3": "Liegerad"}}
        assert item.kind.export_values(item, "2") == {"rad": "2", "rad_3_text": None}


def test_multiple_choice_with_images_and_no_images_without_the_tick(app, client, survey):
    add(client, "multiple_choice")
    _save(client)
    frame = client.get(f"{E}/items/2/frame").data.decode()
    assert len(re.findall(r'class="p-image-option[ "]', frame)) == 3 and 'type="checkbox" name="i2" value="1"' in frame
    assert "<img src=" not in frame                       # no approved image yet: an empty tile
    _save(client, images=False)
    kind, config, keys, problems = _item(app)
    assert "images" not in config and keys == [] and not any("Bild" in p for p in problems)
    assert "p-image-option" not in client.get(f"{E}/items/2/frame").data.decode()
    assert client.get(f"{E}/items/2/images").status_code == 404


def test_an_image_choice_is_named_so_in_the_editor(app, client, survey):
    """Pre-test 2026-10-20: a click on "Bildauswahl" in the palette opened a page headed "Einfachauswahl" (the preset
    is a single choice with images): the page, the list and the image page now name it "Bildauswahl"; a single
    choice without images stays "Einfachauswahl"."""
    add(client, "image_choice")
    page = client.get(f"{E}/items/2").data.decode()
    h1 = re.search(r"<h1[^>]*>.*?</h1>", page, re.S).group(0)
    assert "Bildauswahl" in h1 and "Einfachauswahl" not in h1 and "<title>Bildauswahl" in page
    listing = client.get(E + "/").data.decode()
    assert 'title="Bildauswahl"' in listing
    assert "Bildauswahl · " in client.get(f"{E}/items/2/images").data.decode()
    add(client, "single_choice")
    assert "Einfachauswahl" in re.search(r"<h1[^>]*>.*?</h1>", client.get(f"{E}/items/3").data.decode(), re.S).group(0)


def test_image_tiles_keep_a_square_frame_with_the_whole_image():
    """Pre-test 2026-10-20: the height attribute of the <img> beat aspect-ratio (no height: auto), so a tile was as
    tall as the image's pixels, the image cut by object-fit: cover and the boxes below it out of sight."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / "app" / "static" / "participant.css").read_text(encoding="utf-8")
    rule = re.search(r"\.p-image-option img, \.p-image-option \.p-image-missing \{([^}]*)\}", css).group(1)
    assert "height: auto" in rule and "aspect-ratio: 1" in rule and "object-fit: contain" in rule
