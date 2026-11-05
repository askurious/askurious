"""Web service in the flow (app/webservice.py, Philipp 2026-10-08): the server calls an API where a participant
passes the node and keeps fields of its JSON answer as values for later texts and the export; https and public
servers only."""
import copy
import csv
import io
import json
import socket

import pytest
from werkzeug.datastructures import MultiDict

from app import flow, vignettes, webservice
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK, add, experiments_on, fill_consent
from tests.helpers.page_pos import page_pos

E = "/surveys/1/editor"
URL = "https://api.open-meteo.com/v1/forecast?city={{city}}&current=temperature_2m"


def node_form(url=URL, values=(("temp", "current.temperature_2m", "number"), ("unit", "current_units.temperature_2m", "text")),
              **more):
    data = MultiDict({"name": "Wetter", "code": "ws1", "method": "GET", "url": url, "body": "", "header": "", **more})
    for name, path, kind in values:
        data.add("value_name", name)
        data.add("value_path", path)
        data.add("value_kind", kind)
    return data


@pytest.fixture
def answers(monkeypatch):
    """The API: records what was called, answers with JSON."""
    calls = []

    def fetch(method, url, body=None, headers=None, timeout=None):
        calls.append((method, url, body, headers))
        return 200, json.dumps({"current": {"temperature_2m": 13.8}, "current_units": {"temperature_2m": "°C"}}).encode()

    monkeypatch.setattr(webservice, "fetch", fetch)
    return calls


def build(app, client):
    """consent | city (text) | web service | page that shows the temperature"""
    experiments_on(client, "webservice")
    fill_consent(client)
    add(client, "text_input")                                        # 2
    client.post(E + "/items/2", data={"code": "city", "question": "Ihre Stadt?"})
    client.post(E + "/blocks")                                       # block 3
    add(client, "info", block=3)                                     # 3
    client.post(E + "/items/3", data={"code": "outro", "question": "In {{city}} sind es {{ws1_temp}} {{ws1_unit}}."})
    r = client.post(E + "/flow/add", data={"kind": "ws", "target": "k2", "mode": "after"})
    assert r.status_code in (302, 303)
    with app.app_context():
        nid = flow.walk_nodes(db.session.get(Survey, 1), "ws")[0]["id"]
    r = client.post(f"{E}/flow/{nid}/update", data=node_form(), follow_redirects=True)
    assert r.status_code == 200
    return nid


def start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"}, follow_redirects=True)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING
        return s.code


def test_participant_passes_the_web_service_and_sees_its_value(app, client, survey, answers):
    nid = build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [n["t"] for n in flow.tree(s)["children"]][:4] == ["block", "block", "ws", "block"]
        assert not [m for sev, m, _ in flow.check(s) if "Web Service" in m and sev == "error"]
    page = client.get("/surveys/1/flow/").data.decode()
    assert "Web Service" in page and "api.open-meteo.com" in page and "{{ws1_temp}}" in page
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    page = p.post(f"/s/{uid}", data={"nav": "next", "i2": "Aachen Mitte"}, follow_redirects=True).data.decode()
    assert "In Aachen Mitte sind es 13,8 °C." in page or "In Aachen Mitte sind es 13.8 °C." in page
    assert answers[0][:2] == ("GET", "https://api.open-meteo.com/v1/forecast?city=Aachen%20Mitte&current=temperature_2m")
    p.post(f"/s/{uid}", data={"nav": "next"})                        # once only: back and forth calls no more
    assert len(answers) == 1
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        assert r.status == Response.COMPLETED and r.external[webservice.KEY][nid]["v"] == {"temp": 13.8, "unit": "°C"}
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig"))))
    assert (rows[-1]["ws1_ok"], rows[-1]["ws1_temp"], rows[-1]["ws1_unit"]) == ("1", "13.8", "°C")
    book = {r[0]: r for r in csv.reader(io.StringIO(client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert "ws1_temp" in book and "current.temperature_2m" in " ".join(book["ws1_temp"])
    privacy = app.test_client().get(f"/s/{uid}/privacy").data.decode()
    assert "Web Service auf (api.open-meteo.com)" in privacy


def test_a_page_sent_by_the_script_shows_the_value_of_the_call(app, client, survey, answers):
    """participant.js sends the field "_direct" and gets the next page in the answer to the POST, but the call runs after
    that answer: the page held the text for an empty value, on the live survey only, the preview redirects (2026-11-05)."""
    build(app, client)
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data={**CONSENT_OK, "_direct": "1"})
    sent = p.post(f"/s/{uid}", data={"nav": "next", "i2": "Aachen", "pos": page_pos(p, 1), "_direct": "1"})
    assert sent.status_code in (302, 303)
    page = p.get(sent.headers["Location"]).data.decode()
    assert "In Aachen sind es 13,8 °C." in page or "In Aachen sind es 13.8 °C." in page
    assert len(answers) == 1


def test_the_call_gets_what_was_typed_whole_and_a_text_of_a_page_the_short_form(app, client, survey, answers):
    """A text of a page takes one line of at most 200 characters (items.base.typed_text), the web service all of it,
    line breaks too: a field of 300 characters reached OpenAI cut at 200 (Philipp 2026-11-05)."""
    build(app, client)
    client.post(E + "/items/2", data={"code": "city", "question": "Ihre Stadt?", "multiline": "1"})
    uid = start(app, client)
    typed = "Aachen " * 60 + "\nEnde"
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    page = p.post(f"/s/{uid}", data={"nav": "next", "i2": typed}, follow_redirects=True).data.decode()
    assert webservice.fill_url("{{city}}", {"city": typed}) in answers[0][1]
    assert "Ende" not in page and "…" in page


def test_a_long_text_of_the_answer_stays():
    assert len(webservice._as("x" * 1500, "text")) == 1500
    assert len(webservice._as("x" * 3000, "text")) == webservice.MAX_TEXT


def build_loop(app, client, **loop):
    """consent | loop over two rows: the question city, the web service, a page with its value and the column"""
    nid = build(app, client)
    client.post(E + "/items/3", data={"code": "outro", "question": "{{city}} {{distance}}: {{ws1_temp}} {{ws1_unit}}."})
    with app.app_context():
        s = db.session.get(Survey, 1)
        node = {**copy.deepcopy(flow.find(flow.tree(s), nid)[0]), "url": "https://api.open-meteo.com/v1/forecast?city={{city}}&d={{distance}}"}
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1},
            {"t": "loop", "id": "l1", "name": "Durchgänge", "code": "loop1", "mode": "all", "k": 1, "order": "fixed", "balanced": True,
             "columns": [{"id": "c1", "name": "distance", "hidden": False}],
             "rows": [{"id": "r1", "values": {"c1": {"de": "nah", "en": "near"}}}, {"id": "r2", "values": {"c1": {"de": "fern", "en": "far"}}}],
             "children": [{"t": "block", "block": 2}, node, {"t": "block", "block": 3}], **loop}]}
        flow.normalize(s)
        db.session.commit()
    return nid


def test_a_web_service_runs_every_pass_of_a_loop(app, client, survey, monkeypatch):
    """One node for all passes, with the column and the answer of the pass; each pass keeps its values, the text shows
    the last call, also when that one failed, and the export has every pass (Philipp, 2026-11-05)."""
    nid = build_loop(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not [m for level, m, _ in flow.check(s) if level == "error"]
    assert "Spalte distance" in client.get("/surveys/1/flow/").data.decode()
    calls = []
    answers = iter([b'{"current": {"temperature_2m": 11}, "current_units": {"temperature_2m": "C"}}', b"no json"])

    def fetch(method, url, body=None, headers=None, timeout=None):
        calls.append(url)
        return 200, next(answers)

    monkeypatch.setattr(webservice, "fetch", fetch)
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    page = p.post(f"/s/{uid}", data={"nav": "next", "i2": "Aachen"}, follow_redirects=True).data.decode()
    assert "Aachen nah: 11 C." in page
    p.post(f"/s/{uid}", data={"nav": "next"}, follow_redirects=True)
    page = p.post(f"/s/{uid}", data={"nav": "next", "i2": "Bonn"}, follow_redirects=True).data.decode()
    assert "Bonn fern:" in page and "11" not in page.split("Bonn fern:")[1][:20]
    assert [c.split("?")[1] for c in calls] == ["city=Aachen&d=nah", "city=Bonn&d=fern"]
    p.post(f"/s/{uid}", data={"nav": "next"})
    assert len(calls) == 2
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        kept = r.external[webservice.KEY]
        assert r.status == Response.COMPLETED and kept[f"{nid}#r1"]["ok"] == 1 and kept[f"{nid}#r2"]["ok"] == 0
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig"))))
    assert (rows[-1]["ws1_ok_1"], rows[-1]["ws1_temp_1"], rows[-1]["ws1_ok_2"], rows[-1]["ws1_temp_2"]) == ("1", "11", "0", "")
    book = {r[0] for r in csv.reader(io.StringIO(client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert {"ws1_ok_2", "ws1_temp_1"} <= book and "ws1_ok" not in book
    with app.app_context():
        from app import export

        s = db.session.get(Survey, 1)
        long = list(csv.DictReader(io.StringIO(export.loop_long_csv(s, flow.find(flow.tree(s), "l1")[0]).lstrip("\ufeff"))))
    assert [(r["distance"], r["ws1_ok"], r["ws1_temp"]) for r in long] == [("nah", "1", "11"), ("fern", "0", "")]


def test_a_web_service_runs_every_vignette_of_a_deck(app, client, survey, answers):
    nid = build_loop(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        tree = copy.deepcopy(flow.tree(s))
        loop = flow.find(tree, "l1")[0]
        loop.update(source="factors", order="plan", plan={"method": "full", "per_person": 2, "decks": 1}, prohibitions=[],
                    attributes=[{"id": "a1", "code": "distance", "levels": [{"id": "d0", "hint": ""}, {"id": "d1", "hint": ""}]}],
                    texts={"de": {"attributes": {"a1": "Abstand"}, "levels": {"d0": "nah", "d1": "fern"}}})
        loop["children"][1]["url"] = "https://api.open-meteo.com/v1/forecast?city={{city}}&d={{distance}}&n={{distance_code}}"
        vignettes.generate(s, loop, seed=1)
        vignettes.materialize(s, loop)
        s.flow = tree
        flow.normalize(s)
        db.session.commit()
        s = db.session.get(Survey, 1)
        assert not [m for level, m, _ in flow.check(s) if level == "error"]
        rows = [r["id"] for r in flow.find(flow.tree(s), "l1")[0]["rows"]]
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    for town in ("Aachen", "Bonn"):
        page = p.post(f"/s/{uid}", data={"nav": "next", "i2": town}, follow_redirects=True).data.decode()
        assert f"{town} " in page and "13" in page
        p.post(f"/s/{uid}", data={"nav": "next"}, follow_redirects=True)
    assert sorted(a[1].split("&", 1)[1].split("&current")[0] for a in answers) == ["d=fern&n=2", "d=nah&n=1"]
    with app.app_context():
        from app import export

        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        assert sorted(k.split("#")[1] for k in r.external[webservice.KEY]) == sorted(rows)
        assert nid in next(iter(r.external[webservice.KEY]))
        s = db.session.get(Survey, 1)
        long = list(csv.DictReader(io.StringIO(export.vignettes_long_csv(s, flow.find(flow.tree(s), "l1")[0]).lstrip("\ufeff"))))
    assert [row["ws1_ok"] for row in long] == ["1", "1"]


def test_a_web_service_alone_in_a_loop_or_in_loops_inside_each_other_is_refused(app, client, survey, answers):
    build_loop(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        tree = copy.deepcopy(flow.tree(s))
        loop = flow.find(tree, "l1")[0]
        service = loop["children"][1]
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2}, {"t": "block", "block": 3},
                                            {**loop, "children": [service]}]}
        flow.normalize(s)
        assert any("fehlt ein Block" in m for level, m, _ in flow.check(s) if level == "error")
        inner = {**loop, "id": "l2", "code": "loop2", "children": [{"t": "block", "block": 3}, service]}
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2}, {**loop, "children": [inner]}]}
        flow.normalize(s)
        assert any("Schleifen ineinander" in m for level, m, _ in flow.check(s) if level == "error")


def test_a_failed_call_lets_the_person_go_on(app, client, survey, monkeypatch):
    build(app, client)

    def down(*a, **k):
        raise OSError("timed out")

    monkeypatch.setattr(webservice, "fetch", down)
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    page = p.post(f"/s/{uid}", data={"nav": "next", "i2": "Aachen"}, follow_redirects=True).data.decode()
    assert "In Aachen sind es" in page                                # on to the next page, values empty
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        entry = next(iter(r.external[webservice.KEY].values()))
        assert entry["ok"] == 0 and "timed out" in entry["err"]


def test_only_for_whom_it_is_switched_on_and_the_form_refuses(app, client, survey):
    fill_consent(client)
    assert client.post(E + "/flow/add", data={"kind": "ws"}).status_code == 403
    experiments_on(client, "webservice")
    client.post(E + "/flow/add", data={"kind": "ws"})
    with app.app_context():
        nid = flow.walk_nodes(db.session.get(Survey, 1), "ws")[0]["id"]

    def save(**kw):
        return client.post(f"{E}/flow/{nid}/update", data=node_form(**kw), follow_redirects=True).data.decode()

    assert "nur https" in save(url="http://api.example/x")
    assert "Feld in der Antwort" in save(values=(("temp", "a..b", "number"),))
    assert "„ok“" in save(values=(("ok", "a", "number"),))
    assert "Kopfzeile" in save(header="Host")
    save(header="X-Api-Key", secret="geheim123")
    with app.app_context():
        node = flow.walk_nodes(db.session.get(Survey, 1), "ws")[0]
        assert node["header"] == "X-Api-Key" and node["secret"] and "geheim123" not in json.dumps(node)
    body = client.get("/surveys/1/export/survey.json").get_data(as_text=True)
    assert "geheim123" not in body and '"secret_set": true' in body


def test_placeholders_go_in_encoded():
    assert webservice.fill_url("https://x.example/?q={{a}}&b={{b|leer}}", {"a": "ä & ö", "b": ""}) == \
        "https://x.example/?q=%C3%A4%20%26%20%C3%B6&b=leer"
    assert json.loads(webservice.fill_body('{"q": "{{a}}"}', {"a": 'say "hi"\n'})) == {"q": 'say "hi"\n'}
    assert webservice.pick({"results": [{"lat": 1.5}]}, "results.0.lat") == 1.5 and webservice.pick({}, "a.b") is None


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "192.168.0.5", "172.18.0.7", "169.254.169.254", "::1", "fd00::1"])
def test_internal_addresses_are_never_called(monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(family, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(webservice.Blocked):
        webservice.fetch("GET", "https://harmless.example/")
    with pytest.raises(webservice.Blocked):
        webservice.fetch("GET", "http://8.8.8.8/")
    result = webservice.call({"url": "https://harmless.example/", "values": [{"name": "x", "path": "x", "kind": "text"}]}, {})
    assert result["ok"] == 0 and result["err"].startswith("blocked")


def test_dot_segments_from_participants_stay_out_of_the_path():
    """Audit 2026-10-13, IN-1: "?a=..&b=admin" made https://api.example/v1/../admin, a path the researchers did not
    give, called with their key in the header. In the query a dot stays a value."""
    filled = webservice.fill_url("https://api.example/v1/{{a}}/{{b}}?x={{a}}", {"a": "..", "b": "admin"})
    assert filled == "https://api.example/v1//admin?x=.."
    assert webservice.fill_url("https://api.example/{{a}}/x", {"a": "."}) == "https://api.example//x"
    assert webservice.fill_url("https://api.example/{{a}}", {"a": "a.b"}) == "https://api.example/a.b"


def test_ipv4_compatible_and_other_special_ipv6_ranges_are_not_public():
    """Audit 2026-10-13, IN-2: ::127.0.0.1 (::/96) counted as public; also documentation and SRv6 ranges."""
    import ipaddress

    for text in ("::127.0.0.1", "::10.0.0.1", "::8.8.8.8", "3fff::1", "5f00::1"):
        assert not webservice.public(ipaddress.ip_address(text)), text
    assert webservice.public(ipaddress.ip_address("2a00:1450:4001::1"))
    assert webservice.public(ipaddress.ip_address("8.8.8.8"))


def test_a_placeholder_outside_quotes_takes_only_a_json_scalar():
    """Audit 2026-10-13, IN-3: {"n": {{a|1}}} with a = [1,2,{}] put a list into the body."""
    body = '{"n": {{a|1}}, "m": {{b}}, "k": {{c}}, "s": "{{a}}"}'
    assert json.loads(webservice.fill_body(body, {"a": "[1,2,{}]", "b": "2.5"})) == \
        {"n": "[1,2,{}]", "m": 2.5, "k": None, "s": "[1,2,{}]"}
    assert json.loads(webservice.fill_body(body, {"a": "true", "b": "-3e2"})) == \
        {"n": True, "m": -300.0, "k": None, "s": "true"}
    assert json.loads(webservice.fill_body('{"q": "\\"{{a}}\\""}', {"a": "x"})) == {"q": '"x"'}  # escaped quotes


def test_answers_with_odd_numbers_or_deep_nesting_are_a_failed_call(monkeypatch):
    """NaN, Infinity, "1e400" and JSON nested deeper than the parser goes give no value and no error page, in the
    participants' call and in the editor's test call (audit 2026-10-28, F01)."""
    node = {"url": URL, "method": "GET", "values": [{"name": "temp", "path": "v", "kind": "number"}]}
    answers = iter([b'{"v": NaN}', b'{"v": Infinity}', b'{"v": "1e400"}', b'{"v": "nan"}', b'{"v": 1e16}',
                    b'[' * 100_000 + b']' * 100_000, b'{"v": 12.5}'])
    monkeypatch.setattr(webservice, "fetch", lambda *a, **k: (200, next(answers)))
    got = [webservice.call(node, {}) for _ in range(6)]
    assert [g["ok"] for g in got] == [0] * 6 and all(g["v"].get("temp", "") == "" for g in got)
    assert webservice.call(node, {})["v"] == {"temp": 12.5}
    monkeypatch.setattr(webservice, "fetch", lambda *a, **k: (200, b'[' * 100_000 + b']' * 100_000))
    out = webservice.test(node, {})
    assert out["err"] and out["v"] == {}


def test_the_time_limit_of_a_call():
    """Default 5 seconds; a node sets its own up to MAX_TIMEOUT (below nginx's 60 s for a participant's request)."""
    assert webservice.time_limit({}) == webservice.TIMEOUT == 5
    assert (webservice.time_limit({"timeout": 30}), webservice.time_limit({"timeout": 46}),
            webservice.time_limit({"timeout": 0}), webservice.time_limit({"timeout": True})) == (30, 5, 5, 5)
    assert webservice.MAX_TIMEOUT < 60


@pytest.mark.parametrize("raw, expected", [("1", 1), (" 30 ", 30), ("45", 45), ("46", None), ("0", None), ("", None),
                                           ("1.5", None), ("²", None)])
def test_the_time_limit_of_the_form(raw, expected):
    assert webservice.read_time_limit(raw) == expected


def test_the_call_gets_the_limit_of_its_node(monkeypatch):
    seen = []

    def fetch(method, url, body, headers, timeout):
        seen.append(timeout)
        return 200, b"{}"

    monkeypatch.setattr(webservice, "fetch", fetch)
    webservice.call({"url": "https://api.example/x", "timeout": 30, "values": []}, {})
    webservice.call({"url": "https://api.example/x", "values": []}, {})
    assert seen == [30, 5]


def test_the_limit_is_saved_and_a_wrong_one_refused(app, client, survey):
    nid = build(app, client)
    form = node_form()
    form["timeout"] = "30"
    client.post(f"{E}/flow/{nid}/update", data=form, follow_redirects=True)
    with app.app_context():
        assert flow.find(flow.tree(db.session.get(Survey, 1)), nid)[0]["timeout"] == 30
    form["timeout"] = "99"
    page = client.post(f"{E}/flow/{nid}/update", data=form, follow_redirects=True).data.decode()
    assert "Zeitlimit" in page
    with app.app_context():
        assert flow.find(flow.tree(db.session.get(Survey, 1)), nid)[0]["timeout"] == 30
    assert 'name="timeout"' in client.get("/surveys/1/flow/").data.decode()


def test_a_page_waits_as_long_as_the_web_services_may_take(app, client, survey):
    """No "send again" before the time limit of a web service is over; the button shows the wait, no note beside it."""
    nid = build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert webservice.wait_seconds(s) == 5
    form = node_form()
    form["timeout"] = "30"
    client.post(f"{E}/flow/{nid}/update", data=form, follow_redirects=True)
    uid = start(app, client)
    p = app.test_client()
    page = p.get(f"/s/{uid}").data.decode()
    assert 'data-wait-seconds="30"' in page and "Einen Moment bitte" not in page


def test_no_wait_note_without_a_web_service(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "a", "question": "Frage"})
    uid = start(app, client)
    assert "data-wait-seconds" not in app.test_client().get(f"/s/{uid}").data.decode()

