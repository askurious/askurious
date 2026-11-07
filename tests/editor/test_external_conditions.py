"""Conditions of the flow on what an external section handed back (concept "Bedingungen auf Rückgabewerte",
app/external.py): the way through the survey follows the return value, and an empty value is treated as an unanswered
question is. The pre-launch check names what cannot work."""
import copy
import json

import pytest

from app import conditions, ddi, design, external, flow
from app.extensions import db
from app.models import Response, Survey
from tests.editor.test_external import E, build, leave, node_form, start, the_response, to_the_section
from tests.exports.test_transfer import _upload as _read_in
from tests.conftest import CONSENT_OK


def with_condition(app, nid, name, op, value="", keep_else=False):
    """Puts the last block behind a condition on a return value, with an "else" on an end if asked."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        tree = copy.deepcopy(flow.tree(s))
        block = next(c for c in tree["children"] if c["t"] == "block" and c["block"] == 3)
        at = tree["children"].index(block)
        branch = {"t": "branch", "id": "b1", "name": "Weg", "cond": {"item": f"x:{nid}:{name}", "op": op, "value": value},
                  "children": [block]}
        tree["children"][at:at + 1] = [branch]
        s.flow = tree
        flow.normalize(s)
        db.session.commit()


def problems(app):
    with app.app_context():
        return [(f.severity, f.message) for f in design.check(db.session.get(Survey, 1))]


def returned(app, client, nid, name, op, value, query):
    """A participation that comes back with `query`; (status after going on, the page shown)."""
    with_condition(app, nid, name, op, value)
    uid = start(app, client)
    p = to_the_section(app, uid)
    _, back = leave(p, uid)
    assert p.get(back + query).status_code == 302
    page = p.get(f"/s/{uid}", follow_redirects=True).data.decode()
    return the_response(app)[0], page


@pytest.mark.parametrize("op, value, query, shown", [
    ("lt", "3", "&score=2", True),
    ("lt", "3", "&score=3", False),
    ("gt", "2,5", "&score=2.6", True),
    ("between", "1..3", "&score=2", True),
    ("eq", "7", "&score=7.0", True),
    ("lt", "2000", "&score=1000", True),
    ("answered", "", "&score=0", True),
    ("answered", "", "", False),
    ("empty", "", "", True),
    ("lt", "3", "", False),                # no value: no comparison holds
    ("lt", "3", "&score=abc", False),      # a number that was none
])
def test_a_number_decides_the_way(app, client, survey, op, value, query, shown):
    nid = build(app, client)
    _, page = returned(app, client, nid, "score", op, value, query)
    assert ("Letzte Seite" in page) is shown


@pytest.mark.parametrize("op, value, query, shown", [
    ("eq", "aborted", "&label=aborted", True),
    ("eq", "aborted", "&label=Aborted", False),   # exact, as written
    ("ne", "aborted", "&label=completed", True),
    ("ne", "aborted", "", False),                 # empty: also "is not" does not hold
    ("contains", "bort", "&label=aborted", True),
])
def test_a_text_decides_the_way(app, client, survey, op, value, query, shown):
    nid = build(app, client)
    _, page = returned(app, client, nid, "label", op, value, query)
    assert ("Letzte Seite" in page) is shown


def test_a_screen_out_follows_a_return_value(app, client, survey):
    nid = build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        tree = copy.deepcopy(flow.tree(s))
        at = next(n for n, c in enumerate(tree["children"]) if c["t"] == "block" and c["block"] == 3)
        tree["children"].insert(at, {"t": "branch", "id": "b1", "name": "Abbruch",
                                     "cond": {"item": f"x:{nid}:label", "op": "eq", "value": "aborted"},
                                     "children": [{"t": "end", "id": "e1", "kind": "screenout", "text": {}}]})
        s.flow = tree
        flow.normalize(s)
        db.session.commit()
    uid = start(app, client)
    p = to_the_section(app, uid)
    _, back = leave(p, uid)
    p.get(back + "&label=aborted")
    p.get(f"/s/{uid}", follow_redirects=True)
    assert the_response(app)[0] == Response.SCREENOUT


def test_answers_behind_the_condition_survive_the_end(app, client, survey):
    """drop_skipped_answers sees the return value too: otherwise the answers behind the condition were dropped."""
    nid = build(app, client)
    with app.app_context():
        client.post(E + "/items/3", data={"code": "outro", "question": "Letzte Seite"})
    from tests.conftest import add

    add(client, "text_input", block=3)                               # 4
    client.post(E + "/items/4", data={"code": "after", "question": "Danach"})
    with_condition(app, nid, "score", "gt", "1")
    uid = start(app, client)
    p = to_the_section(app, uid)
    _, back = leave(p, uid)
    p.get(back + "&score=2")
    assert "Danach" in p.get(f"/s/{uid}", follow_redirects=True).data.decode()
    p.post(f"/s/{uid}", data={"nav": "next", "i4": "behalten"})
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        assert r.status == Response.COMPLETED and r.answers.get("4") == "behalten"


def test_a_wrong_signature_empties_the_value(app, client, survey):
    nid = build(app, client, sign_part="1", sign_values="1")
    _, page = returned(app, client, nid, "score", "lt", "3", "&score=1&sig_v=2&sig=00")
    assert "Letzte Seite" not in page
    assert next(iter(the_response(app)[1].values()))["sig"] == 0


def test_the_test_run_sees_the_value_at_once(app, client, survey):
    """In a test run the return happens within the request that goes on, unlike a real one."""
    nid = build(app, client)
    with_condition(app, nid, "score", "lt", "3")
    P = E + "/preview"
    for value, shown in (("2", True), ("9", False)):
        client.get(P + "?restart=1")
        client.post(P, data=CONSENT_OK)
        client.post(P, data={"nav": "next", "i2": "20"})
        client.post(P, data={"ext_back": "1", "xv_score": value})
        assert ("Letzte Seite" in client.get(P, follow_redirects=True).data.decode()) is shown


def test_the_flow_page_offers_the_values_and_saves_a_condition(app, client, survey):
    nid = build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        offered = conditions.describe(s, everything=True)
        assert offered[f"x:{nid}:score"]["kind"] == "number" and offered[f"x:{nid}:score"]["modes"] == "paramnumber"
        assert offered[f"x:{nid}:label"]["kind"] == "text" and offered[f"x:{nid}:label"]["name"] == "ext1_label"
        assert not [k for k in conditions.describe(s) if k.startswith("x:")]    # not for the Teilquoten
    client.post(E + "/flow/add", data={"kind": "branch", "target": "k3", "mode": "wrap"})
    with app.app_context():
        branch = flow.walk_nodes(db.session.get(Survey, 1), "branch")[0]
    page = client.get("/surveys/1/flow/").data.decode()
    assert f"x:{nid}:score" in page and "Rückgabewert · ext1_score" in page
    client.post(f"{E}/flow/{branch['id']}/update", data={"name": "Weg", "item": f"x:{nid}:score", "mode": "lt", "value": "3,5"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        cond = flow.walk_nodes(s, "branch")[0]["cond"]
        assert cond == {"item": f"x:{nid}:score", "op": "lt", "value": "3,5"}
        assert flow.condition_text(s, cond) == "ext1_score: unter 3,5"


def test_a_statement_cannot_depend_on_a_return_value(app, client, survey):
    from app import rows
    from app.models import Item

    build(app, client)
    with app.app_context():
        assert not [k for k in rows.offered(db.session.get(Item, 3)) if k.startswith("x:")]


def test_the_check_names_a_value_that_is_gone(app, client, survey):
    nid = build(app, client)
    with_condition(app, nid, "gone", "lt", "3")
    assert any(s == "error" and "den es nicht gibt" in m for s, m in problems(app))


def test_the_check_names_a_section_that_comes_later(app, client, survey):
    nid = build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        tree = copy.deepcopy(flow.tree(s))
        section = next(c for c in tree["children"] if c["t"] == "ext")
        tree["children"].remove(section)
        tree["children"].append(section)
        s.flow = tree
        flow.normalize(s)
        db.session.commit()
    with_condition(app, nid, "score", "lt", "3")
    assert any(s == "error" and "erst danach" in m for s, m in problems(app))


def test_the_check_warns_about_an_unsigned_value_only(app, client, survey):
    nid = build(app, client)
    with_condition(app, nid, "score", "lt", "3")
    assert any(s == "warning" and "nicht signierten Rückgabewert" in m for s, m in problems(app))
    client.post(f"{E}/flow/{nid}/update", data=node_form(sign_part="1", sign_values="1"))
    assert not any("nicht signierten Rückgabewert" in m for _, m in problems(app))


def test_a_section_in_a_loop_is_not_offered_and_is_an_error(app, client, survey):
    from tests.editor.test_external import build_loop

    nid = build_loop(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not [k for k in conditions.describe(s, everything=True) if k.startswith("x:")]
        tree = copy.deepcopy(flow.tree(s))
        tree["children"].append({"t": "branch", "id": "b1", "name": "Weg", "children": [],
                                 "cond": {"item": f"x:{nid}:time_ms", "op": "lt", "value": "3"}})
        s.flow = tree
        flow.normalize(s)
        db.session.commit()
    assert any(s == "error" and "in einer Schleife" in m for s, m in problems(app))


def test_a_pass_of_a_loop_gives_no_value():
    class R:
        external = {"o1#r1": {"back": "2026-11-07", "v": {"a": 1}}, "o2": {"back": "2026-11-07", "v": {"b": 2}},
                    "o3": {"out": "2026-11-07", "v": {"c": 3}}, external.PARAMS: {"d": "4"}}

    assert external.flow_view(R) == {"x:o2:b": 2}


def test_the_reference_is_read_strictly():
    assert external.read_ref("x:o1:score") == ("o1", "score")
    assert [external.read_ref(v) for v in ("p:a", "x:o1", "x::a", "x:o1:", 5, None)] == [None] * 6


def test_a_copy_keeps_the_condition(app, client, survey):
    nid = build(app, client)
    with_condition(app, nid, "score", "lt", "3")
    client.post("/surveys/1/duplicate", data={"title": "Kopie"})
    with app.app_context():
        new = db.session.get(Survey, 2)
        section = flow.walk_nodes(new, "ext")[0]
        cond = flow.walk_nodes(new, "branch")[0]["cond"]
        assert cond["item"] == f"x:{section['id']}:score" and flow.condition_text(new, cond) == "ext1_score: unter 3"


def test_a_survey_file_keeps_the_condition(app, client, survey):
    nid = build(app, client)
    with_condition(app, nid, "score", "lt", "3")
    data = json.loads(client.get("/surveys/1/export/survey.json").data)
    data["survey"]["title"] = "Studie B"
    _read_in(client, data)
    with app.app_context():
        new = db.session.get(Survey, 2)
        section = flow.walk_nodes(new, "ext")[0]
        assert flow.walk_nodes(new, "branch")[0]["cond"]["item"] == f"x:{section['id']}:score"


def test_a_survey_file_follows_a_section_that_gets_another_id(app, client, survey):
    nid = build(app, client)
    with_condition(app, nid, "score", "lt", "3")
    raw = client.get("/surveys/1/export/survey.json").data.decode().replace(nid, "root")   # an id that is not taken over
    data = json.loads(raw)
    data["survey"]["title"] = "Studie C"
    _read_in(client, data)
    with app.app_context():
        new = db.session.get(Survey, 2)
        section = flow.walk_nodes(new, "ext")[0]
        assert section["id"] != "root"
        assert flow.walk_nodes(new, "branch")[0]["cond"]["item"] == f"x:{section['id']}:score"


def test_a_survey_file_refuses_a_reference_of_the_wrong_form(app, client, survey):
    from app import transfer

    assert transfer._cond({"item": "x:o1:score", "op": "lt", "value": 3}, {}, {})["item"] == "x:o1:score"
    assert transfer._cond({"item": "x:../:<b>", "op": "lt", "value": 3}, {}, {})["item"] is None


def test_the_metadata_name_the_column(app, client, survey):
    nid = build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert ddi.condition_expression(s, {"item": f"x:{nid}:score", "op": "lt", "value": "3"}) == "ext1_score < 3"
        assert ddi.condition_expression(s, {"item": "x:gone:score", "op": "lt", "value": "3"}) == ""
