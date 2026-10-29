"""Lernhilfen for "Interviews kodieren" (app/learning.py; Ralf 2026-10-29): definition, anchor example and coding rule
at the new category, which passage to take as anchor (also in the coding view), fixed or open main categories in the
settings, why locations say "Pos." at the Excel export, the second coding at "Doppelkodierung zuweisen", and κ with
its glossary entry on the pages of agreement."""
from app import learning
from tests.interview.test_transcript_coding import _transcript
from tests.interview.test_transcript_coding_review import TEXT, _setup


def test_the_coding_pages_explain_their_decisions(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    _transcript(app, rid, TEXT)
    project = client.get(f"{base}?view=categories").data.decode()
    assert "Warum Definition, Ankerbeispiel und Kodierregel?" in project and "Welche Stelle als Ankerbeispiel?" in project
    assert "Mayring, P. (2022)" in project and "Kuckartz, U., &amp; Rädiker, S. (2022)" in project
    settings = client.get(f"{base}?view=settings").data.decode()
    assert "Hauptkategorien festlegen oder aus dem Material bilden?" in settings
    assert "Warum eine zweite Person kodieren lassen?" in client.get(base).data.decode()   # at "Doppelkodierung zuweisen"
    assert "Welche Stelle als Ankerbeispiel?" in client.get(f"{base}/i/{rid}").data.decode()
    assert "Warum Fundstellen mit „Pos.“?" in client.get(f"{base}/overview").data.decode()
    assert "Warum Fundstellen mit „Pos.“?" not in bea.get(f"{base}/overview").data.decode()  # no export, no fold
    assert learning.REASON_PHASES["anchor_choice"] == 2 and learning.REASON_FEATURES["position"] == "transcript_coding"


def test_kappa_has_its_glossary_entry():
    t = learning.term("kappa")
    assert t["symbol"] == "κ" and "Landis" in t["text"] and len(t["sources"]) == 2
    assert any("kappa" in keys for _title, _note, keys in learning.SECTIONS)
