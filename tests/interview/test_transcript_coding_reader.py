"""Interviews kodieren, the role "Lesen" (app/transcript_coding.py, Ralf 2026-11-27): the team enters a person or a
group per analysis as "Lesen" next to "Kodieren". A reader sees what a coder sees of the same interviews, and
changes nothing: every POST of the blueprint is refused on the server, not only left off the pages. Readers do not
count as coders, download the coding guide and the diagram only, and the role can be switched afterwards."""
import json
import re

from app.extensions import db
from app.models import (CodingCategory, CodingEvent, CodingKey, CodingMember, CodingMemo, Response, User, WorkGroup,
                        WorkGroupMember)
from tests.interview.test_interviewer import add as add_interviewer
from tests.interview.test_interviewer import begin, open_link
from tests.interview.test_transcript_coding import BEA, _cats, _guide, _new, _person, _transcript

TEXT = "I: Wie war der Tag?\nB: Abends bin ich einfach fertig. Manchmal frage ich mich, wie lange ich das noch schaffe."
CARL = "0000-0001-5109-3700"


def _setup(app, client):
    """A guide with two interviews (Anna's, Bert's) with transcripts, an analysis from the guide, one coded passage
    and one memo of the team."""
    code, sid = _guide(app, client)
    rid = begin(open_link(app, code, add_interviewer(client, code, "Anna"))[0], code)
    other = begin(open_link(app, code, add_interviewer(client, code, "Bert"))[0], code)
    _transcript(app, rid, TEXT)
    _transcript(app, other, TEXT)
    pid = _new(client, code)
    base = f"/guide/{code}/interviews/coding/{pid}"
    sub = _cats(app, pid, "sub")[0][0]
    r = client.post(f"{base}/i/{rid}/codings", json={"start": TEXT.index("Abends"), "end": TEXT.index("fertig") + 6, "cat": sub})
    assert r.json["ok"]
    assert client.post(f"{base}/memos", json={"kind": "category", "cat": sub, "text": "Grenzfall?"}).json["ok"]
    return code, pid, rid, other, base, sub, r.json["coding"]["id"]


def _member(app, pid, user_id):
    with app.app_context():
        return db.session.scalar(db.select(CodingMember).filter_by(project_id=pid, kind="person", user_id=user_id))


def _state(app, pid):
    """Everything an analysis stores, to show a reader's requests change none of it."""
    with app.app_context():
        rows = lambda model: [  # noqa: E731
            {c.name: str(getattr(r, c.name)) for c in model.__table__.columns}
            for r in db.session.scalars(db.select(model).filter_by(project_id=pid).order_by(model.id))]
        interviews = [(r.id, json.dumps(r.interview, sort_keys=True, default=str))
                      for r in db.session.scalars(db.select(Response).where(Response.interview.is_not(None)).order_by(Response.id))]
        from app.models import CodingProject

        project = db.session.get(CodingProject, pid)
        return (rows(CodingCategory), rows(CodingEvent), rows(CodingMember), rows(CodingMemo), rows(CodingKey), interviews,
                json.dumps(project.settings, sort_keys=True, default=str), project.name, project.visibility, project.mains_fixed)


def _own_forms(page):
    """The forms of a page that post into the analysis (the head of every page has forms of its own, e.g. signing out)."""
    return [f for f in re.findall(r"<form[^>]*>", page) if 'method="post"' in f and "/interviews/coding/" in f]


def test_a_reader_sees_the_analysis_and_no_way_to_change_it(app, client):
    code, pid, rid, other, base, sub, coding = _setup(app, client)
    bea, bea_id = _person(app)
    assert bea.get(base).status_code == 404                                        # "Ansehen" or nothing: not a member
    client.post(f"{base}/members", data={"kind": "person", "person": BEA, "role": "reader"})
    assert _member(app, pid, bea_id).role == "reader"
    pages = ["", "?view=categories", "?view=categories&show=diagram", "/overview", "/passages", "/matrix", "/agreement",
             "/history", "/cases", f"/i/{rid}", f"/i/{other}", "?q=fertig"]
    for path in pages:
        r = bea.get(base + path)
        assert r.status_code == 200, path
        page = r.data.decode()
        assert _own_forms(page) == [], path
        assert "data-tc-new" not in page and "data-memo-save" not in page and "data-tc-grab" not in page, path
    assert "Abends bin ich einfach fertig" in bea.get(f"{base}/passages?cat={sub}").data.decode()
    assert "Lesen als Bea" in bea.get(base).data.decode()
    # the coding view: the passage and the memo are there, the script is told to offer nothing
    page = bea.get(f"{base}/i/{rid}").data.decode()
    data = json.loads(re.search(r'<script type="application/json" id="tc-data">(.*?)</script>', page, re.S).group(1))
    assert data["readonly"] is True and data["suggestions"] == [] and not any(data["editable"].values())
    assert [c["id"] for c in data["codings"]] == [coding] and not data["codings"][0]["mine"]
    assert "data-keys-url" not in page
    memos = json.loads(re.search(r'<script type="application/json" id="tc-memos">(.*?)</script>',
                                 bea.get(base + "?view=categories").data.decode(), re.S).group(1))
    assert [m["text"] for m in memos] == ["Grenzfall?"] and not memos[0]["mine"]
    # neither the settings nor the mutual check (a step of coding), and no link to it
    assert bea.get(base + "?view=settings").status_code == 404
    assert bea.get(f"{base}/check").status_code == 404
    assert f"{base}/check" not in bea.get(base).data.decode() and f"{base}/check" in client.get(base).data.decode()
    # the same pages keep their steps for the team
    assert _own_forms(client.get(base + "?view=categories").data.decode()) and "data-memo-save" in client.get(base).data.decode()


def test_downloads_of_a_reader(app, client):
    """The coding guide (Word) and the diagram, nothing that carries every passage or a table to work on with."""
    code, pid, rid, other, base, sub, coding = _setup(app, client)
    bea, _ = _person(app)
    client.post(f"{base}/members", data={"kind": "person", "person": BEA, "role": "reader"})
    for path in ("/kodierleitfaden.docx", "/map.svg", "/map.png", "/map.pdf"):
        assert bea.get(base + path).status_code == 200, path
    for path in ("/categories.xlsx", "/categories.csv", "/categories.qdc", "/analysis.qdpx", "/matrix?format=csv",
                 "/agreement?format=csv"):
        assert bea.get(base + path).status_code == 404, path
        assert client.get(base + path).status_code == 200, path                    # the team as before
    menu = bea.get(base + "?view=categories").data.decode()
    assert "kodierleitfaden.docx" in menu and "categories.xlsx" not in menu and "analysis.qdpx" not in menu
    assert "format=csv" not in bea.get(f"{base}/matrix").data.decode()


def test_every_post_of_the_blueprint_is_refused_for_a_reader(app, client):
    """Not a list kept by hand: every rule of the blueprint that takes a POST, with ids that exist, so that a route
    added later is covered too. With an analysis in the address the refusal is the central one (403 from _in)."""
    code, pid, rid, other, base, sub, coding = _setup(app, client)
    bea, bea_id = _person(app)
    client.post(f"{base}/members", data={"kind": "person", "person": BEA, "role": "reader"})
    with app.app_context():
        event = db.session.scalar(db.select(CodingEvent.id).filter_by(project_id=pid).order_by(CodingEvent.id.desc()))
        memo = db.session.scalar(db.select(CodingMemo.id).filter_by(project_id=pid))
    mid = _member(app, pid, bea_id).id
    values = {"code": code, "pid": pid, "rid": rid, "cid": None, "mid": None, "eid": event or 1, "hid": "h1",
              "answer": "accept", "action": "accept"}
    form = {"name": "Neu", "definition": "x", "kind": "sub", "parent": str(sub), "cat": str(sub), "text": "x", "start": "0",
            "end": "9", "role": "coder", "person": BEA, "choice": "a", "coding": coding, "rid": str(rid), "anyway": "1",
            "hit": f"{rid}:0:5", "q": "fertig", "action": "save", "slot": "1", "source": str(sub), "target": str(sub),
            "reason": "x", "ai_allowed": "1", "mode": "plan", "who": "all", "visibility": "own"}
    before = _state(app, pid)
    seen = 0
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("transcript_coding.") or "POST" not in rule.methods:
            continue
        # <cid> is a category in the routes of the category system and a passage below /codings/; <mid> a member or a memo
        ids = dict(values, cid=coding if "/codings/" in rule.rule else sub, mid=memo if "/memos/" in rule.rule else mid)
        url = rule.build({k: ids[k] for k in rule.arguments}, append_unknown=False)[1]
        for kind in ("form", "json"):
            r = bea.post("/guide" + url if not url.startswith("/guide") else url, **({"data": form} if kind == "form" else {"json": form}))
            if "pid" in rule.arguments:
                assert r.status_code == 403, (rule.rule, kind, r.status_code)
                seen += 1
            else:
                assert r.status_code in (403, 404, 410), (rule.rule, kind, r.status_code)
    assert seen >= 80, seen                                                        # 40 and more routes, each asked twice
    assert _state(app, pid) == before
    # the cells of the case summaries are written by a route of another blueprint: not by a reader either
    with app.app_context():
        main = db.session.scalar(db.select(CodingCategory.id).filter_by(project_id=pid, kind="main").order_by(CodingCategory.id))
    cell = {"key": f"c{pid}:{main}", "text": "Zusammenfassung"}
    assert bea.post(f"/guide/{code}/interviews/{rid}/summary", data=cell).status_code == 404
    assert _state(app, pid) == before
    # the same requests go through for a coder: the refusal is the role, not the test's data
    client.post(f"{base}/members/{mid}/role", data={"role": "coder"})
    assert bea.post(f"{base}/memos", json={"kind": "category", "cat": sub, "text": "Jetzt darf ich."}).json["ok"]
    assert bea.post(f"/guide/{code}/interviews/{rid}/summary", data=cell).status_code == 200


def test_the_role_is_switched_afterwards_and_readers_are_no_coders(app, client):
    from app import transcript_coding_double
    from app.models import CodingProject, Survey

    code, pid, rid, other, base, sub, coding = _setup(app, client)
    bea, bea_id = _person(app)
    client.post(f"{base}/members", data={"kind": "person", "person": BEA})          # without a role: "Kodieren", as before
    row = _member(app, pid, bea_id)
    assert row.role == "coder"
    span = {"start": TEXT.index("Manchmal"), "end": TEXT.index("schaffe") + 7, "cat": sub}
    own = bea.post(f"{base}/i/{rid}/codings", json=span).json["coding"]["id"]

    def assignable():
        with app.app_context():
            project = db.session.get(CodingProject, pid)
            return dict(transcript_coding_double.assignable(project, db.session.get(Survey, project.survey_id)))

    assert f"u:{bea_id}" in assignable()
    page = client.get(base + "?view=settings").data.decode()
    assert f"/members/{row.id}/role" in page and '<option value="reader"' in page and '<option value="coder" selected' in page
    # switched to "Lesen": nothing more of hers, what she coded stays under her name
    assert client.post(f"{base}/members/{row.id}/role", data={"role": "reader"}).status_code == 302
    assert _member(app, pid, bea_id).role == "reader"
    assert bea.post(f"{base}/i/{rid}/codings/{own}/delete").status_code == 403
    assert bea.post(f"{base}/i/{other}/codings", json=span).status_code == 403
    with app.app_context():
        stored = {c["id"]: c for c in db.session.get(Response, rid).interview["codings"]}
        assert stored[own]["by"] == f"u:{bea_id}" and stored[own]["by_name"] == "Bea"
    assert f"u:{bea_id}" not in assignable()                                       # not offered for double coding or the check
    assert '<option value="reader" selected' in client.get(base + "?view=settings").data.decode()
    # a role that does not exist, a member of another analysis, and anyone but the team
    assert client.post(f"{base}/members/{row.id}/role", data={"role": "admin"}).status_code == 404
    assert client.post(f"{base}/members/999999/role", data={"role": "coder"}).status_code == 404
    assert bea.post(f"{base}/members/{row.id}/role", data={"role": "coder"}).status_code == 403
    assert _member(app, pid, bea_id).role == "reader"
    # back to "Kodieren": her passage is hers again
    client.post(f"{base}/members/{row.id}/role", data={"role": "coder"})
    assert bea.post(f"{base}/i/{rid}/codings/{own}/delete").json["ok"]


def test_groups_read_and_one_coding_row_makes_a_coder(app, client):
    """A group entered with "Lesen": its people read. A person who is also entered with "Kodieren" codes."""
    code, pid, rid, other, base, sub, coding = _setup(app, client)
    bea, bea_id = _person(app)
    carl, carl_id = _person(app, CARL, "Carl")
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        group = WorkGroup(name="Seminar WS")
        group.members.append(WorkGroupMember(orcid=me.orcid, user_id=me.id, name=me.name, role="lead"))
        group.members.append(WorkGroupMember(orcid=BEA, user_id=bea_id, name="Bea", role="member"))
        group.members.append(WorkGroupMember(orcid=CARL, user_id=carl_id, name="Carl", role="member"))
        db.session.add(group)
        db.session.commit()
        gid = group.id
    client.post(f"{base}/members", data={"kind": "group", "group": str(gid), "role": "reader"})
    memo = {"kind": "category", "cat": sub, "text": "Notiz"}
    for person in (bea, carl):
        assert person.get(base).status_code == 200 and person.post(f"{base}/memos", json=memo).status_code == 403
    client.post(f"{base}/members", data={"kind": "person", "person": CARL, "role": "coder"})
    assert carl.post(f"{base}/memos", json=memo).json["ok"]
    assert bea.post(f"{base}/memos", json=memo).status_code == 403


def test_a_reader_sees_the_interviews_a_coder_would(app, client):
    """"jede Gruppe ihre eigenen": the rule of the coders, with the interviewer links the team assigns to the row."""
    from app.models import InterviewerAccess

    code, pid, rid, other, base, sub, coding = _setup(app, client)
    bea, bea_id = _person(app)
    client.post(f"{base}/members", data={"kind": "person", "person": BEA, "role": "reader"})
    client.post(f"{base}/settings", data={"name": "Seminar", "question": "", "visibility": "own"})
    assert bea.get(f"{base}/i/{rid}").status_code == 404 and bea.get(f"{base}/i/{other}").status_code == 404
    with app.app_context():
        anna = db.session.scalar(db.select(InterviewerAccess.id).filter_by(name="Anna"))
    client.post(f"{base}/members/{_member(app, pid, bea_id).id}/interviews", data={"access": str(anna)})
    assert bea.get(f"{base}/i/{rid}").status_code == 200 and bea.get(f"{base}/i/{other}").status_code == 404
    assert "Abends bin ich einfach fertig" in bea.get(f"{base}/passages?cat={sub}").data.decode()


def test_archived_guide_and_interviewer_links(app, client):
    """An archived guide is the team's alone, as for coders; an interviewer link codes as before, whatever the
    roles of the members."""
    from app.models import Survey

    code, pid, rid, other, base, sub, coding = _setup(app, client)
    bea, _ = _person(app)
    client.post(f"{base}/members", data={"kind": "person", "person": BEA, "role": "reader"})
    link, _ = open_link(app, code, add_interviewer(client, code, "Gruppe 1", has_interview="1", may_interview="", may_code="all"))
    span = {"start": TEXT.index("Manchmal"), "end": TEXT.index("schaffe") + 7, "cat": sub}
    assert link.post(f"{base}/i/{rid}/codings", json=span).json["ok"]
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(code=code))
        survey.archived = True
        db.session.commit()
    assert bea.get(base).status_code == 404 and client.get(base).status_code == 200
