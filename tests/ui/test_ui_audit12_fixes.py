"""UI/usability audit 2026-10-12, package 1: a start blocker looks like an error everywhere (U9), the flow says so
(U9, U54), the live list without formatting marks (U29), the location in English (U52), prompts per day without a
needless decimal (U35), delete buttons only with something to delete (U17)."""
from types import SimpleNamespace

from app import lang
from app.extensions import db
from app.items import geolocation
from app.models import Survey
from tests.conftest import add, fill_questions
from tests.diary.test_diary import _new as new_diary
from tests.live.test_live import add as live_add, make as make_live


def _only_the_consent_contact_missing(app, client):
    add(client, "text_input")
    fill_questions(app)


def test_a_start_blocker_is_an_error_in_the_editor_and_on_the_start_list(app, client, survey):
    # the consent has neither a contact e-mail nor an institution: the survey cannot start
    _only_the_consent_contact_missing(app, client)
    editor = client.get("/surveys/1/editor/").get_data(as_text=True)
    errors = [part.split("</li>")[0] for part in editor.split('class="finding finding-error"')[1:]]
    assert any("nicht starten" in e for e in errors)
    field = client.get("/surveys/1/field").get_data(as_text=True)
    assert "verhindert den Start" in field
    assert "Keine Warnungen in der Startprüfung" not in field and "Keine Fehler und Warnungen" not in field


def test_the_flow_names_the_start_blocker(app, client, survey):
    _only_the_consent_contact_missing(app, client)
    page = client.get("/surveys/1/flow/").get_data(as_text=True)
    assert "die den Start verhindern." in page or "der den Start verhindert." in page
    assert "Alle 1 Meldungen" not in page


def test_the_live_list_shows_no_formatting_marks(app, client):
    code, sid, block = make_live(app, client)
    live_add(app, client, sid, block, "info", question="**Willkommen!** Schön, dass Sie da sind.")
    page = client.get(f"/presenter/{code}").get_data(as_text=True)
    assert "**Willkommen!**" not in page and "Willkommen! Schön, dass Sie da sind." in page


def test_the_location_speaks_english_on_english_pages(monkeypatch):
    monkeypatch.setattr(lang, "english", lambda: True)
    item = SimpleNamespace(code="ort")
    kind = geolocation.Geolocation.__new__(geolocation.Geolocation)
    _kind, _unit, ways = kind.result_column(item, "ort_way")
    assert ways["1"] == "position of the device" and ways["6"] == "time limit exceeded"
    _kind, _unit, sources = kind.result_column(item, "ort_state_src")
    assert sources["2"] == "estimated from the IP address"
    assert set(geolocation.WAY_LABELS_EN) == set(geolocation.WAY_LABELS)


def test_prompts_per_day_without_a_needless_decimal(app, client):
    code = new_diary(app, client, running=False)
    page = client.get(f"/diary/{code}/schedule").get_data(as_text=True)
    assert "Etwa 3 Abfragen am Tag." in page and "3.0" not in page


def test_delete_buttons_only_with_something_to_delete(app, client, survey):
    page = client.get("/surveys/1/field").get_data(as_text=True)
    assert "Dummy-Teilnahmen löschen" not in page and "Testdurchläufe löschen" not in page
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.DRAFT
