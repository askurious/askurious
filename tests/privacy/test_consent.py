from werkzeug.datastructures import MultiDict

from app import ai
from app.extensions import db
from app.models import Item, Survey


def consent_item(app):
    with app.app_context():
        return db.session.get(Item, 1)


def test_consent_default_movable_removable_and_readdable(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        first = s.blocks[0].items[0]
        assert first.type == "consent" and first.required and s.blocks[0].title() == "Einwilligung"
        assert _keys(first.config["researchers"]) == ["0000-0000-0000-001X"]
    E = "/surveys/1/editor"
    client.post(E + "/blocks/1/items", data={"type": "info"})           # nothing else in its block
    client.post(E + "/blocks/2/items", data={"type": "info"})           # item 2
    client.post(E + "/items/2/place", data={"block_id": 1, "position": 0})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [[i.type for i in b.items] for b in s.blocks] == [["consent"], ["info"]]
    assert "Einwilligung" not in client.get(E + "/").data.decode().split('data-palette-group="items"')[1].split("</section>")[0]
    # remove it (e.g. a chained questionnaire that already asked for consent): its block goes too
    client.post(E + "/items/1/delete")
    client.get(E + "/")                                                  # the editor does not recreate it
    with app.app_context():
        from app import design

        s = db.session.get(Survey, 1)
        assert s.consent_item is None and [[i.type for i in b.items] for b in s.blocks] == [["info"]]
        assert any("Keine Einwilligung" in f.message and f.severity == "warning" for f in design.check(s))
    palette = client.get(E + "/").data.decode().split('data-palette-group="items"')[1].split("</section>")[0]
    assert 'data-type="consent"' in palette                              # now offered as a question type
    r = client.post(E + "/blocks/2/items", data={"type": "consent"})
    assert "/items/" in r.headers["Location"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [b.items[0].type for b in s.blocks] == ["consent", "info"] and len(s.blocks[0].items) == 1

def test_template_draft_uses_facts(app, client, survey):
    client.post("/surveys/1/consent-facts", data=MultiDict([                  # the facts are kept in the settings
        ("min_age", "18"), ("open_data", "1"), ("repository", "OSF"), ("license", "CC BY 4.0"),
        ("compensation_box", "1"), ("compensation_on", "1"), ("compensation", "Verlosung"),
        ("researchers", "0000-0000-0000-001X"), ("aff_1", "RWTH Aachen"), ("contact_email", "a@b.de")]))
    client.post("/surveys/1/editor/items/1", data=MultiDict([
        ("code", "consent"), ("question", "Einwilligung"), ("duration", "12"),
        ("body", ""), ("agree", "Ja"), ("disagree", "Nein"), ("draft", "template"),
    ]))
    body = consent_item(app).texts["de"]["body"]
    # no deletion code in new surveys since the pre-test 2026-10-16: the browser sentence instead
    for fact in ("12 Minuten", "18 Jahre", "OSF", "CC BY 4.0", "Verlosung", "Philipp (RWTH Aachen)", "a@b.de",
                 "bis 15 Minuten nach der Teilnahme"):
        assert fact in body, fact
    assert "**Anonymität und Datenschutz:** Ihre Antworten" in body and "**Worum geht es?** " in body  # compact style
    page = client.get("/surveys/1/editor/blocks/1/preview", follow_redirects=True).data.decode()
    assert "<strong>Anonymität und Datenschutz:</strong> Ihre Antworten" in page and "<h3>" not in page


def test_ai_draft(app, client, survey, monkeypatch):
    seen = {}

    def fake(instructions, prompt, **kw):
        seen["instructions"], seen["prompt"] = instructions, prompt
        return "**Worum geht es?**\nKI-Text"

    monkeypatch.setattr(ai, "generate_text", fake)
    client.post("/surveys/1/editor/items/1?lang=en", data={"question": "Consent", "body": "", "agree": "Yes",
                                                             "disagree": "No", "draft": "ai"})
    assert consent_item(app).texts["en"]["body"].endswith("KI-Text")
    assert "Language: English" in seen["instructions"] and '"anonymous": true' in seen["prompt"]
    assert '"platform": "Askurious.de"' in seen["prompt"] and "Time per page, only if page_times" in seen["instructions"]


def test_validation_and_answers(app, client, survey):
    with app.app_context():
        item = db.session.get(Item, 1)
        problems = item.kind.validate(item)
        assert problems == ["Einwilligungstext fehlt"]  # the creator is preselected as responsible
        assert item.required
        import pytest
        from app.items.base import AnswerError

        with pytest.raises(AnswerError):
            item.kind.parse_answer(item, {})
        assert item.kind.declined(item.kind.parse_answer(item, {"i1": "0"}))
        assert item.kind.export_values(item, "1")[item.code] == 1


def test_design_check_notes_questions_before_consent(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "text_input"})
    client.post("/surveys/1/editor/blocks/2/move/up")
    from app import design

    with app.app_context():
        msgs = [f.message for f in design.check(db.session.get(Survey, 1))]
    assert any("vor der Einwilligung" in m for m in msgs)


def test_confirmations(app, client, survey):
    import pytest
    from app.items.base import AnswerError

    with app.app_context():
        item = db.session.get(Item, 1)
        keys = [c["key"] for c in item.kind.confirmations(item, "de")]
        assert keys == ["age", "voluntary", "data", "commitment"]
        assert "18 Jahre" in item.kind.confirmations(item, "de")[0]["text"]
        with pytest.raises(AnswerError, match="alle Punkte"):
            item.kind.parse_answer(item, {"i1": "1", "i1_c_age": "1"})
        full = {"i1": "1", **{f"i1_c_{k}": "1" for k in keys}}
        assert item.kind.parse_answer(item, full) == "1"
        assert item.kind.parse_answer(item, {"i1": "0"}) == "0"  # declining needs no ticks
        assert item.kind.export_values(item, "1")["consent_commitment"] == 1
    # switch off commitment and age, customise the data text, turn on open data
    client.post("/surveys/1/editor/items/1", data=MultiDict([
        ("code", "consent"), ("min_age", ""), ("open_data", "1"), ("confirm_voluntary", "1"), ("confirm_data", "1"),
        ("confirm_text_voluntary", "Freiwillig!"), ("body", "x"),
    ]))
    with app.app_context():
        item = db.session.get(Item, 1)
        cs = item.kind.confirmations(item, "de")
        assert [c["key"] for c in cs] == ["voluntary", "data"]
        assert cs[0]["text"] == "Freiwillig!" and "veröffentlicht" in cs[1]["text"]
    page = client.get("/surveys/1/editor/blocks/1/preview", follow_redirects=True).data.decode()
    assert page.count('type="checkbox" name="i1_c_') == 2
    assert 'value="0" formnovalidate' in page and ">Weiter<" in page  # the step has one name on every page (W53)
    assert 'class="p-nav"' not in page  # consent brings its own buttons; the preview bar pages freely


def test_failed_consent_keeps_actual_ticks(client, survey):
    html = client.post("/surveys/1/editor/preview", data={"i1": "1", "i1_c_age": "1"}).data.decode()
    form = html.split("<form", 1)[1]
    assert form.count(" checked") == 1 and form.count("p-missing") == 3
    assert "p-alert" not in html and " required" not in html.split("<form")[1]


def test_extra_consent_can_be_deleted(app, client, survey):
    """Legacy data: a second consent element in a normal block is reported and deletable."""
    from app import design
    from app.models import Item

    with app.app_context():
        s = db.session.get(Survey, 1)
        s.blocks[1].items.append(Item(type="consent", code="consent1", config={}, texts={}, answer_mode="required"))
        db.session.commit()
        assert any("Mehr als eine Einwilligung" in f.message for f in design.check(s))
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"})  # block 2 is not protected
    client.post("/surveys/1/editor/items/2/delete")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [[i.type for i in b.items] for b in s.blocks] == [["consent"], ["info"]]


def test_ai_unavailable_falls_back_to_standard_text(app, client, survey, monkeypatch):
    def broken(instructions, prompt, **kw):
        raise ai.AIError("Kein OpenAI-Schlüssel konfiguriert.")

    monkeypatch.setattr(ai, "generate_text", broken)
    r = client.post("/surveys/1/editor/items/1", data={"question": "Einwilligung", "body": "", "agree": "Ja",
                                                      "disagree": "Nein", "duration": "10",
                                                      "researchers": "0000-0000-0000-001X", "draft": "ai"},
                    follow_redirects=True)
    assert "Stattdessen wurde der Standardtext eingesetzt" in r.data.decode()
    body = consent_item(app).texts["de"]["body"]
    assert body.startswith("**Worum geht es?** ") and "**Freiwilligkeit:** " in body
    # the standard text has a button of its own, with and without AI (UI/UX audit 2026-10-16, 1)
    assert client.get("/surveys/1/editor/items/1").data.decode().count('name="draft" value="template"') == 1


def test_duration_estimate_button_and_no_duration_in_wizard(app, client, survey):
    page = client.get("/surveys/1/editor/items/1").data.decode()
    assert "Aus dem Fragebogen schätzen" in page and "data-duration-estimate=" in page
    wizard = client.get("/surveys/1/setup/consent").data.decode()
    assert 'name="duration"' not in wizard
    with app.app_context():
        before = db.session.get(Item, 1).config["duration"]
    client.post("/surveys/1/setup/consent", data={"min_age": "18", "researchers": "0000-0000-0000-001X",
                                                       "action": "next"})
    with app.app_context():
        assert db.session.get(Item, 1).config["duration"] == before     # not reset by the wizard


def _keys(refs):
    """The iDs a consent's "researchers" names: references by account since 2b ("u<id>"), "former" for the entry
    of a deleted account ("m<id>")."""
    from app.extensions import db
    from app.models import User

    out = []
    for r in refs:
        if r[:1] == "u" and r[1:].isdigit():
            user = db.session.get(User, int(r[1:]))
            out.append(user.orcid if user is not None else None)
        else:
            out.append("former" if r[:1] == "m" and r[1:].isdigit() else r)
    return out
