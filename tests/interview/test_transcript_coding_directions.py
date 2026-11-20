"""Interviews kodieren: the diagram of the category system across, from the top in columns or as a tree from the top
(Ralf 2026-11-20), on the page and in the downloads SVG, PNG and PDF; the choice is kept per person and analysis, and
the Word coding guide takes across or columns, whichever comes out larger on the page."""
import io
import re
import xml.etree.ElementTree as ET

from PIL import Image

from app.extensions import db
from app.models import CodingCategory, CodingKey
from tests.interview.test_transcript_coding_review import _code, _setup, _sub, _sub_under

NS = "{http://www.w3.org/2000/svg}"


def _mains(app, client, base, pid, names):
    out = []
    for name in names:
        client.post(f"{base}/categories/new", data={"kind": "main", "name": name, "anyway": "1"})
        with app.app_context():
            out.append(db.session.scalar(db.select(CodingCategory.id).filter_by(project_id=pid, name=name)))
    return out


def _svg(client, base, direction, extra=""):
    r = client.get(f"{base}/map.svg?o=1&counts=1&pairs=1&dir={direction}{extra}")
    assert r.status_code == 200
    return r.data.decode()


def _texts(svg):
    return " ".join(t.text or "" for t in ET.fromstring(svg).iter(f"{NS}text"))


def test_every_direction_draws_every_category_and_exports(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(client, base, main, "Erschöpfung")
    _sub_under(client, base, tired, "Abendliche Müdigkeit nach langen Schichten im Wechseldienst")
    _code(client, base, rid, "Abends", tired, 29)
    _mains(app, client, base, pid, ["Studium"])
    sizes = {}
    for direction in ("h", "v", "t"):
        svg = _svg(client, base, direction)
        text = _texts(svg)
        for name in ("Belastung", "Erschöpfung", "Abendliche", "Studium"):
            assert name in text, (direction, name)
        # lines only of what app/svg_png.py draws for PNG, PDF and Word: M, L and C
        assert all(set(re.sub(r"[-\d., ]", "", d)) <= set("MLCZ") for d in re.findall(r' d="([^"]*)"', svg)), direction
        root = ET.fromstring(svg)
        sizes[direction] = (float(root.get("width")), float(root.get("height")))
        png = client.get(f"{base}/map.png?o=1&counts=1&dir={direction}")
        assert png.status_code == 200 and Image.open(io.BytesIO(png.data)).size[0] > 400
        pdf = client.get(f"{base}/map.pdf?o=1&counts=1&dir={direction}")
        assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    across, columns, tree = sizes["h"], sizes["v"], sizes["t"]
    assert columns[0] < across[0]                    # from the top it grows with the main categories, not the levels
    assert tree[1] > tree[0] / 4 and columns[1] > 200
    # an unknown direction is drawn across, as before
    assert _svg(client, base, "x") == _svg(client, base, "h")


def test_columns_begin_a_new_row_after_five_main_categories(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    mains = _mains(app, client, base, pid, [f"Haupt {n}" for n in range(2, 8)])   # seven with Belastung
    for m in mains:
        _sub(client, base, m, f"Unter {m}")
    columns = ET.fromstring(_svg(client, base, "v"))
    tree = ET.fromstring(_svg(client, base, "t"))
    assert float(columns.get("width")) < 1700                       # five columns at most side by side
    assert float(tree.get("width")) > 7 * 280                       # the tree: every category without subs side by side
    heads = [r for r in columns.iter(f"{NS}rect") if r.get("rx") == "8"]
    assert len({r.get("y") for r in heads}) == 2                    # two rows of main categories


def test_the_page_offers_the_switch_and_keeps_the_choice_per_person(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    sub = _sub(client, base, main, "Erschöpfung")
    page = client.get(f"{base}?view=categories&show=diagram").data.decode()
    assert 'name="dir" value="h"' in page and 'value="v"' in page and 'value="t"' in page
    assert re.search(r'value="h" data-submit-on-change\s+checked', page)          # across until chosen otherwise
    page = client.get(f"{base}?view=categories&show=diagram&o=1&counts=1&dir=t").data.decode()
    assert "dir=t" in page and f'data-tc-cat="{sub}"' in page and "data-tc-drop-main" in page   # click and drag stay
    # the next visit without the address: still the tree, and the downloads take it
    page = client.get(f"{base}?view=categories&show=diagram").data.decode()
    assert re.search(r'value="t" data-submit-on-change\s+checked', page)
    assert re.search(r'map\.png\?[^"]*dir=t', page) and "pairs=1" not in page.split("map.png", 1)[1].split('"', 1)[0]
    with app.app_context():
        view = db.session.scalar(db.select(CodingKey.view).filter_by(project_id=pid))
    assert view == {"counts": True, "pairs": False, "by": "", "direction": "t"}
    # another person in the analysis keeps their own choice
    other = bea.get(f"{base}?view=categories&show=diagram").data.decode()
    assert re.search(r'value="h" data-submit-on-change\s+checked', other)


def test_the_coding_guide_takes_the_larger_of_across_and_columns(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    _sub(client, base, main, "Erschöpfung")
    from app import transcript_coding_review as tcr

    picked = []
    real = tcr.map_png

    def spy(*args, **kwargs):
        picked.append(kwargs.get("direction"))
        return real(*args, **kwargs)

    tcr.map_png = spy
    try:
        assert client.get(f"{base}/kodierleitfaden.docx").status_code == 200
    finally:
        tcr.map_png = real
    assert picked == ["h", "v"]                                       # both measured, the larger one in the guide
