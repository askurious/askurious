"""The Sichtrecht of an interviewer link (app/interviewer.py, may_see; Ralf 2026-11-05): "own" keeps everything as
before, "all" reads the other interviews of the guide but changes none of them, "results" also opens the results page,
never the export, the settings or the guide."""
import json

from app.extensions import db
from app.models import InterviewerAccess
from tests.interview.test_interview import first, make
from tests.interview.test_interviewer import access_of, add, begin, open_link


def _set(app, name, value):
    with app.app_context():
        access = db.session.scalar(db.select(InterviewerAccess).filter_by(name=name))
        access.may_see = value
        db.session.commit()


def _two_links(app, client, see):
    """A guide with Anna's interview (with a note) and Ben's link with the Sichtrecht `see`."""
    code, sid = make(app, client)
    q = first(app, sid)
    anna, _ = open_link(app, code, add(client, code, "Anna Becker"))
    rid = begin(anna, code)
    anna.post(f"/guide/{code}/interviews/{rid}/save", content_type="application/json", data=json.dumps(
        {"states": {str(q): "asked"}, "ticks": [], "notes": {str(q): "Sturz im Treppenhaus"}, "marks": [],
         "elapsed": 60_000, "blocks": {}}))
    ben, _ = open_link(app, code, add(client, code, "Ben Yilmaz", may_see=see))
    return code, q, rid, ben


def test_own_only_by_default(app, client):
    code, q, rid, ben = _two_links(app, client, "own")
    with app.app_context():
        assert db.session.get(InterviewerAccess, access_of(app, "Ben Yilmaz")).may_see == "own"
    home = ben.get(f"/i/{code}/interviews").data.decode()
    assert "Weitere Interviews dieses Leitfadens" not in home
    assert ben.get(f"/i/{code}/interviews/{rid}/read").status_code == 404
    assert ben.get(f"/guide/{code}/interviews/analysis").status_code == 404


def test_all_reads_but_never_changes(app, client):
    code, q, rid, ben = _two_links(app, client, "all")
    home = ben.get(f"/i/{code}/interviews").data.decode()
    assert "Weitere Interviews dieses Leitfadens" in home and f"/i/{code}/interviews/{rid}/read" in home
    page = ben.get(f"/i/{code}/interviews/{rid}/read").data.decode()
    assert "Sturz im Treppenhaus" in page and "nur lesen" in page and "<form" not in page.split("<!--flashes-->")[1].split("</main>")[0]
    # the routes that change or open an interview stay closed to an interview of someone else
    assert ben.get(f"/guide/{code}/conversation?interview={rid}").status_code == 404
    r = ben.post(f"/guide/{code}/interviews/{rid}/save", content_type="application/json", data=json.dumps(
        {"states": {str(q): "skipped"}, "ticks": [], "notes": {str(q): "überschrieben"}, "marks": [], "elapsed": 0, "blocks": {}}))
    assert r.status_code == 404
    assert ben.post(f"/guide/{code}/interviews/{rid}/end").status_code == 404
    assert "Sturz im Treppenhaus" in ben.get(f"/i/{code}/interviews/{rid}/read").data.decode()
    # no results page with "all" alone
    assert ben.get(f"/guide/{code}/interviews/analysis").status_code == 404


def test_results_opens_the_results_page_only(app, client):
    code, q, rid, ben = _two_links(app, client, "results")
    home = ben.get(f"/i/{code}/interviews").data.decode()
    assert f"/guide/{code}/interviews/analysis" in home
    page = ben.get(f"/guide/{code}/interviews/analysis").data.decode()
    assert "Fallübersicht" in page and "Sturz im Treppenhaus" in page
    assert f"/guide/{code}/settings" not in page and f"/guide/{code}/export" not in page  # no tabs, no downloads
    assert ben.get(f"/guide/{code}/interviews/cases.csv").status_code in (302, 401, 404)
    assert ben.get(f"/guide/{code}/settings").status_code in (302, 401, 404)


def test_rights_dialog_sets_and_undo_knows_it(app, client):
    code, q, rid, ben = _two_links(app, client, "own")
    aid = access_of(app, "Ben Yilmaz")
    client.post(f"/guide/{code}/interviewers/{aid}/rights", data={"has_interview": "1", "may_interview": "1", "has_see": "1", "may_see": "results"})
    with app.app_context():
        assert db.session.get(InterviewerAccess, aid).may_see == "results"
    assert "sieht alle Interviews und Ergebnisse" in client.get(f"/guide/{code}/interviews").data.decode()
    # a form of before without the field keeps the right
    client.post(f"/guide/{code}/interviewers/{aid}/rights", data={"has_interview": "1", "may_interview": "1"})
    with app.app_context():
        assert db.session.get(InterviewerAccess, aid).may_see == "results"
    from app import undo

    assert "may_see" in undo.ACCESS_FIELDS
