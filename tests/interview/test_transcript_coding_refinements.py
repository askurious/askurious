"""Interviews kodieren, small refinements (Ralf 2026-10-29): quotes in exports without Dresing & Pehl time marks,
placed() worked out once per request, the location on "Stellen je Kategorie", and up to two further examples of a
category (chosen in the coding view, else the first passages) in the coding guide and the Excel export."""
import io
import re
import zipfile

from app import transcript_coding_view
from app.extensions import db
from app.models import CodingCategory
from app.transcript_coding_view import clean_quote
from tests.interview.test_transcript_coding import _transcript
from tests.interview.test_transcript_coding_categories_xlsx import _sheets
from tests.interview.test_transcript_coding_review import _setup, _sub

TEXT = ("I: Wie ist es? #00:00:04-0#\n\n"
        "B: Ich bin so müde. #00:14:32-0#\n\n"
        "B: Wieder müde, jeden Tag. #00:15:01-0#\n\n"
        "B: Noch einmal müde am Abend.")


def _code(who, base, rid, word, cat, length):
    start = TEXT.index(word)
    r = who.post(f"{base}/i/{rid}/codings", json={"start": start, "end": start + length, "cat": cat})
    assert r.json["ok"], r.json
    return r.json["coding"]["id"]


def test_clean_quote():
    assert clean_quote("Ich bin so müde. #00:14:32-0#") == "Ich bin so müde."
    assert clean_quote("eins #0:01:02-0#\nzwei") == "eins zwei"


def test_examples_quotes_cache_and_location(app, client, monkeypatch):
    code, pid, rid, base, main, bea = _setup(app, client)
    _transcript(app, rid, TEXT)
    cat = _sub(client, base, main, "Müdigkeit")
    first = _code(client, base, rid, "Ich bin so", cat, len("Ich bin so müde. #00:14:32-0#"))   # with the time mark
    second = _code(client, base, rid, "Wieder", cat, len("Wieder müde, jeden Tag."))
    third = _code(client, base, rid, "Noch einmal", cat, len("Noch einmal müde am Abend."))
    assert client.post(f"{base}/i/{rid}/codings/{first}/anchor").json["ok"]
    # without a choice: the first passages besides the anchor
    sheets = _sheets(client.get(f"{base}/categories.xlsx").data)
    system, passages = sheets[0][1], sheets[1][1]
    row = next(r for r in system if r[2] == "Müdigkeit")
    assert system[0][6] == "Weitere Beispiele"
    assert row[4] == "Ich bin so müde." and "#00:14:32" not in row[4]                # the anchor without its mark
    assert row[6].split("\n") == [f"„Wieder müde, jeden Tag.“ ({_label(app, rid)}, Pos. 3, 00:15:01)",
                                  f"„Noch einmal müde am Abend.“ ({_label(app, rid)}, Pos. 4)"]
    assert all("#00:" not in r[4] for r in passages[1:])                         # quotes without time marks
    # chosen in the coding view: the chosen one first; at most two; the members without the right get a refusal
    assert client.post(f"{base}/i/{rid}/codings/{third}/example").json["chosen"]
    assert bea.post(f"{base}/i/{rid}/codings/{second}/example").status_code == 403
    with app.app_context():
        assert [e["coding"] for e in db.session.get(CodingCategory, cat).examples] == [third]
    guide = client.get(f"{base}/kodierleitfaden.docx")
    with zipfile.ZipFile(io.BytesIO(guide.data)) as z:
        text = re.sub(r"<[^>]+>", "", z.read("word/document.xml").decode())
    assert "Weitere Beispiele: „Noch einmal müde am Abend.“" in text and "„Wieder müde, jeden Tag.“" in text
    assert "#00:14:32-0#" not in text
    # removing the coded passage takes the example with it
    client.post(f"{base}/i/{rid}/codings/{third}/delete")
    with app.app_context():
        assert db.session.get(CodingCategory, cat).examples is None
    # "Stellen je Kategorie" names the location and links to the passage
    page = client.get(f"{base}/passages?cat={cat}").data.decode()
    assert f"{_label(app, rid)}, Pos. 3, 00:15:01" in page and "at=" in page
    # placed() once per request: the overview places each passage once although several parts ask
    calls = []
    real = transcript_coding_view.place
    monkeypatch.setattr(transcript_coding_view, "place", lambda text, c: calls.append(c["id"]) or real(text, c))
    assert client.get(f"{base}/overview").status_code == 200
    assert sorted(calls) == sorted(set(calls)) and len(calls) == 2              # first and second, each once


def _label(app, rid):
    from app.models import Response

    with app.app_context():
        return db.session.get(Response, rid).interview["label"]
