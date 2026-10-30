"""Interviews kodieren: thematic case summaries for everyone working in an analysis (app/transcript_coding_cases.py,
writer/write/undo; Ralf 2026-10-30): who wrote a cell and when ("von Forschenden" for cells from before), the
history of the analysis (without the text), "Rückgängig" of the own last step, the right to write for members and
interviewer links with the coding right in the interviews they see (double coding only once everyone has finished),
the Excel sheet "Fallzusammenfassungen" and the notes in the .qdpx."""
import io
import zipfile

from app.extensions import db
from app.models import CodingEvent, Response
from tests.interview.test_transcript_coding import _person
from tests.interview.test_transcript_coding_categories_xlsx import _sheets
from tests.interview.test_transcript_coding_review import BEA, _code, _setup, _sub


def _save(who, code, rid, key, text):
    return who.post(f"/guide/{code}/interviews/{rid}/summary", data={"key": key, "text": text})


def _meta(app, rid):
    with app.app_context():
        meta = db.session.get(Response, rid).interview
        return meta.get("summaries") or {}, meta.get("summary_meta") or {}, meta.get("summary_log") or []


def test_who_wrote_it_history_and_undo(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    key = f"c{pid}:{main}"
    # a cell from before: kept as it is, shown "von Forschenden"
    with app.app_context():
        row = db.session.get(Response, rid)
        row.interview = {**row.interview, "summaries": {key: "Alter Text"}}
        db.session.commit()
    page = client.get(f"/guide/{code}/interviews/summaries?auswertung={pid}").data.decode()
    assert "Alter Text" in page and "von Forschenden" in page
    r = _save(client, code, rid, key, "Neuer Text")
    assert r.json["ok"] and r.json["by"] == "Philipp" and r.json["at"] and r.json["undo"]
    _save(client, code, rid, key, "Neuer Text, weiter")                       # going on typing: one step
    texts, marks, log = _meta(app, rid)
    assert texts[key] == "Neuer Text, weiter" and marks[key]["by_name"] == "Philipp" and len(log) == 1
    assert log[0]["before"] == "Alter Text"
    # the history of the analysis: one line, without the text
    with app.app_context():
        events = db.session.scalars(db.select(CodingEvent).filter_by(project_id=pid, kind="summarised")).all()
        assert len(events) == 1 and "Neuer Text" not in str(events[0].data) and events[0].data["label"] == "AN-01"
    assert "fasst AN-01 zu „Belastung“ zusammen" in client.get(f"{base}/history").data.decode()
    # "Rückgängig": back to the old text; nothing more to take back after that
    r = client.post(f"/guide/{code}/interviews/{rid}/summary/undo", data={"key": key})
    assert r.json["ok"] and r.json["text"] == "Alter Text" and _meta(app, rid)[0][key] == "Alter Text"
    assert client.post(f"/guide/{code}/interviews/{rid}/summary/undo", data={"key": key}).status_code == 400
    # someone else wrote since: one's own step is no longer to be taken back
    _save(client, code, rid, key, "Philipps Text")
    client.post(f"{base}/members", data={"kind": "person", "person": BEA})
    assert _save(bea, code, rid, key, "Beas Text").json["ok"]
    assert client.post(f"/guide/{code}/interviews/{rid}/summary/undo", data={"key": key}).status_code == 400
    # the guide's own cells (Themenblöcke, "Fall insgesamt") stay the team's
    assert _save(bea, code, rid, "case", "x").status_code == 404
    assert _save(client, code, rid, "case", "Fall").json["ok"]


def test_members_and_links_write_where_they_see_and_after_double_coding(app, client):
    from app.models import User
    from tests.interview.test_transcript_coding_double import _assign

    code, pid, rid, base, main, bea = _setup(app, client)
    key = f"c{pid}:{main}"
    outsider, _o = _person(app, "0000-0002-1694-233X", "Olga")
    assert _save(outsider, code, rid, key, "x").status_code == 404                 # not in the analysis
    assert _save(app.test_client(), code, rid, key, "x").status_code in (302, 401)  # not signed in
    page = bea.get(f"{base}/cases").data.decode()
    assert "Fallzusammenfassungen" in page and f'data-summary-key="{key}"' in page and "summaries.js" in page
    assert "1 Interview</span>" in page and 'button-secondary" data-summary-revert hidden' in page
    assert _save(bea, code, rid, key, "Von Bea").json["by"] == "Bea"
    assert "Fallzusammenfassungen" in client.get(f"{base}/overview").data.decode()
    # double coding: not before everyone has finished
    carl, _c = _person(app, "0000-0001-5109-3700", "Carl")
    client.post(f"{base}/members", data={"kind": "person", "person": "0000-0001-5109-3700"})
    with app.app_context():
        ids = [db.session.scalar(db.select(User.id).filter_by(orcid=o)) for o in (BEA, "0000-0001-5109-3700")]
    _assign(app, client, base, rid, [f"u:{i}" for i in ids])
    assert _save(bea, code, rid, key, "blind").status_code == 404
    assert "erst, wenn alle" in bea.get(f"{base}/cases").data.decode()
    assert _save(client, code, rid, key, "Team").json["ok"]                         # the team always
    bea.post(f"{base}/i/{rid}/done")
    carl.post(f"{base}/i/{rid}/done")
    assert _save(bea, code, rid, key, "Nach Abschluss").json["ok"]


def test_an_interviewer_link_with_the_coding_right(app, client):
    from app.models import InterviewerAccess
    from tests.interview.test_interviewer import add as add_interviewer
    from tests.interview.test_interviewer import begin, open_link
    from tests.interview.test_transcript_coding import _transcript

    code, pid, rid, base, main, bea = _setup(app, client)
    key = f"c{pid}:{main}"
    c, _ = open_link(app, code, add_interviewer(client, code, "Tina", may_code="own"))
    own = begin(c, code)
    _transcript(app, own)
    with app.app_context():
        aid = db.session.scalar(db.select(InterviewerAccess.id).filter_by(name="Tina"))
    client.post(f"{base}/interviewers", data={"access": str(aid)})
    assert _save(c, code, own, key, "Tinas Text").json["by"] == "Tina"
    assert _save(c, code, rid, key, "fremd").status_code == 404                     # not hers
    assert "Tinas Text" in c.get(f"{base}/cases").data.decode()


def test_the_exports_carry_the_case_summaries(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(client, base, main, "Erschöpfung")
    _code(client, base, rid, "Abends", tired, 29)
    key = f"c{pid}:{main}"
    _save(client, code, rid, key, "Müde am Abend")
    sheets = {name: rows for name, rows, _root in _sheets(client.get(f"{base}/categories.xlsx").data)}
    rows = sheets["Fallzusammenfassungen"]
    assert rows[0][:4] == ["Interview", "Nummer", "Hauptkategorie", "Zusammenfassung"]
    assert rows[1][0] == "AN-01" and rows[1][2] == "Belastung" and rows[1][3] == "Müde am Abend" and rows[1][4] == "Philipp"
    data = client.get(f"{base}/analysis.qdpx").data
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        qde = z.read("project.qde").decode("utf-8")
    assert "Fallzusammenfassung: Belastung" in qde and "Müde am Abend" in qde
