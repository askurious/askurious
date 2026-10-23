"""Participant elements on a phone (UI audit 2026-10-12, U39, U40, U46): star anchors under the outer stars, a slider's
row label right above its line with a target 44 px high, and the location note only where the question does not say
it already."""
import os
import re
from types import SimpleNamespace

from app.items import get

CSS = open(os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "participant.css"), encoding="utf-8").read()


def test_star_anchors_take_no_width_of_their_own():
    """U39: long anchors widened the box of the stars, so the upper one stood under the middle star."""
    assert re.search(r"\.p-rating-stars \.p-endpoints \{ width: 0; min-width: 100%; \}", CSS)
    assert re.search(r"\.p-rating-stars \.p-endpoints span \{ max-width: 34%; \}", CSS)


def test_slider_line_is_a_large_target_and_the_label_sits_on_it():
    """U40: the line 44 px high (the track stays thin), the label of a row right above it with the value beside it."""
    assert re.search(r"\.p-slider \.p-range \{ height: 44px; \}", CSS)
    assert re.search(r"\.p-slider-row\.has-label > \.p-slider \{ display: contents; \}", CSS)
    assert re.search(r"\.p-slider-row\.has-label \.p-slider-value \{ position: static; grid-row: 1; grid-column: 2;", CSS)
    template = open(os.path.join(os.path.dirname(__file__), "..", "..", "app", "templates", "items", "slider",
                                 "participant.html"), encoding="utf-8").read()
    assert "p-slider-row{{ ' has-label' if r.text }}" in template


def _geo(question, store="plz"):
    return SimpleNamespace(config={"store": store}, text=lambda key, lang=None: question)


def test_location_note_not_repeated():
    """U46: "Gespeichert werden nur Postleitzahl und Bundesland" in the question and again as the note below."""
    kind = get("geolocation")
    asked = "Dürfen wir Ihren Standort verwenden? Gespeichert werden nur Postleitzahl und Bundesland."
    assert kind.stored_said(_geo(asked), "de")
    assert kind.stored_said(_geo("May we use your location? Only the postal code is stored."), "en")
    assert not kind.stored_said(_geo("Bitte übermitteln Sie Ihren Standort."), "de")
    # the question names another level than the one stored: the note stays
    assert not kind.stored_said(_geo(asked, store="exact"), "de")
    # another language: the note's own sentence in the question
    note = kind.stored_note(_geo("", store="state"), "fr").split(",")[0].rstrip(".")
    assert kind.stored_said(_geo(f"Pouvons-nous ? {note}.", store="state"), "fr")


def test_location_page_without_the_repeated_note(app, client, survey):
    from tests.conftest import add

    add(client, "geolocation")  # item 2
    client.post("/surveys/1/editor/items/2", data={
        "code": "standort", "store": "plz", "fallback": "none",
        "question": "Dürfen wir Ihren Standort verwenden? Gespeichert werden nur Postleitzahl und Bundesland."})
    with app.test_request_context():
        from flask import render_template

        from app.extensions import db
        from app.models import Item, Survey

        item = db.session.get(Item, 2)
        html = render_template("participant/_item.html", survey=db.session.get(Survey, 1), item=item, lang="de",
                               answers={}, errors={}, variables={}, preview=True)
    assert 'class="p-hint p-geo-stored"' not in html and 'aria-describedby="q2"' in html
