"""The side of the coding view as a tree (feedback 2026-11-06): numbers as in the coding guide, "Meine" from the
author, and the passages of one category for a click on it, over the interviews the actor sees."""
import json
import re

from app.extensions import db
from app.models import CodingCategory
from tests.interview.test_interviewer import add, begin, open_link
from tests.interview.test_transcript_coding import _guide, _new, _transcript


def _data(page):
    return json.loads(re.search(r'<script type="application/json" id="tc-data">(.*?)</script>', page, re.S).group(1))


def test_numbers_mine_and_the_passages_of_a_category(app, client):
    code, sid = _guide(app, client)
    a, _ = open_link(app, code, add(client, code, "Anna Neu"))
    r1, r2 = begin(a, code), begin(a, code)
    text = "I: Wie war das?\nB: Anstrengend, sehr anstrengend.\nI: Und dann?\nB: Dann wurde es besser."
    _transcript(app, r1, text)
    _transcript(app, r2, text)
    pid = _new(client, code, start="empty")
    base = f"/guide/{code}/interviews/coding/{pid}"
    client.post(f"{base}/categories/new", data={"kind": "main", "name": "Belastung"})
    with app.app_context():
        main = db.session.scalar(db.select(CodingCategory.id).filter_by(project_id=pid, kind="main"))
    client.post(f"{base}/categories/new", data={"kind": "sub", "parent": main, "name": "Erschöpfung", "definition": "Müde"})
    with app.app_context():
        sub = db.session.scalar(db.select(CodingCategory.id).filter_by(project_id=pid, kind="sub"))
    start = text.index("Anstrengend")
    for rid in (r1, r2):
        assert client.post(f"{base}/i/{rid}/codings", json={"start": start, "end": start + 11, "cat": sub},
                           headers={"X-Requested-With": "fetch"}).json["ok"]
    cats = {c["id"]: c for c in _data(client.get(f"{base}/i/{r1}").data.decode())["categories"]}
    assert cats[main]["num"] == "1" and cats[sub]["num"] == "1.1"
    assert cats[main]["mine"] and cats[sub]["mine"]
    d = client.get(f"{base}/categories/{sub}/spots").json
    assert d["total"] == 2 and {s["rid"] for s in d["spots"]} == {r1, r2}
    assert all(f"at={start}-{start + 11}" in s["url"] for s in d["spots"]) and d["spots"][0]["where"].endswith("Pos. 2")
    assert d["spots"][0]["quote"] == "Anstrengend"
    # the list of the category system: number, name and "Meine" for its search and filter
    page = client.get(f"{base}?view=categories").data.decode()
    assert f'data-tc-num="1.1" data-tc-name="erschöpfung" data-tc-mine' in page
    assert "data-tc-list-search" in page
    # someone else's categories are not "Meine"; nobody outside the analysis reads its passages
    g, _ = open_link(app, code, add(client, code, "Gruppe 1", has_interview="1", may_interview="", may_code="all"))
    cats = {c["id"]: c for c in _data(g.get(f"{base}/i/{r1}").data.decode())["categories"]}
    assert not cats[sub]["mine"]
    assert g.get(f"{base}/categories/{sub}/spots").json["total"] == 2
    assert app.test_client().get(f"{base}/categories/{sub}/spots").status_code in (302, 401, 404)


def test_pickers_carry_the_tree(app, client):
    """Merging and moving choose from the tree (tc_picker.js): each option with number, level, path and passages."""
    code, sid = _guide(app, client)
    pid = _new(client, code, start="empty")
    base = f"/guide/{code}/interviews/coding/{pid}"
    for name in ("Belastung", "Wünsche"):
        client.post(f"{base}/categories/new", data={"kind": "main", "name": name})
    with app.app_context():
        main = db.session.scalar(db.select(CodingCategory.id).filter_by(project_id=pid, name="Belastung"))
    client.post(f"{base}/categories/new", data={"kind": "sub", "parent": main, "name": "Erschöpfung", "definition": "Müde"})
    overview = client.get(f"{base}/overview").data.decode()
    assert "data-tc-merge-dialog" in overview and "tc_picker.js" in overview
    assert re.search(r'<option value="\d+" data-num="1.1" data-level="2" data-path="Belastung › Erschöpfung" data-n="0"', overview)
    page = client.get(f"{base}?view=categories").data.decode()
    assert '<select name="parent" data-tc-pick>' in page and "tc_picker.js" in page
    assert re.search(r'<option value="\d+" data-num="2" data-level="1" data-main="1" data-path="Wünsche">Wünsche</option>', page)
