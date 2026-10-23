"""The federal state of a postal code as something a condition can test: Teilquoten and the flow
(app/items/postal_code.py state, app/flow.py "st:<id>", app/conditions.py)."""
from werkzeug.datastructures import MultiDict

from app import conditions, flow, quotas
from app.extensions import db
from app.items import postal_code
from app.items.federal_state import STATE_CODES
from app.models import Quota, QuotaCell, Response, Survey
from tests.conftest import CONSENT_OK, add
from tests.quotas.test_quotas import E, Q, cells_form, questionnaire, set_flow, start

NORTH = "1, 2, 3, 4"


def plz_survey(app, client, cells=None):
    """gender (2), age (3), plz (6, block 2); one quota on the state of the postal code."""
    questionnaire(client)
    add(client, "postal_code", block=2)                              # 6
    client.post(E + "/items/6", data={"code": "plz", "question": "Postleitzahl"})
    client.post(Q + "/add")
    cells = cells or [("NRW", "nw", 1, [("st:6", "eq", "5")]), ("Norden", "nord", 5, [("st:6", "in", NORTH)])]
    assert "gespeichert" in client.post(Q + "/1", data=cells_form(cells), follow_redirects=True).data.decode()
    set_flow(app)


def person(app, uid, plz, gender="1"):
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": gender, "i3": "30", "i6": plz})
    return p


def cell_codes(app):
    with app.app_context():
        out = []
        for r in db.session.scalars(db.select(Response).order_by(Response.id)):
            entry = (r.quota or {}).get("1") or {}
            cell = db.session.get(QuotaCell, entry["cell"]) if entry.get("cell") else None
            out.append((cell.code if cell else None, entry.get("res")))
        return out


def test_every_postal_code_has_a_state():
    known = {"52062": "5", "10115": "11", "80331": "9", "20095": "2", "28195": "4", "27568": "4", "01067": "14",
             "99084": "16", "66111": "10", "55116": "7", "65183": "6", "39104": "15", "19053": "13", "24103": "1",
             "14467": "12", "30159": "3", "70173": "8", "19273": "3"}                        # Amt Neuhaus: Niedersachsen
    assert {plz: postal_code.state(plz) for plz in known} == known
    assert postal_code.state(" 52 062 ") == "5" and postal_code.state("00000") is None and postal_code.state(None) is None
    table = postal_code._states()
    assert set(table.values()) == set(STATE_CODES)
    assert not [plz for plz in postal_code.table() if plz not in table]                      # also codes without an area
    assert postal_code.state("60312") == "6"                                                 # an area, not in GeoNames


def test_condition_is_offered_ticked_and_worded(app, client, survey):
    plz_survey(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for_cells, for_flow = conditions.describe(s), conditions.describe(s, everything=True)
        assert "st:6" in for_cells and "6" not in for_cells                                  # nothing to tick on the code itself
        assert for_flow["6"]["kind"] == "text" and list(for_flow).index("st:6") == list(for_flow).index("6") + 1
        info = for_cells["st:6"]
        assert (info["kind"], info["modes"], info["multi"]) == ("choice", "choice", False)
        assert len(info["options"]) == 16 and ("5", "Nordrhein-Westfalen") in info["options"]
        assert info["label"] == "plz · Bundesland (aus der Postleitzahl)"
        cells = db.session.get(Quota, 1).cells
        assert cells[0].conds == [{"item": "st:6", "op": "eq", "value": "5"}]
        assert cells[1].conds == [{"item": "st:6", "op": "in", "value": NORTH}]
        assert conditions.text(s, cells[0].conds[0]) == "plz (Bundesland): Nordrhein-Westfalen"
        assert conditions.text(s, cells[1].conds[0], short=True) == "Schleswig-Holstein oder Hamburg oder Niedersachsen oder Bremen"
        assert not quotas.check(s)
    html = client.get(Q + "/").data.decode()
    assert "plz · Bundesland (aus der Postleitzahl)" in html and "Nordrhein-Westfalen" in html


def test_quota_on_the_state(app, client, survey):
    plz_survey(app, client)
    uid = start(app, client)
    assert "Hauptteil" in person(app, uid, "52062").get(f"/s/{uid}").data.decode()            # Aachen
    assert "Hauptteil" in person(app, uid, "20095").get(f"/s/{uid}").data.decode()            # Hamburg
    assert "Hauptteil" in person(app, uid, "80331").get(f"/s/{uid}").data.decode()            # München: no Teilquote
    assert "bereits genügend" in person(app, uid, "50667").get(f"/s/{uid}").data.decode()     # Köln: NRW is full
    assert cell_codes(app) == [("nw", "in"), ("nord", "in"), (None, "none"), ("nw", "full")]


def test_cross_quota_and_dummy_data(app, client, survey):
    plz_survey(app, client, cells=[("Frauen NRW", "f_nw", 3, [(2, "eq", "2"), ("st:6", "eq", "5")]),
                                   ("Männer Bayern", "m_by", 3, [(2, "eq", "1"), ("st:6", "eq", "9")])])
    with app.app_context():
        assert not quotas.check(db.session.get(Survey, 1))
    client.post("/surveys/1/dummy", data={"n": "150"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        tally = quotas.dummy_tally(s)
        assert tally and max(tally.values()) <= 3
        for r in db.session.scalars(db.select(Response).filter(Response.source == "dummy")):
            entry = (r.quota or {}).get("1") or {}
            if entry.get("res") == "in":
                code = db.session.get(QuotaCell, entry["cell"]).code
                assert postal_code.state(r.answers["6"]) == {"f_nw": "5", "m_by": "9"}[code]


def test_branch_in_the_flow(app, client, survey):
    plz_survey(app, client)
    client.post(E + "/flow/add", data={"kind": "branch"})
    with app.app_context():
        nid = flow.walk_nodes(db.session.get(Survey, 1), "branch")[0]["id"]
    form = MultiDict({"name": "Stadtstaaten", "item": "st:6", "mode": "is"})
    form.setlist("codes", ["11", "2", "4"])
    html = client.post(E + f"/flow/{nid}/update", data=form, follow_redirects=True).data.decode()
    assert "Gespeichert" in html and "plz (Bundesland): Berlin oder Hamburg oder Bremen" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        node, _ = flow.find(flow.tree(s), nid)
        assert node["cond"] == {"item": "st:6", "op": "in", "value": "11, 2, 4"}
        assert flow.holds(node["cond"], {6: "10115"}) and not flow.holds(node["cond"], {6: "52062"})
        assert not flow.holds(node["cond"], {}) and not flow.holds(node["cond"], {6: "00000"})
        assert flow.holds({"item": "st:6", "op": "empty"}, {6: "00000"})                     # no state: as not answered
        assert flow.holds({"item": "st:6", "op": "answered"}, {6: "52062"})
        assert flow.holds({"item": "st:6", "op": "nin", "value": "5"}, {6: "10115"})
        assert flow.holds({"item": "st:6", "op": "ne", "value": "5"}, {6: "10115"})
        assert not [m for kind, m, _ in flow.check(s) if "Bundesland" in m]
        node["cond"] = {"item": "st:6", "op": "eq", "value": "77"}
        s.flow = dict(flow.tree(s))
        assert any("nennt kein Bundesland" in m for _, m, _ in flow.check(s))
        node["cond"] = {"item": "st:3", "op": "eq", "value": "5"}                             # not a postal code question
        assert any("nur zu einer Frage nach der Postleitzahl" in m for _, m, _ in flow.check(s))
        db.session.rollback()


def test_design_check_and_duplicate(app, client, survey):
    plz_survey(app, client, cells=[("NRW", "nw", 2, [("st:6", "eq", "5")]), ("falsch", "x", 2, [("st:6", "eq", "77")]),
                                   ("weg", "y", 2, [("st:99", "eq", "5")])])
    with app.app_context():
        text = " | ".join(m for _, m in quotas.check(db.session.get(Survey, 1)))
        assert "„77“ ist kein Antwortcode von plz" in text and "Frage einer Bedingung gibt es nicht mehr" in text
    client.post(Q + "/1", data=cells_form([("NRW", "nw", 2, [("st:6", "eq", "5")])]))
    with app.app_context():
        s = db.session.get(Survey, 1)                                # the check before the question: reported
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "quota", "id": "qn1", "quotas": [1]},
                                            {"t": "block", "block": 2}, {"t": "block", "block": 3}, {"t": "block", "block": 4}]}
        flow.normalize(s)
        assert any("erst nach der Quotenberechnung" in m for _, m in quotas.check(s))
        db.session.rollback()
    client.post("/surveys/1/duplicate", data={"title": "Zweite Welle"})
    with app.app_context():
        copy = db.session.scalar(db.select(Survey).filter_by(title="Zweite Welle"))
        twin = next(i for b in copy.blocks for i in b.items if i.code == "plz")
        assert twin.id != 6 and copy.quotas[0].cells[0].conds == [{"item": f"st:{twin.id}", "op": "eq", "value": "5"}]
        assert conditions.text(copy, copy.quotas[0].cells[0].conds[0]) == "plz (Bundesland): Nordrhein-Westfalen"
