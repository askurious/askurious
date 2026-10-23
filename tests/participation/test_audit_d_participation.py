"""Data audit 2026-10-05, package D: the participant flow and numbers typed in (T-1 … T-44)."""
import csv
import gzip
import io
from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app import flow
from tests.participation.test_participate import _pos
from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent
from tests.helpers.page_pos import page_pos

E = "/surveys/1/editor"


def _response(app):
    with app.app_context():
        return db.session.scalar(db.select(Response).filter_by(is_preview=False, source="real"))


def _start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert survey.status == Survey.RUNNING, survey.status
        return survey.code


def _export(client):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))


def _time_runs_out(app, seconds):
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        hold = dict(r.paradata["_hold"])
        hold["at"] = (datetime.fromisoformat(hold["at"]) - timedelta(seconds=seconds)).isoformat()
        r.paradata = {**r.paradata, "_hold": hold}
        db.session.commit()


# --- T-1: the timer of a page runs out ----------------------------------------------------------------------


def test_timer_keeps_a_personal_total_and_marks_rows_not_shown(app, client, survey):
    fill_consent(client)
    add(client, "text_input")    # 2 income
    add(client, "single_choice")  # 3 filter
    add(client, "page_break")    # 4
    add(client, "constant_sum")  # 5 total from the income
    add(client, "likert")        # 6 with a row shown only to those who answer "ja"
    client.post(E + "/items/2", data={"code": "income", "question": "Einkommen", "validation": "decimal"})
    client.post(E + "/items/3", data=MultiDict([("code", "f"), ("question", "Filter"),
                                                 ("option_id", "o1"), ("option_code", "1"), ("option_label", "ja"),
                                                 ("option_id", "o2"), ("option_code", "2"), ("option_label", "nein")]))
    client.post(E + "/items/5", data=MultiDict([
        ("code", "spend"), ("question", "Wofür?"), ("total", "100"), ("total_mode", "from"), ("total_from", "2"),
        ("total_missing", "skip"), ("option_id", "a"), ("option_code", "rent"), ("option_label", "Miete"),
        ("option_id", "b"), ("option_code", "food"), ("option_label", "Essen")]))
    client.post(E + "/items/6", data=MultiDict([
        ("code", "L"), ("points", "5"), ("labeling", "full"), ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B")]))
    with app.app_context():
        item = db.session.get(Item, 6)
        statements = [dict(s) for s in item.config["statements"]]
        statements[1]["when"] = {"item": 3, "op": "eq", "value": "1"}
        item.config = {**item.config, "statements": statements}
        db.session.commit()
    client.post(E + "/blocks/2", data={"title": "Block", "max_seconds": "30"})
    url = f"/s/{_start(app, client)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.get(url)
    p.post(url, data={"pos": page_pos(p, 1), "nav": "next", "i2": "2000", "i3": "2"})
    p.get(url)
    _time_runs_out(app, 40)
    p.post(url, data={"pos": page_pos(p, 2), "nav": "next", "time_up": "1",
                      "i5_rent": "1500", "i5_food": "500", "i5_touched": "1", "i6_s1": "3"})
    answers = _response(app).answers
    assert answers["5"] == {"rent": 1500, "food": 500}          # was None: the personal total was not known
    assert answers["6"]["_hidden"] == ["s2"]                   # the row not shown is marked (−77 in the export)


def test_timer_marks_carried_options_not_shown(app, client, survey):
    from tests.editor.test_carry import build

    build(app, client)
    client.post(E + "/blocks/2", data={"title": "Block", "max_seconds": "30"})
    url = f"/s/{_start(app, client)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.get(url)
    p.post(url, data={"pos": page_pos(p, 1), "nav": "next", "i2": ["1", "3"]})   # Smartphone and Laptop, not the Tablet
    p.get(url)
    _time_runs_out(app, 31)
    p.post(url, data={"pos": page_pos(p, 2), "nav": "next", "time_up": "1", "i5": ["1"]})
    row = _export(client)[0]
    assert row["abends_1"] == "1" and row["abends_2"] == "-77"   # the Tablet was not offered: −77, not 0


# --- T-2: a quota calculation before the first page that is stored ----------------------------------------


def _quota_before_consent(app, client):
    from tests.quotas.test_quotas import Q, cells_form, questionnaire

    questionnaire(client)
    client.post("/surveys/1/panel", data={"param_name": "src", "param_mode": "store", "param_required": "1",
                                          "param_allowed": "a, b"})
    client.post(Q + "/add")
    client.post(Q + "/1", data=cells_form([("Quelle A", "a", 5, [("p:src", "eq", "a")]),
                                          ("Quelle B", "b", 1, [("p:src", "eq", "b")])]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "branch", "id": "b1", "cond": {"item": "p:src", "op": "eq", "value": "a"},
             "children": [{"t": "block", "block": 2}]},
            {"t": "quota", "id": "qn1", "quotas": [1]},
            {"t": "end", "id": "qe1", "kind": "quotafull", "quotas": [1], "rule": "any", "text": {}, "link": "",
             "link_mode": "now"},
            {"t": "block", "block": 1}, {"t": "block", "block": 3}, {"t": "block", "block": 4}]}
        flow.normalize(s)
        db.session.commit()


def test_quota_calculation_passed_before_the_first_stored_page_is_taken_with_it(app, client, survey):
    from tests.quotas.test_quotas import counters

    _quota_before_consent(app, client)
    uid = _start(app, client)
    first, second = app.test_client(), app.test_client()
    first.get(f"/s/{uid}?src=b")
    second.get(f"/s/{uid}?src=b")                                  # opened while the cell still had room
    for p in (first, second):
        p.post(f"/s/{uid}", data=CONSENT_OK)
        p.post(f"/s/{uid}", data={"nav": "next"})
        p.post(f"/s/{uid}", data={"nav": "next", "i5": "1"})
    with app.app_context():
        found = db.session.scalars(db.select(Response).filter_by(is_preview=False).order_by(Response.id)).all()
        assert [r.status for r in found] == ["completed", "quotafull"]   # was: both completed, the place never taken
        assert all(r.quota for r in found)
    assert counters(app, "b")["done"] == 1
    third = app.test_client()                                      # the cell is full: turned away at the door
    third.get(f"/s/{uid}?src=b")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 2


def test_design_check_warns_when_nobody_is_sure_to_see_a_page_before_the_calculation(app, client, survey):
    from app import quotas

    _quota_before_consent(app, client)
    with app.app_context():
        problems = quotas.check(db.session.get(Survey, 1))
    assert any(level == "warning" and "keine Seite, die alle sehen" in text for level, text in problems)


# --- T-3: back from an external section in another browser -------------------------------------------------


def _external_with_param(app, client, mode):
    from tests.editor.test_external import build

    nid = build(app, client)
    client.post("/surveys/1/panel", data={"param_name": "grp", "param_mode": mode, "param_required": "0",
                                          "param_allowed": "a, b"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        ext = flow.find(flow.tree(s), nid)[0]
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1},
            {"t": "branch", "id": "b1", "cond": {"item": "p:grp", "op": "eq", "value": "a"},
             "children": [{"t": "block", "block": 2}]},
            ext, {"t": "block", "block": 3}]}
        flow.normalize(s)
        db.session.commit()


@pytest.mark.parametrize("mode", ["redirect", "store"])
def test_return_in_another_browser_keeps_the_url_parameters(app, client, survey, mode):
    from app import external
    from tests.editor.test_external import leave, start

    _external_with_param(app, client, mode)
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}?grp=a")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "33"})
    _, back = leave(p, uid)
    r = _response(app)
    # held while outside only where the mode stores nothing ("nur weiterleiten"), never in the export
    assert external.held_params(r) == ({"grp": "a"} if mode == "redirect" else {})
    if mode == "redirect":
        header = next(csv.reader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
        assert "grp" not in header                                 # not a column of the data set
    other = app.test_client()                                     # the web app opened in another browser
    other.get(back)
    assert external.held_params(_response(app)) == {}             # handed to the browser that came back
    other.get(f"/s/{uid}")
    other.post(f"/s/{uid}", data={"nav": "next"})
    r = _response(app)
    assert r.status == "completed" and r.answers.get("2") == 33   # was: the age dropped as "skipped"
    assert external.PARAMS not in (r.external or {})


def test_held_url_parameters_go_when_the_return_link_ran_out(app, client, survey):
    from app import external
    from app.models import utcnow
    from tests.editor.test_external import leave, start

    _external_with_param(app, client, "redirect")
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}?grp=a")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "33"})
    leave(p, uid)
    with app.app_context():
        assert external.sweep_params(utcnow()) == 0
        assert external.sweep_params(utcnow() + timedelta(seconds=external.MAX_SECONDS + 60)) == 1
        db.session.commit()
    assert external.held_params(_response(app)) == {}


# --- T-8 and T-6: the first page, not stored yet ------------------------------------------------------------


def _two_languages(app, client):
    fill_consent(client)
    add(client, "text_input")                                      # 2
    client.post(E + "/items/2", data={"code": "amount", "question": "Betrag", "validation": "decimal"})
    add(client, "page_break")
    add(client, "info")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de", "en"]
        s.language_choice = True
        db.session.commit()
    return _start(app, client)


def test_language_switch_on_the_first_page_stores_nothing(app, client, survey):
    uid = _two_languages(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}?lang=de")
    assert p.post(f"/s/{uid}", data={"set_lang": "en", "pos": page_pos(p, 0)}).status_code == 302
    assert p.post(f"/s/{uid}", data={"nav": "back", "pos": page_pos(p, 0)}).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0   # was: stored without consent
    html = p.get(f"/s/{uid}").data.decode()
    assert '<html lang="en"' in html                               # the page again, in the language picked
    p.post(f"/s/{uid}", data=CONSENT_OK)
    assert _response(app).language == "en"


def test_opening_the_link_again_later_starts_the_clock_afresh(app, client, survey, monkeypatch):
    import app.models as models
    from app import participate
    from tests.participation.test_participate import build

    build(client)
    url = f"/s/{_start(app, client)}"
    clock = {"now": datetime(2026, 10, 5, 9, 0, 0)}
    monkeypatch.setattr(models, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(participate, "utcnow", lambda: clock["now"])
    p = app.test_client()
    first = p.get(url).data.decode()                               # opened from the e-mail, tab closed
    clock["now"] = datetime(2026, 10, 6, 9, 0, 0)                  # the next day: opened again, taking part
    again = p.get(url).data.decode()
    clock["now"] += timedelta(seconds=20)
    p.post(url, data=CONSENT_OK)
    r = _response(app)
    assert r.started_at == datetime(2026, 10, 6, 9, 0, 0)          # was: the day before
    assert 'name="pos" value="r0-' in first and 'name="pos" value="r0-' in again


# --- T-9: the language switch reads the numbers in the language they were typed in ------------------------


def test_language_switch_reads_numbers_in_the_language_of_the_page(app, client, survey):
    uid = _two_languages(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}?lang=de")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data={"pos": page_pos(p, 1), "page_lang": "de", "set_lang": "en", "i2": "1.000"})
    r = _response(app)
    assert r.language == "en" and r.answers["2"] == 1000           # was: 1.0
    # an older page without the field: the language the participation had before the switch
    p.post(f"/s/{uid}", data={"pos": _pos(p.get(f"/s/{uid}").get_data(as_text=True)), "set_lang": "de", "i2": "1,500"})
    assert _response(app).answers["2"] == 1500


# --- T-13: a page sent twice counts its paradata once ------------------------------------------------------


def test_page_sent_twice_counts_focus_and_paste_once(app, client, survey):
    from tests.participation.test_participate import build

    build(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.focus_paste = True
        s.focus_inputs = True
        db.session.commit()
    url = f"/s/{_start(app, client)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data={**CONSENT_OK, "pos": page_pos(p, 0)})
    page = {"nav": "next", "pos": page_pos(p, 1), "i2_s1": "2", "i2_s2": "2", "i3": "41",
            "_fp_blur": "1", "_fp_paste": "1", "_fp_pchars": "20", "_fp_inputs": "2"}
    p.post(url, data=page)
    once = dict(_response(app).paradata.get("_fp"))
    p.post(url, data=page)                                         # the same page again (reload after a lost answer)
    assert _response(app).paradata.get("_fp") == once              # was: pchars 40, flagged as pasted


# --- T-35: a page sent twice says so on the current page ----------------------------------------------------


def test_page_sent_twice_leads_to_the_current_page_with_a_note(app, client, survey):
    from tests.participation.test_participate import build

    build(client)
    url = f"/s/{_start(app, client)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data={**CONSENT_OK, "pos": page_pos(p, 0)})
    assert "schon abgeschickt" not in p.get(url).data.decode()
    assert p.post(url, data={**CONSENT_OK, "pos": page_pos(p, 0)}).status_code == 302     # the consent page once more
    html = p.get(url).data.decode()
    assert "Diese Seite war schon abgeschickt; Sie sind jetzt hier." in html
    assert "schon abgeschickt" not in p.get(url).data.decode()                  # said once


def test_note_for_a_page_sent_twice_is_in_every_language():
    from app.participate import STALE_NOTE
    from app.surveys import LANGUAGE_NAMES
    from app.ui_i18n import tr

    texts = {lang: tr(lang, *STALE_NOTE) for lang in LANGUAGE_NAMES}
    assert len(set(texts.values())) == len(texts)
    assert "du bist" in texts["de-du"]


# --- T-14: a constant sum whose total is worked out from the same page --------------------------------------


def _constant_sum_from_calc(client, page_break):
    fill_consent(client)
    add(client, "text_input")                                      # 2 income
    if page_break:
        add(client, "page_break")                                  # 3
    calc = 4 if page_break else 3
    add(client, "calc")                                            # yearly = income × 12
    add(client, "calc")                                            # a chain: yearly × 1
    add(client, "constant_sum")
    client.post(E + "/items/2", data={"code": "income", "question": "Einkommen", "validation": "decimal"})
    client.post(E + f"/items/{calc}", data={"code": "yr", "question": "Jahr", "calc_a": "2", "calc_op": "mul",
                                            "calc_fixed": "12", "calc_places": "0"})
    client.post(E + f"/items/{calc + 1}", data={"code": "yr2", "question": "Jahr", "calc_a": str(calc), "calc_op": "mul",
                                                "calc_fixed": "1", "calc_places": "0"})
    client.post(E + f"/items/{calc + 2}", data=MultiDict([
        ("code", "spend"), ("question", "Wofür?"), ("total", "100"), ("total_mode", "from"),
        ("total_from", str(calc + 1)), ("total_missing", "skip"), ("option_id", "a"), ("option_code", "rent"),
        ("option_label", "Miete"), ("option_id", "b"), ("option_code", "food"), ("option_label", "Essen")]))
    return calc + 2


def test_constant_sum_with_a_total_from_its_own_page_is_an_error(app, client, survey):
    item_id = _constant_sum_from_calc(client, page_break=False)
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert any("derselben Seite" in p for p in item.kind.validate(item))   # was: nothing, nobody saw it
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.DRAFT    # the pre-launch check stops the start


def test_constant_sum_with_a_total_from_an_earlier_page_is_fine(app, client, survey):
    item_id = _constant_sum_from_calc(client, page_break=True)
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.kind.validate(item) == []


# --- T-15, T-26, T-44, T-38: numbers -------------------------------------------------------------------------


def test_chinese_writes_numbers_like_english():
    from app.items.base import number_text
    from app.items.text import _fmt, _num
    from app.units import fmt, writes_like_english

    assert writes_like_english("en") and writes_like_english("zh-Hans") and not writes_like_english("fr")
    assert _num("1,000", "zh-Hans") == 1000 and _num("1.250", "zh-Hans") == 1.25   # was 1 and 1250
    assert _num("1.000", "de") == 1000 and _num("1.000", "fr") == 1000
    assert number_text(2.5, "zh-Hans") == "2.5" and _fmt(2.5, "zh-Hans") == "2.5" and _fmt(2.5, "de") == "2,5"
    assert fmt(1234.5, "number", lang="zh-Hans", places=1) == "1,234.5"


def test_numbers_with_both_separators_need_groups_of_three_and_a_dash_is_a_minus():
    from app.items.text import _num

    assert _num("1.299,5") == 1299.5 and _num("1,000,000.5", "en") == 1000000.5
    for wrong in ("1,5.3", "1.2,5", "12.34,5"):
        with pytest.raises(ValueError):
            _num(wrong)
    assert _num("–5") == -5 and _num("−5") == -5


def test_psm_prices_are_read_in_the_language_of_the_participant():
    from app.items.psm import parse_price

    assert parse_price("1,500", 2, "en") == 1500 and parse_price("1,500", 2, "de") == 1.5   # was 1500 in German
    assert parse_price("1.500", 2, "de") == 1500 and parse_price("1.500", 2, "en") == 1.5
    assert parse_price("1,500", 2, "zh-Hans") == 1500
    assert parse_price("–5", 2, "de") == -5
    for wrong in ("1,5.3", "1.2,5"):
        with pytest.raises(ValueError):
            parse_price(wrong, 2, "de")
    assert parse_price("1.000", 2) == 1000   # without a language (the editor) as before


def test_values_of_an_external_section_are_read_in_machine_format():
    from app import external

    node = {"values": [{"name": "score", "kind": "number"}]}
    read = lambda raw: external.read_values(node, {"score": raw}).get("score")
    assert read("2.5") == 2.5 and read("2,5") == 2.5 and read("1.000") == 1 and read("-4") == -4
    for wrong in ("1,000.5", "1.000,5", "1.000.000", "1,000,000", "1e3", "1e400", "nan", "abc"):
        assert read(wrong) is None, wrong                          # thousands separators: not taken over


# --- T-41: a damaged table of IP addresses ------------------------------------------------------------------


def test_damaged_ip_table_gives_not_found_instead_of_an_error(app, tmp_path, monkeypatch):
    from app import geoip

    with open(geoip.DATA, "rb") as fh:
        whole = gzip.decompress(fh.read())
    cut = tmp_path / "geo_ip.bin.gz"
    cut.write_bytes(gzip.compress(whole[:len(whole) // 3]))       # an upload cut off
    monkeypatch.setattr(geoip, "DATA", str(cut))
    geoip._table.cache_clear()
    try:
        with app.app_context():
            assert geoip.lookup("8.8.8.8") == ("", "")
        broken = tmp_path / "broken.bin.gz"
        broken.write_bytes(b"\x1f\x8b not gzip at all")
        monkeypatch.setattr(geoip, "DATA", str(broken))
        geoip._table.cache_clear()
        with app.app_context():
            assert geoip.lookup("8.8.8.8") == ("", "") and geoip.month() == ""
    finally:
        geoip._table.cache_clear()
