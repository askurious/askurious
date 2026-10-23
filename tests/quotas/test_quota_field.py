"""Quotas during fieldwork: when a participation counts, reservations that run out, steering, recounting, the
state per provider, and links that show the state without signing in (app/quotas.py, app/quota_views.py)."""
from datetime import timedelta

from app import flow, quotas
from app.extensions import db
from app.models import Quota, QuotaCell, QuotaShare, Response, Survey, utcnow
from tests.quotas.test_quotas import Q, build, cells_form, counters, finish, questionnaire, screen, set_flow, start, statuses

MEN = [("Männer", "m", 1, [(2, "eq", "1")])]
OTHER = "0000-0002-1825-0097"
# who fits no cell of quota 1 is screened out: a condition in the flow (the calculation ends nobody)
NONE_ENDS = {"t": "branch", "id": "bn1", "cond": {"item": "q:1", "op": "eq", "value": "none"},
             "children": [{"t": "end", "id": "en1", "text": {}, "kind": "screenout"}]}


def started_before(app, full="end", count_at="check"):
    """Make the survey one that was started with a "Quotenprüfung" (quotas.is_legacy): its node ends a full
    quota itself, and its quotas may count at once. Such surveys keep that for as long as they are in the field."""
    import copy

    with app.app_context():
        s = db.session.get(Survey, 1)
        root = copy.deepcopy(flow.tree(s))
        root["children"] = [n for n in root["children"] if n.get("id") != "qe1"]
        flow.find(root, "qn1")[0].update(full=full, text={}, link="", link_mode="now")
        s.flow = root
        for quota in s.quotas:
            quota.count_at = count_at
        db.session.commit()


def idle(app, minutes, which=None):
    """Make participations look as if their last page was sent `minutes` ago."""
    with app.app_context():
        for r in db.session.scalars(db.select(Response).filter_by(source="real")):
            if which is None or r.id in which:
                r.last_active_at = utcnow() - timedelta(minutes=minutes)
        db.session.commit()


def sweep(app):
    with app.app_context():
        n = quotas.release_expired()
        db.session.commit()
        return n


# --- when a participation counts ------------------------------------------------------------------------


def test_counting_at_the_check_counts_for_good(app, client, survey):
    end = {"t": "branch", "id": "b9", "cond": {"item": 3, "op": "lt", "value": "18"},
           "children": [{"t": "end", "id": "e9", "text": {}, "kind": "screenout"}]}
    build(app, client, cells=[("Männer", "m", 2, [(2, "eq", "1")])], extra=(end,), count_at="check")
    with app.app_context():
        assert db.session.get(Quota, 1).count_at == "complete"                               # no longer to be chosen
    uid = start(app, client)
    started_before(app)
    young = screen(app, uid, age="16")
    assert counters(app) == {"running": 0, "done": 1, "refused": 0, "over": 0}               # counted at once
    young.post(f"/s/{uid}", data={"nav": "next"})
    assert statuses(app) == ["screenout"] and counters(app)["done"] == 1                     # and it stays counted
    dropout = screen(app, uid)
    assert counters(app)["done"] == 2
    idle(app, 600)
    assert sweep(app) == 0 and counters(app)["done"] == 2                                    # nothing to give back
    assert "bereits genügend" in screen(app, uid).get(f"/s/{uid}").data.decode()             # full after two checks
    finish(uid, dropout)
    assert counters(app) == {"running": 0, "done": 2, "refused": 1, "over": 0}               # completing changes nothing
    with app.app_context():
        r = db.session.scalars(db.select(Response).order_by(Response.id)).first()
        assert r.quota["1"] == {"cell": 1, "res": "in", "held": False, "done": True, "at": "check"}
        assert not quotas.recount(db.session.get(Survey, 1))                                 # the counters match
    html = client.get("/surveys/1/flow/quotas/").data.decode()   # the state is on the tab "Quoten" (UI audit G4)
    assert "zählt sofort an der Quotenberechnung" in html


def test_counting_at_completion_is_the_default_and_dropouts_do_not_fill(app, client, survey):
    build(app, client, cells=MEN)
    with app.app_context():
        assert db.session.get(Quota, 1).count_at == "complete"
    uid = start(app, client)
    screen(app, uid)                                                                         # drops out behind the check
    assert counters(app) == {"running": 1, "done": 0, "refused": 0, "over": 0}
    idle(app, 61)
    assert sweep(app) == 1 and sweep(app) == 0
    assert counters(app) == {"running": 0, "done": 0, "refused": 0, "over": 0}               # the place is free again
    assert "Vielen Dank für Ihre Teilnahme" in finish(uid, screen(app, uid)).data.decode()
    assert counters(app)["done"] == 1


def test_declined_participation_no_longer_counts_at_the_check(app, client, survey):
    build(app, client, cells=MEN, count_at="check")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 2}, {"t": "quota", "id": "qn1", "quotas": [1], "full": "end"},
                                            {"t": "block", "block": 1}, {"t": "block", "block": 3}, {"t": "block", "block": 4}]}
        flow.normalize(s)
        s.status, s.opened_at = Survey.RUNNING, s.created_at
        db.session.get(Quota, 1).count_at = "check"                                          # a survey started before
        db.session.commit()
        uid = s.code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "1", "i3": "30"})
    assert counters(app)["done"] == 1
    p.post(f"/s/{uid}", data={"i1": "0"})
    assert counters(app) == {"running": 0, "done": 0, "refused": 0, "over": 0}


# --- reservations that run out --------------------------------------------------------------------------


def test_reservation_runs_out_and_comes_back_when_there_is_room(app, client, survey):
    app.config["QUOTA_FRESH_HOLD_MINUTES"] = 30  # the quota's own hold time here, not the shorter one before a page
    build(app, client, cells=MEN, hold_minutes="30")
    uid = start(app, client)
    p = screen(app, uid)
    idle(app, 29)
    assert sweep(app) == 0 and counters(app)["running"] == 1                                 # not yet
    idle(app, 31)
    assert sweep(app) == 1 and counters(app)["running"] == 0
    p.post(f"/s/{uid}", data={"nav": "next"})                                                # back at the desk: the place is free
    assert counters(app)["running"] == 1
    p.post(f"/s/{uid}", data={"nav": "next", "i5": "1"})
    assert counters(app) == {"running": 0, "done": 1, "refused": 0, "over": 0}


def test_who_comes_back_too_late_may_finish_and_counts_above_the_target(app, client, survey):
    build(app, client, cells=MEN)
    uid = start(app, client)
    late = screen(app, uid)
    idle(app, 90)
    sweep(app)
    quick = screen(app, uid)                                                                 # takes the freed place
    assert "Hauptteil" in quick.get(f"/s/{uid}").data.decode()
    assert "Vielen Dank für Ihre Teilnahme" in finish(uid, late).data.decode()               # no room, finishes all the same
    assert counters(app) == {"running": 1, "done": 1, "refused": 0, "over": 1}
    finish(uid, quick)
    assert counters(app) == {"running": 0, "done": 2, "refused": 0, "over": 1}
    with app.app_context():
        first = db.session.scalars(db.select(Response).order_by(Response.id)).first()
        assert first.quota["1"]["res"] == "over"
        assert not quotas.recount(db.session.get(Survey, 1))
    assert "(+1)" in client.get("/surveys/1/flow/quotas/").data.decode()
    rows = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()
    cols = rows[0].split(",")
    assert sorted(r.split(",")[cols.index("quota_qa_res")] for r in rows[1:]) == ["1", "3"]


def test_two_sweeps_at_once_give_a_place_back_once(app, client, survey):
    build(app, client, cells=[("Männer", "m", 3, [(2, "eq", "1")])])
    uid = start(app, client)
    screen(app, uid), screen(app, uid)
    idle(app, 61, which={1})
    with app.app_context():
        from sqlalchemy.orm import Session

        assert quotas.release_expired() == 1                                                 # not committed yet in this worker ...
        db.session.commit()
        with Session(db.engine) as other:                                                    # ... another worker sees it released
            stored = other.get(Response, 1).quota
            assert stored["1"]["held"] is False
        assert quotas.release_expired() == 0
        db.session.commit()
    assert counters(app)["running"] == 1                                                     # the second person keeps the place


def test_the_minute_check_and_cron_release_places(app, client, survey):
    build(app, client, cells=MEN)
    uid = start(app, client)
    screen(app, uid)
    idle(app, 120)
    assert client.get("/cron/close?key=k").status_code == 200
    assert counters(app)["running"] == 0


# --- steering during fieldwork --------------------------------------------------------------------------


def test_admins_steer_targets_and_close_cells(app, client, survey):
    build(app, client)
    uid = start(app, client)
    screen(app, uid), screen(app, uid)
    assert "bereits genügend" in screen(app, uid).get(f"/s/{uid}").data.decode()             # target 2
    r = client.post(Q + "/1/fieldwork", data={"mode": "hard", "tolerance": "0", "tolerance_unit": "n", "hold_minutes": "45",
                                          "target_1": "3", "target_2": "2"}, follow_redirects=True)
    assert "Soll „Männer“ 2 → 3" in r.data.decode()
    assert "Hauptteil" in screen(app, uid).get(f"/s/{uid}").data.decode()                    # one more gets in
    client.post(Q + "/1/fieldwork", data={"mode": "soft", "tolerance": "5", "tolerance_unit": "pct", "hold_minutes": "45",
                                     "target_1": "3", "target_2": "2", "closed_2": "1"})
    assert "bereits genügend" in screen(app, uid, "2").get(f"/s/{uid}").data.decode()        # closed by hand, although empty
    with app.app_context():
        s = db.session.get(Survey, 1)
        quota = s.quotas[0]
        assert (quota.mode, quota.tolerance, quota.tolerance_pct, quota.hold_minutes) == ("soft", 5, True, 45)
        assert [c.code for c in quota.cells] == ["m", "f"] and quota.cells[1].closed
        notes = [e["reason"] for e in s.status_log if e["event"] == "quota"]
        assert notes[0] == "Geschlecht x Alter: Platz gehalten 15 → 45 min; Soll „Männer“ 2 → 3"
        assert "Art hart → weich" in notes[1] and "Teilquote „Frauen“ geschlossen" in notes[1]
        assert s.status == Survey.RUNNING
    assert "Quoten geändert: Geschlecht x Alter" in client.get("/surveys/1/field").data.decode()   # the history
    assert "quota-flag-closed" in client.get("/surveys/1/flow/quotas/").data.decode()             # the state


def test_steering_needs_an_admin_and_a_survey_in_the_field(app, client, survey):
    build(app, client)
    client.post(Q + "/1/fieldwork", data={"target_1": "9", "target_2": "9"})                      # a draft: edited as a whole
    with app.app_context():
        assert db.session.get(QuotaCell, 1).target == 2
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "editor"})
    start(app, client)
    editor = app.test_client()
    editor.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    editor.post("/terms/", data={"accept": "1"})
    editor.post(Q + "/1/fieldwork", data={"target_1": "9", "target_2": "9"})
    editor.post(Q + "/recount")
    editor.post(Q + "/link", data={"label": "x"})
    with app.app_context():
        assert db.session.get(QuotaCell, 1).target == 2 and db.session.scalar(db.select(db.func.count(QuotaShare.id))) == 0
    page = editor.get(Q + "/").data.decode()
    assert "nur zu lesen" in page and "Steuerung speichern" not in page
    assert "Kopieren" in page and "Neu zählen" not in page and "/quotas/link\"" not in page


def test_recount_rebuilds_the_counters(app, client, survey):
    build(app, client)
    uid = start(app, client)
    finish(uid, screen(app, uid))
    screen(app, uid), screen(app, uid).get(f"/s/{uid}")                                      # one under way, one refused
    with app.app_context():
        db.session.execute(db.update(QuotaCell).where(QuotaCell.id == 1).values(running=7, done=0, refused=5, over=2))
        db.session.commit()
    r = client.post(Q + "/recount", follow_redirects=True)
    assert "4 Zähler korrigiert" in r.data.decode()
    assert counters(app) == {"running": 1, "done": 1, "refused": 1, "over": 0}
    assert "Alle Zähler stimmten" in client.post(Q + "/recount", follow_redirects=True).data.decode()


# --- the survey closes when nobody can get in any more ----------------------------------------------------


def test_survey_closes_when_all_cells_are_completed(app, client, survey):
    build(app, client, cells=[("Männer", "m", 1, [(2, "eq", "1")]), ("Frauen", "f", 1, [(2, "eq", "2")])], extra=(NONE_ENDS,))
    client.post("/surveys/1/panel", data={"redirect_quotafull": "https://panel.example/full", "redirect_closed": "https://panel.example/closed"})
    uid = start(app, client)
    finish(uid, screen(app, uid, "1"))
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.RUNNING                           # the women's cell is open
    last = screen(app, uid, "2")
    assert "bereits genügend" not in last.get(f"/s/{uid}").data.decode()
    finish(uid, last)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert (s.status, s.close_reason) == (Survey.CLOSED, "quota")
    r = app.test_client().get(f"/s/{uid}")
    assert r.status_code == 302 and r.headers["Location"] == "https://panel.example/full"  # "quota full", not "closed"
    assert "Quoten erfüllt" in client.get("/surveys/1/field").data.decode()


def test_survey_stays_open_when_people_without_a_cell_may_go_on(app, client, survey):
    build(app, client, cells=MEN)                                                           # nobody's cell: goes on
    uid = start(app, client)
    finish(uid, screen(app, uid))
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING and not quotas.exhausted(s)
        calc, end = {"t": "quota", "id": "qn1", "quotas": [1]}, flow.find(flow.tree(s), "qe1")[0]
        rest = [{"t": "block", "block": 3}, {"t": "block", "block": 4}]
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2}, calc, NONE_ENDS, end, *rest]}
        assert quotas.exhausted(s)                                                          # now nobody passes
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2}, calc, NONE_ENDS,
                                            {"t": "branch", "id": "b1", "cond": {"item": 3, "op": "gt", "value": "1"},
                                             "children": [end]}, *rest]}
        assert not quotas.exhausted(s)                                                      # the end is not for everybody
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2}, calc, NONE_ENDS,
                                            {**end, "quotas": [1, 99], "rule": "all"}, *rest]}
        assert quotas.exhausted(s)                                                          # all its quotas are spent
        # a survey started with a "Quotenprüfung": the check itself ends a full quota and people without a cell
        old = {"t": "quota", "id": "qn1", "quotas": [1], "full": "end"}
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2}, old, *rest]}
        assert not quotas.exhausted(s)
        s.quotas[0].no_cell = "end"
        assert quotas.exhausted(s)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                                            {"t": "branch", "id": "b1", "cond": {"item": 3, "op": "gt", "value": "1"},
                                             "children": [old]}, *rest]}
        assert not quotas.exhausted(s)                                                      # the check is not for everybody


# --- the state per provider -----------------------------------------------------------------------------


def providers_survey(app, client):
    questionnaire(client)
    client.post("/surveys/1/panel", data={"param_name": "src", "param_mode": "store", "param_required": "0",
                                          "param_allowed": "a, b"})
    client.post(Q + "/add")
    client.post(Q + "/1", data=cells_form([("Männer", "m", 5, [(2, "eq", "1")]), ("Frauen", "f", 1, [(2, "eq", "2")])]))
    set_flow(app)
    uid = start(app, client)
    finish(uid, screen(app, uid, "1", query="?src=a"))
    finish(uid, screen(app, uid, "1", query="?src=b"))
    screen(app, uid, "1", query="?src=b")
    finish(uid, screen(app, uid, "2", query="?src=a"))
    screen(app, uid, "2", query="?src=b").get(f"/s/{uid}")                                   # full for b
    return uid


def test_state_per_provider(app, client, survey):
    providers_survey(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert quotas.providers(s) == [("src", "a"), ("src", "b")]
        assert quotas.provider(s, "src=b") == ("src", "b") and quotas.provider(s, "src=zz") is None
        b = quotas.report(s, only=("src", "b"))[0]["cells"]
        assert [(r["done"], r["running"], r["refused"], r["target"], r["open"], r["state"]) for r in b] == \
            [(1, 1, 0, 5, 3, "offen"), (0, 0, 1, 1, 0, "voll")]                             # target, open, state: the cell's
        assert dict(quotas.totals(s, ("src", "b"))) == {"begonnen": 3, "abgeschlossen": 1, "Screen-out": 0, "Quote voll": 1,
                                                        "nicht abgeschlossen": 1}
        assert dict(quotas.totals(s))["begonnen"] == 5
    html = client.get("/surveys/1/flow/quotas/?qp=src%3Db").data.decode()
    assert "Gefiltert auf src = b" in html and '<option value="src=b" selected>' in html
    csv_b = client.get(Q + "/state.csv?qp=src%3Db").data.decode("utf-8-sig")
    assert "Nur Teilnahmen mit src = b" in csv_b and "Männer,m,gender: männlich,5,1,1,3,0,0,offen" in csv_b
    assert "Nur Teilnahmen" not in client.get(Q + "/state.csv?qp=src%3Dzz").data.decode("utf-8-sig")


def test_print_view_and_the_piece_the_field_page_reads_again(app, client, survey):
    providers_survey(app, client)
    page = client.get(Q + "/state?qp=src%3Da").data.decode()
    assert page.lstrip().startswith("<!doctype html>") and "Quotenstand" in page and "nur Teilnahmen mit src = a" in page
    assert 'name="robots" content="noindex, nofollow"' in page and "Drucken" in page and "/state.csv?qp=src%3Da" in page
    assert "site-header" not in page                                                        # no way into the app from here
    part = client.get(Q + "/state?part=1").data.decode()
    assert "data-quota-tables" in part and "quota-state-text" in part and "<html" not in part
    field = client.get("/surveys/1/flow/quotas/").data.decode()
    assert "data-quota-live=" in field and "/state?part=1" in field                          # only while the survey runs
    client.post("/surveys/1/close")
    assert "data-quota-live=" not in client.get("/surveys/1/flow/quotas/").data.decode()


# --- links that show the state without signing in ---------------------------------------------------------


def test_share_link_shows_numbers_only_and_can_be_revoked(app, client, survey):
    providers_survey(app, client)
    r = client.post(Q + "/link", data={"label": "Panel B", "qp": "src=b", "conds": "1"}, follow_redirects=True)
    assert "Link angelegt" in r.data.decode() and "Panel B" in r.data.decode()
    with app.app_context():
        share = db.session.scalar(db.select(QuotaShare))
        assert (share.param, share.value, share.show_conds, share.revoked) == ("src", "b", True, False)
        assert share.expires_at > utcnow() + timedelta(days=30) and len(share.token) >= 30
        token = share.token
    visitor = app.test_client()                                                             # not signed in
    r = visitor.get(f"/q/{token}")
    page = r.data.decode()
    assert r.status_code == 200 and r.headers["X-Robots-Tag"] == "noindex, nofollow" and r.headers["Cache-Control"] == "no-store"
    assert "nur Teilnahmen mit src = b" in page and "gender: männlich" in page and "site-header" not in page
    assert "Diese Seite zeigt nur Zahlen" in page and "csrf" not in page.lower()
    body = visitor.get(f"/q/{token}/state.csv?sep=semicolon").data.decode("utf-8-sig")
    assert "Männer;m;gender: männlich;5;1;1;3;0;0;offen" in body
    # nothing else opens with the token, and no other token works
    assert visitor.get(f"/q/{token}x").status_code == 404 and visitor.get("/q/" + "a" * 32).status_code == 404
    assert visitor.get("/surveys/1/field").status_code in (302, 401)
    client.post(Q + "/link/1/withdraw")
    assert visitor.get(f"/q/{token}").status_code == 404 and visitor.get(f"/q/{token}/state.csv").status_code == 404
    assert "Panel B" not in client.get("/surveys/1/flow/quotas/").data.decode()


def test_share_link_without_conditions_and_with_an_end(app, client, survey):
    providers_survey(app, client)
    client.post(Q + "/link", data={"label": "alle", "days": "3"})                            # no "conds": names and codes only
    with app.app_context():
        share = db.session.scalar(db.select(QuotaShare))
        assert share.param == "" and not share.show_conds
        assert timedelta(days=2) < share.expires_at - utcnow() <= timedelta(days=3)
        token = share.token
    visitor = app.test_client()
    page = visitor.get(f"/q/{token}").data.decode()
    assert "Männer" in page and "gender:" not in page and "Bedingungen" not in page
    assert "gender" not in visitor.get(f"/q/{token}/state.csv").data.decode("utf-8-sig")
    assert client.post(Q + "/link", data={"qp": "src=nope"}, follow_redirects=True).data.decode().count("gibt es nicht") == 1
    with app.app_context():
        db.session.scalar(db.select(QuotaShare)).expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert visitor.get(f"/q/{token}").status_code == 404                                    # run out
    client.post("/surveys/1/duplicate", data={"title": "Zweite Welle"})
    with app.app_context():
        twin = db.session.scalar(db.select(Survey).filter_by(title="Zweite Welle"))
        assert not twin.quota_shares and twin.quotas[0].hold_minutes == quotas.DEFAULT_HOLD and twin.quotas[0].count_at == "complete"


def test_share_link_validity_as_for_a_dashboard(app, client, survey):
    """U16: a quota link is made with the same choice of validity as a dashboard link (boards.VALIDITY)."""
    providers_survey(app, client)
    field = client.get("/surveys/1/flow/quotas/").data.decode()
    assert '<select name="valid">' in field and "unbegrenzt (bis zum Widerruf)" in field and "Link erzeugen" in field
    client.post(Q + "/link", data={"label": "immer", "valid": "never"})
    client.post(Q + "/link", data={"label": "eine Woche", "valid": "7"})
    with app.app_context():
        shares = {s.label: s for s in db.session.scalars(db.select(QuotaShare))}
        assert shares["immer"].expires_at is None
        assert 6 <= (shares["eine Woche"].expires_at - utcnow()).days <= 7
    assert "unbegrenzt gültig" in client.get("/surveys/1/flow/quotas/").data.decode()


# --- reservations bound to progress (audit 2026-10-18, BIZ-06) --------------------------------------------


def sweep_at(app, minutes):
    """release_expired as it runs `minutes` from now."""
    with app.app_context():
        n = quotas.release_expired(now=utcnow() + timedelta(minutes=minutes))
        db.session.commit()
        return n


def test_a_reservation_no_page_followed_is_given_back_after_a_few_minutes(app, client, survey):
    """BIZ-06: two scripts pass the screening for a cell of two and stop. Their places go back after the fresh
    hold (QUOTA_FRESH_HOLD_MINUTES), not after the quota's hour, and the next real person gets in. Who went on
    with a page keeps the place for the quota's own hold time."""
    build(app, client, hold_minutes="60")                                   # cells m, f: target 2 each, hard
    uid = start(app, client)
    bots = [screen(app, uid, gender="1") for _ in range(2)]
    assert counters(app)["running"] == 2
    real = screen(app, uid, gender="1")
    assert statuses(app)[-1] == Response.QUOTAFULL                          # at once: the places are held
    keen = screen(app, uid, gender="2")
    keen.post(f"/s/{uid}", data={"nav": "next"})                            # a page after the calculation
    assert sweep_at(app, quotas.FRESH_HOLD + 1) == 2                        # the two scripts, not the keen one
    assert counters(app)["running"] == 0 and counters(app, "f")["running"] == 1
    late = screen(app, uid, gender="1")
    assert statuses(app)[-1] == Response.ACTIVE and counters(app)["running"] == 1
    finish(uid, late)
    assert statuses(app)[-1] == Response.COMPLETED
    assert counters(app)["done"] == 1 and counters(app)["over"] == 0
    assert sweep_at(app, 59) == 0                                           # the keen one keeps the place for the hour
    assert sweep_at(app, 61) == 1
    del bots, real


def test_the_fresh_hold_never_lengthens_a_shorter_quota_hold(app, client, survey):
    """BIZ-06: a quota holding places for two minutes keeps its two minutes, also for a fresh reservation."""
    app.config["QUOTA_FRESH_HOLD_MINUTES"] = 10
    build(app, client, hold_minutes="2")
    uid = start(app, client)
    screen(app, uid, gender="1")
    assert sweep_at(app, 1) == 0 and sweep_at(app, 3) == 1


def test_new_quotas_hold_a_place_a_quarter_of_an_hour():
    """BIZ-06: the default hold time is shorter than the former hour."""
    assert quotas.DEFAULT_HOLD == 15 and quotas.FRESH_HOLD == 5
