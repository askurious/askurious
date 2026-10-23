"""An answer option with a text field ("Anderes, und zwar: ...") in single and multiple choice."""
from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app import design, dummy, export, flow, results
from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Response, Survey
from tests.conftest import add, fill_consent


def _setup(client, type_="single_choice", code="sc", mode="optional"):
    add(client, type_)                                              # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", code), ("question", "Womit?"), ("answer_mode", mode),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Auto"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Rad"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Sonstiges, und zwar:"),
        ("option_text", "c")]))


def _answer(s, answers):
    t0 = datetime(2026, 9, 1, 10)
    s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                answers=answers))


def test_editor_stores_the_text_field_and_the_page_shows_it(app, client, survey):
    fill_consent(client)
    _setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert [o.get("text", False) for o in item.config["options"]] == [False, False, True]
        assert not item.kind.one_click(item)                         # typing needs "Next": no automatic continue
    assert 'name="option_text" value="c" aria-label="mit Textfeld" checked' in client.get(
        "/surveys/1/editor/items/2").data.decode()
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'name="i2_t_c"' in html and 'aria-label="Sonstiges, und zwar:"' in html and html.count("p-other") == 1


def test_single_choice_keeps_the_text_only_with_its_option(app, client, survey):
    _setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        parse = lambda form, lang="de": item.kind.parse_answer(item, MultiDict(form), lang)
        assert parse({"i2": "3", "i2_t_c": "  E-Scooter \n"}) == {"v": "3", "t": {"3": "E-Scooter"}}
        assert parse({"i2": "1", "i2_t_c": "E-Scooter"}) == "1"       # another option chosen: the text is no answer
        assert parse({"i2": "3"}) == "3"                              # optional: the option counts without a text
        assert parse({"i2_t_c": "E-Scooter"}) is None
        with pytest.raises(AnswerError):
            parse({"i2": "3", "i2_t_c": "x" * 501})
        item.answer_mode = "required"
        for lang, start in (("de", "Bitte tragen"), ("en", "Please type")):
            with pytest.raises(AnswerError) as e:
                parse({"i2": "3", "i2_t_c": "  "}, lang)
            assert str(e.value).startswith(start)
        assert parse({"i2": "1"}) == "1"
        item.answer_mode = "nudge"                                    # a hint once, in the participant's language
        assert "Textfeld" in item.kind.soft_warning(item, "3", "de")
        assert "text field" in item.kind.soft_warning(item, "3", "en")
        assert item.kind.soft_warning(item, {"v": "3", "t": {"3": "x"}}, "de") is None
        assert item.kind.soft_warning(item, "1", "de") is None
        # what was typed comes back when the page fails
        assert item.kind.display_value(item, MultiDict({"i2": "3", "i2_t_c": "abc"})) == {"v": "3", "t": {"3": "abc"}}


def test_multiple_choice_text_and_limits(app, client, survey):
    _setup(client, "multiple_choice", "mc")
    with app.app_context():
        item = db.session.get(Item, 2)
        parse = lambda pairs: item.kind.parse_answer(item, MultiDict(pairs), "de")
        assert parse([("i2", "1"), ("i2", "3"), ("i2_t_c", "Bahn")]) == {"v": ["1", "3"], "t": {"3": "Bahn"}}
        assert parse([("i2", "1"), ("i2_t_c", "Bahn")]) == ["1"]
        assert parse([("i2", "3")]) == ["3"]
        item.answer_mode = "required"
        with pytest.raises(AnswerError):
            parse([("i2", "1"), ("i2", "3")])


def test_export_codebook_and_results(app, client, survey):
    _setup(client)
    add(client, "multiple_choice")                                  # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "mc"), ("question", "Was noch?"), ("answer_mode", "optional"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "A"),
        ("option_id", "b"), ("option_code", "x"), ("option_label", "Anderes"), ("option_text", "b")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        _answer(s, {"2": {"v": "3", "t": {"3": "E-Scooter"}}, "3": {"v": ["1", "x"], "t": {"x": "=SUMME(A1)"}}})
        _answer(s, {"2": {"v": "3", "t": {"3": "e-scooter"}}, "3": ["x"]})
        _answer(s, {"2": "1", "3": []})
        _answer(s, {})
        db.session.commit()
        cols = [c for c, _ in export.columns(s)]
        assert cols[cols.index("sc"):cols.index("sc") + 2] == ["sc", "sc_3_text"]
        assert cols[cols.index("mc_1"):cols.index("mc_1") + 3] == ["mc_1", "mc_x", "mc_x_text"]
        rows = export.rows(s)
        assert [(r["sc"], r["sc_3_text"]) for r in rows] == [("3", "E-Scooter"), ("3", "e-scooter"), ("1", None), (None, None)]
        assert [(r["mc_x"], r["mc_x_text"]) for r in rows] == [(1, "=SUMME(A1)"), (1, None), (0, None), (None, None)]
        assert "'=SUMME(A1)" in export.to_csv(s)                      # a typed formula stays text in a spreadsheet
        book = {row[0]: row for row in export.codebook(s)}
        assert book["sc_3_text"][2] == "Text" and "Antwortoption 3" in book["sc_3_text"][4]
        assert "Sonstiges" in book["sc_3_text"][1] and book["sc"][3].startswith("1 = Auto")
        assert "Messniveau nominal" in book["mc_x_text"][4]
        # published data: the texts only with the tick for free texts
        assert export.open_columns(s) == {"sc_3_text", "mc_x_text"}
        public = export.to_csv(s, public=True)
        assert "E-Scooter" not in public and "sc_3_text" not in public and ",sc," in public
        assert "E-Scooter" in export.to_csv(s, public=True, open_answers=True)
        assert "with a text field: `sc_3_text`" in export.questionnaire_md(s)
        export.spss_zip(s), export.spss_sav(s)                        # both build with the text columns
        # results: the option is counted as before, the texts are listed
        sc, mc = results.compute(s)["items"]
        main, text = sc["columns"]
        assert main["type"] == "freq" and next(r for r in main["rows"] if r["value"] == "3")["count"] == 2
        assert text["type"] == "text" and text["texts"] == {"rows": [("E-Scooter", 2)], "more": 0}
        assert [c["type"] for c in mc["columns"]] == ["text"]          # the options themselves: one list (multi)
        assert [r["value"] for r in mc["multi"][0]["rows"]][-1] == "x"  # "Anderes, und zwar" last, whatever its count
    html = client.get("/surveys/1/results").data.decode()
    assert "E-Scooter" in html and "2×" in html


def test_conditions_read_the_chosen_code(app):
    cond = {"item": 2, "op": "eq", "value": "3"}
    assert flow.holds(cond, {2: {"v": "3", "t": {"3": "Bahn"}}}) and flow.holds(cond, {2: "3"})
    assert not flow.holds(cond, {2: {"v": "1", "t": {}}})
    assert flow.holds({"item": 2, "op": "contains", "value": "3"}, {2: {"v": ["1", "3"], "t": {"3": "x"}}})
    assert not flow.holds({"item": 2, "op": "answered"}, {2: None})


def test_codes_of_text_options_must_fit_a_variable_name(app, client, survey):
    _setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "options": [{"id": "a", "code": "1"}, {"id": "b", "code": "2"},
                                                  {"id": "c", "code": "über 30", "text": True}]}
        db.session.commit()
        errors = [f.message for f in design.check(db.session.get(Survey, 1)) if f.severity == design.ERROR]
    assert any("Optionen mit Textfeld" in e and "über 30" in e for e in errors)


def test_dummy_data_fill_the_text_field(app, client, survey):
    fill_consent(client)
    _setup(client, mode="required")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 30, seed=3) == 30
        rows = export.rows(s, sources=("dummy",))
        with_text = [r for r in rows if r["sc"] == "3"]
        assert with_text and all(r["sc_3_text"].startswith("Dummy-Antwort") for r in with_text)
        assert all(r["sc_3_text"] is None for r in rows if r["sc"] != "3")


def test_labels_outside_the_participant_page_lose_the_colon(app, client, survey):
    """UI audit 2026-10-12, U27: "Sonstiges, und zwar:" stands without its colon in results, quotas and conditions;
    the participant page keeps it (the text field follows there)."""
    from app import conditions
    from app.items.base import answer_label

    assert answer_label("Ich beschreibe mich selbst:") == "Ich beschreibe mich selbst"
    assert answer_label("Anderes, und zwar： ") == "Anderes, und zwar"
    assert answer_label("Uhrzeit 12:30") == "Uhrzeit 12:30" and answer_label(None) == ""
    _setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        assert flow.answer_codes(item)[-1] == ("3", "Sonstiges, und zwar")
        assert conditions.describe(s)["2"]["options"][-1] == ("3", "Sonstiges, und zwar")
        _answer(s, {"2": {"v": "3", "t": {"3": "E-Scooter"}}})
        db.session.commit()
        main = results.compute(s)["items"][0]["columns"][0]
        assert next(r for r in main["rows"] if r["value"] == "3")["label"] == "Sonstiges, und zwar"
    page = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'aria-label="Sonstiges, und zwar:"' in page


def test_the_test_run_summary_reads_like_answers(app, client, survey):
    """UI audit 2026-10-12, U28: the summary under the end page of a test run shows the code with its wording and
    the typed text in quotes, not the stored structure ({'3': 'E-Scooter'})."""
    from app.editor import preview_answer

    _setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert preview_answer(item, {"v": "3", "t": {"3": "E-Scooter"}}, "de") == "3 (Sonstiges, und zwar: „E-Scooter“)"
        assert preview_answer(item, {"v": "3", "t": {"3": "E-Scooter"}}, "en") == "3 (Sonstiges, und zwar: “E-Scooter”)"
        assert preview_answer(item, "1", "de") == "1 (Auto)"
        assert preview_answer(item, None, "de") == "—" and preview_answer(item, [], "de") == "—"
        consent = db.session.get(Item, 1)
        assert "{" not in preview_answer(consent, {"a": {"b": "c"}, "d": ["e", "f"]}, "de")
