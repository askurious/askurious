"""Pre-launch check on what participants read across pages, and the earlier consent wording in survey files (UI audit
2026-10-12, U41 and U43): headings on some pages and none on others, a duration in a text that contradicts the
consent, and "Fortsetzen" carried on by an exported file."""
import json

from app import design, transfer
from app.extensions import db
from app.models import Block, Item, Survey, User
from tests.conftest import add, fill_consent


def test_stated_minutes_reads_durations_of_taking_part():
    assert design.stated_minutes("Schön, dass Sie mitmachen! Die Befragung dauert etwa acht Minuten.") == [(8, 8)]
    assert design.stated_minutes("Die Teilnahme dauert ca. 5 Min. Danke!") == [(5, 5)]
    assert design.stated_minutes("The survey takes about 10 to 15 minutes.") == [(10, 15)]
    assert design.stated_minutes("Ihre Teilnahme nimmt etwa 10–12 Minuten in Anspruch.") == [(10, 12)]
    # a question about minutes is no statement on the survey
    assert design.stated_minutes("Fahren Sie mehr als 30 Minuten zur Arbeit?") == []
    assert design.stated_minutes("Wie viele Minuten brauchen Sie für den Weg?") == []


def _info(client, text):
    add(client, "info")  # item 2 in block 2
    client.post("/surveys/1/editor/items/2", data={"code": "willkommen", "question": text})


def _messages(app):
    with app.app_context():
        return [f.message for f in design.check(db.session.get(Survey, 1))]


def test_a_welcome_text_with_another_duration_than_the_consent_is_named(app, client, survey):
    fill_consent(client)  # the consent names 5 minutes
    _info(client, "Willkommen! Die Befragung dauert etwa acht Minuten.")
    found = [m for m in _messages(app) if "Nennt 8 Minuten" in m]
    assert found and "etwa 5 Minuten" in found[0]


def test_a_matching_duration_is_not_named(app, client, survey):
    fill_consent(client)
    _info(client, "Willkommen! Die Befragung dauert etwa 5 Minuten.")
    assert not [m for m in _messages(app) if "nennt" in m and "Minuten" in m]


def test_blocks_with_and_without_their_heading_are_named(app, client, survey):
    add(client, "text_input")  # item 2 in block 2
    with app.app_context():
        s = db.session.get(Survey, 1)
        first = db.session.get(Block, 2)
        second = Block(survey_id=1, position=len(s.blocks), titles={"de": "Ihr Alltag"}, show_title=True)
        second.items.append(Item(position=0, type="text_input", code="q2", texts={"de": {"question": "Wie?"}}))
        db.session.add(second)
        first.titles, first.show_title = {"de": "Zu Ihrer Person"}, False
        db.session.commit()
    found = [m for m in _messages(app) if m.startswith("Überschriften")]
    assert found and "„Zu Ihrer Person“" in found[0] and "1 von 2" in found[0]
    with app.app_context():
        for b in db.session.get(Survey, 1).blocks:
            b.show_title = True
        db.session.commit()
    assert not [m for m in _messages(app) if m.startswith("Überschriften")]


def _read_back(app, change=None):
    with app.test_request_context():
        data = transfer.dump(db.session.get(Survey, 1), with_files=False)
        if change:
            change(data)
        new, _ = transfer.create(transfer.loads(json.dumps(data).encode()), db.session.get(User, 1))
        return new.consent_item.text("agree"), new.consent_item.text("agree_note")


def test_a_file_with_the_earlier_button_fortsetzen_reads_in_with_weiter(app, client, survey):
    fill_consent(client)  # typed "Fortsetzen" in the editor: an own word there, so the file carries it
    with app.app_context():
        assert db.session.get(Item, 1).text("agree") == "Fortsetzen"

    def note_too(data):  # as an export of the time wrote it: button and note in the earlier wording
        consent = next(i for b in data["blocks"] for i in b["items"] if i["type"] == "consent")
        consent["texts"]["de"]["agree_note"] = ("Mit „Fortsetzen“ bestätigen Sie, dass Sie die Informationen gelesen "
                                                "haben und der Teilnahme zustimmen.")
    agree, note = _read_back(app, note_too)
    assert agree == "Weiter" and "„Weiter“" in note


def test_an_own_note_keeps_the_earlier_button(app, client, survey):
    fill_consent(client)

    def own_note(data):
        consent = next(i for b in data["blocks"] for i in b["items"] if i["type"] == "consent")
        consent["texts"]["de"]["agree_note"] = "Mit „Fortsetzen“ stimmen Sie zu."
    agree, note = _read_back(app, own_note)
    assert agree == "Fortsetzen" and note == "Mit „Fortsetzen“ stimmen Sie zu."
