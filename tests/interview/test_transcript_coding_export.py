"""Interviews kodieren, stage 4 (app/transcript_coding_export.py): an analysis as a REFI-QDA project (.qdpx) with the
coded passages as PlainTextSelection and Coding, and its category system as a codebook (.qdc); only the team exports."""
import io
import zipfile
from xml.etree import ElementTree as ET

from app.extensions import db
from app.models import CodingCategory
from tests.interview.test_transcript_coding_review import TEXT, _code, _setup, _sub

P = "{urn:QDA-XML:project:1.0}"
C = "{urn:QDA-XML:codebook:1.0}"


def test_project_with_coded_passages(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(bea, base, main, "Müdigkeit")
    exhausted = _sub(client, base, main, "Erschöpfung")
    _code(bea, base, rid, "Abends", tired, 29)
    _code(client, base, rid, "Manchmal", exhausted)
    client.post(f"{base}/categories/{exhausted}", data={"name": "Erschöpfung", "definition": "müde durch die Pflege",
                                                         "delimitation": "nicht: Krankheit"})
    client.post(f"{base}/merge", data={"source": tired, "target": exhausted, "reason": "dasselbe"})
    r = client.get(f"{base}/analysis.qdpx")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        root = ET.fromstring(z.read("project.qde"))
        source = root.find(f"{P}Sources/{P}TextSource")
        text = z.read(source.get("plainTextPath").replace("internal://", "Sources/")).decode("utf-8")
    assert text == TEXT
    with app.app_context():
        guid = db.session.get(CodingCategory, exhausted).guid.upper()
    codes = {c.get("name"): c for c in root.iter(f"{P}Code")}
    assert set(codes) == {"Belastung", "Erschöpfung"}                               # merged away: not exported
    assert codes["Erschöpfung"].get("guid") == guid and codes["Erschöpfung"] in list(codes["Belastung"])
    assert "nicht: Krankheit" in codes["Erschöpfung"].find(f"{P}Description").text
    selections = source.findall(f"{P}PlainTextSelection")
    assert len(selections) == 2
    for sel in selections:
        start, end = int(sel.get("startPosition")), int(sel.get("endPosition"))
        assert text[start:end] in ("Abends bin ich einfach fertig", "Manchmal")
        assert sel.find(f"{P}Coding/{P}CodeRef").get("targetGUID") == guid           # both count for the merged one
    users = {u.get("guid"): u.get("name") for u in root.iter(f"{P}User")}
    assert {"Philipp", "Bea"} <= set(users.values())
    assert {sel.get("creatingUser") for sel in selections} <= set(users)
    assert root.find(f"{P}Cases/{P}Case/{P}SourceRef").get("targetGUID") == source.get("guid")


def test_codebook_and_rights(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    _sub(client, base, main, "Erschöpfung")
    r = client.get(f"{base}/categories.qdc")
    root = ET.fromstring(r.data)
    assert [c.get("name") for c in root.iter(f"{C}Code")] == ["Belastung", "Erschöpfung"]
    assert bea.get(f"{base}/analysis.qdpx").status_code == 404                    # coders see no exports
    assert bea.get(f"{base}/categories.qdc").status_code == 404
    assert ".qdpx" in client.get(f"{base}/overview").data.decode() and ".qdpx" not in bea.get(f"{base}/overview").data.decode()


def test_the_csv_of_the_category_system_starts_no_formula_in_a_spreadsheet(monkeypatch):
    """A category name, a definition or a quote that begins like a formula gets an apostrophe in the CSV file
    (export.safe_text); the Excel file writes the same cells as plain text and keeps them as they are."""
    import csv
    import io

    from app import transcript_coding_export as module

    head = ["Nummer", "Kategorie", "Definition"]
    system = [["1", "=SUMME(A1:A9)", "-2"], ["1.1", "@Verweis", "+cmd|x"]]
    monkeypatch.setattr(module, "_codebook", lambda project, survey, who=None: (head, system, [], [], [], [], {}))
    rows = list(csv.reader(io.StringIO(module.build_csv(None, None).decode("utf-8-sig")), delimiter=";"))
    assert rows[1] == ["1", "'=SUMME(A1:A9)", "-2"] and rows[2] == ["1.1", "'@Verweis", "'+cmd|x"]
    assert system[0][1] == "=SUMME(A1:A9)"                                          # the rows of the Excel file untouched
