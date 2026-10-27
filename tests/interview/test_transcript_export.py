"""The transcript as Word and PDF with head and numbered lines, and all of them as a ZIP (app/transcript_export.py,
Ralf 2026-10-18, thread "Transkript-Export", all points as recommended)."""
import io
import re
import zipfile

from app.extensions import db
from app.models import Response, Survey
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start

TEXT = ("I: Wie geht es Ihnen heute, nach allem, was in den letzten Wochen passiert ist? #00:00:04-0#\n\n"
        "B: Sehr gut. Danke der Nachfrage. " + "Und dann war da noch etwas. " * 8 + "#00:42:13-0#\n\n"
        "I: Danke. #00:42:20-0#")


def _setup(app, client, checked=True):
    from app import interview

    code, sid = make(app, client)
    rid = start(app, client, code, label="KFZ_42")
    with app.app_context():
        short = interview.shown_form(db.session.get(Survey, sid), "short")
        age = next(i for i in short.items if i.type == "text_input")
        gender = next(i for i in short.items if i.type == "single_choice")
        option = gender.config["options"][0]["code"]
        age_code, gender_code = age.code, gender.code
    client.post(f"/guide/{code}/interviews/{rid}/form", data={f"i{age.id}": "42", f"i{gender.id}": option})
    client.post(f"/guide/{code}/settings", data={"part": "interview", "order": "free", "short": "after", "post": "1",
                                                 "head_title": "Methodenseminar", "head_age": age_code,
                                                 "head_gender": gender_code})
    data = {"text": TEXT, "checked_shown": "1"}
    if checked:
        data["checked"] = "1"
    client.post(f"/guide/{code}/interviews/{rid}/transcript", data=data)
    return code, sid, rid


def _xml(data, part="word/document.xml"):
    return zipfile.ZipFile(io.BytesIO(data)).read(part).decode("utf-8")


def test_word_has_head_numbering_and_bold_interviewer(app, client):
    code, sid, rid = _setup(app, client)
    r = client.get(f"/guide/{code}/interviews/{rid}/transcript/export?format=docx")
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith("-KFZ_42-transkript.docx")
    doc = _xml(r.data)
    assert '<w:lnNumType w:countBy="1" w:distance="284" w:restart="continuous"/>' in doc
    assert "Kürzel: KFZ_42" in doc and "Dauer der Aufnahme: 00:42:20" in doc and "Datum und Ort der Aufnahme: 12.10.2026" in doc
    assert re.search(r"Befragte\(r\): 42, \w+", doc)
    assert "Transkribiert von: Philipp<" in doc and "geprüft von" not in doc and "Dresing &amp; Pehl" in doc
    assert "#00:" not in doc                                    # time marks left out by default
    first = doc.split("Wie geht es")[0].rsplit("<w:p>", 1)[-1]
    assert "<w:b/>" in first and "<w:tab/>" in first             # I: bold, then a tab
    second = doc.split("Sehr gut.")[0].rsplit("<w:p>", 1)[-1]
    assert "<w:b/>" not in second
    styles = _xml(r.data, "word/styles.xml")
    head_style = styles.split('w:styleId="Transkriptkopf"')[1].split("</w:style>")[0]
    assert "suppressLineNumbers" in head_style and "Tahoma" in head_style
    assert "Courier New" in styles.split('w:styleId="Normal"')[1].split("</w:style>")[0]
    header = _xml(r.data, "word/header1.xml") if "word/header1.xml" in zipfile.ZipFile(io.BytesIO(r.data)).namelist() \
        else next(_xml(r.data, n) for n in zipfile.ZipFile(io.BytesIO(r.data)).namelist() if "header" in n)
    assert "Methodenseminar_KFZ_42" in header and "Entwurf" not in header
    marked = _xml(client.get(f"/guide/{code}/interviews/{rid}/transcript/export?format=docx&marks=1").data)
    assert "#00:00:04-0#" in marked


def test_pdf_numbers_every_printed_line(app, client):
    import pypdfium2

    code, sid, rid = _setup(app, client, checked=False)
    r = client.get(f"/guide/{code}/interviews/{rid}/transcript/export?format=pdf")
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    text = pypdfium2.PdfDocument(r.data)[0].get_textpage().get_text_range()
    assert "Methodenseminar_KFZ_42 (Entwurf)" in text and "Entwurf, nicht geprüft" in text
    body = text.split("Pehl (2018)", 1)[1]                      # after the head (the page number stands before it)
    numbers = [int(m) for m in re.findall(r"^\s*(\d+)\s", body, re.M)]
    assert numbers[:6] == [1, 2, 3, 4, 5, 6]                     # the long answer wraps over several numbered lines
    assert "#00:" not in text


def test_head_changes_are_kept_and_follow_the_automatic_values(app, client):
    from app import transcript_export

    code, sid, rid = _setup(app, client, checked=False)
    with app.app_context():
        survey, row = db.session.get(Survey, sid), db.session.get(Response, rid)
        head = {k: (v, a) for k, _, v, a in transcript_export.head(survey, row, "de")}
    form = {"text": TEXT, "checked_shown": "1", "checked": "1", "head_shown": "1"}
    for key, (value, auto) in head.items():
        form[f"head_{key}"], form[f"auto_{key}"] = value, auto
    form["head_datum"] = "12.10.2026, Aachen"
    client.post(f"/guide/{code}/interviews/{rid}/transcript", data=form)   # ticks "geprüft" with stale "am" in the page
    with app.app_context():
        survey, row = db.session.get(Survey, sid), db.session.get(Response, rid)
        assert row.interview["transcript_head"] == {"datum": "12.10.2026, Aachen"}
        values = {k: v for k, _, v, _ in transcript_export.head(survey, row, "de")}
        assert values["datum"] == "12.10.2026, Aachen" and "Entwurf" not in values["am"]
    page = client.get(f"/guide/{code}/interviews/{rid}/transcript").data.decode()
    assert 'name="head_datum" value="12.10.2026, Aachen"' in page and "Zeitmarken behalten" in page


def test_all_transcripts_as_zip(app, client):
    code, sid, rid = _setup(app, client)
    start(app, client, code, label="ohne")                        # no transcript: no file
    r = client.get(f"/guide/{code}/interviews/transcripts.zip?format=both")
    names = sorted(zipfile.ZipFile(io.BytesIO(r.data)).namelist())
    assert names == [f"{code}-KFZ_42-transkript.docx", f"{code}-KFZ_42-transkript.pdf"]
    assert "transcripts.zip" in client.get(f"/guide/{code}/export").data.decode()


def test_turns_keep_speakers_and_drop_marks():
    from app.transcript_export import turns

    assert turns("I: Frage? #00:00:01-0#\n\nB: Antwort. #00:00:05-0#\n[Teil 2 fehlt]") == [
        ("I", "Frage?", True), ("B", "Antwort.", False), ("", "[Teil 2 fehlt]", False)]


def test_transcribed_by_names_the_checker_and_the_machine(monkeypatch):
    """Ralf 2026-10-18: "Transkribiert von: geprüft von …" read oddly; now the person, and the machine with "mit"."""
    from types import SimpleNamespace

    from app import transcript_export

    monkeypatch.setattr(transcript_export, "interviewee", lambda *a: "")

    def by(**tr):
        return transcript_export.automatic(None, SimpleNamespace(interview={"transcript": tr}), "de")["von"]

    assert by(checked="2026-10-18", checked_by="Philipp", model="large-v3") == "Philipp, mit Whisper (large-v3)"
    assert by(checked="2026-10-18", checked_by="Philipp", source="upload", model="") == "Philipp"
    assert by(model="large-v3") == "Whisper (large-v3)"


def test_a_viewer_gets_no_transcript(app, client):
    """The transcript holds what was said (raw data): "Ansehen" is refused like every export of answers
    (tests/exports/test_exports_audit_findings.py, RAW_ELSEWHERE)."""
    from app.models import SurveyMember, User

    code, sid, rid = _setup(app, client)
    viewer = app.test_client()
    viewer.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Viewer")
    viewer.post("/terms/", data={"accept": "1"})
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0002-1825-0097"))
        db.session.add(SurveyMember(survey_id=sid, user=user, name="Viewer", role="viewer"))
        db.session.commit()
    r = viewer.get(f"/guide/{code}/interviews/{rid}/transcript/export?format=docx")
    assert r.status_code in (302, 403, 404) and "attachment" not in r.headers.get("Content-Disposition", "")
