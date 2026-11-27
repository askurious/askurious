"""AI help at the fields of one question (experimental "ai_fields": app/ai_fields.py, editor.ai_field and
ai_field_apply): "Formulieren" at the question text, "Vorschlagen" at the answer options and the statements. Concept
"KI-Hilfe direkt an Fragen" (2026-11-27), E1 to E9."""
import json

import pytest

from app import ai_fields
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add, experiments_on

E = "/surveys/1/editor"


@pytest.fixture
def calls(monkeypatch):
    """The AI call replaced in the module that makes it: what was sent, and the answer the test sets."""
    seen = {"answer": {}, "sent": []}

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None, **_more):
        seen["sent"].append({"instructions": instructions, "prompt": prompt, "purpose": purpose, "item_type": item_type,
                             "schema": schema})
        return json.dumps(seen["answer"])

    monkeypatch.setattr(ai_fields.ai, "generate_text", fake)
    return seen


def _item(app, item_id=2):
    return db.session.get(Item, item_id)


def _write(app, item_id=2, question=None, options=None, statements=None, config=None):
    """Texts and rows of an item straight into the database (source language "de")."""
    with app.app_context():
        item = _item(app, item_id)
        cfg, texts = dict(item.config or {}), {k: dict(v) for k, v in (item.texts or {}).items()}
        de = texts.setdefault("de", {})
        if question is not None:
            de["question"] = question
        for head, rows in (("options", options), ("statements", statements)):
            if rows is None:
                continue
            cfg[head] = [{"id": f"r{n}", "code": "", "reverse": False} if head == "statements" else
                         {"id": f"r{n}", "code": str(n)} for n, _ in enumerate(rows, start=1)]
            de[head] = {f"r{n}": text for n, text in enumerate(rows, start=1)}
        cfg.update(config or {})
        item.config, item.texts = cfg, texts
        db.session.commit()


def _choice(app, client, kind="single_choice"):
    experiments_on(client, "ai_fields")
    add(client, kind)
    _write(app, question="Mit welchem Verkehrsmittel fahren Sie meistens zur Arbeit?", options=["Auto"])


def _page(client, item_id=2, lang=None):
    return client.get(f"{E}/items/{item_id}" + (f"?lang={lang}" if lang else "")).data.decode()


def _apply(client, part, item_id=2, **data):
    if "rows" in data:
        data["rows"] = json.dumps(data["rows"])
    return client.post(f"{E}/items/{item_id}/ai-field/{part}/apply", data=data)


def _undo(client):
    return client.post(f"{E}/undo", follow_redirects=True).data.decode()


# --- who sees the buttons, who reaches the routes ---------------------------------------------------------------------

def test_without_the_feature_no_button_and_the_routes_answer_404(app, client, survey, calls):
    add(client, "single_choice")
    _write(app, question="Frage?", options=["Auto"])
    html = _page(client)
    assert "data-ai-field" not in html and "data-ai-fields" not in html
    assert client.post(f"{E}/items/2/ai-field/options").status_code == 404
    assert _apply(client, "options", rows=[{"text": "Fahrrad"}]).status_code == 404
    assert not calls["sent"]
    with app.app_context():
        assert len(_item(app).config["options"]) == 1


def test_with_the_feature_the_buttons_stand_at_the_fields(app, client, survey):
    _choice(app, client)
    html = _page(client)
    assert 'data-ai-field="question"' in html and 'data-ai-field="options"' in html
    assert 'data-ai-field="statements"' not in html
    assert "/items/2/ai-field/PART" in html and "/items/2/ai-field/PART/apply" in html
    # the translation view has none (E7)
    other = _page(client, lang="en")
    assert "data-ai-field" not in other


def test_generative_ai_switched_off_shows_and_does_nothing(app, client, survey, calls):
    from app.models import User

    _choice(app, client)
    with app.app_context():
        db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X")).ai_enabled = False
        db.session.commit()
    assert "data-ai-field" not in _page(client)
    assert client.post(f"{E}/items/2/ai-field/options").status_code == 404
    assert _apply(client, "question", text="Neu?").status_code == 404
    assert not calls["sent"]


def test_a_likert_matrix_has_the_button_at_the_statements_not_at_the_instruction(app, client, survey):
    experiments_on(client, "ai_fields")
    add(client, "likert")
    html = _page(client)
    assert 'data-ai-field="statements"' in html and 'data-ai-field="question"' not in html
    assert client.post(f"{E}/items/2/ai-field/question", data={"idea": "x"}).status_code == 404


def test_consent_locked_construct_and_unshared_wording_are_closed(app, client, survey, calls, monkeypatch):
    """Never to an AI (E6): the consent, a validated construct while locked, wording that may not be shared; no
    button, 404, and nothing of them in a prompt."""
    from app import constructs

    experiments_on(client, "ai_fields")
    for part in ai_fields.PARTS:  # item 1 is the consent
        assert client.post(f"{E}/items/1/ai-field/{part}", data={"idea": "x"}).status_code == 404
    assert "data-ai-field" not in _page(client, 1)
    client.post(f"{E}/blocks/2/items", data={"type": "construct:tb"})  # item 2: a validated scale, locked
    assert "data-ai-field" not in _page(client)
    assert client.post(f"{E}/items/2/ai-field/statements", data={"idea": "x"}).status_code == 404
    assert _apply(client, "statements", rows=[{"text": "Neu"}]).status_code == 404
    # unlocked, but its wording may only go to participants
    add(client, "likert")
    _write(app, 3, statements=["Geheimer Wortlaut."], config={"construct": {"key": "geheim", "unlocked": True}})
    monkeypatch.setattr(constructs, "NOT_SHARED", {"geheim"})
    assert "data-ai-field" not in _page(client, 3)
    assert client.post(f"{E}/items/3/ai-field/statements", data={"idea": "x"}).status_code == 404
    assert not calls["sent"]


def test_a_running_survey_is_refused(app, client, survey, calls):
    _choice(app, client)
    with app.app_context():
        db.session.get(Survey, 1).status = "active"
        db.session.commit()
    assert "data-ai-field" not in _page(client)
    r = client.post(f"{E}/items/2/ai-field/options")
    assert r.status_code == 409 and "Textkorrekturen" in r.get_json()["error"]
    r = _apply(client, "options", rows=[{"text": "Fahrrad"}])
    assert r.status_code == 409
    assert not calls["sent"]
    with app.app_context():
        assert len(_item(app).config["options"]) == 1


def test_someone_who_may_only_view_is_refused(app, client, survey, calls):
    from app.models import User

    _choice(app, client)
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0000-0000-002X&name=Gast")
    other.post("/terms/", data={"accept": "1"})
    experiments_on(other, "ai_fields", orcid="0000-0000-0000-002X")
    with app.app_context():
        from app.models import SurveyMember

        guest = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-002X"))
        db.session.add(SurveyMember(survey_id=1, user_id=guest.id, role="viewer"))
        db.session.commit()
    assert other.get(f"{E}/items/2").status_code == 200
    # the editor sends a viewer's post back to the page (302) before the route's own 404
    assert other.post(f"{E}/items/2/ai-field/options").status_code in (302, 404)
    rows = json.dumps([{"text": "Fahrrad"}])
    assert other.post(f"{E}/items/2/ai-field/options/apply", data={"rows": rows}).status_code in (302, 404)
    assert not calls["sent"]
    with app.app_context():
        assert len(_item(app).config["options"]) == 1


# --- answer options ----------------------------------------------------------------------------------------------------

def test_options_are_suggested_from_the_element_and_the_study_context(app, client, survey, calls):
    _choice(app, client, "multiple_choice")
    calls["answer"] = {"options": [{"text": "Auto", "with_text": False, "exclusive": False},  # there already
                                   {"text": "Fahrrad oder E-Bike", "with_text": False, "exclusive": False},
                                   {"text": "Anderes, und zwar", "with_text": True, "exclusive": False},
                                   {"text": "Ich arbeite nur zu Hause", "with_text": False, "exclusive": True}]}
    d = client.post(f"{E}/items/2/ai-field/options", data={"seen": ["Zu Fuß"]}).get_json()
    assert [o["text"] for o in d["options"]] == ["Fahrrad oder E-Bike", "Anderes, und zwar", "Ich arbeite nur zu Hause"]
    assert d["options"][1]["with_text"] and d["options"][2]["exclusive"]
    sent = calls["sent"][0]
    assert sent["purpose"] == "field_options" and sent["item_type"] == "multiple_choice" and sent["schema"]
    assert "Mit welchem Verkehrsmittel" in sent["prompt"] and "Auto" in sent["prompt"] and "Title: Test" in sent["prompt"]
    assert "Zu Fuß" in sent["instructions"] and "formal: Sie" in sent["instructions"]
    with app.app_context():
        assert len(_item(app).config["options"]) == 1  # nothing changes before the take-over


def test_a_single_choice_takes_no_exclusive_mark_and_needs_a_question_text(app, client, survey, calls):
    _choice(app, client)
    calls["answer"] = {"options": [{"text": "Keines davon", "with_text": False, "exclusive": True}]}
    d = client.post(f"{E}/items/2/ai-field/options").get_json()
    assert d["options"] == [{"text": "Keines davon", "with_text": False, "exclusive": False}]
    _write(app, question="")
    d = client.post(f"{E}/items/2/ai-field/options").get_json()
    assert "Fragetext" in d["error"] and len(calls["sent"]) == 1


def test_appending_options_is_one_step_and_replaces_nothing(app, client, survey):
    _choice(app, client, "multiple_choice")
    r = _apply(client, "options", rows=[{"text": "  Fahrrad\n oder E-Bike ", "with_text": False, "exclusive": False},
                                        {"text": "Anderes, und zwar", "with_text": True},
                                        {"text": "Nichts davon", "exclusive": True},
                                        {"text": "auto"},  # there already: left out
                                        {"text": "x" * 900}, {"nonsense": 1}, "text"])
    assert r.get_json()["done"] is True
    with app.app_context():
        item = _item(app)
        options, labels = item.config["options"], item.texts["de"]["options"]
        assert [labels[o["id"]] for o in options][:4] == ["Auto", "Fahrrad oder E-Bike", "Anderes, und zwar", "Nichts davon"]
        assert len(options) == 5 and len(labels[options[4]["id"]]) == ai_fields.MAX_ROW
        assert [o["code"] for o in options] == ["1", "2", "3", "4", "5"]
        assert options[2].get("text") is True and options[3].get("exclusive") is True and "text" not in options[1]
        assert "options" not in item.texts.get("en", {}) or not set(item.texts["en"]["options"]) - {"r1"}
        assert not any(o.get("ai") for o in options)  # options carry no mark (E4)
    assert "angehängt" in _page(client)
    _undo(client)
    with app.app_context():
        item = _item(app)
        assert [o["id"] for o in item.config["options"]] == ["r1"] and item.texts["de"]["options"] == {"r1": "Auto"}


def test_nothing_chosen_changes_nothing(app, client, survey):
    _choice(app, client)
    assert "nichts geändert" in _apply(client, "options", rows=[]).get_json()["error"]
    assert "nichts geändert" in _apply(client, "options", rows="kein JSON").get_json()["error"]
    with app.app_context():
        assert len(_item(app).config["options"]) == 1


# --- question text -----------------------------------------------------------------------------------------------------

def test_three_wordings_and_the_chosen_one_replaces_the_question_text(app, client, survey, calls):
    _choice(app, client)
    calls["answer"] = {"variants": ["Wie kommen Sie zur Arbeit?", "Womit fahren Sie zur Arbeit?", "Dritte?", "Vierte?"]}
    d = client.post(f"{E}/items/2/ai-field/question", data={"idea": "überwiegend genutztes Verkehrsmittel"}).get_json()
    assert d["variants"] == ["Wie kommen Sie zur Arbeit?", "Womit fahren Sie zur Arbeit?", "Dritte?"]
    sent = calls["sent"][0]
    assert sent["purpose"] == "field_question" and "überwiegend genutztes Verkehrsmittel" in sent["prompt"]
    assert "Do not name published scales" in sent["instructions"]
    assert _apply(client, "question", text=" Womit fahren Sie\nzur Arbeit? ").get_json()["done"] is True
    with app.app_context():
        assert _item(app).texts["de"]["question"] == "Womit fahren Sie zur Arbeit?"
    _undo(client)
    with app.app_context():
        assert _item(app).texts["de"]["question"] == "Mit welchem Verkehrsmittel fahren Sie meistens zur Arbeit?"


def test_a_question_needs_a_keyword(app, client, survey, calls):
    _choice(app, client)
    assert "erfassen" in client.post(f"{E}/items/2/ai-field/question", data={"idea": "  "}).get_json()["error"]
    assert "nichts geändert" in _apply(client, "question", text=" ").get_json()["error"]
    assert not calls["sent"]


def test_informal_address_goes_into_the_instructions(app, client, survey, calls):
    _choice(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = _item(app)
        item.texts = {"de-du": item.texts["de"]}
        s.languages, s.default_language = ["de-du"], "de-du"
        db.session.commit()
    calls["answer"] = {"variants": ["Wie kommst du zur Arbeit?"]}
    assert client.post(f"{E}/items/2/ai-field/question", data={"idea": "Verkehrsmittel"}).get_json()["variants"]
    assert "informal: du" in calls["sent"][0]["instructions"]


# --- statements --------------------------------------------------------------------------------------------------------

def _matrix(app, client, statements=("Ich fühle mich im Bus sicher.", "Die Fahrpläne sind verlässlich.")):
    experiments_on(client, "ai_fields")
    add(client, "likert")
    _write(app, statements=list(statements))


def test_statements_follow_the_ones_there_and_are_marked_into_the_codebook(app, client, survey, calls):
    from app import design, export

    _matrix(app, client)
    calls["answer"] = {"statements": [{"text": "Ich finde an der Haltestelle schnell Auskunft.", "reverse": False},
                                      {"text": "Im Bus fühle ich mich unwohl.", "reverse": True}]}
    d = client.post(f"{E}/items/2/ai-field/statements", data={"n": "2", "reverse": "1"}).get_json()
    assert [s["reverse"] for s in d["statements"]] == [False, True]
    sent = calls["sent"][0]
    assert sent["purpose"] == "field_statements" and sent["item_type"] == "likert"
    assert "Ich fühle mich im Bus sicher." in sent["prompt"] and "reverse-keyed" in sent["instructions"]
    assert _apply(client, "statements", rows=d["statements"]).get_json()["done"] is True
    with app.app_context():
        item = _item(app)
        rows = item.config["statements"]
        assert len(rows) == 4 and [bool(s.get("ai")) for s in rows] == [False, False, True, True]
        assert rows[3]["reverse"] is True and "ai" not in item.config  # no AI scale as a whole
        labels = dict(item.kind.export_columns(item))
        code = item.code
        assert labels[f"{code}_3"].endswith("[KI-generiert, nicht validiert]")
        assert "KI-generiert" not in labels[f"{code}_1"]
        book = {r[0]: " ".join(str(c) for c in r) for r in export.codebook(db.session.get(Survey, 1))}
        assert "KI-generiert, nicht validiert" in book[f"{code}_4"] and "KI-generiert" not in book[f"{code}_2"]
    html = _page(client)
    assert html.count('class="stmt-ai"') == 2 and "2 von der KI" in html
    with app.test_request_context():
        from flask_login import login_user

        from app.models import User

        login_user(db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X")))
        findings = [f.message for f in design.check(db.session.get(Survey, 1))]
    assert any("2 Aussagen von einer KI formuliert" in t for t in findings)
    # the mark stays when the page saves the statements again
    with app.app_context():
        item = _item(app)
        form = {"question": "Instruktion", "code": item.code, "answer_mode": "nudge", "points": "5", "anchors": "agree",
                "labeling": "full", "stmt_id": [s["id"] for s in item.config["statements"]],
                "stmt_code": ["", "", "", ""], "stmt_text": ["A", "B", "C geändert", "D"],
                "stmt_reverse": [item.config["statements"][3]["id"]]}
    client.post(f"{E}/items/2", data=form)
    with app.app_context():
        item = _item(app)
        assert [bool(s.get("ai")) for s in item.config["statements"]] == [False, False, True, True]
        assert item.texts["de"]["statements"][item.config["statements"][2]["id"]] == "C geändert"
    _undo(client)
    _undo(client)
    with app.app_context():
        assert len(_item(app).config["statements"]) == 2


def test_an_empty_matrix_needs_to_know_what_to_measure(app, client, survey, calls):
    _matrix(app, client, statements=())
    assert "messen" in client.post(f"{E}/items/2/ai-field/statements").get_json()["error"]
    assert not calls["sent"]
    calls["answer"] = {"statements": [{"text": "Ich fahre gern Bus.", "reverse": True}]}
    d = client.post(f"{E}/items/2/ai-field/statements", data={"idea": "Zufriedenheit mit dem Nahverkehr", "own": "1"}).get_json()
    assert d["statements"] == [{"text": "Ich fahre gern Bus.", "reverse": False}]  # no reversed ones were asked for
    assert "Zufriedenheit mit dem Nahverkehr" in calls["sent"][0]["prompt"]


def test_a_survey_file_keeps_the_mark(app, client, survey):
    from app.items.likert import Likert

    rule = Likert.import_rules["statements"]
    assert rule([{"id": "a1", "code": "", "reverse": False, "ai": True}])[0]["ai"] is True


# --- a keyword that names a construct ------------------------------------------------------------------------------------

def test_a_keyword_naming_a_construct_offers_the_construct_before_any_ai_call(app, client, survey, calls):
    from app import constructs

    _matrix(app, client)
    with app.test_request_context():
        from flask_login import login_user

        from app.models import User

        login_user(db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X")))
        name = constructs.get("tb").name
    d = client.post(f"{E}/items/2/ai-field/statements", data={"idea": f"die {name} messen"}).get_json()
    assert not calls["sent"] and "statements" not in d
    hit = next(h for h in d["catalog"] if h["key"] == "tb")
    assert hit["name"] and hit["items"] > 0
    # a scale of the same type with statements: the existing way "Anhängen" of take_construct
    assert hit["url"].endswith("/items/2/take-construct?lang=de") and hit["fields"] == {"take": "tb|append"}
    before = 2
    client.post(hit["url"], data=hit["fields"])
    with app.app_context():
        assert len(_item(app).config["statements"]) > before
    # own suggestions all the same: now the AI is asked
    calls["answer"] = {"statements": [{"text": "Neue Technik probiere ich gern aus.", "reverse": False}]}
    d = client.post(f"{E}/items/2/ai-field/statements", data={"idea": f"die {name} messen", "own": "1"}).get_json()
    assert d["statements"] and len(calls["sent"]) == 1


def test_at_a_question_text_the_construct_comes_as_a_new_element_behind(app, client, survey, calls):
    from app import constructs

    _choice(app, client)
    with app.test_request_context():
        from flask_login import login_user

        from app.models import User

        login_user(db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X")))
        name = constructs.get("tb").name
    d = client.post(f"{E}/items/2/ai-field/question", data={"idea": name}).get_json()
    hit = next(h for h in d["catalog"] if h["key"] == "tb")
    assert hit["fields"] == {"type": "construct:tb", "position": "1"} and "/blocks/2/items" in hit["url"]
    assert not calls["sent"]
    client.post(hit["url"], data=hit["fields"])
    with app.app_context():
        block = _item(app).block
        assert [i.type for i in block.items] == ["single_choice", "likert"]
    assert client.post(f"{E}/items/2/ai-field/question", data={"idea": "Verkehrsmittel für den Arbeitsweg"}).get_json() \
        .get("catalog") is None


def test_no_hit_for_words_that_name_no_construct(app, client, survey):
    with app.test_request_context():
        from flask_login import login_user

        from app.models import User

        login_user(db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X")))
        s = db.session.get(Survey, 1)
        assert ai_fields.catalog_hits(s, "") == []
        assert ai_fields.catalog_hits(s, "die Skala der") == []
        assert ai_fields.catalog_hits(s, "Wie oft fahren Sie mit dem Bus?") == []


# --- the feature in the lists ---------------------------------------------------------------------------------------------

def test_the_feature_is_listed_in_german_and_english_and_counted_by_purpose(app):
    from app import ai_stats, experimental

    assert "ai_fields" in experimental.KEYS and "ai_fields" in experimental.FEATURES_EN
    for purpose in ("field_question", "field_options", "field_statements"):
        assert purpose in ai_stats.PURPOSES and purpose in ai_stats.PURPOSES_EN
