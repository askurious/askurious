"""Interviews kodieren, comparing cases from codings (app/transcript_coding_cases.py): the case summaries with the main
categories of an analysis as columns, the passages per cell with their time mark, the draft from the passages, saving
under the analysis's key, grouping by a subcategory, the table "Fälle × Unterkategorien" and its CSV, the AI draft only
with an own access; the case summaries by Themenblock stay as they were."""
import json

from app import ai, transcript_coding_ai, transcript_coding_cases as cases
from app.extensions import db
from app.models import Response
from tests.interview.test_transcript_coding_review import TEXT, _code, _setup, _sub


def test_time_mark_and_draft():
    text = "B: Abends bin ich fertig. #00:04:02-7#\nI: Und dann?"
    assert cases.mark_after(text, text.index("fertig") + 6) == "00:04:02"
    assert cases.mark_after(text, len(text)) == ""
    items = [{"sub": "Erschöpfung", "quote": "Abends bin ich fertig"}, {"sub": "Erschöpfung", "quote": "müde"},
             {"sub": "", "quote": "x" * 200}]
    lines = cases.draft(items).split("\n")
    assert lines[0] == "Erschöpfung (2): „Abends bin ich fertig“" and lines[1].startswith("(Hauptkategorie) (1): „xxx")
    assert lines[1].endswith("…“")


def test_columns_cells_saving_grouping_and_types(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired, worry = _sub(client, base, main, "Erschöpfung"), _sub(client, base, main, "Zukunftssorgen")
    _code(client, base, rid, "Abends", tired, 29)
    _code(client, base, rid, "Manchmal", tired)
    page_url = f"/guide/{code}/interviews/summaries"
    plain = client.get(page_url).data.decode()
    assert "Themenblöcke des Leitfadens" in plain and "Fälle × Unterkategorien" not in plain   # as before by default
    page = client.get(f"{page_url}?auswertung={pid}").data.decode()
    key = f"c{pid}:{main}"
    assert f'data-summary-key="{key}"' in page and "aus Stellen vorbefüllen" in page and "Abends bin ich einfach fertig" in page
    assert "Erschöpfung (2)" in page and "Fälle × Unterkategorien" in page
    assert client.post(f"/guide/{code}/interviews/{rid}/summary", data={"key": key, "text": "Müdigkeit am Abend"}).status_code == 200
    assert client.post(f"/guide/{code}/interviews/{rid}/summary", data={"key": f"c{pid}:{tired}", "text": "x"}).status_code == 400  # a subcategory is no column
    assert client.post(f"/guide/{code}/interviews/{rid}/summary", data={"key": "c999:1", "text": "x"}).status_code == 400
    with app.app_context():
        assert db.session.get(Response, rid).interview["summaries"][key] == "Müdigkeit am Abend"
    grouped = client.get(f"{page_url}?auswertung={pid}&nach=sub{tired}").data.decode()
    assert "mit „Erschöpfung“ (1)" in grouped and "ohne „Erschöpfung“ (0)" in grouped and "1 mit Stellen" in grouped
    csv_text = client.get(f"{page_url}/types.csv?auswertung={pid}").data.decode("utf-8-sig")
    head, row = csv_text.splitlines()[:2]
    assert head.split(";")[1:] == ["Belastung / Erschöpfung", "Belastung / Zukunftssorgen"]
    assert row.split(";")[1:] == ["2", "0"]
    assert worry
    # the Word and CSV files of the case summaries by Themenblock are unchanged
    assert client.get(f"{page_url}.csv").status_code == 200


def test_ai_draft_only_with_an_own_access(app, client, monkeypatch):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(client, base, main, "Erschöpfung")
    _code(client, base, rid, "Abends", tired, 29)
    url = f"/guide/{code}/interviews/{rid}/summary/suggest"
    key = f"c{pid}:{main}"
    assert not client.post(url, data={"key": key}).json["ok"]                       # not allowed in the analysis
    assert "Zusammenfassung vorschlagen" not in client.get(f"/guide/{code}/interviews/summaries?auswertung={pid}").data.decode()
    client.post(f"{base}/ai", data={"ai_allowed": "1"})
    monkeypatch.setattr(transcript_coding_ai, "own_access", lambda who: object())
    calls = []
    monkeypatch.setattr(ai, "generate_text", lambda instructions, prompt, **kw: calls.append((kw.get("purpose"), json.loads(prompt))) or json.dumps({"summary": "Abends erschöpft."}))
    assert "Zusammenfassung vorschlagen" in client.get(f"/guide/{code}/interviews/summaries?auswertung={pid}").data.decode()
    r = client.post(url, data={"key": key})
    assert r.json == {"ok": True, "text": "Abends erschöpft."}
    assert calls[0][0] == "transcript_coding_summary" and calls[0][1]["quotes"][0]["quote"] == "Abends bin ich einfach fertig"
    assert TEXT
