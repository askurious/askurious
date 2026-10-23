"""Personal code (items/personal_code.py): normalising, the editor's parts, answers, export, codebook, open answers,
dummy data and the page."""
import math

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import DONT_KNOW, AnswerError
from app.items.personal_code import DEFAULT_PARTS, FREQ, KEYS, code_of, collision, letter, shares, split, value
from app.models import Item, Response, Survey
from tests.conftest import add


def setup_code(client, parts=None, required=True, export_parts=False):
    add(client, "personal_code")                                                   # item 2
    data = [("code", "pcode"), ("question", "Ihr Code"), ("answer_mode", "required" if required else "optional"),
            ("parts_shown", "1")] + [("part", p) for p in (parts or DEFAULT_PARTS)]
    if export_parts:
        data.append(("export_parts", "1"))
    client.post("/surveys/1/editor/items/2", data=MultiDict(data))


def sent(item, **parts):
    """Form fields: part=value, or part=None for the tick "weiß nicht"."""
    return MultiDict({(f"i{item.id}_{k}_dk" if v is None else f"i{item.id}_{k}"): ("1" if v is None else v)
                      for k, v in parts.items()})


FULL = {"mother_first": "m", "birth_day": "7", "father_first": "K", "older_siblings": "1", "birthplace_first": "Aachen"[0]}


def test_letters_and_digits_are_normalised():
    assert [letter(x) for x in ("a", " ä ", "É", "ß", "ø", "Ł", "ı", "ж", "中")] == ["A", "A", "E", "S", "O", "L", "I", "Ж", "中"]
    assert letter("ab") is None and letter("1") is None and letter("") is None and letter("-") is None
    assert value("day", "7") == "07" and value("day", "31") == "31" and value("day", "32") is None and value("day", "0") is None
    assert value("month", "12") == "12" and value("month", "13") is None and value("digit", "10") is None
    assert value("digit", "٣") is None                                              # ASCII digits only
    parts = DEFAULT_PARTS
    assert code_of(parts, {"mother_first": "M", "birth_day": "07", "father_first": DONT_KNOW, "older_siblings": "1",
                           "birthplace_first": "A"}) == "M07?1A"
    assert code_of(parts, {"mother_first": "M", "birth_day": DONT_KNOW}) == "M?????"   # parts missing: placeholders
    assert split(parts, "M07X1A") == ["M", "07", "X", "1", "A"] and split(parts, "M07") is None


def test_editor_parts_and_validation(app, client, survey):
    add(client, "personal_code")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.parts(item) == DEFAULT_PARTS and item.kind.validate(item) == []
        assert "persönlichen Code" in item.text("question")
    # ticked in any order: the code keeps the order of the list
    client.post("/surveys/1/editor/items/2", data=MultiDict([("code", "pcode"), ("question", "Code"), ("parts_shown", "1"),
                                                             ("part", "birth_year_last"), ("part", "mother_second"),
                                                             ("part", "birth_day")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.parts(item) == ["birth_day", "mother_second", "birth_year_last"]
    client.post("/surveys/1/editor/items/2", data=MultiDict([("code", "pcode"), ("question", "Code"), ("parts_shown", "1"),
                                                             ("part", "birth_day"), ("part", "mother_second")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert any("mindestens 3" in p for p in item.kind.validate(item))
        item.config = {**item.config, "parts": list(DEFAULT_PARTS) + ["mother_second", "father_second"]}
        assert any("höchstens 6" in p for p in item.kind.validate(item))
        # a survey file: only the known parts, three to six
        assert item.kind.import_config({"parts": ["birth_day", "nope", "father_first"]})[1] == ["parts"]
        assert item.kind.import_config({"parts": DEFAULT_PARTS, "export_parts": True})[0]["export_parts"] is True


def test_collision_estimate():
    """Shared codes by sample size: uneven letters give more shared codes than equal ones; the same numbers in app.js."""
    assert all(abs(sum(shares(k)) - 1) < 1e-9 for k in KEYS)
    assert set(FREQ["birth_day"]) == {f"{d:02d}" for d in range(1, 32)}
    assert collision(DEFAULT_PARTS, 1) == (0.0, 0.0)
    small, big = collision(DEFAULT_PARTS, 50), collision(DEFAULT_PARTS, 10000)
    assert 0 < small[0] < 0.01 and big[0] > 0.999 and small[1] < big[1] < 0.1
    # three letters equally common would give 1/26^3 per pair; with their real spread codes are shared more often
    n = 100
    uniform = -math.expm1(-n * (n - 1) / 2 / 26 ** 3)
    assert collision(["mother_first", "father_first", "birthplace_first"], n)[0] > uniform
    # two people, parts without letters: the bins are exact, the chance is the product of the parts' sums of squares
    q2 = sum(x * x for x in shares("birth_month")) * sum(x * x for x in shares("birth_day")) / 10
    assert collision(["birth_day", "birth_month", "birth_year_last"], 2)[0] == pytest.approx(-math.expm1(-q2), rel=1e-3)


def test_editor_shows_collision_estimate(app, client, survey):
    add(client, "personal_code")
    page = client.get("/surveys/1/editor/items/2").get_data(as_text=True)
    assert "data-sgic-collision" in page and 'value="170"' in page                 # 50 participants: log10(50) * 100
    assert "Abschätzung von Kollisionen" in page and 'min="100" max="470"' in page   # 10 to 50,000
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.max_responses = 300
        db.session.commit()
    page = client.get("/surveys/1/editor/items/2").get_data(as_text=True)
    assert "<output data-sgic-n>300</output>" in page and 'value="248"' in page


def test_answers(app, client, survey):
    setup_code(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        answer = kind.parse_answer(item, sent(item, **FULL))
        assert answer == {"mother_first": "M", "birth_day": "07", "father_first": "K", "older_siblings": "1",
                          "birthplace_first": "A"} and not kind.is_missing(item, answer)
        # "weiß nicht" by the tick or an X typed into a field of digits
        dk = kind.parse_answer(item, sent(item, **{**FULL, "father_first": None, "birth_day": "xx"}))
        assert dk["father_first"] == DONT_KNOW and dk["birth_day"] == DONT_KNOW
        assert kind.export_values(item, dk) == {"pcode": "M???1A"}
        with pytest.raises(AnswerError, match="jeden Teil"):                         # a gap links nothing
            kind.parse_answer(item, sent(item, **{**FULL, "older_siblings": ""}))
        with pytest.raises(AnswerError, match="genau einen Buchstaben"):
            kind.parse_answer(item, sent(item, **{**FULL, "mother_first": "7"}))
        with pytest.raises(AnswerError, match="01 bis 31"):
            kind.parse_answer(item, sent(item, **{**FULL, "birth_day": "40"}))
        with pytest.raises(AnswerError, match="Ziffer von 0 bis 9"):
            kind.parse_answer(item, sent(item, **{**FULL, "older_siblings": "12"}))
        with pytest.raises(AnswerError, match="eine Ziffer"):
            kind.parse_answer(item, sent(item, **{**FULL, "older_siblings": "a"}), lang="de")
        with pytest.raises(AnswerError):                                             # required
            kind.parse_answer(item, MultiDict())
        assert "Please enter exactly one letter for “First letter of your mother’s first name”" in str(
            pytest.raises(AnswerError, kind.parse_answer, item, sent(item, **{**FULL, "mother_first": "77"}), "en").value)
        item.answer_mode = "optional"
        assert kind.parse_answer(item, MultiDict()) is None
        with pytest.raises(AnswerError):                                             # optional, but not half of it
            kind.parse_answer(item, sent(item, mother_first="M"))


def test_export_codebook_and_open_columns(app, client, survey):
    setup_code(client, export_parts=True)
    with app.app_context():
        from app import export

        item = db.session.get(Item, 2)
        kind = item.kind
        cols = [c for c, _ in kind.export_columns(item)]
        assert cols == ["pcode", "pcode_mo1", "pcode_bday", "pcode_fa1", "pcode_sib", "pcode_pl1"]
        answer = kind.parse_answer(item, sent(item, **{**FULL, "older_siblings": None}))
        assert kind.export_values(item, answer) == {"pcode": "M07K?A", "pcode_mo1": "M", "pcode_bday": "07",
                                                    "pcode_fa1": "K", "pcode_sib": "?", "pcode_pl1": "A"}
        assert kind.export_values(item, None)["pcode"] is None
        typ, values, notes = kind.codebook_info(item, "pcode", "de")
        assert typ == "Text" and values == "6 Zeichen" and "1. Erster Buchstabe des Vornamens Ihrer Mutter (1 Buchstabe)" in notes[0]
        assert "? (bei zwei Ziffern ??) = weiß nicht; X ist ein Buchstabe" in notes
        assert "2. Day of the month of your birthday (01 to 31) (2 digits)" in kind.codebook_info(item, "pcode", "de", ui="en")[2][0]
        assert kind.codebook_info(item, "pcode_bday", "de")[1] == "01–31"
        # an open answer: a public data package holds it only with the tick
        assert set(cols) <= export.open_columns(db.session.get(Survey, 1))
        assert kind.result_kind is None


def test_page_dummy_data_and_text_columns(app, client, survey):
    setup_code(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "data-code" in html and 'name="i2_birth_day"' in html and 'name="i2_birth_day_dk"' in html
    assert 'inputmode="numeric"' in html and "Tag Ihres Geburtstags" in html
    editor = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="part"' in editor and "Direnga" in editor and "Kearney" in editor
    with app.app_context():
        from app import dummy, export

        s = db.session.get(Survey, 1)
        dummy.generate(s, 10, seed=3)
        db.session.commit()
        item = db.session.get(Item, 2)
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(isinstance(a, dict) and not item.kind.is_missing(item, a) for a in answers)
        # a code of digits only stays text in SPSS, R and jamovi (leading zeros)
        item.config = {**item.config, "parts": ["birth_day", "older_siblings", "birth_year_last"]}
        db.session.commit()
        _cols, _data, meta = export._spss_dictionary(s, False, ("dummy",), None, True, ("dummy",))
        assert meta["pcode"]["kind"] == "str"


def test_codebook_file_and_questionnaire(app, client, survey):
    setup_code(client)
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "pcode" in book and "Teile in dieser Reihenfolge" in book
    assert client.get("/surveys/1/export/questionnaire.docx").status_code == 200
