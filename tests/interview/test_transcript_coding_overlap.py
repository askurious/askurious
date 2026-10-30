"""Interviews kodieren: overlaps of categories in the overview (app/transcript_coding_overlap.py, Ralf 2026-10-30):
counted by shared characters (or the same paragraph), the diagonal the passages, levels main/all, a cell lists its
pairs, blind double coding counts only what one sees, and the Excel file has the sheet "Überschneidungen"."""
import re

from app.extensions import db
from app.models import CodingCategory, Response
from tests.interview.test_transcript_coding import _person, _transcript
from tests.interview.test_transcript_coding_categories_xlsx import _sheets
from tests.interview.test_transcript_coding_review import BEA, _code, _setup, _sub

TEXT2 = "B: Abends bin ich einfach fertig.\n\nManchmal frage ich mich, wie lange ich das noch schaffe."


def _world(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(client, base, main, "Erschöpfung")
    doubt = _sub(client, base, main, "Zweifel")
    client.post(f"{base}/categories/new", data={"kind": "main", "name": "Zukunft", "anyway": "1"})
    with app.app_context():
        future = db.session.scalar(db.select(CodingCategory.id).filter_by(project_id=pid, name="Zukunft"))
    return code, pid, rid, base, main, bea, tired, doubt, future


def _cells(page):
    box = page[page.index('id="ueberschneidungen"'):]
    box = box[:box.index("</table>")]
    return [re.findall(r"<td class=\"num[^\"]*\"[^>]*>(?:<a [^>]*>)?(?:<span class=\"muted\">)?(\d+)", row)
            for row in box.split("<tr>")[2:]]


def test_overlaps_count_shared_characters_and_the_diagonal(app, client):
    code, pid, rid, base, main, bea, tired, doubt, future = _world(app, client)
    _code(client, base, rid, "Abends", tired, 29)                     # "Abends bin ich einfach fertig."
    _code(client, base, rid, "einfach", doubt, 14)                    # inside it
    _code(client, base, rid, "Manchmal", future, 30)                  # elsewhere
    page = client.get(f"{base}/overview?ov=all").data.decode()
    assert "Überschneidungen" in page and "Was sagen Überschneidungen?" in page
    # rows/columns: Belastung, Erschöpfung, Zweifel, Zukunft
    assert _cells(page) == [["0", "0", "0", "0"], ["0", "1", "1", "0"], ["0", "1", "1", "0"], ["0", "0", "0", "1"]]
    # main level: both passages count for "Belastung", which is not an overlap with itself
    assert _cells(client.get(f"{base}/overview").data.decode()) == [["2", "0"], ["0", "1"]]
    # a cell lists the pair with places and quotes, and the ways to tidy up
    page = client.get(f"{base}/overview?ov=all&ov_a={tired}&ov_b={doubt}").data.decode()
    assert "1 Paar<" in page and "AN-01, Pos. 1" in page and "„einfach fertig“" in page
    assert f"merge={tired},{doubt}" in page and "Alle Stellen beider ansehen" in page


def test_the_same_paragraph_counts_when_asked(app, client):
    code, pid, rid, base, main, bea, tired, doubt, future = _world(app, client)
    _transcript(app, rid, TEXT2)
    start = TEXT2.index("Manchmal")
    for word, cat, n in (("Abends", tired, 6), ("fertig", doubt, 6)):   # same paragraph, no shared character
        s = TEXT2.index(word)
        assert client.post(f"{base}/i/{rid}/codings", json={"start": s, "end": s + n, "cat": cat}).json["ok"]
    assert client.post(f"{base}/i/{rid}/codings", json={"start": start, "end": start + 8, "cat": future}).json["ok"]
    assert _cells(client.get(f"{base}/overview?ov=all").data.decode())[1] == ["0", "1", "0", "0"]
    para = _cells(client.get(f"{base}/overview?ov=all&ov_para=1").data.decode())
    assert para[1] == ["0", "1", "1", "0"] and para[3] == ["0", "0", "0", "1"]   # another paragraph: none


def test_blind_double_coding_counts_only_what_one_sees(app, client):
    from app.models import User
    from tests.interview.test_transcript_coding_double import _assign

    code, pid, rid, base, main, bea, tired, doubt, future = _world(app, client)
    carl, _c = _person(app, "0000-0001-5109-3700", "Carl")
    client.post(f"{base}/members", data={"kind": "person", "person": "0000-0001-5109-3700"})
    with app.app_context():
        ids = [db.session.scalar(db.select(User.id).filter_by(orcid=o)) for o in (BEA, "0000-0001-5109-3700")]
    _assign(app, client, base, rid, [f"u:{i}" for i in ids])
    _code(bea, base, rid, "Abends", tired, 29)
    _code(carl, base, rid, "einfach", doubt, 14)
    assert _cells(bea.get(f"{base}/overview?ov=all").data.decode())[1] == ["0", "1", "0", "0"]     # Carl's: hidden
    assert _cells(client.get(f"{base}/overview?ov=all").data.decode())[1] == ["0", "1", "1", "0"]  # the team: all
    assert "ov_a" not in bea.get(f"{base}/overview?ov=all&ov_a={tired}&ov_b={doubt}").data.decode().split('id="ueberschneidungen"')[1].split("</table>")[0]


def test_the_excel_file_has_the_overlaps(app, client):
    code, pid, rid, base, main, bea, tired, doubt, future = _world(app, client)
    _code(client, base, rid, "Abends", tired, 29)
    _code(client, base, rid, "einfach", doubt, 14)
    rows = dict((name, rows) for name, rows, _root in _sheets(client.get(f"{base}/categories.xlsx").data))["Überschneidungen"]
    assert rows[0][0] == "Kategorie" and rows[0][2].endswith("Erschöpfung")
    tired_row = next(r for r in rows if str(r[0]).endswith("Erschöpfung"))
    assert tired_row[2] == "1" and tired_row[3] == "1"                # itself, and with "Zweifel"
    with app.app_context():
        assert db.session.get(Response, rid) is not None
