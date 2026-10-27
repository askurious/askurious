"""Interviews kodieren, AI suggestions (app/transcript_coding_ai.py): off until the team allows them, only over an own AI
access, quotes anchored in the transcript or dropped, accepting, discarding and taking back, the mark "KI-Vorschlag"
in the coding guide and the export, and the paragraph in the interview consent template."""
import json

import pytest

from app import ai, transcript_coding_ai
from app.extensions import db
from app.models import CodingProject, Response
from tests.interview.test_transcript_coding_review import TEXT, _setup, _sub


@pytest.fixture
def own(monkeypatch):
    """An own AI access for everyone signed in; the AI answers what `answer` holds."""
    answer = {"passages": []}
    calls = []

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None, store=False, units=1):
        calls.append((purpose, json.loads(prompt)))
        return json.dumps(answer)

    monkeypatch.setattr(transcript_coding_ai, "own_access", lambda who: object() if who.user_id else None)
    monkeypatch.setattr(ai, "generate_text", fake)
    return answer, calls


def test_off_until_allowed_and_only_with_an_own_access(app, client, monkeypatch):
    code, pid, rid, base, main, bea = _setup(app, client)
    page = client.get(f"{base}/i/{rid}").data.decode()
    assert "Stellen vorschlagen" not in page and "KI-Vorschläge sind in dieser Auswertung erlaubt" not in page
    assert not client.post(f"{base}/i/{rid}/suggest", json={"section": 0}).json["ok"]
    client.post(f"{base}/ai", data={"ai_allowed": "1"})
    assert bea.post(f"{base}/ai", data={}).status_code == 404                         # only the team switches
    with app.app_context():
        assert db.session.get(CodingProject, pid).ai_allowed
    page = client.get(f"{base}/i/{rid}").data.decode()
    assert "KI-Vorschläge sind in dieser Auswertung erlaubt" in page                # no own access: a note
    monkeypatch.setattr(transcript_coding_ai, "own_access", lambda who: object())
    assert "Stellen vorschlagen" in client.get(f"{base}/i/{rid}").data.decode()


def test_suggest_accept_discard_and_take_back(app, client, own):
    answer, calls = own
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(client, base, main, "Erschöpfung", "müde durch die Pflege")
    worry = _sub(client, base, main, "Zukunftssorgen", "Sorgen um später")
    client.post(f"{base}/ai", data={"ai_allowed": "1"})
    answer["passages"] = [{"quote": "bin ich einfach fertig", "category": tired, "reason": "Erschöpfung am Abend"},
                          {"quote": "wie lange ich das noch schaffe", "category": worry, "reason": "Sorge"},
                          {"quote": "Das steht nicht im Text", "category": tired, "reason": "erfunden"},
                          {"quote": "fertig", "category": 99999, "reason": "keine Kategorie"}]
    r = client.post(f"{base}/i/{rid}/suggest", json={"section": 0})
    assert r.json["ok"] and r.json["total"] == 1 and len(r.json["suggestions"]) == 2
    purpose, prompt = calls[0]
    assert purpose == "transcript_coding_suggest" and prompt["transcript_section"] == TEXT
    assert {c["name"] for c in prompt["categories"]} >= {"Erschöpfung", "Zukunftssorgen"}
    quotes = {s["quote"] for s in r.json["suggestions"]}
    assert quotes == {"bin ich einfach fertig", "wie lange ich das noch schaffe"}
    # asking again gives nothing twice
    assert client.post(f"{base}/i/{rid}/suggest", json={"section": 0}).json["suggestions"] == []
    sugg = {s["cat"]: s["id"] for s in r.json["suggestions"]}
    made = client.post(f"{base}/i/{rid}/suggestions/accept", json={"ids": [sugg[tired]]}).json["codings"]
    assert made[0]["origin"] == "ai" and made[0]["accepted_by"] == "Philipp" and made[0]["cat"] == tired
    assert client.post(f"{base}/i/{rid}/suggestions/discard", json={"cat": worry}).json["ids"] == [sugg[worry]]
    with app.app_context():
        meta = db.session.get(Response, rid).interview
        assert [c["origin"] for c in meta["codings"]] == ["ai"]
        assert {s["id"]: (bool(s.get("accepted")), bool(s.get("discarded"))) for s in meta["suggestions"]} == {
            sugg[tired]: (True, False), sugg[worry]: (False, True)}
    # the mark in the coding guide and the export
    assert client.get(f"{base}/kodierleitfaden.docx").status_code == 200
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(client.get(f"{base}/analysis.qdpx").data)) as z:
        assert "KI-Vorschlag, übernommen von Philipp" in z.read("project.qde").decode("utf-8")
    # taking both back: the coding goes, the suggestions are open again
    client.post(f"{base}/i/{rid}/suggestions/undo", json={"ids": [sugg[tired], sugg[worry]]})
    with app.app_context():
        meta = db.session.get(Response, rid).interview
        assert meta["codings"] == [] and not any(s.get("accepted") or s.get("discarded") for s in meta["suggestions"])


def test_long_transcripts_in_sections(app, client, own):
    answer, calls = own
    text = ("B: " + "Ein Satz über den Alltag. " * 30 + "\n") * 40
    sections = transcript_coding_ai.sections(text)
    assert len(sections) > 1 and sections[0][0] == 0 and sections[-1][1] == len(text)
    assert all(e - s <= transcript_coding_ai.SECTION for s, e in sections)
    assert all(text[e - 1] == "\n" for s, e in sections[:-1])                       # cut at line ends


def test_consent_template_names_it(app, client):
    from app import interview_docx
    from app.models import Survey

    code, pid, rid, base, main, bea = _setup(app, client)
    with app.app_context():
        assert not interview_docx._ai_coding(db.session.scalar(db.select(Survey).filter_by(code=code)))
    client.post(f"{base}/ai", data={"ai_allowed": "1"})
    with app.app_context():
        assert interview_docx._ai_coding(db.session.scalar(db.select(Survey).filter_by(code=code)))
