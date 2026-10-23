"""Statements of a Likert matrix and pairs of a semantic differential that only some people see (app/rows.py)."""
import json

from werkzeug.datastructures import MultiDict

from app import design, rows
from app.extensions import db
from app.models import Item, Response, Survey, User
from tests.conftest import CONSENT_OK, add, fill_consent


def build(app, client, required=True):
    """Page 1: devices (multiple choice: phone, tablet, laptop); page 2: one Likert statement per device and
    one for everybody, and a semantic differential with a pair for phone users."""
    fill_consent(client)
    for kind in ("multiple_choice", "page_break", "likert", "semantic"):
        add(client, kind)                                           # 2, 3, 4, 5
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "geraete"), ("question", "Welche Geräte nutzen Sie?"), ("answer_mode", "optional"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Smartphone"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Tablet"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Laptop")]))
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "zuf"), ("question", "Wie zufrieden sind Sie?"), ("answer_mode", "required" if required else "optional"),
        ("points", "5"), ("anchors", "satisfied"), ("labeling", "full"),
        ("stmt_id", "s1"), ("stmt_code", "zuf_phone"), ("stmt_text", "Smartphone"),
        ("stmt_id", "s2"), ("stmt_code", "zuf_tablet"), ("stmt_text", "Tablet"),
        ("stmt_id", "s3"), ("stmt_code", "zuf_all"), ("stmt_text", "Internet insgesamt"),
        ("when_s1_item", "2"), ("when_s1_mode", "is"), ("when_s1_codes", "1"),
        ("when_s2_item", "2"), ("when_s2_mode", "is"), ("when_s2_codes", "2"),
        ("when_s3_item", "")]))
    client.post("/surveys/1/editor/items/5", data=MultiDict([
        ("code", "sd"), ("question", "Ihr Smartphone ist …"), ("answer_mode", "optional"), ("points", "7"),
        ("pair_id", "p1"), ("pair_code", ""), ("pair_left", "langsam"), ("pair_right", "schnell"),
        ("when_p1_item", "2"), ("when_p1_mode", "is"), ("when_p1_codes", "1")]))


def start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    return p, uid


def test_conditions_are_stored_and_shown_in_the_editor(app, client, survey):
    build(app, client)
    with app.app_context():
        item = db.session.get(Item, 4)
        stmts = {s["id"]: s for s in item.config["statements"]}
        assert stmts["s1"]["when"] == {"item": 2, "op": "in", "value": "1"} and "when" not in stmts["s3"]
        assert rows.text(item, stmts["s1"]) == "geraete: Smartphone"
        assert item.kind.validate(item) == [] and item.kind.missing_codes(item, "zuf_phone") == (-77,)
        assert item.kind.missing_codes(item, "zuf_all") == ()
        assert "nur gezeigt, wenn geraete: Smartphone" in dict(item.kind.export_columns(item))["zuf_phone"]
        assert db.session.get(Item, 5).config["pairs"][0]["when"]["item"] == 2
    html = client.get("/surveys/1/editor/items/4").data.decode()
    assert "Aussagen nur manchen zeigen" in html and "2 von 3 mit Bedingung" in html and 'name="when_s1_codes" value="1" checked' in html
    # a form that does not send the fields of a condition (a script, an older page) leaves it as it is
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "zuf"), ("question", "Wie zufrieden?"), ("points", "5"), ("anchors", "satisfied"),
        ("stmt_id", "s1"), ("stmt_code", "zuf_phone"), ("stmt_text", "Smartphone"),
        ("stmt_id", "s2"), ("stmt_code", "zuf_tablet"), ("stmt_text", "Tablet"), ("when_s2_item", ""),
        ("stmt_id", "s3"), ("stmt_code", "zuf_all"), ("stmt_text", "Internet insgesamt")]))
    with app.app_context():
        stmts = {s["id"]: s for s in db.session.get(Item, 4).config["statements"]}
        assert stmts["s1"]["when"]["value"] == "1" and "when" not in stmts["s2"]  # "immer zeigen" removes it


def test_only_the_rows_that_apply_are_shown_required_and_exported(app, client, survey):
    build(app, client)
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data=MultiDict([("nav", "next"), ("i2", "1")]))           # a phone, no tablet
    html = p.get(f"/s/{uid}").data.decode()
    assert 'name="i4_s1"' in html and 'name="i4_s2"' not in html and 'name="i4_s3"' in html and 'name="i5_p1"' in html
    # required: the two statements shown, not the hidden one
    assert "Bitte beantworten Sie alle Aussagen" in p.post(f"/s/{uid}", data={"nav": "next", "i4_s1": "4"}).data.decode()
    p.post(f"/s/{uid}", data={"nav": "next", "i4_s1": "4", "i4_s2": "5", "i4_s3": "2"})   # a value for the hidden row is not taken
    with app.app_context():
        from app import export, quality

        response = db.session.scalars(db.select(Response).filter_by(is_preview=False)).one()
        assert response.status == Response.COMPLETED
        assert response.answers["4"] == {"_hidden": ["s2"], "s1": 4, "s3": 2}
        s = db.session.get(Survey, 1)
        row = export.rows(s)[0]
        assert (row["zuf_phone"], row["zuf_tablet"], row["zuf_all"]) == (4, -77, 2)
        assert row["sd_1"] in (None, "")                           # shown and left out: empty, not -77
        assert row["qc_missing"] == 0.5     # the pair left out; the Likert is complete; the required question is no part of it
        book = {r[0]: r for r in export.codebook(s)}
        assert "-77 = nicht gezeigt" in book["zuf_tablet"][3] and "-77" not in book["zuf_all"][3]
        assert quality.sequence(db.session.get(Item, 4), response.answers["4"]) == [4, 2]


def test_a_question_without_rows_left_is_skipped(app, client, survey):
    build(app, client)
    with app.app_context():                                         # without the statement for everybody
        item = db.session.get(Item, 4)
        item.config = {**item.config, "statements": item.config["statements"][:2]}
        db.session.commit()
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data=MultiDict([("nav", "next"), ("i2", "3")]), follow_redirects=True)  # only a laptop
    with app.app_context():
        from app import export

        response = db.session.scalars(db.select(Response).filter_by(is_preview=False)).one()
        assert response.status == Response.COMPLETED and set(response.answers) == {"1", "2"}   # page 2 never came
        row = export.rows(db.session.get(Survey, 1))[0]
        assert row["zuf_phone"] in (None, "") and row["qc_missing"] == 0


def test_going_back_and_changing_the_filter(app, client, survey):
    build(app, client, required=False)
    with app.app_context():
        db.session.get(Survey, 1).allow_back = True
        db.session.commit()
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data=MultiDict([("nav", "next"), ("i2", "1")]))
    p.post(f"/s/{uid}", data={"nav": "back", "i4_s1": "4", "i4_s3": "3"})
    p.post(f"/s/{uid}", data=MultiDict([("nav", "next"), ("i2", "2")]))           # now a tablet, no phone
    html = p.get(f"/s/{uid}").data.decode()
    assert 'name="i4_s1"' not in html and 'name="i4_s2"' in html and 'name="i5_p1"' not in html
    p.post(f"/s/{uid}", data={"nav": "next", "i4_s2": "1", "i4_s3": "3"})
    with app.app_context():
        response = db.session.scalars(db.select(Response).filter_by(is_preview=False)).one()
        assert response.answers["4"] == {"_hidden": ["s1"], "s2": 1, "s3": 3} and "5" not in response.answers


def test_url_parameter_and_flow_condition_on_the_matrix(app, client, survey):
    build(app, client)
    with app.app_context():
        from app import flow

        item = db.session.get(Item, 4)
        s = item.survey
        assert rows.hidden(item.config["statements"], {2: ["2"]}, s) == {"s1"}
        when = {"item": "p:src", "op": "eq", "value": "panel"}
        assert rows.hidden([{"id": "x", "when": when}], {"p:src": "panel"}, s) == set()
        assert rows.hidden([{"id": "x", "when": when}], {}, s) == {"x"} and rows.hidden([{"id": "x", "when": when}], None, s) == set()
        # "beantwortet" on a matrix none of whose shown rows was answered: the note of hidden rows is no answer
        assert not flow.holds({"item": 4, "op": "answered", "value": ""}, {4: {"_hidden": ["s2"]}})
        assert item.kind.is_missing(item, {"_hidden": ["s2"], "s1": 1, "s3": 2}) is False
        assert item.kind.is_missing(item, {"_hidden": ["s2"], "s1": 1}) is True


def test_design_check(app, client, survey):
    build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 4)
        assert not [f for f in design.check(s) if f.item_id == 4]
        stmts = [dict(x) for x in item.config["statements"]]
        stmts[0]["when"] = {"item": 2, "op": "eq", "value": ""}       # nothing ticked
        stmts[1]["when"] = {"item": 5, "op": "answered", "value": ""}   # a question on the same page
        item.config = {**item.config, "statements": stmts, "level": "metric", "construct": {"key": "x", "unlocked": True}}
        found = [f.message for f in design.check(s) if f.item_id == 4]
        assert any("zuf_phone" in m and "unvollständig" in m for m in found)
        assert any("zuf_tablet" in m and "früheren Seite" in m for m in found)
        assert any("nicht validiert" in m for m in found) and any("weniger als zwei Aussagen" in m for m in found)
        db.session.rollback()


def test_scale_mean_over_the_rows_shown_and_alpha_over_complete_cases(app, client, survey):
    build(app, client)
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 4)
        item.config = {**item.config, "level": "metric"}
        data = [({"s1": 5, "s2": 4, "s3": 5}, ["1", "2"]), ({"s1": 2, "s2": 1, "s3": 2}, ["1", "2"]),
                ({"s1": 3, "s2": 3, "s3": 4}, ["1", "2"]), ({"s1": 4, "s2": 4, "s3": 3}, ["1", "2"]),
                ({"_hidden": ["s2"], "s1": 1, "s3": 3}, ["1"]), ({"_hidden": ["s1", "s2"], "s3": 5}, ["3"])]
        for answer, devices in data:
            db.session.add(Response(survey_id=1, status=Response.COMPLETED, source="real", language="de",
                                    answers={"2": devices, "4": answer}, qversion=s.qversion))
        db.session.commit()
        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 4)
        scale = entry["scale"]
        assert scale["n"] == 4 and scale["alpha"] is not None       # alpha: the four who saw every statement
        # the last person saw only s3: fewer than two statements shown, no score (scales.MIN_SHOWN)
        assert scale["partial"]["n"] == 5 and abs(scale["partial"]["k"] - 14 / 5) < 1e-9
        means = [14 / 3, 5 / 3, 10 / 3, 11 / 3, 2.0]
        assert abs(scale["mean"] - sum(means) / 5) < 1e-9
        cols = {c["code"]: c for c in entry["columns"]}
        assert cols["zuf_tablet"]["n"] == 4 and cols["zuf_phone"]["n"] == 5 and entry["answered"] == 6
    html = client.get("/surveys/1/results").data.decode()
    assert "Einige Aussagen sahen nicht alle" in html
    assert "Mazza, G. L." in html                                   # the source, in the one fold of the page
    assert "· nicht gezeigt: 2<" in html


def test_dummies_copies_and_survey_file(app, client, survey):
    build(app, client)
    with app.app_context():
        from app import dummy, duplicate, transfer

        s = db.session.get(Survey, 1)
        dummy.generate(s, 12, seed=4)
        db.session.commit()
        for r in db.session.scalars(db.select(Response).filter_by(source="dummy")):
            answer, devices = r.answers.get("4"), r.answers.get("2") or []
            if answer is not None:
                assert ("s1" in answer) == ("1" in devices) and ("s2" in answer) == ("2" in devices)
                assert sorted(answer.get("_hidden", [])) == sorted(sid for sid, code in (("s1", "1"), ("s2", "2")) if code not in devices)
        new, _ = duplicate.create(s, db.session.get(User, 1), "Welle 2")
        twin = {i.code: i for b in new.blocks for i in b.items}
        assert twin["zuf"].config["statements"][0]["when"]["item"] == twin["geraete"].id != 2
        assert twin["sd"].config["pairs"][0]["when"]["item"] == twin["geraete"].id
        alone = duplicate.copy_item(db.session.get(Item, 4), new)    # without the question it depends on
        assert not rows.conditional(alone.config["statements"])
        data = json.dumps(transfer.dump(s)).encode()
    with app.test_request_context():
        other, _ = transfer.create(transfer.loads(data), db.session.get(User, 1), "Dritte")
        twin = {i.code: i for b in other.blocks for i in b.items}
        assert twin["zuf"].config["statements"][1]["when"] == {"item": twin["geraete"].id, "op": "in", "value": "2"}


def test_live_feedback_leaves_a_statement_not_shown_out_of_the_mean(app, client, survey):
    """D-1/S-3 (audit 2026-10-03): answers 4 and 4, one statement not shown: the scale mean is 4.0, not
    (4 + 4 - 77) / 3 = -23.0, in the Einordnung, the Live-Vergleich, the crowd and the 2D map alike."""
    from app import scales
    from app.items import get

    build(app, client)
    with app.app_context():
        item = db.session.get(Item, 4)
        answer = {"s1": 4, "s3": 4, "_hidden": ["s2"]}
        assert item.kind.export_values(item, answer) == {"zuf_phone": 4, "zuf_tablet": -77, "zuf_all": 4}
        assert scales.answer_score(item, answer) == 4.0
        for key in ("sample_map", "score_band", "sample_crowd", "sample_compare"):
            assert get(key).value(item, answer) == 4.0, key
        # S-6: a statement shown and left out leaves the score empty; fewer than two shown: no score either
        assert get("score_band").value(item, {"s1": 4, "_hidden": ["s2"]}) is None
        assert get("score_band").value(item, {"s3": 4, "_hidden": ["s1", "s2"]}) is None


def test_scale_score_needs_every_statement_shown_answered(app, client, survey):
    """S-6: proration only for what was not shown. s1 = 4, s3 shown and skipped, s2 not shown: no score on the
    results page and in the export (before: 4.0 from a single answer)."""
    build(app, client, required=False)
    with app.app_context():
        from app import export, results, scales

        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 4)
        item.config = {**item.config, "level": "metric"}
        for answer in ({"s1": 4, "_hidden": ["s2"]}, {"s1": 2, "s3": 4, "_hidden": ["s2"]}):
            db.session.add(Response(survey_id=1, status=Response.COMPLETED, source="real", language="de",
                                    answers={"2": ["1"], "4": answer}, qversion=s.qversion))
        db.session.commit()
        scale = next(e for e in results.compute(s)["items"] if e["item"].id == 4)["scale"]
        assert scale["partial"]["n"] == 1 and scale["mean"] == 3.0
        found = scales.export_scales([item], set())[0][1]
        table = export.rows(s, False, ("real",))
        assert [scales.export_values(item, found, r, [])["zuf_m"] for r in table] == [None, 3.0]
        assert "weniger als 2 gezeigt" in scales.codebook_note(item, found[0][3])


def test_tempo_threshold_counts_only_the_rows_shown(app, client, survey):
    """Mathe-Audit Q-8: the reading time of the tempo hint counts the words of the statements the person sees; the
    statement for tablet users does not count for someone who ticked only the smartphone."""
    from app import tempo
    from app.models import Item

    build(app, client)
    with app.app_context():
        item = db.session.get(Item, 4)
        every = tempo.words(item, "de")
        phone_only = tempo.words(item, "de", answers={2: ["1"]})
        assert every - phone_only == 1                                  # "Tablet": one word less
        assert tempo.threshold([item], "de", answers={2: ["1"]}) < tempo.threshold([item], "de")
