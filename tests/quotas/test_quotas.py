"""Quotas: cells, the check in the flow, hard and soft counting, the state on the fieldwork page (app/quotas.py)."""
import csv
import io
import json

from werkzeug.datastructures import MultiDict

from app import flow, panel, quotas, runtime
from app.extensions import db
from app.models import Quota, QuotaCell, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent
from tests.exports.test_transfer import _upload as _read_in

E = "/surveys/1/editor"
Q = "/surveys/1/flow/quotas"


def questionnaire(client):
    """consent (1) | block 2: gender (2, codes 1/2), age (3, integer) | block 3: outro (4) | block 4: last (5)"""
    fill_consent(client)
    add(client, "single_choice")                                     # 2
    client.post(E + "/items/2", data=MultiDict([("code", "gender"), ("question", "Geschlecht"),
                                                ("option_id", "a"), ("option_code", "1"), ("option_label", "männlich"),
                                                ("option_id", "b"), ("option_code", "2"), ("option_label", "weiblich")]))
    add(client, "text_input")                                        # 3
    client.post(E + "/items/3", data={"code": "age", "validation": "integer", "question": "Alter"})
    client.post(E + "/blocks")                                       # block 3
    add(client, "info", block=3)                                     # 4
    client.post(E + "/items/4", data={"code": "main", "question": "Hauptteil"})
    client.post(E + "/blocks")                                       # block 4
    add(client, "text_input", block=4)                               # 5
    client.post(E + "/items/5", data={"code": "late", "validation": "integer", "question": "Spätere Frage"})


def cond_fields(prefix, item, op, value):
    """One condition as the page sends it: ticked answers ("_codes") for a choice, "_from"/"_to" for a number.
    A comparison the page has no fields for (<, >, ≠, a broken range) travels as the stored condition ("_raw"),
    as it does when such a condition is shown again."""
    import json

    fields = MultiDict({f"{prefix}_item": str(item)})
    if op == "eq":
        fields.update({f"{prefix}_from": value, f"{prefix}_to": value, f"{prefix}_value": value})
        fields.add(f"{prefix}_codes", value)
    elif op == "in":
        for code in flow.code_list(value):
            fields.add(f"{prefix}_codes", code)
    elif op == "between" and flow.bounds(value)[0] is not None:
        fields.update({f"{prefix}_from": flow.bounds(value)[0], f"{prefix}_to": flow.bounds(value)[1]})
    elif op in ("ge", "le"):
        fields[f"{prefix}_from" if op == "ge" else f"{prefix}_to"] = value
    else:
        fields[f"{prefix}_raw"] = json.dumps({"item": item, "op": op, "value": value})
    if op == "eq":  # a code or a parameter the page does not offer: the stored condition is kept
        fields[f"{prefix}_raw"] = json.dumps({"item": item, "op": op, "value": value})
    return fields


def cells_form(cells, **settings):
    """The form of one quota: cells = [(name, code, target, [(item, op, value), ...])]."""
    data = MultiDict({"name": "Geschlecht x Alter", "code": "qa", "mode": "hard", "tolerance": "0", "tolerance_unit": "n",
                      "no_cell": "continue", "cells": str(len(cells)), **settings})
    for n, (name, code, target, conds) in enumerate(cells):
        data.update({f"c{n}_name": name, f"c{n}_code": code, f"c{n}_target": str(target)})
        for m, (item, op, value) in enumerate(conds):
            for key, values in cond_fields(f"c{n}_k{m}", item, op, value).lists():
                data.setlist(key, values)
    return data


def set_flow(app, extra=(), full="end", quota_ids=None, text=None, rule="any"):
    """consent, screening, quota calculation, [end "Quote voll" for its quotas], main, [extra nodes], last"""
    with app.app_context():
        s = db.session.get(Survey, 1)
        ids = quota_ids if quota_ids is not None else [q.id for q in s.quotas]
        end = [{"t": "end", "id": "qe1", "kind": "quotafull", "quotas": ids, "rule": rule, "text": text or {}, "link": "",
                "link_mode": "now"}] if full == "end" else []
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                                            {"t": "quota", "id": "qn1", "quotas": ids}, *end,
                                            {"t": "block", "block": 3}, *extra, {"t": "block", "block": 4}]}
        flow.normalize(s)
        db.session.commit()


def build(app, client, cells=None, extra=(), full="end", **settings):
    questionnaire(client)
    client.post(Q + "/add")
    cells = cells or [("Männer", "m", 2, [(2, "eq", "1")]), ("Frauen", "f", 2, [(2, "eq", "2")])]
    r = client.post(Q + "/1", data=cells_form(cells, **settings), follow_redirects=True)
    assert "gespeichert" in r.data.decode()
    set_flow(app, extra, full)


def start(app, client):
    r = client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"}, follow_redirects=True)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING, r.data.decode()[:3000]
        return s.code


def screen(app, uid, gender="1", age="30", query=""):
    """A participant up to the page behind the quota check (or to the end, if the quota was full)."""
    p = app.test_client()
    p.get(f"/s/{uid}{query}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": gender, "i3": age})
    return p


def finish(uid, p, late="1"):
    p.post(f"/s/{uid}", data={"nav": "next"})
    p.post(f"/s/{uid}", data={"nav": "next", "i5": late})
    return p.get(f"/s/{uid}")


def counters(app, code="m"):
    with app.app_context():
        c = db.session.scalar(db.select(QuotaCell).filter_by(code=code))
        return {"running": c.running, "done": c.done, "refused": c.refused, "over": c.over}


def statuses(app):
    with app.app_context():
        return [r.status for r in db.session.scalars(db.select(Response).filter_by(source="real").order_by(Response.id))]


# --- conditions -----------------------------------------------------------------------------------------


def test_new_comparisons():
    a = {1: 18, 2: "2", 3: ["1", "3"], 4: "1987-05-23", 5: 18.0, 6: "abc"}
    yes = [(1, "le", "18"), (1, "ge", "18"), (1, "le", "18,5"), (1, "between", "18..29"), (1, "between", "10 bis 18"),
           (1, "between", "18;18"), (1, "between", "17 - 19"), (2, "in", "1, 2"), (3, "in", "3;4"), (4, "ge", "23.05.1987"),
           (4, "le", "1987"), (4, "between", "1980..1990"), (5, "le", "18"), (5, "ge", "18")]
    no = [(1, "le", "17"), (1, "ge", "19"), (1, "between", "19..29"), (1, "between", "18"), (2, "in", "1, 3"),
          (3, "in", "2"), (4, "ge", "24.05.1987"), (6, "le", "5"), (6, "between", "1..9"), (9, "le", "5"), (9, "in", "1")]
    for item, op, value in yes:
        assert flow.holds({"item": item, "op": op, "value": value}, a), (item, op, value)
    for item, op, value in no:
        assert not flow.holds({"item": item, "op": op, "value": value}, a), (item, op, value)
    assert flow.bounds("18..29") == ("18", "29") and flow.bounds("-5 .. 5") == ("-5", "5") and flow.bounds("x") == (None, None)
    assert flow.code_list(" 1, 2;3 ") == ["1", "2", "3"]


# --- the page "Quoten" ----------------------------------------------------------------------------------


def test_quota_page_adds_edits_and_deletes(app, client, survey):
    questionnaire(client)
    assert "Noch keine Quote" in client.get(Q + "/").data.decode()
    client.post(Q + "/add")
    cells = [("Männer unter 30", "m_jung", 50, [(2, "eq", "1"), (3, "lt", "30")]), ("Frauen", "", 80, [(2, "eq", "2")])]
    html = client.post(Q + "/1", data=cells_form(cells), follow_redirects=True).data.decode()
    assert "gespeichert" in html and "Männer unter 30" in html and "quota_qa" in html
    # removing a Teilquote is a button (×) that fills a hidden field, never a tick box that saves at once
    assert 'name="c0_delete" value="" data-remove-value' in html and 'data-remove-flag="[data-cell]"' in html
    assert 'type="checkbox" name="c0_delete"' not in html
    with app.app_context():
        quota = db.session.get(Quota, 1)
        assert [(c.name, c.code, c.target) for c in quota.cells] == [("Männer unter 30", "m_jung", 50), ("Frauen", "frauen", 80)]
        assert quota.cells[0].conds == [{"item": 2, "op": "eq", "value": "1"}, {"item": 3, "op": "lt", "value": "30"}]
        assert quotas.cell_text(db.session.get(Survey, 1), quota.cells[0]) == "gender: männlich; age: unter 30"
    # refused: a bad code, a code twice
    bad = cells_form([("A", "1x", 5, [(2, "eq", "1")])])
    assert "Nicht gespeichert" in client.post(Q + "/1", data=bad, follow_redirects=True).data.decode()
    twice = cells_form([("A", "a", 5, [(2, "eq", "1")]), ("B", "A", 5, [(2, "eq", "2")])])
    assert "doppelt" in client.post(Q + "/1", data=twice, follow_redirects=True).data.decode()
    with app.app_context():
        assert len(db.session.get(Quota, 1).cells) == 2                                    # nothing of the refused forms stayed
        first = db.session.get(Quota, 1).cells[0].id
    # keep the first cell (by id), delete the second
    form = cells_form(cells)
    form.update({"c0_id": str(first), "c1_id": str(first + 1), "c1_delete": "1"})
    client.post(Q + "/1", data=form)
    with app.app_context():
        assert [c.id for c in db.session.get(Quota, 1).cells] == [first]
    client.post(Q + "/1/delete")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Quota.id))) == 0
        assert db.session.scalar(db.select(db.func.count(QuotaCell.id))) == 0


def test_cells_and_conditions_are_fixed_in_the_field(app, client, survey):
    build(app, client)
    start(app, client)
    html = client.get(Q + "/").data.decode()
    assert "Bedingungen sind fest" in html and "Steuerung speichern" in html and "Quote löschen" not in html
    client.post(Q + "/1", data=cells_form([("X", "x", 1, [(2, "eq", "1")])]))                # the form of a draft
    client.post(Q + "/1/delete")
    client.post(Q + "/add")
    with app.app_context():
        assert [c.code for c in db.session.get(Quota, 1).cells] == ["m", "f"] and len(db.session.get(Survey, 1).quotas) == 1


def test_flow_page_adds_and_sets_the_check(app, client, survey):
    questionnaire(client)
    client.post(Q + "/add")
    client.post(E + "/flow/add", data={"kind": "quota"})
    with app.app_context():
        node = quotas.nodes(db.session.get(Survey, 1))[0]
        assert node == {"t": "quota", "id": node["id"], "quotas": [1]}                      # the uncalculated quota is taken
    nid = node["id"]
    # the calculation is a trigger: it takes the quotas and nothing else, whatever a form sends
    client.post(E + f"/flow/{nid}/update", data={"quotas": "1", "full": "end", "text_de": "Leider voll.",
                                                  "link": "https://panel.example/full", "link_mode": "button"})
    with app.app_context():
        assert quotas.nodes(db.session.get(Survey, 1))[0] == {"t": "quota", "id": nid, "quotas": [1]}
    # what happens when the quota is full is said by an end "Quote voll": its quotas, one or all, text and link
    client.post(E + "/flow/add", data={"kind": "end"})
    with app.app_context():
        eid = flow.walk_nodes(db.session.get(Survey, 1), "end")[0]["id"]
    client.post(E + f"/flow/{eid}/update", data={"kind": "quotafull", "quotas": ["1", "99"], "rule": "all", "text_de": "Leider voll.",
                                                  "link": "https://panel.example/full", "link_mode": "button"})
    with app.app_context():
        end = flow.walk_nodes(db.session.get(Survey, 1), "end")[0]
        assert (end["quotas"], end["rule"], end["text"]["de"], end["link"], end["link_mode"]) == \
            ([1], "all", "Leider voll.", "https://panel.example/full", "button")
        assert quotas.full_ends(db.session.get(Survey, 1)) == [end]
    html = client.get("/surveys/1/flow/").data.decode()
    assert "Quotenberechnung" in html and "Quotenprüfung" not in html and "wenn voll: Quote 1" in html
    assert "data-case-when" in html and 'data-case-show="quotafull"' in html and "alle Quoten voll sind" in html
    assert 'name="full"' not in html and "quota_census" not in html and "/quotas/assistant" not in html
    r = client.post(E + f"/flow/{eid}/update", data={"kind": "quotafull", "quotas": "1", "link": "https://{{x}}/"},
                    follow_redirects=True)
    assert "Abschlusslink" in r.data.decode()
    client.post(E + f"/flow/{eid}/update", data={"kind": "screenout", "quotas": "1"})      # another kind names no quota
    with app.app_context():
        assert "quotas" not in flow.walk_nodes(db.session.get(Survey, 1), "end")[0]
    client.post(E + f"/flow/{eid}/update", data={"kind": "quotafull", "quotas": "1"})
    client.post(Q + "/1/delete")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert quotas.nodes(s)[0]["quotas"] == [] and flow.walk_nodes(s, "end")[0]["quotas"] == []  # no longer named


# --- pre-launch check ---------------------------------------------------------------------------------------


def test_design_check(app, client, survey):
    questionnaire(client)
    client.post(Q + "/add")
    with app.app_context():
        s = db.session.get(Survey, 1)
        messages = [m for _, m in quotas.check(s)]
        assert any("nirgends berechnet" in m for m in messages) and any("keine Teilquote" in m for m in messages)
        assert not any("beendet niemanden" in m for m in messages)                          # said once it is calculated
    cells = [("ohne Soll", "a", 0, [(2, "eq", "9")]), ("spät", "b", 5, [(5, "gt", "3")]), ("doppelt", "c", 5, [(5, "gt", "3")]),
             ("Bereich", "d", 5, [(3, "between", "18")]), ("URL", "e", 5, [("p:src", "eq", "x")])]
    client.post(Q + "/1", data=cells_form(cells))
    set_flow(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = quotas.check(s)
        text = " | ".join(m for _, m in found)
        assert "ohne Soll" in text and "kein Antwortcode von gender" in text
        assert "erst nach der Quotenberechnung" in text and "dieselben Bedingungen" in text
        assert "braucht zwei Werte" in text and "URL-Parameter „src“, der nicht deklariert" in text
        assert "beendet niemanden" not in text                                              # the end behind it names the quota
        # the calculation before the first page, in a loop, without a quota
        s.flow = {"t": "root", "children": [{"t": "quota", "id": "qn1", "quotas": []}, {"t": "block", "block": 1},
                                            {"t": "block", "block": 2}, {"t": "block", "block": 3}, {"t": "block", "block": 4}]}
        flow.normalize(s)
        text = " | ".join(m for _, m in quotas.check(s))
        assert "vor der ersten Seite" in text and "Quotenberechnung ohne Quote" in text
    assert "Noch nicht startklar" in client.get("/surveys/1/field").data.decode()


def test_design_check_of_the_end_quota_full(app, client, survey):
    end = {"t": "end", "id": "e9", "text": {}, "kind": "quotafull", "quotas": [1], "rule": "any"}
    for_all = "beendet die Befragung für alle"

    def warnings():
        with app.app_context():
            return " | ".join(m for _, m, _ in flow.check(db.session.get(Survey, 1)))

    build(app, client)                                 # calculation and end behind it: nothing to say
    assert "Ende" not in warnings() and "beendet niemanden" not in warnings()
    set_flow(app, full="continue")                     # calculated, but no end and no condition looks at it
    assert "“ beendet niemanden" in warnings()
    set_flow(app, [{"t": "branch", "id": "b9", "cond": {"item": "q:1", "op": "eq", "value": "full"},
                    "children": [{"t": "block", "block": 4}]}], full="continue")   # a condition does: the researcher's own way
    assert "beendet niemanden" not in warnings()
    set_flow(app, [end], full="continue")              # an end "Quote voll" is a condition in itself, also at the top level
    assert for_all not in warnings() and "beendet niemanden" not in warnings()
    set_flow(app, [{**end, "quotas": []}], full="continue")                         # it has to name a quota
    assert "Ende (Quote voll) ohne Quote" in warnings() and for_all not in warnings()
    set_flow(app, [{**end, "kind": "screenout"}])      # another kind at the top level ends everybody
    assert for_all in warnings()
    with app.app_context():                            # before the calculation it looks for itself: it needs the answers by then
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, end, {"t": "block", "block": 2},
                                            {"t": "quota", "id": "qn1", "quotas": [1]}, {"t": "block", "block": 3},
                                            {"t": "block", "block": 4}]}
        flow.normalize(s)
        db.session.commit()
    assert "Ende (Quote voll) vor der Berechnung von Quote „" in warnings()


# --- hard quotas ----------------------------------------------------------------------------------------


def test_hard_quota_reserves_at_the_check_and_never_exceeds(app, client, survey):
    build(app, client)
    uid = start(app, client)
    first, second = screen(app, uid), screen(app, uid)
    assert "Hauptteil" in first.get(f"/s/{uid}").data.decode()
    assert counters(app) == {"running": 2, "done": 0, "refused": 0, "over": 0}
    third = screen(app, uid)                                                               # both places are reserved
    html = third.get(f"/s/{uid}").data.decode()
    assert "bereits genügend Teilnahmen" in html
    assert counters(app) == {"running": 2, "done": 0, "refused": 1, "over": 0}
    assert "Hauptteil" in screen(app, uid, gender="2").get(f"/s/{uid}").data.decode()       # the other cell has room
    assert "Vielen Dank für Ihre Teilnahme" in finish(uid, first).data.decode()
    assert counters(app) == {"running": 1, "done": 1, "refused": 1, "over": 0}
    finish(uid, second)
    assert counters(app) == {"running": 0, "done": 2, "refused": 1, "over": 0}
    assert "bereits genügend" in screen(app, uid).get(f"/s/{uid}").data.decode()            # full for good
    assert statuses(app) == ["completed", "completed", "quotafull", "active", "quotafull"]
    with app.app_context():
        r = db.session.scalars(db.select(Response).order_by(Response.id)).all()
        assert r[0].quota == {"1": {"cell": 1, "res": "in", "held": False, "done": True}}
        assert r[2].quota == {"1": {"cell": 1, "res": "full"}}


def test_a_place_goes_back_when_the_participation_ends_early(app, client, survey):
    end = {"t": "branch", "id": "b9", "cond": {"item": 3, "op": "lt", "value": "18"},
           "children": [{"t": "end", "id": "e9", "text": {}, "kind": "screenout"}]}
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])], extra=(end,))
    uid = start(app, client)
    young = screen(app, uid, age="16")                                                      # takes the place ...
    assert counters(app)["running"] == 1
    young.post(f"/s/{uid}", data={"nav": "next"})                                           # ... and is screened out later
    assert "andere Zielgruppe" in young.get(f"/s/{uid}").data.decode()
    assert counters(app) == {"running": 0, "done": 0, "refused": 0, "over": 0}
    assert "Hauptteil" in screen(app, uid).get(f"/s/{uid}").data.decode()                   # the place is free again
    with app.app_context():
        first = db.session.scalars(db.select(Response).order_by(Response.id)).first()
        assert first.status == "screenout" and first.quota["1"] == {"cell": 1, "res": "in", "held": False, "done": False}


def test_check_and_reservation_are_one_statement(app, client, survey):
    """Two workers that both read "one place left" cannot both take it."""
    from sqlalchemy.orm import Session

    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])])
    with app.app_context():
        quota = db.session.get(Quota, 1)
        cell = quota.cells[0]
        assert not quotas.is_full(quota, cell)                                              # this worker sees room
        with Session(db.engine) as other:                                                   # another worker takes the place
            other.execute(db.update(QuotaCell).where(QuotaCell.id == cell.id).values(running=1))
            other.commit()
        assert quotas._take(quota, cell) is False                                           # the statement itself says no
        db.session.commit()
        assert db.session.get(QuotaCell, cell.id).running == 1
        with Session(db.engine) as other:
            other.execute(db.update(QuotaCell).where(QuotaCell.id == cell.id).values(running=0))
            other.commit()
        assert quotas._take(quota, cell) is True and quotas._take(quota, cell) is False
        cell.closed = True
        db.session.commit()
        db.session.execute(db.update(QuotaCell).values(running=0))
        assert quotas._take(quota, cell) is False                                           # closed by hand


def test_decision_is_made_once_and_there_is_no_way_back(app, client, survey):
    build(app, client)
    uid = start(app, client)
    p = screen(app, uid)
    html = p.get(f"/s/{uid}").data.decode()
    assert "Hauptteil" in html and 'value="back"' not in html                              # right behind the check
    p.post(f"/s/{uid}", data={"nav": "back"})                                               # a forged "Back"
    assert "Hauptteil" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={"nav": "next"})
    html = p.get(f"/s/{uid}").data.decode()
    assert "Spätere Frage" in html and 'value="back"' in html                              # back within what follows
    p.post(f"/s/{uid}", data={"nav": "back", "i5": ""})
    assert "Hauptteil" in p.get(f"/s/{uid}").data.decode()
    assert counters(app) == {"running": 1, "done": 0, "refused": 0, "over": 0}              # still one place, taken once


def test_cross_quota_first_matching_cell_and_nobodys_cell(app, client, survey):
    cells = [("Männer bis 29", "m_jung", 1, [(2, "eq", "1"), (3, "between", "18..29")]),
             ("Männer ab 30", "m_alt", 1, [(2, "eq", "1"), (3, "ge", "30")]),
             ("alle Frauen", "f", 5, [(2, "in", "2")])]
    build(app, client, cells=cells)
    uid = start(app, client)
    screen(app, uid, "1", "25"), screen(app, uid, "1", "30"), screen(app, uid, "2", "70")
    assert [counters(app, c)["running"] for c in ("m_jung", "m_alt", "f")] == [1, 1, 1]
    assert "bereits genügend" in screen(app, uid, "1", "29").get(f"/s/{uid}").data.decode()
    nobody = screen(app, uid, "1", "17")                                                    # no cell: goes on, counts nowhere
    assert "Hauptteil" in nobody.get(f"/s/{uid}").data.decode()
    with app.app_context():
        last = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
        assert last.quota == {"1": {"cell": None, "res": "none"}}


def test_nobodys_cell_ends_through_a_condition_in_the_flow(app, client, survey):
    # the calculation ends nobody: who fits no cell is screened out by a condition on the quota, if that is wanted
    none = {"t": "branch", "id": "b9", "cond": {"item": "q:1", "op": "eq", "value": "none"},
            "children": [{"t": "end", "id": "e9", "text": {}, "kind": "screenout"}]}
    questionnaire(client)
    client.post(Q + "/add")
    client.post(Q + "/1", data=cells_form([("Männer ab 18", "m", 5, [(2, "eq", "1"), (3, "ge", "18")])], no_cell="end"))
    with app.app_context():
        assert db.session.get(Quota, 1).no_cell == "continue"                               # no longer a setting of the quota
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                                            {"t": "quota", "id": "qn1", "quotas": [1]}, none,
                                            {"t": "block", "block": 3}, {"t": "block", "block": 4}]}
        flow.normalize(s)
        db.session.commit()
    uid = start(app, client)
    out = screen(app, uid, "1", "17")
    assert "andere Zielgruppe" in out.get(f"/s/{uid}").data.decode()                        # a screen-out, not "quota full"
    assert statuses(app)[-1] == "screenout"
    assert "Hauptteil" in screen(app, uid, "1", "30").get(f"/s/{uid}").data.decode()


def two_quotas(app, client, rule):
    build(app, client, cells=[("Männer", "m", 5, [(2, "eq", "1")])])
    client.post(Q + "/add")
    form = cells_form([("unter 30", "jung", 1, [(3, "lt", "30")]), ("ab 30", "alt", 5, [(3, "ge", "30")])],
                      name="Alter", code="alter")
    client.post(Q + "/2", data=form)
    set_flow(app, rule=rule)
    return start(app, client)


def test_two_quotas_end_when_one_of_them_is_full(app, client, survey):
    uid = two_quotas(app, client, "any")
    screen(app, uid, "1", "20")
    assert (counters(app, "m")["running"], counters(app, "jung")["running"]) == (1, 1)
    second = screen(app, uid, "1", "22")                                                    # room among the men, not under 30
    assert "bereits genügend" in second.get(f"/s/{uid}").data.decode()
    assert (counters(app, "m")["running"], counters(app, "jung")["running"]) == (1, 1)      # the men's place went back
    assert counters(app, "jung")["refused"] == 1 and counters(app, "m")["refused"] == 0
    with app.app_context():
        last = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
        # every quota is calculated on its own; the place among the men was given back when the participation ended
        assert last.quota == {"1": {"cell": 1, "res": "in", "held": False, "done": False}, "2": {"cell": 2, "res": "full"}}


def test_two_quotas_end_only_when_all_of_them_are_full(app, client, survey):
    uid = two_quotas(app, client, "all")
    screen(app, uid, "1", "20")
    second = screen(app, uid, "1", "22")                                                    # only one of the two is full
    assert "Hauptteil" in second.get(f"/s/{uid}").data.decode()
    assert (counters(app, "m")["running"], counters(app, "jung")["running"]) == (2, 1)      # counts among the men
    with app.app_context():
        db.session.get(QuotaCell, 1).target = 2                                             # now the men are full as well
        db.session.commit()
    third = screen(app, uid, "1", "23")
    assert "bereits genügend" in third.get(f"/s/{uid}").data.decode()
    assert statuses(app) == ["active", "active", "quotafull"]


def test_end_before_the_calculation_looks_and_takes_nothing(app, client, survey):
    # the end stands right behind the screening questions, the calculation at the very end: the place is taken
    # when the questionnaire is done, and the end turns away whose cell is full by then
    questionnaire(client)
    client.post(Q + "/add")
    client.post(Q + "/1", data=cells_form([("Männer", "m", 1, [(2, "eq", "1")])]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "end", "id": "qe1", "kind": "quotafull", "quotas": [1], "rule": "any", "text": {}},
            {"t": "block", "block": 3}, {"t": "block", "block": 4}, {"t": "quota", "id": "qn1", "quotas": [1]}]}
        flow.normalize(s)
        db.session.commit()
    html = client.get(E + "/preview?restart=1").data.decode()
    assert "qfull=qe1&on=1" in html and "qfull=qn1&on=1" in html                             # the end has a switch of its own
    client.get(E + "/preview?qfull=qe1&on=1")
    client.post(E + "/preview", data=CONSENT_OK)
    client.post(E + "/preview", data={"nav": "next", "i2": "1", "i3": "30"})
    html = client.get(E + "/preview").data.decode()
    assert "Vorschau beendet" in html and "Quote voll" in html
    uid = start(app, client)
    first, second = screen(app, uid), screen(app, uid)
    assert "Hauptteil" in second.get(f"/s/{uid}").data.decode()                              # both pass: nothing is taken yet
    assert counters(app) == {"running": 0, "done": 0, "refused": 0, "over": 0}
    assert "Vielen Dank für Ihre Teilnahme" in finish(uid, first).data.decode()
    assert counters(app) == {"running": 0, "done": 1, "refused": 0, "over": 0}               # counted when it was done
    assert "bereits genügend" in screen(app, uid).get(f"/s/{uid}").data.decode()             # full for who comes now
    assert counters(app) == {"running": 0, "done": 1, "refused": 1, "over": 0}               # turned away: counted as refused
    assert "Vielen Dank für Ihre Teilnahme" in finish(uid, second).data.decode()             # who was inside may finish
    assert counters(app) == {"running": 0, "done": 1, "refused": 2, "over": 0}
    assert statuses(app) == ["completed", "completed", "quotafull"]
    with app.app_context():
        last = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
        assert last.quota == {"1": {"cell": 1, "res": "full"}}                              # what the end saw is kept
        assert not quotas.recount(db.session.get(Survey, 1))                                 # the counters match


def test_end_sends_to_the_link_of_the_full_quota_or_cell(app, client, survey):
    men, age, young, end = (f"https://panel.example/{name}" for name in ("men", "age", "young", "end"))
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])])
    client.post(Q + "/add")
    client.post(Q + "/2", data=cells_form([("unter 30", "jung", 1, [(3, "lt", "30")]), ("ab 30", "alt", 1, [(3, "ge", "30")])],
                                          name="Alter", code="alter"))
    set_flow(app)
    form = {"kind": "quotafull", "quotas": ["1", "2"], "rule": "any", "link": end, "link_mode": "now", "qlink_1": men,
            "qlink_2": age, "qmode_2": "button", "clink_2_jung": young, "clink_2_weg": "https://panel.example/gone"}
    r = client.post(E + "/flow/qe1/update", data={**form, "clink_2_alt": "http://plain.example/"}, follow_redirects=True)
    assert "Abschlusslink zu „Alter“, Teilquote alt" in r.data.decode()                    # checked like every link
    client.post(E + "/flow/qe1/update", data=form)
    with app.app_context():
        node = quotas.full_ends(db.session.get(Survey, 1))[0]
        assert node["links"] == {"1": {"link": men, "mode": "", "cells": {}}, "2": {"link": age, "mode": "button", "cells": {"jung": young}}}
    html = client.get("/surveys/1/flow/").data.decode()
    # the page shows such an end as triggers: the cell, each quota with a link, then the end as a whole
    assert html.count("data-case>") == 5 and f'name="case0_link" value="{men}"' in html
    assert f'name="case1_link" value="{young}"' in html and f'name="case2_link" value="{age}"' in html
    assert f'name="case3_link" value="{end}"' in html and 'name="case1_cells" value="jung" checked' in html
    # a cell that gets another code keeps its link (the cells keep their ids in the form)
    with app.app_context():
        ids = [c.id for c in db.session.get(Quota, 2).cells]
    client.post(Q + "/2", data=cells_form([("unter 30", "young", 1, [(3, "lt", "30")]), ("ab 30", "alt", 1, [(3, "ge", "30")])],
                                          name="Alter", code="alter", c0_id=str(ids[0]), c1_id=str(ids[1])))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [c.id for c in db.session.get(Quota, 2).cells] == ids
        assert quotas.full_ends(s)[0]["links"]["2"]["cells"] == {"young": young}
        assert panel.link_hosts(s) == ["panel.example"]                                     # named on the privacy page
    # the copy and the survey file carry the links, on the quotas of the new survey
    client.post("/surveys/1/duplicate", data={"title": "Zweite Welle"})
    with app.app_context():
        twin = db.session.scalar(db.select(Survey).filter_by(title="Zweite Welle"))
        new_ids = [str(q.id) for q in twin.quotas]
        links = quotas.full_ends(twin)[0]["links"]
        assert list(links) == new_ids and "1" not in new_ids and links[new_ids[1]]["cells"] == {"young": young}
    data = json.loads(client.get("/surveys/1/export/survey.json").data)
    assert next(n for n in data["flow"]["children"] if n.get("id") == "qe1")["links"]["2"]["cells"] == {"young": young}
    next(n for n in data["flow"]["children"] if n.get("id") == "qe1")["links"]["2"]["cells"]["alt"] = "http://plain.example/"
    _read_in(client, data, title="Aus Datei")
    with app.app_context():
        read = db.session.scalar(db.select(Survey).filter_by(title="Aus Datei"))
        links = quotas.full_ends(read)[0]["links"]
        assert list(links) == [str(q.id) for q in read.quotas] and "1" not in links           # on the quotas of the new survey
        assert [(e["link"], e["mode"], e["cells"]) for e in links.values()] == [(men, "", {}), (age, "button", {"young": young})]
    uid = start(app, client)
    screen(app, uid, "1", "20")                                                              # a place in both quotas
    # men and "unter 30" are full: the first full quota in the order of the end that has a link decides
    assert screen(app, uid, "1", "22").get(f"/s/{uid}").headers["Location"] == men
    page = screen(app, uid, "2", "22").get(f"/s/{uid}").data.decode()                        # only "unter 30": its own link
    assert f'href="{young}"' in page                                                         # ... with the mode of its quota
    screen(app, uid, "2", "40")
    assert f'href="{age}"' in screen(app, uid, "2", "41").get(f"/s/{uid}").data.decode()     # "ab 30" has none: the quota's
    assert statuses(app) == ["active", "quotafull", "quotafull", "active", "quotafull"]
    # the order: cell, quota, end; nothing of its own leaves it to the rules and links of the settings
    view = {"q:7": "full", "qc:7": "a"}
    assert flow.end_link({"quotas": [7], "links": {"7": {"link": "", "mode": "", "cells": {"b": young}}}, "link": end,
                          "link_mode": "auto"}, view) == (end, "auto")
    assert flow.end_link({"quotas": [7], "links": {"7": {"link": age, "mode": "", "cells": {"a": young}}}, "link": end,
                          "link_mode": "auto"}, view) == (young, "auto")
    assert flow.end_link({"quotas": [7], "link": ""}, view) == (None, None) and flow.end_link(None, view) == (None, None)
    assert flow.node_links({"link": end, "links": {"7": {"link": age, "cells": {"a": young}}}}) == [end, age, young]


def trigger_form(*cards):
    """The form of an end as the page sends it: one card per trigger, in their order. card: (number, fields)."""
    data = MultiDict()
    for k, fields in cards:
        data.add("case", str(k))
        for key, value in fields.items():
            for v in value if isinstance(value, list) else [value]:
                data.add(f"case{k}_{key}", v)
    return data


def test_end_with_several_triggers(app, client, survey):
    men, young, age = (f"https://panel.example/{name}" for name in ("men", "young", "age"))
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])])
    client.post(Q + "/add")
    client.post(Q + "/2", data=cells_form([("unter 30", "jung", 1, [(3, "lt", "30")]), ("ab 30", "alt", 1, [(3, "ge", "30")])],
                                          name="Alter", code="alter"))
    set_flow(app)
    cards = [(0, {"when": "cellfull", "quota": "2", "cells": ["jung", "weg"], "text_de": "Ihre Altersgruppe ist voll.", "link": young,
                  "link_mode": "button", "quotas": "1"}),
             (1, {"when": "quotafull", "quotas": ["1", "99"], "rule": "any", "link": men}),
             (2, {"when": "quotafull", "quotas": "2", "rule": "all", "link": age, "link_mode": "auto"}),
             (3, {"when": "nocell", "nquotas": "1", "text_de": "Diesmal suchen wir Männer."}),
             (4, {"when": "", "link": "https://panel.example/ignored"}),                       # no trigger chosen: left out
             (5, {"when": "excluded", "remove": "1"})]                                         # ticked "entfernen"
    r = client.post(E + "/flow/qe1/update", data=trigger_form(*cards[:1], (1, {**cards[1][1], "link": "http://plain.example/"})),
                    follow_redirects=True)
    assert "Auslöser 2 (Quote voll), Abschlusslink" in r.data.decode()                      # links are checked, nothing saved
    r = client.post(E + "/flow/qe1/update", data=trigger_form(cards[4], cards[5]), follow_redirects=True)
    assert "Ein Ende braucht mindestens einen Auslöser" in r.data.decode()
    client.post(E + "/flow/qe1/update", data=trigger_form(*cards))
    with app.app_context():
        s = db.session.get(Survey, 1)
        node = json.loads(json.dumps(flow.find(flow.tree(s), "qe1")[0]))                    # a copy to take apart
        assert set(node) == {"t", "id", "cases"}                                            # the older form is gone
        ids = [case.pop("id") for case in node["cases"]]
        assert len(set(ids)) == 4 and all(i.startswith("c") for i in ids)
        blank = {"de": "", "en": ""}
        assert node["cases"] == [
            {"when": "cellfull", "quota": 2, "cells": ["jung"], "text": {"de": "Ihre Altersgruppe ist voll.", "en": ""},
             "link": young, "link_mode": "button"},
            {"when": "quotafull", "quotas": [1], "rule": "any", "text": blank, "link": men, "link_mode": "now"},
            {"when": "quotafull", "quotas": [2], "rule": "all", "text": blank, "link": age, "link_mode": "auto"},
            {"when": "nocell", "quotas": [1], "text": {"de": "Diesmal suchen wir Männer.", "en": ""}, "link": "", "link_mode": "now"}]
        assert flow.end_label(node) == "mehrere Auslöser" and flow.child_label(s, node) == "Ende (mehrere Auslöser)"
        assert quotas.end_quotas(node) == [2, 1, 2] and quotas.full_ends(s) == [flow.find(flow.tree(s), "qe1")[0]]
        assert not [m for kind, m, _ in flow.check(s) if "Ende" in m]
        sig = runtime._signature(s)
    html = client.get("/surveys/1/flow/").data.decode()
    assert html.count("data-case>") == 5 and "data-case-template" in html                    # four cards and the one to add
    assert "Ende (mehrere Auslöser)" in html and f'name="case0_link" value="{young}"' in html
    assert "Teilquote voll: Alter, 1 Teilquote · Quote voll: Geschlecht x Alter · Quote voll: Alter · Keine Teilquote passt: " in html
    assert 'name="case0_cells" value="jung" checked' in html and 'name="case0_cells" value="alt" checked' not in html
    assert '"2": [["jung", "unter 30"], ["alt", "ab 30"]]' in html                           # the cells of each quota, for the page
    # the order of the cards is the order of the triggers; the ids stay with them
    client.post(E + "/flow/qe1/update", data=trigger_form(*[(k, {**cards[k][1], "id": ids[k]}) for k in (1, 0, 2, 3)]))
    with app.app_context():
        node, _ = flow.find(flow.tree(db.session.get(Survey, 1)), "qe1")
        assert [(c["id"], c["when"]) for c in node["cases"]] == [(ids[1], "quotafull"), (ids[0], "cellfull"), (ids[2], "quotafull"),
                                                                 (ids[3], "nocell")]
    client.post(E + "/flow/qe1/update", data=trigger_form(*[(k, {**cards[k][1], "id": ids[k]}) for k in (0, 1, 2, 3)]))
    # the translation page has a field per trigger; a corrected text is no change of the flow's structure
    with app.app_context():
        from app import translations

        s = db.session.get(Survey, 1)
        unit = next(u for u in translations.units(s, "en") if u.key == "flow:qe1")
        assert unit.get("de") == {ids[0]: "Ihre Altersgruppe ist voll.", ids[1]: "", ids[2]: "", ids[3]: "Diesmal suchen wir Männer."}
        assert unit.role(ids[3]) == "Auslöser 4: Keine Teilquote passt"
        unit.set("en", {ids[0]: "Your age group is full."})
        db.session.commit()
        node, _ = flow.find(flow.tree(db.session.get(Survey, 1)), "qe1")
        assert node["cases"][0]["text"] == {"de": "Ihre Altersgruppe ist voll.", "en": "Your age group is full."}
        assert runtime._signature(db.session.get(Survey, 1)) == sig
    # the copy and the survey file carry the triggers, on the quotas of the new survey
    client.post("/surveys/1/duplicate", data={"title": "Zweite Welle"})
    data = json.loads(client.get("/surveys/1/export/survey.json").data)
    assert data["version"] == 6 and [c["when"] for c in next(n for n in data["flow"]["children"] if n.get("id") == "qe1")["cases"]] == \
        ["cellfull", "quotafull", "quotafull", "nocell"]
    _read_in(client, data, title="Aus Datei")
    with app.app_context():
        for title in ("Zweite Welle", "Aus Datei"):
            twin = db.session.scalar(db.select(Survey).filter_by(title=title))
            first, second = (q.id for q in twin.quotas)
            found = flow.cases(quotas.full_ends(twin)[0])
            assert first != 1 and [(c["when"], c.get("quota"), c.get("quotas"), c["link"]) for c in found] == [
                ("cellfull", second, None, young), ("quotafull", None, [first], men), ("quotafull", None, [second], age),
                ("nocell", None, [first], "")], title
            assert found[0]["cells"] == ["jung"] and found[0]["text"]["de"] == "Ihre Altersgruppe ist voll."
    uid = start(app, client)
    screen(app, uid, "1", "20")                                                              # a place in both quotas
    page = screen(app, uid, "1", "22").get(f"/s/{uid}").data.decode()                        # men and "unter 30" are full:
    assert "Ihre Altersgruppe ist voll." in page and f'href="{young}"' in page               # the first trigger that holds
    assert screen(app, uid, "1", "40").get(f"/s/{uid}").headers["Location"] == men           # only the men are full
    assert "Ihre Altersgruppe ist voll." in screen(app, uid, "2", "22").get(f"/s/{uid}").data.decode()
    page = screen(app, uid, "2", "40").get(f"/s/{uid}").data.decode()                        # a woman with room in her age group:
    assert "Diesmal suchen wir Männer." in page and "href=" not in page.split("p-end")[1][:600]  # no cell among the men
    assert "Hauptteil" not in page and statuses(app) == ["active", "quotafull", "quotafull", "quotafull", "screenout"]
    with app.app_context():
        assert counters(app, "alt") == {"running": 0, "done": 0, "refused": 0, "over": 0}    # the places taken went back
        assert not quotas.recount(db.session.get(Survey, 1))


def test_triggers_follow_their_quotas_and_the_check_names_what_is_missing(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")]), ("Frauen", "f", 1, [(2, "eq", "2")])])
    cards = [(0, {"when": "screenout", "text_de": "Leider nein."}), (1, {"when": "cellfull", "quota": "1"}),
             (2, {"when": "quotafull"}), (3, {"when": "nocell", "nquotas": "1"})]
    client.post(E + "/flow/qe1/update", data=trigger_form(*cards))
    with app.app_context():
        s = db.session.get(Survey, 1)
        text = " | ".join(m for _, m, _ in flow.check(s))
        assert "Der Auslöser „Screen-out“ greift immer; die Auslöser dahinter kommen nie zum Zug" in text
        assert "Ein Ende außerhalb einer Bedingung beendet die Befragung für alle" in text
        assert "Ende (Teilquote voll) ohne Teilquote" in text and "Ende (Quote voll) ohne Quote" in text
        assert quotas.exhausted(s) is False
    cards = [(0, {"when": "cellfull", "quota": "1", "cells": ["m", "f"]}), (1, {"when": "nocell", "nquotas": "1"}),
             (2, {"when": "quotafull", "quotas": "1"})]
    client.post(E + "/flow/qe1/update", data=trigger_form(*cards))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not [m for kind, m, _ in flow.check(s) if "Ende" in m or "beendet niemanden" in m]
        ids = [c.id for c in db.session.get(Quota, 1).cells]
    # a cell that gets another code stays ticked; one that is deleted goes
    client.post(Q + "/1", data=cells_form([("Männer", "maenner", 1, [(2, "eq", "1")])], c0_id=str(ids[0])))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert flow.cases(flow.find(flow.tree(s), "qe1")[0])[0]["cells"] == ["maenner"]
        for cell in db.session.get(Quota, 1).cells:                                           # spent, and nobody passes
            cell.done = cell.target
        assert quotas.exhausted(s)                                                            # ... so the survey may close
    client.post(Q + "/1/delete")
    with app.app_context():
        found = flow.cases(flow.find(flow.tree(db.session.get(Survey, 1)), "qe1")[0])
        assert [(c.get("quota"), c.get("quotas"), c.get("cells")) for c in found] == [(None, None, []), (None, [], None), (None, [], None)]


def test_draft_with_a_quota_check_gets_the_new_form(app, client, survey):
    questionnaire(client)
    client.post(Q + "/add")
    client.post(Q + "/1", data=cells_form([("Männer", "m", 1, [(2, "eq", "1")])]))
    old = {"t": "quota", "id": "qn1", "quotas": [1], "full": "end", "text": {"de": "Ihre Gruppe ist voll."},
           "link": "https://panel.example/full", "link_mode": "button"}
    with app.app_context():
        s = db.session.get(Survey, 1)
        quota = db.session.get(Quota, 1)
        quota.no_cell, quota.count_at = "end", "check"
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2}, old,
                                            {"t": "block", "block": 3}, {"t": "block", "block": 4}]}
        db.session.commit()
        assert quotas.upgrade_drafts() == 1 and quotas.upgrade_drafts() == 0                # at the start of the server, once
        s = db.session.get(Survey, 1)
        top = s.flow["children"]
        assert [n["t"] for n in top] == ["block", "block", "quota", "branch", "end", "block", "block"]
        assert top[2] == {"t": "quota", "id": "qn1", "quotas": [1]}
        assert top[3]["cond"] == {"item": "q:1", "op": "eq", "value": "none"} and top[3]["children"][0]["kind"] == "screenout"
        assert {k: top[4][k] for k in ("kind", "quotas", "rule", "text", "link", "link_mode")} == {
            "kind": "quotafull", "quotas": [1], "rule": "any", "text": {"de": "Ihre Gruppe ist voll."},
            "link": "https://panel.example/full", "link_mode": "button"}
        quota = db.session.get(Quota, 1)
        assert (quota.no_cell, quota.count_at) == ("continue", "complete")
        assert not [m for kind, m in quotas.check(s) if kind == "error"]
    uid = start(app, client)
    screen(app, uid)
    assert "Ihre Gruppe ist voll." in screen(app, uid).get(f"/s/{uid}").data.decode()        # behaves as before
    assert "andere Zielgruppe" in screen(app, uid, gender="2").get(f"/s/{uid}").data.decode()
    assert statuses(app) == ["active", "quotafull", "screenout"]


def test_survey_in_the_field_keeps_its_quota_check(app, client, survey):
    # changing the flow of a running survey would restart the participations under way: its "Quotenprüfung" stays
    # and ends a full quota itself, with its own text
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])], full="continue")
    uid = start(app, client)
    old = {"t": "quota", "id": "qn1", "quotas": [1], "full": "end", "text": {"de": "Ihre Gruppe ist voll."}, "link": "",
           "link_mode": "now"}
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2}, old,
                                            {"t": "block", "block": 3}, {"t": "block", "block": 4}]}
        db.session.commit()
        assert quotas.upgrade_drafts() == 0 and flow.normalize(s)["children"][2] == old
        db.session.commit()
    screen(app, uid)
    assert "Ihre Gruppe ist voll." in screen(app, uid).get(f"/s/{uid}").data.decode()
    assert statuses(app) == ["active", "quotafull"]
    html = client.get("/surveys/1/flow/").data.decode()
    assert "mit einer „Quotenprüfung“ gestartet" in html and "wenn voll: Ende „Quote voll“" in html


def test_full_quota_may_go_on_instead_of_ending(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])], full="continue")
    uid = start(app, client)
    screen(app, uid)
    second = screen(app, uid)
    assert "Hauptteil" in second.get(f"/s/{uid}").data.decode()                              # goes on without a place
    assert "Vielen Dank für Ihre Teilnahme" in finish(uid, second).data.decode()
    assert counters(app) == {"running": 1, "done": 0, "refused": 1, "over": 0}


def test_text_and_link_of_the_check(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])])
    client.post("/surveys/1/panel", data={"param_name": "PID", "param_mode": "redirect", "param_required": "0",
                                          "param_allowed": "", "redirect_quotafull": "https://panel.example/full?id={{PID}}",
                                          "mode_quotafull": "button"})
    set_flow(app, text={"de": "Ihre Gruppe ist schon voll."})
    uid = start(app, client)
    screen(app, uid, query="?PID=a1")
    html = screen(app, uid, query="?PID=b2").get(f"/s/{uid}").data.decode()
    assert "Ihre Gruppe ist schon voll." in html and 'href="https://panel.example/full?id=b2"' in html


# --- soft quotas ----------------------------------------------------------------------------------------


def test_soft_quota_lets_in_until_the_target_is_completed(app, client, survey):
    build(app, client, cells=[("Männer", "m", 2, [(2, "eq", "1")])], mode="soft", tolerance="1", tolerance_unit="n")
    uid = start(app, client)
    a, b, c = screen(app, uid), screen(app, uid), screen(app, uid)                          # three inside: target 2 + tolerance 1
    assert counters(app)["running"] == 3
    assert "bereits genügend" in screen(app, uid).get(f"/s/{uid}").data.decode()            # the cap
    finish(uid, a), finish(uid, b)
    assert counters(app) == {"running": 1, "done": 2, "refused": 1, "over": 0}
    assert "bereits genügend" in screen(app, uid).get(f"/s/{uid}").data.decode()            # target completed: closed
    assert "Vielen Dank für Ihre Teilnahme" in finish(uid, c).data.decode()                 # who is inside may finish
    assert counters(app) == {"running": 0, "done": 3, "refused": 2, "over": 0}
    with app.app_context():
        quota = db.session.get(Quota, 1)
        assert quotas.tolerance_of(quota, quota.cells[0]) == 1
        quota.tolerance, quota.tolerance_pct = 10, True
        assert quotas.tolerance_of(quota, quota.cells[0]) == 1                              # 10 % of 2, at least 1
        quota.cells[0].target = 55
        assert quotas.tolerance_of(quota, quota.cells[0]) == 6


# --- what never counts ----------------------------------------------------------------------------------


def test_preview_tries_out_both_ways_and_counts_nothing(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])])
    html = client.get(E + "/preview?restart=1").data.decode()
    assert "Quote: frei" in html and "voll simulieren" in html and "qfull=qn1&on=1" in html
    client.post(E + "/preview", data=CONSENT_OK)
    client.post(E + "/preview", data={"nav": "next", "i2": "1", "i3": "30"})
    assert "Hauptteil" in client.get(E + "/preview").data.decode()
    client.get(E + "/preview?qfull=qn1&on=1")                                          # a new test run, check full
    client.post(E + "/preview", data=CONSENT_OK)
    client.post(E + "/preview", data={"nav": "next", "i2": "1", "i3": "30"})
    html = client.get(E + "/preview").data.decode()
    assert "Vorschau beendet" in html and "Quote voll" in html and "Teilquote „Männer“" in html
    assert counters(app) == {"running": 0, "done": 0, "refused": 0, "over": 0}
    client.post("/surveys/1/dummy", data={"n": "12"})
    assert counters(app) == {"running": 0, "done": 0, "refused": 0, "over": 0}               # the real counters stay
    with app.app_context():
        dummies = db.session.scalars(db.select(Response).filter_by(source="dummy")).all()
        men = [r for r in dummies if (r.quota or {}).get("1", {}).get("cell") == 1]
        # the dummies count among themselves: one man gets the place, the others see "quota full"
        assert sorted(r.status for r in men) == ["completed"] + ["quotafull"] * (len(men) - 1) and len(men) > 1
        assert all(r.status == "completed" for r in dummies if r not in men)
        assert quotas.dummy_tally(db.session.get(Survey, 1)) == {1: 1}
    client.post("/surveys/1/dummy", data={"n": "6"})                                          # a later portion counts on
    with app.app_context():
        assert quotas.dummy_tally(db.session.get(Survey, 1)) == {1: 1}


def test_declining_gives_the_place_back(app, client, survey):
    """A consent behind the check: the declined participation is deleted, its place is free again."""
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])])
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 2}, {"t": "quota", "id": "qn1", "quotas": [1], "full": "end"},
                                            {"t": "block", "block": 1}, {"t": "block", "block": 3}, {"t": "block", "block": 4}]}
        flow.normalize(s)
        s.status, s.opened_at = Survey.RUNNING, s.created_at
        db.session.commit()
        uid = s.code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "1", "i3": "30"})
    assert counters(app)["running"] == 1
    p.post(f"/s/{uid}", data={"i1": "0"})                                                   # declines
    assert counters(app) == {"running": 0, "done": 0, "refused": 0, "over": 0}
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0


def test_full_cell_on_url_parameters_turns_away_at_the_door(app, client, survey):
    questionnaire(client)
    client.post("/surveys/1/panel", data={"param_name": "kurs", "param_mode": "store", "param_required": "0",
                                          "param_allowed": "a, b"})
    client.post(Q + "/add")
    client.post(Q + "/1", data=cells_form([("Kurs A", "a", 1, [("p:kurs", "eq", "a")]), ("Kurs B", "b", 1, [("p:kurs", "eq", "b")])]))
    set_flow(app)
    client.post(E + "/flow/qe1/update", data={"kind": "quotafull", "quotas": "1", "qmode_1": "button",
                                                "clink_1_a": "https://panel.example/kurs-a"})
    uid = start(app, client)
    first = screen(app, uid, query="?kurs=a")
    assert "Hauptteil" in first.get(f"/s/{uid}").data.decode()
    before = len(statuses(app))
    r = app.test_client().get(f"/s/{uid}?kurs=a")                                           # the cell is full: no first page
    assert r.status_code == 200 and "bereits genügend Teilnahmen" in r.data.decode()
    assert 'href="https://panel.example/kurs-a"' in r.data.decode()                         # with the link of that cell
    assert len(statuses(app)) == before and counters(app, "a")["refused"] == 0              # nothing stored or counted
    assert "Einwilligung" in app.test_client().get(f"/s/{uid}?kurs=b").data.decode()         # the other course gets in


# --- export, state, duplicate ---------------------------------------------------------------------------


def test_export_and_codebook(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")]), ("Frauen", "f", 5, [(2, "eq", "2")])])
    uid = start(app, client)
    finish(uid, screen(app, uid))
    screen(app, uid).get(f"/s/{uid}")                                                       # full
    screen(app, uid, "2")                                                                   # under way
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig"))))
    assert [(r["status"], r["quota_qa"], r["quota_qa_res"]) for r in rows] == \
        [("completed", "m", "1"), ("quotafull", "m", "2"), ("active", "f", "1")]
    book = {r[0]: r for r in csv.reader(io.StringIO(client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert "m = Männer [gender: männlich, Soll 1]" in book["quota_qa"][3] and "hart" in book["quota_qa"][4]
    assert "1 = Platz; 2 = voll" in book["quota_qa_res"][3]
    with app.app_context():                                                                 # a clean pre-launch check
        assert not [m for _, m in quotas.check(db.session.get(Survey, 1))]


def test_state_on_the_field_page_to_copy_and_as_csv(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")]), ("Frauen", "f", 5, [(2, "eq", "2")])])
    uid = start(app, client)
    finish(uid, screen(app, uid))
    screen(app, uid).get(f"/s/{uid}")
    screen(app, uid, "2")
    # Datenerhebung shows the short form, the tab "Quoten" the state with everything around it (UI audit G4)
    field = client.get("/surveys/1/field").data.decode().split('id="quotas"')[1].split("</section>")[0]
    assert "1 von 6" in field and "Quoten öffnen" in field and "Kopieren" not in field
    html = client.get("/surveys/1/flow/quotas/").data.decode()
    assert 'id="quotas"' in html and "Kopieren" in html and "state.csv" in html
    assert '<span class="quota-flag quota-flag-full">voll</span>' in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        table = quotas.report_table(s)
        assert table[0] == list(quotas.COLUMNS)
        assert table[1] == ["Geschlecht x Alter", "Männer", "m", "gender: männlich", 1, 1, 0, 0, 1, 0, "voll"]
        assert table[2][4:] == [5, 0, 1, 5, 0, 0, "offen"] and table[3][1] == "Summe" and table[3][4:8] == [6, 1, 1, 5]
        assert dict(quotas.totals(s)) == {"begonnen": 3, "abgeschlossen": 1, "Screen-out": 0, "Quote voll": 1, "nicht abgeschlossen": 1}
        text = quotas.report_text(s)
        assert text.startswith("Quotenstand: Test (1)\nStand: ") and "Männer\tm\tgender: männlich\t1\t1\t0\t0\t1\t0\tvoll" in text
    r = client.get(Q + "/state.csv?sep=semicolon")
    body = r.data.decode("utf-8-sig")
    assert r.headers["Content-Disposition"].startswith('attachment; filename="quotenstand_1_') and "Männer;m;" in body
    assert "gender:" not in client.get(Q + "/state.csv?conds=0").data.decode("utf-8-sig")
    assert "Geschlecht x Alter" in html and "\tm\t" in html                                  # the text to copy is on the page


def test_duplicate_takes_quotas_without_counts(app, client, survey):
    build(app, client, cells=[("Männer", "m", 3, [(2, "eq", "1"), (3, "ge", "18")])])
    uid = start(app, client)
    finish(uid, screen(app, uid))
    client.post("/surveys/1/duplicate", data={"title": "Zweite Welle"})
    with app.app_context():
        twin = db.session.scalar(db.select(Survey).filter_by(title="Zweite Welle"))
        quota = twin.quotas[0]
        cell = quota.cells[0]
        assert (quota.code, cell.code, cell.target, cell.done, cell.running) == ("qa", "m", 3, 0, 0)
        own = {i.code: i.id for b in twin.blocks for i in b.items}
        assert cell.conds == [{"item": own["gender"], "op": "eq", "value": "1"}, {"item": own["age"], "op": "ge", "value": "18"}]
        assert quotas.nodes(twin)[0]["quotas"] == [quota.id] and quota.id != 1
        assert quotas.full_ends(twin)[0]["quotas"] == [quota.id]                            # the end names the copy's quota
        assert not [m for _, m in quotas.check(twin)]
        assert db.session.get(QuotaCell, 1).done == 1                                       # the original keeps its count


def test_deleting_a_survey_takes_its_quotas_along(app, client, survey):
    build(app, client)
    client.post("/surveys/1/archive", data={"archived": "1"})
    client.post("/surveys/1/delete", data={"confirm": "1"})
    with app.app_context():
        assert db.session.get(Survey, 1) is None
        assert db.session.scalar(db.select(db.func.count(Quota.id))) == 0
        assert db.session.scalar(db.select(db.func.count(QuotaCell.id))) == 0


def test_a_new_quota_brings_its_calculation_and_end(app, client, survey):
    """Ralf, 2026-10-03 (UI/UX audit, U6): creating a quota also puts a calculation and an end "Quote voll" with it
    into the flow, before the separated section, unless the box is unticked; one step for "Rückgängig"."""
    questionnaire(client)
    page = client.get(Q + "/").data.decode()
    assert 'name="with_flow" value="1" checked' in page and '<details class="panel quota-intro" open>' in page
    client.post(Q + "/add", data={"with_flow": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        top = [n["t"] for n in flow.tree(s)["children"]]
        calc, end = flow.walk_nodes(s, "quota")[0], flow.walk_nodes(s, "end")[0]
        qid = s.quotas[0].id
        assert calc["quotas"] == [qid] and flow.cases(end)[0]["when"] == "quotafull" and flow.cases(end)[0]["quotas"] == [qid]
        assert top.index("quota") + 1 == top.index("end") and ("split" not in top or top.index("end") < top.index("split"))
        assert quotas.full_ends(s) == [end]
    page = client.get(Q + "/").data.decode()
    assert '<details class="panel quota-intro">' in page and "beendet niemanden" not in page
    client.post(Q + "/add")                                                  # unticked: the quota alone
    with app.app_context():
        assert len(flow.walk_nodes(db.session.get(Survey, 1), "quota")) == 1
