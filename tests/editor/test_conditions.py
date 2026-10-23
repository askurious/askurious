"""Conditions without answer codes: what they can be put on, the fields of the form, the wording, and the
element "Bedingung" on the Ablauf page (app/conditions.py)."""
import json

from werkzeug.datastructures import MultiDict

from app import conditions, flow
from app.extensions import db
from app.models import Survey
from tests.conftest import add
from tests.quotas.test_quotas import E, Q, cells_form, questionnaire


def survey_with_everything(app, client):
    """gender (2, choice), age (3, number), main (4, info), late (5, number), media (6, multiple choice),
    note (7, free text), scale (8, Likert), born (9, date); URL parameters src (a, b) and free; one quota."""
    questionnaire(client)
    add(client, "multiple_choice", block=2)
    client.post(E + "/items/6", data=MultiDict([("code", "media"), ("question", "Medien"),
                                                ("option_id", "a"), ("option_code", "1"), ("option_label", "Zeitung"),
                                                ("option_id", "b"), ("option_code", "2"), ("option_label", "Radio")]))
    add(client, "text_input", block=2)
    client.post(E + "/items/7", data={"code": "note", "question": "Anmerkung"})
    add(client, "likert", block=2)
    add(client, "date_time", block=2)
    client.post(E + "/items/9", data={"code": "born", "question": "Geboren am", "parts": "date"})
    client.post("/surveys/1/panel", data=MultiDict([
        ("param_name", "src"), ("param_mode", "store"), ("param_required", "0"), ("param_allowed", "a, b"),
        ("param_name", "free"), ("param_mode", "store"), ("param_required", "0"), ("param_allowed", "")]))
    client.post(Q + "/add")
    client.post(Q + "/1", data=cells_form([("Frauen", "f", 5, [(2, "eq", "2")]), ("Männer", "m", 5, [(2, "eq", "1")])]))


def test_describe_everything(app, client, survey):
    survey_with_everything(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        d = conditions.describe(s, everything=True)
        assert {ref: (i["kind"], i["modes"]) for ref, i in d.items()} == {
            "2": ("choice", "choice"), "3": ("number", "number"), "6": ("choice", "choice"), "7": ("text", "text"),
            "8": ("other", "other"), "9": ("date", "date"), "5": ("number", "number"),
            "p:src": ("choice", "param"), "p:free": ("text", "paramtext"), "q:1": ("choice", "quota"), "qc:1": ("choice", "quota")}
        assert d["qc:1"]["options"] == [("f", "Frauen"), ("m", "Männer")] and d["q:1"]["options"][1] == ("full", "voll, kein Platz")
        assert list(conditions.describe(s)) == ["2", "3", "6", "9", "5", "p:src", "p:free"]     # for a Teilquote
        data = conditions.page_data(d)
        assert data["modes"]["choice"][0] == ["is", "ist"] and data["items"]["3"]["kind"] == "number"
        json.dumps(data)


def test_from_form_and_view_are_two_sides(app, client, survey):
    survey_with_everything(app, client)
    cases = [
        ({"item": "2", "mode": "is", "codes": ["2"]}, {"item": 2, "op": "eq", "value": "2"}, "gender: weiblich"),
        ({"item": "2", "mode": "is", "codes": ["1", "2"]}, {"item": 2, "op": "in", "value": "1, 2"}, "gender: männlich oder weiblich"),
        ({"item": "2", "mode": "not", "codes": ["1"]}, {"item": 2, "op": "ne", "value": "1"}, "gender: nicht männlich"),
        ({"item": "2", "mode": "not", "codes": ["1", "2"]}, {"item": 2, "op": "nin", "value": "1, 2"},
         "gender: nicht männlich oder weiblich"),
        ({"item": "6", "mode": "is", "codes": ["2"]}, {"item": 6, "op": "in", "value": "2"}, "media: Radio"),
        ({"item": "6", "mode": "not", "codes": ["2"]}, {"item": 6, "op": "nin", "value": "2"}, "media: nicht Radio"),
        ({"item": "2", "mode": "answered", "codes": ["1"]}, {"item": 2, "op": "answered", "value": ""}, "gender: beantwortet"),
        ({"item": "3", "mode": "range", "from": "18", "to": "29"}, {"item": 3, "op": "between", "value": "18..29"}, "age: 18 bis 29"),
        ({"item": "3", "from": "50"}, {"item": 3, "op": "ge", "value": "50"}, "age: ab 50"),
        ({"item": "3", "mode": "range", "to": "17"}, {"item": 3, "op": "le", "value": "17"}, "age: bis 17"),
        ({"item": "3", "mode": "lt", "value": "18"}, {"item": 3, "op": "lt", "value": "18"}, "age: unter 18"),
        ({"item": "3", "mode": "empty"}, {"item": 3, "op": "empty", "value": ""}, "age: nicht beantwortet"),
        ({"item": "9", "mode": "gt", "value": "31.12.1999"}, {"item": 9, "op": "gt", "value": "31.12.1999"}, "born: nach 31.12.1999"),
        ({"item": "7", "mode": "contains", "value": "ja"}, {"item": 7, "op": "contains", "value": "ja"}, "note: enthält ja"),
        ({"item": "8", "mode": "is"}, {"item": 8, "op": "answered", "value": ""}, "likert1: beantwortet"),
        ({"item": "p:src", "mode": "not", "codes": ["b"]}, {"item": "p:src", "op": "ne", "value": "b"}, "URL src: nicht b"),
        ({"item": "p:free", "mode": "answered"}, {"item": "p:free", "op": "answered", "value": ""}, "URL free: vorhanden"),
        ({"item": "q:1", "codes": ["full"]}, {"item": "q:1", "op": "eq", "value": "full"}, "Quote „Geschlecht x Alter“: voll"),
        ({"item": "qc:1", "mode": "is", "codes": ["f", "m"]}, {"item": "qc:1", "op": "in", "value": "f, m"},
         "Teilquote von „Geschlecht x Alter“: Frauen oder Männer"),
    ]
    with app.app_context():
        s = db.session.get(Survey, 1)
        d = conditions.describe(s, everything=True)
        code8 = next(i.code for b in s.blocks for i in b.items if i.id == 8)
        for fields, cond, words in cases:
            form = MultiDict()
            for key, value in fields.items():
                form.setlist(key, value if isinstance(value, list) else [value])
            assert conditions.from_form(form, "", d) == cond, fields
            assert conditions.text(s, cond) == words.replace("likert1", code8), cond
            v = conditions.view(cond, d)                                                     # and back: nothing is "special"
            assert not v["special"] and v["mode"] == fields.get("mode", v["mode"]) or fields.get("mode") == "is" and fields["item"] == "8"
            back = MultiDict({"item": v["ref"], "mode": v["mode"], "from": v["lo"], "to": v["hi"], "value": v["value"]})
            back.setlist("codes", v["codes"])
            assert conditions.from_form(back, "", d) == cond, cond
        assert conditions.from_form(MultiDict({"item": ""}), "", d) is None
        assert conditions.from_form(MultiDict({"item": "2", "mode": "is"}), "", d) == {"item": 2, "op": "eq", "value": ""}
        # a condition the form has no fields for stays as it is, until something is entered
        old = {"item": 6, "op": "contains", "value": "1"}
        assert conditions.view(old, d)["special"] and conditions.view({"item": 77, "op": "eq", "value": "x"}, d)["special"]
        kept = MultiDict({"item": "6", "mode": "is", "raw": json.dumps(old)})
        assert conditions.from_form(kept, "", d) == old
        kept.setlist("codes", ["2"])
        assert conditions.from_form(kept, "", d) == {"item": 6, "op": "in", "value": "2"}
        assert conditions.view({"item": 3, "op": "lt", "value": "18"}, conditions.describe(s), modes=False)["special"]


def test_none_of_several(app):
    answers = {1: "2", 2: ["1", "3"]}
    assert flow.holds({"item": 1, "op": "nin", "value": "1, 3"}, answers)
    assert not flow.holds({"item": 1, "op": "nin", "value": "2, 3"}, answers)
    assert not flow.holds({"item": 2, "op": "nin", "value": "3"}, answers) and flow.holds({"item": 2, "op": "nin", "value": "2"}, answers)
    assert not flow.holds({"item": 9, "op": "nin", "value": "1"}, answers)                    # not answered: no condition holds


def test_branch_is_set_by_ticking_and_shown_with_its_answers(app, client, survey):
    survey_with_everything(app, client)
    client.post(E + "/flow/add", data={"kind": "branch"})
    with app.app_context():
        nid = flow.walk_nodes(db.session.get(Survey, 1), "branch")[0]["id"]
    form = MultiDict({"name": "Frauen", "item": "2", "mode": "is"})
    form.setlist("codes", ["2"])
    r = client.post(E + f"/flow/{nid}/update", data=form, follow_redirects=True)
    html = r.data.decode()
    assert "Gespeichert" in html and "gender: weiblich" in html
    settings = html.split(f'id="node-{nid}"')[1].split("</form>")[1]
    assert '<option value="2" selected>gender · Geschlecht</option>' in html
    assert '<input type="checkbox" name="c0_codes" value="2" checked > weiblich' in html
    assert '<option value="is" selected>ist</option>' in html and '<option value="not" >ist nicht</option>' in html
    assert 'name="op"' not in html and "Antwortcode" not in html and "data-cond-items" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        node, _ = flow.find(flow.tree(s), nid)
        assert node["cond"] == {"item": 2, "op": "eq", "value": "2"} and node["name"] == "Frauen"
    client.post(E + f"/flow/{nid}/update", data={"item": "3", "mode": "range", "from": "18", "to": ""})
    html = client.get("/surveys/1/flow/").data.decode()
    assert "age: ab 18" in html and 'name="c0_from" value="18"' in html and 'name="c0_to" value=""' in html
    client.post(E + f"/flow/{nid}/update", data={"item": "3", "mode": "lt", "value": "18"})
    html = client.get("/surveys/1/flow/").data.decode()
    assert "age: unter 18" in html and '<option value="lt" selected>kleiner als</option>' in html and 'name="c0_value" value="18"' in html
    client.post(E + f"/flow/{nid}/update", data={"item": "7", "mode": "empty"})
    with app.app_context():
        node, _ = flow.find(flow.tree(db.session.get(Survey, 1)), nid)
        assert node["cond"] == {"item": 7, "op": "empty", "value": ""}
    assert "note: nicht beantwortet" in client.get("/surveys/1/flow/").data.decode()
    assert settings is not None


def test_stored_condition_the_page_cannot_show_is_kept(app, client, survey):
    survey_with_everything(app, client)
    client.post(E + "/flow/add", data={"kind": "branch"})
    with app.app_context():
        nid = flow.walk_nodes(db.session.get(Survey, 1), "branch")[0]["id"]
    client.post(E + f"/flow/{nid}/update", data={"item": "6", "op": "contains", "value": "1"})   # the older form of the request
    html = client.get(f"/surveys/1/flow/?open={nid}").data.decode()
    assert "media: enthält Zeitung" in html and 'class="quota-special"' in html and 'name="c0_raw"' in html
    raw = html.split('name="c0_raw" value="')[1].split('"')[0]
    import html as html_lib

    client.post(E + f"/flow/{nid}/update", data={"item": "6", "mode": "is", "raw": html_lib.unescape(raw), "name": "Zeitung"})
    with app.app_context():
        node, _ = flow.find(flow.tree(db.session.get(Survey, 1)), nid)
        assert node["cond"] == {"item": 6, "op": "contains", "value": "1"} and node["name"] == "Zeitung"


def test_a_branch_with_several_conditions(app, client, survey):
    """UI/UX audit 2026-10-03 (S6): an element "Bedingung" holds several conditions, all or one of them must
    hold; lines are added and removed on the page ("c<k>_…", gaps allowed), one line stays a plain condition."""
    survey_with_everything(app, client)
    client.post(E + "/flow/add", data={"kind": "branch"})
    with app.app_context():
        nid = flow.walk_nodes(db.session.get(Survey, 1), "branch")[0]["id"]
    form = MultiDict({"name": "Junge Frauen", "c0_item": "2", "c0_mode": "is", "c3_item": "3", "c3_mode": "range",
                      "c3_from": "18", "c3_to": "29", "rule": "all"})
    form.setlist("c0_codes", ["2"])
    html = client.post(E + f"/flow/{nid}/update", data=form, follow_redirects=True).data.decode()
    assert "gender: weiblich und age: 18 bis 29" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        node, _ = flow.find(flow.tree(s), nid)
        assert node["cond"]["rule"] == "all" and [c["item"] for c in flow.parts(node["cond"])] == [2, 3]
        assert flow.holds(node["cond"], {2: "2", 3: "20"}) and not flow.holds(node["cond"], {2: "2", 3: "40"})
        assert not [m for kind, m, _ in flow.check(s) if "Bedingung" in m and kind == "error"]
    page = client.get(f"/surveys/1/flow/?open={nid}").data.decode()
    assert 'name="c0_item"' in page and 'name="c1_item"' in page and "data-cond-rule" in page and "data-cond-remove" in page
    form["rule"] = "any"
    client.post(E + f"/flow/{nid}/update", data=form)
    with app.app_context():
        node, _ = flow.find(flow.tree(db.session.get(Survey, 1)), nid)
        assert flow.holds(node["cond"], {2: "1", 3: "20"}) and not flow.holds(node["cond"], {2: "1", 3: "40"})
    one = MultiDict({"c5_item": "2", "c5_mode": "is"})
    one.setlist("c5_codes", ["1"])
    client.post(E + f"/flow/{nid}/update", data=one)                                       # one line left: a plain condition
    with app.app_context():
        node, _ = flow.find(flow.tree(db.session.get(Survey, 1)), nid)
        assert node["cond"] == {"item": 2, "op": "eq", "value": "1"}


def test_several_conditions_go_along_into_a_file_and_a_copy(app, client, survey):
    survey_with_everything(app, client)
    client.post(E + "/flow/add", data={"kind": "branch"})
    with app.app_context():
        nid = flow.walk_nodes(db.session.get(Survey, 1), "branch")[0]["id"]
    form = MultiDict({"c0_item": "2", "c1_item": "3", "c1_mode": "range", "c1_from": "18", "rule": "any"})
    form.setlist("c0_codes", ["2"])
    client.post(E + f"/flow/{nid}/update", data=form)
    client.post("/surveys/1/duplicate", data={"title": "Kopie"})
    with app.app_context():
        copies = db.session.scalars(db.select(Survey).where(Survey.id != 1)).all()
        assert copies
        for s in copies:
            branch = flow.walk_nodes(s, "branch")[0]
            items = {i.code: i.id for b in s.blocks for i in b.items}
            assert branch["cond"]["rule"] == "any"
            assert [c["item"] for c in flow.parts(branch["cond"])] == [items["gender"], items["age"]]


def test_thousands_points_in_conditions(app):
    """Audit 2026-10-03 (R-2): the value of a condition is read like the answer of a number field, German or
    English style ("1.000" and "1000" are 1000 on both sides, "1.500,50", "1,500.50" and "1500.5" are 1500.5)."""
    def holds(op, value, answer):
        return flow.holds({"item": 3, "op": op, "value": value}, {3: answer})

    assert not holds("gt", "1.000", 500) and holds("gt", "1.000", 1500)        # „größer als 1.000“ at 500: was True
    assert holds("between", "1.000..2.000", 1500)                               # „von 1.000 bis 2.000“ at 1500: was False
    assert not holds("between", "1.000..2.000", 2500) and holds("between", "1.000..2.000", 1000)
    assert holds("eq", "1.000", 1000) and not holds("ne", "1.000", 1000)        # „= 1.000“ at 1000: was False
    for value in ("1.500,50", "1,500.50", "1500.5", "1500,5"):                  # „1.500,50“ was never read
        assert holds("lt", value, 1500) and not holds("lt", value, 1501), value
        assert holds("ge", value, 1500.5) and not holds("ge", value, 1500), value
        assert holds("le", value, 1500.5) and holds("eq", value, 1500.5), value
    assert holds("gt", "2,5", 3) and not holds("gt", "2,5", 2) and holds("lt", "0.125", 0.1)
    assert holds("gt", "−5", -4) and not holds("lt", "abc", 1)
    assert holds("gt", "999", "1.000") and holds("eq", "1.000", 1000.0)          # an answer given as text, a Rechenwert


def test_number_conditions_are_stored_plain_and_checked(app, client, survey):
    """R-2: what is typed is stored without thousands separators; what cannot be read for sure is named by the
    pre-launch check (flow, Teilquoten, rows that only some people see)."""
    survey_with_everything(app, client)
    client.post(E + "/flow/add", data={"kind": "branch"})
    with app.app_context():
        nid = flow.walk_nodes(db.session.get(Survey, 1), "branch")[0]["id"]

    def stored(data):
        client.post(E + f"/flow/{nid}/update", data=data)
        with app.app_context():
            s = db.session.get(Survey, 1)
            return flow.find(flow.tree(s), nid)[0]["cond"], [m for kind, m, _ in flow.check(s) if "Bedingung" in m]

    cond, found = stored({"item": "3", "mode": "gt", "value": "1.000"})
    assert cond == {"item": 3, "op": "gt", "value": "1000"} and not [m for m in found if "Zahl" in m or "eindeutig" in m]
    cond, _ = stored({"item": "3", "mode": "range", "from": "1.000", "to": "1,500.50"})
    assert cond == {"item": 3, "op": "between", "value": "1000..1500,5"}
    assert flow.holds(cond, {3: 1200}) and not flow.holds(cond, {3: 1600})
    cond, _ = stored({"item": "3", "mode": "range", "from": "1.000", "to": "1000"})
    assert cond == {"item": 3, "op": "eq", "value": "1000"}                    # the same number: =
    cond, found = stored({"item": "3", "mode": "lt", "value": "1,000"})
    assert cond["value"] == "1,000" and [m for m in found if "„1,000“ ist nicht eindeutig (1 oder 1000?)" in m]
    cond, found = stored({"item": "3", "mode": "lt", "value": "1.2.3"})
    assert cond["value"] == "1.2.3" and [m for m in found if "„1.2.3“ ist keine Zahl" in m]
    cond, found = stored({"item": "9", "mode": "gt", "value": "31.12.1999"})    # a date stays a date
    assert cond["value"] == "31.12.1999" and not [m for m in found if "keine Zahl" in m]

    from app import quotas, rows
    from tests.quotas.test_quotas import Q, cells_form

    client.post(Q + "/1", data=cells_form([("Viel", "v", 5, [(3, "between", "1.000..2.000")]), ("Wenig", "w", 5, [(3, "gt", "1,000")])]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        cells = s.quotas[0].cells
        assert cells[0].conds == [{"item": 3, "op": "between", "value": "1000..2000"}]
        assert quotas.match(s.quotas[0], {3: 1500}) is cells[0]
        assert [m for _, m in quotas.check(s) if "Teilquote „Wenig“" in m and "nicht eindeutig" in m]
        likert = next(i for b in s.blocks for i in b.items if i.type == "likert")
        statements = [dict(r) for r in likert.config["statements"]]
        statements[0]["when"] = {"item": 3, "op": "ge", "value": "viel"}
        likert.config = {**likert.config, "statements": statements}
        db.session.commit()
        assert [m for _, m, _, _ in rows.check(s) if "„viel“ ist keine Zahl" in m]


def test_score_band_limits_are_read_like_condition_values(app, client, survey):
    """R-2, the same reading for the limits of a Einordnung: "1.000" is 1000, "2,5" and "2.5" are 2.5."""
    from app.models import Item
    from tests.conftest import add, fill_consent

    fill_consent(client)
    add(client, "score_band")                                                               # 2
    form = MultiDict([("code", "band"), ("question", "Einordnung"), ("source", ""), ("basis", "value")])
    for upper, label in (("2.5", "niedrig"), ("1.000", "mittel"), ("1.500,5", "hoch"), ("", "offen")):
        form.add("band_id", ""); form.add("band_upper", upper); form.add("band_label", label); form.add("band_text", "")
    client.post(E + "/items/2", data=form)
    with app.app_context():
        assert [b["upper"] for b in db.session.get(Item, 2).config["bands"]] == [2.5, 1000.0, 1500.5, None]
