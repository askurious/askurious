"""Switching the source language keeps what was written (Philipp, 2026-10-06: a survey switched from "Deutsch (Sie)"
to "Deutsch (du)" showed every element written before empty, the IAT among them)."""
from app.extensions import db
from app.models import Block, Item, Survey
from tests.conftest import add

META = {"title": "Test", "allow_back": "1", "part": "befragung"}


def _written_in_german(app, client):
    client.post("/surveys/new", data={"title": "Test", "languages": ["de"], "default_language": "de"})
    add(client, "text_input")                                                          # item 2 in block 2
    client.post("/surveys/1/editor/items/2", data={"code": "q", "question": "Wie alt sind Sie?", "answer_mode": "optional"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        db.session.get(Block, 2).titles = {"de": "Demographische Angaben"}
        s.end_texts = {"de": {"thanks": "Danke!"}}
        s.flow = {**(s.flow or {}), "extra": [{"t": "end", "id": "e1", "text": {"de": "Ende"}}]}
        db.session.commit()


def test_switching_to_du_carries_the_texts(app, client):
    _written_in_german(app, client)
    client.post("/surveys/1/meta", data={**META, "languages": ["de-du"], "default_language": "de-du"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        assert s.default_language == "de-du" and s.languages == ["de-du"]
        assert item.text("question") == "Wie alt sind Sie?"                            # carried, to be reworded
        assert item.texts["de"]["question"] == "Wie alt sind Sie?"                     # the original stays
        assert db.session.get(Block, 2).titles["de-du"] == "Demographische Angaben"
        assert s.end_texts["de-du"] == {"thanks": "Danke!"}
        assert s.flow["extra"][0]["text"] == {"de": "Ende", "de-du": "Ende"}


def test_existing_du_texts_are_kept_and_a_switched_survey_is_repaired(app, client):
    _written_in_german(app, client)
    with app.app_context():                                                             # switched before the fix
        s = db.session.get(Survey, 1)
        s.languages, s.default_language = ["de-du"], "de-du"
        add_du = db.session.get(Item, 2)
        add_du.texts = {**add_du.texts, "de-du": {"question": "Wie alt bist du?"}}
        db.session.commit()
    client.post("/surveys/1/meta", data={**META, "languages": ["de-du"], "default_language": "de-du"})   # just saved
    with app.app_context():
        assert db.session.get(Item, 2).text("question") == "Wie alt bist du?"          # not overwritten
        assert db.session.get(Block, 2).titles["de-du"] == "Demographische Angaben"    # repaired


def test_english_keeps_its_own_texts(app, client):
    _written_in_german(app, client)
    client.post("/surveys/1/meta", data={**META, "languages": ["de", "en"], "default_language": "de"})
    client.post("/surveys/1/editor/items/2?lang=en", data={"code": "q", "question": "How old are you?"})
    client.post("/surveys/1/meta", data={**META, "languages": ["de", "en"], "default_language": "en"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.text("question") == "How old are you?" and item.texts["de"]["question"] == "Wie alt sind Sie?"


def test_a_du_text_saved_empty_is_filled_entry_by_entry(app, client):
    """An element opened in "Deutsch (du)" before the texts were carried: its autosave stored "" for the question
    and each option, which blocked carrying the whole text (gender without labels, 2026-10-06)."""
    _written_in_german(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages, s.default_language = ["de-du"], "de-du"
        item = db.session.get(Item, 2)
        item.texts = {"de": {"question": "Welchem Geschlecht?", "options": {"o1": "weiblich", "o2": "männlich"}},
                      "de-du": {"question": "", "options": {"o1": "", "o2": "Mann"}}}
        db.session.commit()
    client.post("/surveys/1/meta", data={**META, "languages": ["de-du"], "default_language": "de-du"})
    with app.app_context():
        assert db.session.get(Item, 2).texts["de-du"] == {"question": "Welchem Geschlecht?",
                                                          "options": {"o1": "weiblich", "o2": "Mann"}}  # "Mann" kept
