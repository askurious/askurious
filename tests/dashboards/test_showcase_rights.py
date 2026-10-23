"""Audit 2026-10-11, package V6 "Beispielbefragungen und Admin" (M6, M7, rights H2, AU9, BT3).

M6: on an example survey the team's comments stay with the team (decision 5a): not in comments.csv, comments.zip or
the image of a comment for who sees the survey only as an example; a sweep over every GET route of a survey (all of
them need "Ansehen", surveys.required_role) finds the text, name and image of a comment nowhere.
M7: switching an example survey on or off needs the second factor while ADMIN_TOTP is on (twofactor.admin_rights).
Rights H2: pretest links of someone who left do not come back to life when the survey becomes an example.
AU9: "Datenbank prüfen" (a connection from the server to any host) after a fresh sign-in, like "Kopieren".
BT3 (status page: date of the deploy only) is in tests/platform/test_feedback.py."""
import io
import re
import time
import zipfile

from app.extensions import db
from app.models import PreviewComment, Survey, SurveyMember
from tests.conftest import add, allow
from tests.editor.test_comment_shots import _png, _send
from tests.dashboards.test_showcase import ME, OTHER, _as, _showcase

MARK = "Interne-Notiz-Q7x"        # the text of the team's comment
AUTHOR = "Olga Geheimname"         # who wrote it


def _team_comment(app, client):
    """A comment of the team with an image; its id and the bytes of the stored image."""
    add(client, "info")                                                        # item 2
    r = _send(client, "/surveys/1/editor/preview/comment", data={"text": MARK}, image=_png())
    assert r.status_code == 200 and r.json["shot"] is True
    cid = r.json["id"]
    with app.app_context():
        c = db.session.get(PreviewComment, cid)
        c.author = AUTHOR
        db.session.commit()
    shot = client.get(f"/surveys/1/editor/preview/comment/{cid}/shot")
    assert shot.status_code == 200
    return cid, shot.data


def _texts(resp):
    """The text of a response, with every file inside a ZIP."""
    data = resp.get_data()
    out = [data.decode("utf-8", "replace")]
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                out += [z.read(n).decode("utf-8", "replace") for n in z.namelist()]
        except zipfile.BadZipFile:
            pass
    return "\n".join(out)


def _survey_get_routes(app, cid):
    """Every GET route with a survey (its number, or its code as in /guide/<code>), filled in for survey 1 (other
    numbers 1 or the comment, text "x")."""
    with app.app_context():
        code = db.session.get(Survey, 1).code
    out = []
    for rule in app.url_map.iter_rules():
        if ("GET" not in (rule.methods or ()) or not {"survey_id", "code"} & set(rule.arguments)
                or rule.endpoint == "static"):
            continue
        values = {}
        for arg in rule.arguments:
            conv = rule._converters.get(arg)
            name = type(conv).__name__ if conv else ""
            if arg == "survey_id":
                values[arg] = 1
            elif arg == "code":
                values[arg] = code
            elif arg == "comment_id":
                values[arg] = cid
            elif "Integer" in name or arg.endswith("_id") or name == "SidConverter":
                values[arg] = 1
            else:
                values[arg] = "x"
        try:
            with app.test_request_context():
                from flask import url_for

                out.append((rule.endpoint, url_for(rule.endpoint, **values)))
        except Exception:  # noqa: BLE001 - a converter that wants another value: not a route to sweep here
            continue
    return out


def test_m6_the_teams_comments_reach_no_viewer_of_an_example(app, client, survey):
    cid, shot = _team_comment(app, client)
    allow(app, OTHER)
    _showcase(client)
    other = _as(app, OTHER, "Kollegin")
    # the three routes of the finding
    for path in ("/surveys/1/export/comments.csv", "/surveys/1/export/comments.zip",
                 f"/surveys/1/editor/preview/comment/{cid}/shot"):
        assert other.get(path).status_code == 404, path
    assert "comments.csv" not in other.get("/surveys/1/data").data.decode()      # no export row for them either
    # the team still has them
    assert MARK in client.get("/surveys/1/export/comments.csv").data.decode()
    assert MARK in _texts(client.get("/surveys/1/export/comments.zip"))
    assert "Kommentare aus Testdurchläufen" in client.get("/surveys/1/data").data.decode()
    # every route that needs "Ansehen": nothing of the comment
    routes = _survey_get_routes(app, cid)
    assert len(routes) > 120 and any(ep == "surveys.export_comments" for ep, _ in routes)
    leaks, swept = [], 0
    for endpoint, path in routes:
        try:
            resp = other.get(path)
        except Exception:  # noqa: BLE001 - a dummy value a route cannot use (testing propagates errors)
            continue
        swept += 1
        if resp.status_code >= 400:
            continue
        body = _texts(resp)
        if MARK in body or AUTHOR in body or shot in resp.get_data():
            leaks.append(f"{endpoint} {path} {resp.status_code}")
    assert swept > 120
    assert not leaks, leaks


def test_m7_switching_an_example_needs_the_second_factor(app, client, survey):
    from app import twofactor

    app.config["ADMIN_TOTP"] = True
    assert "Als Beispielbefragung allen zeigen" in client.get("/surveys/1").data.decode()  # ORCID exemption
    with client.session_transaction() as sess:
        sess[twofactor.SIGNED_IN_VIA] = twofactor.VIA_LINK
    assert "Als Beispielbefragung allen zeigen" not in client.get("/surveys/1").data.decode()
    assert client.post("/surveys/1/showcase", data={"state": "on"}).status_code == 403
    with app.app_context():
        assert db.session.get(Survey, 1).showcase is False
    with client.session_transaction() as sess:
        sess[twofactor.SIGNED_IN_VIA] = twofactor.VIA_ORCID
    assert "Als Beispielbefragung allen zeigen" in client.get("/surveys/1").data.decode()
    assert "ist jetzt eine Beispielbefragung" in _showcase(client).data.decode()


def _link_path(html, pattern):
    return "/p/" + re.search(pattern, html).group(1).split("/p/", 1)[1]


def test_h2_pretest_links_of_someone_who_left_stay_dead_on_an_example(app, client, survey):
    add(client, "info")
    allow(app, OTHER)
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, role="editor"))
        db.session.commit()
    other = _as(app, OTHER, "Kollegin")
    pretest = _link_path(other.get("/surveys/1/field?pretest=7").data.decode(), r'id="pretest-url"[^>]*value="([^"]+)"')
    phone = _link_path(other.get("/surveys/1/editor/preview?device=mobile").data.decode(),
                       r'value="(https?://[^"]+/p/[^"]+)"')
    tester = app.test_client()
    assert tester.get(pretest).status_code == 200
    with app.app_context():                                             # she leaves the survey
        db.session.execute(db.delete(SurveyMember).filter_by(survey_id=1, orcid=OTHER))
        db.session.commit()
    assert app.test_client().get(pretest).status_code == 404
    _showcase(client)                                                   # an example now: she may view it again
    assert other.get("/surveys/1").status_code == 200
    assert app.test_client().get(pretest).status_code == 404            # but her old links stay dead
    assert app.test_client().get(phone).status_code == 404
    # An example-only viewer previews locally with isolated transient answers; no phone link writes team data.
    preview = other.get("/surveys/1/editor/preview?device=mobile")
    assert preview.status_code == 200 and not re.search(r'value="https?://[^" ]+/p/', preview.data.decode())
    _showcase(client, on=False)
    assert other.get("/surveys/1/editor/preview?device=mobile").status_code == 404
    # the team's own links are untouched
    own = _link_path(client.get("/surveys/1/field?pretest=7").data.decode(), r'id="pretest-url"[^>]*value="([^"]+)"')
    assert app.test_client().get(own).status_code == 200
    assert ME


def test_au9_checking_a_database_needs_a_fresh_sign_in(app, client):
    form = {"action": "check", "host": "127.0.0.1", "port": "1", "name": "x", "user": "u", "password": "p"}
    with client.session_transaction() as s:
        s["signed_in_at"] = time.time() - 3600
    r = client.post("/admin/database", data=form)
    assert r.status_code == 302 and "/auth/" in r.headers["Location"]       # to sign in again, nothing connected
    with client.session_transaction() as s:
        s["signed_in_at"] = time.time()
    r = client.post("/admin/database", data=form)
    assert r.status_code == 200 and 'id="datenbank"' in r.get_data(as_text=True)   # fresh: checked on the page
