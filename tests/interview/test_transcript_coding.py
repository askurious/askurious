"""Interviews kodieren, stage 1 (app/transcript_coding.py, app/transcript_coding_links.py; experimental
"transcript_coding"): analyses of a guide, the category system with its rights, similar names, proposals and undo,
members, the tick for interviewer links and coding links. Above all: the interviewer links (app/interviewer.py) work
exactly as before, with the feature on or off, ticked or not."""
import re
from app.extensions import db
from app.models import (CodingCategory, CodingEvent, CodingMember, CodingProject, InterviewerAccess, Response,
                        Survey, User, utcnow)
from tests.conftest import experiments_on
from tests.interview.test_interview import make
from tests.interview.test_interviewer import add as add_interviewer
from tests.interview.test_interviewer import begin, open_link

FEATURE = "transcript_coding"
BEA = "0000-0002-1825-0097"


def _guide(app, client, on=True):
    code, sid = make(app, client)
    if on:
        experiments_on(client, FEATURE)
    return code, sid


def _new(client, code, name="Seminar", **extra):
    r = client.post(f"/guide/{code}/interviews/coding/new", data={"name": name, "question": "Wie erleben sie die Pflege?",
                                                                  "start": "guide", **extra})
    m = re.search(r"/interviews/coding/(\d+)$", r.headers.get("Location", ""))
    return int(m.group(1)) if m else None


def _cats(app, pid, kind=None):
    with app.app_context():
        q = db.select(CodingCategory).filter_by(project_id=pid).where(CodingCategory.removed_at.is_(None))
        if kind:
            q = q.filter_by(kind=kind)
        return [(c.id, c.kind, c.name, c.parent_id, c.author_key) for c in db.session.scalars(q.order_by(CodingCategory.id))]


def _person(app, orcid=BEA, name="Bea"):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    c.post("/terms/", data={"accept": "1"})
    with app.app_context():
        return c, db.session.scalar(db.select(User).filter_by(orcid=orcid)).id


def _snapshot(app):
    """Everything stored about the interviewer links, to show nothing of it changes."""
    with app.app_context():
        return [(a.id, a.name, a.token_hash, a.revision, a.blocked, a.may_record, a.may_transcribe, a.max_devices,
                 a.expires_at, a.hints_version) for a in db.session.scalars(db.select(InterviewerAccess).order_by(InterviewerAccess.id))]


def _transcript(app, rid, text="I: Wie war das?\nB: Anstrengend.", checked=False):
    with app.app_context():
        row = db.session.get(Response, rid)
        row.interview = {**row.interview, "transcript": {"text": text, "checked": checked}}
        db.session.commit()


def test_off_without_the_feature(app, client):
    code, sid = _guide(app, client, on=False)
    assert client.get(f"/guide/{code}/interviews/coding").status_code == 404
    assert client.post(f"/guide/{code}/interviews/coding/new", data={"name": "x", "start": "empty"}).status_code == 404
    assert "/interviews/coding" not in client.get(f"/guide/{code}/interviews/analysis").data.decode()
    experiments_on(client, FEATURE)
    assert "/interviews/coding" in client.get(f"/guide/{code}/interviews/analysis").data.decode()
    assert client.get(f"/guide/{code}/interviews/coding").status_code == 200


def test_analysis_from_the_guide_with_the_keys_of_the_codebook(app, client):
    from app import interview, qdc

    code, sid = _guide(app, client)
    pid = _new(client, code)
    assert pid
    with app.app_context():
        survey = db.session.get(Survey, sid)
        blocks = interview.guide_blocks(survey)
        mains = db.session.scalars(db.select(CodingCategory).filter_by(project_id=pid, kind="main").order_by(CodingCategory.id)).all()
        assert [m.guid for m in mains] == [qdc._guid(survey, "block", b.id) for b in blocks]
        subs = db.session.scalars(db.select(CodingCategory).filter_by(project_id=pid, kind="sub")).all()
        assert subs and all(s.parent_id in {m.id for m in mains} and s.source.startswith("item:") for s in subs)
        sub = subs[0]
        assert sub.guid == qdc._guid(survey, "item", int(sub.source.split(":")[1]))
        first = blocks[0].id
    page = client.get(f"/guide/{code}/interviews/coding/{pid}?view=categories").data.decode()
    assert "Kategoriensystem" in page and mains[0].name in page
    # only some topic blocks; empty; a copy
    some = _new(client, code, "Nur Block 1", which="some", blocks=str(first))
    assert len(_cats(app, some, "main")) == 1
    empty = _new(client, code, "Induktiv", start="empty")
    assert _cats(app, empty) == []
    copy = _new(client, code, "Kopie", start="copy", copy_of=str(pid))
    assert sorted(c[2] for c in _cats(app, copy)) == sorted(c[2] for c in _cats(app, pid))
    assert _new(client, code, "seminar") is None                                    # the same name twice


def test_categories_rights_similar_names_proposals_and_undo(app, client):
    code, sid = _guide(app, client)
    pid = _new(client, code, start="empty")
    base = f"/guide/{code}/interviews/coding/{pid}"
    client.post(f"{base}/categories/new", data={"kind": "main", "name": "Belastung"})
    main = _cats(app, pid, "main")[0][0]
    # a member (a person with an account, no role on the guide)
    bea, bea_id = _person(app)
    assert bea.get(base).status_code == 404
    client.post(f"{base}/members", data={"kind": "person", "person": BEA})
    assert bea.get(base).status_code == 200
    r = bea.post(f"{base}/categories/new", data={"kind": "sub", "parent": main, "name": "Erschöpfung", "definition": ""})
    assert _cats(app, pid, "sub") == []                                            # the definition is required
    bea.post(f"{base}/categories/new", data={"kind": "sub", "parent": main, "name": "Erschöpfung", "definition": "Müde durch die Pflege"})
    sub = _cats(app, pid, "sub")[0]
    assert sub[2] == "Erschöpfung" and sub[4] == f"u:{bea_id}"
    # similar names first: "Erschöpft" is refused until the person saw them ("anyway")
    assert [r["name"] for r in bea.get(f"{base}/similar?q=Erschöpft").json] == ["Erschöpfung"]
    bea.post(f"{base}/categories/new", data={"kind": "sub", "parent": main, "name": "Erschöpft", "definition": "x"})
    assert len(_cats(app, pid, "sub")) == 1
    bea.post(f"{base}/categories/new", data={"kind": "sub", "parent": main, "name": "Erschöpft", "definition": "x", "anyway": "1"})
    assert len(_cats(app, pid, "sub")) == 2
    # the author changes their own entry; the team too; another member only proposes
    assert bea.post(f"{base}/categories/{sub[0]}", data={"name": "Erschöpfung", "definition": "Müde, auch Schlafmangel", "delimitation": ""}).status_code == 302
    cid, _ = _person(app, "0000-0001-5109-3700", "Carl")
    client.post(f"{base}/members", data={"kind": "person", "person": "0000-0001-5109-3700"})
    cid.post(f"{base}/categories/{sub[0]}", data={"name": "Müdigkeit", "definition": "anders", "delimitation": ""})
    assert _cats(app, pid, "sub")[0][2] == "Erschöpfung"
    cid.post(f"{base}/categories/{sub[0]}/propose", data={"name": "Müdigkeit", "definition": "Müde, auch Schlafmangel", "reason": "kürzer"})
    with app.app_context():
        proposal = db.session.scalar(db.select(CodingEvent).filter_by(kind="proposal", status="open"))
        assert proposal.data["proposed"] == {"name": "Müdigkeit"}
    bea.post(f"{base}/proposals/{proposal.id}/accept")
    assert _cats(app, pid, "sub")[0][2] == "Müdigkeit"
    # undo: Bea's last step was taking on the proposal ("changed" by her): taken back
    assert bea.post(f"{base}/undo").status_code == 302
    assert _cats(app, pid, "sub")[0][2] == "Erschöpfung"
    # Carl may not remove Bea's category, Bea may
    cid.post(f"{base}/categories/{sub[0]}/remove")
    assert len(_cats(app, pid, "sub")) == 2
    bea.post(f"{base}/categories/{sub[0]}/remove")
    assert len(_cats(app, pid, "sub")) == 1
    bea.post(f"{base}/undo")                                                      # the removal taken back
    assert len(_cats(app, pid, "sub")) == 2
    # main categories fixed: a member's new main category becomes a candidate, the team takes it on
    client.post(f"{base}/settings", data={"name": "Seminar", "question": "", "visibility": "all", "mains_fixed": "1"})
    bea.post(f"{base}/categories/new", data={"kind": "main", "name": "Unterstützung", "definition": "Hilfe von außen"})
    candidate = _cats(app, pid, "candidate")[0]
    bea.post(f"{base}/categories/{main}", data={"name": "Last", "definition": "", "delimitation": ""})
    assert _cats(app, pid, "main")[0][2] == "Belastung"                           # fixed: only the team
    assert bea.post(f"{base}/categories/{candidate[0]}/promote").status_code == 404
    client.post(f"{base}/categories/{candidate[0]}/promote")
    assert {c[2] for c in _cats(app, pid, "main")} == {"Belastung", "Unterstützung"}
    assert "Verlauf" in client.get(f"{base}/overview").data.decode()


def test_interviewer_links_stay_as_they_are(app, client):
    """Ralf 2026-10-27: existing interviewer links go on working unchanged; the tick is off by default and only
    additive."""
    code, sid = _guide(app, client)
    c, _ = open_link(app, code, add_interviewer(client, code, "Anna Becker"))
    rid = begin(c, code)
    other, _ = open_link(app, code, add_interviewer(client, code, "Bert"))
    theirs = begin(other, code)
    _transcript(app, rid)
    _transcript(app, theirs)
    before = _snapshot(app)
    pid = _new(client, code)
    base = f"/guide/{code}/interviews/coding/{pid}"
    # without the tick: no coding for the interviewer, the own list as before
    home = c.get(f"/i/{code}/interviews").data.decode()
    assert "AB-01" in home and "/interviews/coding/" not in home
    assert c.get(base).status_code == 404
    with app.app_context():
        aid = db.session.scalar(db.select(InterviewerAccess.id).filter_by(name="Anna Becker"))
    client.post(f"{base}/interviewers", data={"access": str(aid)})
    assert c.get(base).status_code == 404                                         # assigned, but no right at the link
    client.post(f"/guide/{code}/interviewers/{aid}/rights", data={"may_record": "1", "may_transcribe": "1", "may_code": "own"})
    client.post(f"{base}/interviewers", data={"access": str(aid)})
    assert _snapshot(app) == before                                                # nothing of the links changed
    home = c.get(f"/i/{code}/interviews").data.decode()
    assert f"/interviews/coding/{pid}" in home
    page = c.get(base).data.decode()
    assert "AB-01" in page and "BE-01" not in page                                 # only the own interviews
    # also after "Transkript geprüft": coding yes, the interview itself no (as before, decision E)
    _transcript(app, rid, checked=True)
    assert "AB-01" in c.get(base).data.decode()
    assert c.get(f"/guide/{code}/interviews/{rid}/transcript").status_code == 404
    # the interviewer's other routes as before
    assert c.get(f"/i/{code}/interviews").status_code == 200
    assert other.get(base).status_code == 404
    assert c.get(f"/guide/{code}/interviews/coding").status_code in (302, 404)    # no list of analyses
    assert c.post(f"{base}/settings", data={"name": "x"}).status_code == 404       # no team rights
    # "nur ausgewählte" without this person: not here any more; the right off: nowhere
    client.post(f"{base}/interviewers", data={"who": "selected", "access": str(aid)})
    assert c.get(base).status_code == 200
    client.post(f"{base}/interviewers", data={"who": "selected"})
    assert c.get(base).status_code == 404
    client.post(f"{base}/interviewers", data={"who": "all"})
    assert c.get(base).status_code == 200
    client.post(f"/guide/{code}/interviewers/{aid}/rights", data={"may_record": "1", "may_transcribe": "1", "may_code": "none"})
    assert c.get(base).status_code == 404
    assert _snapshot(app) == before


def test_box_all_over_the_interviewer_ticks(app, client):
    """Ralf 2026-10-28: a box "Alle" over the people with the right "darf kodieren", only with two of them or more;
    ticked when all are assigned (the half state is drawn by app.js); it has no name, so the form saves the single
    boxes. The level of the right stands behind the name."""
    code, sid = _guide(app, client)
    add_interviewer(client, code, "Anna Becker", may_code="own")
    pid = _new(client, code)
    base = f"/guide/{code}/interviews/coding/{pid}"
    assert "data-check-all-box" not in client.get(f"{base}?view=settings").data.decode()     # one person: no box
    add_interviewer(client, code, "Bert", may_code="all")
    with app.app_context():
        ids = db.session.scalars(db.select(InterviewerAccess.id).order_by(InterviewerAccess.id)).all()
    page = client.get(f"{base}?view=settings").data.decode()
    assert "data-check-all-box" not in page and "alle mit Kodierrecht" in page                # "alle": no ticks
    client.post(f"{base}/interviewers", data={"who": "selected"})                             # switching over keeps all
    with app.app_context():
        assert len(db.session.scalars(db.select(CodingMember).filter_by(project_id=pid, kind=CodingMember.INTERVIEWER)).all()) == 2
    client.post(f"{base}/interviewers", data={"who": "selected"})                             # now untick both
    page = client.get(f"{base}?view=settings").data.decode()
    box = re.search(r'<input type="checkbox" data-check-all-box="access"[^>]*>', page).group(0)
    assert "checked" not in box and "name=" not in box
    assert "eigene Interviews" in page and "alle Interviews" in page
    client.post(f"{base}/interviewers", data={"who": "selected", "access": [str(i) for i in ids]})
    box = re.search(r'<input type="checkbox" data-check-all-box="access"[^>]*>', client.get(f"{base}?view=settings").data.decode()).group(0)
    assert "checked" in box
    with app.app_context():
        assert len(db.session.scalars(db.select(CodingMember).filter_by(project_id=pid, kind=CodingMember.INTERVIEWER)).all()) == 2


def test_the_address_of_a_coding_link_of_before(app, client):
    """The coding links of 2026-10-27 are gone (Ralf 2026-10-28): their address says so kindly, never an error."""
    code, sid = _guide(app, client)
    for c in (app.test_client(), client):
        page = c.get(f"/guide/{code}/coding-link")
        assert page.status_code == 410 and "Dieser Link gilt nicht mehr" in page.data.decode()
        assert c.post(f"/guide/{code}/coding-link", data={"key": "abc", "hints": "2026-10-27"}).status_code == 410
    pid = _new(client, code)
    assert "Kodierlinks ohne Konto" not in client.get(f"/guide/{code}/interviews/coding/{pid}?view=settings").data.decode()

def test_own_interviews_only_and_groups(app, client):
    code, sid = _guide(app, client)
    a, _ = open_link(app, code, add_interviewer(client, code, "Anna Neu"))
    b, _ = open_link(app, code, add_interviewer(client, code, "Bert Ernst"))
    ra, rb = begin(a, code), begin(b, code)
    pid = _new(client, code)
    base = f"/guide/{code}/interviews/coding/{pid}"
    with app.app_context():
        anna = db.session.scalar(db.select(InterviewerAccess.id).filter_by(name="Anna Neu"))
    client.post(f"{base}/settings", data={"name": "Seminar", "question": "", "visibility": "own"})
    # a person without an account who codes "all" by an interviewer link, assigned Anna's interviews
    g1, _ = open_link(app, code, add_interviewer(client, code, "Gruppe 1", has_interview="1", may_interview="", may_code="all"))
    with app.app_context():
        gid1 = db.session.scalar(db.select(InterviewerAccess.id).filter_by(name="Gruppe 1"))
    client.post(f"{base}/interviewers", data={"who": "selected", "access": str(gid1)})
    with app.app_context():
        row = db.session.scalar(db.select(CodingMember).filter_by(project_id=pid, access_id=gid1))
        row.access_ids = [anna]
        db.session.commit()
    page = g1.get(base).data.decode()
    assert "AN-01" in page and "BE-01" not in page
    team = client.get(base).data.decode()
    assert "AN-01" in team and "BE-01" in team                                     # the team sees all
    client.post(f"{base}/settings", data={"name": "Seminar", "question": "", "visibility": "all"})
    assert "BE-01" in g1.get(base).data.decode()
    # a group of the team member as member: its people get in
    from app.models import WorkGroup, WorkGroupMember

    bea, bea_id = _person(app)
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        group = WorkGroup(name="Seminar WS")
        group.members.append(WorkGroupMember(orcid=me.orcid, user_id=me.id, name=me.name, role="lead"))
        group.members.append(WorkGroupMember(orcid=BEA, user_id=bea_id, name="Bea", role="member"))
        db.session.add(group)
        db.session.commit()
        gid = group.id
    assert bea.get(base).status_code == 404
    client.post(f"{base}/members", data={"kind": "group", "group": str(gid)})
    assert bea.get(base).status_code == 200


def test_everything_goes_with_the_guide_and_the_account(app, client):
    from app import accounts, lifecycle, transcript_coding

    code, sid = _guide(app, client)
    pid = _new(client, code)
    bea, bea_id = _person(app)
    client.post(f"/guide/{code}/interviews/coding/{pid}/members", data={"kind": "person", "person": BEA})
    with app.app_context():
        user = db.session.get(User, bea_id)
        accounts.delete(user, "self")
        assert db.session.scalar(db.select(db.func.count(CodingMember.id)).filter_by(user_id=bea_id)) == 0
        transcript_coding.forget_survey(sid)
        db.session.commit()
        for model in (CodingProject, CodingCategory, CodingEvent, CodingMember):
            assert db.session.scalar(db.select(db.func.count(model.id))) == 0, model
    assert lifecycle  # lifecycle.delete calls forget_survey (app/lifecycle.py)


def test_a_coding_group_does_not_cross_the_organisation_fence(app, client):
    """A group with the role "Kodieren" becomes an organisation of its own while the guide stays where it is: its
    members keep coding as persons, a person who joins the group later does not read the transcripts, and a group
    that ends up in another organisation lets nobody in (audit 2026-10-28, S01). The personal role counts as a share
    beyond the organisation (V01)."""
    from app import orgs
    from app.models import Organisation, WorkGroup, WorkGroupMember

    code, sid = _guide(app, client)
    pid = _new(client, code)
    base = f"/guide/{code}/interviews/coding/{pid}"
    bea, bea_id = _person(app)
    carl, carl_id = _person(app, orcid="0000-0003-1415-9269", name="Carl")
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        group = WorkGroup(name="Seminar WS")
        group.members.append(WorkGroupMember(orcid=BEA, user_id=bea_id, name="Bea", role="lead"))
        db.session.add(group)
        db.session.flush()
        gid = group.id
        # the team added the group while it was in the guide's organisation (the guide's admin is not in it)
        db.session.add(CodingMember(project_id=pid, kind=CodingMember.GROUP, group_id=gid, added_by_id=me.id))
        db.session.commit()
    assert bea.get(base).status_code == 200
    with app.app_context():
        group = db.session.get(WorkGroup, gid)
        shares = orgs.crossing(orgs.plan(group), keep_group=True)
        assert [(kind, thing.id) for kind, thing, _g, _r in shares] == [("coding", pid)]
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        orgs.make_from_group(group, "Lehrstuhl", by=me)
        db.session.commit()
        rows = db.session.scalars(db.select(CodingMember).filter_by(project_id=pid)).all()
        assert [(r.kind, r.user_id) for r in rows] == [(CodingMember.PERSON, bea_id)]   # personal now, group row gone
        group = db.session.get(WorkGroup, gid)
        group.members.append(WorkGroupMember(orcid="0000-0003-1415-9269", user_id=carl_id, name="Carl", role="member"))
        db.session.commit()
        assert orgs.personal_shares(bea_id) == {(orgs.of(db.session.get(Survey, sid)), me.id)}
    assert bea.get(base).status_code == 200                       # keeps coding in person
    assert carl.get(base).status_code == 404                      # the newcomer reads nothing
    # a group row that points across the fence some other way lets nobody in
    with app.app_context():
        db.session.add(CodingMember(project_id=pid, kind=CodingMember.GROUP, group_id=gid))
        db.session.commit()
    assert carl.get(base).status_code == 404
    with app.app_context():
        other = db.session.scalar(db.select(Organisation).filter_by(name="Lehrstuhl"))
        db.session.get(Survey, sid).org_id = other.id             # the guide moves there too: same organisation
        db.session.commit()
    assert carl.get(base).status_code == 200
    with app.app_context():
        from app import groups

        groups.remove(db.session.get(WorkGroup, gid))
        db.session.commit()
        assert db.session.scalar(db.select(CodingMember).filter_by(kind=CodingMember.GROUP, group_id=gid)) is None
