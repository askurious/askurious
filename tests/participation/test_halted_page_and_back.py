"""A page sent while the survey is open for editing again, and "Back" after "Zurück" was switched off (audit data
storage and calculations 2026-10-21, A2 and A3)."""
import re

from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent


def _two_pages(client):
    fill_consent(client)
    add(client, "text_input")   # 2
    client.post("/surveys/1/editor/items/2", data={"code": "f1", "question": "Frage 1"})
    add(client, "page_break")   # 3
    add(client, "text_input")   # 4
    client.post("/surveys/1/editor/items/4", data={"code": "f2", "question": "Frage 2"})


def _start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        url = "/s/" + db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    return p, url


def _response(app):
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        return dict(r.answers), r.status, r.position


def _token(html):
    return re.search(r'name="pos" value="([^"]+)"', html).group(1)


def test_page_sent_while_the_survey_is_a_draft_keeps_its_answers(app, client, survey):
    """A2: the page sent while the survey waits (opened for editing, questionnaire unchanged) keeps its valid
    answers as "Back" does, stays on the page, says so, and the waiting page does not drop the tab's copy."""
    _two_pages(client)
    p, url = _start(app, client)
    token = _token(p.get(url).get_data(as_text=True))
    _answers, _status, position = _response(app)
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    html = p.post(url, data={"nav": "next", "i2": "Antwort auf Seite 1", "pos": token}).get_data(as_text=True)
    answers, status, now = _response(app)
    assert answers.get("2") == "Antwort auf Seite 1" and status == Response.ACTIVE and now == position
    assert "auch die gültigen Angaben auf dieser Seite" in html
    assert "data-keep-inputs" in html   # participant.js keeps the inputs held in the tab
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    again = p.get(url).get_data(as_text=True)
    assert 'name="i2"' in again and "Antwort auf Seite 1" in again


def test_page_sent_after_the_questionnaire_changed_is_dropped_and_says_so(app, client, survey):
    """A2: once the questionnaire was changed in the draft, the page is dropped (T-7), and the text says that the
    answers of this page could not be saved."""
    _two_pages(client)
    p, url = _start(app, client)
    token = _token(p.get(url).get_data(as_text=True))
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    add(client, "text_input")   # 5: the questionnaire changes
    html = p.post(url, data={"nav": "next", "i2": "verloren", "pos": token}).get_data(as_text=True)
    answers, status, _ = _response(app)
    assert "2" not in answers and status == Response.ACTIVE
    assert "konnten nicht gespeichert werden" in html and "data-keep-inputs" in html


def test_waiting_page_on_open_keeps_its_text(app, client, survey):
    _two_pages(client)
    p, url = _start(app, client)
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    html = p.get(url).get_data(as_text=True)
    assert "Ihre bisherigen Antworten bleiben gespeichert. Bitte" in html and "data-keep-inputs" in html


def test_back_without_allow_back_stays_on_the_page(app, client, survey):
    """A3: "Back" from a page shown while it was allowed, sent after "Zurück" was switched off, keeps what is valid
    and shows the same page again instead of going forward (on the last page: completing the participation)."""
    _two_pages(client)
    p, url = _start(app, client)
    p.post(url, data={"nav": "next", "i2": "eins", "pos": _token(p.get(url).get_data(as_text=True))})
    html = p.get(url).get_data(as_text=True)
    assert 'value="back"' in html
    token = _token(html)
    _a, _s, position = _response(app)
    with app.app_context():
        db.session.get(Survey, 1).allow_back = False
        db.session.commit()
    p.post(url, data={"nav": "back", "i4": "zwei", "pos": token})
    answers, status, now = _response(app)
    assert status == Response.ACTIVE and now == position
    assert answers.get("4") == "zwei"
    assert 'name="i4"' in p.get(url).get_data(as_text=True)
