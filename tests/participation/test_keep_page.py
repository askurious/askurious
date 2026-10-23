"""A dropped connection on "Weiter" (participant.js, keepPage): the inputs of a page stay in the tab under a name for
the participation and the page, and the page says in its own language what happened."""
import os
import re

from app.extensions import db
from app.models import Survey
from tests.conftest import CONSENT_OK, add, fill_consent

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
JS = open(os.path.join(ROOT, "app/static/participant.js"), encoding="utf-8").read()

TEXTS = ("Sending …", "No internet connection. Your answers are still here. Please send the page again once you are back online.",
         "Sending is taking unusually long. Your answers are still here. If nothing happens, please send the page again.",
         "Your entries on this page have been restored.")


def build(client):
    fill_consent(client)
    add(client, "text_input")                              # 2
    client.post("/surveys/1/editor/items/2", data={"code": "age", "question": "Alter"})
    add(client, "page_break")                              # 3
    add(client, "info")                                    # 4
    client.post("/surveys/1/editor/items/4", data={"code": "bye", "question": "Fast fertig"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with client.application.app_context():
        return "/s/" + db.session.get(Survey, 1).code


def keep_of(html):
    found = re.search(r'data-keep="([0-9a-f]{16}-[^"]+)"', html)
    return found and found.group(1)


def test_page_carries_its_keep_name_and_the_texts(app, client, survey):
    url = build(client)
    p = app.test_client()
    consent = keep_of(p.get(url).data.decode())  # not stored yet: the participant ID in the cookie names it
    p.post(url, data=dict(CONSENT_OK))
    html = p.get(url).data.decode()
    first = keep_of(html)
    assert consent and consent != first and consent.split("-")[0] == first.split("-")[0]
    assert first, "form[data-keep] fehlt"
    # the notes next to the buttons, in a live region, in the page's language
    assert 'data-send-note aria-live="polite"' in html
    assert 'data-sending="Wird gesendet …"' in html and "Keine Verbindung zum Internet" in html
    # the same page again: the same name; the next page: another one
    assert keep_of(p.get(url).data.decode()) == first
    p.post(url, data={"nav": "next", "i2": "41"})
    second = keep_of(p.get(url).data.decode())
    assert second and second != first and second.split("-")[0] == first.split("-")[0]
    # another participation: another digest, and it says nothing about the participation
    q = app.test_client()
    q.get(url)
    q.post(url, data=dict(CONSENT_OK))
    other = keep_of(q.get(url).data.decode())
    assert other.split("-")[0] != first.split("-")[0]


def test_hidden_fields_kept_only_where_the_item_says_so(app, client, survey):
    for name in ("slider", "ranking", "highlight", "image_mark"):
        html = open(os.path.join(ROOT, f"app/templates/items/{name}/participant.html"), encoding="utf-8").read()
        assert "data-keep" in html, name
    # the page's own fields, the trap fields and files are never kept
    for name in ("csrf_token", "pos", "time_more", "more_info", "ki_kennwort"):
        assert name in JS.split("var SKIP")[1].split("\n")[0]
    assert "file|password" in JS and 'closest(".p-trap")' in JS


def test_script_restores_before_the_items_and_sends_as_a_plain_form():
    keep = JS.index("var keepPage")
    assert keep < JS.index("Sliders without a starting value")  # the item scripts start from the restored values
    assert "sessionStorage" in JS[keep:keep + 3000]
    # server state wins; nothing of an ended participation stays in the tab
    assert "same(rendered[name], saved.base[name])" in JS and 'querySelector(".p-end:not([data-keep-inputs])")' in JS
    lock = JS[JS.index("One page, one submit"):]
    assert "navigator.onLine === false" in lock and "data-slow" in lock and 'role", "alert"' in lock
    assert "fetch(" not in lock.split("keepPage (above)")[0]  # the browser follows the server's answer as before


def test_catalog_has_the_texts_in_every_language():
    from app.surveys import LANGUAGE_NAMES
    from app.ui_i18n import CATALOG

    others = set(LANGUAGE_NAMES) - {"de", "en", "de-du"}
    for en in TEXTS:
        assert set(CATALOG[en]) >= others, en
    for en in TEXTS[1:]:
        assert "Sie" not in CATALOG[en]["de-du"]


def test_du_survey_says_it_with_du():
    from app.ui_i18n import tr

    text = tr("de-du", "Keine Verbindung zum Internet. Ihre Antworten sind noch da. Bitte senden Sie die Seite erneut, "
              "sobald Sie wieder online sind.", TEXTS[1])
    assert text.startswith("Keine Verbindung zum Internet. Deine Antworten") and "sende" in text
    assert tr("fr", "Wird gesendet …", "Sending …") == "Envoi en cours …"
