"""Field packs of the offline mode (app/interview_offline.py) have an upper bound: at most MAX_LIVE_PACKS live packs per
guide and MAX_LIVE_PACKS_EACH per researcher or interviewer link, refused with 429 and a message the field kit shows;
and every change of the packs holds the survey's row first, so two devices syncing at once keep both results
(backend security report 2026-10-23, offline interview packages)."""
from app import edit_guard, interview_offline
from app.extensions import db
from app.models import Survey
from tests.interview.test_interview_offline_mode import _on, _pack, _sync


def _ask(client, code, device="dev1"):
    return client.post(f"/guide/{code}/offline/pack", json={"n": 1, "device": device})


def test_packs_per_person_are_bounded(app, client, monkeypatch):
    monkeypatch.setattr(interview_offline, "MAX_LIVE_PACKS_EACH", 2)
    code, sid = _on(app, client)
    first = _pack(client, code, n=1)
    _pack(client, code, n=1)
    r = _ask(client, code)
    assert r.status_code == 429 and r.json["error"] == "limit" and "offene Feldpakete" in r.json["message"]
    with app.app_context():
        assert len(db.session.get(Survey, sid).interview["offline_packs"]) == 2  # nothing stored for the refusal
    # a withdrawn pack no longer counts
    assert client.post(f"/guide/{code}/offline/packs/{first['pack']}/withdraw").status_code == 302
    assert _ask(client, code).status_code == 200


def test_packs_per_guide_are_bounded(app, client, monkeypatch):
    monkeypatch.setattr(interview_offline, "MAX_LIVE_PACKS", 1)
    code, _ = _on(app, client)
    _pack(client, code, n=1)
    r = _ask(client, code, device="dev2")
    assert r.status_code == 429 and "Leitfaden" in r.json["message"]


def test_the_field_kit_shows_the_reason_of_a_refusal():
    from pathlib import Path

    js = (Path(__file__).parents[2] / "app" / "static" / "interview-offline.js").read_text(encoding="utf-8")
    assert "r.status === 429" in js and 'err.message === "limit"' in js


def test_a_new_pack_and_a_sync_hold_the_survey_first(app, client, monkeypatch):
    code, sid = _on(app, client)
    real, held = edit_guard.lock, []

    def lock(obj):
        held.append((type(obj).__name__, obj.id))
        return real(obj)

    monkeypatch.setattr(edit_guard, "lock", lock)
    pack = _pack(client, code, n=2)
    assert held == [("Survey", sid)]
    held.clear()
    assert _sync(client, code, pack, pack["slots"][0]).status_code == 200
    assert held[:1] == [("Survey", sid)]
    assert _sync(client, code, pack, pack["slots"][1]).status_code == 200
    with app.app_context():
        slots = db.session.get(Survey, sid).interview["offline_packs"][0]["slots"]
        assert all(s["rid"] for s in slots)  # both syncs kept in the list of places
