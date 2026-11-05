"""External sections of the flow (app/external.py): people leave for a web app and come back through a signed
return link, with the declared values; the rest of the survey goes on behind the section."""
import copy
import csv
import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from werkzeug.datastructures import MultiDict

from app import external, flow
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent
from tests.exports.test_transfer import _upload as _read_in

E = "/surveys/1/editor"
URL = "https://webapp.example/task?pid={{pid}}&return={{return}}"


def node_form(url=URL, values=(("score", "number"), ("label", "text")), **more):
    data = MultiDict({"name": "Experiment", "code": "ext1", "url": url, "text_de": "", "text_en": "", **more})
    for name, kind in values:
        data.add("value_name", name)
        data.add("value_kind", kind)
    return data


def build(app, client, url=URL, **more):
    """consent | intro (age) | external section | outro"""
    fill_consent(client)
    add(client, "text_input")                                        # 2
    client.post(E + "/items/2", data={"code": "age", "validation": "integer", "question": "Alter"})
    client.post(E + "/blocks")                                       # block 3
    add(client, "info", block=3)                                     # 3
    client.post(E + "/items/3", data={"code": "outro", "question": "Letzte Seite"})
    client.post(E + "/flow/add", data={"kind": "ext", "target": "k2", "mode": "after"})
    with app.app_context():
        nid = flow.walk_nodes(db.session.get(Survey, 1), "ext")[0]["id"]
    if url is not None:
        r = client.post(f"{E}/flow/{nid}/update", data=node_form(url, **more), follow_redirects=True)
        assert r.status_code == 200
    return nid


def start(app, client):
    r = client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"}, follow_redirects=True)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING, r.data.decode()[-3000:]
        return s.code


def to_the_section(app, uid):
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "33"})
    return p


def leave(p, uid):
    """Send the page of the section; returns (address of the web app, return link as a path on this site)."""
    r = p.post(f"/s/{uid}")
    assert r.status_code == 303
    query = parse_qs(urlsplit(r.headers["Location"]).query)
    back = urlsplit(query["return"][0])
    return r.headers["Location"], f"{back.path}?{back.query}"


def the_response(app):
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        return r.status, dict(r.external or {}), r.outside_since, r.pid


def test_the_flow_page_adds_and_saves_the_section(app, client, survey):
    nid = build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        node = flow.find(flow.tree(s), nid)[0]
        assert [n["t"] for n in flow.tree(s)["children"]][:4] == ["block", "block", "ext", "block"]
        assert (node["code"], node["name"], node["url"], node["delay"]) == ("ext1", "Experiment", URL, 0)
        assert node["values"] == [{"name": "score", "kind": "number"}, {"name": "label", "kind": "text"}]
        assert not [m for sev, m, _ in flow.check(s) if "Externer Abschnitt" in m and sev == "error"]
        # values without a signature: a hint, no error (tests/platform/test_security_keys.py)
        assert [m for sev, m, _ in flow.check(s) if "nicht signiert" in m and sev == "warning"]
    page = client.get("/surveys/1/flow/").data.decode()
    assert "Externer Abschnitt" in page and "webapp.example" in page and "ext1_score" in page and "Was die Web-App tun muss" in page


def test_what_the_form_refuses(app, client, survey):
    nid = build(app, client)

    def save(**kw):
        return client.post(f"{E}/flow/{nid}/update", data=node_form(**kw), follow_redirects=True).data.decode()

    assert "vollständige Adresse" in save(url="webapp.example/task")
    assert "nach dem Rechnernamen" in save(url="https://{{pid}}.example/?return={{return}}")
    assert "kein deklarierter Parameter" in save(url="https://webapp.example/?x={{nix}}&return={{return}}")
    assert "nur in Abschlusslinks" in save(url="https://webapp.example/?s={{sig}}&return={{return}}")
    assert "Buchstabe am Anfang" in save(values=(("1score", "number"),))
    assert "doppelt" in save(values=(("score", "number"), ("Score", "text")))
    assert "Signatur" in save(values=(("t", "number"),))
    assert "Variablenname" in save(code="1x")
    with app.app_context():  # nothing of it was stored
        node = flow.find(flow.tree(db.session.get(Survey, 1)), nid)[0]
        assert node["url"] == URL and [v["name"] for v in node["values"]] == ["score", "label"]


def test_the_design_check_names_what_is_missing(app, client, survey):
    nid = build(app, client, url=None)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert any("noch keine Adresse" in m for _, m, _ in flow.check(s))
    client.post(f"{E}/flow/{nid}/update", data=node_form("https://webapp.example/task?pid={{pid}}"))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert any("{{return}} nicht" in m for _, m, _ in flow.check(s))
        # before the consent, and in a loop
        tree = flow.tree(s)
        node = flow.find(tree, nid)[0]
        s.flow = {"t": "root", "children": [node, {"t": "block", "block": 1},
                                            {"t": "loop", "id": "l1", "name": "L", "code": "loop1", "columns": [{"id": "c1", "name": "x"}],
                                             "rows": [{"id": "r1", "values": {"c1": {"de": "a"}}}], "mode": "all", "k": 1,
                                             "order": "fixed", "balanced": True, "source": "factors",
                                             "children": [{"t": "block", "block": 2}, {**node, "id": "o2", "code": "ext2"}]},
                                            {"t": "block", "block": 3}]}
        flow.normalize(s)
        found = [m for _, m, _ in flow.check(s)]
        assert any("vor der Einwilligung" in m for m in found) and any("Vignetten-Schleife" in m for m in found)


def test_leaving_and_coming_back_with_values(app, client, survey):
    build(app, client)
    uid = start(app, client)
    p = to_the_section(app, uid)
    page = p.get(f"/s/{uid}").data.decode()
    assert "Sie werden weitergeleitet" in page and "Teilmodul" in page and 'data-auto-leave="0"' in page
    assert "Letzte Seite" not in page and "webapp.example" not in page and page.count("<noscript><button") == 1
    assert the_response(app)[2] is None                               # the page alone is not leaving
    target, back = leave(p, uid)
    status, state, outside, pid = the_response(app)
    assert target.startswith(f"https://webapp.example/task?pid={pid}&return=http%3A%2F%2F") and "back%3Ft%3D" in target
    assert status == "active" and outside is not None and len(state) == 1
    # the survey link opened again without the return link: the page of the section once more, the same return link
    again = p.get(f"/s/{uid}").data.decode()
    assert "Sie waren schon dort?" in again and "data-auto-leave" not in again and "<noscript>" not in again
    assert "kind=technical" in again and "data-leave-help" not in again      # the way out when the web app is down
    assert leave(p, uid)[1] == back
    # the web app sends the person back, with declared values and others
    r = p.get(back + "&score=17&label=gut%20gemacht&secret=x")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/s/{uid}")
    assert "Letzte Seite" in p.get(f"/s/{uid}", follow_redirects=True).data.decode()
    status, state, outside, _ = the_response(app)
    entry = next(iter(state.values()))
    assert entry["v"] == {"score": 17, "label": "gut gemacht"} and entry["n"] == 2 and entry["s"] >= 0 and entry["back"]
    assert outside is None and status == "active"
    # the link a second time changes nothing: in this browser it leads on, elsewhere it says so
    assert p.get(back + "&score=99").status_code == 302
    other = app.test_client().get(back + "&score=99")
    assert other.status_code == 200 and "bereits verwendet" in other.data.decode()
    assert next(iter(the_response(app)[1].values()))["v"]["score"] == 17
    p.post(f"/s/{uid}", data={"nav": "next"})
    assert the_response(app)[0] == "completed"


def test_the_return_link_continues_in_another_browser(app, client, survey):
    build(app, client)
    uid = start(app, client)
    p = to_the_section(app, uid)
    _, back = leave(p, uid)
    elsewhere = app.test_client()
    assert elsewhere.get(back + "&score=3,5").status_code == 302
    assert "Letzte Seite" in elsewhere.get(f"/s/{uid}", follow_redirects=True).data.decode()
    assert next(iter(the_response(app)[1].values()))["v"] == {"score": 3.5}
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(source="real")) == 1


def test_a_link_this_server_did_not_sign_is_refused(app, client, survey):
    build(app, client)
    uid = start(app, client)
    p = to_the_section(app, uid)
    _, back = leave(p, uid)
    assert app.test_client().get(back[:-4] + "abcd").status_code == 400
    assert app.test_client().get(f"/s/{uid}/back").status_code == 400
    assert "nicht gültig" in app.test_client().get(f"/s/{uid}/back?t=x.y").data.decode()
    assert not next(iter(the_response(app)[1].values())).get("back")


def test_values_that_are_no_numbers_and_long_texts(app):
    node = {"values": [{"name": "score", "kind": "number"}, {"name": "note", "kind": "text"}, {"name": "n", "kind": "number"}]}
    got = external.read_values(node, {"score": "abc", "note": "x" * 300 + "\x00", "n": "1e400", "other": "1"})
    assert got == {"note": "x" * 100}
    assert external.read_values(node, {"score": "-4", "n": "2.50"}) == {"score": -4, "n": 2.5}


def test_automatic_redirect_and_own_text(app, client, survey):
    build(app, client, delay="5", text_de="**Gleich geht es zur Aufgabe.**", text_en="**On to the task.**")
    uid = start(app, client)
    p = to_the_section(app, uid)
    page = p.get(f"/s/{uid}").data.decode()
    assert 'data-auto-leave="5"' in page and "Gleich geht es zur Aufgabe." in page and "in 5 Sekunden" in page
    assert "Teilmodul" not in page and "data-leave-help hidden" in page


def test_a_stored_mode_becomes_a_delay():
    """Sections saved before the delay could be set: "auto" keeps its 2 seconds, "button" no longer waits for a click."""
    assert (external.delay({"mode": "auto"}), external.delay({"mode": "button"}), external.delay({})) == (2, 0, 0)
    assert (external.delay({"delay": 7, "mode": "auto"}), external.delay({"delay": 61}), external.delay({"delay": True})) == (7, 0, 0)


@pytest.mark.parametrize("raw, expected", [("", 0), ("0", 0), (" 12 ", 12), ("60", 60), ("61", None), ("-1", None), ("1.5", None), ("²", None)])
def test_the_delay_of_the_form(raw, expected):
    assert external.read_delay(raw) == expected


def test_a_delay_out_of_range_is_refused(app, client, survey):
    nid = build(app, client)
    client.post(f"{E}/flow/{nid}/update", data=MultiDict({"name": "Experiment", "code": "ext1", "url": URL, "delay": "90"}))
    with app.app_context():
        assert flow.find(flow.tree(db.session.get(Survey, 1)), nid)[0]["delay"] == 0


def test_a_failed_redirect_can_be_reported_as_a_technical_problem(app, client, survey):
    build(app, client)
    uid = start(app, client)
    p = to_the_section(app, uid)
    leave(p, uid)
    form = p.get(f"/s/{uid}/complaint?kind=technical").data.decode()
    assert 'value="technical" checked' in form


def test_a_section_in_a_condition_is_passed_by_the_others(app, client, survey):
    nid = build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        node = flow.find(flow.tree(s), nid)[0]
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "lt", "value": "18"}, "children": [node]},
            {"t": "block", "block": 3}]}
        flow.normalize(s)
        db.session.commit()
    uid = start(app, client)
    p = to_the_section(app, uid)                                      # 33 years old: not into the section
    assert "Letzte Seite" in p.get(f"/s/{uid}").data.decode()
    assert the_response(app)[1] == {}


def test_the_test_run_shows_the_address_and_simulates_the_return(app, client, survey):
    build(app, client)
    P = E + "/preview"
    client.get(P + "?restart=1")
    client.post(P, data=CONSENT_OK)
    client.post(P, data={"nav": "next", "i2": "20"})
    page = client.get(P).data.decode()
    assert "Rückkehr simulieren" in page and "webapp.example/task?pid=" in page and "nur-im-Feld" in page
    assert 'name="xv_score"' in page
    client.post(P, data={"ext_back": "1", "xv_score": "12", "xv_label": "ok"})
    assert "Letzte Seite" in client.get(P).data.decode()
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="test"))
        assert next(iter(r.external.values()))["v"] == {"score": 12, "label": "ok"} and r.outside_since is None


def test_dummy_data_pass_the_section_with_values(app, client, survey):
    build(app, client)
    client.post("/surveys/1/dummy", data={"n": "5"})
    with app.app_context():
        dummies = db.session.scalars(db.select(Response).filter_by(source="dummy")).all()
        assert len(dummies) == 5 and all(d.status == "completed" for d in dummies)
        for d in dummies:
            entry = next(iter(d.external.values()))
            assert isinstance(entry["v"]["score"], int) and entry["v"]["label"].startswith("Dummy-") and entry["s"] >= 40


def test_privacy_page_and_consent_name_the_other_website(app, client, survey):
    build(app, client)
    uid = start(app, client)
    page = app.test_client().get(f"/s/{uid}/privacy").data.decode()
    assert "auf einer anderen Website statt (webapp.example)" in page
    with app.app_context():
        s = db.session.get(Survey, 1)
        consent = s.consent_item
        assert "webapp.example" in consent.kind.facts(consent, "de")["external_website"]


def test_the_survey_file_and_a_copy_keep_the_section(app, client, survey):
    build(app, client)
    data = json.loads(client.get("/surveys/1/export/survey.json").data)
    data["survey"]["title"] = "Studie B"
    _read_in(client, data)
    with app.app_context():
        new = db.session.get(Survey, 2)
        node = flow.walk_nodes(new, "ext")[0]
        assert (node["code"], node["url"], node["delay"]) == ("ext1", URL, 0)
        assert node["values"] == [{"name": "score", "kind": "number"}, {"name": "label", "kind": "text"}]


def test_who_is_no_admin_cannot_change_the_address(app, client, survey):
    nid = build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        form = node_form("https://evil.example/?return={{return}}")
        assert "Rolle „Admin“" in flow.update(s, nid, form, s.languages, links=False)
        form = node_form(URL, values=(("points", "number"),))
        assert flow.update(s, nid, form, s.languages, links=False) is None
        node = flow.find(flow.tree(s), nid)[0]
        assert node["url"] == URL and [v["name"] for v in node["values"]] == ["points"]


def test_export_and_codebook_have_the_values_and_the_time_outside(app, client, survey):
    build(app, client)
    uid = start(app, client)
    p = to_the_section(app, uid)
    _, back = leave(p, uid)
    p.get(back + "&score=17&label=gut")
    p.get(f"/s/{uid}", follow_redirects=True)
    p.post(f"/s/{uid}", data={"nav": "next"})
    stays = to_the_section(app, uid)                                  # leaves and does not come back
    leave(stays, uid)
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig"))))
    done = next(r for r in rows if r["status"] == "completed")
    out = next(r for r in rows if r["status"] == "active")
    assert (done["ext1_score"], done["ext1_label"]) == ("17", "gut") and float(done["ext1_seconds"].replace(",", ".")) >= 0
    assert (out["ext1_score"], out["ext1_label"], out["ext1_seconds"]) == ("", "", "")
    head = list(rows[0])
    assert head.index("ext1_seconds") + 1 == head.index("ext1_score") == head.index("ext1_label") - 1
    book = {r[0]: r for r in csv.reader(io.StringIO(client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert "Zeit außerhalb" in book["ext1_seconds"][1] and "webapp.example" in book["ext1_seconds"][4]
    assert book["ext1_label"][2] == "Text" and "Rückgabewert score" in book["ext1_score"][1]
    assert client.get("/surveys/1/export/spss.zip").status_code == 200
    assert client.get("/surveys/1/export/data.sav").status_code == 200


def test_a_value_named_like_another_column_is_an_error(app, client, survey):
    nid = build(app, client)
    client.post(f"{E}/flow/{nid}/update", data=node_form(values=(("seconds", "number"),)))
    from app import design

    with app.app_context():
        found = [f.message for f in design.check(db.session.get(Survey, 1)) if f.severity == "error"]
        assert any("ext1_seconds" in m and "doppelt" in m for m in found)


def test_the_field_page_counts_who_is_outside(app, client, survey):
    from datetime import timedelta

    build(app, client)
    uid = start(app, client)
    assert "Gerade außerhalb" in client.get("/surveys/1/field").data.decode()
    for _ in range(2):
        leave(to_the_section(app, uid), uid)
    back_again = to_the_section(app, uid)
    back_again.get(leave(back_again, uid)[1])
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert external.outside(s) == {"now": 2, "gone": 0, "minutes": 60}
        late = db.session.scalars(db.select(Response).filter(Response.outside_since.is_not(None))).first()
        late.outside_since -= timedelta(minutes=61)
        db.session.commit()
        assert external.outside(s) == {"now": 1, "gone": 1, "minutes": 60}
    page = client.get("/surveys/1/field").data.decode()
    assert "<strong data-outside-now>1</strong>" in page and "<strong data-outside-gone>1</strong>" in page
    assert client.get("/surveys/1/field/live").get_json()["outside"] == {"now": 1, "gone": 1, "minutes": 60}


def test_a_survey_without_a_section_shows_nothing_of_it(app, client, survey):
    fill_consent(client)
    add(client, "info")
    assert "Gerade außerhalb" not in client.get("/surveys/1/field").data.decode()
    with app.app_context():
        assert external.outside(db.session.get(Survey, 1)) is None and external.columns(db.session.get(Survey, 1)) == []


LOOP_URL = "https://webapp.example/task?distance={{distance}}&size={{size}}&return={{return}}"


def build_loop(app, client, url=LOOP_URL, en=False, **loop):
    """consent | intro (age) | loop over two rows: external section, then a question | outro"""
    nid = build(app, client, url=None)
    add(client, "single_choice", block=3)                            # 4
    client.post(E + "/items/4", data={"code": "rating", "question": "{{distance}}: {{ext1_time_ms}} ms",
                                      "option_id": ["a", "b"], "option_code": ["1", "2"], "option_label": ["gut", "schlecht"]})
    with app.app_context():
        s = db.session.get(Survey, 1)
        section = flow.find(flow.tree(s), nid)[0]
        rows = [{"id": "r1", "values": {"c1": {"de": "0", "en": "short"}, "c2": {"de": "1"}}},
                {"id": "r2", "values": {"c1": {"de": "1", "en": "long"}, "c2": {"de": "0"}}}]
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "loop", "id": "l1", "name": "Durchgänge", "code": "loop1", "mode": "all", "k": 1, "order": "fixed",
             "balanced": True, "rows": rows,
             "columns": [{"id": "c1", "name": "distance", "hidden": False}, {"id": "c2", "name": "size", "hidden": False}],
             "children": [{**section, "url": url, "delay": 0, "values": [{"name": "time_ms", "kind": "number"}]},
                          {"t": "block", "block": 3}], **loop}]}
        flow.normalize(s)
        db.session.commit()
    return nid


def test_one_section_runs_every_pass_of_a_loop(app, client, survey):
    """One address for all passes, then a question in the same pass; the values of each pass stay apart, the text
    shows the last return, and "Back" does not lead into the web app again."""
    nid = build_loop(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not [m for level, m, _ in flow.check(s) if level == "error" and "Abschnitt" in m]
    uid = start(app, client)
    p = to_the_section(app, uid)
    first, back = leave(p, uid)
    assert "distance=0&size=1&return=" in first
    assert the_response(app)[1][f"{nid}#r1"]["n"] == 1
    assert p.get(back + "&time_ms=501").status_code == 302
    page = p.get(f"/s/{uid}", follow_redirects=True).data.decode()
    assert "0: 501 ms" in page and 'name="nav" value="back"' not in page
    p.post(f"/s/{uid}", data={"nav": "back"})                        # sent by hand, it leads nowhere either
    assert "0: 501 ms" in p.get(f"/s/{uid}", follow_redirects=True).data.decode()
    assert p.get(back + "&time_ms=9").status_code == 302             # the link of a pass counts once
    p.post(f"/s/{uid}", data={"nav": "next", "i4": "1"})
    p.get(f"/s/{uid}", follow_redirects=True)
    second, back = leave(p, uid)
    assert "distance=1&size=0&return=" in second
    p.get(back + "&time_ms=702")
    page = p.get(f"/s/{uid}", follow_redirects=True).data.decode()
    assert "1: 702 ms" in page and 'name="nav" value="back"' not in page  # the next return overwrites the text
    p.post(f"/s/{uid}", data={"nav": "next", "i4": "2"})
    assert the_response(app)[0] == "completed"
    kept = the_response(app)[1]
    assert (kept[f"{nid}#r1"]["v"], kept[f"{nid}#r2"]["v"]) == ({"time_ms": 501}, {"time_ms": 702})
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig"))))
    assert (rows[0]["ext1_time_ms_1"], rows[0]["ext1_time_ms_2"]) == ("501", "702") and "ext1_time_ms" not in rows[0]
    assert float(rows[0]["ext1_seconds_2"].replace(",", ".")) >= 0
    assert "ext1_time_ms_2" in {r[0] for r in csv.reader(io.StringIO(client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    with app.app_context():
        from app import export

        s = db.session.get(Survey, 1)
        long = list(csv.DictReader(io.StringIO(export.loop_long_csv(s, flow.find(flow.tree(s), "l1")[0]).lstrip("\ufeff"))))
    assert [(r["distance"], r["rating"], r["ext1_time_ms"]) for r in long] == [("0", "1", "501"), ("1", "2", "702")]


def test_the_address_takes_the_default_language_of_a_column(app, client, survey):
    build_loop(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        node = external.nodes(s)[0]
        assert external.row_values(s, node, "r1") == {"distance": "0", "size": "1"}
        assert external.row_values(s, node, "nope") == {} and external.row_values(s, node, None) == {}


def test_a_return_link_of_one_pass_does_not_end_another(app, client, survey):
    build_loop(app, client)
    uid = start(app, client)
    p = to_the_section(app, uid)
    _, back = leave(p, uid)
    token = back.split("t=")[1]
    with app.app_context():
        pid, nid, key, row = external.read_token(token)
        assert row == "r1"
        s = db.session.get(Survey, 1)
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        now = r.started_at
        assert not external.come_back(r, external.nodes(s)[0], key, {}, now, "r2")


def test_a_return_link_of_a_section_without_a_loop_still_has_four_parts(app, client, survey):
    build(app, client)
    uid = start(app, client)
    p = to_the_section(app, uid)
    _, back = leave(p, uid)
    with app.app_context():
        assert external.read_token(back.split("t=")[1])[3] is None


def test_columns_count_only_in_their_own_loop(app, client, survey):
    nid = build(app, client, url=None)
    r = client.post(f"{E}/flow/{nid}/update", data=node_form(LOOP_URL), follow_redirects=True).data.decode()
    assert "distance" in r
    with app.app_context():
        assert flow.find(flow.tree(db.session.get(Survey, 1)), nid)[0]["url"] == ""


def test_a_column_named_like_a_placeholder_is_refused(app, client, survey):
    build_loop(app, client, url="https://webapp.example/?pid={{pid}}&return={{return}}",
               columns=[{"id": "c1", "name": "pid", "hidden": False}, {"id": "c2", "name": "size", "hidden": False}])
    with app.app_context():
        assert any("heißt wie ein Platzhalter" in m for _, m, _ in flow.check(db.session.get(Survey, 1)))


def test_a_loop_of_sections_alone_runs_and_is_not_empty(app, client, survey):
    nid = build_loop(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        tree = copy.deepcopy(flow.tree(s))
        loop = flow.find(tree, "l1")[0]
        loop["children"] = [c for c in loop["children"] if c["t"] == "ext"]
        tree["children"].insert(tree["children"].index(loop) + 1, {"t": "block", "block": 3})
        s.flow = tree
        flow.normalize(s)
        db.session.commit()
        assert not any("ist leer" in m for _, m, _ in flow.check(s))
    uid = start(app, client)
    p = to_the_section(app, uid)
    for value in ("10", "20"):
        p.get(f"/s/{uid}", follow_redirects=True)
        _, back = leave(p, uid)
        p.get(back + f"&time_ms={value}")
    assert "20 ms" in p.get(f"/s/{uid}", follow_redirects=True).data.decode()
    kept = the_response(app)[1]
    assert sorted(k for k in kept if k.startswith(nid)) == [f"{nid}#r1", f"{nid}#r2"]


def test_a_section_in_a_loop_leaves_a_survey_file_with_its_address(app, client, survey):
    build_loop(app, client)
    data = json.loads(client.get("/surveys/1/export/survey.json").data)
    _read_in(client, data, title="Kopie")
    with app.app_context():
        twin = db.session.scalar(db.select(Survey).filter_by(title="Kopie"))
        assert external.nodes(twin)[0]["url"] == LOOP_URL


def test_dummy_data_go_through_every_pass(app, client, survey):
    nid = build_loop(app, client)
    client.post("/surveys/1/dummy", data={"n": "3"})
    with app.app_context():
        dummies = db.session.scalars(db.select(Response).filter_by(source="dummy")).all()
        assert len(dummies) == 3 and all(d.status == "completed" for d in dummies)
        assert all({f"{nid}#r1", f"{nid}#r2"} <= set(d.external) for d in dummies)


def test_the_flow_page_puts_a_section_into_a_loop(app, client, survey):
    """Through the page, as a researcher does it: the section goes into the loop, and its address offers the columns."""
    nid = build(app, client, url=None)
    client.post(E + "/flow/add", data={"kind": "loop", "target": "k3", "mode": "wrap"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        lid = flow.walk_nodes(s, "loop")[0]["id"]
        tree = copy.deepcopy(flow.tree(s))
        loop = flow.find(tree, lid)[0]
        loop.update(columns=[{"id": "c1", "name": "distance", "hidden": False}],
                    rows=[{"id": "r1", "values": {"c1": {"de": "0"}}}])
        s.flow = tree
        db.session.commit()
    client.post(f"{E}/flow/{nid}/place", data={"target": lid, "mode": "into"})
    r = client.post(f"{E}/flow/{nid}/update", data=node_form("https://webapp.example/?d={{distance}}&return={{return}}"),
                    follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        s = db.session.get(Survey, 1)
        node, parent = flow.find(flow.tree(s), nid)
        assert parent["t"] == "loop" and node["url"].startswith("https://webapp.example/?d={{distance}}")
    assert "{{distance}}" in client.get("/surveys/1/flow/").data.decode()


def test_the_preview_goes_through_the_passes(app, client, survey):
    nid = build_loop(app, client)
    P = E + "/preview"
    client.get(P + "?restart=1")
    client.post(P, data=CONSENT_OK)
    client.post(P, data={"nav": "next", "i2": "33"})
    page = client.get(P).data.decode()
    assert "distance=0&amp;size=1" in page
    client.post(P, data={"ext_back": "1", "xv_time_ms": "12"})
    assert "0: 12 ms" in client.get(P, follow_redirects=True).data.decode()
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="test"))
        assert r.external[f"{nid}#r1"]["v"] == {"time_ms": 12}

