"""Statements pasted as a list and the source of a scale's items (app/item_paste.py, app/item_source.py; user test
2026-10-27, point 7)."""
from werkzeug.datastructures import MultiDict

from app import design, item_paste, item_source
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add

ATI = """Fragebogen zur interaktionsbezogenen Technikaffinität

01 Ich beschäftige mich gern genauer mit technischen
Systemen.
02 Ich probiere gern die Funktionen neuer technischer
Systeme aus.
03 In erster Linie beschäftige ich mich mit technischen
Systemen, weil ich muss. (R)
"""


def test_a_pdf_copy_becomes_one_statement_per_item():
    got = item_paste.parse(ATI)
    # the heading before the numbered items is left out
    assert [t for _, t, _ in got] == ["Ich beschäftige mich gern genauer mit technischen Systemen.",
                                          "Ich probiere gern die Funktionen neuer technischer Systeme aus.",
                                          "In erster Linie beschäftige ich mich mit technischen Systemen, weil ich muss."]
    assert [r for _, _, r in got] == [False, False, True]


def test_plain_lines_codes_and_broken_words():
    assert item_paste.parse("Ich bin gesellig\nIch bin eher zurückhaltend") == [
        ("", "Ich bin gesellig", False), ("", "Ich bin eher zurückhaltend", False)]
    assert item_paste.parse("Ich arbeite gründlich und\nsorgfältig") == [("", "Ich arbeite gründlich und sorgfältig", False)]
    assert item_paste.parse("ati1\tErstens\nati2\tZweitens") == [("ati1", "Erstens", False), ("ati2", "Zweitens", False)]
    assert item_paste.parse("A1;Erstens") == [("A1", "Erstens", False)]
    assert item_paste.parse("1. Ich nutze Sys-\ntemen gern\n2. Das zweite") == [
        ("", "Ich nutze Systemen gern", False), ("", "Das zweite", False)]


def _form(**extra):
    return MultiDict([("code", "L"), ("question", "Wie sehr?"), ("points", "5"), ("labeling", "full"),
                      ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Vorhanden"), *extra.items()])


def test_pasted_statements_go_to_the_end(app, client, survey):
    add(client, "likert")
    client.post("/surveys/1/editor/items/2", data=_form(stmt_paste="01 Erste\n02 Zweite (R)"))
    with app.app_context():
        item = db.session.get(Item, 2)
        texts = item.texts["de"]["statements"]
        assert [texts[s["id"]] for s in item.config["statements"]] == ["Vorhanden", "Erste", "Zweite"]
        assert [s["reverse"] for s in item.config["statements"]] == [False, False, True]


def test_source_given_own_or_missing(app, client, survey):
    add(client, "likert")
    client.post("/surveys/1/editor/items/2", data=_form(item_source_shown="1"))
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        assert item_source.missing(item)
        assert any("Quelle der Items fehlt" in f.message for f in design.check(s))
    client.post("/surveys/1/editor/items/2", data=_form(item_source_shown="1", item_source_own="1"))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert not item_source.missing(item) and item_source.text(item) == ""
    client.post("/surveys/1/editor/items/2", data=_form(item_source_shown="1", item_source="Franke, T. (2019). ATI."))
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        assert item_source.text(item) == "Franke, T. (2019). ATI."
        assert not any("Quelle der Items fehlt" in f.message for f in design.check(s))
    assert "Quelle der Items" in client.get("/surveys/1/editor/items/2").data.decode()


def test_a_doi_is_completed_once(app, client, survey, monkeypatch):
    from app import citations

    calls = []

    def fake(doi):
        calls.append(doi)
        return {"title": "The affinity for technology interaction (ATI) scale", "authors": [["Franke", "Thomas"]],
                "year": 2019, "journal": "International Journal of Human–Computer Interaction", "volume": "35",
                "issue": "6", "pages": "456-467"}
    monkeypatch.setattr(citations, "fetch", fake)
    add(client, "likert")
    form = _form(item_source_shown="1", item_source="10.1080/10447318.2018.1456150")
    client.post("/surveys/1/editor/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        text = item_source.text(item)
        assert text.startswith("Franke, T. (2019). The affinity") and text.endswith("https://doi.org/10.1080/10447318.2018.1456150")
        stored = item.config["source"]["text"]
    client.post("/surveys/1/editor/items/2", data=_form(item_source_shown="1", item_source=stored))
    assert calls == ["10.1080/10447318.2018.1456150"]
