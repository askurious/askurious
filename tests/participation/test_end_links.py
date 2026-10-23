"""Kinds of ends in the flow (screen-out, quota full) and completion links (app/panel.py, app/flow.py)."""
import csv
import io

from werkzeug.datastructures import MultiDict

from app import flow, panel
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"
NAMES = {"PID", "pid"}


def build(app, client, kind="quotafull", link="", link_mode="now", text=None):
    """consent | age | wenn age < 18: Ende (kind) | outro"""
    fill_consent(client)
    add(client, "text_input")                                        # 2
    client.post(E + "/items/2", data={"code": "age", "validation": "integer", "question": "Alter"})
    client.post(E + "/blocks")                                       # block 3
    add(client, "info", block=3)                                     # 3
    client.post(E + "/items/3", data={"code": "outro", "question": "Letzte Seite"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        end = {"t": "end", "id": "e1", "text": text or {}, "kind": kind, "link": link, "link_mode": link_mode}
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "lt", "value": "18"}, "children": [end]},
            {"t": "block", "block": 3}]}
        flow.normalize(s)
        db.session.commit()


def links(client, redirects=None, modes=None, params=(("PID", "redirect"),)):
    data = MultiDict()
    for name, mode in params:
        for key, value in (("param_name", name), ("param_mode", mode), ("param_required", "0"), ("param_allowed", "")):
            data.add(key, value)
    for outcome, url in (redirects or {}).items():
        data.add(f"redirect_{outcome}", url)
    for outcome, mode in (modes or {}).items():
        data.add(f"mode_{outcome}", mode)
    return client.post("/surveys/1/panel", data=data, follow_redirects=True)


def start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def run(app, uid, age, query="?PID=p7"):
    p = app.test_client()
    p.get(f"/s/{uid}{query}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": str(age)})
    return p


# --- the address check ----------------------------------------------------------------------------------


def test_link_error_accepts_what_panels_use():
    for url in ("https://panel.example/done?id={{PID}}&status=complete", "https://app.prolific.com/submissions/complete?cc=C0DE1",
                "https://panel.example/r/{{PID}}/{{pid}}#end", "http://localhost:5000/back?x={{PID}}", "http://127.0.0.1/x", ""):
        assert panel.link_error(url, NAMES) is None, url


def test_link_error_refuses_what_could_lead_elsewhere():
    refused = {
        "https://{{PID}}/done": "Rechnernamen",                       # the participant's link would pick the host
        "https://panel.example.{{PID}}/done": "Rechnernamen",
        "https://{{PID}}@panel.example/": "Rechnernamen",
        "https://user:pw@panel.example/": "Zugangsdaten",
        "http://panel.example/done": "nur https",
        "javascript:alert(1)": "https://",
        "//panel.example/done": "https://",
        "panel.example/done": "https://",
        "https://panel.example\\evil.example/": "Schrägstrich",
        "https://panel.example/a b": "Leerzeichen",
        "https://panel.example/?x={{nope}}": "kein deklarierter Parameter",
        "https://panel.example/?x={{PID": "nicht lesbar",
    }
    for url, word in refused.items():
        assert word in (panel.link_error(url, NAMES) or ""), url


def test_stored_link_with_placeholder_in_the_host_is_never_followed(app, client, survey):
    """A link stored before the check existed: the end page stays instead of an open redirect."""
    build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {"params": [{"name": "PID", "mode": "redirect", "required": False, "allowed": []}],
                   "redirects": {"completed": "https://{{PID}}/done", "quotafull": "https://panel.example\\@{{PID}}/"}}
        db.session.commit()
        assert panel.target(s, "completed", {"PID": "evil.example"}) == (None, None)
        assert panel.target(s, "quotafull", {"PID": "evil.example"}) == (None, None)
        errors = [f.message for f in __import__("app.design", fromlist=["check"]).check(s) if f.severity == "error"]
        assert any("Weiterleitung „abgeschlossen“" in m and "Rechnernamen" in m for m in errors)
        s.panel = {**s.panel, "redirects": {"completed": "https://{{PID}}/done"}}
        s.flow = flow.tree(s)
        db.session.commit()
    assert "Noch nicht startklar" in client.get("/surveys/1/field").data.decode()          # the start is blocked
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status, s.opened_at = Survey.RUNNING, s.created_at                                # as if it ran already
        db.session.commit()
        uid = s.code
    p = run(app, uid, 30, "?PID=evil.example")
    p.post(f"/s/{uid}", data={"nav": "next"})
    r = p.get(f"/s/{uid}")
    assert r.status_code == 200 and "Vielen Dank für Ihre Teilnahme" in r.data.decode()


def test_settings_refuse_bad_links_and_keep_the_form(app, client, survey):
    html = links(client, {"completed": "https://{{PID}}/done"}).data.decode()
    assert "Rechnernamen" in html and "https://{{PID}}/done" in html
    html = links(client, {"completed": "https://panel.example/?x={{other}}"}).data.decode()
    assert "kein deklarierter Parameter" in html
    with app.app_context():
        assert not (db.session.get(Survey, 1).panel or {}).get("redirects")
    html = links(client, {"completed": "https://panel.example/?x={{PID}}&p={{pid}}", "quotafull": "https://panel.example/full"},
                 {"quotafull": "button", "completed": "now"}).data.decode()
    assert "Teilnahmelinks gespeichert" in html
    with app.app_context():
        cfg = panel.config(db.session.get(Survey, 1))
        assert cfg["redirects"]["quotafull"] == "https://panel.example/full" and cfg["modes"] == {"quotafull": "button"}


# --- kinds of ends --------------------------------------------------------------------------------------


def test_end_of_kind_quota_full_sets_status_text_and_link(app, client, survey):
    links(client, {"completed": "https://panel.example/r?id={{PID}}&s=complete",
                   "quotafull": "https://panel.example/r?id={{PID}}&s=quotafull",
                   "screenout": "https://panel.example/r?id={{PID}}&s=screenout"})
    build(app, client, kind="quotafull")
    uid = start(app, client)
    young = run(app, uid, 16)
    r = young.get(f"/s/{uid}")
    assert r.status_code == 302 and r.headers["Location"] == "https://panel.example/r?id=p7&s=quotafull"
    done = run(app, uid, 40)
    done.post(f"/s/{uid}", data={"nav": "next"})
    assert done.get(f"/s/{uid}").headers["Location"] == "https://panel.example/r?id=p7&s=complete"
    with app.app_context():
        assert sorted(r.status for r in db.session.scalars(db.select(Response))) == ["completed", "quotafull"]
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig"))))
    full = next(r for r in rows if r["status"] == "quotafull")
    assert full["finished"] and full["age"] == "16"
    assert "quotafull" in client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "<strong>1</strong> Quote voll" in client.get("/surveys/1/data").data.decode()


def test_quota_full_standard_text_own_text_and_survey_text(app, client, survey):
    build(app, client, kind="quotafull")
    uid = start(app, client)
    html = run(app, uid, 16).get(f"/s/{uid}").data.decode()
    assert "bereits genügend Teilnahmen" in html and "andere Zielgruppe" not in html
    client.post("/surveys/1/end-texts", data={"quotafull_de": "**Voll.** Danke trotzdem."})
    assert "Danke trotzdem" in run(app, uid, 16).get(f"/s/{uid}").data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        root = __import__("copy").deepcopy(flow.tree(s))
        flow.find(root, "e1")[0]["text"] = {"de": "Eigener Text des Endes"}
        s.flow = root
        db.session.commit()
    assert "Eigener Text des Endes" in run(app, uid, 16).get(f"/s/{uid}").data.decode()


def test_screenout_stays_as_it_was(app, client, survey):
    build(app, client, kind="screenout")
    uid = start(app, client)
    html = run(app, uid, 16).get(f"/s/{uid}").data.decode()
    assert "andere Zielgruppe" in html
    with app.app_context():
        assert db.session.scalar(db.select(Response.status)) == "screenout"
        node = {"t": "end", "id": "old", "text": {}}                                       # an end stored before kinds existed
        assert flow.end_kind(node) == "screenout" and flow.child_label(None, node) == "Ende"


def test_link_of_the_end_goes_before_the_link_of_the_outcome(app, client, survey):
    links(client, {"quotafull": "https://panel.example/standard"})
    build(app, client, kind="quotafull", link="https://other.example/full?id={{PID}}&p={{pid}}")
    uid = start(app, client)
    r = run(app, uid, 16).get(f"/s/{uid}")
    with app.app_context():
        pid = db.session.scalar(db.select(Response.pid))
    assert r.status_code == 302 and r.headers["Location"] == f"https://other.example/full?id=p7&p={pid}"


def test_end_page_with_button_and_automatic_forward(app, client, survey):
    links(client, {"completed": "https://panel.example/done?id={{PID}}"}, {"completed": "auto"})
    build(app, client, kind="screenout", link="https://panel.example/out?id={{PID}}", link_mode="button")
    with app.app_context():  # the deletion code is off by default since the pre-test 2026-10-16
        from app.extensions import db as _db
        from app.models import Survey as _Survey

        _db.session.get(_Survey, 1).erase_code = True
        _db.session.commit()
    uid = start(app, client)
    r = run(app, uid, 16, "?PID=a%22b").get(f"/s/{uid}")
    html = r.data.decode()
    assert r.status_code == 200 and "andere Zielgruppe" in html                            # the text is shown first
    assert 'href="https://panel.example/out?id=a%22b"' in html and "http-equiv" not in html
    done = run(app, uid, 40)
    done.post(f"/s/{uid}", data={"nav": "next"})
    html = done.get(f"/s/{uid}").data.decode()
    # with a deletion code on the page nothing moves on by itself: the code has to be noted first (UI audit R60)
    assert "p-erase-code" in html and "data-forward-after" not in html and "http-equiv" not in html
    with app.app_context():
        db.session.get(Survey, 1).erase_code = False
        db.session.commit()
    done = run(app, uid, 41)
    done.post(f"/s/{uid}", data={"nav": "next"})
    html = done.get(f"/s/{uid}").data.decode()
    # the countdown runs in participant.js and can be stopped; the meta refresh only for browsers without scripts
    assert '<noscript><meta http-equiv="refresh" content="5;url=https://panel.example/done?id=p7"></noscript>' in html
    assert 'data-forward-after="5"' in html and "data-forward-stop" in html and "Anhalten" in html
    assert "in 5 Sekunden weitergeleitet" in html and "Vielen Dank für Ihre Teilnahme" in html


def test_flow_page_edits_kind_and_link_of_an_end(app, client, survey):
    links(client)
    build(app, client, kind="screenout")
    base = {"text_de": "Schade", "text_en": ""}
    html = client.post(E + "/flow/e1/update", data={**base, "kind": "quotafull", "link": "https://{{PID}}/x"},
                       follow_redirects=True).data.decode()
    assert "Abschlusslink" in html and "Rechnernamen" in html
    client.post(E + "/flow/e1/update", data={**base, "kind": "quotafull", "link": "https://panel.example/x?i={{PID}}",
                                              "link_mode": "button"})
    with app.app_context():
        node, _ = flow.find(flow.tree(db.session.get(Survey, 1)), "e1")
        assert (node["kind"], node["link"], node["link_mode"]) == ("quotafull", "https://panel.example/x?i={{PID}}", "button")
    html = client.get("/surveys/1/flow/").data.decode()
    assert "Ende (Quote voll)" in html and "eigener Abschlusslink: panel.example" in html


def test_preview_names_the_link_without_following_it(app, client, survey):
    links(client, {"quotafull": "https://panel.example/full?p={{pid}}"})
    build(app, client, kind="quotafull")
    client.get(E + "/preview?restart=1")
    client.post(E + "/preview", data=CONSENT_OK)
    client.post(E + "/preview", data={"nav": "next", "i2": "12"})
    r = client.get(E + "/preview")
    html = r.data.decode()
    assert r.status_code == 200 and "Quote voll" in html and "https://panel.example/full?p=" in html
    assert "in der Vorschau nicht aufgerufen" in html


def test_privacy_page_names_where_participants_are_sent(app, client, survey):
    links(client)
    build(app, client)
    assert "andere Website" not in client.get("/s/1/privacy").data.decode()
    client.post(E + "/flow/e1/update", data={"kind": "quotafull", "link": "https://other.example/full"})
    links(client, {"completed": "https://panel.example/done?id={{PID}}"})
    with app.app_context():
        assert panel.link_hosts(db.session.get(Survey, 1)) == ["panel.example", "other.example"]
    html = client.get("/s/1/privacy").data.decode()
    assert "panel.example, other.example" in html and "keine Ihrer Antworten" in html
    assert "forwarded to another website (panel.example, other.example)" in client.get("/s/1/privacy?lang=en").data.decode()


def test_changing_links_in_the_field_is_noted(app, client, survey):
    links(client, {"completed": "https://panel.example/a"})
    build(app, client)
    start(app, client)
    links(client, {"completed": "https://panel.example/a"})                                # unchanged: no note
    with app.app_context():
        assert [e["event"] for e in db.session.get(Survey, 1).status_log] == ["start"]
    links(client, {"completed": "https://panel.example/b"})
    with app.app_context():
        assert [e["event"] for e in db.session.get(Survey, 1).status_log] == ["start", "links"]
    html = client.get("/surveys/1/field").data.decode()
    assert "Abschlusslinks geändert" in html and "Läuft</strong> seit" in html                     # a note, no change of state


def test_methods_and_duplicate_know_the_new_end(app, client, survey):
    links(client, {"quotafull": "https://panel.example/full"}, {"quotafull": "button"})
    build(app, client, kind="quotafull", link="https://other.example/x")
    uid = start(app, client)
    run(app, uid, 16).get(f"/s/{uid}")
    from app import methods

    with app.app_context():
        s = db.session.get(Survey, 1)
        f = methods.facts(s)
        assert f["quotafull"] == 1 and f["incomplete"] == 0 and f["screenout"] == 0
    client.post("/surveys/1/duplicate", data={"title": "Zweite Welle"})
    with app.app_context():
        twin = db.session.scalar(db.select(Survey).filter_by(title="Zweite Welle"))
        node = next(n for n, _, _ in flow.walk(flow.tree(twin)) if n["t"] == "end")
        assert node["kind"] == "quotafull" and node["link"] == "https://other.example/x"
        assert panel.config(twin)["modes"] == {"quotafull": "button"}
