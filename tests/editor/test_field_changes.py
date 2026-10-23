"""Changes to a survey while it collects data (data audit 2026-10-05, package E): what participations under way
keep, what the editor refuses once real answers exist, and how the data set says what happened."""
import csv
import io
from datetime import datetime

from app import balance, flow, participate, runtime, translations
from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions, fill_table
from tests.analysis.test_vignettes import FORM, _add, _node

E = "/surveys/1/editor"


def _rows(client, everyone=True):
    url = "/surveys/1/export/data.csv" + ("?all=1" if everyone else "")
    return list(csv.DictReader(io.StringIO(client.get(url).data.decode("utf-8-sig"))))


# --- T-4: translating a vignette loop in the field -------------------------------------------------------------


def test_translation_in_the_field_keeps_the_layout(app, client, survey):
    """T-4: a translated level of a vignette loop changed the layout signature; every participation under way
    started again on page 1 with a new deck."""
    fill_consent(client)
    add(client, "rating")
    _add(client)
    node = _node(app)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data=FORM)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data={"action": "generate"})
    node = _node(app)
    fill_questions(app)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data={**CONSENT_OK, "pos": "r0"})
    p.get(f"/s/{uid}")
    with app.app_context():
        resp = db.session.scalar(db.select(Response))
        before = (resp.position, resp.layout["loops"], resp.qversion)
        assert before[0] > 0
    with app.app_context():  # the researcher translates a level into English while the survey runs
        s = db.session.get(Survey, 1)
        unit = next(u for u in translations.units(s, "en") if u.key == f"loop:{node['id']}")
        unit.set("en", {"l": {"l11": "humanoid robot"}})
        db.session.commit()
        assert runtime.layout_matches(s, db.session.scalar(db.select(Response)).layout)
    page = p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        resp = db.session.scalar(db.select(Response))
        assert (resp.position, resp.layout["loops"], resp.qversion) == before
        assert not (resp.paradata or {}).get("_relayout")
    assert "wurde inzwischen geändert" not in page


def test_layouts_drawn_under_the_old_signature_stay_valid(app, client, survey):
    """T-4: layouts stored before the fix carry the signature with the labels of the vignette loop; they keep
    their page and deck and get today's signature."""
    add(client, "rating")
    _add(client)
    node = _node(app)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data=FORM)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data={"action": "generate"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        old = runtime._signature_vignette_texts(s)
        assert old != runtime._signature(s)
        r = Response(survey_id=1, source="real", pid="x1", position=2, answers={},
                     layout={**runtime.build_layout(s, __import__("random").Random(3)), "sig": old})
        db.session.add(r)
        db.session.commit()
        drawn = r.layout["loops"]
        runtime.ensure_layout(s, r)
        assert r.position == 2 and r.layout["loops"] == drawn and r.layout["sig"] == runtime._signature(s)


# --- T-5: vignette decks on the first page ----------------------------------------------------------------------


def test_first_page_keeps_the_draw_with_tied_decks(app, client, survey):
    """T-5: with three or more decks tied, the first page (all counts) and its first "Next" (only the counts of the
    picked deck, balance.keep) drew from other random numbers: the same deck, but every later draw differed."""
    add(client, "rating")
    _add(client)
    node = _node(app)
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data={**FORM, "per_person": "3", "decks": "4"})
    client.post(f"/surveys/1/flow/vignettes/{node['id']}", data={"action": "generate"})
    node = _node(app)
    decks = node["plan"]["decks_rows"]
    assert len(decks) == 4
    with app.app_context():  # one earlier real participation saw deck 1: three decks are tied
        db.session.add(Response(survey_id=1, source="real", pid="old", layout={"loops": {node["id"]: list(decks[0])}}))
        db.session.commit()
    with app.test_request_context("/s/x"):
        s = db.session.get(Survey, 1)
        for seed in range(40):
            pending = {"seed": seed, "pid": f"p{seed}", "at": "2026-10-05T10:00:00", "shown": "2026-10-05T10:00:00",
                       "q": s.qversion}
            first = participate._new_response(s, "de", pending)   # the link opened: all counts
            again = participate._new_response(s, "de", pending)   # the first "Next": the counts kept in the session
            assert first.layout["loops"] == again.layout["loops"]
            assert first.layout["stmt"] == again.layout["stmt"]
            assert first.layout["loops"][node["id"]][0] not in decks[0]  # never the deck shown before


# --- T-7 and T-33: participations under way while the survey is edited again -------------------------------------


def _two_pages(client):
    fill_consent(client)
    add(client, "text_input")                                       # 2
    client.post(E + "/items/2", data={"code": "q1", "question": "Frage 1"})
    add(client, "page_break")                                       # 3
    add(client, "text_input")                                       # 4
    client.post(E + "/items/4", data={"code": "q2", "question": "Frage 2"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})


def test_participation_waits_while_the_survey_is_a_draft_again(app, client, survey):
    """T-7: after "Zur Bearbeitung öffnen" a participation under way went on in the draft, on the changed
    questionnaire, with a new condition; its old and new answers mixed. Now it waits with its answers kept and goes
    on after the restart, and says that it starts again (T-33)."""
    _two_pages(client)
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=CONSENT_OK)
    p.post("/s/1", data={"nav": "next", "i2": "alt", "pos": "r1"})
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        before = (r.position, r.qversion, dict(r.answers), r.layout)
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    add(client, "text_input")                                       # 5: a new question in block 2
    client.post(E + "/items/5", data={"code": "q3", "question": "Frage 3"})
    page = p.get("/s/1").data.decode()
    assert "gerade nicht geöffnet" in page and "bleiben gespeichert" in page
    p.post("/s/1", data={"nav": "next", "i4": "neu", "pos": "r2"})   # a page sent from the open tab
    p.post("/s/1", data=CONSENT_OK | {"pos": "r0"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        r = db.session.scalar(db.select(Response))
        assert s.status == Survey.DRAFT
        assert (r.position, r.qversion, dict(r.answers), r.layout) == before   # nothing of the draft reached it
        assert r.status == Response.ACTIVE
    # the researchers may still try the draft out
    assert client.get("/surveys/1/editor/preview").status_code in (200, 302)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    page = p.get("/s/1", follow_redirects=True).data.decode()
    assert "wurde inzwischen geändert" in page                       # T-33: said on the page
    with app.app_context():
        # the participation, not the researchers' preview run (no ORDER BY: PostgreSQL may give that one first)
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.position == 0 and r.qversion == 2 and r.paradata["_relayout"] == 1
        assert r.answers["2"] == "alt"
    assert "wurde inzwischen geändert" not in p.get("/s/1").data.decode()  # once
    page = p.post("/s/1", data=CONSENT_OK | {"pos": "r0"}, follow_redirects=True).data.decode()
    assert 'value="alt"' in page                                     # the answer given before is filled in
    row = _rows(client)[0]
    assert row["relayout"] == "1" and row["qversion"] == "2"
    codebook = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "relayout" in codebook


def test_relayout_after_a_post_is_said_on_the_next_page(app, client, survey):
    """T-33: the new layout comes with a page sent from the old one: the redirect leads to the first page, which says
    why (not "this page had already been sent")."""
    _two_pages(client)
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=CONSENT_OK)
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    add(client, "text_input")
    client.post(E + "/items/5", data={"code": "q3", "question": "Frage 3"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    p.post("/s/1", data={"nav": "next", "i2": "x", "pos": "r1"})
    page = p.get("/s/1", follow_redirects=True).data.decode()
    assert "wurde inzwischen geändert" in page and "schon abgeschickt" not in page


def test_no_relayout_column_before_the_survey_was_edited_again(app, client, survey):
    _two_pages(client)
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=CONSENT_OK)
    assert "relayout" not in _rows(client)[0]


# --- T-10: a randomizer behind a branch ---------------------------------------------------------------------------


def test_randomizer_in_a_branch_counts_only_who_reached_it(app, client, survey):
    """T-10: a randomizer behind a branch drew for everybody; its column named a condition for people who never saw
    it, and the balance evened out over all of them instead of over those who reached it."""
    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post(E + "/items/2", data={"code": "age", "validation": "integer", "question": "Alter"})
    client.post(E + "/blocks"), client.post(E + "/blocks"), client.post(E + "/blocks")  # 3, 4, 5
    for n in (3, 4, 5):
        add(client, "text_input", block=n)
        client.post(E + f"/items/{n}", data={"code": f"q{n}", "question": f"Frage {n}"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "lt", "value": "18"}, "children": [
                {"t": "rand", "id": "r1", "code": "cond1", "name": "Exp", "k": 1, "balanced": True, "shuffle": False,
                 "children": [{"t": "block", "block": 3}, {"t": "block", "block": 4}]}]},
            {"t": "block", "block": 5}]}
        flow.normalize(s)
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    app.config["BALANCE_SECONDS"] = 0
    for age in ("30", "40", "50", "16", "15"):
        p = app.test_client()
        p.get("/s/1")
        p.post("/s/1", data=CONSENT_OK)
        p.post("/s/1", data={"nav": "next", "i2": age})
        for _ in range(3):
            if "Vielen Dank" in p.get("/s/1").data.decode():
                break
            p.post("/s/1", data={"nav": "next", "i3": "x", "i4": "y", "i5": "z"})
    rows = _rows(client)
    assert [r["status"] for r in rows] == ["completed"] * 5
    by_age = {r["age"]: r for r in rows}
    assert all(by_age[a]["cond1"] == "" for a in ("30", "40", "50"))       # never reached: empty
    minors = sorted(by_age[a]["cond1"] for a in ("16", "15"))
    assert all(minors) and minors[0] != minors[1]                          # balanced among those who reached it
    with app.app_context():
        assert sorted(balance.counts(db.session.get(Survey, 1))["rand"]["r1"].values()) == [1, 1]
    codebook = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "leer bei nicht erreichtem Zweig" in codebook


# --- T-20: a loop row with real answers ---------------------------------------------------------------------------


def _loop_with_answers(app, client):
    add(client, "rating")
    r = client.post("/surveys/1/editor/blocks/2/loop")
    nid = r.headers["Location"].split("/loop/")[1].split("?")[0]
    fill_table(client, f"/surveys/1/flow/loop/{nid}", robot=["Pepper", "Nao", "Kiste"])
    with app.app_context():
        s = db.session.get(Survey, 1)
        node = next(n for n, _, _ in flow.walk(flow.tree(s)) if n["t"] == "loop")
        rows = [r["id"] for r in node["rows"]]
        item = db.session.scalar(db.select(Item).filter_by(block_id=2, type="rating"))
        s.qversion = 2  # a survey that ran, opened for editing again ("Zur Bearbeitung öffnen")
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, pid="old", qversion=1,
                                answers={f"{item.id}#{rid}": n + 1 for n, rid in enumerate(rows)},
                                layout={"loops": {node["id"]: rows}}, completed_at=datetime(2026, 10, 4)))
        db.session.commit()
        return nid, node["code"], rows, item.code


def test_a_loop_row_real_participants_saw_stays(app, client, survey):
    """T-20: a row deleted after "Zur Bearbeitung öffnen" took its answers out of the data set, and the order column
    said "None". Now the row stays (with a note); new rows may still be added and texts changed."""
    nid, code, rows, item_code = _loop_with_answers(app, client)
    data = {"col_id": ["c0"], "col_name": ["robot"], "row_id": [rows[0], rows[2], ""],
            f"cell_{rows[0]}_c0": "Pepper 2", f"cell_{rows[2]}_c0": "Kiste", "cell__c0": "Roomba"}
    page = client.post(f"/surveys/1/flow/loop/{nid}", data=data, follow_redirects=True).data.decode()
    assert "Zeile 2 bleibt in der Tabelle" in page
    with app.app_context():
        node = flow.find(flow.tree(db.session.get(Survey, 1)), nid)[0]
        assert [r["id"] for r in node["rows"]][:3] == rows and len(node["rows"]) == 4    # kept in place, one added
        assert node["rows"][0]["values"]["c0"]["de"] == "Pepper 2"
    row = _rows(client)[0]
    assert row[f"{code}_order"] == "1|2|3"
    assert [row[f"{item_code}_{n}"] for n in (1, 2, 3)] == ["1", "2", "3"]


def test_order_column_never_says_none(app, client, survey):
    """T-20: a row removed before rows were kept (or by any other way) is "?" in the order column, never "None"."""
    nid, code, rows, _ = _loop_with_answers(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        root = __import__("copy").deepcopy(flow.tree(s))
        node = flow.find(root, nid)[0]
        node["rows"] = [r for r in node["rows"] if r["id"] != rows[1]]
        s.flow = root
        db.session.commit()
    assert _rows(client)[0][f"{code}_order"] == "1|?|2"


# --- T-32: what real answers mean stays when the survey is edited again -------------------------------------------


def _likert(client, points="5", code="L", s1="s1c", reverse=(), extra=(), text="A"):
    from werkzeug.datastructures import MultiDict

    data = [("code", code), ("question", "Wie sehr?"), ("points", points), ("labeling", "full"),
            ("stmt_id", "s1"), ("stmt_code", s1), ("stmt_text", text), ("stmt_id", "s2"), ("stmt_code", "s2c"),
            ("stmt_text", "B"), *[("stmt_reverse", r) for r in reverse], *extra]
    return client.post(E + "/items/2", data=MultiDict(data), follow_redirects=True).data.decode()


def _reopened_with_answers(app, item_id, answer, source="real"):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.qversion = 2  # ran, then "Zur Bearbeitung öffnen"
        db.session.add(Response(survey_id=1, source=source, is_preview=source == "test", status=Response.COMPLETED,
                                pid=f"p{source}", qversion=1, answers={str(item_id): answer}, layout={}))
        db.session.commit()


def test_scale_codes_and_reversal_stay_with_real_answers(app, client, survey):
    """T-32: after "Zur Bearbeitung öffnen", 5 to 7 points reversed the old answers wrongly, a new code changed their
    meaning. The editor keeps them now and says why; texts and new statements may still change."""
    add(client, "likert")
    _likert(client)
    _reopened_with_answers(app, 2, {"s1": 5, "s2": 1})
    page = _likert(client, points="7", text="A neu")
    assert "Änderung wurde nicht gespeichert" in page and "Stufenzahl" in page
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["points"] == 5 and item.text("statements")["s1"] == "A neu"
        assert len(item.t("de")["labels"]) == 5
    assert "Änderung wurde nicht gespeichert" in _likert(client, s1="neu")
    assert "Änderung wurde nicht gespeichert" in _likert(client, reverse=("s1",))
    page = _likert(client, extra=[("stmt_id", ""), ("stmt_code", "s3c"), ("stmt_text", "C")])  # one more statement
    assert "Änderung wurde nicht gespeichert" not in page
    with app.app_context():
        item = db.session.get(Item, 2)
        assert [(s["code"], s["reverse"]) for s in item.config["statements"]] == [("s1c", False), ("s2c", False),
                                                                                  ("s3c", False)]
    page = _likert(client, code="L2")
    assert "Variablenname bleibt" in page
    with app.app_context():
        assert db.session.get(Item, 2).code == "L"
    assert "Diese Frage hat schon echte Antworten" in client.get(E + "/items/2").data.decode()  # said at the top


def test_option_codes_stay_but_options_can_be_added(app, client, survey):
    from werkzeug.datastructures import MultiDict

    add(client, "single_choice")
    opts = lambda *o: [x for oid, code, label in o for x in (("option_id", oid), ("option_code", code), ("option_label", label))]
    client.post(E + "/items/2", data=MultiDict([("code", "f"), ("question", "Q"), *opts(("o1", "1", "ja"), ("o2", "2", "nein"))]))
    with app.app_context():
        ids = [o["id"] for o in db.session.get(Item, 2).config["options"]]
    _reopened_with_answers(app, 2, "1")
    swapped = client.post(E + "/items/2", data=MultiDict([("code", "f"), ("question", "Q"), *opts(
        (ids[0], "2", "ja"), (ids[1], "1", "nein"))]), follow_redirects=True).data.decode()
    removed = client.post(E + "/items/2", data=MultiDict([("code", "f"), ("question", "Q"), *opts(
        (ids[0], "1", "ja"))]), follow_redirects=True).data.decode()
    assert "Änderung wurde nicht gespeichert" in swapped and "Änderung wurde nicht gespeichert" in removed
    client.post(E + "/items/2", data=MultiDict([("code", "f"), ("question", "Q"), *opts(
        (ids[0], "1", "ja"), (ids[1], "2", "nein"), ("", "3", "vielleicht"))]))
    with app.app_context():
        assert [o["code"] for o in db.session.get(Item, 2).config["options"]] == ["1", "2", "3"]


def test_test_runs_do_not_fix_the_structure(app, client, survey):
    add(client, "likert")
    _likert(client)
    _reopened_with_answers(app, 2, {"s1": 5}, source="test")
    assert "Änderung wurde nicht gespeichert" not in _likert(client, points="7")
    with app.app_context():
        assert db.session.get(Item, 2).config["points"] == 7


def test_formula_with_real_inputs_is_refused_when_reopened(app, client, survey):
    """A reopened questionnaire retains the calculation used by earlier real answers."""
    add(client, "text_input")                                                       # 2
    client.post(E + "/items/2", data={"code": "monat", "validation": "integer", "question": "Monat"})
    add(client, "calc")                                                             # 3
    form = {"code": "jahr", "question": "Jahr", "calc_a": "2", "calc_op": "mul", "calc_fixed": "12", "calc_places": "0"}
    client.post(E + "/items/3", data=form)
    with app.app_context():
        assert db.session.get(Item, 3).config["fixed"] == 12
        s = db.session.get(Survey, 1)
        s.qversion = 2
        for n, value in enumerate((10, 20, None)):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, pid=f"c{n}", layout={},
                                    answers={"2": value} if value is not None else {}))
        db.session.commit()
    page = client.post(E + "/items/3", data={**form, "calc_fixed": "6"}, follow_redirects=True).data.decode()
    assert "Änderung wurde nicht gespeichert" in page and "Rechenregel und ihre Quellen" in page
    with app.app_context():
        assert db.session.get(Item, 3).config["fixed"] == 12
