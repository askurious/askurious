"""Statements pasted as a list and the source of a scale's items (app/item_paste.py, app/item_source.py; user test
2026-10-27, point 7)."""
from werkzeug.datastructures import MultiDict

from app import design, item_paste, item_source
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add

# own example statements (no wording of a published scale in the code, NOTICE), broken over lines as in a PDF
PDF_COPY = """Fragebogen zum Umgang mit neuen Geräten

01 Ich lese die Anleitung, bevor ich ein neues
Gerät benutze.
02 Neue Apps richte ich am liebsten
selbst ein.
03 Bei neuen Geräten warte ich lieber, bis mir jemand
hilft. (R)
"""


def test_a_pdf_copy_becomes_one_statement_per_item():
    got = item_paste.parse(PDF_COPY)
    # the heading before the numbered items is left out
    assert [t for _, t, _ in got] == ["Ich lese die Anleitung, bevor ich ein neues Gerät benutze.",
                                      "Neue Apps richte ich am liebsten selbst ein.",
                                      "Bei neuen Geräten warte ich lieber, bis mir jemand hilft."]
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


def _first_scale(app, kind="likert"):
    with app.app_context():
        from app import constructs

        return next(c.key for c in constructs.search() if (c.spec or {}).get("type") == kind and c.citation)


def test_take_a_construct_replaces_or_appends(app, client, survey):
    key = _first_scale(app)
    add(client, "likert")
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert "Items aus den Konstrukten übernehmen" in page and f'value="{key}|replace"' in page
    client.post("/surveys/1/editor/items/2", data=_form())
    client.post("/surveys/1/editor/items/2/take-construct", data={"take": f"{key}|append"})
    with app.app_context():
        item = db.session.get(Item, 2)
        texts = item.texts["de"]["statements"]
        assert texts[item.config["statements"][0]["id"]] == "Vorhanden" and len(item.config["statements"]) > 1
        assert "construct" not in item.config and item_source.text(item)  # the construct's citation as the source
    client.post("/surveys/1/editor/items/2/take-construct", data={"take": f"{key}|replace"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["construct"]["key"] == key and "Vorhanden" not in item.texts["de"]["statements"].values()
    # a construct is one already: nothing more to take
    assert client.post("/surveys/1/editor/items/2/take-construct", data={"take": f"{key}|append"}).status_code == 400


def test_earlier_answers_are_one_quiet_line(app, client, survey):
    add(client, "text_input")
    add(client, "page_break")
    add(client, "likert")
    page = client.get("/surveys/1/editor/items/4").data.decode()
    assert 'class="loop-vars pipe-only"' in page and "↪ Frühere Antworten einsetzen" in page


def test_the_example_of_the_question_can_be_selected_and_taken(app, client, survey):
    """Point 11: the hint stood inside the label of the question text, so every click on it went into the field."""
    add(client, "likert")
    page = client.get("/surveys/1/editor/items/2").data.decode()
    head = page.split('<div class="question-field-head">', 1)[1].split("</div>", 1)[0]
    assert '<label class="label-title" for="question-2">' in head and "</label>" in head.split('<span class="hint">')[0]
    assert 'data-take-example="Inwieweit stimmen Sie den folgenden Aussagen zu?"' in head
