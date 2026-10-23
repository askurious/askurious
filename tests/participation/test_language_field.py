"""The language switch on participant pages and the platform's deletion page (UI audit 2026-10-12, U44, U47): from four
languages on one field with a globe instead of a row of links that takes two lines on a phone; the deletion page
without a survey follows the language picked in the header, as the other public pages do."""
from types import SimpleNamespace

from flask import render_template

from app.lang import PICKED


def _langs(app, languages, lang="de"):
    survey = SimpleNamespace(languages=languages, language_choice=True)
    with app.test_request_context():
        return render_template("participant/_langs.html", survey=survey, lang=lang)


def test_three_languages_stay_links(app):
    html = _langs(app, ["de", "en", "fr"])
    assert 'data-set-lang="en"' in html and "data-lang-select" not in html
    assert 'aria-current="true"' in html


def test_four_languages_and_more_are_one_field(app):
    html = _langs(app, ["de", "en", "fr", "tr", "ar"], lang="fr")
    assert "data-lang-select" in html and "data-send-page" in html and "data-set-lang" not in html
    assert '<option value="fr" lang="fr" selected aria-current="true">Français</option>' in html
    assert '<option value="ar" lang="ar">' in html
    assert html.count("<option") == 5
    assert "<noscript><button" in html  # without JavaScript the form opens ?lang= with its button


def test_german_counts_once_for_the_field(app):
    html = _langs(app, ["de", "de-du", "en", "fr"], lang="de")
    assert html.count("data-set-lang=") == 3 and "data-lang-select" not in html  # three to choose from: links


def test_deletion_page_has_the_field(app):
    html = app.test_client().get("/privacy/delete").data.decode()
    assert "data-lang-select" in html and "data-send-page" not in html
    assert html.count("<option") >= 12


def test_deletion_page_follows_the_language_picked_in_the_header(app):
    guest = app.test_client()
    page = guest.get("/privacy/delete", headers={"Accept-Language": "de-DE,de;q=0.9"}).data.decode()
    assert "Antworten mit Löschcode löschen" in page
    with guest.session_transaction() as s:
        s[PICKED] = "en"  # picked "EN" on the start page
    page = guest.get("/privacy/delete", headers={"Accept-Language": "de-DE,de;q=0.9"}).data.decode()
    assert "Delete answers with a deletion code" in page
    # ?lang= still decides
    assert "Supprimer" in guest.get("/privacy/delete?lang=fr").data.decode()


def test_deletion_page_without_a_pick_follows_the_browser(app):
    page = app.test_client().get("/privacy/delete", headers={"Accept-Language": "tr-TR,tr;q=0.9"}).data.decode()
    assert '<html lang="tr"' in page
