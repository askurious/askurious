"""Keys and signatures (security audit 2026-10-03, package 1): a copy and a survey file carry no signing key (S1),
the return link of an external section is opaque (H1) and runs out (S5), a return in another browser does not open
what came before the section (S5), and the returned values can be signed by the web app (S2)."""
import csv
import hashlib
import hmac
import io
import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from app import external, flow, panel, secrets_box
from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.editor.test_external import E, build, leave, node_form, start, the_response, to_the_section
from tests.exports.test_transfer import _upload as _read_in


def _node(app, survey_id=1):
    with app.app_context():
        return flow.walk_nodes(db.session.get(Survey, survey_id), "ext")[0]


def _key(app, survey_id=1):
    with app.app_context():
        return external.sign_key(_node(app, survey_id))


def _drawn_before(app, survey_id=1):
    """The key of the external section as one drawn before 2026-10-29, which still takes version 1."""
    import copy

    with app.app_context():
        s = db.session.get(Survey, survey_id)
        root = copy.deepcopy(s.flow)
        s.flow = root
        for node in flow.walk_nodes(s, "ext"):
            node["sign"].pop(external.V2_ONLY, None)
        db.session.commit()


def _sign_on(client, nid, **more):
    return client.post(f"{E}/flow/{nid}/update", data=node_form(sign_part="1", sign_values="1", **more),
                       follow_redirects=True)


def _sig(key, text):
    return hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()


def _token(back):
    return parse_qs(urlsplit(back).query)["t"][0]


# --- S1: no key in a copy ------------------------------------------------------------------------


def test_a_copy_does_not_take_the_key_of_the_completion_links(app, client, survey):
    from tests.participation.test_end_links import build as build_ends
    from tests.participation.test_signed_links import KEY, settings

    build_ends(app, client)
    settings(client)
    with app.app_context():
        assert panel.signs(db.session.get(Survey, 1))
    page = client.post("/surveys/1/duplicate", data={"title": "Welle 2", "with_people": "1"},
                       follow_redirects=True).data.decode()
    assert "Signaturschlüssel wurde nicht übernommen" in page
    with app.app_context():
        original, copy = db.session.get(Survey, 1), db.session.get(Survey, 2)
        assert panel.signs(original) and secrets_box.decrypt(original.panel["sign"]["key"]) == KEY
        assert not panel.signs(copy) and copy.panel["sign"]["key"] == ""
        assert panel.sign_config(copy)["alg"] == "sha256"  # how links are signed stays
        # the link with {{sig}} is named by the pre-launch check until a key is entered
        assert any("braucht einen Signaturschlüssel" in m for _, m in panel.check(copy))


def test_a_copy_and_a_file_do_not_take_the_key_of_the_returned_values(app, client, survey):
    nid = build(app, client)
    _sign_on(client, nid)
    key = _key(app)
    assert len(key) >= 40
    data = client.get("/surveys/1/export/survey.json").data.decode()
    assert key not in data and _node(app)["sign"]["key"] not in data
    exported = next(n for n in external.nodes_in(json.loads(data)["flow"]))
    assert exported["sign"] == {"signed": True}
    page = client.post("/surveys/1/duplicate", data={"title": "Welle 2", "with_people": "1"},
                       follow_redirects=True).data.decode()
    assert "Rückgabewerte signieren" in page
    copy = _node(app, 2)
    assert "sign" not in copy and not external.signs(copy) and external.signs(_node(app))
    page = _read_in(client, None, raw=data.encode(), title="Studie B")
    assert "Schlüssel steht nicht in der Datei" in page
    assert "sign" not in _node(app, 3)


# --- H1 and S5: the return link ------------------------------------------------------------------


def test_the_return_link_is_opaque_short_and_runs_out(app, client, survey):
    build(app, client)
    uid = start(app, client)
    p = to_the_section(app, uid)
    target, back = leave(p, uid)
    pid = the_response(app)[3]
    token = _token(back)
    assert len(token) < 160 and pid not in target.split("&return=")[1] and pid not in token
    import base64

    assert pid.encode() not in base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    with app.app_context():
        node = _node(app)
        assert external.read_token(token)[:2] == (pid, node["id"])
        assert external.read_token(token, now=utcnow() + timedelta(seconds=external.MAX_SECONDS - 60)) is not None
        assert external.read_token(token, now=utcnow() + timedelta(seconds=external.MAX_SECONDS + 60)) is None
        # a token of another purpose, or one of the form before (signed, readable), is not read
        assert external.read_token(secrets_box.seal("other", b'["a","b","c",1]')) is None
        from itsdangerous import URLSafeSerializer

        old = URLSafeSerializer(app.config["SECRET_KEY"], salt="external-return").dumps([pid, node["id"], "k"])
        assert external.read_token(old) is None
        # an expired link is refused at the door
        kept = external.state(db.session.scalar(db.select(Response).filter_by(pid=pid)))[node["id"]]
        stale = int((utcnow() - timedelta(days=8) - external._EPOCH).total_seconds())
        expired = secrets_box.seal(external.PURPOSE, json.dumps([pid, node["id"], kept["k"], stale]).encode())
    r = app.test_client().get(f"/s/{uid}/back?t={expired}")
    assert r.status_code == 400 and "nicht gültig" in r.data.decode()
    assert not next(iter(the_response(app)[1].values())).get("back")


def test_another_browser_goes_on_from_the_section_only(app, client, survey):
    build(app, client)
    uid = start(app, client)
    p = to_the_section(app, uid)
    _, back = leave(p, uid)
    elsewhere = app.test_client()
    assert elsewhere.get(back + "&score=3").status_code == 302
    page = elsewhere.get(f"/s/{uid}", follow_redirects=True).data.decode()
    assert "Letzte Seite" in page and 'value="back"' not in page
    elsewhere.post(f"/s/{uid}", data={"nav": "back", "pos": page.split('name="pos" value="')[1].split('"')[0]})
    assert "Letzte Seite" in elsewhere.get(f"/s/{uid}", follow_redirects=True).data.decode()       # still on the last page
    # the privacy page names no deletion code and offers no "delete here"
    privacy = elsewhere.get(f"/s/{uid}/privacy").data.decode()
    assert "Ihr Löschcode" not in privacy
    erase = elsewhere.get(f"/s/{uid}/delete").data.decode()
    assert "In diesem Browser liegt eine Teilnahme" not in erase
    elsewhere.post(f"/s/{uid}/delete", data={"sure": "1", "here": "1"})
    assert the_response(app)[0] == "active"
    # the browser that left keeps everything
    assert "Ihr Löschcode" not in p.get(f"/s/{uid}/privacy").data.decode()
    assert "In diesem Browser liegt eine Teilnahme" in p.get(f"/s/{uid}/delete").data.decode()


def test_the_same_browser_may_go_back_before_the_section(app, client, survey):
    build(app, client)
    uid = start(app, client)
    p = to_the_section(app, uid)
    _, back = leave(p, uid)
    p.get(back + "&score=3")
    page = p.get(f"/s/{uid}", follow_redirects=True).data.decode()
    assert "Letzte Seite" in page and 'value="back"' in page
    p.post(f"/s/{uid}", data={"nav": "back", "pos": page.split('name="pos" value="')[1].split('"')[0]})
    assert "Alter" in p.get(f"/s/{uid}", follow_redirects=True).data.decode()


# --- S2: signed values ---------------------------------------------------------------------------


def test_signing_is_set_by_admins_and_keeps_its_key(app, client, survey):
    nid = build(app, client)
    page = client.get("/surveys/1/flow/").data.decode()
    assert "Rückgabewerte signieren" in page and "Nicht signiert:" in page
    _sign_on(client, nid)
    node = _node(app)
    key = _key(app)
    assert external.signs(node) and key not in json.dumps(node)  # stored encrypted
    page = client.get("/surveys/1/flow/").data.decode()
    assert key in page and "Neuen Schlüssel erzeugen" in page and "Nicht signiert:" not in page
    # off and on again: the same key; "Neuen Schlüssel erzeugen": another one
    client.post(f"{E}/flow/{nid}/update", data=node_form(sign_part="1"))
    assert not external.signs(_node(app)) and _key(app) == key
    _sign_on(client, nid)
    assert _key(app) == key
    _sign_on(client, nid, sign_renew="1")
    assert external.signs(_node(app)) and _key(app) not in ("", key)
    # who is no admin changes nothing of it (the checkbox is not sent then)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert flow.update(s, nid, node_form(sign_part="1"), s.languages, links=False) is None
        assert external.signs(flow.find(flow.tree(s), nid)[0])
    # "sig" is the name of the signature
    assert "Rückgabewert „sig“" in client.post(f"{E}/flow/{nid}/update", data=node_form(values=(("sig", "number"),)),
                                               follow_redirects=True).data.decode()


def test_signed_values_are_kept_only_with_a_valid_signature(app, client, survey, monkeypatch):
    # signed as in version 1 (no sig_v), which is taken until external.V1_UNTIL; version 2 in tests/audits/test_cr8_h.py
    monkeypatch.setattr(external, "V1_UNTIL", datetime(9999, 1, 1).date())
    nid = build(app, client)
    _sign_on(client, nid)
    _drawn_before(app)
    key = _key(app)
    uid = start(app, client)

    def go(query):
        p = to_the_section(app, uid)
        _, back = leave(p, uid)
        t = _token(back)
        r = p.get(back + query(t))
        assert r.status_code == 302                                # the return counts either way
        assert "Letzte Seite" in p.get(f"/s/{uid}", follow_redirects=True).data.decode()
        p.post(f"/s/{uid}", data={"nav": "next"})
        with app.app_context():
            last = db.session.scalars(db.select(Response).filter_by(source="real").order_by(Response.id.desc())).first()
            return last.external[nid]

    ok = go(lambda t: f"&score=17&label=gut%20gemacht&other=1&sig={_sig(key, f't={t}&score=17&label=gut gemacht')}")
    assert ok["v"] == {"score": 17, "label": "gut gemacht"} and ok["sig"] == 1
    upper = go(lambda t: f"&score=5&sig={_sig(key, f't={t}&score=5').upper()}")
    assert upper["v"] == {"score": 5} and upper["sig"] == 1
    changed = go(lambda t: f"&score=999&sig={_sig(key, f't={t}&score=17')}")
    assert changed["v"] == {} and changed["sig"] == 0
    missing = go(lambda t: "&score=17")
    assert missing["v"] == {} and missing["sig"] == 0
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig"))))
    assert sorted((r["ext1_score"], r["ext1_sig"]) for r in rows) == [("", "0"), ("", "0"), ("17", "1"), ("5", "1")]
    book = {r[0]: r for r in csv.reader(io.StringIO(client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert "verworfen" in book["ext1_sig"][3] and "signiert: nur mit gültiger Signatur" in book["ext1_score"][4]
    with app.app_context():
        assert not [m for _, m, _ in flow.check(db.session.get(Survey, 1)) if "nicht signiert" in m]


def test_unsigned_values_say_so_in_the_codebook(app, client, survey):
    build(app, client)
    book = {r[0]: r for r in csv.reader(io.StringIO(client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert "aus dem Browser der Teilnehmenden" in book["ext1_score"][4] and "ext1_sig" not in book


def test_the_signed_text(app):
    node = {"values": [{"name": "score", "kind": "number"}, {"name": "label", "kind": "text"}]}
    # version 1 (taken until external.V1_UNTIL); version 2 in tests/audits/test_cr8_h.py
    assert external.signed_text(node, "TOK", {"label": "a b", "score": "1", "x": "2"}, 1) == "t=TOK&score=1&label=a b"
    assert external.signed_text(node, "TOK", {"label": ""}, 1) == "t=TOK&label="
    with app.app_context():
        assert not external.signature_ok({**node, "sign": {"on": True, "key": "unreadable"}}, {"t": "TOK", "sig": "00"})


def test_version_1_only_for_keys_drawn_before(app, client, survey):
    """A key drawn now takes signatures of version 2 only; a key of before still takes version 1 until V1_UNTIL
    (audit 2026-10-28, S03)."""
    from datetime import datetime

    from app import secrets_box

    nid = build(app, client)
    _sign_on(client, nid)
    with app.app_context():
        node = flow.walk_nodes(db.session.get(Survey, 1), "ext")[0]
        assert external.v2_only(node)
        key = external.sign_key(node)
        old = {**node, "sign": {"on": True, "key": secrets_box.encrypt(key, secrets_box.PARTICIPANT)}}
    args = {"t": "TOK", "score": "5"}
    before = datetime(2027, 1, 1)
    with app.app_context():
        v1 = {**args, "sig": _sig(key, external.signed_text(node, "TOK", args, 1))}
        v2 = {**args, "sig_v": "2", "sig": _sig(key, external.signed_text(node, "TOK", {**args, "sig_v": "2"}))}
        assert not external.signature_ok(node, v1, before) and external.signature_ok(node, v2, before)
        assert external.signature_ok(old, v1, before) and external.signature_ok(old, v2, before)
        assert not external.signature_ok(old, v1, datetime(2027, 4, 1))
