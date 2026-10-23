"""The page "Quoten" without answer codes and with little to type: conditions are picked (answers ticked,
numbers as from … to), a cross quota is built by ticking, codes are made by themselves (app/quotas.py)."""
from werkzeug.datastructures import MultiDict

from app import panel, quotas
from app.extensions import db
from app.models import Quota, Survey
from tests.conftest import add
from tests.quotas.test_quotas import E, Q, cells_form, questionnaire


def quota_one(app, client):
    questionnaire(client)                                              # gender (2, choice), age (3, whole number)
    add(client, "multiple_choice", block=2)                            # 6
    client.post(E + "/items/6", data=MultiDict([("code", "media"), ("question", "Welche Medien?"),
                                                ("option_id", "a"), ("option_code", "1"), ("option_label", "Zeitung"),
                                                ("option_id", "b"), ("option_code", "2"), ("option_label", "Radio")]))
    add(client, "text_input", block=2)                                 # 7: free text, nothing to tick
    client.post(E + "/items/7", data={"code": "open", "question": "Anmerkung"})
    client.post("/surveys/1/panel", data=MultiDict([
        ("param_name", "src"), ("param_mode", "store"), ("param_required", "0"), ("param_allowed", "a, b"),
        ("param_name", "free"), ("param_mode", "store"), ("param_required", "0"), ("param_allowed", "")]))
    client.post(Q + "/add")


def test_describe_offers_what_can_be_picked(app, client, survey):
    quota_one(app, client)
    with app.app_context():
        d = quotas.describe(db.session.get(Survey, 1))
        assert list(d) == ["2", "3", "6", "5", "p:src", "p:free"]                            # no consent, no free text
        assert (d["2"]["kind"], d["2"]["options"], d["2"]["multi"]) == ("choice", [("1", "männlich"), ("2", "weiblich")], False)
        assert d["3"]["kind"] == "number" and d["6"]["multi"] and d["2"]["label"] == "gender · Geschlecht"
        assert (d["p:src"]["kind"], d["p:src"]["options"]) == ("choice", [("a", "a"), ("b", "b")]) and d["p:free"]["kind"] == "text"


def test_conditions_are_saved_from_what_is_ticked_and_entered(app, client, survey):
    quota_one(app, client)
    form = MultiDict({"name": "Mix", "cells": "5", "mode": "hard", "no_cell": "continue"})
    form.update({"c0_k0_item": "2", "c0_k1_item": "3", "c0_k1_from": "18", "c0_k1_to": "29", "c0_target": "10"})
    form.setlist("c0_k0_codes", ["2"])                                                       # one answer of a single choice
    form.update({"c1_k0_item": "2", "c1_k1_item": "3", "c1_k1_from": "50", "c1_target": "5"})
    form.setlist("c1_k0_codes", ["1", "2", "9"])                                             # two answers; "9" is none of them
    form.update({"c2_k0_item": "6", "c2_k1_item": "3", "c2_k1_to": "17", "c2_name": "Zeitung, jung"})
    form.setlist("c2_k0_codes", ["1"])                                                       # multiple choice: "one of"
    form.update({"c3_k0_item": "p:src", "c3_k1_item": "p:free", "c3_k1_value": "x", "c3_k2_item": "3",
                 "c3_k2_from": "30", "c3_k2_to": "30"})
    form.setlist("c3_k0_codes", ["b"])
    form.update({"c4_k0_item": "7", "c4_k0_value": "hallo"})                                 # not offered: no condition, no cell
    r = client.post(Q + "/1", data=form, follow_redirects=True)
    assert "gespeichert" in r.data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        cells = s.quotas[0].cells
        assert [c.conds for c in cells] == [
            [{"item": 2, "op": "eq", "value": "2"}, {"item": 3, "op": "between", "value": "18..29"}],
            [{"item": 2, "op": "in", "value": "1, 2"}, {"item": 3, "op": "ge", "value": "50"}],
            [{"item": 6, "op": "in", "value": "1"}, {"item": 3, "op": "le", "value": "17"}],
            [{"item": "p:src", "op": "eq", "value": "b"}, {"item": "p:free", "op": "eq", "value": "x"},
             {"item": 3, "op": "eq", "value": "30"}]]
        # names and codes make themselves: the name from the conditions, the code from the name
        assert [(c.name, c.code) for c in cells] == [
            ("weiblich / 18 bis 29", "weiblich_18_bis_29"), ("männlich oder weiblich / ab 50", "maennlich_oder_weiblich_ab_50"),
            ("Zeitung, jung", "zeitung_jung"), ("b / x / 30", "b_x_30")]
        assert quotas.cell_text(s, cells[1]) == "gender: männlich oder weiblich; age: ab 50"
        assert quotas.cell_text(s, cells[3]) == "URL src: b; URL free: x; age: 30"
        assert quotas.match(s.quotas[0], {2: "2", 3: 20}).code == "weiblich_18_bis_29"
        assert quotas.match(s.quotas[0], {2: "1", 3: 50, 6: ["2"]}).code == "maennlich_oder_weiblich_ab_50"
        assert quotas.match(s.quotas[0], {6: ["1", "2"], 3: 17}).code == "zeitung_jung"


def test_page_shows_answers_to_tick_and_no_codes(app, client, survey):
    quota_one(app, client)
    client.post(Q + "/1", data=cells_form([("Frauen jung", "", 10, [(2, "eq", "2"), (3, "between", "18..29")]),
                                           ("Sonderfall", "", 5, [(3, "lt", "18")])]))
    html = client.get(Q + "/").data.decode()
    row = html.split('name="c0_name"')[1].split('name="c1_name"')[0]
    assert '<input type="checkbox" name="c0_k0_codes" value="2" checked > weiblich' in row
    assert '<input type="checkbox" name="c0_k0_codes" value="1"  > männlich' in row
    assert 'name="c0_k1_from" value="18"' in row and 'name="c0_k1_to" value="29"' in row
    assert "_op" not in html and "Antwortcode" not in html                                   # no comparison to choose, no codes to know
    assert 'class="quota-code-col"' in html and "Export-Codes zeigen" in html                # codes are there, out of the way
    special = html.split('name="c1_name"')[1].split('name="c2_name"')[0]
    assert "age: unter 18" in special and 'name="c1_k0_raw"' in special and 'name="c1_k0_from"' not in special
    assert "data-add-cond" in html and "data-add-cell" in html and "data-cell-template" in html and "c__N__" in html
    assert '"kind": "choice"' in html and '"options": [["1", "m\\u00e4nnlich"], ["2", "weiblich"]]' in html


def test_saving_the_page_again_keeps_every_condition(app, client, survey):
    """What the page sends back for stored conditions gives the same conditions, also for one it cannot show."""
    quota_one(app, client)
    client.post(Q + "/1", data=cells_form([("A", "a", 1, [(2, "in", "1, 2"), (3, "ge", "18")]), ("B", "b", 2, [(3, "lt", "18")]),
                                           ("C", "c", 3, [(7, "eq", "x")])]))
    with app.app_context():
        before = [(c.id, c.name, c.code, c.target, c.conds) for c in db.session.get(Quota, 1).cells]
        assert before[1][4] == [{"item": 3, "op": "lt", "value": "18"}] and before[2][4] == [{"item": 7, "op": "eq", "value": "x"}]
    import html as html_lib
    import re

    page = client.get(Q + "/").data.decode()
    form_html = page.split('id="quota-1"')[1].split("</form>")[0]
    form = MultiDict()
    for tag in re.findall(r"<(?:input|select)\b[^>]*>(?:\s*<option[^>]*selected[^>]*>)?", form_html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name or "disabled" in tag.split(">")[0]:
            continue
        if tag.startswith("<select"):
            chosen = re.search(r'<option value="([^"]*)"[^>]*selected', tag)
            continue_ = chosen is None
            if continue_:
                continue
            form.add(name.group(1), html_lib.unescape(chosen.group(1)))
        elif 'type="checkbox"' in tag:
            if re.search(r"\bchecked\b", tag):
                form.add(name.group(1), html_lib.unescape(re.search(r'value="([^"]*)"', tag).group(1)))
        else:
            value = re.search(r'value="([^"]*)"', tag)
            form.add(name.group(1), html_lib.unescape(value.group(1)) if value else "")
    for select in re.findall(r'<select name="([^"]+)"[^>]*>(.*?)</select>', form_html, flags=re.S):
        chosen = re.search(r'<option value="([^"]*)"[^>]*\bselected\b', select[1])
        form.setlist(select[0], [html_lib.unescape(chosen.group(1)) if chosen else ""])
    assert form["c1_k0_item"] == "3" and form["c2_k0_item"] == "7" and form.getlist("c0_k0_codes") == ["1", "2"]
    r = client.post(Q + "/1", data=form, follow_redirects=True)
    assert "gespeichert" in r.data.decode()
    with app.app_context():
        assert [(c.id, c.name, c.code, c.target, c.conds) for c in db.session.get(Quota, 1).cells] == before


def test_builder_lists_questions_and_needs_no_typed_codes(app, client, survey):
    quota_one(app, client)
    html = client.get(Q + "/").data.decode()
    builder = html.split("data-quota-builder")[1].split("</details>")[0]
    assert "Kreuzquote aufbauen" in html and 'name="m0_item"' in builder and "textarea" not in builder
    assert '<option value="2">gender · Geschlecht</option>' in builder and '<option value="p:src">URL-Parameter · src</option>' in builder
    client.post(Q + "/1/cross", data=MultiDict([("m0_item", "p:src"), ("m0_codes", "a"), ("m0_codes", "b"),
                                                  ("m1_item", "6"), ("m1_codes", "2"), ("target", "7"), ("replace", "1")]))
    with app.app_context():
        cells = db.session.get(Quota, 1).cells
        assert [(c.name, c.target) for c in cells] == [("a / Radio", 7), ("b / Radio", 7)]
        assert cells[0].conds == [{"item": "p:src", "op": "eq", "value": "a"}, {"item": 6, "op": "in", "value": "2"}]
    r = client.post(Q + "/1/cross", data={"m0_item": "p:free"}, follow_redirects=True)
    assert "keine Liste erlaubter Werte" in r.data.decode()


def test_link_rules_are_picked_like_any_condition(app, client, survey):
    quota_one(app, client)
    client.post(Q + "/1", data=cells_form([("Frauen", "", 10, [(2, "eq", "2")])]))
    html = client.get("/surveys/1/field").data.decode()
    # a new line offers the Teilquoten, the result of a quota and the URL parameters to pick (_condition.html)
    assert '<option value="qc:1" >Quote · Geschlecht x Alter: Teilquote</option>' in html and '<option value="q:1" >' in html
    data = MultiDict([("param_name", "src"), ("param_mode", "store"), ("param_required", "0"), ("param_allowed", "a, b"),
                      ("param_name", "free"), ("param_mode", "store"), ("param_required", "0"), ("param_allowed", "")])
    for item, value, url in (("qc:1|frauen", "", "https://panel.example/f"), ("p:src|b", "ignored", "https://panel.example/b"),
                             ("p:free", "x", "https://panel.example/x")):
        for key, v in (("rule_outcome", "completed"), ("rule_item", item), ("rule_op", "eq"), ("rule_value", value),
                       ("rule_url", url), ("rule_mode", "now")):
            data.add(key, v)
    assert "Teilnahmelinks gespeichert" in client.post("/surveys/1/panel", data=data, follow_redirects=True).data.decode()
    with app.app_context():
        rules = panel.config(db.session.get(Survey, 1))["rules"]
        assert [(r["item"], r["op"], r["value"]) for r in rules] == [("qc:1", "eq", "frauen"), ("p:src", "eq", "b"), ("p:free", "eq", "x")]
    html = client.get("/surveys/1/field").data.decode()
    assert '<option value="qc:1" selected>' in html and 'name="r0_codes" value="frauen" checked' in html
    assert '<option value="p:src" selected>' in html and 'name="r1_codes" value="b" checked' in html
    assert '<option value="p:free" selected>' in html and 'name="r2_value" value="x"' in html


def test_tolerance_is_locked_while_the_quota_is_hard(app, client, survey):
    """UI/UX audit 2026-10-05, V19: the tolerance counts only for a soft quota."""
    client.post("/surveys/1/flow/quotas/add")
    page = client.get("/surveys/1/flow/quotas/").data.decode()
    field = page[page.index('name="tolerance"'):].split(">", 1)[0]
    assert "data-soft-only" in field and "disabled" in field
