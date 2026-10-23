"""Translation page: every participant-facing text next to its translation, what is missing or changed since,
AI translation of everything open, confirming per block or for everything, validated constructs left alone."""
from tests.instrument_permissions import grant_instrument
import json

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Survey, TranslationMark
from tests.conftest import add


def choice(client, question="Wie oft nutzen Sie KI?", options=("nie", "oft")):
    add(client, "single_choice")                                                  # item 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([("code", "q1"), ("question", question),
        *[("option_id", "") for o in options], *[("option_label", o) for o in options],
        *[("option_code", str(n)) for n in range(1, len(options) + 1)]]))


def ask(client, app, question):
    """Change the question text of item 2 and keep its options."""
    with app.app_context():
        item = db.session.get(Item, 2)
        opts = [(o["id"], o["code"], item.texts["de"]["options"][o["id"]]) for o in item.config["options"]]
    client.post("/surveys/1/editor/items/2", data=MultiDict([("code", "q1"), ("question", question),
        *[("option_id", i) for i, _, _ in opts], *[("option_code", c) for _, c, _ in opts], *[("option_label", l) for _, _, l in opts]]))


def fields(app):
    from app import translations

    with app.app_context():
        rows, counts = translations.overview(db.session.get(Survey, 1), "en")
        return {(u.key, f["path"]): f for u in rows for f in u.fields}, counts


def test_one_language_explains_how_to_add_one(client):
    client.post("/surveys/new", data={"title": "Einsprachig", "languages": ["de"], "default_language": "de"})
    html = client.get("/surveys/1/translation/").data.decode()
    assert "nur eine Sprache" in html and "Übersetzung" in html


def test_status_missing_ok_changed_and_confirm(app, client, survey):
    choice(client)
    html = client.get("/surveys/1/translation/").data.decode()
    assert "Übersetzung Deutsch (Sie) → English" in html and 'data-path="question"' in html and " Übersetzung</a>" in html
    f, counts = fields(app)
    assert f[("item:2", "question")]["status"] == "missing" and counts["missing"] >= 3
    with app.app_context():
        opt = next(p for (u, p) in f if u == "item:2" and p.startswith("options."))
    r = client.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "item:2", "path": "question", "text": "How often do you use AI?"})
    assert r.json["status"] == "ok"
    client.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "item:2", "path": opt, "text": "never"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.texts["en"]["question"] == "How often do you use AI?" and item.texts["en"]["options"][opt.split(".")[1]] == "never"
    # the source changes substantially: to check, with the change marked; whitespace alone does not count
    ask(client, app, "Wie oft nutzen Sie  KI? ")
    assert fields(app)[0][("item:2", "question")]["status"] == "ok"
    ask(client, app, "Wie oft nutzen Sie KI bei der Arbeit?")
    f, _ = fields(app)
    assert f[("item:2", "question")]["status"] == "changed"
    assert "<del>KI?</del>" in str(f[("item:2", "question")]["diff"]) and "<ins>" in str(f[("item:2", "question")]["diff"])
    html = client.get("/surveys/1/translation/").data.decode()
    assert "geändert seit der Übersetzung" in html and "tr-diff" in html
    with app.app_context():
        block = db.session.get(Item, 2).block_id
    client.post("/surveys/1/translation/confirm?lang=en", data={"scope": f"block:{block}"})
    assert fields(app)[0][("item:2", "question")]["status"] == "ok"           # still fits, confirmed for the block


def test_existing_translations_count_as_current(app, client, survey):
    choice(client)
    client.post("/surveys/1/editor/items/2?lang=en", data={"question": "How often do you use AI?"})
    with app.app_context():
        # typed in the item editor: marked as translated from the current source
        assert db.session.scalar(db.select(TranslationMark).filter_by(unit="item:2", path="question")).source == "Wie oft nutzen Sie KI?"
        db.session.execute(db.delete(TranslationMark))                            # as before this page existed
        db.session.commit()
    assert fields(app)[0][("item:2", "question")]["status"] == "ok"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(TranslationMark.id)).filter_by(unit="item:2")) >= 1


def test_ai_translates_everything_open_and_marks_it_unconfirmed(app, client, survey, monkeypatch):
    from app import ai

    choice(client, question="Wie oft nutzen Sie {{tool}}? Lücke [ ]")
    seen = {}

    def fake(instructions, prompt, purpose="text", item_type=None):
        seen["prompt"], seen["purpose"] = prompt, purpose
        entries = json.loads(prompt.split("Texts (JSON):\n", 1)[1])
        return json.dumps({e["id"]: "EN " + e["text"] for e in entries})

    monkeypatch.setattr(ai, "generate_text", fake)
    r = client.post("/surveys/1/translation/ai?lang=en")
    assert r.status_code == 200 and r.json["done"] >= 3 and r.json["left"] == 0
    assert seen["purpose"] == "translation" and "Survey title: Test" in seen["prompt"]
    f, counts = fields(app)
    assert f[("item:2", "question")]["target"] == "EN Wie oft nutzen Sie {{tool}}? Lücke [ ]"
    assert f[("item:2", "question")]["status"] == "ai" and counts["missing"] == 0
    html = client.get("/surveys/1/translation/").data.decode()
    assert "KI, ungeprüft" in html
    client.post("/surveys/1/translation/confirm?lang=en", data={"scope": "all"})
    f, counts = fields(app)
    assert f[("item:2", "question")]["status"] == "ok" and counts["ai"] == 0


def test_ai_answer_that_loses_a_gap_stays_open(app, client, survey, monkeypatch):
    from app import ai

    add(client, "cloze")                                                          # item 2
    client.post("/surveys/1/editor/items/2", data={"code": "cl", "text": "Ein Roboter ist [] und [  ]."})
    monkeypatch.setattr(ai, "generate_text", lambda i, p, purpose="text", item_type=None: json.dumps(
        {e["id"]: ("A robot is nice." if "[" in e["text"] else "EN") for e in json.loads(p.split("Texts (JSON):\n", 1)[1])}))
    client.post("/surveys/1/translation/ai?lang=en")
    assert fields(app)[0][("item:2", "text")]["status"] == "missing"


def test_validated_construct_is_not_translated(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})   # item 2: de and en validated
    grant_instrument(client.application, "kut")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kut"})     # item 3: en only a working translation
    f, counts = fields(app)
    assert f[("item:2", "question")]["status"] == "validated" and counts["validated"] > 0
    assert f[("item:3", "question")]["status"] != "validated"
    r = client.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "item:2", "path": "question", "text": "x"})
    assert r.status_code == 400
    html = client.get("/surveys/1/translation/").data.decode()
    assert "Validierte Fassung aus „Konstrukte“" in html and "keine validierte Fassung" in html


def test_zis_scale_without_version_says_a_translation_needs_consent(app, client, survey):
    """TB exists only in German: a translation is another form than the documented one, which the ZIS terms tie to
    the consent of the authors (rights review 2026-10-21)."""
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:tb"})
    html = client.get("/surveys/1/translation/?lang=en").data.decode()
    assert "Nach den ZIS-Bedingungen braucht sie das Einverständnis der Autorinnen und Autoren" in html


def test_end_texts_and_block_titles_are_translated_too(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.end_texts = {"de": {"thanks": "Danke fürs Mitmachen!"}}
        s.blocks[1].show_title = True
        s.blocks[1].titles = {"de": "Ihre Erfahrungen"}
        block = s.blocks[1].id
        db.session.commit()
    f, _ = fields(app)
    assert f[("end", "thanks")]["status"] == "missing" and f[(f"block:{block}", "title")]["status"] == "missing"
    client.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "end", "path": "thanks", "text": "Thanks for taking part!"})
    client.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": f"block:{block}", "path": "title", "text": "Your experience"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.end_texts["en"]["thanks"] == "Thanks for taking part!" and s.blocks[1].titles["en"] == "Your experience"


def test_viewer_sees_but_cannot_save(app, client, survey):
    choice(client)
    with app.app_context():
        from app.models import SurveyMember

        for m in db.session.scalars(db.select(SurveyMember).filter_by(survey_id=1)):
            m.role = "viewer"
        db.session.commit()
    html = client.get("/surveys/1/translation/").data.decode()
    assert "tr-fixed" in html and "data-tr-ai" not in html
    r = client.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "item:2", "path": "question", "text": "x"})
    assert r.status_code in (302, 403)


def test_start_needs_complete_translation(app, client, survey):
    from tests.conftest import fill_consent

    app.config["TRANSLATION_CHECK"] = True
    choice(client)
    fill_consent(client)
    with app.app_context():
        from app import design

        found = [f.message for f in design.check(db.session.get(Survey, 1))]
    assert any(m.startswith("Übersetzung English unvollständig") for m in found)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        assert db.session.get(Survey, 1).status == "draft"                       # not started
    from app import translations

    with app.app_context():
        s = db.session.get(Survey, 1)
        for u in translations.overview(s, "en")[0]:
            value = u.get("en")
            for f in u.fields:
                if f["status"] == "missing":
                    value = translations.set_path(value, u.get("de"), f["path"], "EN " + f["source"])
            u.set("en", value)
        db.session.commit()
    client.post("/surveys/1/start", data={})
    with app.app_context():
        assert db.session.get(Survey, 1).status == "running"


def test_title_in_every_language(app, client, survey):
    f, _ = fields(app)
    assert f[("title", "title")]["status"] == "missing" and f[("title", "title")]["source"] == "Test"
    client.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "title", "path": "title", "text": "Test survey"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.title == "Test" and s.title_in("en") == "Test survey" and s.title_in("de") == "Test"


def test_general_texts_after_the_blocks_and_validated_text_shown(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})   # item 2
    html = client.get("/surveys/1/translation/").data.decode()
    assert html.index("Allgemeine Texte") > html.index('id="item-2"')
    assert 'class="tr-validated"' in html and "validierte Fassung" in html
    with app.app_context():                                                       # a language added later
        item = db.session.get(Item, 2)
        item.texts = {"de": item.texts["de"]}
        db.session.commit()
    fields(app)
    with app.app_context():
        assert db.session.get(Item, 2).texts.get("en", {}).get("question")         # the validated English version


def test_licensed_wording_never_goes_to_the_ai(app, client, survey, monkeypatch):
    """GDMS (APA PsycTests) may go only to the participants: the AI translation leaves it out, also as glossary,
    and the page says why (copyright audit 2026-10-16, U4)."""
    from app import ai, constructs

    client.post("/surveys/new", data={"title": "GDMS", "languages": ["en", "de"], "default_language": "en"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="GDMS"))
        sid, block = s.id, s.blocks[-1].id
    grant_instrument(client.application, "gdms", survey_id=sid)
    client.post(f"/surveys/{sid}/editor/blocks/{block}/items", data={"type": "construct:gdms"})
    with app.app_context():
        item = next(i for b in db.session.get(Survey, sid).blocks for i in b.items if (i.config or {}).get("construct"))
        wording = item.texts["en"]["question"] or next(iter(item.texts["en"]["statements"].values()))
        assert item.config["construct"]["key"] in constructs.NOT_SHARED
    seen = []
    monkeypatch.setattr(ai, "generate_text", lambda i, p, purpose="text", item_type=None: seen.append(p) or "{}")
    client.post(f"/surveys/{sid}/translation/ai?lang=de")
    assert seen and all(wording not in p for p in seen)  # the title went, the wording did not
    html = client.get(f"/surveys/{sid}/translation/?lang=de").data.decode()
    assert "Die KI übersetzt ihn nicht" in html
