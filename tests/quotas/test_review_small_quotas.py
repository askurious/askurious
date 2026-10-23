"""The small findings of the code review of 2026-10-02 on quotas and conditions (code-review-2026-10-02.md, K7 to K13)."""
from werkzeug.datastructures import MultiDict

from app import census, conditions, flow, loops, quotas
from app.extensions import db
from app.models import Block, Quota, QuotaCell, QuotaShare, Response, Survey
from tests.editor import test_loops
from tests.quotas import test_quota_cross
from tests.quotas import test_quota_field
from tests.conftest import CONSENT_OK, _config, add
from tests.quotas.test_quotas import E, Q, build, cells_form, counters, questionnaire, screen, set_flow, start

SQUARED = "²"  # "²": str.isdigit() says yes, int() raises


# --- K7: a share link without conditions ----------------------------------------------------------------


def _share_without_conditions(app, client):
    client.post(Q + "/link", data={"label": "ohne"})  # no "conds": names and codes only
    with app.app_context():
        share = db.session.scalars(db.select(QuotaShare).order_by(QuotaShare.id.desc())).first()
        assert not share.show_conds
        return share.token


def test_share_link_without_conditions_heads_the_matrix_with_cell_names(app, client, survey):
    test_quota_cross.quota_one(app, client)
    client.post(Q + "/1/cross", data=test_quota_cross.picked(target=5))
    set_flow(app)
    start(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert quotas.report(s)[0]["matrix"]["rows"] == ["gender: männlich", "gender: weiblich"]  # for the researchers
        entry = quotas.report(s, conds=False)[0]
        assert entry["matrix"]["rows"] == ["männlich", "weiblich"]
        assert entry["matrix"]["cols"] == ["18 bis 29", "30 bis 49", "ab 50"]
        assert entry["matrix_text"].splitlines()[0] == "Quote 1\t18 bis 29\t30 bis 49\tab 50"
        assert not any(r["conds"] for r in entry["cells"])
    token = _share_without_conditions(app, client)
    visitor = app.test_client()
    page = visitor.get(f"/q/{token}").data.decode()
    assert "Als Matrix" in page and "<th>männlich</th>" in page
    assert "gender" not in page and "age:" not in page and "Bedingungen" not in page
    body = visitor.get(f"/q/{token}/state.csv").data.decode("utf-8-sig")
    assert "männlich / 18 bis 29" in body and "gender" not in body and "age" not in body
    assert "gender: männlich" in client.get("/surveys/1/flow/quotas/").data.decode()      # the researchers' own page


def test_share_link_without_conditions_leaves_out_a_matrix_its_names_cannot_head(app, client, survey):
    questionnaire(client)
    client.post(Q + "/add")
    cells = [("A", "a", 5, [(2, "eq", "1"), (3, "between", "18..29")]), ("B", "b", 5, [(2, "eq", "1"), (3, "ge", "30")]),
             ("C", "c", 5, [(2, "eq", "2"), (3, "between", "18..29")]), ("D", "d", 5, [(2, "eq", "2"), (3, "ge", "30")])]
    assert "gespeichert" in client.post(Q + "/1", data=cells_form(cells), follow_redirects=True).data.decode()
    set_flow(app)
    start(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert quotas.report(s)[0]["matrix"] is not None and quotas.report(s, conds=False)[0]["matrix"] is None
        # names that contradict each other within a row say no rows either
        for cell, name in zip(s.quotas[0].cells, ("m / jung", "m / alt", "w / jung", "m / alt")):
            cell.name = name
        assert quotas.report(s, conds=False)[0]["matrix"] is None
        db.session.rollback()
    page = app.test_client().get(f"/q/{_share_without_conditions(app, client)}").data.decode()
    assert "Als Matrix" not in page and "gender" not in page and "age:" not in page and ">A <" in page


# --- K8: a quota added to a calculation the person has passed --------------------------------------------


def test_quota_added_to_a_passed_calculation_takes_no_second_place(app, client, survey):
    build(app, client)
    uid = start(app, client)
    p = screen(app, uid, gender="1")
    assert counters(app)["running"] == 1
    client.post("/surveys/1/close", data={"paused": "1"})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    client.post(Q + "/add")
    form = cells_form([("Alle", "alle", 50, [(3, "ge", "18")])])
    form["name"], form["code"] = "Alter", "qb"
    client.post(Q + "/2", data=form)
    set_flow(app)  # the calculation now names both quotas
    uid = start(app, client)
    # the same person goes on: the changed flow restarts the pages, the answers and the decision are kept
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "1", "i3": "30"})
    assert counters(app, "m")["running"] == 1                                               # not a second place
    assert counters(app, "alle")["running"] == 1                                            # the new quota: calculated
    with app.app_context():
        stored = db.session.scalar(db.select(Response).filter_by(source="real")).quota
        assert {k: (e["res"], e["held"]) for k, e in stored.items()} == {"1": ("in", True), "2": ("in", True)}
        assert not quotas.recount(db.session.get(Survey, 1))                                # the counters say what is held


# --- K9: "from X to X" on a number ----------------------------------------------------------------------


def test_equal_compares_numbers_as_numbers_and_codes_as_texts():
    assert conditions.range_cond(3, "2,5", "2,5") == {"item": 3, "op": "eq", "value": "2,5"}
    eq = lambda value, answer, op="eq": flow.holds({"item": 3, "op": op, "value": value}, {3: answer})  # noqa: E731
    assert eq("2,5", 2.5) and eq("2.5", 2.5) and eq("2", 2.0) and eq("018", 18) and eq("18", 18)
    assert not eq("2,5", 2.4) and not eq("19", 18)
    assert not eq("2,5", 2.5, "ne") and eq("2,5", 2.4, "ne") and not eq("2", 2.0, "ne")
    assert not eq("abc", 18) and eq("abc", 18, "ne")                                        # no number: texts, as before
    # the answer of a choice question is its code, a text: "01" and "1" stay two codes
    assert eq("1", "1") and not eq("1", "01") and not eq("1", "1.0") and not eq("1,0", "1")
    assert eq("1", "01", "ne") and eq("1", {"v": "1", "t": {"1": "anderes"}})
    assert not eq("1", True)                                                                # a ticked box is no number


# --- K10: digits that are not ASCII ----------------------------------------------------------------------


def test_number_fields_take_ascii_digits_only(app, client, survey):
    questionnaire(client)
    client.post(Q + "/add")
    form = cells_form([("M", "m", 2, [(2, "eq", "1")])], hold_minutes=SQUARED, tolerance=SQUARED)
    form["c0_target"] = SQUARED
    assert "gespeichert" in client.post(Q + "/1", data=form, follow_redirects=True).data.decode()
    form["c0_target"], form["c0_id"] = "2", SQUARED                                         # the id of a cell
    assert client.post(Q + "/1", data=form).status_code == 302
    assert client.post(Q + "/1/cross", data=test_quota_cross.picked(target=SQUARED, replace=False)).status_code == 302
    with app.app_context():
        quota = db.session.get(Quota, 1)
        assert quota.hold_minutes == quotas.DEFAULT_HOLD and [c.target for c in quota.cells][:1] == [2]
        assert len(quota.cells) == 7 and {c.target for c in quota.cells[1:]} == {0}
        assert census.options(MultiDict({"n": SQUARED}))["n"] == 0 and census.options(MultiDict({"n": "9" * 5000}))["n"] == 0
        assert census.options(MultiDict({"n": "1000"}))["n"] == 1000
    assert client.get(Q + f"/assistant?n={SQUARED}").status_code == 200
    # the randomizer of the flow
    client.post(E + "/flow/add", data={"kind": "rand"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        rid = next(n["id"] for n, _, _ in flow.walk(flow.tree(s)) if n["t"] == "rand")
        assert flow.update(s, rid, MultiDict({"code": "cond1", "k": SQUARED}), ["de"]) is None
        assert flow.find(flow.tree(s), rid)[0]["k"] == 1
        assert flow.update(s, rid, MultiDict({"code": "cond1", "k": "2"}), ["de"]) is None
        assert flow.find(flow.tree(s), rid)[0]["k"] == 2
        db.session.rollback()
    # steering in the field, and the days of a share link
    client.post(Q + "/1", data=cells_form([("M", "m", 2, [(2, "eq", "1")])]))
    set_flow(app)
    start(app, client)
    with app.app_context():
        cell_id = db.session.get(Quota, 1).cells[0].id
    r = client.post(Q + "/1/fieldwork", data={f"target_{cell_id}": SQUARED, "hold_minutes": "45"}, follow_redirects=True)
    assert r.status_code == 200 and "Platz gehalten 15 → 45 min" in r.data.decode()
    with app.app_context():
        assert [c.target for c in db.session.get(Quota, 1).cells] == [2]
    assert client.post(Q + "/link", data={"days": SQUARED}).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(QuotaShare.id))) == 1


def test_loop_takes_ascii_digits_only(app, client, survey):
    url = test_loops.setup(client, mode="k", k=SQUARED)
    with app.app_context():
        loop = loops.config(db.session.get(Block, 2))
        assert (loop["mode"], loop["k"], len(loop["rows"])) == ("k", 1, 4)                  # saved, with the default
    assert client.get(url).status_code == 200


# --- K11: the upgrade of the drafts at the start of the server -------------------------------------------


def _legacy_draft(survey):
    """A draft with a quota and a "Quotenprüfung" as it was stored before the calculation became a trigger."""
    quotas.add(survey)
    db.session.flush()
    survey.flow = {"t": "root", "children": [
        *[{"t": "block", "block": b.id} for b in survey.blocks],
        {"t": "quota", "id": "qn1", "quotas": [survey.quotas[0].id], "full": "end", "text": {"de": "voll"}}]}


def test_a_draft_that_cannot_be_upgraded_stops_neither_the_others_nor_the_start(app, client, survey, tmp_path, monkeypatch):
    client.post("/surveys/new", data={"title": "Zweite", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        broken, good = db.session.get(Survey, 1), db.session.get(Survey, 2)
        _legacy_draft(broken)
        _legacy_draft(good)
        broken.flow = {"t": "root", "children": [{"id": "x1"}, *broken.flow["children"]]}  # an element without a kind
        db.session.commit()
        assert quotas.upgrade_drafts() == 1                                                 # no exception; the other is done
        assert not quotas.is_legacy(db.session.get(Survey, 2).flow["children"][-2])
        assert db.session.get(Survey, 1).flow["children"][0] == {"id": "x1"}                # left as it is
        assert quotas.upgrade_drafts() == 0

    def locked(survey):
        from sqlalchemy.exc import OperationalError

        raise OperationalError("UPDATE survey", {}, Exception("database is locked"))

    monkeypatch.setattr(quotas, "upgrade", locked)
    from app import create_app

    assert create_app(_config(tmp_path)) is not None                                        # another process starts


# --- K12: the share page counts once ---------------------------------------------------------------------


def test_share_page_counts_the_participations_once(app, client, survey, monkeypatch):
    test_quota_field.providers_survey(app, client)
    client.post(Q + "/link", data={"label": "Panel B", "qp": "src=b", "conds": "1"})
    with app.app_context():
        token = db.session.scalar(db.select(QuotaShare)).token
    calls = {"count": 0, "providers": 0}
    count, providers = quotas._from_responses, quotas.providers

    def counting(*args, **kwargs):
        calls["count"] += 1
        return count(*args, **kwargs)

    def listing(*args, **kwargs):
        calls["providers"] += 1
        return providers(*args, **kwargs)

    monkeypatch.setattr(quotas, "_from_responses", counting)
    monkeypatch.setattr(quotas, "providers", listing)
    visitor = app.test_client()
    page = visitor.get(f"/q/{token}").data.decode()
    assert calls == {"count": 1, "providers": 0}
    # the numbers are those of the provider, on the page, in the text to copy and in the totals
    assert "nur Teilnahmen mit src = b" in page and "Männer\tm\tgender: männlich\t5\t1\t1\t3\t0\t0\toffen" in page
    assert "<strong>3</strong> begonnen" in page and "begonnen\t3" in page
    calls.update(count=0)
    body = visitor.get(f"/q/{token}/state.csv").data.decode("utf-8-sig")
    assert calls == {"count": 1, "providers": 0}
    assert "Männer,m,gender: männlich,5,1,1,3,0,0,offen" in body and "begonnen,3" in body
    calls.update(count=0)
    html = client.get("/surveys/1/flow/quotas/?qp=src%3Db").data.decode()                  # the researchers' page
    assert calls["count"] == 1 and '<option value="src=b" selected>' in html


# --- K13: the pre-launch check of the cells -------------------------------------------------------------------


def test_design_check_names_unknown_codes_of_none_of_and_unreadable_dates(app, client, survey):
    questionnaire(client)
    add(client, "date_time", block=2)                                                       # 6
    client.post(E + "/items/6", data={"code": "born", "question": "Geboren am", "parts": "date"})
    client.post(Q + "/add")
    set_flow(app)

    def found(*conds):
        with app.app_context():
            s = db.session.get(Survey, 1)
            s.quotas[0].cells = [QuotaCell(name=f"Z{n}", code=f"z{n}", conds=[cond], target=5, closed=False, running=0, done=0,
                                           refused=0, over=0, position=n) for n, cond in enumerate(conds)]
            db.session.flush()
            out = [(kind, m) for kind, m in quotas.check(s) if "Teilquote „Z" in m]
            db.session.rollback()
            return out

    assert found({"item": 2, "op": "nin", "value": "1, 2"}, {"item": 2, "op": "in", "value": "1"},
                 {"item": 6, "op": "between", "value": "01.01.1980..31.12.1989"}, {"item": 6, "op": "ge", "value": "1990-01-01"},
                 {"item": 6, "op": "eq", "value": "1987"}, {"item": 6, "op": "answered", "value": ""}) == []
    (kind, message), = found({"item": 2, "op": "nin", "value": "1, 9"})
    assert kind == "warning" and "nennt einen Wert, der kein Antwortcode von gender ist (vorhanden: 1, 2)" in message
    for cond, bad in (({"item": 6, "op": "between", "value": "01.01.1980..Silvester"}, "Silvester"),
                      ({"item": 6, "op": "ge", "value": "14:30"}, "14:30"), ({"item": 6, "op": "eq", "value": "gestern"}, "gestern")):
        (kind, message), = found(cond)
        assert kind == "warning" and f"„{bad}“ lässt sich nicht mit den Antworten von born vergleichen" in message
        assert "erwartet z. B. 23.05.2000" in message and "so trifft die Bedingung nie zu" in message
        assert not flow.holds(cond, {6: "1985-05-23"})                                      # it really never holds
