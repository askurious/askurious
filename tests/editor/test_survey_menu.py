"""One place for what is done with a survey as a whole and one list for its end (UI/UX audit 2026-10-16, findings 2,
3, 5, 6, 141, 142): the menu "⋯" in the head of every page and on the card, the button for the next step, the start
list with the data quality and the panel, and the list "Abschluss" of a closed survey."""
from app.extensions import db
from app.models import Survey
from tests.participation.test_participate import build


def test_menu_in_the_head_and_next_step(client, survey):
    build(client)
    page = client.get("/surveys/1/editor/").data.decode()
    assert "survey-menu" in page and "Duplizieren …" in page and "#export-file" in page and "/surveys/1/archive" in page
    assert "Zum Start" in page and "/field#status" in page
    assert "Löschen geht im Archiv." in page
    field = client.get("/surveys/1/field").data.decode()
    assert "Zum Start" not in field                                   # the next step is on this page itself
    assert "Datenqualität vor dem Start eingestellt" in field and "/quality#prevent" in field


def test_card_shows_three_ways_and_the_menu(client, survey):
    build(client)
    page = client.get("/dashboard/").data.decode()
    card = page.split('class="card-actions"', 1)[1].split("</li>", 1)[0]
    assert "survey-menu" in card and "Einstellungen" in card.split("nav-menu-panel", 1)[1]   # in the menu, not as a button
    assert card.split("survey-menu", 1)[0].count('class="button button-small') <= 4


def test_closed_survey_gets_the_closing_list_and_can_be_deleted_from_the_archive(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    client.post("/surveys/1/close", data={})
    with app.app_context():
        assert db.session.get(Survey, 1).status == "closed"
    field = client.get("/surveys/1/field").data.decode()
    closing = field.split('id="abschluss"', 1)[1]
    assert "Ergebnisse prüfen" in closing and "#zenodo" in closing and "#retention" in closing and "Archivieren" in closing
    page = client.get("/surveys/1/editor/").data.decode()
    assert "Abschluss" in page and "/field#abschluss" in page
    client.post("/surveys/1/archive", data={"archived": "1"})
    page = client.get("/surveys/1/editor/").data.decode()
    assert "Mit allen Daten löschen …" in page and "data-confirm-type" in page and "Aus dem Archiv holen" in page


def test_panel_without_return_links_is_a_point_of_the_start_list(app, client, survey):
    build(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {"params": [{"name": "pid"}], "redirects": {}}
        db.session.commit()
    field = client.get("/surveys/1/field").data.decode()
    assert "Abschlusslinks für das Panel" in field and "#panel" in field
