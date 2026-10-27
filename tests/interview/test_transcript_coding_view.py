"""Interviews kodieren, stage 2 (app/transcript_coding_view.py): marking on word boundaries, assigning, re-finding a
passage after a correction of the transcript, "nicht mehr zuzuordnen", removing, the anchor example, a new category
from the coding view, who may code which interview, and that codings and anchors go with the interview."""
from app import transcript_coding_view as view
from app.extensions import db
from app.models import CodingCategory, InterviewerAccess, Response
from tests.interview.test_interviewer import add as add_interviewer
from tests.interview.test_interviewer import begin, open_link
from tests.interview.test_transcript_coding import BEA, _cats, _guide, _new, _person, _snapshot, _transcript

TEXT = "I: Wie war der Tag?\nB: Abends bin ich einfach fertig. Manchmal frage ich mich, wie lange ich das noch schaffe."


def test_snap_to_whole_words():
    assert TEXT[slice(*view.snap(TEXT, TEXT.index("bends"), TEXT.index("fertig") + 3))] == "Abends bin ich einfach fertig"
    assert view.snap(TEXT, 5, 5) is None
    assert view.snap(TEXT, 2, 3) is None                                          # only a space
    s, e = view.snap(TEXT, TEXT.index(" Manchmal"), TEXT.index("Manchmal") + 8)
    assert TEXT[s:e] == "Manchmal"                                                # spaces at the ends trimmed


def test_place_after_a_correction():
    start = TEXT.index("Abends")
    coding = {"start": start, "end": start + 29, "quote": "Abends bin ich einfach fertig"}
    assert view.place(TEXT, coding) == (start, start + 29)
    shifted = "[Vorgespräch]\n" + TEXT
    assert shifted[slice(*view.place(shifted, coding))] == "Abends bin ich einfach fertig"
    spaced = TEXT.replace("bin ich", "bin\nich")
    assert spaced[slice(*view.place(spaced, coding))] == "Abends bin\nich einfach fertig"
    assert view.place(TEXT.replace("einfach fertig", "müde"), coding) is None    # the wording is gone
    twice = TEXT + " Abends bin ich einfach fertig."
    assert view.place(twice, coding)[0] == start                                  # the occurrence nearest the old place


def _setup(app, client):
    code, sid = _guide(app, client)
    c, _ = open_link(app, code, add_interviewer(client, code, "Anna"))
    rid = begin(c, code)
    other = begin(open_link(app, code, add_interviewer(client, code, "Bert"))[0], code)
    _transcript(app, rid, TEXT)
    _transcript(app, other, TEXT)
    pid = _new(client, code)
    return code, pid, rid, other, c


def test_code_remove_and_anchor(app, client):
    code, pid, rid, other, anna = _setup(app, client)
    base = f"/guide/{code}/interviews/coding/{pid}/i/{rid}"
    page = client.get(base)
    assert page.status_code == 200 and "tc-data" in page.data.decode()
    sub = _cats(app, pid, "sub")[0][0]
    r = client.post(f"{base}/codings", json={"start": TEXT.index("bends"), "end": TEXT.index("fertig") + 2, "cat": sub})
    assert r.json["ok"] and r.json["coding"]["quote"] == "Abends bin ich einfach fertig"
    cid = r.json["coding"]["id"]
    assert not client.post(f"{base}/codings", json={"start": TEXT.index("Abends"), "end": TEXT.index("fertig") + 6, "cat": sub}).json["ok"]
    with app.app_context():
        stored = db.session.get(Response, rid).interview["codings"]
        assert stored[0]["project"] == pid and stored[0]["cat"] == sub and stored[0]["by"].startswith("u:")
    # a category in use cannot be removed (merging comes with stage 3)
    client.post(f"/guide/{code}/interviews/coding/{pid}/categories/{sub}/remove")
    assert sub in [c[0] for c in _cats(app, pid, "sub")]
    # the anchor example, shown on the page of the analysis
    assert client.post(f"{base}/codings/{cid}/anchor").json["ok"]
    assert "Abends bin ich einfach fertig" in client.get(f"/guide/{code}/interviews/coding/{pid}").data.decode()
    # another member may neither remove it nor choose the anchor of a category not theirs
    bea, _ = _person(app)
    client.post(f"/guide/{code}/interviews/coding/{pid}/members", data={"kind": "person", "person": BEA})
    assert bea.post(f"{base}/codings/{cid}/delete").status_code == 403
    assert bea.post(f"{base}/codings/{cid}/anchor").status_code == 403
    assert client.post(f"{base}/codings/{cid}/delete").json["ok"]
    with app.app_context():
        assert db.session.get(Response, rid).interview["codings"] == []
        assert db.session.get(CodingCategory, sub).anchor is None


def test_lost_after_the_wording_is_gone(app, client):
    code, pid, rid, other, anna = _setup(app, client)
    base = f"/guide/{code}/interviews/coding/{pid}/i/{rid}"
    sub = _cats(app, pid, "sub")[0][0]
    client.post(f"{base}/codings", json={"start": TEXT.index("Abends"), "end": TEXT.index("fertig") + 6, "cat": sub})
    _transcript(app, rid, "[Vorgespräch]\n" + TEXT)
    page = client.get(base).data.decode()
    assert "Nicht mehr zuzuordnen" not in page                                    # found again
    _transcript(app, rid, TEXT.replace("einfach fertig", "müde"))
    assert "Nicht mehr zuzuordnen" in client.get(base).data.decode()


def test_who_codes_which_interview(app, client):
    code, pid, rid, other, anna = _setup(app, client)
    before = _snapshot(app)
    with app.app_context():
        aid = db.session.scalar(db.select(InterviewerAccess.id).filter_by(name="Anna"))
    sub = _cats(app, pid, "sub")[0][0]
    own, theirs = f"/guide/{code}/interviews/coding/{pid}/i/{rid}", f"/guide/{code}/interviews/coding/{pid}/i/{other}"
    assert anna.get(own).status_code == 404                                       # without the tick
    client.post(f"/guide/{code}/interviews/coding/{pid}/interviewers", data={"access": str(aid)})
    _transcript(app, rid, TEXT, checked=True)
    assert anna.get(own).status_code == 200 and anna.get(theirs).status_code == 404
    assert anna.post(f"{theirs}/codings", json={"start": 0, "end": 5, "cat": sub}).status_code == 404
    assert anna.post(f"{own}/codings", json={"start": 0, "end": 5, "cat": sub}).json["ok"]
    assert anna.get(f"/guide/{code}/interviews/{rid}/transcript").status_code == 404  # the interview itself: as before
    assert _snapshot(app) == before


def test_new_category_from_the_coding_view(app, client):
    code, pid, rid, other, anna = _setup(app, client)
    url = f"/guide/{code}/interviews/coding/{pid}/categories/quick"
    main = _cats(app, pid, "main")[0][0]
    r = client.post(url, json={"name": "Erschöpfung", "definition": "müde", "kind": "sub", "parent": main})
    assert r.json["ok"] and r.json["category"]["parent_id"] == main
    r = client.post(url, json={"name": "Erschöpft", "definition": "müde", "kind": "sub", "parent": main})
    assert not r.json["ok"] and r.json["similar"][0]["name"] == "Erschöpfung"
    assert client.post(url, json={"name": "Erschöpft", "definition": "müde", "kind": "sub", "parent": main, "anyway": "1"}).json["ok"]


def test_codings_and_anchors_go_with_the_interview(app, client):
    from app import erasure
    from app.models import Survey

    code, pid, rid, other, anna = _setup(app, client)
    base = f"/guide/{code}/interviews/coding/{pid}/i/{rid}"
    sub = _cats(app, pid, "sub")[0][0]
    cid = client.post(f"{base}/codings", json={"start": 0, "end": 5, "cat": sub}).json["coding"]["id"]
    client.post(f"{base}/codings/{cid}/anchor")
    with app.app_context():
        survey = db.session.get(Survey, db.session.get(Response, rid).survey_id)
        erasure.erase(survey, [db.session.get(Response, rid)], "request")
        db.session.commit()
        assert db.session.get(Response, rid) is None and db.session.get(CodingCategory, sub).anchor is None
