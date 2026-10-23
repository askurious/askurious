"""Rechenwert: a number worked out from earlier answers (app/items/calc.py)."""
import json

from werkzeug.datastructures import MultiDict

from app import design
from app.extensions import db
from app.models import Item, Response, Survey, User
from tests.conftest import CONSENT_OK, add, fill_consent


def build(app, client):
    """Page 1: monthly income and the hours worked; then two Rechenwerte (income per year, income per hour).
    Page 2: a text that shows them and a constant sum over the yearly income."""
    fill_consent(client)
    for kind in ("text_input", "text_input", "calc", "calc", "page_break", "info", "constant_sum"):
        add(client, kind)                                              # 2 .. 8
    client.post("/surveys/1/editor/items/2", data={"code": "income", "question": "Monatseinkommen?", "validation": "decimal"})
    client.post("/surveys/1/editor/items/3", data={"code": "hours", "question": "Stunden im Monat?", "validation": "integer"})
    client.post("/surveys/1/editor/items/4", data={"code": "year", "question": "Jahreseinkommen", "calc_a": "2", "calc_op": "mul",
                                                   "calc_b": "", "calc_fixed": "12", "calc_places": "0", "calc_unit": "€"})
    client.post("/surveys/1/editor/items/5", data={"code": "rate", "question": "Stundenlohn", "calc_a": "2", "calc_op": "div",
                                                   "calc_b": "3", "calc_fixed": "99", "calc_places": "2", "calc_unit": "€"})
    client.post("/surveys/1/editor/items/7", data={"code": "fazit", "question": "Im Jahr: {{year|unbekannt}}, je Stunde: {{rate|unbekannt}}."})
    client.post("/surveys/1/editor/items/8", data=MultiDict([
        ("code", "spend"), ("question", "Wofür?"), ("answer_mode", "optional"), ("total", "100"),
        ("total_mode", "from"), ("total_from", "4"), ("total_missing", "skip"),
        ("option_id", "a"), ("option_code", "rent"), ("option_label", "Miete"),
        ("option_id", "b"), ("option_code", "rest"), ("option_label", "Rest")]))


def start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    return p, uid


def test_settings_and_value(app, client, survey):
    build(app, client)
    with app.app_context():
        year, rate = db.session.get(Item, 4), db.session.get(Item, 5)
        assert year.config == {"a": "2", "op": "mul", "b": None, "fixed": 12, "places": 0, "unit": "€"}
        assert rate.config["b"] == "3" and rate.config["fixed"] is None      # a question chosen: the fixed number does not count
        assert year.kind.validate(year) == [] and rate.kind.validate(rate) == []
        assert year.kind.formula(year) == "income × 12" and rate.kind.formula(rate) == "income ÷ hours"
        assert year.kind.value(year, {2: 2350.5}) == 28206 and year.kind.shown(year, 28206, "de") == "28.206 €"
        assert rate.kind.value(rate, {2: 2400, 3: 160}) == 15.0 and rate.kind.shown(rate, 15.0, "de") == "15,00 €"
        assert rate.kind.value(rate, {2: 2400, 3: 0}) is None and rate.kind.value(rate, {2: 2400}) is None
        # the questions on its own page and Rechenwerte before it are offered, nothing after it
        assert [ref for ref, _ in rate.kind.sources(rate)] == ["2", "3", "4"]
        assert [ref for ref, _ in year.kind.sources(year)] == ["2", "3"]
        # a chain: a Rechenwert as the first value of the next one
        rate.config = {**rate.config, "a": "4", "b": None, "fixed": 12, "op": "div"}
        assert rate.kind.value(rate, {2: 100}) == 100.0
        year.config = {**year.config, "a": "5"}                               # ... but never in a circle
        assert year.kind.value(year, {2: 100}) is None and any("nicht davor" in p for p in year.kind.validate(year))
        db.session.rollback()
        assert not [f for f in design.check(db.session.get(Survey, 1)) if f.item_id in (4, 5, 7, 8)]
    html = client.get("/surveys/1/editor/items/5").data.decode()
    assert "geteilt durch" in html and "income · Monatseinkommen?" in html and "year · Rechenwert: Jahreseinkommen" in html
    chips = client.get("/surveys/1/editor/items/7").data.decode()
    assert 'data-insert="{{year}}"' in chips and 'data-insert="{{rate}}"' in chips


def test_participants_never_see_it_but_texts_and_the_constant_sum_use_it(app, client, survey):
    build(app, client)
    p, uid = start(app, client)
    first = p.get(f"/s/{uid}").data.decode()
    assert "Monatseinkommen?" in first and "Jahreseinkommen" not in first and "Rechenwert" not in first
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "2.350,50", "i3": "160"})
    html = p.get(f"/s/{uid}").data.decode()
    assert "Im Jahr: 28.206 €, je Stunde: 14,69 €." in html
    assert 'data-total="28206"' in html                                # the constant sum distributes the yearly income
    p.post(f"/s/{uid}", data={"nav": "next", "i8_rent": "12000", "i8_rest": "16206", "i8_touched": "1"})
    with app.app_context():
        from app import export, results

        s = db.session.get(Survey, 1)
        response = db.session.scalars(db.select(Response).filter_by(is_preview=False)).one()
        assert response.status == Response.COMPLETED and "4" not in response.answers    # nothing is stored for it
        row = export.rows(s)[0]
        assert row["year"] == 28206 and row["rate"] == 14.69 and row["spend_total"] == 28206
        book = {r[0]: r for r in export.codebook(s)}
        assert "berechnet: income × 12" in " ".join(str(x) for x in book["year"])
        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 4)
        assert entry["columns"][0]["mean"] == 28206


def test_a_missing_part_leaves_it_empty(app, client, survey):
    build(app, client)
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "", "i3": "160"})
    html = p.get(f"/s/{uid}").data.decode()
    assert "Im Jahr: unbekannt, je Stunde: unbekannt." in html and "data-csum" not in html   # no total: the constant sum is left out
    with app.app_context():
        from app import export

        row = export.rows(db.session.get(Survey, 1), include_incomplete=True)[0]
        assert row["year"] in (None, "") and row["rate"] in (None, "")


def test_design_check_dummies_and_copies(app, client, survey):
    build(app, client)
    with app.app_context():
        from app import dummy, duplicate, transfer

        s = db.session.get(Survey, 1)
        info = db.session.get(Item, 7)
        info.texts = {**info.texts, "de": {"question": "Im Jahr: {{year}}."}}
        msgs = [f.message for f in design.check(s) if f.item_id == 7]
        assert any("{{year}} hat keinen Ersatztext" in m for m in msgs)
        first = db.session.get(Item, 2)
        first.texts = {**first.texts, "de": {"question": "Sie verdienen {{year|?}}"}}      # the Rechenwert stands after it
        assert any("ist ein Rechenwert, der erst danach steht" in f.message for f in design.check(s) if f.item_id == 2)
        db.session.rollback()
        source = db.session.get(Item, 2)
        source.config = {**source.config, "min": 500, "max": 9000}
        db.session.commit()
        dummy.generate(s, 5, seed=1)
        db.session.commit()
        for r in db.session.scalars(db.select(Response).filter_by(source="dummy")):
            assert sum(r.answers["8"].values()) == int(round(r.answers["2"] * 12 + 0.0))
        new, _ = duplicate.create(s, db.session.get(User, 1), "Welle 2")
        twin = {i.code: i for b in new.blocks for i in b.items}
        assert twin["year"].config["a"] == str(twin["income"].id) != "2"
        assert twin["rate"].config["b"] == str(twin["hours"].id) and twin["spend"].config["total_from"] == str(twin["year"].id)
        alone = duplicate.copy_item(db.session.get(Item, 5), new)
        assert alone.config["a"] is None and alone.config["b"] is None
        text = json.dumps(transfer.dump(s)).encode()
    with app.test_request_context():
        other, _ = transfer.create(transfer.loads(text), db.session.get(User, 1), "Dritte")
        twin = {i.code: i for b in other.blocks for i in b.items}
        assert twin["year"].config == {"a": str(twin["income"].id), "op": "mul", "b": None, "fixed": 12, "places": 0, "unit": "€"}


def test_conditions_of_the_flow_and_of_statements_on_a_rechenwert(app, client, survey):
    """A Rechenwert is a number a condition can test: offered as from … to, worked out from the answers so far."""
    from app import conditions, flow, rows

    fill_consent(client)
    add(client, "text_input")                                                   # 2
    client.post("/surveys/1/editor/items/2", data={"code": "income", "question": "Monatseinkommen?", "validation": "integer"})
    add(client, "calc")                                                         # 3
    client.post("/surveys/1/editor/items/3", data={"code": "year", "question": "Jahreseinkommen", "calc_a": "2", "calc_op": "mul",
                                                   "calc_b": "", "calc_fixed": "12", "calc_places": "0"})
    client.post("/surveys/1/editor/blocks"), client.post("/surveys/1/editor/blocks")   # blocks 3, 4
    add(client, "text_input", block=3)                                          # 4
    client.post("/surveys/1/editor/items/4", data={"code": "rich", "question": "Wie legen Sie an?"})
    add(client, "text_input", block=4)                                          # 5
    client.post("/surveys/1/editor/items/5", data={"code": "last", "question": "Sonst noch?"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        described = conditions.describe(s, everything=True)
        assert described["3"]["kind"] == "number" and described["3"]["label"] == "year · Rechenwert: Jahreseinkommen"
        assert "3" not in conditions.describe(s)                                # not in the Teilquoten
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {}, "children": [{"t": "block", "block": 3}]}, {"t": "block", "block": 4}]}
        db.session.commit()
        later = db.session.get(Item, 5)
        assert "3" in rows.offered(later)                                       # also for the statements of a later page
        assert flow.holds({"item": 3, "op": "ge", "value": "60000"}, {2: 5000}, s)
        assert not flow.holds({"item": 3, "op": "ge", "value": "60000"}, {2: 4000}, s)
        assert flow.holds({"item": 3, "op": "empty", "value": ""}, {}, s) and not flow.holds({"item": 3, "op": "lt", "value": "1"}, {}, s)
    client.post("/surveys/1/editor/flow/b1/update", data={"item": "3", "mode": "range", "from": "60000", "to": ""})
    with app.app_context():
        s = db.session.get(Survey, 1)
        node, _ = flow.find(flow.tree(s), "b1")
        assert node["cond"] == {"item": 3, "op": "ge", "value": "60000"}
        assert conditions.text(s, node["cond"]) == "year: ab 60000"
        assert not [p for p in flow.check(s) if "b1" in str(p) or "Bedingung" in p[1]]
    assert "year: ab 60000" in client.get("/surveys/1/flow/").data.decode()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    for income, asked in (("5000", True), ("4000", False)):
        p = app.test_client()
        p.get("/s/1")
        p.post("/s/1", data=CONSENT_OK)
        p.post("/s/1", data={"nav": "next", "i2": income})
        html = p.get("/s/1").data.decode()
        assert ("Wie legen Sie an?" in html) is asked and ("Sonst noch?" in html) is not asked


def test_rounds_half_up_and_chains_work_with_the_exact_value(app, client, survey):
    """R-3: half up (2,5 -> 3; 12,5 % -> 13; 1,005 -> 1,01), R-4: a chain reads the unrounded value."""
    build(app, client)
    with app.app_context():
        year, rate = db.session.get(Item, 4), db.session.get(Item, 5)
        rate.config = {**rate.config, "a": "2", "b": None, "fixed": 2, "op": "div", "places": 0}
        assert rate.kind.value(rate, {2: 5}) == 3                                   # round() gave 2
        rate.config = {**rate.config, "fixed": 8, "op": "pct"}
        assert rate.kind.value(rate, {2: 1}) == 13                                  # 12,5 % gave 12
        rate.config = {**rate.config, "fixed": 1, "op": "mul", "places": 2}
        assert rate.kind.value(rate, {2: 1.005}) == 1.01 and rate.kind.value(rate, {2: 2.675}) == 2.68
        assert rate.kind.value(rate, {2: 0.125}) == 0.13
        assert rate.kind.shown(rate, rate.kind.value(rate, {2: 0.125}), "de") == "0,13 €"
        # 2000 ÷ 3 with 0 decimals reads 667, but the next Rechenwert takes 666,67 … : times 3 is 2000, not 2001
        year.config = {**year.config, "a": "2", "op": "div", "fixed": 3, "places": 0}
        rate.config = {**rate.config, "a": "4", "b": None, "fixed": 3, "op": "mul", "places": 0}
        assert year.kind.value(year, {2: 2000}) == 667 and rate.kind.value(rate, {2: 2000}) == 2000
        rate.config = {**rate.config, "fixed": 160, "places": 1}
        assert rate.kind.value(rate, {2: 2000}) == 106666.7                         # 106 720 with the rounded 667
        db.session.rollback()
        book = {r[0]: r for r in __import__("app.export", fromlist=["x"]).codebook(db.session.get(Survey, 1))}
        assert "kaufmännisch gerundet" in " ".join(str(x) for x in book["year"])


def test_fixed_number_is_read_german_in_every_tab(app, client, survey):
    """A fixed number typed in the editor of an English survey: "1.000" is 1000 (it was 1.0), as in conditions."""
    build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages, s.default_language = ["en"], "en"
        db.session.commit()
    form = {"code": "year", "question": "Yearly income", "calc_a": "2", "calc_op": "mul", "calc_b": "", "calc_places": "0"}
    client.post("/surveys/1/editor/items/4?lang=en", data={**form, "calc_fixed": "1.000"})
    with app.app_context():
        assert db.session.get(Item, 4).config["fixed"] == 1000
    client.post("/surveys/1/editor/items/4?lang=en", data={**form, "calc_fixed": "1,125"})
    with app.app_context():
        item = db.session.get(Item, 4)
        assert item.config["fixed"] == 1.125 and item.kind.fixed_text(item, "en") == "1,125"   # shown as it is read back
