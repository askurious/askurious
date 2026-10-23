"""Many participants and researchers at once: database settings, no long write locks, a page that could not be
stored (audit of 2026-10-01, package 1)."""
import sqlite3

from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import EditMark, Response, Survey
from tests.conftest import CONSENT_OK, _config, add, fill_consent, needs_sqlite


def _running(app, client):
    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "age", "validation": "integer", "question": "Alter"})
    client.post("/surveys/1/start", data={})
    with app.app_context():
        return f"/s/{db.session.get(Survey, 1).code}"


def _write_lock_free(app):
    """True if another connection could write right now (nobody holds the database's write lock)."""
    path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    con = sqlite3.connect(path, timeout=0.2)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


def test_sqlite_runs_with_wal_and_waits_for_a_busy_database(tmp_path):
    from app import create_app

    class C(_config(tmp_path)):
        SQLITE_JOURNAL = "WAL"
        SQLITE_BUSY_SECONDS = 20

    app = create_app(C)
    with app.app_context():
        with db.engine.connect() as con:
            assert con.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert con.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL
            assert con.execute(text("PRAGMA busy_timeout")).scalar() == 20000
        db.engine.dispose()


def test_other_journal_modes_and_no_setting(tmp_path):
    from app import create_app

    class C(_config(tmp_path)):
        SQLITE_JOURNAL = "DELETE"  # e.g. a network drive, where WAL does not work
        SQLITE_BUSY_SECONDS = 7

    app = create_app(C)
    with app.app_context():
        with db.engine.connect() as con:
            assert con.execute(text("PRAGMA journal_mode")).scalar() == "delete"
            assert con.execute(text("PRAGMA busy_timeout")).scalar() == 7000
        db.engine.dispose()


@needs_sqlite
def test_ai_draft_in_the_wizard_holds_no_write_lock(app, client, survey, monkeypatch):
    """A site admin is not charged for AI calls, so nothing committed before the call: the facts saved by the
    wizard stayed an open write for as long as OpenAI took, and every participant got "database is locked"."""
    from app import ai

    seen = {}

    class Client:
        class responses:  # noqa: N801
            @staticmethod
            def create(**kw):
                seen["free"] = _write_lock_free(app)
                return type("R", (), {"output_text": "Entwurf der KI", "usage": None})()

    monkeypatch.setattr(ai, "_client", lambda: Client())
    r = client.post("/surveys/1/setup/consent", data={"wizard": "draft_ai", "min_age": "18", "duration": "7",
                                                             "researchers": "0000-0000-0000-001X"})
    assert r.status_code == 302 and seen == {"free": True}
    with app.app_context():
        consent = db.session.get(Survey, 1).consent_item
        assert consent.texts["de"]["body"] == "Entwurf der KI" and consent.config["min_age"] == 18


@needs_sqlite
def test_dummy_data_is_stored_one_by_one(app, client, survey):
    """Not one transaction for all of them: the write lock is free between two simulated participations."""
    fill_consent(client)
    add(client, "text_input")
    commits = []
    with app.app_context():
        from sqlalchemy.orm import Session

        from app import dummy

        def note(session):
            commits.append(_write_lock_free(app))

        event.listen(Session, "after_commit", note)
        try:
            created = dummy.generate(db.session.get(Survey, 1), 4, seed=1)
        finally:
            event.remove(Session, "after_commit", note)
        assert created == 4
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(source="dummy", status="completed")) == 4
    assert commits == [True] * 4


def test_page_that_could_not_be_stored_can_be_sent_again(app, client, survey, monkeypatch):
    """The database stays locked for longer than it waits: a page that says so, nothing half stored, the
    cookie as before; sending the same page again works."""
    url = _running(app, client)
    p = app.test_client()
    p.get(url)
    real = db.session.commit
    failed = []

    def locked():
        if not failed:
            failed.append(1)
            raise OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))
        real()

    monkeypatch.setattr(db.session, "commit", locked)
    r = p.post(url, data=CONSENT_OK)
    html = r.data.decode()
    assert r.status_code == 503 and r.headers["Retry-After"] == "10"
    assert "stark ausgelastet" in html and "data-resend" in html and "Seite erneut senden" in html
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0  # rolled back
    with p.session_transaction() as s:
        assert "r1" not in s and "n1" in s  # no id of a participation that was never stored; the start still waits
    r = p.post(url, data=CONSENT_OK)  # what the button does: the same request again
    assert r.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1
    assert "Alter" in p.get(url).data.decode()


def test_busy_page_follows_the_language(app, client, survey, monkeypatch):
    url = _running(app, client)
    p = app.test_client()
    p.get(url + "?lang=en")

    def locked():
        raise OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(db.session, "commit", locked)
    r = p.post(url + "?lang=en", data=CONSENT_OK)
    assert r.status_code == 503 and "very busy" in r.data.decode() and "Send the page again" in r.data.decode()


def test_open_editor_writes_every_second_report_only(app, client, survey):
    add(client, "text_input")
    url = "/surveys/1/editor/presence"
    assert client.post(url, data={"key": "item:2"}).status_code == 200
    with app.app_context():
        first = db.session.scalar(db.select(EditMark.seen_at).filter_by(key="item:2"))
    client.post(url, data={"key": "item:2"})
    with app.app_context():
        assert db.session.scalar(db.select(EditMark.seen_at).filter_by(key="item:2")) == first  # still fresh: not written
        from datetime import timedelta

        mark = db.session.scalar(db.select(EditMark).filter_by(key="item:2"))
        mark.seen_at = first - timedelta(seconds=25)
        db.session.commit()
    client.post(url, data={"key": "item:2"})
    with app.app_context():
        assert db.session.scalar(db.select(EditMark.seen_at).filter_by(key="item:2")) >= first
    client.post(url, data={"key": "item:2", "leave": "1"})  # leaving is always written
    with app.app_context():
        assert db.session.scalar(db.select(EditMark.seen_at).filter_by(key="item:2")) is None
