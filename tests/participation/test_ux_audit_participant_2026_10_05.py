"""Participant pages of the UI/UX audit 2026-10-05, package F: "weiß nicht" for matrix, rating and date (V41),
options that exclude the others (V36), confirming a given order (V37), the location's defaults (V39, V40), the end
page without a deletion code (V43), the survey's title on the first page (V34) and where messages stand (V44, V45)."""
from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app import export, results
from tests.participation.test_participate import _pos
from app.extensions import db
from app.items.base import DONT_KNOW, DONT_KNOW_CODE, AnswerError
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent
from tests.helpers.page_pos import page_pos


def _new(client, type_, **form):
    add(client, type_)
    with client.application.app_context():
        item_id = max(i.id for i in db.session.scalars(db.select(Item)))
    if form:
        client.post(f"/surveys/1/editor/items/{item_id}", data=form)
    return item_id


def _answers(app, values):
    """One completed real participation per dict {item id: stored answer}."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for v in values:
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                        answers={str(k): a for k, a in v.items()}))
        db.session.commit()


def _likert(client, dont_know=True):
    add(client, "likert")                                           # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
            ("code", "TA"), ("points", "5"), ("labeling", "full"), ("answer_mode", "optional"),
            *([("dont_know", "1")] if dont_know else []),
            ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Bus und Bahn sind gut ausgebaut"),
            ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "Ich fahre gern Rad"), ("stmt_reverse", "s2")]))


# --- V41: "weiß nicht" -------------------------------------------------------------------------------------------

def test_matrix_dont_know_is_an_answer_and_missing_in_export_and_results(app, client, survey):
    _likert(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["dont_know"] is True
        answer = item.kind.parse_answer(item, MultiDict({"i2_s1": "dk", "i2_s2": "2"}))
        assert answer == {"s1": DONT_KNOW, "s2": 2} and not item.kind.is_missing(item, answer)
        # exported as -98, never recoded (s2 is reversed: 2 -> 4), declared missing
        assert item.kind.export_values(item, {"s1": DONT_KNOW, "s2": DONT_KNOW}) == {"TA_1": DONT_KNOW_CODE, "TA_2": DONT_KNOW_CODE}
        assert item.kind.export_values(item, answer) == {"TA_1": DONT_KNOW_CODE, "TA_2": 4}
        assert DONT_KNOW_CODE in item.kind.missing_codes(item, "TA_1")
        book = {r[0]: r for r in export.codebook(db.session.get(Survey, 1))}
        assert "-98 = weiß nicht" in book["TA_1"][3]
    _answers(app, [{2: {"s1": 1, "s2": 5}}, {2: {"s1": 3, "s2": 4}}, {2: {"s1": DONT_KNOW, "s2": 4}}])
    with app.app_context():
        r = results.compute(db.session.get(Survey, 1))
        col = next(c for e in r["items"] if e["item"].id == 2 for c in e["columns"] if c["code"] == "TA_1")
        assert col["n"] == 2                                                # "weiß nicht" counts in no share
        assert [m["count"] for m in col["missing"]] == [1] and col["missing"][0]["label"] == "weiß nicht"
    html = client.get("/surveys/1/results").data.decode()
    assert "als fehlend gewertet: weiß nicht (1)" in html


def test_matrix_without_the_tick_takes_no_dont_know(app, client, survey):
    _likert(client, dont_know=False)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.parse_answer(item, MultiDict({"i2_s1": "dk", "i2_s2": "2"})) == {"s2": 2}
        assert item.kind.missing_codes(item, "TA_1") == ()


def test_matrix_page_shows_the_column_set_apart(app, client, survey):
    _likert(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "p-likert-dk" in html and 'name="i2_s1" value="dk"' in html and "p-point-dk" in html
    assert "1 von 2" in html                                               # the statement's number on phones (V38)


def test_rating_dont_know(app, client, survey):
    rid = _new(client, "rating", code="R", answer_mode="optional", style="numbers", min="0", max="10", dont_know="1")
    with app.app_context():
        item = db.session.get(Item, rid)
        assert item.kind.parse_answer(item, MultiDict({f"i{rid}": "7", f"i{rid}_dk": "1"})) == DONT_KNOW  # the tick wins
        assert item.kind.parse_answer(item, MultiDict({f"i{rid}": "7"})) == 7
        assert item.kind.export_values(item, DONT_KNOW) == {"R": DONT_KNOW_CODE}
        assert item.kind.missing_codes(item, "R") == (DONT_KNOW_CODE,)
        assert not item.kind.one_click(item)                               # the tick sends nothing by itself
        item.config = {**item.config, "min": -100}
        assert any("-98" in p for p in item.kind.validate(item))           # the code would lie on the scale
    _answers(app, [{rid: 4}, {rid: 8}, {rid: DONT_KNOW}])
    with app.app_context():
        r = results.compute(db.session.get(Survey, 1))
        col = next(e for e in r["items"] if e["item"].id == rid)["columns"][0]
        assert col["n"] == 2
    page = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert f'name="i{rid}_dk"' in page and "data-dont-know" in page


def test_date_dont_know_has_its_own_column(app, client, survey):
    did = _new(client, "date_time", code="D", question="Wann zuletzt Bus gefahren?", answer_mode="required",
               parts="year", span="past", minute_step="5", dont_know="1")
    with app.app_context():
        item = db.session.get(Item, did)
        assert item.kind.parse_answer(item, MultiDict({f"i{did}_dk": "1"})) == DONT_KNOW   # also where required
        with pytest.raises(AnswerError):
            item.kind.parse_answer(item, MultiDict({}))
        assert [c for c, _ in item.kind.export_columns(item)] == ["D", "D_dk"]
        assert item.kind.export_values(item, DONT_KNOW) == {"D": None, "D_dk": 1}
        assert item.kind.export_values(item, 1990) == {"D": 1990, "D_dk": 0}
        assert item.kind.export_values(item, None) == {"D": None, "D_dk": None}
        assert item.kind.date_kind(item, "D_dk") is None
        book = {r[0]: r for r in export.codebook(db.session.get(Survey, 1))}
        assert "1 = weiß nicht" in book["D_dk"][3]
    _answers(app, [{did: 1980}, {did: 1990}, {did: DONT_KNOW}])
    with app.app_context():
        r = results.compute(db.session.get(Survey, 1))
        e = next(e for e in r["items"] if e["item"].id == did)
        assert [c["code"] for c in e["columns"]] == ["D"] and e["columns"][0]["mean"] == 1985   # "weiß nicht" is no year
        row = export.rows(db.session.get(Survey, 1))[2]
        assert row["D"] in (None, "") and row["D_dk"] == 1


# --- V36: options that exclude the others -------------------------------------------------------------------------

def test_multiple_choice_exclusive_option_and_hint(app, client, survey):
    mid = _new(client, "multiple_choice")
    client.post(f"/surveys/1/editor/items/{mid}", data=MultiDict([
        ("code", "V"), ("question", "Welche Verkehrsmittel nutzen Sie?"), ("answer_mode", "optional"), ("multi_hint", "1"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Bus"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Rad"),
        ("option_id", "c"), ("option_code", "9"), ("option_label", "keines davon"), ("option_exclusive", "c")]))
    with app.app_context():
        item = db.session.get(Item, mid)
        assert [o.get("exclusive", False) for o in item.config["options"]] == [False, False, True]
        assert item.kind.parse_answer(item, MultiDict([(f"i{mid}", "9")])) == ["9"]
        with pytest.raises(AnswerError, match="keines davon"):
            item.kind.parse_answer(item, MultiDict([(f"i{mid}", "1"), (f"i{mid}", "9")]))
        assert item.kind.export_values(item, ["9"]) == {"V_1": 0, "V_2": 0, "V_9": 1}       # a column of its own
    page = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "Mehrere Antworten möglich." in page and 'value="9"  data-exclusive' in page
    client.post(f"/surveys/1/editor/items/{mid}", data=MultiDict([
        ("code", "V"), ("question", "Welche?"), ("answer_mode", "optional"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Bus"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Rad")]))
    page = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "Mehrere Antworten möglich." not in page                           # switched off


# --- V37: a given order neither moved nor confirmed -----------------------------------------------------------------

def test_untouched_order_of_an_optional_ranking_is_asked_once(app, client, survey):
    rid = _new(client, "ranking")
    client.post(f"/surveys/1/editor/items/{rid}", data=MultiDict([
        ("code", "P"), ("question", "Was ist wichtig?"), ("answer_mode", "optional"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "A"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "B")]))
    with app.app_context():
        item = db.session.get(Item, rid)
        assert "noch nicht bestätigt" in item.kind.soft_warning(item, None, "de")
        assert item.kind.soft_warning(item, ["2", "1"], "de") is None
        item.answer_mode = "required"
        assert item.kind.soft_warning(item, None, "de") is None


# --- V39, V40: location ---------------------------------------------------------------------------------------------

def test_location_defaults_to_one_kilometre_with_a_purpose_and_a_note(app, client, survey):
    gid = _new(client, "geolocation")
    with app.app_context():
        item = db.session.get(Item, gid)
        assert item.config["store"] == "1km"
        assert "Regionen" in item.text("question", "de")
        assert item.kind.stored_note(item, "de") == "Gespeichert wird nur: die Position, auf etwa 1 km gerundet."
        assert "1 km" in item.kind.stored_note(item, "en")


# --- V34, V43, V44, V45: first page, end page, messages ---------------------------------------------------------------

def _run(app, client):
    fill_consent(client)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        return f"/s/{db.session.get(Survey, 1).code}"


def test_first_page_names_the_survey_and_the_end_page_the_right_to_delete(app, client, survey):
    tid = _new(client, "text_input", code="alter", question="Wie alt sind Sie?", answer_mode="required",
               validation="integer", min="14", max="99")
    url = _run(app, client)
    with app.app_context():
        db.session.get(Survey, 1).erase_code = False                         # no deletion code: the line instead
        db.session.commit()
    p = app.test_client()
    first = p.get(url).data.decode()
    assert '<p class="p-survey-title"' in first and ">Test</p>" in first
    p.post(url, data=CONSENT_OK)
    failed = p.post(url, data={"nav": "next", f"i{tid}": "", "pos": page_pos(p, 1)}, follow_redirects=True).data.decode()
    assert "p-survey-title" not in failed                                    # only on the first page
    # the message right under the question, the valid range still there
    assert failed.index(f'id="q{tid}"') < failed.index(f'id="msg{tid}"') < failed.index(f'name="i{tid}"')
    assert "14 bis 99" in failed
    done = p.post(url, data={"nav": "next", f"i{tid}": "30", "pos": _pos(failed)}, follow_redirects=True).data.decode()
    assert "in diesem Browser auf der Seite „Datenschutz“ löschen" in done
