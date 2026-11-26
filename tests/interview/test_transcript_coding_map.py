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
from tests.interview.test_interviewer import add as add_interviewer
from tests.interview.test_interviewer import begin, open_link
from tests.interview.test_transcript_coding import _transcript
from tests.interview.test_transcript_coding_review import TEXT, _code, _setup, _sub, _sub_under

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
    # a ground per row, tinted in the colour of the category (Ralf 2026-11-20), so no line shows through
    assert len(re.findall(r'rx="6" fill="#[0-9a-f]{6}" stroke="none"', svg)) == 3


# --- long names wrap instead of being cut off (Ralf 2026-11-20): names stopped after 22 (subcategories), 30 (main
# categories), 34 (project) and 42 characters (question)

def test_long_names_wrap_in_full_and_rows_do_not_overlap(app, monkeypatch):
    long_main = "Belastungen im Alltag zwischen Beruf und Familie"
    long_sub = "Erschöpfung am Abend nach langen Schichten im Krankenhaus"
    main = _cat(1, long_main, 1, None)
    subs = [_cat(10, long_sub, 2, 1), _cat(11, "Zeitdruck", 2, 1), _cat(12, "Donaudampfschifffahrtsgesellschaft", 3, 11)]
    monkeypatch.setattr(review, "tree", lambda p: ([(main, subs)], []))
    monkeypatch.setattr(review, "stats", lambda p, w: {1: {"n": 9}, 10: {"n": 4}, 11: {"n": 5}, 12: {"n": 5}})
    monkeypatch.setattr(review, "fuzzy", lambda p, w: [])
    with app.test_request_context():
        svg = review.map_svg(N(name="Kodierung der Interviews mit Pflegekräften",
                               question="Wie erleben Pflegekräfte die Vereinbarkeit von Schichtdienst und Familie?"), None, None)
    texts = re.findall(r'<text x="([\d.]+)" y="([\d.-]+)"[^>]*>([^<]*)</text>', svg)
    words = " ".join(t for _x, _y, t in texts).replace("- ", "")
    for name in (long_main, long_sub, "Donaudampfschifffahrtsgesellschaft", "Kodierung der Interviews mit Pflegekräften",
                 "Wie erleben Pflegekräfte die Vereinbarkeit von Schichtdienst und Familie?"):
        assert name in words, name                                                      # every word is there
    assert len([t for _x, _y, t in texts if t in long_sub]) >= 2                        # wrapped over lines
    # no two text lines of one column on the same height or closer than a line
    by_x = {}
    for x, y, t in texts:
        by_x.setdefault(x, []).append(float(y))
    for ys in by_x.values():
        ys.sort()
        assert all(b - a >= 13 for a, b in zip(ys, ys[1:])), ys
    # the white grounds of the rows do not overlap
    grounds = sorted((x, float(y), float(y) + float(h)) for x, y, h in
                     re.findall(r'<rect x="([\d.]+)" y="([\d.-]+)" width="270" height="([\d.]+)" rx="6" fill="#[0-9a-f]{6}"', svg))
    assert len(grounds) == 3 and all(a[2] <= b[1] for a, b in zip(grounds, grounds[1:]) if a[0] == b[0]), grounds


def test_wrap_measures_and_splits_words():
    assert review._wrap("Kurz", 146) == ["Kurz"]
    lines = review._wrap("x" * 80, 146)
    assert len(lines) > 1 and all(line.endswith("-") for line in lines[:-1]) and "".join(lines).replace("-", "") == "x" * 80
    assert review._wrap("", 146) == [""]


def test_switches_for_counts_and_pair_lines(app, client):
    """Ralf 2026-11-20: two ticks over the diagram leave out bars and numbers and the dashed lines of blurring pairs,
    on the page and in the SVG, PNG and PDF; without a choice in the address everything is shown as before."""
    code, pid, rid, base, main, bea = _setup(app, client)
    a, b = _sub(client, base, main, "Müdigkeit"), _sub(client, base, main, "Erschöpfung")
    _code(client, base, rid, "Abends", a, 29)
    _code(client, base, rid, "Abends", b, 29)                                     # the same passage: a blurring pair
    dashed, bar = 'stroke-dasharray="6 5"', 'fill="#378ccf"'
    full = client.get(f"{base}/map.svg").data.decode()
    assert dashed in full and bar in full and "Gestrichelt: unscharfes Paar" in full and "Balken und Zahl:" in full  # legend
    page = client.get(f"{base}?view=categories&show=diagram").data.decode()
    assert 'name="counts" value="1" data-submit-on-change checked' in page and 'name="pairs" value="1" data-submit-on-change checked' in page
    assert f"{base}/map.svg\"" in page                                             # all shown: plain download links
    page = client.get(f"{base}?view=categories&show=diagram&o=1&counts=1").data.decode()  # lines off
    assert dashed not in page and bar in page and "Gestrichelt:" not in page and "Balken und Zahl:" in page
    assert f"{base}/map.svg?o=1&amp;counts=1" in page and f"{base}/map.pdf?o=1&amp;counts=1" in page
    page = client.get(f"{base}?view=categories&show=diagram&o=1&pairs=1").data.decode()  # bars and numbers off
    assert dashed in page and bar not in page and 'fill="#efeee9"' not in page and "Balken und Zahl:" not in page
    assert "Gestrichelt: unscharfes Paar" in page
    bare = client.get(f"{base}/map.svg?o=1").data.decode()
    assert dashed not in bare and bar not in bare and "Gestrichelt" not in bare and "Balken und" not in bare
    assert client.get(f"{base}/map.png?o=1").data[:4] == b"\x89PNG"
    assert client.get(f"{base}/map.pdf?o=1&counts=1").data[:4] == b"%PDF"
    assert review.map_options({}) == {"counts": True, "pairs": True, "by": "", "direction": "h"}


def test_colour_by_a_characteristic_of_the_interviews(app, client):
    """Ralf 2026-11-20: "Färben nach" colours the diagram by a characteristic of the interviews, to see which
    categories only one group of interviewees has: a row of one group tinted in its colour, a row of several white with
    its dot split, the bar split by group, a main category of one group framed; a legend names the groups; the
    downloads follow the choice."""
    code, pid, rid, base, main, bea = _setup(app, client)
    rid2 = begin(open_link(app, code, add_interviewer(client, code, "Ben Zwei"))[0], code, mode="telefon")
    _transcript(app, rid2, TEXT)
    a, b, c = (_sub(client, base, main, n) for n in ("Nur vor Ort", "Nur am Telefon", "Beides"))
    _code(client, base, rid, "Abends", a, 29)
    _code(client, base, rid2, "Abends", b, 29)
    _code(client, base, rid, "Manchmal", c)
    _code(client, base, rid2, "Manchmal", c)
    svg = client.get(f"{base}/map.svg?by=form").data.decode()
    face, phone = review.GROUP_COLOURS[0], review.GROUP_COLOURS[1]           # in the order of the interview forms
    assert "Farben nach „Interviewform“ (Interview)" in svg and "nur Persönliches Interview" in svg
    assert "nur Telefonisches Interview" in svg and "Stellen aus beiden Gruppen" in svg
    assert f'fill="{review._tint(face, 0.18)}"' in svg and f'fill="{review._tint(phone, 0.18)}"' in svg
    assert " Z\" fill=\"" in svg                                                 # the split dot of "Beides"
    assert "Beides: Persönliches Interview und Telefonisches Interview" in svg   # said in words too, not only in colour
    assert f'fill="{face}"/>' in svg and f'fill="{phone}"/>' in svg              # the bar split by group
    assert "<title>Belastung: Persönliches Interview und Telefonisches Interview</title>" in svg  # main: both, no frame
    assert client.get(f"{base}/map.png?by=form").data[:4] == b"\x89PNG"
    assert client.get(f"{base}/map.pdf?o=1&by=form").data[:4] == b"%PDF"
    page = client.get(f"{base}?view=categories&show=diagram&o=1&counts=1&pairs=1&by=form").data.decode()
    assert '<option value="form" selected>' in page and '<option value="von" >' in page
    assert f"{base}/map.svg?by=form" in page                                       # the download takes the choice
    # without a choice: the colours of the categories, no groups
    plain = client.get(f"{base}/map.svg").data.decode()
    assert "Farben nach" not in plain and " Z\"" not in plain
    # who interviewed, as a second characteristic; an unknown key draws as without a choice
    assert "nur Ben Zwei" in client.get(f"{base}/map.svg?by=von").data.decode()
    assert "Farben nach" not in client.get(f"{base}/map.svg?by=x99").data.decode()
    # a person who sees only their own interviews sees the colours of those only (here: none of theirs)
    assert review.map_options({"by": "form"}) == {"counts": True, "pairs": True, "by": "form", "direction": "h"}
    assert review.map_query({"counts": True, "pairs": False, "by": "form"}) == {"o": "1", "counts": "1", "by": "form"}


def test_colour_of_a_category_by_hand_and_take_it_back(app, client):
    """Ralf 2026-11-20: a colour per category in "Bearbeiten"; what stands below with the old colour follows, an
    unknown colour is refused, "Rückgängig" gives both their colour back; the diagram tints the row."""
    code, pid, rid, base, main, bea = _setup(app, client)
    a = _sub(client, base, main, "Müdigkeit")
    red, green = "#B3261E", "#1F6B2A"

    def edit(cid, colour, name):
        return client.post(f"{base}/categories/{cid}", data={"name": name, "definition": "d", "delimitation": "", "colour": colour})

    edit(a, red, "Müdigkeit")
    a1 = _sub_under(client, base, a, "Abends müde")                              # takes the red over when made
    with app.app_context():
        assert db.session.get(CodingCategory, a1).colour == red
    page = client.get(f"{base}?view=categories").data.decode()
    assert f'name="colour" value="{red}" data-reload-saved checked' in page and "wie darüber" in page
    edit(a, green, "Müdigkeit")
    with app.app_context():
        assert db.session.get(CodingCategory, a).colour == green
        assert db.session.get(CodingCategory, a1).colour == ""                    # follows: as above
        assert tc.colour_of(db.session.get(CodingProject, pid), db.session.get(CodingCategory, a1)) == green
    assert f'fill="{review._tint(green, 0.12)}"' in client.get(f"{base}/map.svg").data.decode()
    client.post(f"{base}/undo")
    with app.app_context():
        assert db.session.get(CodingCategory, a).colour == red and db.session.get(CodingCategory, a1).colour == red
    edit(a, "#123456", "Müdigkeit")                                                # not one of the colours
    with app.app_context():
        assert db.session.get(CodingCategory, a).colour == red
    # a main category has no "wie darüber"; its frame takes its colour
    edit(main, green, "Belastung")
    assert f'stroke="{green}" stroke-width="2"' in client.get(f"{base}/map.svg").data.decode()


def test_the_drawing_as_a_file_runs_and_loads_nothing(app, client):
    """The SVG opened by its address is a document: it carries the policy that lets it neither run nor load anything,
    and texts in attributes are written with their quotes as entities."""
    from app import transcript_coding_review as review

    assert review.escape('a"b\'c<d&') == "a&#34;b&#39;c&lt;d&amp;"
    code, pid, rid, base, main, bea = _setup(app, client)
    r = client.get(f"{base}/map.svg")
    assert r.status_code == 200 and r.mimetype == "image/svg+xml"
    policy = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in policy and "sandbox" in policy and r.headers["X-Content-Type-Options"] == "nosniff"
