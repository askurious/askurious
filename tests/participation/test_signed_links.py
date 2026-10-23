"""Signed completion links: {{sig}} is an HMAC over the filled address before its parameter (app/panel.py)."""
import base64
import hashlib
import hmac
from urllib.parse import quote

from werkzeug.datastructures import MultiDict

from app import panel
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK
from tests.participation.test_end_links import build, run, start

KEY = "s3cret-Key/äö"
LINK = "https://panel.example/done?RIS=10&RID={{PID}}&hash={{sig}}"


def settings(client, link=LINK, key=KEY, alg="sha256", enc="hex", base="amp", params=(("PID", "redirect"),), **more):
    data = MultiDict([("redirect_completed", link), ("sign_alg", alg), ("sign_enc", enc), ("sign_base", base),
                      ("sign_key", key)] + list(more.items()))
    for name, mode in params:
        for field, value in (("param_name", name), ("param_mode", mode), ("param_required", "0"), ("param_allowed", "")):
            data.add(field, value)
    return client.post("/surveys/1/panel", data=data, follow_redirects=True).data.decode()


def mac(text, alg=hashlib.sha256, key=KEY):
    return hmac.new(key.encode(), text.encode(), alg).digest()


def test_key_is_stored_encrypted_and_never_shown(app, client, survey):
    build(app, client)
    html = settings(client)
    assert "gespeichert" in html and KEY not in html and "ist gespeichert und wird nicht angezeigt" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        stored = s.panel["sign"]
        assert stored["key"] and KEY not in str(s.panel) and panel.signs(s)
        assert (stored["alg"], stored["enc"], stored["base"]) == ("sha256", "hex", "amp")
    # an empty field keeps the key; the other choices change
    assert "gespeichert" in settings(client, key="", alg="sha1", enc="base64url", base="plain")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.panel["sign"]["key"] and panel.sign_config(s)["alg"] == "sha1"
        assert panel.signature(panel.sign_config(s), "abc") == base64.urlsafe_b64encode(
            mac("abc", hashlib.sha1)).decode().rstrip("=")
    # a form of another part of the page (no sign fields) leaves everything
    client.post("/surveys/1/panel", data={"redirect_completed": LINK})
    with app.app_context():
        assert panel.signs(db.session.get(Survey, 1))
    # the tick removes the key; the link with {{sig}} is refused then
    html = settings(client, key="", sign_key_remove="1")
    assert "braucht einen Signaturschlüssel" in html
    settings(client, link="https://panel.example/done?RID={{PID}}", key="", sign_key_remove="1")
    with app.app_context():
        assert not panel.signs(db.session.get(Survey, 1))


def test_link_is_signed_up_to_the_signature_parameter(app, client, survey):
    build(app, client)
    assert "gespeichert" in settings(client)
    uid = start(app, client)
    p = run(app, uid, 30, query="?PID=a b%26c")                                              # a value that needs encoding
    p.post(f"/s/{uid}", data={"nav": "next"})
    target = p.get(f"/s/{uid}").headers["Location"]
    signed = "https://panel.example/done?RIS=10&RID=a%20b%26c&"
    assert target == signed + "hash=" + mac(signed).hex()
    with app.app_context():
        s = db.session.get(Survey, 1)
        sign = panel.sign_config(s)
        values = {"PID": "x1"}
        plain = "https://panel.example/done?RIS=10&RID=x1"
        assert panel._filled(LINK, values, {**sign, "base": "plain"}) == plain + "&hash=" + mac(plain).hex()
        assert panel._filled(LINK, values, {**sign, "alg": "sha1"}) == plain + "&hash=" + mac(plain + "&", hashlib.sha1).hex()
        b64 = panel._filled(LINK, values, {**sign, "enc": "base64"})
        assert b64 == plain + "&hash=" + quote(base64.b64encode(mac(plain + "&")).decode(), safe="")
        # the signature as the first parameter, and parameters behind it (not signed, kept)
        first = panel._filled("https://panel.example/done?h={{sig}}&RID={{PID}}#end", values, sign)
        assert first == "https://panel.example/done?h=" + mac("https://panel.example/done?").hex() + "&RID=x1#end"
        example = panel.sign_example(s)
        assert example[1] == "https://panel.example/done?RIS=10&RID=TEST&" and example[0].endswith(mac(example[1]).hex())
    assert "Beispiel mit Testwerten" in client.get("/surveys/1/field").data.decode()


def test_where_the_signature_may_stand(app, client, survey):
    build(app, client)
    for bad in ("https://panel.example/{{sig}}/done", "https://panel.example/done?a={{sig}}&b={{sig}}",
                "https://panel.example/done?a=x{{sig}}", "https://panel.example/done?a={{sig}}x"):
        assert "genau einmal vorkommen" in settings(client, link=bad), bad
    assert "höchstens 200 Zeichen" in settings(client, key="k" * 201)
    assert "höchstens 200 Zeichen" in settings(client, key=" key")
    html = settings(client, params=(("sig", "redirect"),))
    assert "URL-Parameter „sig“ umbenennen" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not panel.signs(s)                                                            # nothing was stored
        # without a key a parameter may be called sig, as before
        assert panel.link_error("https://p.example/?s={{sig}}", {"sig"}) is None
        assert "Signaturschlüssel" in panel.link_error("https://p.example/?s={{sig}}", {"PID"})
        assert panel._filled("https://p.example/?s={{sig}}", {"sig": "v"}) == "https://p.example/?s=v"


def test_end_in_the_flow_rules_and_an_unreadable_key(app, client, survey):
    build(app, client, kind="screenout", link="https://panel.example/out?RID={{PID}}&sig={{sig}}")
    settings(client, link="")
    uid = start(app, client)
    target = run(app, uid, 12).get(f"/s/{uid}").headers["Location"]
    signed = "https://panel.example/out?RID=p7&"
    assert target == signed + "sig=" + mac(signed).hex()
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not [m for _, m in panel.check(s)]
        s.panel = {**s.panel, "sign": {**s.panel["sign"], "key": "not-a-token"}}
        assert any("nicht mehr lesbar" in m for _, m in panel.check(s))
        assert panel._filled("https://p.example/?s={{sig}}", {}, panel.sign_config(s)) == "https://p.example/?s="
        db.session.rollback()
    client.post("/surveys/1/duplicate", data={"title": "Zweite Welle"})
    with app.app_context():
        copy = db.session.scalar(db.select(Survey).filter_by(title="Zweite Welle"))
        # the copy has no key: who may edit is its admin and would sign links as the original (tests/platform/test_security_keys.py)
        assert not panel.signs(copy) and panel.signature(panel.sign_config(copy), "abc") == ""


# --- signed entry links (audit 2026-10-18, BIZ-05) ---------------------------------------------------------


def entry_survey(app, client, **more):
    build(app, client)
    assert "gespeichert" in settings(client, params=(("PID", "once"),), sign_entry="1", **more)
    return start(app, client)


def test_signed_entry_link_is_taken_and_an_unsigned_or_wrong_one_is_refused(app, client, survey):
    """BIZ-05: with "Auch den Einstiegslink signieren lassen" only links the panel signed start a participation."""
    uid = entry_survey(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert panel.entry_signed(s) and panel.sign_config(s)["entry_param"] == "sig"
    base = f"http://localhost/s/{uid}"
    good = app.test_client()
    page = good.get(f"/s/{uid}?PID=p7&sig=" + mac(f"{base}?PID=p7&").hex())
    assert page.status_code == 200
    assert good.post(f"/s/{uid}", data={**CONSENT_OK}).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(Response.params)) == {"PID": "p7"}
    # the signature over the part behind the "?" only is taken as well
    assert app.test_client().get(f"/s/{uid}?PID=p8&sig=" + mac("PID=p8&").hex()).status_code == 200
    # without a signature, a wrong one, one for another ID, or a declared parameter behind the signature: refused
    for query in ("?PID=p9", "?PID=p9&sig=00ff", "?PID=p9&sig=" + mac(f"{base}?PID=p7&").hex(),
                  "?sig=" + mac(f"{base}?").hex() + "&PID=p9", ""):
        assert app.test_client().get(f"/s/{uid}{query}").status_code == 400, query


def test_signed_entry_needs_a_key_and_a_free_parameter_name(app, client, survey):
    build(app, client)
    html = settings(client, key="", params=(("PID", "once"),), sign_entry="1")       # no key stored yet
    with app.app_context():
        assert not panel.entry_signed(db.session.get(Survey, 1))
    html = settings(client, params=(("PID", "once"), ("code", "store")), sign_entry="1", sign_entry_param="code")
    assert "schon ein URL-Parameter" in html
    settings(client, params=(("PID", "once"),), sign_entry="1", sign_entry_param="h")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert panel.entry_signed(s) and panel.sign_config(s)["entry_param"] == "h"
        assert panel.entry_link(s, "https://x/s/abc") == "https://x/s/abc?PID=…&h=…"
    del html


def test_quotas_and_conditions_on_unchecked_url_parameters_are_named_in_the_check(app, client, survey):
    """BIZ-05: a condition follows a URL parameter without allowed values and without a signed entry link."""
    import copy

    from app import flow

    build(app, client)
    assert "gespeichert" in settings(client, link="https://panel.example/done?hash={{sig}}", params=(("src", "store"),))
    with app.app_context():
        s = db.session.get(Survey, 1)
        root = copy.deepcopy(flow.tree(s))
        root["children"].append({"t": "branch", "id": "bsrc", "cond": {"item": "p:src", "op": "eq", "value": "a"},
                                 "children": []})
        s.flow = root
        db.session.commit()
        assert panel.steering_params(s) == {"src"}
        assert any("erlaubter Werte" in m for _, m in panel.check(s))
    data = MultiDict([("param_name", "src"), ("param_mode", "store"), ("param_required", "0"), ("param_allowed", "a, b")])
    client.post("/surveys/1/panel", data=data)
    with app.app_context():
        assert not any("erlaubter Werte" in m for _, m in panel.check(db.session.get(Survey, 1)))
