"""The results page before the first participation and the typed texts on it (UI audit 2026-10-12): without
participations a way in instead of empty panels, the note on dummy data only with dummy data (U26); the texts typed
next to "Anderes, und zwar" as a plain list, not framed like buttons (U25)."""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add, fill_consent, fill_questions


def test_empty_results_offer_dummy_data_and_the_preview(app, client, survey):
    fill_consent(client)
    add(client, "single_choice")
    html = client.get("/surveys/1/results").get_data(as_text=True)
    assert "Noch keine Teilnahmen in der Grundlage" in html and "res-empty" in html
    assert "/surveys/1/field#testing" in html and "Dummy-Daten erzeugen" in html
    assert "/editor/preview" in html and "Vorschau durchklicken" in html
    assert 'class="panel res-item"' not in html and "Mit Dummy-Daten:" not in html
    assert "mit Dummy-Daten</span>" not in html          # the badge of the basis neither


def test_without_dummies_in_the_pool_no_dummy_button(app, client, survey):
    html = client.get("/surveys/1/results?source=real").get_data(as_text=True)
    assert "Noch keine Teilnahmen in der Grundlage" in html and "Dummy-Daten erzeugen" not in html


def test_with_participations_the_panels_are_back(app, client, survey):
    fill_consent(client)
    add(client, "single_choice")
    fill_questions(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                    answers={}))
        db.session.commit()
    html = client.get("/surveys/1/results").get_data(as_text=True)
    assert "Noch keine Teilnahmen in der Grundlage" not in html and 'class="panel res-item"' in html


def test_the_dummy_note_of_the_hypotheses_needs_dummies():
    html = open("app/templates/surveys/_hypotheses.html", encoding="utf-8").read()
    assert '"dummy" in f.sources and (by_source is not defined or by_source.get("dummy"))' in html


def test_typed_texts_are_a_plain_list():
    css = open("app/static/app.css", encoding="utf-8").read()
    rule = css[css.index(".res-texts > li {"):].split("}", 1)[0]
    assert "border" not in rule
    assert ".res-texts { columns: 16rem auto;" in css
