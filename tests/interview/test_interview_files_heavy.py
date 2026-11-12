"""The evaluations and files of the interviews take a place among the heavy evaluations (app/heavy.py, @limited):
the overview and the map of an analysis, its Excel, CSV, QDPX and coding guide, the case overview, notes and
transcripts. Before, they ran unchecked in the researchers' worker (up to minutes of CPU and 730 MB for a large
project; performance audit 2026-11-11). Without a free place they answer 503 at once."""
import pytest

from app import heavy
from tests.interview.test_transcript_coding_review import _setup

PATHS = ("overview", "map.svg", "map.png", "map.pdf", "categories.xlsx", "categories.csv", "analysis.qdpx",
         "kodierleitfaden.docx")
GUIDE = ("cases.csv", "project.qdpx", "notes.docx", "notes.zip", "transcripts.zip")


@pytest.mark.parametrize("path", PATHS + GUIDE)
def test_without_a_free_place_the_file_waits_its_turn(app, client, monkeypatch, path):
    code, pid, rid, base, main, bea = _setup(app, client)
    url = f"/guide/{code}/interviews/{path}" if path in GUIDE else f"{base}/{path}"
    monkeypatch.setattr(heavy, "hold", lambda *a, **k: False)
    assert client.get(url).status_code == 503
