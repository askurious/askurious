"""Text highlighting (mark words of a text, one to three categories) and cloze text (gaps in a text)."""
import json

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

PASSAGE = "Der Roboter hilft im Haushalt, aber er ist laut."


def setup_highlight(client, required=True, **extra):
    add(client, "highlight")                                                      # item 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "hl"), ("question", "Markieren Sie, was Ihnen gefällt oder missfällt."), ("passage", PASSAGE),
        ("answer_mode", "required" if required else "optional"), ("ncats", "2"),
        ("cat_label", "positiv"), ("cat_label", "negativ"), ("cat_color", "green"), ("cat_color", "red"), *extra.items()]))


def setup_cloze(client, required=True):
    add(client, "cloze")                                                          # item 2
    client.post("/surveys/1/editor/items/2", data={
        "code": "cl", "question": "Ergänzen Sie.", "text": "Ein Roboter ist [] und [  ].\n\nAm liebsten wäre er [    ].",
        "answer_mode": "required" if required else "optional"})


def test_highlight_defaults_per_number_of_categories(app, client, survey):
    setup_highlight(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert [(k, label) for k, label, _ in item.kind.cats(item)] == [(1, "positiv"), (2, "negativ")]
    for n, labels in ((1, ["wichtig"]), (3, ["positiv", "negativ", "neutral"])):
        client.post("/surveys/1/editor/items/2", data={"code": "hl", "passage": PASSAGE, "ncats": str(n)})
        with app.app_context():
            item = db.session.get(Item, 2)
            assert [label for _, label, _ in item.kind.cats(item)] == labels
            assert item.texts["en"]["cats"][0] in ("important", "positive")       # suggested in every language
    client.post("/surveys/1/editor/items/2", data=MultiDict([("code", "hl"), ("passage", PASSAGE), ("ncats", "3"),
        ("cat_label", "gut"), ("cat_label", "schlecht"), ("cat_label", "egal"),
        ("cat_color", "green"), ("cat_color", "red"), ("cat_color", "grey")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert [label for _, label, _ in item.kind.cats(item)] == ["gut", "schlecht", "egal"]   # adjustable
        assert item.config["cats"][2]["color"] == "grey"


def test_highlight_parse_export_codebook(app, client, survey):
    setup_highlight(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        with pytest.raises(AnswerError, match="mindestens ein Wort"):
            item.kind.parse_answer(item, MultiDict({"i2": "{}"}))
        # word 3 = "hilft", 9 = "laut"; out of range, unknown category and garbage are dropped
        answer = item.kind.parse_answer(item, MultiDict({"i2": json.dumps({"3": 1, "9": 2, "99": 1, "4": 7, "x": 1})}))
        assert answer == {"3": 1, "9": 2}
        out = item.kind.export_values(item, answer)
        assert out["hl_w3"] == 1 and out["hl_w9"] == 2 and out["hl_w1"] == 0 and out["hl_c1"] == 1 and out["hl_c2"] == 1
        assert len([c for c in out if c.startswith("hl_w")]) == 9
        cols = dict(item.kind.export_columns(item))
        assert cols["hl_w3"] == "Wort 3: hilft" and cols["hl_c2"] == "Anzahl markierter Wörter: negativ"
        assert item.kind.codebook_info(item, "hl_w3", "de")[1] == "0 = nicht markiert; 1 = positiv; 2 = negativ"
        assert item.kind.export_values(item, None)["hl_w3"] is None                # not seen: empty, not 0


def test_highlight_participant_page_and_results(app, client, survey):
    setup_highlight(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-hl="2"' in html and 'data-hl-w="3">hilft</span>' in html and 'data-hl-tool="2"' in html
    assert "--hl1: #a8e6b0" in html and 'name="i2"' in html
    fill_consent(client)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        url = f"/s/{db.session.get(Survey, 1).code}"
    for marks in ({"3": 1, "9": 2}, {"3": 1}, {"9": 2, "5": 1}):
        p = app.test_client()
        p.get(url)
        p.post(url, data=CONSENT_OK)
        p.post(url, data={"nav": "next", "i2": json.dumps(marks)})
    with app.app_context():
        stored = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(is_preview=False))]
        assert {"3": 1, "9": 2} in stored
    html = client.get("/surveys/1/results").data.decode()
    assert 'class="res-hl"' in html and 'class="res-hl-w"' in html
    assert "hilft: positiv 2, negativ 0 (von 3)" in html and "laut: positiv 0, negativ 2 (von 3)" in html


def test_highlight_text_fixed_once_running(app, client, survey):
    setup_highlight(client)
    fill_consent(client)
    client.post("/surveys/1/start", data={})
    client.post("/surveys/1/editor/items/2", data={"code": "hl", "question": "Neu", "passage": "Ganz anderer Text"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.text("passage") == PASSAGE and item.text("question") == "Neu"


def test_cloze_gaps_sizes_parse_export(app, client, survey):
    setup_cloze(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert html.count('class="p-cloze-gap') == 3 and "is-s" in html and "is-m" in html and "is-l" in html
    assert 'name="i2_3"' in html and 'maxlength="50"' in html and "<p>Am liebsten wäre er" in html
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.gaps(item) == ["s", "m", "l"]
        with pytest.raises(AnswerError, match="alle Lücken"):
            item.kind.parse_answer(item, MultiDict({"i2_1": "nett", "i2_2": ""}))
        answer = item.kind.parse_answer(item, MultiDict({"i2_1": " nett ", "i2_2": "laut; teuer", "i2_3": "x" * 80}))
        assert answer == ["nett", "laut, teuer", "x" * 50]                        # one line, no separator, 50 characters
        assert item.kind.export_values(item, answer) == {"cl": "nett;laut, teuer;" + "x" * 50}
        assert item.kind.export_values(item, ["a", "", "c"]) == {"cl": "a;;c"}
        assert item.kind.export_values(item, None) == {"cl": None}
        assert "getrennt mit „;“" in item.kind.codebook_info(item, "cl", "de")[2][0]


def test_cloze_optional_and_results(app, client, survey):
    setup_cloze(client, required=False)
    fill_consent(client)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        url = f"/s/{db.session.get(Survey, 1).code}"
    for a, b in (("nett", "leise"), ("Nett", ""), ("hilfreich", "leise")):
        p = app.test_client()
        p.get(url)
        p.post(url, data=CONSENT_OK)
        p.post(url, data={"nav": "next", "i2_1": a, "i2_2": b})
    html = client.get("/surveys/1/results").data.decode()
    assert 'class="res-cloze"' in html and "Lücke 1" in html and "nach „…Ein Roboter ist“" in html
    assert "<td>nett</td>" in html and "3 ausgefüllt, 2 verschiedene" in html
    csv = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig")
    assert "nett;leise;" in csv and "Nett;;" in csv


def test_validation_and_palette(app, client, survey):
    add(client, "cloze")
    add(client, "highlight")
    with app.app_context():
        problems = [p for b in db.session.get(Survey, 1).blocks for i in b.items for p in i.kind.validate(i)]
    assert "Der Text enthält keine Lücke (als [ ] schreiben)" in problems and "Text zum Markieren fehlt" in problems
    html = client.get("/surveys/1/editor/").data.decode()
    assert "Texthervorhebung" in html and "Lückentext" in html


def test_dummies(app, client, survey):
    setup_highlight(client, required=False)
    add(client, "cloze")                                                          # item 3
    client.post("/surveys/1/editor/items/3", data={"code": "cl", "text": "A [] B [  ]"})
    with app.app_context():
        from app import dummy

        dummy.generate(db.session.get(Survey, 1), 6, seed=3)
        db.session.commit()
        rows = list(db.session.scalars(db.select(Response).filter_by(source="dummy")))
        assert rows and all(r.answers.get("2") and len(r.answers.get("3")) == 2 for r in rows)
    assert client.get("/surveys/1/export/data.sav?source=dummy").status_code == 200
