"""UI/UX audit 2026-10-05, package B (Startprüfung und Startklar): V11 to V17."""
from werkzeug.datastructures import MultiDict

from app import design, quotas
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add, fill_consent

E = "/surveys/1/editor"


def _messages(app, severity=None):
    with app.app_context():
        return [f.message for f in design.check(db.session.get(Survey, 1)) if severity in (None, f.severity)]


def _choice(client, item_id, labels, code="antwort", question="Würden Sie es wieder tun?"):
    client.post(f"{E}/items/{item_id}", data=MultiDict({
        "code": code, "question": question, "option_id": [f"o{n}" for n in range(len(labels))],
        "option_code": [str(n + 1) for n in range(len(labels))], "option_label": labels}))


def test_v11_empty_questions_block_the_start(app, client, survey):
    fill_consent(client)
    add(client, "text_input")                                                   # 2: no question
    add(client, "likert")                                                       # 3: one statement, no texts
    errors = _messages(app, design.ERROR)
    assert any("Fragetext fehlt" in m for m in errors)
    with app.app_context():
        likert = db.session.get(Item, 3)
        if len((likert.config or {}).get("statements") or []) == 1:
            assert any("leere Zeile" in m for m in errors)
    client.post(f"{E}/items/2", data={"code": "q2", "question": "Wie heißen Sie?"})
    assert not any("Fragetext fehlt" in m for m in _messages(app, design.ERROR))


def test_v12_block_titles_are_filled_and_checked(app, client, survey):
    from flask import render_template_string

    with app.test_request_context():
        assert render_template_string("{{ 'Bewertung: {{vm}}'|readable_placeholders }}") == "Bewertung: [vm]"
        assert "Bewertung: dem Auto" in str(render_template_string("{{ t|fill(v) }}", t="Bewertung: {{vm}}", v={"vm": "dem Auto"}))
    with app.app_context():
        block = db.session.get(Survey, 1).blocks[1]
        block.titles, block.show_title = {"de": "Bewertung: {{vm}}"}, True
        db.session.commit()
    assert any("Blocktitel „Bewertung: {{vm}}“" in m and "keiner Schleife" in m for m in _messages(app, design.WARNING))


def test_v13_contact_is_needed(app, client, survey):
    fill_consent(client, contact_email="")
    # staged: a warning in the draft (still editable and testable), an error that blocks the start
    assert not any("Kontakt-E-Mail" in m for m in _messages(app, design.ERROR))
    assert any("keine Kontakt-E-Mail und keine Einrichtung" in m for m in _messages(app, design.WARNING))
    with app.app_context():
        at_start = design.check(db.session.get(Survey, 1), start=True)
    assert any("keine Kontakt-E-Mail und keine Einrichtung" in f.message for f in design.blocking(at_start))
    with app.app_context():
        uid = db.session.get(Survey, 1).public_key
    assert "leiten die Betreiber der Plattform weiter" in client.get(f"/s/{uid}/privacy").data.decode()
    fill_consent(client, contact_email="", aff_1="RWTH Aachen")  # an institution: a warning, also at the start
    with app.app_context():
        at_start = design.check(db.session.get(Survey, 1), start=True)
    assert [f.severity for f in at_start if "Kontakt-E-Mail" in f.message] == [design.WARNING]
    fill_consent(client)
    assert not any("Kontakt-E-Mail" in m for m in _messages(app))


def test_v14_long_pages_get_a_hint(app, client, survey):
    for n in (2, 3, 4):
        add(client, "ranking")
    assert any("Seitenumbruch erwägen" in m for m in _messages(app, design.WARNING))


def test_v15_quota_gap_is_a_sentence_with_the_remedy(app, client, survey):
    add(client, "single_choice")                                                # 2
    _choice(client, 2, ["weiblich", "männlich", "divers", "keine Angabe"], code="geschlecht",
            question="Welchem Geschlecht fühlen Sie sich zugehörig?")
    with app.app_context():
        item = db.session.get(Item, 2)
        text = quotas._gap_message("Quote „Geschlecht“", [2], [[("3", "x", True)], [("4", "y", True)]], {2: item})
    assert text == ("Quote „Geschlecht“: Wer bei „Welchem Geschlecht fühlen Sie sich …“ „divers“ oder „keine Angabe“ "
                    "antwortet, passt in keine Teilquote. Teilquote ergänzen oder die Antwort im Element als „fehlend“ "
                    "markieren.")


def test_v16_v17_options_missing_and_ordered(app, client, survey):
    add(client, "single_choice")                                                # 2
    _choice(client, 2, ["ja, bestimmt", "eher ja", "eher nein", "nein", "keine Angabe"])
    warnings = _messages(app, design.WARNING)
    assert any("„keine Angabe“ ist nicht als „fehlend“ markiert" in m for m in warnings)
    assert any("Messniveau ist aber „nominal“" in m for m in warnings)
    page = client.get(f"{E}/").data.decode()
    assert "Auf „ordinal“ stellen" in page
    assert "<strong>fehlend:</strong>" in client.get(f"{E}/items/2").data.decode()
    client.post(f"{E}/items/2/level-of-measurement", data={"level": "ordinal"})
    with app.app_context():
        assert db.session.get(Item, 2).config["level"] == "ordinal"
    assert not any("nominal" in m for m in _messages(app, design.WARNING))
    # plain categories stay nominal without a hint
    add(client, "single_choice")                                                # 3
    _choice(client, 3, ["Bus", "Bahn", "Auto"], code="vm", question="Womit fahren Sie?")
    assert not any("(vm)" in m and "nominal" in m for m in _messages(app))
