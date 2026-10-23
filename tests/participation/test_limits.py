import re

import pytest

from app import ai, limits
from app.extensions import db
from app.models import AiUsage, RateCount, Response
from tests.conftest import fill_consent
from tests.editor.test_comments import _setup

OTHER = "0000-0001-5555-5555"


def _running(app, client):
    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"})
    client.post("/surveys/1/start", data={})


def test_participation_starts_are_limited_per_sender(app, client, survey, monkeypatch):
    from tests.conftest import CONSENT_OK

    _running(app, client)
    app.config.update(START_LIMIT=3, START_WINDOW=600)
    script = {"REMOTE_ADDR": "203.0.113.5"}

    def start(env):  # a participation starts (and is stored) with the first submitted page
        c = app.test_client()
        assert c.get("/s/1", environ_base=env).status_code == 200      # opening the link alone is never limited
        return c, c.post("/s/1", data=CONSENT_OK, environ_base=env)

    for _ in range(3):
        assert start(script)[1].status_code == 302
    r = start(script)[1]
    assert r.status_code == 429 and r.headers["Retry-After"] == "600" and "ein paar Minuten" in r.data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(source="real")) == 3
    # other senders are not affected, and a started participation continues
    assert start({"REMOTE_ADDR": "203.0.113.6"})[1].status_code == 302
    app.config["START_LIMIT"] = 5
    started, first = start(script)
    assert first.status_code == 302
    app.config["START_LIMIT"] = 3
    assert started.get("/s/1", environ_base=script).status_code == 200          # has its cookie: no new start
    # the window moves on
    clock = limits.time.time()
    monkeypatch.setattr(limits.time, "time", lambda: clock + 601)
    assert start(script)[1].status_code == 302


def test_nothing_about_the_sender_is_kept_and_local_proxy_is_not_limited(app, client, survey):
    _running(app, client)
    from tests.conftest import CONSENT_OK

    app.config.update(START_LIMIT=1)
    c = app.test_client()
    c.get("/s/1", environ_base={"REMOTE_ADDR": "203.0.113.9"})
    c.post("/s/1", data=CONSENT_OK, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    with app.app_context():
        keys = db.session.scalars(db.select(RateCount.key)).all()
    assert keys and all(len(k) == 32 and "203" not in k for k in keys)  # a keyed digest of the day, no address
    for _ in range(3):                                                             # PROXY_COUNT not set: no limit
        assert app.test_client().get("/s/1", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200


def test_phone_link_comments_are_limited_per_day(app, client, survey):
    _setup(client)
    html = client.get("/surveys/1/editor/preview?device=mobile").data.decode()
    path = "/" + re.search(r'value="(https?://[^"]+/p/[^"]+)"', html).group(1).split("://", 1)[1].split("/", 1)[1]
    app.config["PHONE_COMMENTS_PER_DAY"] = 2
    phone = app.test_client()
    for n in range(2):
        assert phone.post(path + "/comment", data={"block_id": "2", "text": f"Kommentar {n}"}).status_code == 200
    r = phone.post(path + "/comment", data={"block_id": "2", "text": "einer zu viel"})
    assert r.status_code == 429 and "sehr viele Kommentare" in r.json["error"]
    # comments in the editor are not counted
    assert client.post("/surveys/1/editor/preview/comment", data={"block_id": "2", "text": "ok"}).status_code == 200


def test_ai_calls_are_limited_per_researcher_and_day(app, client, survey, monkeypatch):
    calls = []

    class Fake:
        class responses:
            @staticmethod
            def create(**kw):
                calls.append(kw)
                return type("R", (), {"output_text": "Text"})()

    monkeypatch.setattr(ai, "_client", lambda: Fake)
    app.config.update(AI_TEXTS_PER_DAY=2)
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "editor"})
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    with app.test_request_context():
        from flask_login import login_user

        from app.models import User

        login_user(db.session.scalar(db.select(User).filter_by(orcid=OTHER)))
        assert ai.generate_text("i", "p") == "Text" and ai.generate_text("i", "p") == "Text"
        with pytest.raises(ai.AIError, match="Tageslimit von 2 KI-Texten"):
            ai.generate_text("i", "p")
        assert len(calls) == 2                                                   # the third call never left
        assert db.session.scalar(db.select(AiUsage.count).filter_by(kind="text")) == 2
        # site admins (the operator pays) have AI_ADMIN_FACTOR times the limit (audit 2026-10-13, KR-2)
        login_user(db.session.scalar(db.select(User).filter_by(is_admin=True)))
        for _ in range(3):
            ai.generate_text("i", "p")
        assert len(calls) == 5


def _other_process():
    """What another server process knows: nothing of this one's memory (the counters in memory, kept answers)."""
    for store in (limits._asked, limits._fallback, limits._load_fallback):
        store.clear()


def test_starts_per_sender_and_survey_hold_across_processes(app, client, survey):
    """Audit 2026-10-18, BIZ-01: SURVEY_SENDER_LIMIT and START_LIMIT counted in the memory of each server process,
    so with 2 containers × 12 processes one sender got up to 24 times the limit. They count in rate_count now: once
    a sender stored the limit of new participations, every further one is refused, whichever process answers."""
    from tests.conftest import CONSENT_OK

    _running(app, client)
    app.config.update(SURVEY_SENDER_LIMIT=2, START_LIMIT=0)
    script = {"REMOTE_ADDR": "203.0.113.50"}

    def start():
        _other_process()  # every request in a process of its own (round robin of nginx)
        c = app.test_client()
        c.get("/s/1", environ_base=script)
        return c.post("/s/1", data=CONSENT_OK, environ_base=script).status_code

    assert [start() for _ in range(4)] == [302, 302, 429, 429]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(source="real")) == 2
        row = db.session.scalar(db.select(RateCount).filter_by(kind="survey_sender"))
        assert row.n == 2 and "203.0.113" not in row.key  # one row per slot, a digest of the sender and survey
    app.config.update(SURVEY_SENDER_LIMIT=0, START_LIMIT=3)
    assert [start() for _ in range(4)] == [302, 302, 302, 429]  # START_LIMIT (all surveys) the same way
    # the rows go once the longest window is over (housekeeping, at most a day)
    with app.test_request_context():
        assert limits.sweep(now=limits.time.time() + limits.DAY + 700) >= 2
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count()).select_from(RateCount)) == 0


def test_a_database_that_cannot_count_falls_back_to_memory(app, monkeypatch):
    """BIZ-01: a limit against load must not turn real participants away when rate_count cannot be written; the
    process then counts in its memory as before."""
    app.config.update(START_LIMIT=2)

    def broken():
        raise RuntimeError("no database")

    with app.test_request_context(environ_base={"REMOTE_ADDR": "203.0.113.51"}):
        monkeypatch.setattr(limits.db.session, "connection", broken)
        assert limits.allow_start() and limits.allow_start() and not limits.allow_start()
