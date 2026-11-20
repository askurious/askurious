"""Interviews kodieren, AI suggestions (app/transcript_coding_ai.py): off until the team allows them, only over an own AI
access, quotes anchored in the transcript or dropped, accepting, discarding and taking back, the mark "KI-Vorschlag"
in the coding guide and the export, and the paragraph in the interview consent template."""
import json

import pytest

from app import ai, transcript_coding_ai
from app.extensions import db
from app.models import CodingProject, Response
from tests.interview.test_transcript_coding import _transcript
from tests.interview.test_transcript_coding_review import TEXT, _sub
from tests.interview.test_transcript_coding_review import _setup as _unchecked_setup


def _setup(app, client):
    """As _unchecked_setup, with the transcript checked: suggestions only come for checked transcripts."""
    out = _unchecked_setup(app, client)
    _transcript(app, out[2], TEXT, checked="2026-11-01T10:00:00")
    return out


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


def test_only_a_checked_transcript_goes_to_the_ai_and_masked(app, client, own):
    """A draft may still carry names and places: no suggestions before it is checked (409, no call to the AI); a
    checked one goes with contact details masked, as every other participants' text."""
    answer, calls = own
    code, pid, rid, base, main, bea = _unchecked_setup(app, client)
    _sub(client, base, main, "Erschöpfung", "müde")
    client.post(f"{base}/ai", data={"ai_allowed": "1"})
    page = client.get(f"{base}/i/{rid}").data.decode()
    assert "erst, wenn das Transkript geprüft ist" in page and "Stellen vorschlagen" not in page
    r = client.post(f"{base}/i/{rid}/suggest", json={"section": 0})
    assert r.status_code == 409 and not r.json["ok"] and calls == []
    text = TEXT + " Schreiben Sie mir an anna.neu@example.org."
    _transcript(app, rid, text, checked="2026-11-01T10:00:00")
    assert client.post(f"{base}/i/{rid}/suggest", json={"section": 0}).json["ok"]
    sent = calls[-1][1]["transcript_section"]
    assert "anna.neu@example.org" not in sent and "[EMAIL]" in sent and "Abends bin ich einfach fertig" in sent


def test_anchor_examples_go_only_from_checked_transcripts_and_masked(app, client, own):
    """Audit 2026-11-20: the anchor example of a category went to the AI as it stood, also when it was coded in a
    transcript nobody had checked yet. Now only the anchor of a checked interview goes, its contact details masked."""
    from tests.interview.test_interviewer import add as add_interviewer
    from tests.interview.test_interviewer import begin, open_link
    from tests.interview.test_transcript_coding_review import _code

    answer, calls = own
    code, pid, rid, base, main, bea = _unchecked_setup(app, client)
    draft = TEXT + " Mein Chef Herr Maier, maier@example.org, sagt das auch."
    _transcript(app, rid, draft)                                              # Anna's interview: a draft, not checked
    sub = _sub(client, base, main, "Erschöpfung", "müde")
    start = draft.index("Mein Chef")
    r = client.post(f"{base}/i/{rid}/codings", json={"start": start, "end": len(draft), "cat": sub})
    assert client.post(f"{base}/i/{rid}/codings/{r.json['coding']['id']}/anchor").status_code == 200
    other = begin(open_link(app, code, add_interviewer(client, code, "Bert Zwei"))[0], code)
    _transcript(app, other, TEXT, checked="2026-11-01T10:00:00")              # a second interview, checked
    client.post(f"{base}/ai", data={"ai_allowed": "1"})
    assert client.post(f"{base}/i/{other}/suggest", json={"section": 0}).json["ok"]
    cats = {c["id"]: c for c in calls[-1][1]["categories"]}
    assert "anchor_example" not in cats[sub] and "Maier" not in json.dumps(calls[-1][1], ensure_ascii=False)
    _transcript(app, rid, draft, checked="2026-11-02T10:00:00")               # now checked: the anchor goes, masked
    assert client.post(f"{base}/i/{other}/suggest", json={"section": 0}).json["ok"]
    anchor = {c["id"]: c for c in calls[-1][1]["categories"]}[sub]["anchor_example"]
    assert "Mein Chef" in anchor and "maier@example.org" not in anchor and "[EMAIL]" in anchor


def test_listed_names_and_the_interviewers_name_are_replaced_before_sending(app, client, own):
    """Ralf 2026-11-20 (audit, "b as the next step"): the team lists names of people, places and employers in the
    settings of the analysis; they, and the name of whoever conducted the interview, go to the AI as [NAME], in the
    section, in an anchor example and in the reason that comes back. What is not listed stays."""
    from tests.interview.test_transcript_coding_review import _code

    answer, calls = own
    code, pid, rid, base, main, bea = _unchecked_setup(app, client)
    text = TEXT + " Mein Chef Herr Maier in Würselen sagt das auch, und Anna Neu hat genickt. Frau Schmitz nicht."
    _transcript(app, rid, text, checked="2026-11-01T10:00:00")
    sub = _sub(client, base, main, "Erschöpfung", "müde")
    client.post(f"{base}/ai", data={"ai_allowed": "1"})
    page = client.get(f"{base}?view=settings").data.decode()
    assert "Vor dem Senden ersetzen" in page
    r = client.post(f"{base}/ai/mask", data={"ai_mask": "Maier\n  Würselen \nmaier\nx\n"}, follow_redirects=True)
    assert "2 Namen werden vor dem Senden ersetzt" in r.data.decode()
    with app.app_context():
        assert transcript_coding_ai.mask_terms(db.session.get(CodingProject, pid)) == ["Maier", "Würselen"]
    start = text.index("Mein Chef")
    cid = client.post(f"{base}/i/{rid}/codings", json={"start": start, "end": start + 40, "cat": sub}).json["coding"]["id"]
    assert client.post(f"{base}/i/{rid}/codings/{cid}/anchor").status_code == 200
    answer["passages"] = [{"quote": "Abends bin ich einfach fertig", "category": sub, "reason": "Herr Maier ist müde"}]
    out = client.post(f"{base}/i/{rid}/suggest", json={"section": 0}).json
    sent = calls[-1][1]
    section = sent["transcript_section"]
    assert "Maier" not in section and "Würselen" not in section and "[NAME]" in section
    assert "Anna Neu" not in section and "Anna" not in section          # who conducted it (the interviewer link's name)
    assert "Schmitz" in section                                           # not listed: stays
    anchor = {c["id"]: c for c in sent["categories"]}[sub]["anchor_example"]
    assert "Maier" not in anchor and "[NAME]" in anchor
    assert out["suggestions"][0]["reason"] == "Herr [NAME] ist müde"


def test_only_the_team_changes_the_list_and_it_shows_only_with_ai_allowed(app, client, own):
    code, pid, rid, base, main, bea = _setup(app, client)
    assert 'id="ai-mask"' not in client.get(f"{base}?view=settings").data.decode()          # AI not allowed yet
    assert bea.post(f"{base}/ai/mask", data={"ai_mask": "Maier"}).status_code == 404            # a member, not the team
    with app.app_context():
        assert transcript_coding_ai.mask_terms(db.session.get(CodingProject, pid)) == []
        assert transcript_coding_ai.read_mask_terms("a\nMaier\nmaier\n" + "\n".join(f"Name{i}" for i in range(400)))[:1] == ["Maier"]
        assert len(transcript_coding_ai.read_mask_terms("\n".join(f"Name{i}" for i in range(400)))) == transcript_coding_ai.MASK_TERMS
