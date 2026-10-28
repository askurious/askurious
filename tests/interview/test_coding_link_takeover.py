"""Coding links of before become interviewer links (app/transcript_coding_links.py, convert; Ralf 2026-10-28): a
session of a coding link goes on without signing in again, its codings stay its own, it gets no interview functions,
a blocked one stays blocked, and the other analyses of the guide keep exactly their coders."""
from datetime import timedelta

from app import transcript_coding_links as links
from app.extensions import db
from app.models import CodingLink, CodingMember, InterviewerAccess, utcnow
from tests.interview.test_interviewer import add, begin, open_link
from tests.interview.test_transcript_coding import _cats, _guide, _new, _transcript


def _old_link(app, code, pid, name="Lea", blocked=False):
    """A coding link as made before 2026-10-28, with a session of it in a browser of its own."""
    c = app.test_client()
    with app.app_context():
        from app.models import CodingProject

        project = db.session.get(CodingProject, pid)
        now = utcnow().isoformat(timespec="seconds")
        row = CodingLink(project_id=pid, survey_id=project.survey_id, name=name, token_hash=f"hash-{name}", revision="r1",
                         blocked=blocked, devices=[{"id": "dev1", "kind": "Gerät", "first": now, "last": now}],
                         expires_at=utcnow() + timedelta(days=30))
        db.session.add(row)
        db.session.commit()
        lid = row.id
    with c.session_transaction() as s:
        s[links.SESSION_KEY] = {str(pid): {"a": lid, "r": "r1", "d": "dev1", "t": now}}
    return c, lid


def test_session_goes_on_and_codings_stay_its_own(app, client):
    code, sid = _guide(app, client)
    iv, _ = open_link(app, code, add(client, code, "Anna"))
    rid = begin(iv, code)
    _transcript(app, rid, "B: Abends bin ich einfach fertig.")
    pid = _new(client, code)
    other = _new(client, code, "Zweite", start="empty")
    base = f"/guide/{code}/interviews/coding/{pid}"
    lea, lid = _old_link(app, code, pid)
    page = lea.get(base)                                                            # no new sign-in
    assert page.status_code == 200 and "Kodieren als Lea" in page.data.decode()
    with app.app_context():
        access = db.session.scalar(db.select(InterviewerAccess).filter_by(name="Lea"))
        member = db.session.scalar(db.select(CodingMember).filter_by(project_id=pid, access_id=access.id))
        assert (access.may_interview, access.may_record, access.may_transcribe, access.may_code) == (False, False, False, "all")
        assert access.revision == "r1" and member.role == f"l:{lid}" and db.session.get(CodingLink, lid).blocked
    sub = _cats(app, pid, "sub")[0][0]
    made = lea.post(f"{base}/i/{rid}/codings", json={"start": 3, "end": 9, "cat": sub}).json
    assert made["ok"]
    with app.app_context():
        from app.models import Response

        mine = [c for c in db.session.get(Response, rid).interview["codings"] if c["id"] == made["coding"]["id"]]
        assert mine[0]["by"] == f"l:{lid}"                                        # the key of the coding link of before
    assert lea.get(f"/guide/{code}/interviews/coding/{other}").status_code == 404   # only its own analysis
    # no interview functions
    assert lea.post(f"/i/{code}/interviews/new", data={"consent": "1"}).status_code == 404
    assert lea.get(f"/guide/{code}/interviews/{rid}/transcript").status_code == 404
    # its old address opens the interviewer link now; blocking that blocks the old way in too
    client.post(f"/guide/{code}/interviewers/{access.id}/block")
    assert lea.get(base).status_code in (302, 404)
    second, _ = _old_link(app, code, pid, name="Max")
    assert second.get(base).status_code == 200                                     # another one, taken over too
    page = client.get(f"{base}?view=settings").data.decode()
    assert "Kodierlinks ohne Konto" not in page                                    # nothing left to look after there


def test_a_blocked_link_stays_blocked(app, client):
    code, sid = _guide(app, client)
    pid = _new(client, code)
    base = f"/guide/{code}/interviews/coding/{pid}"
    lea, lid = _old_link(app, code, pid, blocked=True)
    assert lea.get(base).status_code in (302, 404)
    with app.app_context():
        assert db.session.scalar(db.select(InterviewerAccess).filter_by(name="Lea")) is None   # not taken over
        assert links.links(db.session.get(__import__("app.models", fromlist=["CodingProject"]).CodingProject, pid))
