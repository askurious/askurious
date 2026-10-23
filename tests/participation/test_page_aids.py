"""Aids on the participants' pages (UI/UX audit 2026-10-16, low findings 92-106): questions as headings, the star
explained before the first question, the progress value as the page, the limit of a text field, its purpose for the
browser, the summary of several messages, what a lost session means for the answers, the tick on the deletion page."""
import re

from app.extensions import db
from app.models import Item
from tests.conftest import add
from tests.participation.test_participate import build, consent_form, uid

JS = open("app/static/participant.js", encoding="utf-8").read()


def test_questions_are_headings_and_the_star_is_explained_first(app, client, survey):
    build(client)
    client.post("/surveys/1/editor/items/3", data={"code": "age", "validation": "decimal", "question": "Alter",
                                                   "answer_mode": "required"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=consent_form())
    page = p.get(url).data.decode()
    # a label inside its heading (h2 around the label; T23); the note on the star before the questions (T20)
    assert re.search(r'<h2 class="p-q-head"><label class="p-question" id="q3" for="t3"', page)
    assert page.index('class="p-required-note"') < page.index('class="p-items"')
    # the value of the progress bar is the page, as its text says (T22)
    assert 'aria-valuemin="1"' in page and 'aria-valuenow="2"' in page and 'aria-valuetext="Seite 2 von etwa' in page


def test_text_field_shows_its_limit_and_names_its_purpose(app, client, survey):
    add(client, "text_input")                                                # 2
    client.post("/surveys/1/editor/items/2", data={"code": "name", "question": "Ihr Name", "max_length": "120",
                                                   "validation": "none", "purpose_shown": "1", "purpose": "name"})
    with app.app_context():
        assert db.session.get(Item, 2).config["purpose"] == "name"
    editor = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="purpose"' in editor and '<option value="name" selected>' in editor
    client.post("/surveys/1/editor/items/2", data={"purpose_shown": "1", "purpose": "evil"})     # unknown: none
    with app.app_context():
        assert db.session.get(Item, 2).config["purpose"] == ""
    client.post("/surveys/1/editor/items/2", data={"code": "name", "question": "Ihr Name", "max_length": "120",
                                                   "validation": "none", "purpose_shown": "1", "purpose": "name"})
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert re.search(r'<input type="text" id="t2"[^>]*autocomplete="name"', html)
    # a small limit is shown from the start and describes the field (T28)
    assert 'data-count-for="t2" data-max="120"' in html and "Höchstens 120 Zeichen" in html
    assert re.search(r'id="t2"[^>]*aria-describedby="[^"]*cnt2', html)
    assert 'line.dataset.left.replace("{n}"' in JS and "used >= max * 0.8" in JS


def test_several_messages_get_a_summary_and_a_lost_session_says_what_is_kept(app, client, survey):
    assert "function messageSummary(scope, marked)" in JS and "marked.length < 2" in JS
    assert JS.count("messageSummary(") == 3                                 # defined, on the page, on a block page
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        lost = app.test_client().post(f"/s/{uid(app)}", data={"nav": "next"}).data.decode()
        assert "Ihre bisherigen Antworten sind gespeichert. Diese Teilnahme lässt sich leider nicht weiterführen" in lost
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_the_deletion_page_asks_for_the_tick_itself(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    template = open("app/templates/participant/erase.html", encoding="utf-8").read()
    assert template.count(" novalidate>") == 2                               # no message in the browser's language
    said = app.test_client().post(url + "/delete", data={"code": "1-2222-2222-2222"}).data.decode()
    assert "Bitte bestätigen Sie, dass Ihre Antworten endgültig gelöscht werden sollen." in said
