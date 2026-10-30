"""The note on the whole interview (Ralf 2026-10-30): one field in the interview view that stays on every page, beside
the notes per Leitfrage; stored with the interview, shown on the analysis page and in the notes exports."""
import json

from app.extensions import db
from app.models import Response
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start


def _save(client, code, rid, payload):
    r = client.post(f"/guide/{code}/interviews/{rid}/save", data=json.dumps(payload), content_type="application/json")
    assert r.json["ok"]


def test_general_note_saved_kept_and_shown(app, client):
    from app import interview_results

    code, sid = make(app, client)
    rid = start(app, client, code)
    page = client.get(f"/guide/{code}/conversation?interview={rid}").data.decode()
    assert "data-iv-general" in page and "Notizen zum ganzen Interview" in page
    _save(client, code, rid, {"note": "  Raum laut, Fenster zu.\nWirkt müde.  ", "elapsed": 1000})
    with app.app_context():
        row = db.session.get(Response, rid)
        assert row.interview["note"] == "Raum laut, Fenster zu.\nWirkt müde."
    rec = client.get(f"/guide/{code}/conversation?interview={rid}").data.decode()
    assert "Raum laut, Fenster zu." in rec                                  # the view starts from the stored note
    # a page or offline copy from before sends no "note": the stored note stays
    _save(client, code, rid, {"notes": {}, "elapsed": 2000})
    with app.app_context():
        row = db.session.get(Response, rid)
        assert row.interview["note"].startswith("Raum laut")
        text = interview_results.notes_text(row.survey, row, "de")
        assert "== Notizen zum ganzen Interview ==" in text and "Wirkt müde." in text
    analysis = client.get(f"/guide/{code}/interviews/analysis").data.decode()
    assert "Notizen zum ganzen Interview" in analysis and "Wirkt müde." in analysis
    # emptied: gone; too long: cut
    _save(client, code, rid, {"note": "   "})
    with app.app_context():
        assert "note" not in db.session.get(Response, rid).interview
    _save(client, code, rid, {"note": "x" * 6000})
    with app.app_context():
        assert len(db.session.get(Response, rid).interview["note"]) == 5000


def test_trial_view_has_the_field_too(app, client):
    code, sid = make(app, client)
    page = client.get(f"/guide/{code}/conversation").data.decode()
    assert "data-iv-general" in page
