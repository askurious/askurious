"""Special categories (Art. 9 GDPR) and voice recordings in the consent (comparison with the RWTH template
2026-10-20, L8 L9): a switch with its own sentence and explicit tick, a tick for the audio recording, both fixed from
the start; the pre-launch check points to a data protection impact assessment."""
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions


def _item(app):
    return db.session.get(Survey, 1).consent_item


def test_switch_adds_sentence_tick_column_and_dpia_hint(app, client, survey):
    from app import design

    fill_consent(client, special_box="1", special_categories="1")
    with app.app_context():
        item = _item(app)
        assert item.config["special_categories"] is True
        assert "Art. 9 Abs. 2 lit. a DSGVO" in item.kind.template_body(item, "de")
        assert [c["key"] for c in item.kind.confirmations(item, "de")] == ["voluntary", "data", "special", "commitment"]
        assert "consent_special" in [code for code, _ in item.kind.export_columns(item)]
        messages = [f.message for f in design.check(db.session.get(Survey, 1))]
        assert any("Datenschutz-Folgenabschätzung" in m for m in messages)
        # an own text without Art. 9 gets a hint as well ("Text" from fill_consent)
        assert any("nennt die besonderen Kategorien" in m for m in messages)
    page = client.get("/surveys/1/editor/items/1").data.decode()
    assert "Besondere Kategorien (Art. 9 DSGVO)" in page and "Tonaufnahme" not in page
    # a form without the box (a script, a form of before) keeps the switch; one with the box and no tick clears it
    client.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": "0000-0000-0000-001X"})
    with app.app_context():
        assert _item(app).config["special_categories"] is True
    client.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": "0000-0000-0000-001X", "special_box": "1"})
    with app.app_context():
        item = _item(app)
        assert not item.config["special_categories"]
        assert "special" not in [c["key"] for c in item.kind.confirmations(item, "de")]


def test_participants_must_tick_the_explicit_consent(app, client, survey):
    fill_consent(client, special_box="1", special_categories="1")
    add(client, "text_input")
    fill_questions(app)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING
        assert s.consent_item.config["confirm_keys"] == ["age", "voluntary", "data", "special", "commitment"]
        url = f"/s/{s.code}"
    p = app.test_client()
    p.get(url)
    html = p.post(url, data=CONSENT_OK).data.decode()
    assert "Bitte bestätigen Sie alle Punkte" in html
    # after the start the switch stays as it was: participants gave their explicit consent
    client.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": "0000-0000-0000-001X", "special_box": "1"})
    with app.app_context():
        assert _item(app).config["special_categories"] is True


def test_voice_message_brings_its_tick_and_the_open_data_sentence(app, client, survey):
    fill_consent(client, open_data="1")
    add(client, "voice")
    with app.app_context():
        item = _item(app)
        assert "audio" in [c["key"] for c in item.kind.confirmations(item, "de")]
        assert "Tonaufnahmen werden nicht veröffentlicht, nur anonyme Daten." in item.kind.template_body(item, "de")
        assert "Voice recordings are not published" in item.kind.template_body(item, "en")


def test_started_surveys_keep_the_confirmations_of_their_start(app, client, survey):
    fill_consent(client)
    add(client, "voice")
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = s.consent_item
        s.status = Survey.RUNNING  # started before 2026-10-20: no frozen list, the four of before
        db.session.commit()
        assert "audio" not in item.kind.confirmation_keys(item)
        item.config = {**item.config, "confirm_keys": ["voluntary", "data", "audio", "commitment"]}
        db.session.commit()
        item = db.session.get(Item, item.id)
        assert [c["key"] for c in item.kind.confirmations(item, "de")] == ["voluntary", "data", "audio", "commitment"]
