"""Interviews kodieren: protecting the category system (Ralf 2026-10-29). The setting "Fremde Kategorien verschieben
und zusammenführen nur Team und Autor" turns moving and merging someone else's subcategory into a proposal that the
team or the author takes on; "Zurücknehmen" in the history takes a move back for the team, the author and the mover,
with a confirmation when the category was changed since."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.extensions import db
from app.models import CodingCategory, CodingEvent, CodingProject
from tests.interview.test_transcript_coding import _person
from tests.interview.test_transcript_coding_review import _setup, _sub

NODE = shutil.which("node")


def _guard(client, base, on=True):
    data = {"name": "Seminar", "question": "", "visibility": "all", "has_guarded": "1"}
    if on:
        data["guarded"] = "1"
    assert client.post(f"{base}/settings", data=data).status_code == 302


def _cat(app, cid):
    with app.app_context():
        c = db.session.get(CodingCategory, cid)
        return {"parent": c.parent_id, "merged": c.merged_into}


def _open(app, pid):
    with app.app_context():
        return [(e.id, e.category_id, e.data) for e in db.session.scalars(
            db.select(CodingEvent).filter_by(project_id=pid, kind="proposal", status="open"))]


def test_setting_turns_moving_and_merging_into_proposals(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    team_a = _sub(client, base, main, "Erschöpfung")
    team_b = _sub(client, base, main, "Müdigkeit")
    beas = _sub(bea, base, main, "Schlaf")
    # off (the default): every member moves subcategories directly, as before
    bea.post(f"{base}/categories/{team_a}/move", data={"parent": str(team_b)})
    assert _cat(app, team_a)["parent"] == team_b
    client.post(f"{base}/categories/{team_a}/move", data={"parent": str(main)})
    _guard(client, base)
    with app.app_context():
        assert db.session.get(CodingProject, pid).settings["guarded"] is True
    # on: someone else's subcategory → a proposal, nothing moved
    r = bea.post(f"{base}/categories/{team_a}/move", data={"parent": str(team_b), "reason": "gehört dazu", "json": "1"})
    assert r.json == {"ok": True, "error": "", "proposed": True}
    assert _cat(app, team_a)["parent"] == main
    (eid, cat_id, data), = _open(app, pid)
    assert cat_id == team_a and data["move"]["parent"] == team_b
    assert "unter „Belastung › Müdigkeit“ hängen" in client.get(f"{base}?view=categories").data.decode()
    # one's own subcategory still moves directly
    bea.post(f"{base}/categories/{beas}/move", data={"parent": str(team_b)})
    assert _cat(app, beas)["parent"] == team_b
    # the team takes the proposal on: moved
    client.post(f"{base}/proposals/{eid}/accept")
    assert _cat(app, team_a)["parent"] == team_b
    # merging someone else's subcategory: a proposal as well, with its reason; the team takes it on
    client.post(f"{base}/categories/{team_a}/move", data={"parent": str(main)})
    bea.post(f"{base}/merge", data={"source": team_a, "target": team_b, "reason": "dasselbe", "back": base})
    assert _cat(app, team_a)["merged"] is None
    (eid, cat_id, data), = _open(app, pid)
    assert data["merge"]["target"] == team_b
    client.post(f"{base}/proposals/{eid}/accept")
    assert _cat(app, team_a)["merged"] == team_b
    # the drop dialog knows it: someone else's category is proposed
    page = bea.get(f"{base}?view=categories").data.decode()
    drop = json.loads(page.split('id="tc-drop-data">')[1].split("</script>")[0])
    assert drop["guarded"] and drop["cats"][str(team_b)]["propose"] and not drop["cats"][str(beas)]["propose"]
    assert "Fremde Kategorien verschieben und zusammenführen nur Team und Autor" in client.get(f"{base}?view=settings").data.decode()


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_drop_rules_say_propose_with_node():
    cats = {"1": {"id": 1, "kind": "main", "level": 1, "anc": [], "parent": None, "move": False},
            "2": {"id": 2, "kind": "sub", "level": 2, "anc": [1], "parent": 1, "move": True, "propose": True, "author": False},
            "3": {"id": 3, "kind": "sub", "level": 2, "anc": [1], "parent": 1, "move": True, "propose": False, "author": True},
            "4": {"id": 4, "kind": "sub", "level": 2, "anc": [1], "parent": 1, "move": True, "propose": False, "author": True}}
    data = {"cats": cats, "team": False, "fixed": False, "max": 8, "may_main": True, "guarded": True}
    script = Path(__file__).parents[1] / "transcript_coding_drop_cases.js"
    calls = [["hang", "2", "3"], ["hang", "3", "4"], ["merge", "2", "3"], ["merge", "3", "4"]]
    out = subprocess.run([NODE, str(script)], input=json.dumps({"data": data, "calls": calls}), capture_output=True,
                         text=True, timeout=60, check=True)
    hang_other, hang_own, merge_other, merge_own = json.loads(out.stdout)
    assert hang_other["ok"] and hang_other["propose"] and hang_own["ok"] and not hang_own.get("propose")
    assert merge_other["ok"] and merge_other["propose"] and merge_own["ok"] and not merge_own.get("propose")


def test_taking_a_move_back_from_the_history(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    team_a = _sub(client, base, main, "Erschöpfung")
    team_b = _sub(client, base, main, "Müdigkeit")
    carl, _carl_id = _person(app, "0000-0001-5109-3700", "Carl")
    client.post(f"{base}/members", data={"kind": "person", "person": "0000-0001-5109-3700"})
    bea.post(f"{base}/categories/{team_a}/move", data={"parent": str(team_b)})       # Bea moves the team's category
    with app.app_context():
        moved = db.session.scalar(db.select(CodingEvent.id).filter_by(project_id=pid, kind="moved"))
    assert "Zurücknehmen" in client.get(f"{base}/overview").data.decode()
    assert carl.post(f"{base}/events/{moved}/undo").status_code == 403              # neither team, author nor mover
    # changed since: first a question, then with the confirmation
    client.post(f"{base}/categories/{team_a}", data={"name": "Erschöpfung", "definition": "neu", "delimitation": ""})
    page = client.get(f"{base}/overview").data.decode()
    assert "Die Kategorie wurde seither weiter geändert" in page                     # the warning of the dialog
    client.post(f"{base}/events/{moved}/undo")
    assert _cat(app, team_a)["parent"] == team_b                                     # not without confirming
    client.post(f"{base}/events/{moved}/undo", data={"force": "1"})
    assert _cat(app, team_a)["parent"] == main
    with app.app_context():
        assert db.session.get(CodingEvent, moved).data["undone"]
        assert db.session.get(CodingCategory, team_a).definition == "neu"             # later changes stay
