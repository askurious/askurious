"""What a participant can send beyond what the pages offer: oversized, repeated, out of range or unreadable
values (audit of 2026-10-01, package 2). Nothing of it may be stored, break an export or end in a server error."""
import json
import math
import re

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions

E = "/surveys/1/editor"


def _start(app, client):
    fill_questions(app)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        assert db.session.get(Survey, 1).status == "running"
        return f"/s/{db.session.get(Survey, 1).code}"


def _participant(app, url, query=""):
    p = app.test_client()
    p.get(url + query)
    p.post(url, data=CONSENT_OK)
    return p


def _answers(app):
    with app.app_context():
        return db.session.scalars(db.select(Response).order_by(Response.id.desc())).first().answers


def _choice(client, min_=""):
    add(client, "multiple_choice")                                    # 2
    client.post(E + "/items/2", data=MultiDict([("code", "mc"), ("question", "MC"), ("answer_mode", "optional"), ("min", min_),
                ("option_id", ""), ("option_code", "1"), ("option_label", "rot"),
                ("option_id", ""), ("option_code", "2"), ("option_label", "blau"),
                ("option_id", ""), ("option_code", "3"), ("option_label", "grün")]))


def test_whole_takes_plain_digits_only():
    from app.items.base import whole

    assert whole("7") == 7 and whole("007") == 7 and whole("123456789") == 123456789
    for bad in ("", "²", "٣", "-1", "1.0", " 1", "1234567890", "9" * 5000, None, 3, ["1"]):
        assert whole(bad) is None


def test_multiple_choice_keeps_each_option_once(app, client, survey):
    fill_consent(client)
    _choice(client)
    url = _start(app, client)
    p = _participant(app, url)
    body = "nav=next&" + "&".join(["i2=1"] * 20000) + "&i2=3"
    assert p.post(url, data=body, content_type="application/x-www-form-urlencoded").status_code == 302
    assert _answers(app)["2"] == ["1", "3"]
    with app.app_context():
        assert len(json.dumps(db.session.scalar(db.select(Response)).answers)) < 200


def test_repeated_option_does_not_count_towards_the_minimum(app, client, survey):
    fill_consent(client)
    _choice(client, min_="3")
    url = _start(app, client)
    p = _participant(app, url)
    r = p.post(url, data=MultiDict([("nav", "next"), ("i2", "1"), ("i2", "1"), ("i2", "1")]))
    assert r.status_code == 200 and "mindestens 3" in r.data.decode()


def test_participant_pages_take_small_forms_only(app, client, survey):
    fill_consent(client)
    _choice(client)
    url = _start(app, client)
    p = _participant(app, url)
    assert app.config["PARTICIPANT_FORM_BYTES"] == 2 * 1024 * 1024 < app.config["MAX_CONTENT_LENGTH"]
    body = "nav=next&i2=1&x=" + "a" * (2 * 1024 * 1024)
    r = p.post(url, data=body, content_type="application/x-www-form-urlencoded")
    assert r.status_code == 413 and "konnte nicht angenommen werden" in r.data.decode()
    assert "2" not in _answers(app)
    # researcher pages keep the larger limit (image uploads)
    r = client.post(E + "/items/2", data="code=mc&question=" + "a" * (2 * 1024 * 1024),
                    content_type="application/x-www-form-urlencoded")
    assert r.status_code == 302


def test_values_that_are_no_numbers_are_an_error_on_the_question(app, client, survey):
    fill_consent(client)
    add(client, "likert")                                             # 2
    client.post(E + "/items/2", data=MultiDict([("code", "TA"), ("answer_mode", "required"), ("points", "5"), ("labeling", "full"),
                ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A")]))
    add(client, "constant_sum")                                       # 3
    client.post(E + "/items/3", data=MultiDict([("code", "cs"), ("question", "Verteilen"), ("answer_mode", "optional"), ("total", "100"),
                ("option_id", ""), ("option_code", "1"), ("option_label", "A"), ("option_id", ""), ("option_code", "2"), ("option_label", "B")]))
    url = _start(app, client)
    p = _participant(app, url)
    for value in ("²", "9" * 5000, "٣"):
        r = p.post(url, data={"nav": "next", "i2_s1": value, "i3_1": value, "i3_touched": "1"})
        assert r.status_code == 200 and "has-error" in r.data.decode(), value
    assert "2" not in _answers(app)


def test_unreadable_hidden_fields_are_no_server_error(app, client, survey):
    fill_consent(client)
    add(client, "highlight")                                          # 2
    client.post(E + "/items/2", data=MultiDict([("code", "hl"), ("question", "Markieren"), ("answer_mode", "optional"),
                ("passage", "Der Roboter hilft im Haushalt"), ("ncats", "2")]))
    add(client, "assign")                                             # 3
    client.post(E + "/items/3", data=MultiDict([("code", "sort"), ("question", "Gut?"), ("answer_mode", "optional"), ("keys", "1"),
                ("cat_L_label", "gut"), ("cat_L_code", "1"), ("cat_R_label", "schlecht"), ("cat_R_code", "2"),
                ("el_id", "a"), ("el_code", "robot"), ("el_label", "Roboter"), ("el_id", "b"), ("el_code", "nurse"), ("el_label", "Pflege")]))
    url = _start(app, client)
    deep = "[" * 100000
    for hl in ('{"²": 1}', json.dumps({"9" * 5000: 1}), deep, '{"1": true}', '{"1": 1.5}', "[1, 2]"):
        p = _participant(app, url)
        r = p.post(url, data={"nav": "next", "i2": hl})
        assert r.status_code == 302, hl
        assert _answers(app).get("2") is None, hl  # nothing of it counts as a marking
    p = _participant(app, url)
    p.post(url, data={"nav": "next", "i2": '{"1": 1, "5": 2, "99": 1, "2": 9}'})
    assert _answers(app)["2"] == {"1": 1, "5": 2}
    for raw in ('[{"s": [], "c": "1"}]', deep, '[{"s": {"x": 1}, "c": "1"}]'):
        r = p.post(url, data={"nav": "next", "i3": raw})
        assert r.status_code in (200, 302), raw


def test_parser_that_fails_gives_an_error_on_the_question_not_a_500(app, client, survey, monkeypatch):
    """The safety net behind the single checks: whatever a parser trips over stays on the question."""
    from app.items import text as text_mod

    fill_consent(client)
    add(client, "text_input")
    url = _start(app, client)
    p = _participant(app, url)

    def broken(self, item, form, lang="de", meta=None):
        raise OverflowError("int too large to convert to float")

    monkeypatch.setattr(text_mod.TextInput, "parse_answer", broken)
    r = p.post(url, data={"nav": "next", "i2": "x"})
    assert r.status_code == 200 and "konnte nicht gelesen werden" in r.data.decode()


def test_image_marks_refuse_nan_and_numbers_beyond_floats():
    from app.items.image_mark import _xy

    assert _xy([0.25, 2]) == [0.25, 1.0]
    for bad in ([float("nan"), 0.5], ["nan", "nan"], ["inf", 0], [10 ** 400, 0], [0.5], "x", None):
        assert _xy(bad) is None, bad
    assert all(math.isfinite(v) for v in _xy(["1e-9", "0.5"]))


def test_comparison_counts_and_bands_stay_in_range(app, client, survey):
    """A hidden field with thousands of digits was stored as a number and broke the .sav export of the survey."""
    fill_consent(client)
    add(client, "likert")                                             # 2
    client.post(E + "/items/2", data=MultiDict([("code", "TA"), ("answer_mode", "optional"), ("points", "5"), ("labeling", "full"),
                ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"), ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B")]))
    add(client, "page_break")                                         # 3
    add(client, "sample_compare")                                     # 4
    client.post(E + "/items/4", data=MultiDict([("code", "cmp"), ("question", "Vergleich"), ("source", "2"), ("show_own", "1")]))
    url = _start(app, client)
    p = _participant(app, url)
    p.post(url, data={"nav": "next", "i2_s1": "2", "i2_s2": "3"})
    assert p.post(url, data={"nav": "next", "i4_n_2": "9" * 4000}).status_code == 302
    assert _answers(app).get("4") is None
    assert client.get("/surveys/1/export/data.sav").status_code == 200
    p = _participant(app, url)
    p.post(url, data={"nav": "next", "i2_s1": "2", "i2_s2": "3"})
    p.post(url, data={"nav": "next", "i4_n_2": "17"})
    assert _answers(app)["4"] == {"2": 0}  # the server works the size out again: one participation, below MIN_N


def test_sav_export_survives_a_number_no_float_can_hold():
    from app import sav

    data = sav.write([{"name": "n", "label": "", "measure": "scale", "kind": "num", "decimals": 0, "value_labels": {}, "missing": []}],
                     [[10 ** 400], [3]], file_label="x")
    assert data[:4] == b"$FL2"


def test_rating_takes_values_of_its_scale_only(app, client, survey):
    fill_consent(client)
    add(client, "rating")                                             # 2 stars 1..5
    client.post(E + "/items/2", data={"code": "stars", "question": "R", "answer_mode": "optional", "style": "stars", "points": "5"})
    add(client, "rating")                                             # 3 slider 0..100 step 5
    client.post(E + "/items/3", data={"code": "sl", "question": "S", "answer_mode": "optional", "style": "slider",
                                      "min": "0", "max": "100", "step": "5"})
    url = _start(app, client)
    with app.app_context():
        assert db.session.get(Item, 3).config["step"] == 5
    p = _participant(app, url)
    r = p.post(url, data={"nav": "next", "i2": "2.5", "i3": "50"})
    assert r.status_code == 200 and "Ungültige Bewertung" in r.data.decode()
    r = p.post(url, data={"nav": "next", "i2": "3", "i3": "52"})
    assert r.status_code == 200 and "Ungültige Bewertung" in r.data.decode()
    assert p.post(url, data={"nav": "next", "i2": "3", "i3": "55"}).status_code == 302
    assert _answers(app) == {"1": "1", "2": 3, "3": 55}


def test_long_text_is_no_email_address(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "mail", "question": "E-Mail", "validation": "email", "max_length": "20000"})
    url = _start(app, client)
    p = _participant(app, url)
    r = p.post(url, data={"nav": "next", "i2": "a@" + "." * 19000 + "@"})
    assert r.status_code == 200 and "has-error" in r.data.decode()
    assert p.post(url, data={"nav": "next", "i2": "a@example.org"}).status_code == 302


def test_acbc_unacceptable_levels_are_stored_once(app, client, survey):
    from tests.analysis.test_acbc import setup, start

    setup(client, rules=True, screens=4)
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2_byo_a1": "a1l0", "i2_byo_a2": "a2l1", "i2_byo_a3": "a3l0", "i2_byo_a4": "a4l2"})
    for _ in range(12):
        html = p.get(f"/s/{uid}").data.decode()
        if 'name="i2_ng"' in html:
            break
        form = MultiDict({"nav": "next"})
        for c in re.findall(r'name="i2_s(\d+)" value="1"', html):
            form[f"i2_s{c}"] = "1" if int(c) % 2 else "0"
        for c in re.findall(r'name="i2_s(\d+)" value="a"', html):
            form[f"i2_s{c}"] = "a"
        p.post(f"/s/{uid}", data=form)
    offered = re.findall(r'name="i2_ng" value="([^"]+)"', html)
    assert offered
    form = MultiDict({"nav": "next"})
    form.setlist("i2_ng", [offered[0]] * 5000)
    assert p.post(f"/s/{uid}", data=form).status_code == 302
    with app.app_context():
        rules = db.session.scalar(db.select(Response)).answers["2"]["rules"]
        assert [offered[0]] in rules.values() and len(json.dumps(rules)) < 300


def test_control_characters_in_a_url_parameter_do_not_break_the_page(app, client, survey):
    """Formatting marks the places of `code` and of the gaps of a cloze text with control characters; a
    parameter value carrying the same characters made the page fail."""
    from app import panel
    from app.items.base import inline_rich, rich

    assert panel.clean_value(" a\x007\x00b\x02\x03\n ") == "a7b"
    assert len(panel.clean_value("x" * 500)) == 100
    assert str(inline_rich("`x` {{v}}", {"v": "a\x009\x00b"})) == "<code>x</code> a9b"
    assert "<code>x</code>" in str(rich("`x` \x005\x00"))
    fill_consent(client)
    add(client, "cloze")                                              # 2
    client.post(E + "/items/2", data=MultiDict([("code", "cz"), ("question", "Ergänzen Sie `x` {{gruppe}}"), ("answer_mode", "optional"),
                ("text", "Roboter sind [   ]. {{gruppe}}")]))
    client.post("/surveys/1/panel", data=MultiDict([("param_name", "gruppe"), ("param_mode", "store"), ("param_required", "0"),
                ("param_allowed", "")]))
    url = _start(app, client)
    for query in ("?gruppe=a%007%00b", "?gruppe=a%029%03b", "?gruppe=%02%03"):
        p = _participant(app, url, query)
        r = p.get(url)
        assert r.status_code == 200 and 'name="i2_1"' in r.data.decode(), query
    with app.app_context():
        from app.items.cloze import Cloze

        item = db.session.get(Item, 2)
        assert 'name="i2_1"' in str(Cloze().text_html(item, "de", None, {"gruppe": "\x029\x03"}))  # also a value not cleaned on the way
