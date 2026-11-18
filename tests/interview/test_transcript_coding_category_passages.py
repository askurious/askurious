"""Interviews kodieren: a click on a category of the diagram opens a dialog with its passages (Ralf 2026-11-18). The
passages come from a JSON route only on demand, a page at a time, for the category and everything below it (as the
diagram counts), with the same rights as the other overviews of an analysis."""
import re

from tests.interview.test_transcript_coding import _person
from tests.interview.test_transcript_coding_review import TEXT, _code, _setup, _sub


def test_the_nodes_of_the_diagram_name_their_passages(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    sub = _sub(client, base, main, "Erschöpfung")
    page = client.get(f"{base}?view=categories&show=diagram").data.decode()
    assert f'data-tc-passages="{base}/categories/{main}/passages.json"' in page
    assert f'data-tc-passages="{base}/categories/{sub}/passages.json"' in page
    assert "data-tc-passages-dialog" in page and 'tabindex="0" role="button"' in page
    assert "data-tc-passages-dialog" not in client.get(f"{base}?view=categories").data.decode()   # the list: none
    assert b"data-tc-passages" not in client.get(f"{base}/map.svg").data                          # nor the download


def test_passages_of_a_category_with_everything_below_it(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(client, base, main, "Erschöpfung")
    evening = _sub(client, base, tired, "Abendliche Müdigkeit")
    _code(client, base, rid, "Abends", evening, 29)
    _code(client, base, rid, "Manchmal", tired, 20)
    d = client.get(f"{base}/categories/{main}/passages.json").json
    assert d["name"] == "Belastung" and d["total"] == 2 and d["pages"] == 1 and d["sizes"] == [10, 20, 50]
    first, second = d["rows"]
    assert first["sub"] == "Erschöpfung" and second["sub"] == "Erschöpfung › Abendliche Müdigkeit"  # tree order
    assert second["quote"] == "Abends bin ich einfach fertig"
    assert second["where"].endswith("Pos. 1") and "/i/" in second["url"] and "at=" in second["url"]
    d = client.get(f"{base}/categories/{tired}/passages.json").json
    assert d["total"] == 2 and d["rows"][0]["sub"] == "" and d["rows"][1]["sub"] == "Abendliche Müdigkeit"
    assert client.get(f"{base}/categories/{evening}/passages.json").json["total"] == 1
    # the number in the dialog's head is the number on the diagram's node
    svg = client.get(f"{base}/map.svg").data.decode()
    assert ">2</text>" in svg
    # members see it too, others not; a category of another analysis or none at all: 404
    assert bea.get(f"{base}/categories/{main}/passages.json").json["total"] == 2
    stranger, _id = _person(app, orcid="0000-0003-1415-9269", name="Cem")
    assert stranger.get(f"{base}/categories/{main}/passages.json").status_code == 404
    assert client.get(f"{base}/categories/999999/passages.json").status_code == 404


def test_pages_of_passages(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    sub = _sub(client, base, main, "Erschöpfung")
    words = [m for m in re.finditer(r"\w{2,}", TEXT)][:12]                          # twelve passages, one word each
    assert len(words) == 12
    for m in words:
        assert client.post(f"{base}/i/{rid}/codings", json={"start": m.start(), "end": m.end(), "cat": sub}).json["ok"]
    d = client.get(f"{base}/categories/{sub}/passages.json").json
    assert d["total"] == 12 and d["per"] == 10 and d["pages"] == 2 and len(d["rows"]) == 10
    d = client.get(f"{base}/categories/{sub}/passages.json?page=2").json
    assert d["page"] == 2 and len(d["rows"]) == 2
    d = client.get(f"{base}/categories/{sub}/passages.json?per=20&page=9").json        # beyond the end: the last
    assert d["per"] == 20 and d["page"] == 1 and len(d["rows"]) == 12
    assert client.get(f"{base}/categories/{sub}/passages.json?per=7").json["per"] == 10  # only 10, 20, 50


def test_the_dialog_names_the_definition_of_the_category(app, client):
    """The definition of the clicked category stands fixed above the list (Ralf 2026-11-18); none: empty, no text."""
    code, pid, rid, base, main, bea = _setup(app, client)
    sub = _sub(client, base, main, "Erschöpfung", definition="Müdigkeit nach der Arbeit.\nAuch am Wochenende.")
    assert client.get(f"{base}/categories/{sub}/passages.json").json["definition"] == "Müdigkeit nach der Arbeit.\nAuch am Wochenende."
    assert client.get(f"{base}/categories/{main}/passages.json").json["definition"] == ""
    page = client.get(f"{base}?view=categories&show=diagram").data.decode()
    assert re.search(r'<p class="[^"]*tc-passages-def[^"]*" data-tc-passages-def hidden></p>', page)
