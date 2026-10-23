"""Whether participants may switch the language, the links per language, and the designer in the default language."""
from app.extensions import db
from app.models import Survey
from tests.participation.test_participate import build, uid


def _running(client):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})


def test_without_language_choice_there_is_no_switch(app, client, survey):
    _running(client)
    url = f"/s/{uid(app)}"
    assert "data-set-lang" in app.test_client().get(url).data.decode()
    with app.app_context():
        db.session.get(Survey, 1).language_choice = False
        db.session.commit()
    p = app.test_client()
    page = p.get(url, headers={"Accept-Language": "en"}).data.decode()
    assert "data-set-lang" not in page and 'lang="en"' in page
    assert 'lang="de"' in p.get(url + "?lang=de").data.decode()          # a link with ?lang= still decides


def test_setting_and_links_per_language(app, client, survey):
    page = client.get("/surveys/1/field").data.decode()
    assert 'data-link-lang' in page and '<option value="en">' in page and "danach wählbar oben auf jeder Seite" in page
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de", "en"], "default_language": "de",
                                         "language_choice_shown": "1"})
    with app.app_context():
        assert db.session.get(Survey, 1).language_choice is False
    editor = client.get("/surveys/1/editor/?lang=en").data.decode()
    assert 'class="lang-switch"' not in editor


def test_german_with_du(app, client, survey):
    """"Deutsch (du)" as a language: the platform says "du", <html lang> stays "de", the switch shows German once."""
    from app.ui_i18n import pick, tr

    assert tr("de-du", "Bitte wählen Sie eine Antwort.", "Please choose an answer.") == "Bitte wähle eine Antwort."
    assert tr("de-du", "Weiter", "Next") == "Weiter"                                       # nothing to say differently
    from app.items.text import MESSAGES
    assert pick(MESSAGES, "de-du")["integer"] == "Bitte gib eine ganze Zahl ein."
    assert pick(MESSAGES, "es")["integer"] == "Introduzca un número entero."               # the tables in every language
    _running(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de", "de-du", "en"]
        db.session.commit()
    url = f"/s/{uid(app)}"
    p = app.test_client()
    page = p.get(url + "?lang=de-du").data.decode()
    assert '<html lang="de"' in page and "Bitte bestätige alle Punkte" not in page
    switch = page.split('class="p-langs"')[1].split("</nav>")[0]
    assert switch.count("Deutsch") == 1 and 'data-set-lang="de-du"' in switch and "English" in switch
    q = app.test_client()
    assert 'data-set-lang="de" aria-current' in q.get(url, headers={"Accept-Language": "de-DE"}).data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages, s.default_language = ["de-du", "en"], "de-du"
        db.session.commit()
    r = app.test_client().get(url, headers={"Accept-Language": "de-DE,de;q=0.9"}).data.decode()
    assert 'data-set-lang="de-du" aria-current' in r                                       # German from the browser: "du"


def test_language_checkboxes_in_columns(client, survey):
    page = client.get("/surveys/1").data.decode()
    assert '<div class="lang-checks">' in page and 'value="de-du"' in page and "Deutsch (du)" in page


def test_progress_bar_can_be_switched_off(app, client, survey):
    _running(client)
    url = f"/s/{uid(app)}"
    assert 'class="p-progress"' in app.test_client().get(url).data.decode()
    with app.app_context():
        db.session.get(Survey, 1).show_progress = False
        db.session.commit()
    assert 'class="p-progress"' not in app.test_client().get(url).data.decode()
