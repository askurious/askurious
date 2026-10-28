"""Interviews kodieren with deeper category systems (Ralf 2026-10-28: 3, 4, 5, 6 levels below a main category or
more; app/transcript_coding.py MAX_DEPTH): subcategories under subcategories down to MAX_DEPTH levels and not
deeper, the tree with level and path, the numbers counting everything below, the coding view's list with the path,
the map, .qdc nested, the coding guide numbered 1.1.1, the case comparison by main category, merging not into
something below itself, copying a deep system, and an existing two-level system unchanged."""
import io
import xml.etree.ElementTree as ET

from app import transcript_coding as tc
from app.extensions import db
from app.models import CodingCategory, CodingProject
from tests.interview.test_transcript_coding_review import _code, _setup, _sub


def _chain(client, base, main, n, prefix="Ebene"):
    """n subcategories each under the one before: [ids], the first right under `main`."""
    ids, parent = [], main
    for i in range(2, n + 2):
        parent = _sub(client, base, parent, f"{prefix} {i}")
        ids.append(parent)
    return ids


def test_levels_down_to_the_limit_and_not_deeper(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    ids = _chain(client, base, main, tc.MAX_DEPTH - 1)                              # levels 2 … MAX_DEPTH
    r = client.post(f"{base}/categories/new", data={"kind": "sub", "parent": ids[-1], "name": "Zu tief", "definition": "d", "anyway": "1"})
    page = client.get(r.headers.get("Location", base) if r.status_code == 302 else base).data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(CodingCategory.id).filter_by(name="Zu tief")) is None
        project = db.session.get(CodingProject, pid)
        mains, _cands = tc.tree(project)
        below = mains[0][1]
        assert [c.level for c in below] == list(range(2, tc.MAX_DEPTH + 1))
        assert below[-1].path.startswith("Belastung › Ebene 2 › Ebene 3") and below[-1].rel.startswith("Ebene 2 › ")
        assert below[0].anc == [main] and below[-1].anc[-1] == ids[-2]
    assert "Tiefer als" in page or r.status_code in (400, 302)
    # the list: indented, foldable, "+ Unterkategorie" only where it may go deeper
    page = client.get(f"{base}?view=categories").data.decode()
    assert page.count('data-tc-fold="') == tc.MAX_DEPTH - 1                          # every one with something below
    assert f'data-parent="{ids[-1]}"' not in page and f'data-parent="{ids[-2]}"' in page
    assert f'data-tc-anc="{main} {ids[0]}"' in page


def test_numbers_view_map_exports_and_cases_on_every_level(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    two, three, four = _chain(client, base, main, 3)
    _code(client, base, rid, "Abends", four, 29)
    _code(client, base, rid, "Manchmal", two)
    with app.app_context():
        project = db.session.get(CodingProject, pid)
        from app import transcript_coding_review as review, transcript_coding_export as export
        from app.models import Survey, User

        who = tc._user_actor("team", db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X")))
        st = review.stats(project, who)
        assert (st[main]["n"], st[two]["n"], st[three]["n"], st[four]["n"]) == (2, 2, 1, 1)
        assert (st[two]["own"], st[three]["own"]) == (1, 0)
        svg = review.map_svg(project, who, db.session.get(Survey, project.survey_id))
        assert "Ebene 4" in svg and svg.startswith("<svg")
        root = ET.fromstring(export.build_qdc(project))
        ns = {"q": export.CODEBOOK_NS}
        deep = root.find(".//q:Code[@name='Belastung']/q:Code[@name='Ebene 2']/q:Code[@name='Ebene 3']/q:Code[@name='Ebene 4']", ns)
        assert deep is not None
    # the coding view's list: the path of the one above, the level
    page = client.get(f"{base}/i/{rid}").data.decode()
    assert r'"parent": "Belastung \u203a Ebene 2 \u203a Ebene 3"' in page and '"level": 4' in page   # JSON escapes ›
    # the overview: indented, "selbst" for one with something below
    page = client.get(f"{base}/overview").data.decode()
    assert "--lvl: 2" in page and "selbst 1" in page
    # the coding guide: numbered down the levels
    docx = client.get(f"{base}/kodierleitfaden.docx").data
    from docx import Document

    heads = [p.text for p in Document(io.BytesIO(docx)).paragraphs if p.style.name.startswith("Heading")]
    assert "1 Belastung" in heads and "1.1 Ebene 2" in heads and "1.1.1.1 Ebene 4" in heads
    # the case comparison: in the cell of the main category, with the path below it
    page = client.get(f"/guide/{code}/interviews/summaries?auswertung={pid}").data.decode()
    assert "Ebene 2 › Ebene 3 › Ebene 4" in page
    assert client.get(f"{base}/agreement").status_code == 200                       # with the sums below


def test_merging_not_into_below_and_deep_copy(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    two, three = _chain(client, base, main, 2)
    r = client.post(f"{base}/merge", data={"source": two, "target": three, "reason": "gehört zusammen"})
    with app.app_context():
        assert db.session.get(CodingCategory, two).merged_into is None              # not into something below itself
    other = _sub(client, base, main, "Andere")
    client.post(f"{base}/merge", data={"source": two, "target": other, "reason": "gehört zusammen"})
    with app.app_context():
        assert db.session.get(CodingCategory, two).merged_into == other
        assert db.session.get(CodingCategory, three).parent_id == other              # what stood below goes along
    copy = client.post(f"/guide/{code}/interviews/coding/new", data={"name": "Kopie", "start": "copy", "copy_of": pid})
    assert copy.status_code == 302
    with app.app_context():
        new = db.session.scalar(db.select(CodingProject).filter_by(name="Kopie"))
        mains, _c = tc.tree(new)
        assert [(c.name, c.level) for c in mains[0][1]] == [("Andere", 2), ("Ebene 3", 3)]
    assert r.status_code in (302, 200)


def test_two_levels_as_before(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    sub = _sub(client, base, main, "Erschöpfung")
    _code(client, base, rid, "Abends", sub, 29)
    page = client.get(f"{base}?view=categories").data.decode()
    assert 'data-tc-fold="' in page and "--lvl: 0" in page                          # the main category folds its one
    assert '"parent": "Belastung"' in client.get(f"{base}/i/{rid}").data.decode()
