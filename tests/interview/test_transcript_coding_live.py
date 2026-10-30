"""Interviews kodieren (Ralf 2026-10-30): "Einordnen nach …" of a new category (app/transcript_coding.py, _place) and
the pages that bring themselves up to date (app/transcript_coding_live.py): the version answers 204 without a body
while nothing changed, with the same checks as the pages, and stays blind in blind double coding."""
import json
import re

from app import transcript_coding as tc
from app import transcript_coding_live as live
from app.extensions import db
from app.models import CodingCategory, CodingProject, InterviewerAccess
from tests.interview.test_interviewer import add as add_interviewer
from tests.interview.test_interviewer import begin, open_link
from tests.interview.test_transcript_coding import _person, _transcript
from tests.interview.test_transcript_coding_double import _assign
from tests.interview.test_transcript_coding_review import BEA, _code, _setup, _sub


def _order(app, pid, parent_id, sub=True):
    with app.app_context():
        return [c.name for c in tc.siblings_of(db.session.get(CodingProject, pid), parent_id, sub)]


def _new(who, base, main, name, after=None):
    data = {"kind": "sub", "parent": main, "name": name, "definition": "d", "anyway": "1"}
    if after is not None:
        data["after"] = str(after)
    assert who.post(f"{base}/categories/new", data=data).status_code == 302
    with who.application.app_context():
        return db.session.scalar(db.select(CodingCategory.id).filter_by(name=name))


def test_new_category_at_the_start_in_the_middle_and_at_the_end(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    a = _new(client, base, main, "A")
    _new(client, base, main, "B")                                                  # no choice: at the end, as before
    assert _order(app, pid, main) == ["A", "B"]
    _new(client, base, main, "Anfang", after="start")
    _new(client, base, main, "Mitte", after=a)                                     # directly after A
    _new(bea, base, main, "Ende", after="")                                        # "am Ende"
    assert _order(app, pid, main) == ["Anfang", "A", "Mitte", "B", "Ende"]
    # the pages show the same order: the list and the coding view
    page = client.get(f"{base}?view=categories").data.decode()
    assert [page.index(f">{n}</strong>") for n in ("Anfang", "A", "Mitte", "B", "Ende")] == sorted(
        page.index(f">{n}</strong>") for n in ("Anfang", "A", "Mitte", "B", "Ende"))
    names = [c["name"] for c in json.loads(re.search(r'id="tc-data">(.*?)</script>', client.get(f"{base}/i/{rid}").data.decode(), re.S).group(1))["categories"]]
    assert names[1:6] == ["Anfang", "A", "Mitte", "B", "Ende"]
    # the dialog knows the siblings of every level, and each category has "Danach" with itself as the preset
    siblings = json.loads(re.search(r'id="tc-siblings">(.*?)</script>', page, re.S).group(1))
    assert [n for _i, n in siblings[str(main)]] == ["Anfang", "A", "Mitte", "B", "Ende"]
    assert [n for _i, n in siblings["main"]] == ["Belastung"]
    assert f'data-after="{a}"' in page and 'name="after" data-tc-after' in page


def test_other_level_main_categories_and_undo(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    a = _new(client, base, main, "A")
    b = _new(client, base, main, "B")
    deep = _new(client, base, a, "Unter A")
    # an id of another level is no place among these siblings: at the end
    _new(client, base, main, "Fremd", after=deep)
    _new(client, base, a, "Auch fremd", after=b)
    assert _order(app, pid, main) == ["A", "B", "Fremd"] and _order(app, pid, a) == ["Unter A", "Auch fremd"]
    # main categories: at the start among them, also from the coding view (JSON), which answers with the order
    client.post(f"{base}/categories/new", data={"kind": "main", "name": "Zweite", "anyway": "1"})
    r = client.post(f"{base}/categories/quick", json={"kind": "main", "name": "Erste", "anyway": "1", "after": "start"})
    assert r.json["ok"] and [c["name"] for c in r.json["categories"] if c["kind"] == "main"] == ["Erste", "Belastung", "Zweite"]
    r = bea.post(f"{base}/categories/quick", json={"kind": "sub", "parent": main, "name": "Neu", "definition": "d", "anyway": "1", "after": a})
    assert r.json["ok"] and _order(app, pid, main) == ["A", "Neu", "B", "Fremd"]
    # "Rückgängig" takes it out again; the others keep their order
    bea.post(f"{base}/undo")
    assert _order(app, pid, main) == ["A", "B", "Fremd"]
    with app.app_context():
        assert db.session.get(CodingCategory, r.json["category"]["id"]).removed_at is not None
    client.post(f"{base}/undo")                                                    # "Erste" goes again
    assert _order(app, pid, None, sub=False) == ["Belastung", "Zweite"]


def _changes(who, base, version="", rid=None):
    return who.get(f"{base}/changes?v={version}" + (f"&rid={rid}" if rid else ""))


def _version(page):
    return re.search(r'data-tc-version="([0-9a-f]+)"', page).group(1)


def test_changes_small_without_a_change_and_new_after_one(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    page = client.get(f"{base}?view=categories").data.decode()
    assert 'data-tc-every="5"' in page and "transcript_coding_live.js" in page and 'data-tc-live="categories"' in page
    v = _version(page)
    r = _changes(client, base, v)
    assert r.status_code == 204 and r.data == b"" and r.headers["Cache-Control"] == "no-store"
    r = _changes(client, base, "old")
    assert r.status_code == 200 and r.json == {"v": v}
    # someone else adds a category: a new version, the page shows it
    _sub(bea, base, main, "Erschöpfung")
    r = _changes(client, base, v)
    assert r.status_code == 200 and r.json["v"] != v
    assert "Erschöpfung" in client.get(f"{base}?view=categories").data.decode()
    # the coding view: its data along with the version, a coding of someone else shows
    with app.app_context():
        cat = db.session.scalar(db.select(CodingCategory.id).filter_by(project_id=pid, name="Erschöpfung"))
    view = client.get(f"{base}/i/{rid}").data.decode()
    vi = _version(view)
    assert _changes(client, base, vi, rid).status_code == 204
    _code(bea, base, rid, "Abends", cat)
    r = _changes(client, base, vi, rid)
    assert r.status_code == 200 and [c["cat"] for c in r.json["data"]["codings"]] == [cat]
    assert r.json["data"]["categories"][1]["n"] == 1
    # the overview has it too
    assert "data-tc-poll" in client.get(f"{base}/overview").data.decode()
    assert "data-tc-poll" not in client.get(f"{base}?view=settings").data.decode()


def test_changes_have_the_rights_of_the_pages(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    outsider, _id = _person(app, "0000-0002-1694-233X", "Olga")
    assert _changes(outsider, base).status_code == 404                              # not a member of the analysis
    assert _changes(app.test_client(), base).status_code in (302, 401, 404)        # not signed in
    assert _changes(bea, base, rid=999999).status_code == 404                       # no such interview
    # an interviewer link without an account, with the right to code its own: its own interview, not another
    c, _ = open_link(app, code, add_interviewer(client, code, "Anna Becker", may_code="own"))
    own = begin(c, code)
    _transcript(app, own)
    with app.app_context():
        aid = db.session.scalar(db.select(InterviewerAccess.id).filter_by(name="Anna Becker"))
    client.post(f"{base}/interviewers", data={"access": str(aid)})
    assert _changes(c, base).status_code == 200
    assert _changes(c, base, rid=own).status_code == 200
    assert _changes(c, base, rid=rid).status_code == 404                            # Anna Neu's interview


def test_changes_stay_blind_in_double_coding(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    cat = _sub(client, base, main, "Erschöpfung")
    with app.app_context():
        from app.models import User

        bea_id = db.session.scalar(db.select(User).filter_by(orcid=BEA)).id
    carl, carl_id = _person(app, "0000-0001-5109-3700", "Carl")
    client.post(f"{base}/members", data={"kind": "person", "person": "0000-0001-5109-3700"})
    _assign(app, client, base, rid, [f"u:{bea_id}", f"u:{carl_id}"])
    v = _version(bea.get(f"{base}/i/{rid}").data.decode())
    vp = _version(bea.get(f"{base}?view=categories").data.decode())
    _code(carl, base, rid, "Abends", cat)                                           # Carl codes, Bea is blind
    assert _changes(bea, base, v, rid).status_code == 204
    assert _changes(bea, base, vp).status_code == 204
    team = _version(client.get(f"{base}/i/{rid}").data.decode())
    _code(carl, base, rid, "Manchmal", cat)
    r = _changes(client, base, team, rid)                                           # the team sees everything
    assert r.status_code == 200 and len(r.json["data"]["codings"]) == 2
    _code(bea, base, rid, "Manchmal", cat)                                          # her own passage: new for her
    r = _changes(bea, base, v, rid)
    assert r.status_code == 200 and len(r.json["data"]["codings"]) == 1


def test_version_is_kept_under_the_same_stamp(app, client, monkeypatch):
    """The expensive part runs once per change, not with every question."""
    code, pid, rid, base, main, bea = _setup(app, client)
    calls = []
    real = live._digest
    monkeypatch.setattr(live, "_digest", lambda *a, **k: calls.append(1) or real(*a, **k))
    v = _version(client.get(f"{base}?view=categories").data.decode())
    for _ in range(3):
        assert _changes(client, base, v).status_code == 204
    assert len(calls) == 1
    _sub(client, base, main, "Neu")
    assert _changes(client, base, v).status_code == 200 and len(calls) == 2
