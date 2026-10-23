"""The formatting field over the text fields of the editor (app/static/richtext.js): it reads a text the way the
server does, and what it writes back is read by the server as the same formatted text. The JavaScript runs
under Node; without Node on the machine these tests are skipped."""
import re
import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.items.base import PLACEHOLDER, inline_rich, rich
from tests.conftest import add

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node to run app/static/richtext.js")

A, F = "\ue000", "\ue001"  # start and end of the selection in a text
FORMATS = ("b", "i", "u", "small", "code", "sub", "sup")


def run(cases):
    script = Path(__file__).parents[1].joinpath("richtext_cases.js")
    done = subprocess.run([NODE, str(script)], input=json.dumps(cases).encode(), capture_output=True, check=True)
    return json.loads(done.stdout.decode("utf-8"))


class _Model(HTMLParser):
    """What a browser shows for the HTML of rich / inline_rich, in the shape of richtext.js: blocks of text
    pieces with their formats. A format holds from its opening tag to its closing tag, also when tags overlap;
    closing a format also ends a sub or sup that was opened inside it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.open, self.atoms = [], [], None

    def block(self, kind):
        self.atoms, self.open = [], []
        self.blocks.append([kind, self.atoms])

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "h3", "li"):
            self.block({"p": "p", "h3": "h", "li": "li"}[tag])
        elif tag == "br":
            self.atoms.append(["br"])
        elif tag != "ul":
            self.open.append({"strong": "b", "em": "i"}.get(tag, tag))

    def handle_endtag(self, tag):
        name = {"strong": "b", "em": "i"}.get(tag, tag)
        if name in self.open:
            at = len(self.open) - 1 - self.open[::-1].index(name)
            inner = self.open[at + 1:] if name in ("sub", "sup") else [f for f in self.open[at + 1:] if f not in ("sub", "sup")]
            self.open = self.open[:at] + inner

    def handle_data(self, data):
        f, pos = " ".join(x for x in FORMATS if x in self.open), 0
        for m in PLACEHOLDER.finditer(data):
            self.atoms += [["t", data[pos:m.start()], f], ["v", m.group(1), f]]
            pos = m.end()
        self.atoms.append(["t", data[pos:], f])


def _tidy(atoms):
    out = []
    for a in atoms:
        if a[0] == "t" and not a[1]:
            continue
        if a[0] == "t" and out and out[-1][0] == "t" and out[-1][2] == a[2]:
            out[-1] = ["t", out[-1][1] + a[1], a[2]]
        else:
            out.append(list(a))
    return out


def model(html, mode):
    """The model of richtext.js (norm) for the HTML the server made."""
    parser = _Model()
    if mode == "inline":
        parser.block("p")
    parser.feed(str(html))
    parser.close()
    if mode == "inline":
        return [["p", _tidy(parser.atoms)]]
    out = []
    for kind, atoms in parser.blocks:
        lines = [[]]
        for a in _tidy(atoms):
            if a[0] == "br":
                lines.append([])
            else:
                lines[-1].append(a)
        for line in lines:  # spaces at the ends of a line show nowhere
            if line and line[0][0] == "t":
                line[0][1] = line[0][1].lstrip()
            if line and line[-1][0] == "t":
                line[-1][1] = line[-1][1].rstrip()
        lines = [_tidy(line) for line in lines]
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        if lines:
            flat = [a for i, line in enumerate(lines) for a in ([["br"]] if i else []) + line]
            out.append([kind, _tidy(flat)])
    return out


def server(text, mode):
    return inline_rich(text) if mode == "inline" else rich(text)


INLINE = [
    "**fett**, *kursiv*, <u>unter</u>, `a*b*c`, H~2~O, x^2^, <small>klein</small>, <sub>i</sub>, <sup>n</sup>",
    "ca. ~5 bis ~10 Minuten", "2 ^ 3", "a ~ b", "Preis: 5 * 3 = 15", "Tom & Jerry", "2*3*4", "5 * 3 * 2",
    "<script>alert(1)</script>", '<u onclick="x">a</u>', "eins\n\nzwei", "a\nb\n\nc", "",
    "***beides***", "**a *b* c**", "***a** b*", "*a **b***", "**a** **b**", "*kursiv*er", "a*b*", "**", "****", "*** ***",
    "Un<i>be</i>dingt", "<b>offen", "zu</b> viel", "<u>a<b>b</u>c</b>", "<B>GROSS</B> <br/> <BR >",
    "`code mit **Sternen**` und `", "`<b>`", "<code>**x**</code>", "`a\nb`",
    "Hallo {{name}}, **{{ ort }}**!", "{{kein platzhalter}", "~{{x}}~ und `{{y}}`",
    "**fett\nüber Zeilen**", "*a~b*c~", "~a b~", "x^2^^3^", "~~a~~", "^a^b^", "<i>*x*</i>",
    "<small><sub>x</sub></small>", "<sub>a<i>b</sub>c</i>", "<i>a<sub>b</i>c</sub>",
    "\"Anführung\" 'einfach' & <> &amp; &lt;b&gt;", "Ende mit Leerzeichen ", "  vorn", "Größe: 5 m^2^ *je* Person (m²)",
    # _italic_ (pre-test 2026-10-20, 33), never inside words, addresses, paths or placeholders
    "Formatierungen _möglich_.", "snake_case und Q1_1", "a_b@x.de", "https://x.de/_a_/b?x=_y_&z=1", "{{x_y}} {{_x_}}",
    "Name: ____ und ____", "\"_zitat_\" „_wort_“ (_a b_)", "**_fett kursiv_**", "_x_y", "eins_ zwei _drei", "`_a_`",
    "_a\nb_", "*a* _b_ *c*", "_kursiv_er", "C:\\_a_\\b",
]
BLOCK = [
    "**Überschrift**\n\nCO~2~ und `code`", "Absatz eins\nzweite Zeile\n\nAbsatz zwei", "- a\n- b\n- **c**",
    "**Titel**\n- a\n- b", "**Titel**\nText darunter", "- a\nkein Listenpunkt", "  eingerückt  \n\n\n\n  noch einer ",
    "a<br>b", "a<br>\nb", "- a<br>b\n-  c", "<b>ganz fett</b>", "**fett** am Anfang, dann Text", "-kein Punkt", "- ", "",
    "{{name}}\n\n- {{x}} **y**", "Text mit <u>offenem Tag\nnächste Zeile", "**H**\n\n**H2**\nText\n\n- l",
    "*kursiv\nüber Zeilen*", "a\n \nb", "\n\nführende Leerzeilen", "`code`\n- x", "**Titel mit *kursiv* darin**",
    "Zeile <small>klein</small>\n- kein\n\n- doch\n- ja", "**a**\n**b**", "Ein Satz.\n\n**Fett** und <i>Wort</i>teil ~a b~.",
    "_kursiv\nüber Zeilen_", "- _a_\n- b_c", "**Titel mit _kursiv_ darin**",
]
CASES = [(t, "inline") for t in INLINE] + [(t, "block") for t in BLOCK]


@pytest.fixture(scope="module")
def results():
    return dict(zip(CASES, run([{"text": t, "mode": m} for t, m in CASES])))


@needs_node
@pytest.mark.parametrize("text,mode", CASES)
def test_field_reads_a_text_as_the_server_does(results, text, mode):
    assert results[text, mode]["model"] == model(server(text, mode), mode)


@needs_node
@pytest.mark.parametrize("text,mode", CASES)
def test_what_the_field_writes_is_shown_the_same(results, text, mode):
    """A text that goes through the field (read, written back) looks the same to participants; writing it a
    second time changes nothing more."""
    r = results[text, mode]
    assert r["exact"]
    assert model(server(r["text"], mode), mode) == model(server(text, mode), mode)
    assert r["again"] == r["text"]


@needs_node
def test_field_writes_the_marks_researchers_know():
    """Marks where the server reads them, tags only where it would not."""
    texts = ["Wie **oft** nutzen Sie *wirklich* CO~2~-Rechner (m^2^)?", "<u>unter</u> und <small>klein</small>", "`a*b`",
             "***beides***", "Un<i>be</i>dingt", "<sub>a b</sub>"]
    assert [r["text"] for r in run([{"text": t, "mode": "inline"} for t in texts])] == texts
    spelled = run([{"text": "<strong>a</strong> <em>b</em> <b>c</b><br>d <sub>2</sub>", "mode": "inline"},
                   {"text": "**Titel**\nText\n\n\n- a", "mode": "block"}, {"text": "<b>ganz fett</b>\n\n- **a** b", "mode": "block"}])
    assert [r["text"] for r in spelled] == ["**a** *b* **c**\nd ~2~", "**Titel**\n\nText\n\n- a", "<b>ganz fett</b>\n\n- **a** b"]


@needs_node
def test_gaps_of_a_cloze_text():
    text = "Die [ ] ist **[   ]** und\n[     ]."
    with_gaps, without = run([{"text": text, "mode": "block", "gaps": True}, {"text": text, "mode": "block"}])
    assert [a for a in with_gaps["model"][0][1] if a[0] == "g"] == [["g", 1, ""], ["g", 3, "b"], ["g", 5, ""]]
    assert with_gaps["text"] == text and with_gaps["exact"]
    assert not any(a[0] == "g" for a in without["model"][0][1]) and without["text"] == text


@needs_node
def test_buttons_and_keys():
    def after(text, op, arg=None, mode="inline"):
        return run([{"text": text, "mode": mode, "op": op, "arg": arg}])[0]["text"]

    assert after(f"Hallo {A}Welt{F}", "toggleFormat", "b") == "Hallo **Welt**"
    assert after(f"Hallo {F}Welt{A}", "toggleFormat", "u") == "Hallo <u>Welt</u>"           # selected backwards
    assert after(f"Un{A}be{F}dingt", "toggleFormat", "i") == "Un<i>be</i>dingt"
    assert after(f"**a {A}b{F} c**", "toggleFormat", "b") == "**a **b** c**"                # bold off in the middle
    assert after(f"H{A}2{F}O", "toggleFormat", "sub") == "H~2~O"
    assert after(f"x{A}~2~{F}", "toggleFormat", "sup") == "x^2^"                           # high instead of low
    assert after(f"{A}eins **zwei**{F}", "toggleFormat", "b") == "**eins zwei**"            # partly bold: all bold
    assert after(f"a{A}{F}", "insertText", "x\ny <b>") == "ax\ny <b>"                       # only text arrives
    assert after(f"ab{A}{F}cd", "splitBlock", mode="block") == "ab\n\ncd"
    assert after(f"- a\n- b{A}{F}c", "splitBlock", mode="block") == "- a\n- b\n- c"
    assert after(f"{A}a\n\nb{F}", "toggleType", "li", mode="block") == "- a\n- b"
    assert after(f"- a\n- {A}{F}b", "toggleType", "li", mode="block") == "- a\n\nb"
    assert after(f"Titel{A}{F}\n\nText", "toggleType", "h", mode="block") == "**Titel**\n\nText"
    assert after(f"x{A}{F}", "insertText", "eins\nzwei\n\ndrei", mode="block") == "xeins\nzwei\n\ndrei"


@needs_node
def test_typed_marks_become_formatting():
    """Somebody types **word** or "- " into the field: it then shows what participants will see."""
    typed = run([{"text": "a", "mode": "inline", "op": "insertText", "arg": "**b** {{name}}"},
                 {"text": "", "mode": "block", "op": "insertText", "arg": "- eins"}])
    assert typed[0]["shown"] == "a**b** {{name}}" and typed[1]["shown"] == "- eins"
    assert ["v", "name", ""] in run([{"text": typed[0]["shown"], "mode": "inline"}])[0]["model"][0][1]


@needs_node
def test_placeholder_with_a_text_for_an_empty_value():
    """{{name|text}} is one placeholder that carries its text (app/piping.py); the text is plain, whatever marks it
    holds, and a placeholder set by "Antwort einfügen" is written the way the server reads it."""
    read, marks, odd = run([{"text": "{{marke|Ihr Produkt}} ist **{{ name | du }}**", "mode": "inline"},
                            {"text": "{{a|*x*}} und {{b|}}", "mode": "inline"},
                            {"text": "{{a|b|c}} {{a|{b}}", "mode": "inline"}])
    assert read["model"] == [["p", [["v", "marke|Ihr Produkt", ""], ["t", " ist ", ""], ["v", "name|du", "b"]]]]
    assert read["text"] == "{{marke|Ihr Produkt}} ist **{{name|du}}**" and read["exact"]
    assert marks["model"] == [["p", [["v", "a|*x*", ""], ["t", " und ", ""], ["v", "b", ""]]]]
    assert inline_rich("**{{a|*x*}}**", {"a": ""}) == "<strong>*x*</strong>"     # the server: no formatting inside it
    assert not any(a[0] == "v" for a in odd["model"][0][1]) and odd["text"] == "{{a|b|c}} {{a|{b}}"  # no placeholders
    put = run([{"text": f"Zu {A}{F}:", "mode": "inline", "op": "insert", "arg": [{"k": "v", "name": "marke", "alt": "Ihr Produkt"}]},
               {"text": f"- a{A}b{F}", "mode": "block", "op": "insert", "arg": [{"k": "v", "name": "marke"}]}])
    assert [r["text"] for r in put] == ["Zu {{marke|Ihr Produkt}}:", "- a{{marke}}"]


def _field(html, name):
    """The opening tag of the textarea with this name."""
    start = html.index(f'<textarea name="{name}"')
    return html[start:html.index(">", start) + 1]


def test_question_field_gets_the_formatting_field(client, survey):
    add(client, "single_choice")
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'data-rich="inline"' in _field(html, "question")
    assert "richtext.js" in html


def test_long_texts_get_the_field_with_paragraphs(client, survey):
    """Info text, consent, product description, texts before choice tasks, end texts: paragraphs, lists and
    headings as in rich(). The text of a cloze item also knows its gaps; while the survey is a draft their number
    may change."""
    for kind in ("info", "cloze", "psm", "maxdiff", "highlight"):
        add(client, kind)
    page = lambda item: client.get(f"/surveys/1/editor/items/{item}").data.decode()  # noqa: E731
    assert 'data-rich="block"' in _field(page(1), "body")                  # consent
    assert 'data-rich="block"' in _field(page(2), "question")              # info
    cloze = _field(page(3), "text")
    assert 'data-rich="block"' in cloze and "data-rich-gaps" in cloze and "data-rich-gaps-fixed" not in cloze
    assert 'data-rich="inline"' in _field(page(3), "question")
    assert 'data-rich="block"' in _field(page(4), "product")
    assert 'data-rich="block"' in _field(page(5), "explain_intro")
    assert "data-rich" not in _field(page(6), "passage")                   # words are counted: plain text
    # an end text without an own text shows the standard; "Anpassen" brings the formatted field (V88, app.js)
    assert 'data-standard="' in _field(client.get("/surveys/1").data.decode(), "thanks_de")


def test_translation_page_uses_the_field_for_formatted_texts(client, survey):
    """Texts participants see formatted are translated in the formatting field, with the source shown formatted
    next to it; options and labels stay plain text fields."""
    for kind in ("single_choice", "info", "cloze"):
        add(client, kind)
    client.post("/surveys/1/editor/items/2", data={"code": "q1", "question": "Wie **oft**?", "option_label": ["Nie", "Oft"], "option_id": ["", ""],
                                                  "option_code": ["1", "2"]})
    client.post("/surveys/1/editor/items/3", data={"code": "i1", "question": "**Hinweis**\n\n- eins\n- zwei"})
    client.post("/surveys/1/editor/items/4", data={"code": "c1", "question": "Bitte ergänzen", "text": "Paris liegt in [ ]."})
    html = client.get("/surveys/1/translation/?lang=en").data.decode()

    def row(unit, path):
        start = html.index(f'data-unit="{unit}" data-path="{path}"')
        return html[html.rindex("<div class=\"tr-row\"", 0, start):html.index("</textarea>", start)]

    question = row("item:2", "question")
    assert 'data-rich="inline"' in question and 'data-rich-bar="float"' in question and 'data-rich-show="inline"' in question
    option = re.search(r'data-unit="item:2" data-path="(options\.[^"]+)"', html).group(1)
    assert "data-rich" not in row("item:2", option)                         # an answer option: plain
    assert 'data-rich="block"' in row("item:3", "question") and 'data-rich-show="block"' in row("item:3", "question")
    cloze = row("item:4", "text")
    assert 'data-rich="block" data-rich-gaps' in cloze and 'data-rich-show="block" data-rich-gaps' in cloze
    assert 'data-rich="inline"' in row("item:4", "question")


def test_which_texts_count_as_formatted(app, client, survey):
    from app import translations
    from app.extensions import db
    from app.models import Survey

    for kind in ("info", "cloze", "psm", "likert"):
        add(client, kind)
    with app.app_context():
        units = {u.key: u for u in translations.units(db.session.get(Survey, 1), "en")}
        mode = translations.rich_mode
        assert mode(units["item:1"], "question") == "inline" and mode(units["item:1"], "body") == "block"   # consent
        assert mode(units["item:2"], "question") == "block"                                                  # info
        assert mode(units["item:3"], "text") == "gaps" and mode(units["item:3"], "question") == "inline"     # cloze
        assert mode(units["item:4"], "product") == "block" and mode(units["item:4"], "q_cheap") == ""        # psm
        assert mode(units["item:5"], "statements.0.text") == "" and mode(units["item:5"], "text") == ""      # likert
        assert mode(units["end"], "thanks") == "block" and mode(units["title"], "title") == ""
