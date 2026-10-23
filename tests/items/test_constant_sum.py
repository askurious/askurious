import json

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import AnswerError
from app.items.constant_sum import even_split
from app.models import Item, Survey
from tests.conftest import add


def setup(client, required=True):
    add(client, "constant_sum")                                     # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "imp"), ("question", "Verteilen Sie 100 Punkte."), ("answer_mode", "required" if required else "optional"),
        ("total", "100"), ("unit", "Punkte"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Sicherheit"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Kosten"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Komfort")]))


def test_even_split():
    assert even_split(100, 3) == [34, 33, 33] and sum(even_split(7, 4)) == 7


def test_parse_sum_and_export(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == [] and item.config["total"] == 100
        form = MultiDict({"i2_1": "50", "i2_2": "30", "i2_3": "20"})
        with pytest.raises(AnswerError):                            # untouched even split is no answer
            item.kind.parse_answer(item, form)
        form["i2_touched"] = "1"
        assert item.kind.parse_answer(item, form) == {"1": 50, "2": 30, "3": 20}
        form["i2_3"] = "25"
        with pytest.raises(AnswerError, match="100"):               # without JavaScript: server checks the sum
            item.kind.parse_answer(item, form)
        assert item.kind.export_values(item, {"1": 50, "2": 30, "3": 20}) == {"imp_1": 50, "imp_2": 30, "imp_3": 20}
        assert [c for c, _ in item.kind.export_columns(item)] == ["imp_1", "imp_2", "imp_3"]


def test_optional_untouched_is_missing(app, client, survey):
    setup(client, required=False)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.parse_answer(item, MultiDict({"i2_1": "34", "i2_2": "33", "i2_3": "33"})) is None


def test_preview_and_dummy(app, client, survey):
    setup(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-csum="2"' in html and 'value="34"' in html and "Sicherheit" in html
    with app.app_context():
        from app import dummy
        from app.models import Response

        dummy.generate(db.session.get(Survey, 1), 5, seed=3)
        db.session.commit()
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(sum(a.values()) == 100 for a in answers)


def test_currency_unit(app, client, survey):
    setup(client)
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "budget"), ("question", "Budget"), ("total", "1000"), ("format", "currency"), ("currency", "EUR"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "A"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "B")]))
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "Verteilen Sie 1.000 €." in html and 'class="p-csum-unit">€<' in html


# --- the total from an earlier answer ----------------------------------------------------------------------


def personal(app, client, missing="skip", fallback="", pct=False, required=True):
    """Page 1: income (a number field); page 2: its distribution over rent and food."""
    from tests.conftest import fill_consent

    fill_consent(client)
    for kind in ("text_input", "page_break", "constant_sum"):
        add(client, kind)                                           # 2, 3, 4
    client.post("/surveys/1/editor/items/2", data={"code": "income", "question": "Monatseinkommen?", "validation": "decimal",
                                                   "answer_mode": "optional"})
    data = [("code", "spend"), ("question", "Wofür?"), ("answer_mode", "required" if required else "optional"),
            ("total", "100"), ("format", "currency"), ("currency", "EUR"),
            ("total_mode", "from"), ("total_from", "2"), ("total_missing", missing), ("total_fallback", fallback),
            ("option_id", "a"), ("option_code", "rent"), ("option_label", "Miete"),
            ("option_id", "b"), ("option_code", "food"), ("option_label", "Essen")]
    client.post("/surveys/1/editor/items/4", data=MultiDict(data + ([("export_pct", "1")] if pct else [])))


def start(app, client):
    from tests.conftest import CONSENT_OK

    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    return p, uid


def test_personal_total_settings(app, client, survey):
    personal(app, client, pct=True)
    with app.app_context():
        item = db.session.get(Item, 4)
        assert item.config["total_from"] == "2" and item.config["export_pct"] is True and "total_missing" not in item.config
        assert item.kind.total_sources(item) == [("2", "income · Monatseinkommen?")] and item.kind.validate(item) == []
        assert item.kind.total_for(item, {2: 2350.4}) == 2350 and item.kind.total_for(item, {2: 250000}) == 250000  # no cap
        assert item.kind.total_for(item, {}) is None and item.kind.total_for(item, {2: 0}) is None
        assert item.kind.total_for(item, {2: -5}) is None and item.kind.total_for(item, {2: "viel"}) is None
        assert [c for c, _ in item.kind.export_columns(item)] == ["spend_rent", "spend_food", "spend_total", "spend_rent_pct", "spend_food_pct"]
        assert item.kind.export_values(item, {"rent": 1500, "food": 500}) == {
            "spend_rent": 1500, "spend_food": 500, "spend_total": 2000, "spend_rent_pct": 75.0, "spend_food_pct": 25.0}
        assert item.kind.export_values(item, None)["spend_total"] is None
        # the question it comes from must stand on an earlier page and ask a number
        source = db.session.get(Item, 2)
        source.config = {**source.config, "validation": "none"}
        assert any("keine Zahl" in p for p in item.kind.validate(item))
    html = client.get("/surveys/1/editor/items/4").data.decode()
    assert "Summe aus einer früheren Antwort nehmen" in html and "income · Monatseinkommen?" in html
    # unticked: the fixed total again, nothing of the personal one stays
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "spend"), ("question", "Wofür?"), ("total", "100"), ("total_from", "2"), ("export_pct", "1"),
        ("option_id", "a"), ("option_code", "rent"), ("option_label", "Miete"),
        ("option_id", "b"), ("option_code", "food"), ("option_label", "Essen")]))
    with app.app_context():
        item = db.session.get(Item, 4)
        assert not item.kind.personal(item) and "export_pct" not in item.config and item.kind.total_for(item, {}) == 100


def test_personal_total_for_participants(app, client, survey):
    personal(app, client)
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "2350,4"})
    html = p.get(f"/s/{uid}").data.decode()
    assert 'data-total="2350"' in html and 'value="1175"' in html and 'step="20"' in html and "2.350 €" in html
    # the sum is checked against the person's total: 2000 of 2350 is no answer
    p.post(f"/s/{uid}", data={"nav": "next", "i4_rent": "1500", "i4_food": "500", "i4_touched": "1"})
    with app.app_context():
        from app.models import Response

        assert "4" not in (db.session.scalars(db.select(Response).filter_by(is_preview=False)).one().answers or {})
    p.post(f"/s/{uid}", data={"nav": "next", "i4_rent": "1500", "i4_food": "850", "i4_touched": "1"})
    with app.app_context():
        from app import export
        from app.models import Response

        response = db.session.scalars(db.select(Response).filter_by(is_preview=False)).one()
        assert response.answers["4"] == {"rent": 1500, "food": 850} and response.status == Response.COMPLETED
        row = export.rows(db.session.get(Survey, 1))[0]
        assert row["spend_total"] == 2350 and row["spend_rent"] == 1500


def test_personal_total_missing_skips_the_question(app, client, survey):
    personal(app, client)
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": ""}, follow_redirects=True)
    with app.app_context():
        from app import export, quality
        from app.models import Response

        response = db.session.scalars(db.select(Response).filter_by(is_preview=False)).one()
        assert response.status == Response.COMPLETED and "4" not in response.answers  # its page was never shown
        survey_ = db.session.get(Survey, 1)
        row = export.rows(survey_)[0]
        assert row["spend_total"] in (None, "") and row["spend_rent"] in (None, "")
        # not shown is not left out: only the income counts as a question left open
        items = export._items(survey_)
        shown = [i.code for i, _ in quality.shown(survey_, response, quality.context(survey_, items))]
        assert "spend" not in shown and "income" in shown


def test_personal_total_fallback(app, client, survey):
    personal(app, client, missing="fallback", fallback="2000")
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "0"})
    assert 'data-total="2000"' in p.get(f"/s/{uid}").data.decode()


def test_personal_total_changed_after_going_back(app, client, survey):
    personal(app, client)
    client.post("/surveys/1/settings", data={"allow_back": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.allow_back = True
        db.session.commit()
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "1000"})
    p.post(f"/s/{uid}", data={"nav": "back", "i4_rent": "700", "i4_food": "300", "i4_touched": "1"})
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "3000"})
    html = p.get(f"/s/{uid}").data.decode()
    # the stored distribution no longer adds up to the total: an even split of the new one, not answered yet
    assert 'data-total="3000"' in html and 'value="1500"' in html and "data-answered" not in html


def test_personal_total_dummies_results_and_copies(app, client, survey):
    personal(app, client, pct=True)
    with app.app_context():
        from app import dummy, duplicate, results, transfer
        from app.models import Response, User

        s = db.session.get(Survey, 1)
        source = db.session.get(Item, 2)
        source.config = {**source.config, "min": 500, "max": 9000}
        db.session.commit()
        dummy.generate(s, 6, seed=2)
        db.session.commit()
        answers = [r.answers for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(sum(a["4"].values()) == int(a["2"] + 0.5) for a in answers)
        entry = next(e for e in results.compute(s, sources=("dummy",))["items"] if e["item"].id == 4)
        cols = {c["code"]: c for c in entry["columns"]}
        assert set(cols) == {"spend_rent", "spend_food", "spend_total"}           # the percent columns once only
        assert cols["spend_rent"]["unit"] == "%" and abs(cols["spend_rent"]["mean"] + cols["spend_food"]["mean"] - 100) < 0.2
        assert "mittlerer Betrag" in cols["spend_rent"]["label"]
        # a copy and a survey file name the question of the copy
        new, _ = duplicate.create(s, db.session.get(User, 1), "Welle 2")
        twin = {i.code: i for b in new.blocks for i in b.items}
        assert twin["spend"].config["total_from"] == str(twin["income"].id) != "2"
        # copied alone into another survey, the question it came from stays behind: a fixed total again
        alone = duplicate.copy_item(db.session.get(Item, 4), new)
        assert "total_from" not in alone.config
        data = json.dumps(transfer.dump(s)).encode()
    with app.test_request_context():
        other, _ = transfer.create(transfer.loads(data), db.session.get(User, 1), "Dritte")
        twin = {i.code: i for b in other.blocks for i in b.items}
        assert twin["spend"].config["total_from"] == str(twin["income"].id) and twin["spend"].config["export_pct"] is True


def test_personal_total_leaves_an_option_not_shown_out_of_the_shares(app, client, survey):
    """D-2/R-1 (audit 2026-10-03): with a personal total, -77 (option not shown) is no amount. Option "car" seen by
    one of three people (200 of 1000): n = 1, M = 20 %, mean amount 200 on the results page and on a dashboard
    (before: n = 3, M = 2.8 %, Min -7.7 %)."""
    from app import boards, export, results
    from app.models import Response

    personal(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 4)
        # a third option only some people see (carried from an earlier question: app/carry.py)
        item.config = {**item.config, "options": item.config["options"] + [
            {"id": "c", "code": "car", "src": {"item": 2, "code": "1", "which": "chosen"}}]}
        db.session.commit()
        assert item.kind.missing_codes(item, "spend_car") == (-77,)
        for total, answer in ((1000, {"rent": 500, "food": 300, "car": 200}), (1000, {"rent": 600, "food": 400}),
                              (2000, {"rent": 1500, "food": 500})):
            db.session.add(Response(survey_id=1, status=Response.COMPLETED, source="real", language="de",
                                    answers={"2": total, "4": answer}, qversion=s.qversion))
        db.session.commit()
        table = export.rows(s, False, ("real",))
        assert [r["spend_car"] for r in table] == [200, -77, -77]
        cols = {c["code"]: c for c in next(e for e in results.compute(s)["items"] if e["item"].id == 4)["columns"]}
        car = cols["spend_car"]
        assert car["n"] == 1 and car["mean"] == 20.0 and car["min"] == 20.0 and car["not_shown"] == 2
        assert "mittlerer Betrag 200" in car["label"]
        assert cols["spend_rent"]["n"] == 3
        tile = {r["label"]: r for r in boards._ranks(item, table, "de")["rows"]}
        assert tile["car"]["n"] == 1 and tile["car"]["_xs"] == [20.0]


def test_personal_total_shares_round_half_up_after_the_mean(app, client, survey):
    """R-7: 100 of 1600 is 6,25 %: the export column reads 6,3 (round() gave 6,2), and the results page takes the
    mean of the unrounded shares (6,25, not the mean of 6,2 and 6,2)."""
    from app import export, results
    from app.models import Response

    personal(app, client, pct=True)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for _ in range(2):
            db.session.add(Response(survey_id=1, status=Response.COMPLETED, source="real", language="de",
                                    answers={"2": 1600, "4": {"rent": 100, "food": 1500}}, qversion=s.qversion))
        db.session.commit()
        assert [r["spend_rent_pct"] for r in export.rows(s, False, ("real",))] == [6.3, 6.3]
        cols = {c["code"]: c for c in next(e for e in results.compute(s)["items"] if e["item"].id == 4)["columns"]}
        assert cols["spend_rent"]["mean"] == 6.25 and cols["spend_rent"]["median"] == 6.25
