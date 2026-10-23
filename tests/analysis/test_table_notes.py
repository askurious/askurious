"""Administration > Datenbank explains every table in a few words (Philipp, 2026-10-06)."""
from app.extensions import db
from app.table_notes import NOTES


def test_every_table_has_a_note():
    missing = sorted(set(db.metadata.tables) - set(NOTES))
    assert not missing, "Tabellen ohne Erklärung in app/table_notes.py: " + ", ".join(missing)
    assert all(text and len(text) < 120 for text in NOTES.values())


def test_the_database_page_shows_them(app, client):
    page = client.get("/admin/database").data.decode()
    assert "<th>Inhalt</th>" in page and NOTES["response"] in page
