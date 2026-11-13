"""Interviews kodieren: the category system as a place to drag (Ralf 2026-10-28). The map on the page carries the
nodes to drag (only where the person may move them) and the root as "als Hauptkategorie", the SVG to download stays
plain; reordering among siblings; merging that harmonises name, definition, delimitation and anchor, taken back
exactly; the dialog's data; the link "Diagramm" in the coding view; the rules of a drop (app/static/transcript_coding_drop.js)
run with Node."""
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace as N

import pytest

from app import transcript_coding as tc, transcript_coding_review as review
from app.extensions import db
from app.models import CodingCategory, CodingEvent, CodingProject
from tests.interview.test_transcript_coding_review import _code, _setup, _sub

NODE = shutil.which("node")


def _names(app, pid, parent):
    with app.app_context():
        return [c.name for c in tc.live(db.session.get(CodingProject, pid)) if c.parent_id == parent]


def test_map_to_drag_and_the_plain_svg(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    sub = _sub(client, base, main, "Erschöpfung")
    page = client.get(f"{base}?view=categories&show=diagram").data.decode()
    assert f'data-tc-cat="{sub}"' in page and f'data-tc-grab="{sub}"' in page and "data-tc-drop-main" in page
    assert 'id="tc-drop-data"' in page and "data-tc-drop-dialog" in page and "transcript_coding_drop.js" in page
    svg = client.get(f"{base}/map.svg").data.decode()
    assert "data-tc-" not in svg and "<title>" not in svg                           # the download stays a plain picture
    # with the main categories fixed a member drags subcategories, not the main ones
    client.post(f"{base}/settings", data={"name": "Seminar", "question": "", "visibility": "all", "mains_fixed": "1"})
    page = bea.get(f"{base}?view=categories&show=diagram").data.decode()
    assert f'data-tc-grab="{sub}"' in page and f'data-tc-grab="{main}"' not in page and f'data-tc-cat="{main}"' in page
    data = json.loads(page.split('id="tc-drop-data">', 1)[1].split("</script>", 1)[0])
    assert data["cats"][str(main)]["move"] is False and data["fixed"] and not data["team"] and data["max"] == tc.MAX_DEPTH
    # the coding view leads to the map
    assert f"{base}?view=categories&amp;show=diagram" in client.get(f"{base}/i/{rid}").data.decode()


def test_reorder_among_siblings_and_take_it_back(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    a, b, c = (_sub(client, base, main, n) for n in ("A", "B", "C"))
    assert _names(app, pid, main) == ["A", "B", "C"]
    r = client.post(f"{base}/categories/{c}/order", data={"before": str(a), "json": "1"})
    assert r.json["ok"] and _names(app, pid, main) == ["C", "A", "B"]
    client.post(f"{base}/categories/{c}/order", data={"before": ""})                  # to the end
    assert _names(app, pid, main) == ["A", "B", "C"]
    bea_sub = _sub(bea, base, main, "D")
    assert bea.post(f"{base}/categories/{bea_sub}/order", data={"before": str(a), "json": "1"}).json["ok"]
    assert _names(app, pid, main) == ["D", "A", "B", "C"]
    assert not client.post(f"{base}/categories/{a}/order", data={"before": str(main), "json": "1"}).json["ok"]  # not a sibling
    bea.post(f"{base}/undo")
    assert _names(app, pid, main) == ["A", "B", "C", "D"]


def test_merge_and_harmonise_then_take_back_exactly(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    a = _sub(client, base, main, "Müdigkeit", definition="müde sein")
    b = _sub(client, base, main, "Erschöpfung", definition="erschöpft sein")
    _code(client, base, rid, "Abends", a, 29)
    with app.app_context():
        before = {c.id: (c.name, c.definition, c.delimitation, c.anchor, c.former_names) for c in tc.live(db.session.get(CodingProject, pid))}
    r = client.post(f"{base}/merge", data={"source": a, "target": b, "reason": "dasselbe", "name": "Erschöpfung am Abend",
                                            "definition": "müde oder erschöpft", "delimitation": "nicht: krank", "anchor": "target"})
    assert r.status_code == 302
    with app.app_context():
        target = db.session.get(CodingCategory, b)
        assert (target.name, target.definition, target.delimitation) == ("Erschöpfung am Abend", "müde oder erschöpft", "nicht: krank")
        assert set(target.former_names) == {"Erschöpfung", "Müdigkeit"}
        event = db.session.scalar(db.select(CodingEvent).filter_by(project_id=pid, kind="merged"))
        assert event.data["unified"] and event.data["before"]["name"] == "Erschöpfung"
    assert "vereinheitlicht sie als „Erschöpfung am Abend“" in client.get(f"{base}/history").data.decode()
    client.post(f"{base}/events/{event.id}/undo")
    with app.app_context():
        after = {c.id: (c.name, c.definition, c.delimitation, c.anchor, c.former_names) for c in tc.live(db.session.get(CodingProject, pid))}
        assert after[b] == before[b] and after[a] == before[a]                       # both exactly as before
    # a name taken at that place is refused; without the fields it merges as before
    c = _sub(client, base, main, "Schlaf")
    client.post(f"{base}/merge", data={"source": a, "target": b, "reason": "x", "name": "Schlaf"})
    with app.app_context():
        assert db.session.get(CodingCategory, a).merged_into is None
    client.post(f"{base}/merge", data={"source": a, "target": b, "reason": "x"})
    with app.app_context():
        assert db.session.get(CodingCategory, a).merged_into == b and db.session.get(CodingCategory, b).name == "Erschöpfung"
    assert c


@pytest.mark.skipif(NODE is None, reason="needs Node to run app/static/transcript_coding_drop.js")
def test_rules_of_a_drop_with_node():
    cats = {"1": {"id": 1, "kind": "main", "level": 1, "anc": [], "parent": None, "move": False},
            "2": {"id": 2, "kind": "sub", "level": 2, "anc": [1], "parent": 1, "move": True},
            "3": {"id": 3, "kind": "sub", "level": 3, "anc": [1, 2], "parent": 2, "move": True},
            "4": {"id": 4, "kind": "main", "level": 1, "anc": [], "parent": None, "move": False},
            "5": {"id": 5, "kind": "sub", "level": 2, "anc": [4], "parent": 4, "move": True},
            "6": {"id": 6, "kind": "candidate", "level": 1, "anc": [], "parent": None, "move": True}}
    data = {"cats": cats, "team": False, "fixed": True, "max": 3, "may_main": False}
    calls = [["hang", "2", "5"], ["hang", "2", "3"], ["hang", "3", "5"], ["hang", "2", "4"], ["hang", "1", "4"],
             ["hang", "5", "6"], ["merge", "2", "5"], ["merge", "1", "4"], ["merge", "2", "3"], ["merge", "2", "4"],
             ["siblings", "2", "5"], ["siblings", "1", "4"]]
    script = Path(__file__).parents[1] / "transcript_coding_drop_cases.js"
    out = subprocess.run([NODE, str(script)], input=json.dumps({"data": data, "calls": calls}), capture_output=True, text=True,
                         timeout=60, check=True)
    r = json.loads(out.stdout)
    hang = {tuple(c[1:]): x for c, x in zip(calls, r) if c[0] == "hang"}
    merge = {tuple(c[1:]): x for c, x in zip(calls, r) if c[0] == "merge"}
    assert not hang[("2", "5")]["ok"] and "tiefer" in hang[("2", "5")]["why"]        # 2 + its child would reach level 4
    assert not hang[("2", "3")]["ok"]                                                # not under something below itself
    assert hang[("3", "5")]["ok"]
    assert hang[("2", "4")]["ok"]                                                    # level 1 + 2 = 3: allowed
    assert not hang[("1", "4")]["ok"] and "Forschenden" in hang[("1", "4")]["why"]   # main level: the team's
    assert not hang[("5", "6")]["ok"]                                                # nothing under a candidate
    assert merge[("2", "5")]["ok"] and not merge[("1", "4")]["ok"] and not merge[("2", "3")]["ok"] and not merge[("2", "4")]["ok"]
    assert r[-2] is False and r[-1] is True                                          # 2 and 5 under different ones

# --- no line over a number (Ralf 2026-11-10: the dashed lines of blurring pairs lay over the numbers): every line is
# drawn before the rows, every subcategory row has a white ground, and lines leave a row to the right of its number

def _cat(i, name, level, parent):
    return N(id=i, name=name, level=level, parent_id=parent, colour=None, anc=[], path=name)


def test_lines_under_the_rows_and_right_of_the_numbers(app, monkeypatch):
    main = _cat(1, "Belastung", 1, None)
    subs = [_cat(10, "Erschöpfung", 2, 1), _cat(11, "Zeitdruck", 2, 1), _cat(12, "Abends", 3, 10)]
    cats = {c.id: c for c in [main] + subs}
    monkeypatch.setattr(review, "tree", lambda p: ([(main, subs)], []))
    monkeypatch.setattr(review, "stats", lambda p, w: {1: {"n": 25}, 10: {"n": 16}, 11: {"n": 5}, 12: {"n": 4}})
    monkeypatch.setattr(review, "fuzzy", lambda p, w: [{"a": cats[10], "b": cats[11], "overlap": 1, "checks": 0},
                                                       {"a": cats[11], "b": cats[12], "overlap": 0, "checks": 1}])
    with app.test_request_context():
        svg = review.map_svg(N(name="P", question="F?"), None, None)
    first_row = svg.index('<circle')
    assert svg.rfind("<path") < first_row                                           # all lines under the rows
    counts = {int(m.group(2)): float(m.group(1)) for m in
              re.finditer(r'<text x="([\d.]+)" y="[\d.-]+" text-anchor="end" fill="#5f5e59">(\d+)</text>', svg)}
    dashed = re.findall(r'<path d="M([\d.]+) [\d.-]+ C [^"]*, ([\d.]+) [\d.-]+" fill="none" stroke="#b35c00"', svg)
    assert len(dashed) == 2
    for xa, xb in dashed:
        for x in (float(xa), float(xb)):
            assert any(0 < x - cx <= 20 for n, cx in counts.items() if n != 25), x    # starts right of a number
    assert svg.count('fill="#ffffff" stroke="none"') == 3                            # a white ground per row
