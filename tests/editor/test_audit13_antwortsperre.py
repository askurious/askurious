"""Audit Daten und Berechnungen 2026-10-12, package B: the answer lock after "Zur Bearbeitung öffnen" (N-1 to N-10,
N-13, M-2, M-3, M-4, M-7, M-11; decisions 1 to 3). Each case: a question with a real answer in a survey opened for
editing again (qversion 2), a change in the element editor, and what now happens instead of reading the old answer
differently."""
import re
from types import SimpleNamespace

from werkzeug.datastructures import MultiDict

from app import answer_lock, export
from app.extensions import db
from app.items import get as kind_of
from app.items.base import shape_changes
from app.models import Item, Response, Survey
from app.rows import NOT_SHOWN
from tests.conftest import add, fill_consent

E = "/surveys/1/editor"
REFUSED = re.compile(r"„\w+“ hat schon echte Antworten")


class Fake(SimpleNamespace):
    """A stand-in for models.Item: code, type, config, texts (cfg = config)."""

    def __init__(self, type_, code, config, texts=None):
        super().__init__(type=type_, code=code, config=config, cfg=config, texts=texts or {"de": {}}, kind=kind_of(type_),
                         block=None, id=99)

    def t(self, lang=None):
        return (self.texts or {}).get(lang or "de") or {}

    def text(self, key, lang=None):
        return self.t(lang).get(key)


def _changes(type_, before, after, code="x", texts=None, texts_after=None):
    kind = kind_of(type_)
    return shape_changes(kind.answer_shape(Fake(type_, code, before, texts)),
                         kind.answer_shape(Fake(type_, code, after, texts_after or texts)))


def _reopened(app, item_id, answer):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.qversion = 2
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, pid="p1",
                                qversion=1, answers={str(item_id): answer}, layout={}))
        db.session.commit()


def _rows(app):
    with app.app_context():
        return list(export.rows(db.session.get(Survey, 1), with_hb=False))


def _row(app):
    return _rows(app)[0]


def _post(client, item_id, data):
    return client.post(f"{E}/items/{item_id}", data=data, follow_redirects=True).data.decode()


# --- N-1 delay discounting ---------------------------------------------------------------------------------------

def _choices(k, amount=100, delays=(7, 30, 182, 365, 1826), steps=6):
    out = ""
    for d in delays:
        now, step = amount / 2, amount / 4
        value = amount / (1 + k * d)
        for _ in range(steps):
            x = "L" if value > now else "S"
            out += x
            now += step if x == "L" else -step
            step /= 2
    return out


DELAYS = [("1w", 7, "in 1 Woche"), ("1m", 30, "in 1 Monat"), ("6m", 182, "in 6 Monaten"), ("1y", 365, "in 1 Jahr"),
          ("5y", 1826, "in 5 Jahren")]


def _dd_form(steps="6", amount="100", days=None):
    data = [("code", "dd"), ("question", "Was wäre Ihnen lieber?"), ("mode", "adjusting"), ("amount", amount),
            ("steps", steps), ("now", "heute"), ("unit", "{n} €")]
    for (did, d, label), dd in zip(DELAYS, days or [d for _, d, _ in DELAYS]):
        data += [("de_id", did), ("de_code", did), ("de_days", str(dd)), ("de_label", label)]
    return MultiDict(data)


def _discounting(app, client):
    fill_consent(client)
    add(client, "discounting")  # item 2
    client.post(f"{E}/items/2", data=_dd_form())
    _reopened(app, 2, {"c": _choices(0.01), "rt": []})
    return _row(app)


def test_discounting_steps_and_days_stay_with_real_answers(app, client, survey):
    before = _discounting(app, client)
    assert before["dd_k"] is not None
    assert REFUSED.search(_post(client, 2, _dd_form(steps="4")))
    assert REFUSED.search(_post(client, 2, _dd_form(days=[14, 60, 364, 730, 3652])))
    assert REFUSED.search(_post(client, 2, _dd_form(amount="50")))
    with app.app_context():
        assert db.session.get(Item, 2).config["steps"] == 6
    after = _row(app)
    assert (after["dd_k"], after["dd_ip_1y"]) == (before["dd_k"], before["dd_ip_1y"])
    # a text change still goes through
    form = _dd_form()
    form["question"] = "Was nähmen Sie lieber?"
    assert not REFUSED.search(_post(client, 2, form))


def test_discounting_choices_of_another_length_give_no_scores():
    kind = kind_of("discounting")
    item = Fake("discounting", "dd", {"mode": "adjusting", "amount": 100, "steps": 4,
                                     "delays": [{"id": d, "code": d, "days": n} for d, n, _ in DELAYS]})
    assert kind.scores(item, {"c": _choices(0.01), "rt": []}) == {}  # 30 choices for 5 x 4: not cut to fit
    assert kind.scores(item, {"c": _choices(0.01, steps=4), "rt": []})["k"] is not None


# --- N-2, N-10 text highlighting ------------------------------------------------------------------------------------

def _hl_form(passage="Der Roboter ist laut", ncats=None):
    data = [("code", "hl"), ("question", "Q"), ("passage", passage), ("cat_label", "positiv"), ("cat_label", "negativ"),
            ("cat_color", "green"), ("cat_color", "red")]
    return MultiDict(data + ([("ncats", ncats)] if ncats else []))


def test_highlight_text_and_categories_stay_with_real_answers(app, client, survey):
    add(client, "highlight")
    client.post(f"{E}/items/2", data=_hl_form(ncats="2"))
    _reopened(app, 2, {"4": 2})
    assert REFUSED.search(_post(client, 2, _hl_form("Ein sehr leiser Roboter hilft")))
    assert REFUSED.search(_post(client, 2, MultiDict([("code", "hl"), ("question", "Q"),
                                                       ("passage", "Der Roboter ist laut"), ("ncats", "1")])))
    with app.app_context():
        survey_ = db.session.get(Survey, 1)
        cols = dict(export.columns(survey_))
        item = db.session.get(Item, 2)
        notes = item.kind.codebook_info(item, "hl_w4", "de", "de")[2]
    assert cols["hl_w4"] == "Wort 4: laut"
    row = _row(app)
    assert row["hl_w4"] == 2 and row["hl_c2"] == 1
    assert any("Sprache der Teilnahme" in n for n in notes)  # N-10: two languages, the codebook says how to read it


# --- N-3 cloze, N-4 associations -----------------------------------------------------------------------------------

def test_cloze_gap_count_stays_after_reopen(app, client, survey):
    add(client, "cloze")
    client.post(f"{E}/items/2", data={"code": "cl", "question": "Q", "text": "Hauptstadt: [] , Fluss: []"})
    _reopened(app, 2, ["Paris", "Seine"])
    _post(client, 2, {"code": "cl", "question": "Q", "text": "Land: [] , Hauptstadt: [] , Fluss: []"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.text("text") == "Hauptstadt: [] , Fluss: []"
        res = item.kind.results(item, [_row(app)], "de")
    assert res["gaps"][0]["before"] == "Hauptstadt:" and res["gaps"][0]["answers"] == [("paris", 1)]
    # the wording with the same gaps may change
    _post(client, 2, {"code": "cl", "question": "Q", "text": "Die Hauptstadt: [] , der Fluss: []"})
    with app.app_context():
        assert db.session.get(Item, 2).text("text") == "Die Hauptstadt: [] , der Fluss: []"


def test_association_only_more_slots_after_reopen(app, client, survey):
    add(client, "association")
    client.post(f"{E}/items/2", data={"code": "as", "question": "KI", "slots": "4", "min": "1"})
    _reopened(app, 2, [{"t": w, "v": 1} for w in ("a", "b", "c", "d")])
    assert REFUSED.search(_post(client, 2, {"code": "as", "question": "KI", "slots": "3", "min": "1"}))
    assert _row(app)["as_4"] == "d"
    assert not REFUSED.search(_post(client, 2, {"code": "as", "question": "KI", "slots": "5", "min": "1"}))
    with app.app_context():
        assert db.session.get(Item, 2).config["slots"] == 5


# --- N-5 options and areas added: -77 for earlier versions ------------------------------------------------------------

def _mc_form(*options):
    data = [("code", "mc"), ("question", "Q")]
    for oid, code, label in options:
        data += [("option_id", oid), ("option_code", code), ("option_label", label)]
    return MultiDict(data)


def test_multiple_choice_option_added_after_reopen_is_not_shown_for_earlier(app, client, survey):
    add(client, "multiple_choice")
    client.post(f"{E}/items/2", data=_mc_form(("o1", "1", "A"), ("o2", "2", "B")))
    _reopened(app, 2, ["1"])
    page = _post(client, 2, _mc_form(("o1", "1", "A"), ("o2", "2", "B"), ("", "3", "C")))
    assert not REFUSED.search(page)  # adding stays allowed (decisions T-32)
    with app.app_context():
        item = db.session.get(Item, 2)
        added = item.config["options"][-1]
        assert added["since"] == 2 and "since" not in item.config["options"][0]
        assert item.kind.missing_codes(item, "mc_3") == (NOT_SHOWN,)
        assert item.kind.missing_codes(item, "mc_1") == ()
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, pid="p2",
                                qversion=2, answers={"2": ["3"]}, layout={}))
        db.session.commit()
        book = export.codebook(db.session.get(Survey, 1))
    old, new = _rows(app)
    assert (old["mc_1"], old["mc_2"], old["mc_3"]) == (1, 0, NOT_SHOWN)  # never shown: not "not ticked"
    assert (new["mc_1"], new["mc_3"]) == (0, 1)
    assert "ab Fragebogen-Fassung 2 ergänzt" in str(book)
    # a later save keeps the stamp, and a copy of the survey starts without it
    _post(client, 2, _mc_form(("o1", "1", "A"), ("o2", "2", "B"), (added["id"], "3", "C neu")))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["options"][-1]["since"] == 2
        assert all("since" not in o for o in answer_lock.without_since(item.config)["options"])


def test_option_added_before_the_first_start_has_no_stamp(app, client, survey):
    add(client, "multiple_choice")
    client.post(f"{E}/items/2", data=_mc_form(("o1", "1", "A"), ("", "2", "B")))
    with app.app_context():
        assert all("since" not in o for o in db.session.get(Item, 2).config["options"])


def test_hot_spot_area_moved_is_refused_and_added_is_not_shown():
    kind = kind_of("image_mark")
    a1 = {"id": "a1", "code": "1", "box": [0.1, 0.1, 0.2, 0.2]}
    before = {"mode": "areas", "areas": [a1]}
    assert _changes("image_mark", before, {**before, "areas": [{**a1, "box": [0.5, 0.5, 0.2, 0.2]}]})
    a2 = {"id": "a2", "code": "2", "box": [0.5, 0.5, 0.2, 0.2], "since": 2}
    assert _changes("image_mark", before, {**before, "areas": [a1, a2]}) == []  # adding stays allowed
    item = Fake("image_mark", "hs", {"mode": "areas", "areas": [a1, a2]})
    old, new = SimpleNamespace(qversion=1), SimpleNamespace(qversion=2)
    assert kind.export_response_values(item, ["1"], old) == {"hs_2": NOT_SHOWN}
    assert kind.export_response_values(item, ["1"], new) == {}
    assert kind.export_response_values(item, None, old) == {}
    assert kind.missing_codes(item, "hs_2") == (NOT_SHOWN,) and kind.missing_codes(item, "hs_1") == ()
    table = [{"hs_n": 1, "hs_1": 1, "hs_2": NOT_SHOWN}, {"hs_n": 1, "hs_1": 0, "hs_2": 1}]
    item.text = lambda key, lang=None: ""
    item.t = lambda lang=None: {"areas": {"a1": "A", "a2": "B"}}
    res = kind.results(item, table, "de")
    assert [r["share"] for r in res["rows"]] == [0.5, 1.0]  # the area added later over those who saw it


# --- N-6 to N-8, N-13, M-3: settings the answers are read by --------------------------------------------------------

def test_read_settings_are_fixed():
    assert _changes("constant_sum", {"total": 100}, {"total": 10})
    assert _changes("constant_sum", {"total": 100, "format": "number"}, {"total": 100, "format": "currency"})
    assert _changes("date_time", {"parts": "date"}, {"parts": "time"})
    assert _changes("assign", {"sides": "person"}, {"sides": "fixed"})
    assert _changes("psm", {"currency": "EUR", "nms": True}, {"currency": "USD", "nms": True})
    assert _changes("psm", {"currency": "EUR", "nms": True}, {"currency": "EUR", "nms": False})
    assert _changes("psm", {"currency": "EUR", "nms": False}, {"currency": "EUR", "nms": True}) == []  # switched on
    base = {"min": 0, "max": 100, "step": 1, "format": "number", "currency": "EUR", "rows": [{"id": "r1", "code": ""}]}
    assert _changes("slider", base, {**base, "format": "currency", "currency": "USD"})
    assert _changes("text_input", {"validation": "none"}, {"validation": "integer"})
    opts = [{"id": "a", "code": "a"}, {"id": "b", "code": "b"}]
    assert _changes("position2d", {"options": opts, "allow_skip": True}, {"options": opts, "allow_skip": False})
    assert _changes("position2d", {"options": opts, "allow_skip": False}, {"options": opts, "allow_skip": True}) == []
    assert _changes("voice", {"max_seconds": 60, "text_fallback": True}, {"max_seconds": 60, "text_fallback": False})
    assert _changes("geolocation", {"store": "1km"}, {"store": "exact"})  # finer: refused (N-13)
    assert _changes("geolocation", {"store": "100m"}, {"store": "1km"}) == []  # coarser: as in the field


def test_constant_sum_total_change_is_refused_in_the_editor(app, client, survey):
    add(client, "constant_sum")
    with app.app_context():
        item = db.session.get(Item, 2)
        item.code = "cs"
        db.session.commit()
    _reopened(app, 2, None)
    with app.app_context():
        item, s = db.session.get(Item, 2), db.session.get(Survey, 1)
        before = answer_lock.shape(item)
        item.config = {**item.config, "total": 10}
        assert answer_lock.refused(s, item, before) == ["die Gesamtsumme"]


def test_every_read_setting_has_words_in_both_languages():
    from app.items import all_types
    from app.items.base import READ_WORDS

    for kind in all_types():
        for key in kind.read_keys:
            assert key in READ_WORDS, (kind.key, key)
    assert all(len(pair) == 2 and all(pair) for pair in READ_WORDS.values())


# --- N-9 price levels of a choice-based conjoint -----------------------------------------------------------------------

def test_cbc_price_level_number_stays_while_fixed(app, client, survey):
    add(client, "conjoint")
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "attributes": [
            {"id": "p", "code": "preis", "visual": False, "price_attr": True,
             "levels": [{"id": "p1", "hint": ""}, {"id": "p2", "hint": ""}]}]}
        texts = dict(item.texts)
        texts["de"] = {**texts.get("de", {}), "attributes": {"p": "Preis"}, "levels": {"p1": "10 €", "p2": "20 €"}}
        item.texts = texts
        db.session.commit()
        from app import units

        form = MultiDict([("attr_id", "p"), ("attr_code", "preis"), ("attr_label", "Preis"),
                          ("lvl_p_id", "p1"), ("lvl_p_label", "10,00 €"), ("lvl_p_id", "p2"), ("lvl_p_label", "25 €")])
        item.kind.update_from_form(item, form, "de", False)  # as in the field / with real answers
        assert item.kind.level_numbers(item, 0) == [10, 20]
        assert item.t("de")["levels"]["p1"] == "10,00 €"  # the same number in another wording is taken
        assert any("25 €" in n for n in units.entry_notes())


# --- M-2 map with markers ------------------------------------------------------------------------------------------

def test_map_marker_follow_up_settings_are_fixed():
    q = {"id": "q1", "code": "lage", "kind": "choice", "points": 5, "required": False, "options": ["o1", "o2", "o3"]}
    before = {"min": 1, "max": 5, "questions": [q]}
    assert _changes("map_marker", before, {**before, "questions": [{**q, "options": ["o2", "o3"]}]})
    assert _changes("map_marker", before, {**before, "questions": [{**q, "kind": "scale", "points": 3}]})
    assert _changes("map_marker", before, {**before, "max": 2})
    assert _changes("map_marker", before, {**before, "max": 8}) == []
    assert _changes("map_marker", before, {**before, "questions": [{**q, "options": ["o1", "o2", "o3", "o4"]}]}) == []


def test_map_marker_option_line_deleted_while_running_keeps_the_wordings(app):
    kind = kind_of("map_marker")
    q = {"id": "q1", "code": "lage", "kind": "choice", "points": 5, "required": False, "options": ["o1", "o2", "o3"]}
    texts = {"de": {"question": "Wo?", "questions": {"q1": {"text": "Lage", "left": "", "right": "",
                                                            "options": {"o1": "Park", "o2": "Bahnhof", "o3": "Markt"}}}}}
    item = Fake("map_marker", "m", {"min": 1, "max": 3, "questions": [q]}, texts)
    item.survey = SimpleNamespace(default_language="de")
    with app.test_request_context():
        from app import units

        form = MultiDict([("question", "Wo?"), ("mq_id", "q1"), ("mq_text", "Lage"), ("mq_left", ""), ("mq_right", ""),
                          ("mq_options", "Bahnhof\nMarkt")])
        kind.update_from_form(item, form, "de", structural=False)
        assert item.texts["de"]["questions"]["q1"]["options"] == {"o1": "Park", "o2": "Bahnhof", "o3": "Markt"}
        assert units.entry_notes()
        form.setlist("mq_options", ["Stadtpark\nBahnhof\nMarkt"])  # one line reworded: taken
        kind.update_from_form(item, form, "de", structural=False)
        assert item.texts["de"]["questions"]["q1"]["options"]["o1"] == "Stadtpark"


# --- M-4 Q sort, M-7 ranking: nothing added after real answers -------------------------------------------------------

def test_q_sort_and_ranking_take_no_new_rows():
    seven = [{"id": f"s{i}", "code": f"s{i}"} for i in range(7)]
    eight = seven + [{"id": "s7", "code": "s7"}]
    before = {"options": seven, "range": 3, "capacities": [], "presort": False}
    assert _changes("q_sort", before, {**before, "options": eight})
    assert _changes("q_sort", before, {**before, "capacities": [1, 1, 1, 1, 1, 1, 1]}) == []  # the same grid written down
    opts = [{"id": "a", "code": "1"}, {"id": "b", "code": "2"}]
    assert _changes("ranking", {"options": opts}, {"options": opts + [{"id": "c", "code": "3"}]})
    # elsewhere adding stays allowed (decisions T-32)
    assert _changes("single_choice", {"options": opts}, {"options": opts + [{"id": "c", "code": "3"}]}) == []
    assert _changes("likert", {"points": 5, "statements": opts}, {"points": 5, "statements": opts + [{"id": "c", "code": "3"}]}) == []
    assert _changes("multiple_choice", {"options": opts}, {"options": opts + [{"id": "c", "code": "3"}]}) == []


def test_ranking_add_is_refused_in_the_editor_with_its_own_message(app, client, survey):
    add(client, "ranking")
    data = [("code", "rk"), ("question", "Q")]
    for oid, code in (("o1", "1"), ("o2", "2"), ("o3", "3")):
        data += [("option_id", oid), ("option_code", code), ("option_label", code)]
    client.post(f"{E}/items/2", data=MultiDict(data))
    _reopened(app, 2, {"1": 1, "2": 2, "3": 3})
    page = _post(client, 2, MultiDict(data + [("option_id", ""), ("option_code", "4"), ("option_label", "4")]))
    assert REFUSED.search(page) and "lassen sich ergänzen" not in page.split("hat schon echte Antworten")[1][:400]
    with app.app_context():
        assert len(db.session.get(Item, 2).config["options"]) == 3


def test_q_sort_grid_is_written_down_when_opened_for_editing(app, client, survey):
    from app import lifecycle

    add(client, "q_sort")
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "options": [{"id": f"s{i}", "code": f"s{i}"} for i in range(8)], "range": 3,
                       "capacities": []}
        s = db.session.get(Survey, 1)
        s.status = Survey.CLOSED
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, pid="p1",
                                qversion=1, answers={"2": {"q": {}}}, layout={}))
        db.session.commit()
        lifecycle.reopen_for_editing(s)
        db.session.commit()
        item = db.session.get(Item, 2)
        assert item.config["capacities"] == [1, 1, 1, 2, 1, 1, 1]
        assert answer_lock.note(item) is answer_lock.NOTE_CLOSED


# --- M-11 semantic differential ------------------------------------------------------------------------------------

def test_semantic_keeps_points_from_a_survey_file():
    kind = kind_of("semantic")
    item = Fake("semantic", "sd", {"points": 9, "pairs": [{"id": "p1", "code": "a", "reverse": False}]})
    form = MultiDict([("pair_id", "p1"), ("pair_code", "a"), ("pair_left", "kalt"), ("pair_right", "warm")])
    item.block = SimpleNamespace(survey=SimpleNamespace(languages=["de"], default_language="de"))
    kind.update_from_form(item, form, "de", True)
    assert item.config["points"] == 9
    form["points"] = "6"
    kind.update_from_form(item, form, "de", True)
    assert item.config["points"] == 6
