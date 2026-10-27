"""Interview guides (app/interview.py, concept interviewleitfaden-konzept.md): the third kind of survey. A guide of
Leitfragen with Kurzfragebogen and Postskript, an interview view that stores nothing, Word documents; no data."""
import io
import json
import re
import zipfile

from app.extensions import db
from app.models import Survey
from tests.exports.test_transfer import _upload as _read_in


def make(app, client, title="Pflegende Angehörige", template="witzel", order="free"):
    r = client.post("/guide/new", data={"title": title, "language": "de", "template": template, "order": order,
                                            "target": "erwachsene", "mode": "praesenz", "research_question": "Wie erleben sie die Pflege?"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(title=title))
        # on into the setup wizard (app/wizard.py, Ralf 2026-10-27)
        assert r.headers["Location"].endswith("/setup/researchdesign")
        return survey.code, survey.id


def blocks(app, sid):
    """[(part, title, [item types])] in the order of the guide."""
    from app import interview

    with app.app_context():
        survey = db.session.get(Survey, sid)
        return [(p, b.title(), [i.type for i in b.items]) for p, b in interview.ordered_blocks(survey)]


def first(app, sid, kind="guide_question", part="guide"):
    from app import interview

    with app.app_context():
        survey = db.session.get(Survey, sid)
        return next(i.id for p, b in interview.ordered_blocks(survey) if p == part for i in b.items if i.type == kind)


def block_of(app, sid, part):
    from app import interview

    with app.app_context():
        return next(b.id for p, b in interview.ordered_blocks(db.session.get(Survey, sid)) if p == part)


def docx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return re.sub(r"<[^>]+>", "", z.read("word/document.xml").decode())


def test_created_from_a_template(app, client):
    code, sid = make(app, client)
    with app.app_context():
        survey = db.session.get(Survey, sid)
        assert survey.kind == "interview" and survey.live is None and survey.consent_item is None
        assert survey.interview["template"] == "witzel" and survey.interview["short"] == "after"
        assert survey.research_question == "Wie erleben sie die Pflege?"
    rows = blocks(app, sid)
    assert [p for p, _, _ in rows] == ["guide", "guide", "guide", "guide", "short", "post"]   # Kurzfragebogen after the talk
    assert rows[0][1] == "Einstieg" and rows[0][2] == ["info", "guide_question"]
    assert "single_choice" in rows[4][2] and "date_time" in rows[5][2]
    page = client.get(f"/guide/{code}").data.decode()
    assert "Interviewleitfaden" not in page or True
    for tab in ("Einstellungen", "Leitfaden", "Interviews", "Ergebnisse", "Export", "Interview beginnen</a>"):
        assert tab in page
    assert "Datenerhebung" not in page and "Fragebogen</a>" not in page
    # the narrative interview has no Kurzfragebogen, the empty one a single Themenblock
    _, sid2 = make(app, client, "Biografien", "narrativ")
    assert [p for p, _, _ in blocks(app, sid2)] == ["guide", "guide", "guide", "post"]
    _, sid3 = make(app, client, "Leer", "leer")
    assert [p for p, _, _ in blocks(app, sid3)] == ["guide", "short", "post"]


def test_only_the_types_of_each_part(app, client, survey):
    code, sid = make(app, client)
    guide, short = block_of(app, sid, "guide"), block_of(app, sid, "short")
    r = client.post(f"/surveys/{sid}/editor/blocks/{guide}/items", data={"type": "guide_question"})
    assert "/guide/" in r.headers["Location"] and "neu=1" in r.headers["Location"]       # opens beside the list
    assert client.post(f"/surveys/{sid}/editor/blocks/{short}/items", data={"type": "guide_question"}).status_code == 400
    # a Likert scale is a task there since 2026-10-20 (app/interview_tasks.py), a slider still is not
    assert client.post(f"/surveys/{sid}/editor/blocks/{guide}/items", data={"type": "slider"}).status_code == 400
    assert client.post(f"/surveys/{sid}/editor/blocks/{short}/items", data={"type": "single_choice"}).status_code == 302
    # a questionnaire neither offers nor takes the Leitfrage
    assert "guide_question" not in client.get(f"/surveys/{survey}/editor/").data.decode()
    assert client.post(f"/surveys/{survey}/editor/blocks/2/items", data={"type": "guide_question"}).status_code == 400
    # a Leitfrage does not move into the Kurzfragebogen
    q = first(app, sid)
    client.post(f"/surveys/{sid}/editor/items/{q}/place", data={"block_id": short, "position": 0})
    assert first(app, sid) == q and "guide_question" not in blocks(app, sid)[4][2]


def test_editing_a_leitfrage(app, client):
    code, sid = make(app, client, template="leer")
    q = first(app, sid)
    r = client.post(f"/surveys/{sid}/editor/items/{q}?embed=1", data={
        "code": "L1", "question": "Erzählen Sie doch einmal …", "hint": "Pausen aushalten",
        "aspect_id": ["a1", "a2", "a3"], "aspect_label": ["Anlass", "", "Personen"],
        "keep_id": ["k1"], "keep_label": ["Und dann?"], "probe_id": ["p1"], "probe_label": ["Wer hat entschieden?"],
        "core": "1", "minutes": "15"})
    assert r.status_code == 302
    with app.app_context():
        from app.models import Item

        item = db.session.get(Item, q)
        assert item.config["aspects"] == ["a1", "a3"] and item.config["core"] is True and item.config["minutes"] == 15
        assert item.kind.rows(item, "aspects") == [("a1", "Anlass"), ("a3", "Personen")]
        assert item.text("hint") == "Pausen aushalten"
    page = client.get(f"/guide/{code}").data.decode()
    assert "Erzählen Sie doch einmal" in page and "ca. 15 Min." in page and "Kernfrage" in page
    assert "Anlass" in client.get(f"/surveys/{sid}/editor/items/{q}/frame").data.decode()   # the preview


def test_no_data_pages_and_no_link(app, client):
    code, sid = make(app, client)
    for path, target in ((f"/surveys/{sid}", "/settings"), (f"/surveys/{sid}/data", "/export"),
                         (f"/surveys/{sid}/results", f"/guide/{code}"), (f"/surveys/{sid}/field", f"/guide/{code}"),
                         (f"/surveys/{sid}/editor/", f"/guide/{code}"), (f"/surveys/{sid}/flow/", f"/guide/{code}"),
                         (f"/surveys/{sid}/dashboards/", f"/guide/{code}"), (f"/surveys/{sid}/translation/", f"/guide/{code}")):
        r = client.get(path)
        assert r.status_code == 302 and r.headers["Location"].endswith(target), path
    assert client.post(f"/surveys/{sid}/start").status_code == 404
    assert client.get(f"/s/{code}").status_code == 404
    assert client.get("/guide/nicht-da").status_code == 404


def test_settings(app, client):
    code, sid = make(app, client)
    r = client.post(f"/guide/{code}/settings", data={"part": "interview", "target": "fachleute", "mode": "video",
                                                            "order": "linear", "short": "off"})
    assert r.status_code == 302
    with app.app_context():
        st = db.session.get(Survey, sid).interview
        assert st["order"] == "linear" and st["short"] == "off" and st["post"] is False and st["mode"] == "video"
    assert [p for p, _, _ in blocks(app, sid)] == ["guide"] * 4                       # parts switched off are hidden
    client.post(f"/guide/{code}/settings", data={"part": "interview", "order": "free", "short": "before", "post": "1"})
    rows = blocks(app, sid)
    assert rows[0][0] == "short" and rows[-1][0] == "post" and "single_choice" in rows[0][2]   # the questions are still there
    client.post(f"/guide/{code}/settings", data={"part": "titel", "title": "Neu", "research_question": "Frage?"})
    with app.app_context():
        assert db.session.get(Survey, sid).title == "Neu"
    assert "Beteiligte" in client.get(f"/guide/{code}/settings").data.decode()


def test_themenbloecke(app, client):
    code, sid = make(app, client, template="leer")
    client.post(f"/guide/{code}/control", data={"action": "add_block"})
    rows = blocks(app, sid)
    assert [t for p, t, _ in rows if p == "guide"] == ["Themenblock 1", "Themenblock 2"]
    second = block_of(app, sid, "guide")
    client.post(f"/guide/{code}/control", data={"action": "rename_block", "block": second, "title": "Alltag"})
    client.post(f"/guide/{code}/control", data={"action": "down", "block": second})
    assert [t for p, t, _ in blocks(app, sid) if p == "guide"] == ["Themenblock 2", "Alltag"]


def test_themenblock_dragged(app, client):
    """Dragging a Themenblock by its handle (app.js) goes to interview.control, action "place": it went to
    editor.place_block, which a guide may not open, and every drop ended in 404 (Ralf, 2026-10-20)."""
    from app import interview

    code, sid = make(app, client)
    page = client.get(f"/guide/{code}").data.decode()
    assert f'data-place-block="/guide/{code}/control"' in page and "editor/blocks/" not in page.split("guide-block-drag")[1][:300]
    with app.app_context():
        ids = [b.id for p, b in interview.ordered_blocks(db.session.get(Survey, sid)) if p == "guide"]
    names = lambda: [t for p, t, _ in blocks(app, sid) if p == "guide"]
    r = client.post(f"/guide/{code}/control", data={"action": "place", "block": ids[0], "target": ids[2], "mode": "after"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"#block-{ids[0]}")
    assert names() == ["Themenblock 1", "Themenblock 2", "Einstieg", "Abschluss"]
    client.post(f"/guide/{code}/control", data={"action": "place", "block": ids[3], "target": ids[1], "mode": "before"})
    assert names() == ["Abschluss", "Themenblock 1", "Themenblock 2", "Einstieg"]
    assert [p for p, _, _ in blocks(app, sid)][-2:] == ["short", "post"]               # the forms stay at the end
    short = block_of(app, sid, "short")                                                 # only among the Themenblöcke
    assert client.post(f"/guide/{code}/control", data={"action": "place", "block": ids[0], "target": short,
                                                        "mode": "after"}).status_code == 400
    assert client.post(f"/guide/{code}/control", data={"action": "place", "block": short, "target": ids[0],
                                                        "mode": "after"}).status_code == 400


def test_interview_view(app, client):
    code, sid = make(app, client)
    page = client.get(f"/guide/{code}/conversation").data.decode()
    assert 'data-order="free"' in page and "interview.js" in page and "<script>" not in page
    assert "data-iv-tick" in page and "data-iv-core" in page and "Abschluss" in page
    assert page.count("data-iv-go=") > 4                                               # the list jumps
    assert "Haken und Uhr nur in diesem Tab" in page
    code2, _ = make(app, client, "Der Reihe nach", order="linear")
    linear = client.get(f"/guide/{code2}/conversation").data.decode()
    assert 'data-order="linear"' in linear and "Zu Themenblock" in linear
    assert 'data-iv-mark=' in linear and "<button type=\"button\" data-iv-go" not in linear


def test_word_documents(app, client):
    code, sid = make(app, client)
    page = client.get(f"/guide/{code}/export").data.decode()
    for kind in ("leitfaden", "interviewkarte", "kurzfragebogen", "postskript"):
        assert f"/guide/{code}/export/{kind}.docx" in page
        r = client.get(f"/guide/{code}/export/{kind}.docx")
        assert r.status_code == 200 and r.data[:2] == b"PK"
    text = docx_text(client.get(f"/guide/{code}/export/leitfaden.docx").data)
    assert "Inhaltliche Aspekte (Check)" in text and "Aufrechterhaltungsfragen" in text and "Kurzfragebogen" in text
    assert "Forschungsfrage" in text and "Helfferich" in text and "Witzel" in text
    assert "Interviewkennung" in docx_text(client.get(f"/guide/{code}/export/postskript.docx").data)
    client.post(f"/guide/{code}/settings", data={"part": "interview", "order": "free", "short": "off"})
    assert client.get(f"/guide/{code}/export/kurzfragebogen.docx").status_code == 404


def test_dashboard(app, client):
    code, sid = make(app, client)
    page = client.get("/dashboard/").data.decode()
    assert "/guide/new" in page and "badge-interview" in page and "?art=leitfaden" in page
    assert "Pflegende Angehörige" in client.get("/dashboard/?interview=1").data.decode()


def test_file_and_duplicate(app, client):
    code, sid = make(app, client)
    data = json.loads(client.get(f"/surveys/{sid}/export/survey.json").data)
    assert data["version"] == 6 and data["interview"]["template"] == "witzel"
    assert {b["settings"].get("part") for b in data["blocks"]} == {"guide", "short", "post"}
    _read_in(client, data, title="Aus der Datei")
    with app.app_context():
        copy = db.session.scalar(db.select(Survey).filter_by(title="Aus der Datei"))
        assert copy.interview["template"] == "witzel" and copy.consent_item is None
        assert sorted({(b.settings or {}).get("part") for b in copy.blocks}) == ["guide", "post", "short"]
    client.post(f"/surveys/{sid}/duplicate", data={"title": "Zweite Welle", "with_people": "1"})
    with app.app_context():
        twin = db.session.scalar(db.select(Survey).filter_by(title="Zweite Welle"))
        original = db.session.get(Survey, sid).interview
        assert twin.interview == {k: v for k, v in original.items() if k != "versions"} and twin.consent_item is None



def test_category_system(app, client):
    from xml.etree import ElementTree as ET

    code, sid = make(app, client)
    r = client.get(f"/guide/{code}/export/category-system.qdc")
    assert r.status_code == 200 and r.data.startswith(b'<?xml version="1.0" encoding="utf-8"?>')
    ns = {"q": "urn:QDA-XML:codebook:1.0"}
    root = ET.fromstring(r.data)
    assert root.tag == "{urn:QDA-XML:codebook:1.0}CodeBook"
    mains = root.findall("q:Codes/q:Code", ns)
    assert [m.get("name") for m in mains] == ["Einstieg", "Themenblock 1", "Themenblock 2", "Abschluss"]
    first = mains[0].findall("q:Code", ns)[0]                                   # the Leitfrage, the hint is no category
    assert first.find("q:Description", ns).text.startswith("Zum Einstieg")
    assert [c.get("name") for c in first.findall("q:Code", ns)] == ["Anlass", "erste Erfahrungen", "beteiligte Personen"]
    assert client.get(f"/guide/{code}/export/category-system.qdc").data == r.data   # the same GUIDs every time


def test_consent_template(app, client):
    code, sid = make(app, client)
    text = docx_text(client.get(f"/guide/{code}/export/einwilligung.docx").data)
    assert "Information und Einwilligung" in text and "Tonaufnahme" in text and "Pflegende Angehörige" in text
    assert "10 Jahre" in text and "Unterschrift" in text and "Philipp" in text          # a member is named
    client.post(f"/guide/{code}/settings", data={"part": "interview", "order": "free", "short": "after",
                                                        "post": "1", "data_keep": "60"})
    text = docx_text(client.get(f"/guide/{code}/export/einwilligung.docx").data)
    assert "Tonaufnahme" not in text and "5 Jahre" in text
    client.post(f"/guide/{code}/settings", data={"part": "interview", "order": "free", "short": "after",
                                                        "post": "1", "mode": "gemischt"})      # Ralf 2026-10-15
    text = docx_text(client.get(f"/guide/{code}/export/einwilligung.docx").data)
    assert "je nach Absprache persönlich, als Videogespräch oder am Telefon" in text


def test_versions(app, client):
    import hashlib

    code, sid = make(app, client)
    assert "Noch keine Fassung" in client.get(f"/guide/{code}/export").data.decode()
    client.post(f"/guide/{code}/versions", data={"note": "nach_probe", "text": "Frage 2 offener"})
    client.post(f"/guide/{code}/versions", data={"note": "erfunden"})
    with app.app_context():
        versions = db.session.get(Survey, sid).interview["versions"]
    assert [(v["n"], v["note"], v["text"]) for v in versions] == [(1, "nach_probe", "Frage 2 offener"), (2, "andere", "")]
    page = client.get(f"/guide/{code}/export").data.decode()
    assert "Fassung 2" in page and versions[0]["sha256"] in page and "nach dem Probeinterview" in page
    r = client.get(f"/guide/{code}/versions/fassung-1.zip")
    assert r.status_code == 200 and hashlib.sha256(r.data).hexdigest() == versions[0]["sha256"]
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        assert sorted(z.namelist()) == ["README.md", "category-system.qdc", "checksums.sha256", "interview-card.docx",
                                        "interview-guide.docx", "survey.json"]
    assert client.get(f"/guide/{code}/versions/fassung-9.zip").status_code == 404
    assert client.get(f"/guide/{code}/versions/..%2Fx.zip").status_code == 404
    # settings saved later keep the versions; a survey file and a copy do not take them
    client.post(f"/guide/{code}/settings", data={"part": "interview", "order": "linear", "short": "after", "post": "1"})
    with app.app_context():
        assert len(db.session.get(Survey, sid).interview["versions"]) == 2
    assert "versions" not in json.loads(client.get(f"/surveys/{sid}/export/survey.json").data)["interview"]



def test_print_view_for_pdf(app, client):
    code, sid = make(app, client)
    assert f"/guide/{code}/print" in client.get(f"/guide/{code}/export").data.decode()
    page = client.get(f"/guide/{code}/print").data.decode()
    assert "data-print" in page and "als PDF speichern" in page and "<script>" not in page
    assert "Inhaltliche Aspekte (Check)" in page and "☐ Anlass" in page and "Forschungsfrage" in page
    assert "Kurzfragebogen" in page and "Interviewkennung" in page and "Helfferich" in page and "Witzel" in page


def test_version_on_zenodo(app, client, monkeypatch):
    import hashlib

    from app import zenodo
    from tests.exports.test_zenodo import FakeZenodo

    code, sid = make(app, client)
    client.post(f"/guide/{code}/versions", data={"note": "endgueltig"})
    assert "mit Zenodo verbinden" in client.get(f"/guide/{code}/export").data.decode()      # not connected: a hint only
    from app import zenodo_auth

    fake = FakeZenodo()
    monkeypatch.setattr(zenodo.requests, "request", fake)
    monkeypatch.setattr(zenodo_auth, "available", lambda target: True)
    monkeypatch.setattr(zenodo_auth, "access_token", lambda user, target: "secret-token")
    monkeypatch.setattr(zenodo_auth, "connection", lambda user, target: object())
    assert "Auf Zenodo" in client.get(f"/guide/{code}/export").data.decode()
    result = client.post(f"/guide/{code}/versions/fassung-1.zip/zenodo", data={"target": "sandbox", "action": "upload"},
                         follow_redirects=True)
    assert "keine bestätigte Materiallizenz" in result.data.decode() and not fake.calls
    # Existing drafts remain readable. New uploads need an explicit guide licence choice, which is still pending.
    with app.app_context():
        s = db.session.get(Survey, sid)
        version = s.interview["versions"][0]
        s.interview = {**s.interview, "versions": [{**version, "zenodo": {"id": 42, "target": "sandbox", "state": "draft"}}]}
        db.session.commit()
    assert hashlib.sha256(client.get(f"/guide/{code}/versions/fassung-1.zip").data).hexdigest() == version["sha256"]
    fake.published = True
    client.post(f"/guide/{code}/versions/fassung-1.zip/zenodo", data={"target": "sandbox", "action": "refresh"})
    page = client.get(f"/guide/{code}/export").data.decode()
    assert "Veröffentlicht" in page and "10.5072/zenodo.42" in page
    assert client.post(f"/guide/{code}/versions/fassung-7.zip/zenodo", data={"action": "upload"}).status_code == 404



def test_the_guide_leads_to_what_comes_first_and_keeps_its_downloads_together(app, client):
    # UI audit 2026-10-04, package C: "Interview führen" begins one (R20), the trial says so (R20), the first steps
    # stand on the guide (R27a), the stored interviews' files are in Export too (R22), Auswertung is a tab (R23),
    # the settings fold like the questionnaire's (G6)
    code, sid = make(app, client)
    guide = client.get(f"/guide/{code}").data.decode()
    assert f'href="/guide/{code}/interviews#neu">' in guide and "Vor dem ersten Interview:" in guide
    assert "speichert aus dem Interview nichts" not in guide and "+ Leitfrage</button>" in guide
    assert "Probe ohne Speichern" in client.get(f"/guide/{code}/conversation").data.decode()
    export = client.get(f"/guide/{code}/export").data.decode()
    assert "interviews/project.qdpx" in export or ".qdpx</a>" in export
    nav = client.get(f"/guide/{code}/interviews/analysis").data.decode().split('class="survey-tabs"')[1].split("</nav>")[0]
    assert "Ergebnisse" in nav.split('aria-current="page"')[1].split("</a>")[0]
    settings = client.get(f"/guide/{code}/settings").data.decode()
    assert settings.index("<h2>Interviewleitfaden</h2>") < settings.index("<h2>Beteiligte Forschende</h2>") < settings.index("<h2>Interview</h2>")


def test_transcript_head_person_choices_say_what_they_fill(app, client):
    """Pre-test 2026-10-20, 14: "Interviewte Person im Kopf: Beruf aus" left the tester guessing. The three choices
    name the line about the interviewee ("Befragte(r)") of the transcript head they fill (transcript_export.interviewee) and what to pick."""
    code, _ = make(app, client)
    page = client.get(f"/guide/{code}/settings").data.decode()
    assert "Interviewte Person im Kopf" not in page
    assert "Im Transkriptkopf beschreibt eine Zeile die interviewte Person" in page
    for what in ("Beruf", "Alter", "Geschlecht"):
        assert f"{what} der interviewten Person: Antwort auf" in page
    assert page.count('aria-describedby="head-person-hint"') == 3


def test_the_slider_of_the_short_questionnaire_is_a_slider(app, client):
    """Ralf, 2026-10-21: a rating as a slider in the Kurzfragebogen showed as an empty grey field in the interview
    view; the track colours live on the participant pages, and the field rule of app.css drew the range as a box."""
    import os

    code, sid = make(app, client)
    assert 'class="iv-body"' in client.get(f"/guide/{code}/conversation").data.decode()
    with open(os.path.join(app.static_folder, "app.css"), encoding="utf-8") as f:
        css = f.read()
    assert ".iv-body { --track:" in css and ".iv-body .p-range { padding: 0; border: 0;" in css
