"""Audit 2026-10-05, package G: PostgreSQL and concurrent writers (T-11, T-12, T-25, L-13, T-29, T-37, T-39), with
T-34 (a once-only ID goes on after a lost cookie), T-36 (overlapping and incomplete cells in the pre-launch check) and
T-28 (the speeder judgement at a completion only from a minimum number of completions on).

The tests with threads run on SQLite, where its one writer serialises them anyway, and with TEST_DATABASE_URL on
PostgreSQL, where they show the races: a barrier holds the first request at the critical point for a moment, so the
second one arrives there at the same time unless a lock makes it wait."""
import threading
from datetime import timedelta

import pytest
from sqlalchemy import event, text

from app import auth, lifecycle, panel, quotas
from app.extensions import db
from app.models import QuotaCell, Response, Survey, User, utcnow
from tests.conftest import CONSENT_OK, PG_URL
from tests.quotas.test_quota_field import NONE_ENDS, idle
from tests.quotas.test_quotas import Q, build, cells_form, counters, finish, questionnaire, screen, start

needs_pg = pytest.mark.skipif(not PG_URL, reason="about PostgreSQL's locks (TEST_DATABASE_URL)")
USED = "bereits teilgenommen"


def _gate(monkeypatch, owner, name, timeout=2):
    """Two calls of owner.name meet before they go on; one that waits alone goes on after `timeout` seconds."""
    barrier = threading.Barrier(2, timeout=timeout)
    original = getattr(owner, name)

    def gated(*args, **kwargs):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, gated)


def _parallel(*calls):
    out = [None] * len(calls)

    def run(k):
        out[k] = calls[k]()

    threads = [threading.Thread(target=run, args=(k,)) for k in range(len(calls))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return out


def _count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Response.id)).filter(Response.is_preview.is_(False)))


def _participant(app, uid, query=""):
    p = app.test_client()
    p.get(f"/s/{uid}{query}")
    return p


# --- T-11: two first pages with the same once-only ID at the same moment ---------------------------------


def test_two_first_pages_with_the_same_id_at_the_same_moment_store_one(app, client, survey, monkeypatch):
    from tests.participation.test_panel_once import once_survey

    uid = once_survey(app, client)
    a, b = _participant(app, uid, "?PID=p7"), _participant(app, uid, "?PID=p7")
    _gate(monkeypatch, panel, "_holders")  # both inserted, both about to ask whether the ID is taken
    _parallel(lambda: a.post(f"/s/{uid}", data=CONSENT_OK), lambda: b.post(f"/s/{uid}", data=CONSENT_OK))
    assert _count(app) == 1                                                                 # PostgreSQL kept both before


# the advisory lock of T-11 gave way to a UNIQUE index over the claimed values (audit 2026-10-06, P5):
# tests/audits/test_audit_2026_10_06_g.py


# --- T-34: the same ID while its participation runs goes on ----------------------------------------------


def test_a_panel_id_cannot_resume_another_browsers_running_participation(app, client, survey):
    from tests.participation.test_panel_once import once_survey

    uid = once_survey(app, client)
    first = _participant(app, uid, "?PID=p7")
    first.post(f"/s/{uid}", data=CONSENT_OK)
    other = app.test_client()                                                               # another device, or no cookie
    page = other.get(f"/s/{uid}?PID=p7")
    assert page.status_code == 200 and USED in page.data.decode()
    with app.app_context():
        assert [r.status for r in db.session.scalars(db.select(Response))] == [Response.ACTIVE]
        s = db.session.get(Survey, 1)
        r = db.session.scalar(db.select(Response))
        assert panel.resumable(s, {"PID": "p7"}) is None
        assert panel.resumable(s, {"PID": "p7"}, pid="not-the-owner") is None
        assert panel.resumable(s, {"PID": "p7"}, pid=r.pid).id == r.id
    assert "Alter" in first.get(f"/s/{uid}?PID=p7").data.decode()
    first.post(f"/s/{uid}", data={"nav": "next", "i2": "30"})
    first.post(f"/s/{uid}", data={"nav": "next"})
    with app.app_context():
        assert [r.status for r in db.session.scalars(db.select(Response))] == [Response.COMPLETED]
    assert USED in app.test_client().get(f"/s/{uid}?PID=p7").data.decode()                  # ended: taken part
    assert USED not in app.test_client().get(f"/s/{uid}?PID=p8").data.decode()


# --- T-12: closing by sample size and by spent quotas with completions at the same moment ----------------


def _sample_survey(app, client, max_responses):
    from tests.participation.test_end_links import build as build_simple

    build_simple(app, client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01", "use_max": "1",
                                          "max_responses": str(max_responses)})
    with app.app_context():
        return db.session.get(Survey, 1).code


def _on_last_page(app, uid):
    p = _participant(app, uid)
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "30"})
    return p


def test_two_last_completions_at_the_same_moment_close_the_sample(app, client, survey, monkeypatch):
    uid = _sample_survey(app, client, 3)
    _on_last_page(app, uid).post(f"/s/{uid}", data={"nav": "next"})
    a, b = _on_last_page(app, uid), _on_last_page(app, uid)
    _gate(monkeypatch, Survey, "completed_count")
    _parallel(lambda: a.post(f"/s/{uid}", data={"nav": "next"}), lambda: b.post(f"/s/{uid}", data={"nav": "next"}))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.completed_count() == 3
        assert (s.status, s.close_reason) == (Survey.CLOSED, "sample")                     # PostgreSQL left it open


def test_two_last_completions_at_the_same_moment_close_spent_quotas(app, client, survey, monkeypatch):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")]), ("Frauen", "f", 1, [(2, "eq", "2")])],
          extra=(NONE_ENDS,))
    uid = start(app, client)
    men, women = screen(app, uid, "1"), screen(app, uid, "2")
    for p in (men, women):
        p.post(f"/s/{uid}", data={"nav": "next"})                                           # on the last page
    _gate(monkeypatch, lifecycle, "_quotas_exhausted")
    _parallel(lambda: men.post(f"/s/{uid}", data={"nav": "next", "i5": "1"}),
              lambda: women.post(f"/s/{uid}", data={"nav": "next", "i5": "1"}))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [c.done for q in s.quotas for c in q.cells] == [1, 1]
        assert (s.status, s.close_reason) == (Survey.CLOSED, "quota")


def test_the_housekeeping_closes_spent_quotas_too(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")]), ("Frauen", "f", 1, [(2, "eq", "2")])],
          extra=(NONE_ENDS,))
    uid = start(app, client)
    finish(uid, screen(app, uid, "1"))
    finish(uid, screen(app, uid, "2"))
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status, s.close_reason, s.closed_at = Survey.RUNNING, "", None                    # as if a close was missed
        db.session.commit()
        assert lifecycle.close_due_surveys() == 1
        s = db.session.get(Survey, 1)
        assert (s.status, s.close_reason) == (Survey.CLOSED, "quota")


# --- T-25, T-29, T-39: giving back reservations that ran out -------------------------------------------


def _held(app):
    with app.app_context():
        r = db.session.scalars(db.select(Response).filter_by(source="real")).one()
        return r.id, dict(r.quota), r.version


def test_a_reservation_is_not_given_back_after_the_participation_changed(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])])
    uid = start(app, client)
    screen(app, uid, "1")
    rid, stored, version = _held(app)
    assert counters(app)["running"] == 1
    with app.app_context():
        r = db.session.get(Response, rid)
        r.answers = {**r.answers, "x": 1}                                                   # the person sent a page
        db.session.commit()
        assert quotas._give_back_held(rid, stored, version) == 0                            # read before: nothing
        db.session.commit()
    assert counters(app)["running"] == 1 and _held(app)[1]["1"]["held"] is True
    with app.app_context():
        assert quotas._give_back_held(rid, *_held(app)[1:]) == 1
        db.session.commit()
    assert counters(app)["running"] == 0 and _held(app)[1]["1"]["held"] is False


@needs_pg
def test_a_participation_at_work_is_skipped_not_waited_for(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])])
    uid = start(app, client)
    screen(app, uid, "1")
    idle(app, 120)
    rid = _held(app)[0]
    with app.app_context(), db.engine.connect() as other:
        other.execute(text("SELECT id FROM response WHERE id = :id FOR UPDATE"), {"id": rid})  # a request of the person
        db.session.execute(text("SET lock_timeout = '2s'"))
        assert quotas.release_expired() == 0                                                # no wait, no deadlock
        db.session.rollback()
        other.rollback()
        assert quotas.release_expired() == 1
        db.session.commit()
    assert counters(app)["running"] == 0


def test_no_expiry_while_the_person_is_outside_and_may_come_back(app, client, survey):
    from app.external import MAX_SECONDS

    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])])
    uid = start(app, client)
    screen(app, uid, "1")
    idle(app, 180)
    with app.app_context():
        r = db.session.scalars(db.select(Response)).one()
        r.outside_since = utcnow() - timedelta(minutes=170)                                 # in the web app since then
        db.session.commit()
        assert quotas.release_expired() == 0
        r = db.session.scalars(db.select(Response)).one()
        r.outside_since = utcnow() - timedelta(seconds=MAX_SECONDS + 60)                    # the return link ran out
        db.session.commit()
        assert quotas.release_expired() == 1
        db.session.commit()
    assert counters(app)["running"] == 0


def test_release_reads_survey_by_survey_and_only_participations_with_a_place(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")])])
    uid = start(app, client)
    screen(app, uid, "1")
    idle(app, 120)
    client.post("/surveys/new", data={"title": "Ohne Quoten", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        assert quotas.release_expired(survey=db.session.get(Survey, 2)) == 0                # a survey without quotas
        statements = []

        def note(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", note)
        try:
            assert quotas.release_expired(survey=db.session.get(Survey, 1)) == 1
        finally:
            event.remove(db.engine, "before_cursor_execute", note)
        db.session.commit()
    read = [s for s in statements if "FROM response" in s and "LIKE" in s]
    assert read and all("response.survey_id = " in s for s in read)
    assert counters(app)["running"] == 0


# --- T-37: a full cell at the door gives back reservations that ran out first ----------------------------


def test_the_door_gives_back_reservations_that_ran_out_before_turning_away(app, client, survey, monkeypatch):
    """Also code review 2026-10-15, F3: at most once in RELEASE_TRY_SECONDS per process and survey."""
    questionnaire(client)
    client.post("/surveys/1/panel", data={"param_name": "src", "param_mode": "store", "param_required": "1",
                                          "param_allowed": "a, b"})
    client.post(Q + "/add")
    client.post(Q + "/1", data=cells_form([("Quelle A", "a", 5, [("p:src", "eq", "a")]),
                                          ("Quelle B", "b", 1, [("p:src", "eq", "b")])]))
    from app import flow

    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "quota", "id": "qn1", "quotas": [1]},
            {"t": "end", "id": "qe1", "kind": "quotafull", "quotas": [1], "rule": "any", "text": {}, "link": "",
             "link_mode": "now"}, {"t": "block", "block": 2}, {"t": "block", "block": 3}, {"t": "block", "block": 4}]}
        flow.normalize(s)
        db.session.commit()
    uid = start(app, client)
    gone = _participant(app, uid, "?src=b")
    gone.post(f"/s/{uid}", data=CONSENT_OK)                                                 # takes the one place of B
    assert counters(app, "b")["running"] == 1
    assert "bereits genügend" in app.test_client().get(f"/s/{uid}?src=b").data.decode()     # held: full at the door
    idle(app, 120)                                                                          # no housekeeping since
    from app import participate

    calls = []
    real = quotas.release_expired
    monkeypatch.setattr(quotas, "release_expired", lambda **kw: calls.append(1) or real(**kw))
    assert "bereits genügend" in app.test_client().get(f"/s/{uid}?src=b").data.decode()     # tried just now: not again
    assert calls == []
    participate._release_tried[1] -= participate.RELEASE_TRY_SECONDS                       # half a minute later
    page = app.test_client().get(f"/s/{uid}?src=b").data.decode()
    assert "bereits genügend" not in page and calls == [1]
    assert counters(app, "b")["running"] == 0


# --- L-13: two first sign-ins at the same moment ---------------------------------------------------------


def test_a_first_sign_in_whose_numbers_were_just_taken_tries_again(app, client, monkeypatch):
    original = auth.new_account_ids
    calls = []

    def taken_once():
        calls.append(1)
        if len(calls) == 1:  # the other first sign-in committed these a moment ago
            with app.app_context():
                first = db.session.scalar(db.select(User).order_by(User.id))
                return {"id": first.id, "number": first.number}
        return original()

    monkeypatch.setattr(auth, "new_account_ids", taken_once)
    r = app.test_client().get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Zweite")
    assert r.status_code == 302 and len(calls) == 2
    with app.app_context():
        assert db.session.scalar(db.select(User.name).filter_by(orcid="0000-0002-1825-0097")) == "Zweite"


def test_the_same_first_sign_in_twice_at_once_gives_one_account(app, client, monkeypatch):
    original = auth.new_account_ids
    orcid = "0000-0002-1825-0097"

    def other_tab_first():
        ids = original()
        if not db.session.info.get("raced"):
            db.session.info["raced"] = True
            with db.engine.connect() as other:                                              # the other tab's request
                other.execute(db.insert(User).values(orcid=orcid, name="Tab", **ids))
                other.commit()
            return {"id": ids["id"] + 1, "number": ids["number"] + 1}
        return ids

    monkeypatch.setattr(auth, "new_account_ids", other_tab_first)
    r = app.test_client().get(f"/auth/dev-login?orcid={orcid}&name=Zweite")
    assert r.status_code == 302
    with app.app_context():
        assert db.session.scalars(db.select(User.name).filter_by(orcid=orcid)).all() == ["Zweite"]


def test_an_account_without_orcid_whose_numbers_were_just_taken_tries_again(app, client, monkeypatch):
    """L-13 for the accounts an admin makes (accounts.create_local): a first sign-in took id and number between
    reading and inserting; the account is made with the next ones instead of a 500."""
    from app import accounts
    from app.models import UserLogin

    original = auth.new_account_ids
    calls = []

    def taken_once():
        calls.append(1)
        if len(calls) == 1:
            first = db.session.scalar(db.select(User).order_by(User.id))
            return {"id": first.id, "number": first.number}
        return original()

    monkeypatch.setattr(auth, "new_account_ids", taken_once)
    with app.app_context():
        admin = db.session.scalar(db.select(User).order_by(User.id))
        user = accounts.create_local("Lokale Person", "lokal@example.org", "", False, admin)
        db.session.commit()
        assert len(calls) == 2 and user.id != admin.id and user.number != admin.number
        assert db.session.get(UserLogin, user.id).email == "lokal@example.org"


# --- T-36: overlapping and incomplete cells -------------------------------------------------------------


def _coverage(app, client, cells, **settings):
    questionnaire(client)
    client.post(Q + "/add")
    r = client.post(Q + "/1", data=cells_form(cells, **settings), follow_redirects=True)
    assert "gespeichert" in r.data.decode()
    with app.app_context():
        found = [m for level, m in quotas.check(db.session.get(Survey, 1))
                 if level == "warning" and ("überschneiden" in m or "passt in keine Teilquote" in m)]
        assert found == quotas.coverage(db.session.get(Survey, 1), db.session.get(Survey, 1).quotas[0])
        return found


def test_overlapping_age_ranges_are_named(app, client, survey):
    found = _coverage(app, client, [("Jung", "j", 1, [(3, "between", "18..30")]), ("Alt", "a", 1, [(3, "between", "30..45")])])
    assert len(found) == 1
    assert "„Jung“ und „Alt“ überschneiden sich, z. B. bei age = 30" in found[0] and "„Jung“." in found[0]


def test_a_gap_between_age_ranges_is_named(app, client, survey):
    found = _coverage(app, client, [("Jung", "j", 1, [(3, "between", "18..29")]), ("Alt", "a", 1, [(3, "between", "31..45")])])
    assert found == ["Quote „Geschlecht x Alter“: Wer so antwortet (age = 30), passt in keine Teilquote und zählt dort "
                     "als „keine Teilquote“. Teilquote ergänzen, wenn diese Personen zählen sollen."]


def test_answer_codes_without_a_cell_are_named(app, client, survey):
    found = _coverage(app, client, [("Junge Männer", "j", 1, [(2, "eq", "1"), (3, "between", "18..29")]),
                                    ("Ältere Männer", "a", 1, [(2, "eq", "1"), (3, "ge", "30")])])
    assert len(found) == 1 and "gender „weiblich“, age = 18" in found[0]                    # below 18: screened out
    assert "age = 17" not in found[0]


def test_overlap_with_pick_least_says_so(app, client, survey):
    found = _coverage(app, client, [("Alle Männer", "m", 1, [(2, "eq", "1")]),
                                    ("Männer und Frauen", "x", 1, [(2, "in", "1, 2")])], pick="least")
    assert len(found) == 1 and "gender „männlich“" in found[0] and "kommt in die weniger gefüllte" in found[0]


def test_a_complete_cross_quota_has_no_message(app, client, survey):
    found = _coverage(app, client, [("m jung", "mj", 1, [(2, "eq", "1"), (3, "le", "29")]),
                                    ("m alt", "ma", 1, [(2, "eq", "1"), (3, "ge", "30")]),
                                    ("f jung", "fj", 1, [(2, "eq", "2"), (3, "le", "29")]),
                                    ("f alt", "fa", 1, [(2, "eq", "2"), (3, "ge", "30")])])
    assert found == []


# --- T-28: the speeder judgement at a completion only from SPEEDER_MIN completions on --------------------


def test_an_early_speeder_keeps_its_place_a_bot_does_not(app, client, survey):
    from tests.quotas.test_quota_marked import counters as marked_counters
    from tests.quotas.test_quota_marked import finish as finish_marked
    from tests.quotas.test_quota_marked import setup

    uid = setup(app, client, on=True)
    for _ in range(2):                                                                      # women: 10 minutes each
        p = screen(app, uid, gender="2")
        with app.app_context():
            r = db.session.scalars(db.select(Response).filter_by(status=Response.ACTIVE)).one()
            r.started_at = utcnow() - timedelta(seconds=600)
            db.session.commit()
        finish_marked(uid, p)
    finish_marked(uid, screen(app, uid))                                                    # a man, in a few seconds
    assert quotas.SPEEDER_MIN == 30
    assert marked_counters(app) == {"running": 0, "done": 1, "freed": 0}                   # 3 completions: not judged
    with app.app_context():
        cell = db.session.scalar(db.select(QuotaCell).filter_by(code="m"))
        cell.target = 2
        db.session.commit()
    finish_marked(uid, screen(app, uid), bot=True)                                          # settled at once: frees it
    assert marked_counters(app) == {"running": 0, "done": 1, "freed": 1}
