"""Cross tables of quota cells, conditions of the flow on quotas, completion-link rules, and the quotas on the
results and methods pages (app/quotas.py, app/panel.py, app/flow.py)."""
import pytest
from werkzeug.datastructures import MultiDict

from app import flow, panel, quotas
from app.extensions import db
from app.models import Quota, Response, Survey
from tests.quotas.test_quotas import E, Q, build, counters, finish, questionnaire, screen, set_flow, start

SPEC = "gender: Männer = 1; Frauen = 2\nage: 18 bis 29 = 18..29 [25]; 30 bis 49 = 30..49 [40]; ab 50 = >= 50 [35]"


def quota_one(app, client):
    questionnaire(client)
    client.post(Q + "/add")


# --- the cross-table generator --------------------------------------------------------------------------


def test_parse_attributes(app, client, survey):
    quota_one(app, client)
    client.post("/surveys/1/panel", data={"param_name": "src", "param_mode": "store", "param_required": "0", "param_allowed": ""})
    with app.app_context():
        s = db.session.get(Survey, 1)
        parsed = quotas.parse_attributes(s, SPEC + "\np:src: Panel A = a; beide anderen = b, c\n\n")
        assert parsed[0] == (2, [("Männer", "eq", "1", 1.0), ("Frauen", "eq", "2", 1.0)])
        assert parsed[1] == (3, [("18 bis 29", "between", "18..29", 25.0), ("30 bis 49", "between", "30..49", 40.0),
                                 ("ab 50", "ge", "50", 35.0)])
        assert parsed[2] == ("p:src", [("Panel A", "eq", "a", 1.0), ("beide anderen", "in", "b, c", 1.0)])
        assert quotas.parse_attributes(s, "age: < 30; 30..49; > 49")[0][1] == \
            [("< 30", "lt", "30", 1.0), ("30..49", "between", "30..49", 1.0), ("> 49", "gt", "49", 1.0)]
        for bad, word in (("", "mindestens ein Merkmal"), ("nope: a = 1", "kein Variablenname"), ("gender", "erwartet"),
                          ("p:x: a = 1", "kein deklarierter URL-Parameter"), ("gender: a = 1\ngender: b = 2", "nur einmal"),
                          ("gender: a = 1 [0]", "zusammen null")):
            with pytest.raises(ValueError, match=word):
                quotas.parse_attributes(s, bad)


def picked(target=None, total=None, replace=True, shares=True, age=True):
    """The builder's form: gender with both answers ticked, crossed with three age ranges (shares 25, 40, 35)."""
    data = MultiDict([("m0_item", "2"), ("m0_codes", "1"), ("m0_codes", "2")])
    if age:
        data.update({"m1_item": "3", "m1_r0_from": "18", "m1_r0_to": "29", "m1_r1_from": "30", "m1_r1_to": "49", "m1_r2_from": "50"})
        if shares:
            data.update({"m1_r0_share": "25", "m1_r1_share": "40", "m1_r2_share": "35"})
    for key, value in (("target", target), ("total", total), ("replace", "1" if replace else None)):
        if value is not None:
            data[key] = str(value)
    return data


def test_text_form_of_the_attributes_still_crosses(app, client, survey):
    quota_one(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert quotas.generate_cells(s, s.quotas[0], SPEC, target=3, replace=True) == 6
        assert [c.name for c in s.quotas[0].cells][:2] == ["Männer / 18 bis 29", "Männer / 30 bis 49"]


def test_generate_crosses_names_codes_and_distributes_a_total(app, client, survey):
    quota_one(app, client)
    r = client.post(Q + "/1/cross", data=picked(total=201), follow_redirects=True)
    assert "6 Teilquoten erzeugt" in r.data.decode()
    with app.app_context():
        cells = db.session.get(Quota, 1).cells
        assert [c.name for c in cells] == ["männlich / 18 bis 29", "männlich / 30 bis 49", "männlich / ab 50",
                                           "weiblich / 18 bis 29", "weiblich / 30 bis 49", "weiblich / ab 50"]
        assert [c.code for c in cells][:2] == ["maennlich_18_bis_29", "maennlich_30_bis_49"] and len({c.code for c in cells}) == 6
        assert cells[2].conds == [{"item": 2, "op": "eq", "value": "1"}, {"item": 3, "op": "ge", "value": "50"}]
        assert sum(c.target for c in cells) == 201                                           # exactly the total
        assert [c.target for c in cells] == [25, 41, 35, 25, 40, 35]                         # the rest goes to the largest remainder
        assert not [m for _, m in quotas.check(db.session.get(Survey, 1)) if "Teilquote" in m]
    client.post(Q + "/1/cross", data=picked(target=10, replace=False, age=False))          # added to the six
    with app.app_context():
        cells = db.session.get(Quota, 1).cells
        assert len(cells) == 8 and (cells[6].name, cells[6].code, cells[6].target) == ("männlich", "maennlich", 10)
    for form, word in (({"replace": "1"}, "mindestens ein Merkmal"),
                       ({"m0_item": "2", "replace": "1"}, "mindestens eine Antwort ankreuzen"),
                       ({"m0_item": "3", "replace": "1"}, "einen Bereich angeben"),
                       ({**picked().to_dict(flat=False), "m1_r1_share": ""}, "für jede Ausprägung einen Anteil"),
                       ({"m0_item": "2", "m0_codes": "1", "m1_item": "2", "m1_codes": "2"}, "nur einmal")):
        r = client.post(Q + "/1/cross", data=form, follow_redirects=True)
        assert "Nichts erzeugt" in r.data.decode() and word in r.data.decode(), form
    with app.app_context():
        assert len(db.session.get(Quota, 1).cells) == 8                                      # a refused form changes nothing
    client.post(Q + "/1/cross", data=picked(target=5, age=False))
    with app.app_context():
        assert [(c.code, c.target) for c in db.session.get(Quota, 1).cells] == [("maennlich", 5), ("weiblich", 5)]
    assert "Kreuzquote aufbauen" in client.get(Q + "/").data.decode()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    client.post(Q + "/1/cross", data=picked())                                             # not in the field
    with app.app_context():
        assert len(db.session.get(Quota, 1).cells) == 2


def test_shares_must_be_numbers_from_zero_and_the_form_says_they_are_multiplied(app, client, survey):
    """Shares -1.5 / 5.5 / 3 of a total 7 gave the targets -1 / 5 / 3; "nan" ended in a Python error message."""
    quota_one(app, client)
    for bad in ("-1,5", "nan", "inf"):
        form = picked(total=7)
        form["m1_r0_share"] = bad
        r = client.post(Q + "/1/cross", data=form, follow_redirects=True).data.decode()
        assert "Nichts erzeugt" in r and "Anteile sind Zahlen ab 0" in r and "convert" not in r, bad
    with app.app_context():
        assert db.session.get(Quota, 1).cells == []
    form = picked(total=7)
    form["m1_r0_share"] = "0"
    assert "6 Teilquoten erzeugt" in client.post(Q + "/1/cross", data=form, follow_redirects=True).data.decode()
    html = client.get(Q + "/").data.decode()
    assert "Merkmale gelten also als unabhängig" in html and "gemeinsamen Verteilung" in html


def test_matrix_for_a_complete_cross_of_two(app, client, survey):
    quota_one(app, client)
    client.post(Q + "/1/cross", data=picked(target=5))
    set_flow(app)
    uid = start(app, client)
    finish(uid, screen(app, uid, "2", "35"))
    with app.app_context():
        s = db.session.get(Survey, 1)
        entry = quotas.report(s)[0]
        grid = entry["matrix"]
        assert grid["rows"] == ["gender: männlich", "gender: weiblich"]
        assert grid["cols"] == ["age: 18 bis 29", "age: 30 bis 49", "age: ab 50"]
        assert [[r["done"] for r in row] for row in grid["cells"]] == [[0, 0, 0], [0, 1, 0]]
        assert entry["matrix_text"].splitlines()[2] == "gender: weiblich\t0 / 5\t1 / 5\t0 / 5"
        s.quotas[0].cells[0].conds = s.quotas[0].cells[0].conds[:1]                           # no longer a full cross
        assert quotas.report(s)[0]["matrix"] is None
        db.session.rollback()
    html = client.get("/surveys/1/flow/quotas/").data.decode()
    assert "Als Matrix" in html and "Matrix kopieren" in html and 'data-key="matrix-1"' in html


# --- conditions of the flow on quotas -------------------------------------------------------------------


def branch_survey(app, client, cond):
    """consent, screening, calculation (no end: goes on when full), main [only under `cond`], last"""
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")]), ("Frauen", "f", 5, [(2, "eq", "2")])])
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "quota", "id": "qn1", "quotas": [1]},
            {"t": "branch", "id": "b1", "cond": cond, "children": [{"t": "block", "block": 3}]},
            {"t": "block", "block": 4}]}
        flow.normalize(s)
        db.session.commit()
    return start(app, client)


def test_branch_on_the_result_of_a_quota(app, client, survey):
    uid = branch_survey(app, client, {"item": "q:1", "op": "eq", "value": "in"})
    first, second = screen(app, uid), screen(app, uid)
    assert "Hauptteil" in first.get(f"/s/{uid}").data.decode()                               # has a place: main part
    assert "Spätere Frage" in second.get(f"/s/{uid}").data.decode()                          # full: the block is skipped
    assert counters(app) == {"running": 1, "done": 0, "refused": 1, "over": 0}
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert flow.condition_text(s, {"item": "q:1", "op": "eq", "value": "full"}) == "Quote „Geschlecht x Alter“: voll"
        assert not [m for sev, m, _ in flow.check(s) if sev == "error"]


def test_branch_on_the_cell(app, client, survey):
    uid = branch_survey(app, client, {"item": "qc:1", "op": "eq", "value": "f"})
    assert "Spätere Frage" in screen(app, uid, "1").get(f"/s/{uid}").data.decode()
    assert "Hauptteil" in screen(app, uid, "2").get(f"/s/{uid}").data.decode()                # only the women's cell
    html = client.get("/surveys/1/flow/").data.decode()
    assert '<option value="qc:1" selected>Quote · Geschlecht x Alter: Teilquote</option>' in html
    assert "Teilquote von „Geschlecht x Alter“: Frauen" in html
    assert '"options": [["m", "M\\u00e4nner"], ["f", "Frauen"]]' in html                      # the Teilquoten to tick


def test_flow_page_stores_and_checks_conditions_on_quotas(app, client, survey):
    build(app, client)
    client.post(E + "/flow/add", data={"kind": "branch"})
    with app.app_context():
        nid = next(n["id"] for n in flow.walk_nodes(db.session.get(Survey, 1), "branch"))
    client.post(E + f"/flow/{nid}/update", data={"item": "q:1", "op": "eq", "value": "voll", "name": "Quote voll?"})
    client.post(E + "/flow/add", data={"kind": "end"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        node, _ = flow.find(flow.tree(s), nid)
        assert node["cond"] == {"item": "q:1", "op": "eq", "value": "voll"}
        text = " | ".join(m for _, m, _ in flow.check(s))
        assert "„voll“ kommt nicht vor (möglich: in, full, none)" in text
        # the branch before the check, and on a quota that is gone
        root = flow.tree(s)
        root["children"].remove(node)
        root["children"].insert(1, {**node, "cond": {"item": "q:1", "op": "eq", "value": "full"}})
        s.flow = root
        assert "wird erst danach berechnet" in " | ".join(m for _, m, _ in flow.check(s))
        node["cond"] = {"item": "qc:99", "op": "eq", "value": "x"}
        assert flow.condition_text(s, node["cond"]) == "Bedingung auf eine Quote, die es nicht gibt"


# --- link rules -----------------------------------------------------------------------------------------


def rules_form(rules, quota_placeholder=False, redirects=None):
    data = MultiDict([("param_name", "src"), ("param_mode", "store"), ("param_required", "0"), ("param_allowed", "a, b")])
    for outcome, url in (redirects or {"completed": "https://panel.example/done?lang={{lang}}"}).items():
        data.add(f"redirect_{outcome}", url)
    for outcome, item, op, value, url in rules:
        for key, v in (("rule_outcome", outcome), ("rule_item", item), ("rule_op", op), ("rule_value", value),
                       ("rule_url", url), ("rule_mode", "now")):
            data.add(key, v)
    if quota_placeholder:
        data.add("quota_placeholder", "1")
    return data


def test_link_rules_per_provider_and_cell(app, client, survey):
    build(app, client, cells=[("Männer", "m", 5, [(2, "eq", "1")]), ("Frauen", "f", 5, [(2, "eq", "2")])])
    rules = [("completed", "p:src", "eq", "b", "https://panel-b.example/ok?c={{quota}}"),
             ("completed", "qc:1", "eq", "f", "https://panel.example/frauen"),
             ("completed", "p:src", "in", "x, a", ""),                                       # no link for a
             ("quotafull", "q:1", "eq", "full", "https://panel.example/full")]
    r = client.post("/surveys/1/panel", data=rules_form(rules, quota_placeholder=True), follow_redirects=True)
    assert "Teilnahmelinks gespeichert" in r.data.decode()
    uid = start(app, client)

    def end(gender, query):
        return finish(uid, screen(app, uid, gender, query=query))

    assert end("1", "?src=b").headers["Location"] == "https://panel-b.example/ok?c=m"        # first rule, with the cell code
    assert end("2", "?src=b").headers["Location"] == "https://panel-b.example/ok?c=f"        # the first matching rule wins
    assert end("2", "").headers["Location"] == "https://panel.example/frauen"                # rule on the cell
    r = end("1", "?src=a")
    assert r.status_code == 200 and "Vielen Dank für Ihre Teilnahme" in r.data.decode()      # the rule without a link
    assert end("1", "").headers["Location"] == "https://panel.example/done?lang=de"          # no rule: the standard link
    html = client.get("/surveys/1/field").data.decode()
    assert '<option value="qc:1" selected>Quote · Geschlecht x Alter: Teilquote</option>' in html and 'value="f" checked' in html and "panel-b.example" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert panel.passes_cell(s) and "panel-b.example" in panel.link_hosts(s)
        assert not [m for sev, m in panel.check(s)]
    assert "Gruppe übermittelt" in client.get("/s/1/privacy").data.decode()


def test_quota_placeholder_needs_the_tick_and_rules_are_checked(app, client, survey):
    build(app, client)
    form = rules_form([], redirects={"completed": "https://panel.example/done?c={{quota}}"})
    assert "kein deklarierter Parameter" in client.post("/surveys/1/panel", data=form, follow_redirects=True).data.decode()
    for rule, word in ((("completed", "p:nope", "eq", "a", "https://x.example/"), "die es nicht gibt"),
                       (("completed", "qc:9", "eq", "a", "https://x.example/"), "die es nicht gibt"),
                       (("declined", "qc:1", "eq", "m", "https://x.example/"), "nur URL-Parameter"),
                       (("completed", "p:src", "eq", "", "https://x.example/"), "einen Wert"),
                       (("completed", "p:src", "eq", "a", "https://{{src}}/"), "Rechnernamen")):
        html = client.post("/surveys/1/panel", data=rules_form([rule]), follow_redirects=True).data.decode()
        assert "Regel 1" in html and word in html, rule
    with app.app_context():
        assert not panel.config(db.session.get(Survey, 1))["rules"]
    client.post("/surveys/1/panel", data=rules_form([("completed", "qc:1", "eq", "zz", "https://x.example/")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert any("kein Teilquoten-Code" in m for _, m in panel.check(s))
        assert panel.extra_values(s, Response(language="en", quota={"1": {"cell": 1, "res": "in"}})) == {"lang": "en"}
        assert "Gruppe übermittelt" not in client.get("/s/1/privacy").data.decode()


def test_link_of_the_end_goes_before_a_rule(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])])
    client.post("/surveys/1/panel", data=rules_form([("quotafull", "p:src", "eq", "a", "https://panel.example/rule")],
                                                    redirects={"quotafull": "https://panel.example/standard"}))
    uid = start(app, client)
    screen(app, uid)
    assert screen(app, uid, query="?src=a").get(f"/s/{uid}").headers["Location"] == "https://panel.example/rule"
    assert screen(app, uid, query="?src=b").get(f"/s/{uid}").headers["Location"] == "https://panel.example/standard"
    with app.app_context():
        s = db.session.get(Survey, 1)
        root = __import__("copy").deepcopy(flow.tree(s))
        flow.find(root, "qe1")[0]["link"] = "https://panel.example/check"
        s.flow = root
        db.session.commit()
    assert screen(app, uid, query="?src=a").get(f"/s/{uid}").headers["Location"] == "https://panel.example/check"


def test_duplicate_keeps_conditions_on_parameters_and_points_at_its_own_quotas(app, client, survey):
    build(app, client)
    client.post("/surveys/1/panel", data=rules_form([("completed", "qc:1", "eq", "m", "https://panel.example/m"),
                                                     ("completed", "p:src", "eq", "a", "https://panel.example/a")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        root = __import__("copy").deepcopy(flow.tree(s))
        root["children"] += [{"t": "branch", "id": "bp", "cond": {"item": "p:src", "op": "eq", "value": "a"}, "children": []},
                             {"t": "branch", "id": "bq", "cond": {"item": "qc:1", "op": "eq", "value": "m"}, "children": []}]
        s.flow = root
        flow.normalize(s)
        db.session.commit()
    client.post("/surveys/1/duplicate", data={"title": "Zweite Welle"})
    with app.app_context():
        twin = db.session.scalar(db.select(Survey).filter_by(title="Zweite Welle"))
        qid = twin.quotas[0].id
        assert qid != 1
        conds = {n["id"]: n["cond"]["item"] for n in flow.walk_nodes(twin, "branch")}
        assert conds == {"bp": "p:src", "bq": f"qc:{qid}"}                                    # the parameter keeps its name
        assert [r["item"] for r in panel.config(twin)["rules"]] == [f"qc:{qid}", "p:src"]
        assert [r["item"] for r in panel.config(db.session.get(Survey, 1))["rules"]] == ["qc:1", "p:src"]


# --- results and methods --------------------------------------------------------------------------------


def test_results_page_and_method_section_show_the_quotas(app, client, survey):
    build(app, client, cells=[("Männer", "m", 2, [(2, "eq", "1")]), ("Frauen", "f", 2, [(2, "eq", "2")])])
    uid = start(app, client)
    finish(uid, screen(app, uid, "1")), finish(uid, screen(app, uid, "2")), finish(uid, screen(app, uid, "2"))
    screen(app, uid, "2").get(f"/s/{uid}")
    html = client.get("/surveys/1/results").data.decode()
    assert "<h2>Quoten" in html and "zählt bei Abschluss, hart" in html
    assert '<td class="num">50,0 %</td>' in html and '<td class="num">75,0 %</td>' in html         # men 1 of 2, sum 3 of 4
    from app import methods

    with app.app_context():
        f = methods.facts(db.session.get(Survey, 1))
        assert f["quotas"] == [{"name": "Geschlecht x Alter", "kind": "counted at completion, hard",
                                "kind_de": "bei Abschluss gezählt, hart", "cells": [("Männer", 2, 1), ("Frauen", 2, 2)],
                                "target": 4, "done": 3, "refused": 1, "marked": 0, "freed": 0}]
        assert "nach „Geschlecht x Alter“ quotiert (2 Teilquoten, bei Abschluss gezählt, hart): Soll insgesamt 4, erreicht 3." \
            in methods.draft(f)
        assert 'Quota "Geschlecht x Alter" (counted at completion, hard): target 4, reached 3' in methods.facts_text(f)
    assert "3 von 4" in client.get("/surveys/1/methods").data.decode()


def test_marked_completions_do_not_count_as_reached(app, client, survey):
    """Q-6: what quality control marks is no part of what a quota reached on the results page and in the method
    section; the counters that steer the field stay as they are."""
    build(app, client, cells=[("Männer", "m", 2, [(2, "eq", "1")]), ("Frauen", "f", 2, [(2, "eq", "2")])])
    uid = start(app, client)
    finish(uid, screen(app, uid, "1")), finish(uid, screen(app, uid, "2")), finish(uid, screen(app, uid, "2"))
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.quality_rules = {"flags": ["bot"], "min": 1}
        bot = db.session.scalars(db.select(Response).filter_by(survey_id=1, status=Response.COMPLETED)
                                 .order_by(Response.id.desc())).first()   # a woman, the hidden field filled in
        bot.paradata = {**(bot.paradata or {}), "_bot": 1}
        db.session.commit()
    html = client.get("/surveys/1/results").data.decode()
    assert "markiert</th>" in html
    assert '<td class="num">50,0 %</td>' in html and '<td class="num">75,0 %</td>' not in html   # women 1 of 2, sum 2 of 4
    from app import methods

    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [c.done for c in s.quotas[0].cells] == [1, 2]                                   # the field: unchanged
        f = methods.facts(s)
        assert f["quotas"][0]["cells"] == [("Männer", 2, 1), ("Frauen", 2, 1)]
        assert (f["quotas"][0]["done"], f["quotas"][0]["marked"]) == (2, 1)
        assert "Soll insgesamt 4, erreicht 2 (ohne 1 von der Qualitätskontrolle markierte Abschlüsse)." in methods.draft(f)
        assert "reached 2 (not counting 1 completions marked by quality control)" in methods.facts_text(f)
