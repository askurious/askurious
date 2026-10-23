"""Researcher pages that count instead of loading every participation, and limits that hold for all server
processes (audit of 2026-10-01, package 6)."""
import io
import os

from sqlalchemy import event

from app import limits
from app.extensions import db
from app.models import AiUsage, Response, Survey, User, utcnow
from tests.conftest import CONSENT_OK, add, fill_consent, may_sign_in

ME = "0000-0000-0000-001X"


def _running(app, client):
    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "age", "validation": "integer", "question": "Alter"})
    client.post("/surveys/1/panel", data={"param_name": "gruppe", "param_mode": "store", "param_required": "0", "param_allowed": "a, b"})
    client.post("/surveys/1/start", data={})
    with app.app_context():
        return f"/s/{db.session.get(Survey, 1).code}"


def _fill(app, n=6):
    from datetime import timedelta

    with app.app_context():
        now = utcnow()
        for k in range(n):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, started_at=now - timedelta(minutes=5),
                                    completed_at=now, answers={"2": 20 + k}, params={"gruppe": "a" if k % 2 else "b"}))
        db.session.add(Response(survey_id=1, source="real", status=Response.ACTIVE, answers={}))
        db.session.add(Response(survey_id=1, source="dummy", is_preview=True, status=Response.COMPLETED, started_at=now, completed_at=now, answers={}))
        db.session.commit()


def test_tally_counts_by_source_and_status(app, client, survey):
    _running(app, client)
    _fill(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.tally() == {("real", False, "completed"): 6, ("real", False, "active"): 1, ("dummy", True, "completed"): 1}
        assert s.has_real_responses()
        from app import lifecycle

        assert not lifecycle.deletable(s)


def test_survey_pages_do_not_load_the_answers_of_all_participations(app, client, survey):
    _running(app, client)
    _fill(app)
    loaded = []

    def note(conn, cursor, statement, parameters, context, executemany):
        head = statement.split(" FROM ")[0].lower()
        if "response.answers" in head and "response.layout" in head:  # whole participations, not single columns
            loaded.append(statement[:80])

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", note)
    pages = {"/surveys/1": "6", "/surveys/1/field": "gruppe", "/surveys/1/data": "6", "/surveys/1/editor/": "Alter",
             "/dashboard/": "6 abgeschlossen"}
    for url, expected in pages.items():
        r = client.get(url)
        assert r.status_code == 200 and expected in r.data.decode(), url
    assert loaded == []
    html = client.get("/surveys/1/field").data.decode()
    assert "gruppe=a" in html and "gruppe=b" in html  # the links per group with their counts are still there
    with app.app_context():
        from app import export, panel, timeline

        s = db.session.get(Survey, 1)
        assert [(r["name"], r["value"], r["started"], r["completed"]) for r in panel.group_counts(s, usable=False)] == [
            ("gruppe", "a", 3, 3), ("gruppe", "b", 3, 3), ("gruppe", None, 1, 0)]
        assert export.completion_seconds(s) == [300] * 6 and export.median_seconds(s) == 300
        assert timeline.build(s)["completed"] == 6


def test_new_participations_per_survey_are_reported_for_all_senders(app, client, survey):
    """The cap per survey turns nobody away since the audit of 2026-10-06 (S5): the tab "Datenerhebung" says it was
    reached, once per window; the limit per sender and survey is in tests/audits/test_audit_2026_10_06_b.py."""
    url = _running(app, client)
    app.config["SURVEY_START_LIMIT"], app.config["SURVEY_START_WINDOW"] = 3, 600

    def start():
        p = app.test_client()
        p.get(url)
        return p.post(url, data=CONSENT_OK)

    assert [start().status_code for _ in range(5)] == [302] * 5
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 5
    page = client.get("/surveys/1/field").data.decode()
    assert page.count("Mehr als 3 neue Teilnahmen innerhalb von 10 Minuten") == 1
    app.config["SURVEY_START_LIMIT"] = 0
    assert start().status_code == 302


def test_test_runs_over_the_phone_link_are_limited(app, client, survey):
    from app import editor

    fill_consent(client)
    add(client, "text_input")
    app.config["PHONE_RUNS_PER_DAY"] = 2
    with app.test_request_context():
        from flask_login import login_user

        with app.app_context():
            login_user(db.session.scalar(db.select(User).filter_by(orcid=ME)))
            link = editor.share_url(db.session.get(Survey, 1))
    path = "/p/" + link.rsplit("/p/", 1)[1]
    assert [app.test_client().get(path).status_code for _ in range(3)] == [200, 200, 429]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(source="test")) == 2
    assert client.get("/surveys/1/editor/preview").status_code == 200  # signed-in researchers are not limited


def test_media_serves_images_only(app, client, survey):
    base = app.config["UPLOAD_FOLDER"]
    for folder, name, body in (("progress", "1_abcdefgh.json", b"{}"), ("_theme_rwth", "logo.json", b"{}"),
                               ("abc", "bild.png", b"\x89PNG\r\n\x1a\n"), ("abc", "notiz.txt", b"x")):
        os.makedirs(os.path.join(base, folder), exist_ok=True)
        with io.open(os.path.join(base, folder, name), "wb") as f:
            f.write(body)
    anon = app.test_client()
    assert anon.get("/media/abc/bild.png").status_code == 200
    assert anon.get("/media/progress/1_abcdefgh.json").status_code == 404
    assert anon.get("/media/_theme_rwth/logo.json").status_code == 404
    assert anon.get("/media/abc/notiz.txt").status_code == 404


def test_ai_limit_is_counted_in_one_step(app, client, survey):
    app.config["AI_TEXTS_PER_DAY"] = 2
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Kollegin")
    with app.test_request_context():
        from flask_login import login_user

        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0002-1825-0097"))
        login_user(user)
        assert limits.charge_ai("text") is None and limits.charge_ai("text") is None
        assert "Tageslimit von 2" in limits.charge_ai("text")
        row = db.session.scalar(db.select(AiUsage).filter_by(user_id=user.id, kind="text"))
        assert row.count == 2
        # a second request that read the count before this one wrote it: the database refuses the step beyond the limit
        row.count = 1
        db.session.commit()
        stale = db.session.scalar(db.select(AiUsage).filter_by(user_id=user.id, kind="text"))
        db.session.execute(db.update(AiUsage).where(AiUsage.id == stale.id).values(count=2))  # the other request's step
        assert "Tageslimit von 2" in limits.charge_ai("text")
        db.session.expire_all()
        assert db.session.scalar(db.select(AiUsage.count).filter_by(user_id=user.id, kind="text")) == 2


def test_designs_page_names_the_people_a_design_is_shared_with(app, client, survey):
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Kollegin Beispiel")
    client.post("/designs/new", data={"name": "Hausfarben"})
    may_sign_in(app, "0000-0002-1825-0097")  # sharing needs someone who can sign in (H4)
    client.post("/designs/1/share", data={"orcid": "0000-0002-1825-0097"})
    html = client.get("/designs/").data.decode()
    assert "Kollegin Beispiel" in html
