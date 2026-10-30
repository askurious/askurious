"""Interviews kodieren, the coding view (Ralf 2026-10-30, "beides"): the list of the marked passage shows below it
what the active category says, and a click on a category of the side opens the same text below it. Both are drawn by
transcript_coding_code.js from the categories of #tc-data, which carry definition, delimitation and the anchor example
with its place."""
import json
import re

from tests.interview.test_transcript_coding_review import _code, _setup


def _data(who, base, rid):
    page = who.get(f"{base}/i/{rid}").data.decode()
    return page, json.loads(re.search(r'id="tc-data">(.*?)</script>', page, re.S).group(1))


def test_categories_carry_definition_delimitation_and_anchor(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    r = client.post(f"{base}/categories/new", data={"kind": "sub", "parent": main, "name": "Erschöpfung", "anyway": "1",
                                                     "definition": "Müdigkeit am Ende des Tages", "delimitation": "nicht: Krankheit"})
    assert r.status_code == 302
    page, data = _data(client, base, rid)
    cat = next(c for c in data["categories"] if c["name"] == "Erschöpfung")
    assert cat["definition"] == "Müdigkeit am Ende des Tages" and cat["delimitation"] == "nicht: Krankheit"
    assert cat["anchor"] is None
    # an anchor example: its quote and where it stands, for the team and for a member
    coding = _code(client, base, rid, "Abends", cat["id"], 29)
    assert client.post(f"{base}/i/{rid}/codings/{coding}/anchor").json["ok"]
    for who in (client, bea):
        _page, data = _data(who, base, rid)
        cat = next(c for c in data["categories"] if c["name"] == "Erschöpfung")
        assert cat["anchor"]["quote"].startswith("Abends bin ich einfach fertig")
        assert cat["anchor"]["where"].startswith("AN-01, Pos. 1")
    # the page: the preview below the list, the info buttons find their icon, the arrow keys are named
    assert 'data-tc-preview' in page and 'data-icon-info="' in page and "#info" in page
    assert "↑ ↓ wählen" in page


def test_long_texts_are_cut(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    client.post(f"{base}/categories/new", data={"kind": "sub", "parent": main, "name": "Lang", "anyway": "1",
                                                 "definition": "d" * 900, "delimitation": "a" * 900})
    _page, data = _data(client, base, rid)
    cat = next(c for c in data["categories"] if c["name"] == "Lang")
    assert len(cat["definition"]) == 600 and len(cat["delimitation"]) == 600
