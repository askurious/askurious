"""SP13 C: replayed decline receipts, opaque assignments, and shared-device deletion."""
from datetime import timedelta

from app import browser_receipts, participate
from app.extensions import db
from app.models import BrowserReceipt, Response, Survey, utcnow
from tests.participation.test_participate import build, consent_form, uid


def test_decline_receipt_can_only_be_consumed_once(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        token = browser_receipts.decline(s)
        db.session.commit()
        assert browser_receipts.undo_decline(s, token)
        db.session.commit()
        assert not browser_receipts.undo_decline(s, token)
        assert not browser_receipts.undo_decline(s, True)


def test_cookie_replay_does_not_subtract_other_peoples_declines(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    code = uid(app)
    p = app.test_client()
    p.get(f"/s/{code}")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.declined_count = 2
        token = browser_receipts.decline(s)
        db.session.commit()
    with p.session_transaction() as sess:
        sess["d1"] = token
    cookie = p.get_cookie("session").value
    p.post(f"/s/{code}/after-all")
    for _ in range(3):
        p.set_cookie("session", cookie)
        p.post(f"/s/{code}/after-all")
    with app.app_context():
        assert db.session.get(Survey, 1).declined_count == 1


def test_first_page_seed_is_server_side_and_redraws_have_network_paradata(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    code = uid(app)
    phones = [app.test_client(), app.test_client()]
    for p in phones:
        p.get(f"/s/{code}", environ_overrides={"REMOTE_ADDR": "192.0.2.15"})
        with p.session_transaction() as sess:
            pending = sess["n1"]
            assert "seed" not in pending
        p.post(f"/s/{code}", data=consent_form(), environ_overrides={"REMOTE_ADDR": "192.0.2.15"})
    with app.app_context():
        rows = list(db.session.scalars(db.select(Response).order_by(Response.id)))
        assert [r.paradata["_assignment_network_starts"] for r in rows] == [0, 1]


def test_shared_device_rights_expire_but_active_participation_can_be_deleted(app):
    with app.app_context():
        assert participate._recent_erase(Response(status=Response.ACTIVE))
        assert participate._recent_erase(Response(status=Response.COMPLETED, completed_at=utcnow()))
        assert not participate._recent_erase(Response(status=Response.COMPLETED, completed_at=utcnow()-timedelta(minutes=16)))


def test_default_handover_is_closed_and_expired_receipts_are_removed(app, survey):
    from app import hosts

    with app.test_request_context():
        app.config["PARTICIPANT_HANDOVER_UNTIL"] = ""
        assert not hosts._handover_open()
        s = db.session.get(Survey, 1)
        row = browser_receipts.receipt(s, "decline", "old")
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        browser_receipts.sweep(utcnow())
        assert db.session.get(BrowserReceipt, (1, "decline", browser_receipts.digest("old"))) is None


def test_shared_device_cannot_reveal_code_or_delete_after_fifteen_minutes(app, client, survey):
    from tests.privacy.test_erasure import build as erase_build, take, CODE

    code = erase_build(app, client)
    p = app.test_client()
    deletion_code = CODE.search(take(p, code)).group(1)
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        r.completed_at = utcnow() - timedelta(minutes=16)
        db.session.commit()
    assert deletion_code not in p.get(f"/s/{code}/privacy").data.decode()
    assert deletion_code not in p.get(f"/s/{code}").data.decode()
    p.post(f"/s/{code}/delete", data={"sure": "1", "here": "1"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1
    p.post(f"/s/{code}/delete", data={"sure": "1", "code": deletion_code})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0


def test_opening_the_link_writes_nothing_to_the_database(app, client, survey):
    """Abgleich 2026-10-15 T02-NEU-1: every GET without a cookie wrote and committed a receipt for seven days and
    locked the network row. Now the first page is drawn from the pid alone; the first submit stores and counts."""
    from sqlalchemy import event

    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    code = uid(app)
    writes = []

    def note(conn, cursor, statement, *args):
        if statement.lstrip().split(" ", 1)[0].upper() in ("INSERT", "UPDATE", "DELETE"):
            writes.append(statement)

    phones = [app.test_client() for _ in range(3)]
    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", note)
        try:
            for phone in phones:
                assert phone.get(f"/s/{code}", environ_overrides={"REMOTE_ADDR": "192.0.2.16"}).status_code == 200
        finally:
            event.remove(db.engine, "before_cursor_execute", note)
        assert not [w for w in writes if "browser_receipt" in w or "response" in w.split("(")[0]], writes
        assert db.session.scalar(db.select(db.func.count()).select_from(BrowserReceipt)) == 0
    first = phones[0].get(f"/s/{code}").get_data(as_text=True)
    phones[0].post(f"/s/{code}", data=consent_form(), environ_overrides={"REMOTE_ADDR": "192.0.2.16"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(BrowserReceipt).where(
            BrowserReceipt.scope == "assignment-network")) == 1
    assert first


def test_the_seed_follows_from_the_pid_and_the_secret_key(app, survey):
    """Abgleich 2026-10-15 T02-NEU-1: the same pid always gets the same first page; another pid or key another one."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        seed = browser_receipts._derived_seed(s, "pid-1")
        assert seed == browser_receipts._derived_seed(s, "pid-1") != browser_receipts._derived_seed(s, "pid-2")
        assert 0 <= seed < 2 ** 53
        app.config["SECRET_KEY"] = "another key " * 4
        assert browser_receipts._derived_seed(s, "pid-1") != seed


def test_counting_the_network_keeps_what_is_loaded(app, survey):
    """Code review 2026-10-15, F6: the count of the network is committed at once (its row is not held locked while
    the participation is stored), but what the first submit has loaded stays loaded."""
    from sqlalchemy import inspect

    with app.test_request_context(environ_base={"REMOTE_ADDR": "192.0.2.17"}):
        s = db.session.get(Survey, 1)
        assert browser_receipts.assignment(s, "pid-1", count=True)["earlier"] == 0
        assert not inspect(s).expired_attributes
        assert browser_receipts.assignment(s, "pid-2", count=True)["earlier"] == 1
