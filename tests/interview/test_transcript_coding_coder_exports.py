"""Interviews kodieren: Excel, CSV, .qdc and .qdpx also for interviewer links, a setting of the analysis (Ralf
2026-11-19). Off by default; then the files hold only the interviews and passages the interviewer sees, and with
"Namen der Kodierenden ausblenden" (on by default) "Kodierende 1, 2, …" instead of names. The team's files as before."""
import io
import zipfile

from app.extensions import db
from app.models import InterviewerAccess
from tests.interview.test_interviewer import add as add_interviewer
from tests.interview.test_interviewer import begin, open_link
from tests.interview.test_transcript_coding import _transcript
from tests.interview.test_transcript_coding_review import TEXT, _code, _setup, _sub


def _qde(response):
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as z:
        return z.read("project.qde").decode(), [n for n in z.namelist() if n.startswith("Sources/")]


def _coder(app, client, code, base):
    """An interviewer link "Doll" with the right to code its own interviews, assigned, with one interview."""
    coder, _ = open_link(app, code, add_interviewer(client, code, "Doll"))
    rid = begin(coder, code)
    _transcript(app, rid, TEXT)
    with app.app_context():
        aid = db.session.scalar(db.select(InterviewerAccess.id).filter_by(name="Doll"))
    client.post(f"/guide/{code}/interviewers/{aid}/rights", data={"may_record": "1", "may_transcribe": "1", "may_code": "own"})
    client.post(f"{base}/interviewers", data={"access": str(aid)})
    return coder, rid


def _set(client, base, exports, hidden):
    data = {"name": "Belastung im Alltag", "has_coder_exports": "1"}
    if exports:
        data["coder_exports"] = "1"
    if hidden:
        data["coder_names_hidden"] = "1"
    assert client.post(f"{base}/settings", data=data).status_code == 302


FILES = ("categories.xlsx", "categories.csv", "categories.qdc", "analysis.qdpx")


def test_off_by_default_the_interviewer_gets_the_coding_guide_only(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    coder, _mine = _coder(app, client, code, base)
    page = coder.get(f"{base}?view=categories").data.decode()
    assert "kodierleitfaden.docx" in page and "categories.csv" not in page and "analysis.qdpx" not in page
    for url in FILES:
        assert coder.get(f"{base}/{url}").status_code == 404, url
    settings = client.get(f"{base}?view=settings").data.decode()
    assert 'name="coder_exports"' in settings and 'name="coder_exports" value="1" checked' not in settings
    assert 'name="coder_names_hidden" value="1" checked' in settings              # names hidden: on by default


def test_with_the_setting_the_files_hold_what_the_interviewer_sees(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    sub = _sub(client, base, main, "Erschöpfung")
    _code(client, base, rid, "Abends", sub, 29)                                    # the team's passage in Anna's interview
    coder, mine = _coder(app, client, code, base)
    _code(coder, base, mine, "Manchmal", sub)                                       # Doll's own passage
    _set(client, base, exports=True, hidden=True)
    page = coder.get(f"{base}?view=categories").data.decode()
    assert "categories.csv" in page and "analysis.qdpx" in page and "categories.qdc" in page
    for url in FILES:
        assert coder.get(f"{base}/{url}").status_code == 200, url
    qde, sources = _qde(coder.get(f"{base}/analysis.qdpx"))
    assert len(sources) == 1                                                        # only Doll's own interview
    assert "Kodierende 1" in qde and "Doll" not in qde
    csv = coder.get(f"{base}/categories.csv").data.decode("utf-8-sig")
    assert "Erschöpfung" in csv and csv.splitlines()[1].count(";") > 5
    xlsx = coder.get(f"{base}/categories.xlsx").data
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        strings = "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".xml"))
    assert "Kodierende 1" in strings and "Doll" not in strings and "Abends bin ich" not in strings
    # the team's own files: everything, with names
    qde, sources = _qde(client.get(f"{base}/analysis.qdpx"))
    assert len(sources) == 2 and "Doll" in qde and "Kodierende 1" not in qde
    # names shown: the interviewer's files name the people
    _set(client, base, exports=True, hidden=False)
    qde, _sources = _qde(coder.get(f"{base}/analysis.qdpx"))
    assert "Doll" in qde and "Kodierende" not in qde
    # off again: 404, as before
    _set(client, base, exports=False, hidden=True)
    for url in FILES:
        assert coder.get(f"{base}/{url}").status_code == 404, url


def test_an_old_settings_form_leaves_the_setting_alone(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    coder, _mine = _coder(app, client, code, base)
    _set(client, base, exports=True, hidden=True)
    client.post(f"{base}/settings", data={"name": "Belastung im Alltag"})            # without the hidden field
    assert coder.get(f"{base}/categories.csv").status_code == 200
    assert bea.get(f"{base}/categories.csv").status_code == 404                     # members (accounts) as before
