"""Interviews kodieren: moving categories (app/transcript_coding.py, move; Ralf 2026-10-28): "Verschieben nach …" and
drag & drop in the list post the same route; everything below goes along; not under itself or below, not deeper than
MAX_DEPTH, no second name at the new place; subcategories anyone moves, also under another main category; the main
level only the team while the main categories are fixed; one step in the history to take back; whoever has passages
in it answers "Passt" or takes them out to the old place."""
from app import transcript_coding as tc
from app.extensions import db
from app.models import CodingCategory, CodingEvent
from tests.interview.test_transcript_coding_depth import _chain
from tests.interview.test_transcript_coding_review import _code, _setup, _sub


def _parent(app, cid):
    with app.app_context():
        cat = db.session.get(CodingCategory, cid)
        return cat.parent_id, cat.kind


def _move(who, base, cid, parent, json=False):
    return who.post(f"{base}/categories/{cid}/move", data={"parent": "" if parent is None else str(parent), **({"json": "1"} if json else {})})


def test_move_rules_and_undo(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    client.post(f"{base}/categories/new", data={"kind": "main", "name": "Entlastung", "anyway": "1"})
    with app.app_context():
        other = db.session.scalar(db.select(CodingCategory.id).filter_by(name="Entlastung"))
    two, three = _chain(client, base, main, 2)
    leaf = _sub(bea, base, main, "Müdigkeit")
    # a member moves a subcategory under another main category and under a subcategory
    assert _move(bea, base, leaf, other).status_code == 302
    assert _parent(app, leaf) == (other, "sub")
    r = _move(bea, base, leaf, three, json=True)
    assert r.json == {"ok": True, "error": ""} and _parent(app, leaf) == (three, "sub")
    # with what stands below: "Ebene 2" under "Entlastung" takes "Ebene 3" and "Müdigkeit" along
    _move(client, base, two, other)
    with app.app_context():
        mains, _c = tc.tree(db.session.get(tc.CodingProject, pid))
        below = {m.name: [c.path for c in b] for m, b in mains}
    assert below["Entlastung"] == ["Entlastung › Ebene 2", "Entlastung › Ebene 2 › Ebene 3", "Entlastung › Ebene 2 › Ebene 3 › Müdigkeit"]
    # not under itself or below it, not deeper than the limit, not twice the same name
    assert not _move(client, base, two, leaf, json=True).json["ok"]
    deep = _chain(client, base, main, tc.MAX_DEPTH - 1, prefix="Tief")
    assert not _move(client, base, two, deep[-3], json=True).json["ok"]               # 3 levels would go too deep
    _sub(client, base, main, "Ebene 2")
    assert "gibt es" in _move(client, base, two, main, json=True).json["error"]
    # the main level: with the main categories fixed only the team
    client.post(f"{base}/settings", data={"name": "Seminar", "question": "", "visibility": "all", "mains_fixed": "1"})
    assert not _move(bea, base, three, None, json=True).json["ok"]
    assert not _move(bea, base, other, main, json=True).json["ok"]
    assert _move(bea, base, three, main, json=True).json["ok"]                       # a subcategory still
    assert _move(client, base, three, None, json=True).json["ok"] and _parent(app, three) == (None, "main")
    # the history and taking it back
    with app.app_context():
        event = db.session.scalar(db.select(CodingEvent).filter_by(project_id=pid, kind="moved").order_by(CodingEvent.id.desc()))
        assert event.data["before"]["parent"] == main and event.data["after"]["where"] == ""
    assert "macht „Ebene 3“ zur Hauptkategorie" in client.get(f"{base}/history").data.decode()
    client.post(f"{base}/undo")
    assert _parent(app, three) == (main, "sub")


def test_passages_go_along_and_can_be_taken_out(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    client.post(f"{base}/categories/new", data={"kind": "main", "name": "Entlastung", "anyway": "1"})
    with app.app_context():
        other = db.session.scalar(db.select(CodingCategory.id).filter_by(name="Entlastung"))
    tired = _sub(bea, base, main, "Müdigkeit")
    _code(bea, base, rid, "Abends", tired, 29)
    _move(client, base, tired, other)
    page = bea.get(f"{base}?view=categories").data.decode()
    assert "hat „Müdigkeit“ unter „Entlastung“ gehängt" in page and "Meine Stellen herauslösen" in page
    with app.app_context():
        event = db.session.scalar(db.select(CodingEvent).filter_by(project_id=pid, kind="moved"))
    bea.post(f"{base}/events/{event.id}/split_off")
    with app.app_context():
        back = db.session.scalars(db.select(CodingCategory).filter_by(project_id=pid, name="Müdigkeit")).all()
        assert sorted(c.parent_id for c in back) == sorted([main, other])               # one at the old place again
    assert "Meine Stellen herauslösen" not in bea.get(f"{base}?view=categories").data.decode()


def test_the_list_offers_dragging_and_move_to(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    sub = _sub(client, base, main, "Erschöpfung")
    page = client.get(f"{base}?view=categories").data.decode()
    assert f'data-tc-grab="{sub}"' in page and "data-tc-drop-main" in page and "Verschieben nach …" in page
    assert "— als Hauptkategorie —" in page
