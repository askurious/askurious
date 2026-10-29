"""Interviews kodieren: the category system as an Excel file (app/transcript_coding_export.py, build_xlsx; writer
app/xlsx.py) with numbers down every level and the location of each passage ("B03, Pos. 12, 00:14:32",
app/transcript_coding_view.py, spot), the paragraph numbers in the margin of the coding view, and the same location in
the coding guide and the REFI-QDA description of an anchor example. Only the team of the guide exports."""
import io
import re
import zipfile
from xml.etree import ElementTree as ET

from app.extensions import db
from app.models import Response
from app.transcript_coding_view import line_numbers, paragraphs, position, spot
from tests.interview.test_transcript_coding import _transcript
from tests.interview.test_transcript_coding_review import _setup, _sub

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
TEXT = ("I: Wie geht es Ihnen abends? #00:00:04-0#\n\n"
        "B: Abends bin ich einfach fertig. #00:14:32-0#\n\n"
        "I: Und sonst?\n\n"
        "B: Manchmal frage ich mich, wie lange ich das noch schaffe.")


def _sheets(data):
    """[(sheet name, [[cell texts]])] of an .xlsx, read with the standard library only."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
        book = ET.fromstring(z.read("xl/workbook.xml"))
        out = []
        for i, sheet in enumerate(book.iter(f"{S}sheet"), 1):
            root = ET.fromstring(z.read(f"xl/worksheets/sheet{i}.xml"))
            rows = []
            for row in root.iter(f"{S}row"):
                cells = {}
                for c in row.iter(f"{S}c"):
                    col = re.match(r"[A-Z]+", c.get("r")).group(0)
                    t = c.find(f"{S}is/{S}t")
                    v = c.find(f"{S}v")
                    cells[col] = t.text if t is not None else (v.text if v is not None else "")
                rows.append([cells.get(chr(65 + n), "") for n in range(max((ord(k) - 64 for k in cells), default=0))])
            out.append((sheet.get("name"), rows, root))
    return out


def _code(who, base, rid, word, cat, length=None):
    start = TEXT.index(word)
    r = who.post(f"{base}/i/{rid}/codings", json={"start": start, "end": start + (length or len(word)), "cat": cat})
    assert r.json["ok"], r.json
    return r.json["coding"]["id"]


def test_paragraphs_and_location():
    assert [TEXT[a:b][:5] for a, b in paragraphs(TEXT)] == ["I: Wi", "B: Ab", "I: Un", "B: Ma"]
    assert line_numbers(TEXT) == [1, None, 2, None, 3, None, 4]
    assert position(TEXT, TEXT.index("fertig")) == (2, "00:14:32")      # the time mark of the paragraph
    assert spot("B03", TEXT, TEXT.index("Manchmal")) == "B03, Pos. 4"     # no mark there: no time
    lines = "B: eins\nI: zwei\n  \nB: drei"                                # an empty line separates paragraphs
    assert [lines[a:b] for a, b in paragraphs(lines)] == ["B: eins\nI: zwei", "B: drei"]
    single = "B: eins\nI: zwei #01:02:03-0#\nB: drei"                    # without any empty line: each line one
    assert line_numbers(single) == [1, 2, 3] and position(single, single.index("zwei")) == (2, "01:02:03")
    assert position("", 0) == (1, "")


def test_excel_category_system_and_passages(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    _transcript(app, rid, TEXT)
    with app.app_context():
        label = db.session.get(Response, rid).interview["label"]
    tired = _sub(client, base, main, "Müdigkeit", definition="erschöpft am Abend")
    evening = _sub(client, base, tired, "Abends")                          # a third level
    doubt = _sub(client, base, main, "Zweifel")
    client.post(f"{base}/categories/{tired}", data={"name": "Müdigkeit", "definition": "erschöpft am Abend",
                                                     "delimitation": "nicht: Krankheit"})
    # main categories fixed: a member's new main category becomes a candidate
    client.post(f"{base}/settings", data={"name": "Seminar", "question": "", "visibility": "all", "mains_fixed": "1"})
    bea.post(f"{base}/categories/new", data={"kind": "main", "name": "Geld", "definition": "Kosten der Pflege",
                                                  "anyway": "1"})
    anchor = _code(client, base, rid, "Abends bin", tired, 29)
    _code(bea, base, rid, "Abends bin", evening, 6)
    _code(client, base, rid, "Manchmal", doubt)
    assert client.post(f"{base}/i/{rid}/codings/{anchor}/anchor").json["ok"]

    r = client.get(f"{base}/categories.xlsx")
    assert r.status_code == 200 and r.mimetype.endswith("spreadsheetml.sheet")
    assert "kategoriensystem-" in r.headers["Content-Disposition"] and ".xlsx" in r.headers["Content-Disposition"]
    (name1, system, root1), (name2, passages, _root2) = _sheets(r.data)
    assert (name1, name2) == ("Kategoriensystem", "Stellen")
    assert system[0] == ["Nummer", "Ebene", "Kategorie", "Definition", "Ankerbeispiel", "Fundstelle des Ankers", "Weitere Beispiele",
                         "Kodierregel / Abgrenzung", "Stellen", "Stellen mit Unterkategorien", "Früher auch"]
    by_name = {row[2]: row for row in system[1:]}
    assert [row[0] for row in system[1:4]] == ["1", "1.1", "1.1.1"] and by_name["Zweifel"][0] == "1.2"
    assert [by_name[n][1] for n in ("Belastung", "Müdigkeit", "Abends")] == ["1", "2", "3"]
    tired_row = by_name["Müdigkeit"]
    assert tired_row[4] == "Abends bin ich einfach fertig" and tired_row[5] == f"{label}, Pos. 2, 00:14:32"
    assert tired_row[7] == "nicht: Krankheit" and tired_row[8:10] == ["1", "2"]   # itself, with the one below
    assert by_name["Belastung"][4:6] == ["", ""] and by_name["Belastung"][9] == "3"  # no anchor: empty
    assert system[-1][:3] == ["", "Kandidat", "Geld"]                    # candidates at the end, without a number
    assert passages[0] == ["Nummer", "Kategorie", "Interview", "Fundstelle", "Zitat", "Kodiert von"]
    rows = {(row[1], row[4]): row for row in passages[1:]}
    assert len(passages) == 4
    assert rows[("Müdigkeit", "Abends bin ich einfach fertig")][3] == f"{label}, Pos. 2, 00:14:32"
    assert rows[("Abends", "Abends")][0] == "1.1.1" and rows[("Abends", "Abends")][5] == "Bea"
    assert rows[("Zweifel", "Manchmal")][3] == f"{label}, Pos. 4"         # no time mark in that paragraph
    # the header stays in place and is bold; long cells wrap
    assert root1.find(f"{S}sheetViews/{S}sheetView/{S}pane").get("state") == "frozen"
    assert root1.find(f"{S}cols/{S}col") is not None

    # the same location for the anchor in the coding guide and in the REFI-QDA description
    guide = client.get(f"{base}/kodierleitfaden.docx")
    with zipfile.ZipFile(io.BytesIO(guide.data)) as z:
        assert f"({label}, Pos. 2, 00:14:32)" in re.sub(r"<[^>]+>", "", z.read("word/document.xml").decode())
    codebook = client.get(f"{base}/categories.qdc").data.decode()
    assert f"({label}, Pos. 2, 00:14:32)" in codebook


def test_paragraph_numbers_in_the_coding_view(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    _transcript(app, rid, TEXT)
    page = client.get(f"{base}/i/{rid}").data.decode()
    assert 'data-pos="1">I: Wie geht es' in page and 'data-pos="4">B: Manchmal' in page
    assert '"pos": [1, null, 2, null, 3, null, 4]' in page                 # for the drawing script


def test_only_the_team_exports_the_excel_file(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    assert bea.get(f"{base}/categories.xlsx").status_code == 404           # coders see no exports
    assert "categories.xlsx" in client.get(f"{base}/overview").data.decode()
    assert "categories.xlsx" not in bea.get(f"{base}/overview").data.decode()
