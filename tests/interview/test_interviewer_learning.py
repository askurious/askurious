"""The right "Lernhilfen zeigen" of an interviewer link (app/interviewer.py, app/learning.py; migration 0108, Ralf
2026-10-29): on for every link, also for those of before; with it the sessions of the link see the folds "Warum?" and
the "?" at statistics, without it none. Changing it keeps the link and its session as they are, and "Nicht mehr zeigen"
(kept at an account) is not offered without an account."""
from app.extensions import db
from app.models import InterviewerAccess
from tests.interview.test_interviewer import add, begin, open_link
from tests.interview.test_interviewer_rights import _urls
from tests.interview.test_transcript_coding import _guide, _transcript

RIGHTS = {"may_record": "1", "may_transcribe": "1", "has_interview": "1", "may_interview": "1", "has_learning": "1"}


def _access(app, name):
    with app.app_context():
        return db.session.scalar(db.select(InterviewerAccess).filter_by(name=name))


def test_learning_aids_of_an_interviewer_link(app, client):
    code, sid = _guide(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    rid = begin(c, code)
    _transcript(app, rid)
    before = _access(app, "Anna")
    assert before.show_learning                                                   # on by default
    url = _urls(app, code, rid)["transcript"]
    page = c.get(url)
    assert page.status_code == 200, page.status_code
    text = page.data.decode()
    assert "learn-why" in text
    assert "data-learn-hide" not in text                                          # no account: no "Nicht mehr zeigen"
    # switched off in the dialog: no folds; the link, its devices and the session stay
    r = client.post(f"/guide/{code}/interviewers/{before.id}/rights", data=RIGHTS)
    assert r.status_code == 302
    after = _access(app, "Anna")
    assert not after.show_learning
    assert (after.token_hash, after.revision, after.devices) == (before.token_hash, before.revision, before.devices)
    text = c.get(url).data.decode()
    assert "learn-why" not in text
    # on again
    client.post(f"/guide/{code}/interviewers/{before.id}/rights", data={**RIGHTS, "show_learning": "1"})
    assert "learn-why" in c.get(url).data.decode()
    # a form of before without "has_learning" leaves the right as it is
    client.post(f"/guide/{code}/interviewers/{before.id}/rights", data={"may_record": "1", "may_transcribe": "1"})
    assert _access(app, "Anna").show_learning


def test_new_links_take_the_tick_of_the_add_dialog(app, client):
    code, sid = _guide(app, client)
    client.post(f"/guide/{code}/interviewers/new", data={"names": "Ben", "days": "30", "devices": "2", "has_interview": "1",
                                                     "may_interview": "1", "has_learning": "1"})
    client.post(f"/guide/{code}/interviewers/new", data={"names": "Cem", "days": "30", "devices": "2", "has_interview": "1",
                                                     "may_interview": "1", "has_learning": "1", "show_learning": "1"})
    assert not _access(app, "Ben").show_learning and _access(app, "Cem").show_learning
    page = client.get(f"/guide/{code}/interviews").data.decode()
    assert 'name="show_learning"' in page and "Lernhilfen zeigen" in page
