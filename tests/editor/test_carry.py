"""Options carried from an earlier question into a later one (app/carry.py)."""
import json

from werkzeug.datastructures import MultiDict

from app import carry
from app.extensions import db
from app.models import Item, Response, Survey, User
from tests.conftest import CONSENT_OK, add, fill_consent

TARGETS = ("single_choice", "multiple_choice", "ranking", "constant_sum", "likert")


def build(app, client, which="chosen"):
    """Page 1: devices (multiple choice; "Anderes" with a text field, "keine Angabe" as missing is not possible
    there, so a plain fourth option). Page 2: one question of every kind that takes its options."""
    fill_consent(client)
    add(client, "multiple_choice")                                                  # 2
    add(client, "page_break")                                                       # 3
    for kind in TARGETS:
        add(client, kind)                                                           # 4 .. 8
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "geraete"), ("question", "Welche Geräte nutzen Sie?"), ("answer_mode", "optional"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Smartphone"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Tablet"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Laptop"),
        ("option_id", "d"), ("option_code", "9"), ("option_label", "Anderes, und zwar"), ("option_text", "d")]))
    common = [("carry_item", "2"), ("carry_which", which), ("answer_mode", "optional")]
    client.post("/surveys/1/editor/items/4", data=MultiDict(common + [
        ("code", "meist"), ("question", "Welches am häufigsten?"),
        ("option_id", "x"), ("option_code", "0"), ("option_label", "keines davon")]))
    client.post("/surveys/1/editor/items/5", data=MultiDict(common + [("code", "abends"), ("question", "Welche abends?")]))
    client.post("/surveys/1/editor/items/6", data=MultiDict(common + [("code", "rang"), ("question", "Reihenfolge?")]))
    client.post("/surveys/1/editor/items/7", data=MultiDict(common + [("code", "zeit"), ("question", "Zeit?"), ("total", "100")]))
    client.post("/surveys/1/editor/items/8", data=MultiDict(common + [
        ("code", "zuf"), ("question", "Wie zufrieden?"), ("points", "5"), ("anchors", "satisfied")]))


def start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    return p, uid


def test_the_later_question_holds_the_options_of_the_earlier_one(app, client, survey):
    build(app, client)
    with app.app_context():
        single = db.session.get(Item, 4)
        assert single.config["carry"] == {"item": 2, "which": "chosen"}
        assert [(o["id"], o["code"]) for o in single.config["options"]] == [
            ("cf_a", "1"), ("cf_b", "2"), ("cf_c", "3"), ("cf_d", "9"), ("x", "0")]          # carried first, then the own one
        assert single.config["options"][3]["src"] == {"item": 2, "code": "9", "which": "chosen", "text": True}
        assert single.text("options")["cf_b"] == "Tablet" and single.kind.validate(single) == []
        likert = db.session.get(Item, 8)
        assert [s["code"] for s in likert.config["statements"]] == ["zuf_1", "zuf_2", "zuf_3", "zuf_9"]
        assert likert.text("statements")["cf_a"] == "Smartphone" and likert.kind.validate(likert) == []
        assert [c for c, _ in db.session.get(Item, 6).kind.export_columns(db.session.get(Item, 6))] == ["rang_1", "rang_2", "rang_3", "rang_9"]
        assert db.session.get(Item, 5).kind.missing_codes(db.session.get(Item, 5), "abends_2") == (-77,)
    html = client.get("/surveys/1/editor/items/4").data.decode()
    assert "Antwortoptionen aus einer früheren Frage übernehmen" in html and "geraete · Welche Geräte nutzen Sie?" in html
    assert 'name="option_id" value="cf_a"' not in html and 'name="option_id" value="x"' in html  # carried rows are no form rows
    # the wording follows the earlier question; an option removed there goes here
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "geraete"), ("question", "Welche Geräte nutzen Sie?"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Handy"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Laptop")]))
    with app.app_context():
        single = db.session.get(Item, 4)
        assert [o["id"] for o in single.config["options"]] == ["cf_a", "cf_c", "x"]
        assert single.text("options") == {"cf_a": "Handy", "cf_c": "Laptop", "x": "keines davon"}
    # "nichts übernehmen": the carried rows go, the own one stays
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "meist"), ("question", "Welches am häufigsten?"), ("carry_item", ""),
        ("option_id", "x"), ("option_code", "0"), ("option_label", "keines davon")]))
    with app.app_context():
        single = db.session.get(Item, 4)
        assert "carry" not in single.config and [o["id"] for o in single.config["options"]] == ["x"]


def test_people_see_what_they_chose(app, client, survey):
    build(app, client)
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data=MultiDict([("nav", "next"), ("i2", "1"), ("i2", "2"), ("i2", "9"), ("i2_t_d", "E-Reader {{{pid}}}")]))
    html = p.get(f"/s/{uid}").data.decode()
    for field in ('name="i4" value="1"', 'name="i4" value="2"', 'name="i4" value="9"', 'name="i4" value="0"',
                  'name="i5" value="2"', 'name="i8_cf_a"', 'name="i8_cf_d"', 'name="i7_1"'):
        assert field in html, field
    for field in ('name="i4" value="3"', 'name="i5" value="3"', 'name="i8_cf_c"', 'name="i7_3"', 'data-code="3"'):
        assert field not in html, field
    assert "E-Reader { { {pid}}}" in html                             # what somebody typed is the wording, never a placeholder
    r = p.post(f"/s/{uid}", data=MultiDict([
        ("nav", "next"), ("i4", "3")]))                               # an option that was not shown is no answer
    assert "Ungültige Antwort" in r.data.decode()
    p.post(f"/s/{uid}", data=MultiDict([
        ("nav", "next"), ("i4", "2"), ("i5", "1"), ("i5", "3"),
        ("i6", "9"), ("i6", "1"), ("i6", "2"), ("i6_touched", "1"),
        ("i7_1", "50"), ("i7_2", "30"), ("i7_9", "20"), ("i7_touched", "1"),
        ("i8_cf_a", "4"), ("i8_cf_b", "2"), ("i8_cf_d", "5"), ("i8_cf_c", "1")]))
    with app.app_context():
        from app import export

        response = db.session.scalars(db.select(Response).filter_by(is_preview=False)).one()
        assert response.status == Response.COMPLETED
        assert response.answers["5"] == {"v": ["1"], "t": {}, "h": ["3"]}
        assert response.answers["6"] == ["9", "1", "2"] and response.answers["7"] == {"1": 50, "2": 30, "9": 20}
        assert response.answers["8"] == {"_hidden": ["cf_c"], "cf_a": 4, "cf_b": 2, "cf_d": 5}
        row = export.rows(db.session.get(Survey, 1))[0]
        assert row["meist"] == "2"
        assert (row["abends_1"], row["abends_2"], row["abends_3"], row["abends_9"]) == (1, 0, -77, 0)
        assert (row["rang_9"], row["rang_1"], row["rang_2"], row["rang_3"]) == (1, 2, 3, -77)
        assert (row["zeit_1"], row["zeit_3"]) == (50, -77) and (row["zuf_1"], row["zuf_3"]) == (4, -77)
        book = {r[0]: r for r in export.codebook(db.session.get(Survey, 1))}
        assert "-77 = nicht gezeigt" in book["abends_3"][3]


def test_too_few_options_left(app, client, survey):
    build(app, client)
    with app.app_context():
        single, multi = db.session.get(Item, 4), db.session.get(Item, 5)
        rank, likert = db.session.get(Item, 6), db.session.get(Item, 8)
        one, none, never = {2: ["1"]}, {2: []}, {}
        assert single.kind.applies(single, one) and not single.kind.applies(single, none)   # with "keines davon": two left
        assert multi.kind.applies(multi, one) and not multi.kind.applies(multi, none) and not multi.kind.applies(multi, never)
        assert not rank.kind.applies(rank, one) and rank.kind.applies(rank, {2: ["1", "3"]})  # one element: nothing to rank
        assert likert.kind.applies(likert, one) and not likert.kind.applies(likert, none)
        assert [o["code"] for o in multi.kind.visible(multi, {2: {"v": ["9"], "t": {"9": "x"}}})] == ["9"]
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data=MultiDict([("nav", "next")]), follow_redirects=True)           # nothing ticked
    with app.app_context():
        response = db.session.scalars(db.select(Response).filter_by(is_preview=False)).one()
        assert response.status == Response.COMPLETED and set(response.answers) == {"1", "2"}  # page 2 never came


def test_unchosen_and_all(app, client, survey):
    build(app, client, which="unchosen")
    with app.app_context():
        multi = db.session.get(Item, 5)
        assert [o["code"] for o in multi.kind.visible(multi, {2: ["1"]})] == ["2", "3", "9"]
        assert [o["code"] for o in multi.kind.visible(multi, {2: []})] == ["1", "2", "3", "9"]  # seen, nothing ticked
        assert multi.kind.visible(multi, {}) == []                                              # never asked
        multi.config = {**multi.config, "carry": {"item": 2, "which": "all"}}
        carry.sync(multi)
        assert len(multi.kind.visible(multi, {})) == 4 and multi.kind.missing_codes(multi, "abends_1") == ()
        db.session.rollback()


def test_design_check(app, client, survey):
    build(app, client)
    with app.app_context():
        single = db.session.get(Item, 4)
        single.config = {**single.config, "options": single.config["options"] + [{"id": "y", "code": "1"}]}
        assert any("gehört schon zu einer übernommenen Option" in p for p in single.kind.validate(single))
        single.config = {**single.config, "carry": {"item": 8, "which": "chosen"}}
        assert any("gibt es nicht mehr oder sie ist keine Auswahlfrage" in p for p in single.kind.validate(single))
        single.config = {**single.config, "carry": {"item": 5, "which": "chosen"}}   # on the same page
        assert any("früheren Seite" in p for p in single.kind.validate(single))
        db.session.rollback()


def test_results_dummies_copies_and_survey_file(app, client, survey):
    build(app, client)
    with app.app_context():
        from app import dummy, duplicate, results, transfer

        s = db.session.get(Survey, 1)
        dummy.generate(s, 15, seed=7)
        db.session.commit()
        for r in db.session.scalars(db.select(Response).filter_by(source="dummy")):
            devices = set(carry_codes(r.answers.get("2")))
            if "5" in r.answers and r.answers["5"] is not None:
                assert set(carry_codes(r.answers["5"])) <= devices
            if isinstance(r.answers.get("7"), dict):
                assert set(r.answers["7"]) == devices and sum(r.answers["7"].values()) == 100
            if isinstance(r.answers.get("6"), list):
                assert set(r.answers["6"]) == devices and len(devices) >= 2
        data = results.compute(s, sources=("dummy",))
        rank = next(e for e in data["items"] if e["item"].id == 6)
        assert all(str(r["value"]) != "-77" for c in rank["columns"] for r in c.get("rows", []))   # -77 is in no statistic
        item = db.session.get(Item, 8)
        item.config = {**item.config, "level": "metric"}
        likert = next(e for e in results.compute(s, sources=("dummy",))["items"] if e["item"].id == 8)
        assert likert["scale"] is None and not likert.get("subscales") and not likert.get("scale_hidden")  # a battery, no scale
        db.session.rollback()
        new, _ = duplicate.create(s, db.session.get(User, 1), "Welle 2")
        twin = {i.code: i for b in new.blocks for i in b.items}
        assert twin["meist"].config["carry"]["item"] == twin["geraete"].id != 2
        assert twin["zuf"].config["statements"][0]["src"]["item"] == twin["geraete"].id
        alone = duplicate.copy_item(db.session.get(Item, 5), new)              # without the question it takes from
        assert "carry" not in alone.config and alone.config["options"] == []
        text = json.dumps(transfer.dump(s)).encode()
    with app.test_request_context():
        other, _ = transfer.create(transfer.loads(text), db.session.get(User, 1), "Dritte")
        twin = {i.code: i for b in other.blocks for i in b.items}
        assert twin["rang"].config["carry"] == {"item": twin["geraete"].id, "which": "chosen"}
        assert twin["rang"].config["options"][0]["src"]["item"] == twin["geraete"].id


def carry_codes(answer):
    from app.items.choice import selected

    return selected(answer) or []
