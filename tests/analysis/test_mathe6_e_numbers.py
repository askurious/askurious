"""Mathe-Audit 2026-10-06, package E "Zahlenformate und Rundung" (Z-1 to Z-9, Z-11; N-4, N-5): numbers researchers
type are read in the language of their page with a note on what cannot be read for sure, numbers on their pages are
written in it, prices for participants follow the survey language, and everything rounds half up."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from app import units
from app.extensions import db
from app.models import Item, Survey, User
from tests.conftest import add

E = "/surveys/1/editor"
STATIC = Path(__file__).resolve().parents[2] / "app" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node to run app/static/*.js")


def english(app):
    with app.app_context():
        user = db.session.get(User, 1)
        user.display = {**(user.display or {}), "lang": "en"}
        db.session.commit()


def config(app, item_id=2):
    with app.app_context():
        return dict(db.session.get(Item, item_id).config)


# --- the reader (Z-4) ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang, raw, value, noted", [
    ("de", "1.000", 1000, False), ("de", "1.299,99", 1299.99, False), ("de", "2,5", 2.5, False),
    ("de", "0.500", 0.5, False), ("de", "1,000", 1, True), ("de", "−5", -5, False), ("de", "12 €", 12, False),
    ("en", "1,000", 1000, False), ("en", "1,299.99", 1299.99, False), ("en", "2.5", 2.5, False),
    ("en", "1.000", 1, True), ("en", "10,000", 10000, False),
])
def test_read_entry_follows_the_language_and_notes_ambiguity(app, lang, raw, value, noted):
    with app.test_request_context():
        assert units.read_entry(raw, lang) == value
        notes = units.entry_notes()
        assert bool(notes) == noted
        if noted:
            assert "nicht eindeutig" in notes[0] and raw in notes[0]
        assert units.entry_notes() == []                     # each note is said once


def test_read_entry_known_value_and_no_number(app):
    with app.test_request_context():
        assert units.read_entry("1,125", "de", known=1.125) == 1.125 and units.entry_notes() == []  # shown so: no note
        assert units.read_entry("abc", "de", default=7) == 7 and "keine Zahl" in units.entry_notes()[0]
        assert units.read_entry("", "de", default=3) == 3 and units.entry_notes() == []
        assert units.read_entry("nan", "de") is None


def test_editor_fields_read_thousands_in_german(app, client, survey):
    """Z-4: "0 bis 1.000" was a slider from 0 to 1; PSM bound "1.000" was 1 €; the ACBC base "1.299,99" was 0."""
    add(client, "slider")                                                         # 2
    client.post(E + "/items/2", data=MultiDict([("code", "s"), ("question", "Wie viel?"), ("min", "0"), ("max", "1.000"),
                                                ("step", "10"), ("format", "currency"), ("stmt_id", "a"),
                                                ("stmt_code", ""), ("stmt_text", "Betrag")]))
    assert (config(app)["min"], config(app)["max"]) == (0, 1000)
    html = client.get(E + "/items/2").data.decode()
    assert 'name="max" class="narrow" inputmode="decimal" value="1000"' in html
    add(client, "psm")                                                            # 3
    client.post(E + "/items/3", data={"code": "wtp", "question": "?", "product": "E-Bike", "currency": "EUR",
                                      "decimals": "2", "order": "asc", "consistency": "required", "min": "10",
                                      "max": "1.000"})
    assert config(app, 3)["max"] == 1000
    add(client, "discounting")                                                    # 4
    client.post(E + "/items/4", data={"code": "dd", "question": "?", "mode": "adjusting", "amount": "1.000",
                                      "steps": "5"})
    assert config(app, 4)["amount"] == 1000


def test_ambiguous_number_is_said_after_saving(app, client, survey):
    """Z-4: "1,000" in German is read as 1 like a condition, and the autosave answer says so (once a value is stored,
    the field shows it the same way and nothing is said again)."""
    add(client, "slider")
    form = [("code", "s"), ("question", "Wie viel?"), ("min", "0"), ("max", "1,000"), ("step", "0,1"),
            ("format", "number"), ("stmt_id", "a"), ("stmt_code", ""), ("stmt_text", "Betrag"), ("autosave", "1")]
    d = client.post(E + "/items/2", data=MultiDict(form)).get_json()
    assert d["status"] == "saved" and config(app)["max"] == 1
    assert any("„1,000“ ist nicht eindeutig" in n and "1000" in n for n in d["notes"])
    d = client.post(E + "/items/2", data=MultiDict(form)).get_json()
    assert not any("nicht eindeutig" in n for n in d["notes"])                   # the stored 1 again: no new note


def test_english_page_reads_and_shows_english_numbers(app, client, survey):
    """Z-3: the bounds of a number field followed German on an English page ("10,000" was 10, so 5000 was refused);
    Z-4: the slider fields too. The fields show the stored value in the same language."""
    english(app)
    add(client, "text_input")                                                     # 2
    client.post(E + "/items/2", data={"code": "n", "question": "How many?", "validation": "integer", "min": "1",
                                      "max": "10,000", "max_length": "20"})
    assert config(app)["max"] == 10000
    add(client, "slider")                                                         # 3
    client.post(E + "/items/3", data=MultiDict([("code", "s"), ("question", "?"), ("min", "0"), ("max", "2.5"),
                                                ("step", "0.5"), ("format", "number"), ("stmt_id", "a"),
                                                ("stmt_code", ""), ("stmt_text", "x")]))
    assert (config(app, 3)["max"], config(app, 3)["step"]) == (2.5, 0.5)
    html = client.get(E + "/items/3").data.decode()
    assert 'name="max" class="narrow" inputmode="decimal" value="2.5"' in html


def test_acbc_base_price_and_surcharges(app, client, survey):
    from tests.analysis.test_acbc import acbc_form

    add(client, "acbc")
    form = acbc_form(price=True)
    form.setlist("price_base", ["1.299,99"])
    form.setlist("lvl_a2_price", ["0", "1.000", "2,5"])
    client.post(E + "/items/2", data=form)
    c = config(app)
    assert c["price"]["base"] == 1299.99
    assert [lv["price"] for lv in c["attributes"][1]["levels"]] == [0, 1000, 2.5]


def test_class_bounds_and_admin_prices(app):
    from app import admin, groupvars

    with app.test_request_context():
        assert groupvars.clean_bounds("1.000; 2.000") == [1000, 2000]               # was [1; 2]
        assert groupvars.clean_bounds("18,30,45") == [18, 30, 45] and groupvars.clean_bounds("2,5; 5") == [2.5, 5]
        assert groupvars.clean_bounds("1,000; 2,000") == [1, 2] and len(units.entry_notes()) == 2
        assert groupvars.clean_bounds("1,000", known=[1]) == [1] and units.entry_notes() == []
        assert admin._euro("1.000") == 1000 and admin._euro("0,15 €") == 0.15 and admin._euro("") is None


# --- Rechenwert and Ergebnisband (Z-6, decision 7) ---------------------------------------------------------------

def test_calc_and_score_band_read_german_and_warn(app, client, survey):
    add(client, "text_input")                                                     # 2
    client.post(E + "/items/2", data={"code": "x", "question": "x?", "validation": "decimal"})
    add(client, "calc")                                                           # 3
    d = client.post(E + "/items/3", data={"code": "c", "calc_a": "x", "calc_op": "mul", "calc_fixed": "1,000",
                                          "calc_places": "0", "autosave": "1"}).get_json()
    assert config(app, 3)["fixed"] == 1                                           # German as before
    assert any("„1,000“ ist nicht eindeutig" in n for n in d["notes"])
    client.post(E + "/items/3", data={"code": "c", "calc_a": "x", "calc_op": "mul", "calc_fixed": "1.000",
                                      "calc_places": "0"})
    assert config(app, 3)["fixed"] == 1000
    english(app)
    d = client.post(E + "/items/3", data={"code": "c", "calc_a": "x", "calc_op": "mul", "calc_fixed": "1,000",
                                          "calc_places": "0", "autosave": "1"}).get_json()
    assert config(app, 3)["fixed"] == 1 and any("“1,000” is ambiguous" in n for n in d["notes"])


def test_score_band_bounds_shown_as_read_back(app, client, survey):
    with app.app_context():
        from app.items.score_band import ScoreBand

        assert ScoreBand.upper_text(1.125) == "1,125" and ScoreBand.upper_text(1234567) == "1234567"


# --- writing in the page's language (Z-1, Z-2, Z-7, Z-11) --------------------------------------------------------

def test_simulator_writes_numbers_in_the_page_language(app):
    from app import simulator

    with app.test_request_context():
        assert simulator._money(1299.5) == "1.299,50" and simulator._pct(0.3333) == "33,3"
        token = simulator._NUMBERS.set("en")
        try:
            assert simulator._money(1000) == "1,000" and simulator._money(1299.5) == "1,299.50"
            assert simulator._pct(0.3333) == "33.3" and simulator._points(0.0123) == "+1.2"
        finally:
            simulator._NUMBERS.reset(token)


def test_simulator_price_field_and_reading(app):
    """Z-11: format "g" wrote 12345.67 as 12345.7 (taken over at the next computation) and 1234567.5 as 1.23457e+06;
    N-5: "1,299.99" was read as 1,29999."""
    from app import simulator

    assert simulator._machine(12345.67) == "12345.67" and simulator._machine(1234567.5) == "1234567.5"
    assert simulator._machine(75.0) == "75"
    assert simulator._float("1,299.99") == 1299.99 and simulator._float("1.299,99") == 1299.99
    assert simulator._float("49,5") == 49.5 and simulator._float("1299.99") == 1299.99


def test_simulator_page_in_english(app, client, survey):
    from tests.analysis.test_cbc_results import setup as cbc_setup
    from tests.analysis.test_conjoint_hb import simulate_segments

    cbc_setup(client, price_attr=True, none=True, tasks=12, versions=6)
    simulate_segments(app, 40, none_asc=-0.5)
    query = "sim=2&sim_n=2&sim_0_0=0&sim_0_2=0&sim_0_price=75&sim_1_0=1&sim_1_2=0&sim_1_price=50.5"
    html = client.get("/surveys/1/results?" + query).data.decode()
    assert re.search(r'res-bar-n">\d+,\d %', html)
    english(app)
    html = client.get("/surveys/1/results?" + query).data.decode()
    # English writes per cent without a space (UI/UX audit 2026-10-07, W111)
    assert re.search(r'res-bar-n">\d+\.\d%', html) and not re.search(r'res-bar-n">\d+,\d %', html)
    assert 'name="sim_1_price" value="50.5"' in html


def test_psm_prices_in_the_page_language(app):
    from app import psm_analysis
    from app.results import _Price

    class FakeItem:
        config = {"currency": "EUR"}

        def text(self, *_):
            return ""

    with app.test_request_context():
        assert _Price(FakeItem(), 2)(1299.5) == "1.299,50 €"
        from flask import g

        g.ui_lang = "en"
        assert _Price(FakeItem(), 2)(1299.5) == "€1,299.50"
    chart = psm_analysis.chart([0, 500, 1000, 1500], [("too_cheap", [1, 0.5, 0.2, 0])], (0, 1500))
    assert chart["x_values"] and units.tick_labels(chart["x_values"], lang="en")[-1].count(",") == 1


def test_psm_page_in_english(app, client, survey):
    from tests.analysis.test_psm import setup as psm_setup

    psm_setup(client, nms="1")
    with app.app_context():
        from app import dummy

        dummy.generate(db.session.get(Survey, 1), 30, seed=5)
        db.session.commit()
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    points = re.search(r'<table class="res-table psm-points">(.*?)</table>', html, re.S).group(1)
    assert re.search(r"\d,\d\d €", points)
    english(app)                                     # the same kept results, shown on an English page
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    points = re.search(r'<table class="res-table psm-points">(.*?)</table>', html, re.S).group(1)
    assert re.search(r"€[\d,]+\.\d\d", points) and not re.search(r"\d,\d\d €", points)
    ticks = re.findall(r'class="psm-tick"[^>]*text-anchor="middle">([^<]+)<', html)
    assert ticks and not any("," in t and len(t.split(",")[-1]) != 3 for t in ticks)


def test_warnings_with_the_page_decimal_mark(app):
    """Z-7: "R̂ max. 1.050, needed under 1.01" on an English page."""
    from flask import g

    from app import hb, hb_convergence

    with app.test_request_context():
        g.ui_lang = "en"
        text = hb_convergence.warning({"diag_ok": False, "rhat_max": 1.05, "ess_min": 500, "kept": 1000})
        assert "R̂ max. 1.050, needed under 1.01" in text            # W71: one level of brackets
        assert "Acceptance rate 0.05 far from the target" in hb.acceptance_warning(0.05)
        from app import conditions

        assert "is read as 1.5" in conditions.number_problem("1,500")


# --- participants: prices in their language (Z-5, Z-8) -----------------------------------------------------------

@pytest.mark.parametrize("lang, whole, part", [
    ("de", "1.299 €", "1.299,50 €"), ("fr", "1.299 €", "1.299,50 €"), ("es", "1.299 €", "1.299,50 €"),
    ("pl", "1.299 €", "1.299,50 €"), ("nl", "1.299 €", "1.299,50 €"), ("pt", "1.299 €", "1.299,50 €"),
    ("en", "€1,299", "€1,299.50"), ("zh-Hans", "€1,299", "€1,299.50"), ("ar", "€1,299", "€1,299.50"),
])
def test_acbc_price_for_participants(app, lang, whole, part):
    """Z-5/N-4: French, Spanish, Polish, Dutch and Portuguese showed "€1,299", read there as 1,30 €."""
    from app.items.acbc import ACBC

    class FakeItem:
        config = {"price": {"step": 1, "currency": "€"}}

    kind = ACBC()
    assert kind.format_price(FakeItem(), 1299, lang) == whole
    assert kind.format_price(FakeItem(), 1299.5, lang) == part


@pytest.mark.parametrize("lang, text", [("de", "51,56 €"), ("en", "51.56 €"), ("zh-Hans", "51.56 €"),
                                        ("ar", "51.56 €"), ("fr", "51,56 €")])
def test_discounting_amounts(app, lang, text):
    """Z-8: zh-Hans and ar wrote a decimal comma; Z-9: 51.555 was 51,55 (round half to even on the binary value)."""
    from app.items.discounting import Discounting

    class FakeItem:
        def text(self, *_):
            return "{n} €"

    assert Discounting().money(FakeItem(), 51.555, lang) == text


def test_class_labels_in_arabic():
    from app import groupvars

    assert groupvars._fmt(2.5, "ar") == "2.5" and groupvars._fmt(2.5, "fr") == "2,5"


# --- rounding half up (Z-9) ----------------------------------------------------------------------------------------

def test_hypothesis_and_methods_numbers_round_half_up():
    from app import methods_text
    from app.hypotheses import _de_lang

    assert _de_lang(0.945, 2, "en") == "0.95" and _de_lang(0.125, 2, "de") == "0,13" and _de_lang(-0.5, 1) == "−0,5"
    assert methods_text._num(0.125, "en") == "0.13" and methods_text._num(2.675, "de") == "2,68"


def test_vignette_p_value_keeps_its_decimals(app):
    with app.test_request_context():
        tpl = app.jinja_env.from_string('{% macro dec(v, places=2) %}{{ v|dec(places)|replace("-", "−") }}{% endmacro %}'
                                        "{{ dec(p, p_places(p)) }}")
        assert tpl.render(p=0.04996) == "0,04996"


# --- scripts ---------------------------------------------------------------------------------------------------

def _function(source, name):
    start = source.index(f"function {name}(")
    depth, i = 0, source.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        i += 1
        if depth == 0:
            return source[start:i]


@needs_node
def test_app_js_numbers_in_the_page_language():
    """Z-7/Z-9: the PSM tooltip wrote 0,285 as "28 %" and 1299.5 as "1299,50" on English pages."""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    code = "\n".join(_function(app_js, n) for n in ("de_en", "uiNum", "uiPercent"))
    script = ("var document = {documentElement: {lang: 'de'}};\n" + code + "\nvar out = [uiNum(1299.5, 2), uiPercent(0.285),"
              " uiNum(1.005, 2), uiNum(-0.004, 2)]; document.documentElement.lang = 'en';"
              " out.push(uiNum(1299.5, 2), uiPercent(0.285)); console.log(JSON.stringify(out));")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout
    assert out.strip() == '["1299,50","29 %","1,01","0,00","1299.50","29%"]'


def test_participant_and_live_scripts_follow_the_language_rule():
    """Z-5: the ACBC sum in the browser; Z-8: live.js wrote a point in French and Polish."""
    part = (STATIC / "participant.js").read_text(encoding="utf-8")
    block = part[part.index("[data-acbc-byo]"):part.index("[data-acbc-byo]") + 1200]
    assert "pointLang(info.dataset.lang)" in block and '.slice(0, 2) === "de"' not in block
    live = (STATIC / "live.js").read_text(encoding="utf-8")
    assert 'lang === "zh-Hans" || lang === "ar"' in live
