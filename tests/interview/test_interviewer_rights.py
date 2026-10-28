"""The rights of an interviewer link afterwards (app/interviewer.py, rights; Ralf 2026-10-28): "darf Interviews führen",
"darf Tonaufnahmen machen", "darf transkribieren" and, with "Interviews kodieren", "darf kodieren" (nein, eigene,
alle). A link of before keeps working unchanged; changing a right neither renews the link nor ends a session, and a
right taken away is refused by the server with the next request. A link that only codes sees no interview functions.
Who of the links with the right codes in an analysis: all of them (the default) or the ticked ones."""
import pytest
from flask import url_for

from app.extensions import db
from app.models import InterviewerAccess
from tests.interview.test_interviewer import add, begin, open_link
from tests.interview.test_transcript_coding import _guide, _new, _transcript

RIGHTS = {"may_record": "1", "may_transcribe": "1", "has_interview": "1", "may_interview": "1"}


def _access(app, name):
    with app.app_context():
        return db.session.scalar(db.select(InterviewerAccess).filter_by(name=name))


def _urls(app, code, rid):
    with app.test_request_context():
        return {"record": url_for("interview_audio.upload_page", code=code, rid=rid),
                "transcript": url_for("interview_transcribe.edit", code=code, rid=rid),
                "conduct": url_for("interview.conduct", code=code, interview=rid)}


def test_link_of_before_keeps_working_and_rights_change_without_new_link(app, client):
    code, sid = _guide(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    rid = begin(c, code)
    before = _access(app, "Anna")
    assert before.may_interview and before.may_code == "none"                     # the defaults of every link
    urls = _urls(app, code, rid)
    assert c.get(f"/i/{code}/interviews").status_code == 200
    assert c.get(urls["record"]).status_code == 200 and c.get(urls["transcript"]).status_code == 200
    # recording taken away: refused at once, the session and the link stay
    r = client.post(f"/guide/{code}/interviewers/{before.id}/rights", data={**RIGHTS, "may_record": ""})
    assert r.status_code == 302
    after = _access(app, "Anna")
    assert (after.token_hash, after.revision, after.devices, after.expires_at) == (before.token_hash, before.revision, before.devices, before.expires_at)
    assert not after.may_record and after.may_transcribe
    assert c.get(f"/i/{code}/interviews").status_code == 200                       # no new sign-in
    assert c.get(urls["record"]).status_code == 404
    assert c.get(urls["transcript"]).status_code == 200
    # given back: works again with the same session
    client.post(f"/guide/{code}/interviewers/{before.id}/rights", data=RIGHTS)
    assert c.get(urls["record"]).status_code == 200
    # a form of before without "has_interview" keeps "darf Interviews führen"
    client.post(f"/guide/{code}/interviewers/{before.id}/rights", data={"may_record": "1", "may_transcribe": "1"})
    assert _access(app, "Anna").may_interview


def test_link_that_only_codes_sees_no_interview_functions(app, client):
    code, sid = _guide(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    rid = begin(c, code)
    _transcript(app, rid)
    aid = _access(app, "Anna").id
    urls = _urls(app, code, rid)
    client.post(f"/guide/{code}/interviewers/{aid}/rights", data={"has_interview": "1", "may_record": "1", "may_transcribe": "1",
                                                                    "may_code": "own"})
    access = _access(app, "Anna")
    assert not access.may_interview and not access.may_record and not access.may_transcribe   # only with interviewing
    for url in urls.values():
        assert c.get(url).status_code == 404
    assert c.post(f"/i/{code}/interviews/new", data={"consent": "1"}).status_code == 404
    pid = _new(client, code)
    home = c.get(f"/i/{code}/interviews")                                           # its one analysis at once
    assert home.status_code == 302 and home.headers["Location"].endswith(f"/guide/{code}/interviews/coding/{pid}")
    assert "A-01" in c.get(f"/guide/{code}/interviews/coding/{pid}").data.decode()
    # no analysis it codes in: says so
    for _ in range(2):  # switching over keeps everyone who coded so far; then untick
        client.post(f"/guide/{code}/interviews/coding/{pid}/interviewers", data={"who": "selected"})
    page = c.get(f"/i/{code}/interviews").data.decode()
    assert "Noch keine Auswertung zum Kodieren" in page and "Interview beginnen" not in page


def test_all_interviews_for_coding_not_for_editing(app, client):
    code, sid = _guide(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    other, _ = open_link(app, code, add(client, code, "Bert"))
    mine, theirs = begin(c, code), begin(other, code)
    _transcript(app, mine)
    _transcript(app, theirs)
    pid = _new(client, code)
    base = f"/guide/{code}/interviews/coding/{pid}"
    aid = _access(app, "Anna").id
    assert c.get(base).status_code == 404                                           # no right: no coding
    client.post(f"/guide/{code}/interviewers/{aid}/rights", data={**RIGHTS, "may_code": "own"})
    page = c.get(base).data.decode()
    assert "A-01" in page and "B-01" not in page
    client.post(f"/guide/{code}/interviewers/{aid}/rights", data={**RIGHTS, "may_code": "all"})
    page = c.get(base).data.decode()
    assert "A-01" in page and "B-01" in page
    assert c.get(f"{base}/i/{theirs}").status_code == 200                           # codes the other's transcript
    assert c.get(_urls(app, code, theirs)["transcript"]).status_code == 404         # but cannot edit it
    # "jede Gruppe ihre eigenen": "alle" is its own ones again
    client.post(f"{base}/settings", data={"name": "Seminar", "visibility": "own"})
    page = c.get(base).data.decode()
    assert "A-01" in page and "B-01" not in page
    # assigned only: "nur ausgewählte" without Anna
    client.post(f"{base}/settings", data={"name": "Seminar", "visibility": "all"})
    client.post(f"{base}/interviewers", data={"who": "selected"})
    assert c.get(base).status_code == 200                                         # switched over: still there
    client.post(f"{base}/interviewers", data={"who": "selected"})
    assert c.get(base).status_code == 404


def test_rights_for_everyone_and_undo(app, client):
    code, sid = _guide(app, client)
    add(client, code, "Anna")
    add(client, code, "Bert")
    anna, bert = _access(app, "Anna"), _access(app, "Bert")
    client.post(f"/guide/{code}/interviewers/{anna.id}/rights", data={"has_interview": "1", "may_interview": "1", "may_code": "all"})
    client.post(f"/guide/{code}/interviewers/{anna.id}/rights/all")
    bert = _access(app, "Bert")
    assert (bert.may_interview, bert.may_record, bert.may_transcribe, bert.may_code) == (True, False, False, "all")
    with app.test_request_context():
        undo_url = url_for("editor.undo", survey_id=sid)
    client.post(undo_url)                                                          # "für alle" back
    bert = _access(app, "Bert")
    assert (bert.may_record, bert.may_transcribe, bert.may_code) == (True, True, "none")
    client.post(undo_url)                                                          # Anna's change back
    anna = _access(app, "Anna")
    assert (anna.may_record, anna.may_code) == (True, "none")


def test_new_links_with_rights_and_the_page(app, client):
    code, sid = _guide(app, client)
    add(client, code, "Cora", has_interview="1", may_interview="", may_code="all")
    cora = _access(app, "Cora")
    assert (cora.may_interview, cora.may_record, cora.may_transcribe, cora.may_code) == (False, False, False, "all")
    add(client, code, "Dirk")                                                       # the form of before
    assert _access(app, "Dirk").may_interview and _access(app, "Dirk").may_code == "none"
    page = client.get(f"/guide/{code}/interviews").data.decode()
    assert 'data-dialog-open="iv-add"' in page and 'data-dialog-open="iv-rights-' in page
    assert "Kodieren: alle" in page and "Interviews · Aufnahme · Transkript" in page


@pytest.mark.sqlite_foreign_keys_off  # goes down and up again: SQLite's batch mode (tests/conftest.py, B8)
def test_migration_0104_keeps_links_and_their_sessions(app, client):
    """A link made before 0104, with an open session, works on without signing in again; a link ticked in an
    analysis before gets "eigene", every other one "nein"; all keep "darf Interviews führen"."""
    from alembic import command

    from app import migrations

    code, sid = _guide(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    open_link(app, code, add(client, code, "Bert"))
    rid = begin(c, code)
    _transcript(app, rid)
    pid = _new(client, code)
    anna = _access(app, "Anna")
    client.post(f"/guide/{code}/interviewers/{anna.id}/rights", data={**RIGHTS, "may_code": "own"})
    client.post(f"/guide/{code}/interviews/coding/{pid}/interviewers", data={"who": "selected", "access": str(anna.id)})
    with app.app_context():
        db.session.remove()
        migrations._run(command.downgrade, "0103")
        migrations._run(command.upgrade)
        assert migrations.current() == migrations.head()
    anna_after, bert = _access(app, "Anna"), _access(app, "Bert")
    assert (anna_after.token_hash, anna_after.revision) == (anna.token_hash, anna.revision)
    assert (anna_after.may_interview, anna_after.may_code, bert.may_interview, bert.may_code) == (True, "own", True, "none")
    assert c.get(f"/i/{code}/interviews").status_code == 200                       # the same session
    assert c.get(_urls(app, code, rid)["record"]).status_code == 200
    assert c.get(f"/guide/{code}/interviews/coding/{pid}").status_code == 200
