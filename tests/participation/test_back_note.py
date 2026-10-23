"""Back closed on purpose is said where the button would be (UI audit 2026-10-12, U42): after a quota check or an
external section the answers before it are fixed, and the button used to vanish without a word."""
from types import SimpleNamespace

from flask import render_template

NOTE = "Zurück ist ab hier nicht mehr möglich."


def _nav(app, allow_back=True, page=3, **extra):
    survey = SimpleNamespace(allow_back=allow_back)
    with app.test_request_context():
        return render_template("participant/_nav.html", survey=survey, lang="de", page=page, page_count=9,
                               page_items=[], **extra)


def test_closed_back_is_said(app):
    html = _nav(app, can_back=False)
    assert NOTE in html and 'value="back"' not in html


def test_open_back_shows_the_button(app):
    html = _nav(app, can_back=True)
    assert NOTE not in html and 'value="back"' in html


def test_no_note_where_back_is_off_or_on_the_first_page(app):
    assert NOTE not in _nav(app, allow_back=False, can_back=False)  # the survey has no Back at all
    assert NOTE not in _nav(app, page=1, can_back=False)


def test_note_in_every_language(app):
    survey = SimpleNamespace(allow_back=True)
    with app.test_request_context():
        html = render_template("participant/_nav.html", survey=survey, lang="fr", page=3, page_count=9, page_items=[],
                               can_back=False)
    assert "Il n’est plus possible de revenir en arrière" in html

