import csv
import io

from werkzeug.datastructures import MultiDict

from app import flow
from app.extensions import db
from app.models import ContactEntry, Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions

E = "/surveys/1/editor"


def configure(client, redirects=None, allowed="bio, chemie"):
    data = MultiDict([
        ("param_name", "PID"), ("param_mode", "store"), ("param_required", "1"), ("param_allowed", ""),
        ("param_name", "group"), ("param_mode", "redirect"), ("param_required", "0"), ("param_allowed", allowed),
    ])
    for outcome, url in (redirects or {}).items():
        data.add(f"redirect_{outcome}", url)
    return client.post("/surveys/1/panel", data=data, follow_redirects=True)


def build(app, client, split=False):
    fill_consent(client)
    add(client, "info")                                              # 2
    client.post(E + "/items/2", data={"code": "hello", "question": "Hallo Gruppe {{group}}!"})
    client.post(E + "/blocks")                                       # block 3
    add(client, "info", block=3)                                     # 3
    client.post(E + "/items/3", data={"code": "bio_only", "question": "Nur für Bio"})
    client.post(E + "/blocks")                                       # block 4
    add(client, "text_input", block=4)                               # 4
    client.post(E + "/items/4", data={"code": "mail", "validation": "email"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        children = [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                    {"t": "branch", "id": "b1", "cond": {"item": "p:group", "op": "eq", "value": "bio"},
                     "children": [{"t": "block", "block": 3}]}]
        if split:
            children.append({"t": "split", "id": "s1"})
        children.append({"t": "block", "block": 4})
        s.flow = {"t": "root", "children": children}
        flow.normalize(s)
        db.session.commit()


def start(app, client):
    fill_questions(app)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def test_capture_pipe_branch_store_and_redirect(app, client, survey):
    configure(client, {"completed": "https://panel.example/done?id={{PID}}&g={{group}}&x={{pid}}"})
    build(app, client)
    uid = start(app, client)
    assert "unvollständig" in app.test_client().get(f"/s/{uid}").data.decode()          # PID required
    assert "unvollständig" in app.test_client().get(f"/s/{uid}?PID=a1&group=physik").data.decode()  # not allowed
    p = app.test_client()
    p.get(f"/s/{uid}?PID=a b&group=bio&utm_source=mail")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    assert "Hallo Gruppe bio!" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={"nav": "next"})
    assert "Nur für Bio" in p.get(f"/s/{uid}").data.decode()                            # branch on URL parameter
    p.post(f"/s/{uid}", data={"nav": "next"})
    r = p.post(f"/s/{uid}", data={"nav": "next", "i4": ""})
    r = p.get(f"/s/{uid}")
    assert r.status_code == 302 and r.headers["Location"].startswith("https://panel.example/done?id=a%20b&g=bio&x=")
    with app.app_context():
        resp = db.session.scalar(db.select(Response).filter_by(source="real"))
        assert resp.params == {"PID": "a b"}                                           # only "store" parameters
    row = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert row["url_PID"] == "a b" and "url_group" not in row and "utm_source" not in str(row)
    # the other group skips the branch
    q = app.test_client()
    q.get(f"/s/{uid}?PID=b2&group=chemie")
    q.post(f"/s/{uid}", data=CONSENT_OK)
    q.post(f"/s/{uid}", data={"nav": "next"})
    assert 'name="i4"' in q.get(f"/s/{uid}").data.decode()


def test_split_and_panel_together(app, client, survey):
    configure(client, {"completed": "https://panel.example/done?id={{PID}}"})
    build(app, client, split=True)
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}?PID=secret-77&group=chemie")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next"})
    html = p.get(f"/s/{uid}").data.decode()
    assert "getrennt von Ihren bisherigen Antworten" in html                            # separated section first
    p.post(f"/s/{uid}", data={"nav": "next", "i4": "a@b.de"})
    r = p.get(f"/s/{uid}")
    assert r.status_code == 302 and r.headers["Location"].endswith("id=secret-77")       # redirect after the split
    with app.app_context():
        __import__("app.contact", fromlist=["bundle"]).bundle()  # PostgreSQL: finished sections wait (K11)
        entry = db.session.scalar(db.select(ContactEntry))
        assert entry.answers == {"4": "a@b.de"} and "secret-77" not in str(entry.answers)


def test_declined_and_closed_redirects(app, client, survey):
    configure(client, {"declined": "https://panel.example/noconsent", "closed": "https://panel.example/full"})
    build(app, client)
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}?PID=x")
    r = p.post(f"/s/{uid}", data={"i1": "0"})
    assert r.status_code == 302 and r.headers["Location"] == "https://panel.example/noconsent"
    client.post("/surveys/1/close")
    r = app.test_client().get(f"/s/{uid}?PID=y")
    assert r.status_code == 302 and r.headers["Location"] == "https://panel.example/full"


def test_stored_param_consent_and_checks(app, client, survey):
    client.post("/surveys/1/panel", data={"param_name": "PROLIFIC_PID", "param_mode": "store", "param_required": "1",
                                          "param_allowed": "",
                                          "redirect_completed": "https://app.prolific.com/submissions/complete?cc=C0DE1"})
    html = client.get("/surveys/1/field").data.decode()   # the link for the panel is on "Datenerhebung" (2026-10-03)
    assert "PROLIFIC_PID=" in html and "prolific-preset" not in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.panel["redirects"]["completed"] == "https://app.prolific.com/submissions/complete?cc=C0DE1"
    client.post(E + "/items/1", data={"code": "consent", "body": "", "researchers": "0000-0000-0000-001X",
                                      "draft": "template"})
    with app.app_context():
        body = db.session.get(Item, 1).texts["de"]["body"]
        assert "pseudonym" in body and "PROLIFIC_PID" in body
    r = client.post("/surveys/1/panel", data={"param_name": "bad name", "param_mode": "store", "param_required": "0",
                                              "param_allowed": ""}, follow_redirects=True)
    assert "Ungültiger Parametername" in r.data.decode()
    # a branch on an undeclared parameter is reported
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1},
                                            {"t": "branch", "id": "b", "cond": {"item": "p:nope", "op": "eq", "value": "1"},
                                             "children": [{"t": "block", "block": 2}]}]}
        from app import design

        assert any("nicht deklariert" in f.message for f in design.check(s))


def test_preview_simulates_parameters(app, client, survey):
    configure(client)
    build(app, client)
    html = client.get("/surveys/1/editor/preview?page=2&group=bio", follow_redirects=True).data.decode()
    assert "Hallo Gruppe bio!" in html


def test_link_rules_are_picked_like_any_condition_and_may_test_answers(app, client, survey):
    """UI/UX audit 2026-10-03 (S6): a rule line is the outcome, a condition picked like in the flow (here an answer),
    the link. The end page tests the answers too; declined and closed only know URL parameters."""
    fill_consent(client)
    add(client, "single_choice")                                     # 2
    client.post(E + "/items/2", data=MultiDict([("code", "gender"), ("question", "Geschlecht"),
                                                ("option_id", "a"), ("option_code", "1"), ("option_label", "männlich"),
                                                ("option_id", "b"), ("option_code", "2"), ("option_label", "weiblich")]))
    data = MultiDict({"redirect_completed": "https://panel.example/all", "r7_outcome": "completed", "r7_item": "2",
                      "r7_mode": "is", "r7_url": "https://panel.example/women"})
    data.add("r7_codes", "2")
    client.post("/surveys/1/panel", data=data)
    with app.app_context():
        assert db.session.get(Survey, 1).panel["rules"] == [{"outcome": "completed", "item": 2, "op": "eq", "value": "2",
                                                             "url": "https://panel.example/women", "mode": "now"}]
    page = client.get("/surveys/1/field").data.decode()
    assert 'name="r0_item"' in page and '<option value="2" selected>gender · Geschlecht</option>' in page
    bad = MultiDict({"r0_outcome": "declined", "r0_item": "2", "r0_mode": "is"})
    bad.add("r0_codes", "2")
    assert "nur URL-Parameter möglich" in client.post("/surveys/1/panel", data=bad, follow_redirects=True).data.decode()
    uid = start(app, client)
    for answer, target in (("2", "https://panel.example/women"), ("1", "https://panel.example/all")):
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        r = p.post(f"/s/{uid}", data={"nav": "next", "i2": answer}, follow_redirects=False)
        while r.status_code == 302 and r.headers["Location"].startswith("/"):
            r = p.get(r.headers["Location"])
        html = r.data.decode() if r.status_code == 200 else ""
        assert (r.status_code == 302 and r.headers["Location"] == target) or f'href="{target}"' in html, answer


def test_unused_panel_settings_stay_small(app, client, survey):
    """UI/UX audit 2026-10-05, V20: lines only for completion links that have one, the kind of forwarding with its
    link; the form is on "Datenerhebung", in the block "Teilnahmelink" (Ralf, 2026-10-06)."""
    page = client.get("/surveys/1/field").data.decode()
    assert 'data-link-row="completed" hidden' in page and "+ Abschlusslink" in page
    assert 'class="list-table is-edit param-table"' in page
    assert 'id="panel"' in page and 'id="panel"' not in client.get("/surveys/1").data.decode()
    configure(client, {"screenout": "https://panel.example/so"})
    page = client.get("/surveys/1/field").data.decode()
    assert 'data-link-row="completed" hidden' in page and 'data-link-row="screenout" >' in page
    pick = page[page.index("data-link-pick"):].split("</select>", 1)[0]
    assert "Screen-out" not in pick and "abgeschlossen" in pick
    assert "Panel und Parameter:" not in page                                  # no summary line beside the form
