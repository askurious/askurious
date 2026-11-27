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



def test_confirmations_change_after_reopening_and_the_export_follows_the_version(app, client, survey):
    """A survey reopened for editing can change its confirmations (Philipp, 2026-11-09): the earlier version keeps
    the ones its participants ticked, and the export leaves a column empty for a version that did not ask it."""
    from app.models import Response, Survey

    E = "/surveys/1/editor/items/1"
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.qversion = 2  # as after "Zur Bearbeitung öffnen" of a version started before the record began
        old = Response(survey_id=1, source="real", is_preview=False, answers={"1": "1"}, qversion=1)
        db.session.add(old)
        db.session.commit()
        old_id = old.id
    page = client.get(E).data.decode()
    assert 'name="confirm_commitment" value="1" checked >' in page and "ab dem nächsten Start" in page
    client.post(E, data=MultiDict([("code", "consent"), ("confirm_voluntary", "1"), ("body", "x")]))
    with app.app_context():
        item = db.session.get(Item, 1)
        assert [c["key"] for c in item.kind.confirmations(item, "de")] == ["voluntary"]
        assert item.kind.shown_by_version(item) == {"1": ["age", "voluntary", "data", "commitment"]}
        assert [c for c, _ in item.kind.export_columns(item)] == [
            "consent", "consent_age", "consent_voluntary", "consent_data", "consent_commitment"]
        row = {**item.kind.export_values(item, "1"),
               **item.kind.export_response_values(item, "1", db.session.get(Response, old_id))}
        assert row == {"consent": 1, "consent_age": 1, "consent_voluntary": 1, "consent_data": 1, "consent_commitment": 1}
        new = Response(survey_id=1, source="real", is_preview=False, answers={"1": "1"}, qversion=2)
        row = {**item.kind.export_values(item, "1"), **item.kind.export_response_values(item, "1", new)}
        assert row == {"consent": 1, "consent_age": None, "consent_voluntary": 1, "consent_data": None,
                       "consent_commitment": None}
        item.kind.freeze_confirmations(item)  # the next start records version 2
        assert item.config["confirm_versions"]["2"] == ["voluntary"]  # counts once the version has started


def _field(app, client):
    """A started survey with one question and the consent filled; returns its number for /s/."""
    from tests.conftest import fill_consent

    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "text_input"})
    client.post("/surveys/1/editor/items/2", data={"code": "q", "question": "Frage"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return 1


def _agree(app, uid):
    from tests.conftest import CONSENT_OK

    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)


def test_the_earlier_version_keeps_its_confirmations_from_the_reopening_on(app, client, survey):
    """Ralf, 2026-11-27: which declaration each participant agreed to must always be traceable. A reopened draft
    that gains a confirmation (a voice message brings "audio") must not report it for those who agreed before,
    also before the next start and for a survey reopened before the record of the versions began."""
    from app.models import Response

    uid = _field(app, client)
    _agree(app, uid)
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    with app.app_context():
        item = db.session.get(Item, 1)
        assert item.kind.shown_by_version(item) == {"1": ["voluntary", "data", "commitment"]}
        item.config = {k: v for k, v in item.config.items() if k != "confirm_versions"}  # reopened before the record
        db.session.commit()
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "voice"})
    with app.app_context():
        item = db.session.get(Item, 1)
        old = db.session.scalar(db.select(Response).filter_by(survey_id=1, is_preview=False))
        assert "audio" in [c["key"] for c in item.kind.confirmations(item, "de")]
        assert "consent_audio" in [c for c, _ in item.kind.export_columns(item)]
        row = {**item.kind.export_values(item, "1"), **item.kind.export_response_values(item, "1", old)}
        assert row["consent_audio"] is None and row["consent_voluntary"] == 1
        assert item.kind.asked_in(item, 2) == ["voluntary", "data", "audio", "commitment"]


def test_every_wording_someone_could_agree_to_is_kept(app, client, survey):
    """Ralf, 2026-11-27: the wording of the consent can be corrected in the field, so each one is kept with the time
    it began, the export names the one in force when a participation began and questionnaire.md holds them all."""
    from datetime import timedelta

    from app import export
    from app.models import Response, utcnow

    uid = _field(app, client)
    with app.app_context():
        item = db.session.get(Item, 1)
        first = item.kind.wordings(item)
        assert [w["n"] for w in first] == [1] and first[0]["qversion"] == 1 and first[0]["from"]
        assert first[0]["texts"]["de"]["confirmations"][0][0] == "voluntary" and set(first[0]["texts"]) == {"de", "en"}
        assert "consent_text" not in [c for c, _ in item.kind.export_columns(item)]   # one wording: no column
        body = item.t("de")["body"]
    E = "/surveys/1/editor/items/1"
    form = {"code": "consent", "question": "Einwilligung", "duration": "7", "agree": "Fortsetzen", "disagree": "Nein"}
    client.post(E, data={**form, "body": body + " Tippfehler"})
    client.post(E, data={**form, "body": body + " Korrigiert"})  # nobody began in between: replaced, not kept
    with app.app_context():
        item = db.session.get(Item, 1)
        kept = item.kind.wordings(item)
        assert [w["n"] for w in kept] == [1] and kept[0]["from"] == first[0]["from"]
        assert "Text Korrigiert" in kept[0]["texts"]["de"]["body"]
    client.post("/surveys/1/editor/undo")  # "Rückgängig" takes the correction back, never the record
    with app.app_context():
        item = db.session.get(Item, 1)
        kept = item.kind.wordings(item)
        assert [w["n"] for w in kept] == [1] and kept[0]["from"] == first[0]["from"]
        assert kept[0]["texts"]["de"]["body"] == item.kind.shown_body(item, "de")
    client.post(E, data={**form, "body": body + " Korrigiert"})
    _agree(app, uid)
    with app.app_context():  # the participation began a while ago, then the wording is corrected
        old = db.session.scalar(db.select(Response).filter_by(survey_id=1, is_preview=False))
        old.started_at = utcnow() - timedelta(minutes=5)
        item = db.session.get(Item, 1)
        w = [dict(x) for x in item.config["wordings"]]
        w[0]["from"] = (utcnow() - timedelta(minutes=30)).isoformat(timespec="seconds")
        item.config = {**item.config, "wordings": w}
        db.session.commit()
        old_id = old.id
    client.post(E, data={**form, "body": body + " Neue Angabe"})
    _agree(app, uid)
    with app.app_context():
        item = db.session.get(Item, 1)
        kept = item.kind.wordings(item)
        assert [w["n"] for w in kept] == [1, 2] and "Text Korrigiert" in kept[0]["texts"]["de"]["body"]
        assert "Text Neue Angabe" in kept[1]["texts"]["de"]["body"]
        assert ("consent_text", "Wortlaut der Einwilligung, dem zugestimmt wurde (Nummer, die Wortlaute stehen im "
                "Fragebogen)") in item.kind.export_columns(item)
        rows = {r.id: {**item.kind.export_values(item, "1"), **item.kind.export_response_values(item, "1", r)}
                for r in db.session.scalars(db.select(Response).filter_by(survey_id=1, is_preview=False))}
        assert rows.pop(old_id)["consent_text"] == 1 and [r["consent_text"] for r in rows.values()] == [2]
        assert item.kind.export_values(item, None)["consent_text"] is None
        md = export.questionnaire_md(db.session.get(Survey, 1))
        assert "Wording 1 (questionnaire version 1, from " in md and "Wording 2 (questionnaire version 1, from " in md
        assert "Korrigiert" in md and "Neue Angabe" in md and "column `consent_text`" in md
        assert export.label_en("consent_text", item.kind.export_columns(item)[-1][1]).startswith(
            "Wording of the consent that was agreed to")


def test_a_survey_started_before_wordings_were_kept_gets_the_one_found(app, client, survey):
    """Started before 2026-11-27: the hourly sweep keeps the wording as found, without a time; a correction after
    that is the second one, and participations from before it have the first."""
    from datetime import timedelta

    from app import export
    from app.items import consent
    from app.models import Response

    uid = _field(app, client)
    _agree(app, uid)
    with app.app_context():
        item = db.session.get(Item, 1)
        # past the session: saving the element would keep the wording again
        db.session.execute(Item.__table__.update().where(Item.__table__.c.id == 1).values(
            config={k: v for k, v in item.config.items() if k != "wordings"}))
        db.session.commit()
        db.session.expire_all()
        assert consent.record_missing() == 1 and consent.record_missing() == 0
        item = db.session.get(Item, 1)
        assert [(w["n"], w["from"]) for w in item.kind.wordings(item)] == [(1, None)]
        body = item.t("de")["body"]
    client.post("/surveys/1/editor/items/1", data={"code": "consent", "question": "Einwilligung", "duration": "7",
                                                    "agree": "Fortsetzen", "disagree": "Nein", "body": body + " Später"})
    with app.app_context():
        item = db.session.get(Item, 1)
        assert [w["n"] for w in item.kind.wordings(item)] == [1, 2]
        r = db.session.scalar(db.select(Response).filter_by(survey_id=1, is_preview=False))
        r.started_at = r.started_at - timedelta(minutes=1)
        assert item.kind.export_response_values(item, "1", r)["consent_text"] == 1
        assert "as found on " in export.questionnaire_md(db.session.get(Survey, 1))
        db.session.rollback()
        s = db.session.get(Survey, 1)
        s.erase_code = True   # the text names the way to delete: a deletion code switched on changes what is agreed to
        db.session.commit()
        kept = db.session.get(Item, 1).kind.wordings(db.session.get(Item, 1))
        assert [w["n"] for w in kept] == [1, 2] and "Löschcode" in kept[1]["texts"]["de"]["body"]  # nobody began: replaced


def test_the_agreement_names_the_wording_the_page_showed(app, client, survey):
    """Ralf, 2026-11-27 ("ja"): the number of the wording is kept with the agreement itself. A page that was open
    while the text was corrected comes again with the text as it is now; only then does the agreement count."""
    import re

    from tests.conftest import CONSENT_OK

    from app.models import Response

    _field(app, client)
    p = app.test_client()
    token = re.search(r'name="i1_w" value="([^"]+)"', p.get("/s/1").data.decode()).group(1)
    assert token.startswith("1.")
    E = "/surveys/1/editor/items/1"
    form = {"code": "consent", "question": "Einwilligung", "duration": "7", "agree": "Fortsetzen", "disagree": "Nein"}
    client.post(E, data={**form, "body": "Text, berichtigt"})  # nobody has agreed yet: the wording is replaced
    p.post("/s/1", data={**CONSENT_OK, "i1_w": token})         # agrees to the page from before the correction
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(survey_id=1, is_preview=False))
        assert r is None or ("1" not in (r.answers or {}) and "consent_text" not in (r.layout or {}))  # nothing is kept
    page = p.get("/s/1").data.decode()
    again = re.search(r'name="i1_w" value="([^"]+)"', page).group(1)
    assert "Text, berichtigt" in page and again.startswith("1.") and again != token
    p.post("/s/1", data={**CONSENT_OK, "i1_w": again})
    client.post(E, data={**form, "body": "Text, noch einmal berichtigt"})  # after an agreement: a second wording
    q = app.test_client()                                                    # declining never needs the current text
    q.get("/s/1")
    q.post("/s/1", data={"i1": "0", "i1_w": token})
    with app.app_context():
        item = db.session.get(Item, 1)
        kept = item.kind.wordings(item)
        assert [w["n"] for w in kept] == [1, 2] and kept[0]["texts"]["de"]["body"].startswith("Text, berichtigt")
        r = db.session.scalar(db.select(Response).filter_by(survey_id=1, is_preview=False, status=Response.ACTIVE))
        assert r.answers["1"] == "1" and r.layout["consent_text"] == 1
        r.started_at = None  # the agreement itself names the wording, whatever the beginning says
        assert item.kind.export_response_values(item, "1", r)["consent_text"] == 1
        db.session.rollback()
        assert db.session.get(Survey, 1).declined_count == 1
