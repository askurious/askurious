"""Formatting in texts written by researchers: question texts (one line, inline), longer texts (rich), and the
plain version for export, codebook, results and axis labels."""
from app.items.base import inline_rich, plain, rich
from tests.conftest import add


def test_inline_formatting():
    html = str(inline_rich("**fett**, *kursiv*, <u>unter</u>, `a*b*c`, H~2~O, x^2^, <small>klein</small>, <sub>i</sub>, <sup>n</sup>"))
    assert "<strong>fett</strong>" in html and "<em>kursiv</em>" in html and "<u>unter</u>" in html
    assert "<code>a*b*c</code>" in html                      # nothing is formatted inside code
    assert "H<sub>2</sub>O" in html and "x<sup>2</sup>" in html
    assert "<small>klein</small>" in html and "<sub>i</sub>" in html and "<sup>n</sup>" in html
    assert "<p>" not in str(inline_rich("eins\n\nzwei"))      # no paragraphs in a question text


def test_underscores_mark_italic_but_not_inside_words():
    """Pre-test 2026-10-20, 33: _möglich_ came out with its underscores while *einigen* was italic. Underscores
    are italic as in Markdown, but never in snake_case, codes, addresses, paths, placeholders or blank lines."""
    html = str(inline_rich("Freitextelemente sind mit *einigen* **Formatierungen** _möglich_."))
    assert "<em>einigen</em> <strong>Formatierungen</strong> <em>möglich</em>." in html
    assert "<em>a b</em>" in str(inline_rich("(_a b_)")) and "&#34;<em>Zitat</em>&#34;" in str(inline_rich('"_Zitat_"'))
    assert "<strong><em>beides</em></strong>" in str(inline_rich("**_beides_**"))
    for text in ("snake_case", "Q1_1 und Q1_2", "a_b@x.de", "_kursiv_er", "x_y_z", "https://x.de/_a_/b",
                 "?q=_a_&r=1", "C:\\_a_\\b", "Name: ____ und ____", "__init__", "eins_ zwei _drei"):
        assert "<em>" not in str(inline_rich(text)), text
        assert plain(text) == text
    assert "<code>_a_</code>" in str(inline_rich("`_a_`"))                  # nothing inside code
    assert "<em>" not in str(inline_rich("{{x_y}} {{_x_}}", mark_missing=True))
    assert "<em>" not in str(inline_rich("{{_x_}}", {"_x_": "Wert"})) and "Wert" in str(inline_rich("{{_x_}}", {"_x_": "Wert"}))
    assert "<p><em>Absatz</em> eins</p>" in str(rich("_Absatz_ eins"))
    assert plain("Formatierungen _möglich_.") == "Formatierungen möglich."


def test_ordinary_text_stays_as_it_is():
    for text in ("ca. ~5 bis ~10 Minuten", "2 ^ 3", "a ~ b", "Preis: 5 * 3 = 15", "Tom & Jerry"):
        assert plain(text) == text
        assert "<sub>" not in str(inline_rich(text)) and "<sup>" not in str(inline_rich(text))
    assert "&lt;script&gt;" in str(inline_rich("<script>alert(1)</script>"))  # everything else is escaped
    assert '<u onclick' not in str(inline_rich('<u onclick="x">a</u>'))         # no attributes


def test_plain_drops_the_marks():
    assert plain("**Wie** *oft* nutzen Sie `KI`? H~2~O, x^2^, <u>heute</u><br>") == "Wie oft nutzen Sie KI? H2O, x2, heute"


def test_rich_gets_the_new_marks_too():
    html = str(rich("**Überschrift**\n\nCO~2~ und `code`"))
    assert "CO<sub>2</sub>" in html and "<code>code</code>" in html


def test_question_text_formatted_for_participants_plain_in_export(app, client, survey):
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/2", data={"code": "q1", "question": "Wie **oft** nutzen Sie CO~2~-Rechner?",
                                                  "option_label": ["Nie", "Oft"], "option_code": ["1", "2"]})
    html = client.get("/surveys/1/editor/preview?block=2", follow_redirects=True).data.decode()
    assert "Wie <strong>oft</strong> nutzen Sie CO<sub>2</sub>-Rechner?" in html
    with app.app_context():
        from app import export
        from app.extensions import db
        from app.models import Survey

        labels = dict(export.columns(db.session.get(Survey, 1)))
    assert labels["q1"] == "Wie oft nutzen Sie CO2-Rechner?"
