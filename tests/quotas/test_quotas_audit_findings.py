"""Quotas: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.helpers.audit_audit_2026_10_06_a import EditStep, Survey, db, undo  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_b import _fresh_limits, Quota, QuotaCell, Response, _running, utcnow  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_d import quotas  # noqa: F401
from tests.helpers.audit_audit_small2 import export  # noqa: F401
from tests.helpers.audit_cr8_a import app  # noqa: F401
from tests.helpers.audit_cr8_c import event  # noqa: F401
from tests.helpers.audit_review_medium import CONSENT_OK, MultiDict, _overlap, runtime, test_quotas  # noqa: F401


# --- from tests/audits/test_audit_2026_10_06_a.py: Security audit 2026-10-06, package A "Rollen und Objekte": S1, S2, S9, S10, S12, S22, H5 and the side finding on ---


def test_s9_quota_setting_outside_a_draft_needs_admin(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        step = EditStep(survey_id=1, user_id=1, label="Quoten", need="editor",
                        data={"parts": {"settings:quality": [{"quota_drop_marked": False}, {"quota_drop_marked": True}]}})
        assert undo.need_now(s, step) == "editor"
        s.status = Survey.RUNNING
        assert undo.need_now(s, step) == "admin"


# --- from tests/audits/test_audit_2026_10_06_b.py: Package B of the security and performance audit of 2026-10-06: limits for the public pages. ---


def test_quotas_are_recounted_only_for_a_participation_with_a_quota_decision(app, client, survey, monkeypatch):
    from app import erasure, quotas

    _running(app, client)
    calls = []
    monkeypatch.setattr(quotas, "recount", lambda s: calls.append(s.id) or [])
    with app.app_context():
        quota = Quota(survey_id=1, name="Geschlecht", code="g")
        quota.cells = [QuotaCell(code="w", name="Frauen", target=10)]
        db.session.add(quota)
        plain = Response(survey_id=1, source="real", status=Response.ACTIVE, answers={}, pid="a1")
        held = Response(survey_id=1, source="real", status=Response.ACTIVE, answers={}, pid="a2")
        db.session.add_all([plain, held])
        db.session.flush()
        held.quota = {str(quota.id): {"cell": quota.cells[0].id, "res": "in", "held": True}}
        db.session.commit()
        survey_row = db.session.get(Survey, 1)
        erasure.erase(survey_row, [db.session.get(Response, plain.id)], "self")
        assert calls == []
        erasure.erase(survey_row, [db.session.get(Response, held.id)], "self")
        assert calls == [1]


def test_a_leap_of_running_places_in_a_cell_is_shown(app, client, survey):
    from datetime import timedelta

    from app import quotas

    _running(app, client)
    with app.app_context():
        quota = Quota(survey_id=1, name="Geschlecht", code="g")
        quota.cells = [QuotaCell(code="w", name="Frauen", target=20, done=4), QuotaCell(code="m", name="Männer", target=20)]
        db.session.add(quota)
        db.session.flush()
        women, men = quota.cells[0].id, quota.cells[1].id
        for n in range(12):
            db.session.add(Response(survey_id=1, source="real", status=Response.ACTIVE, answers={}, pid=f"w{n}",
                                    quota={str(quota.id): {"cell": women, "res": "in", "held": True}}))
        for n in range(12):  # held for long: no leap
            db.session.add(Response(survey_id=1, source="real", status=Response.ACTIVE, answers={}, pid=f"m{n}",
                                    started_at=utcnow() - timedelta(minutes=30),
                                    quota={str(quota.id): {"cell": men, "res": "in", "held": True}}))
        db.session.commit()
        found = quotas.jumps(db.session.get(Survey, 1))
    assert [(j["cell"], j["n"], j["open"]) for j in found] == [("Frauen", 12, 16)]
    for url in ("/surveys/1/field", "/surveys/1/flow/quotas/"):
        html = client.get(url).data.decode()
        assert "Frauen</strong> – in den letzten 10 Minuten haben 12 laufende Teilnahmen" in html, url
    with app.app_context():
        assert quotas.jumps(db.session.get(Survey, 1), now=utcnow() + timedelta(minutes=11)) == []


# --- from tests/audits/test_audit_2026_10_12_d.py: Audit data and calculations 2026-10-12, package D (deletion, quotas, kept results): E-7 to E-11. ---


def test_undo_of_quota_delete_recounts(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        q = Quota(survey_id=s.id, code="qa", name="Geschlecht", mode="hard", position=0)
        cell = QuotaCell(code="m", name="maennlich", conds=[], target=5, position=0, done=3)
        q.cells = [cell]
        s.quotas.append(q)
        db.session.flush()
        for n in range(3):
            db.session.add(Response(survey_id=s.id, source="real", status=Response.COMPLETED, answers={},
                                    pid=f"1-x{n}", quota={str(q.id): {"cell": cell.id, "res": "in", "done": True}}))
        db.session.commit()
        qid, cid = q.id, cell.id
    r = client.post(f"/surveys/1/flow/quotas/{qid}/delete")
    assert r.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Quota, qid) is None
    client.post("/surveys/1/editor/undo")
    with app.app_context():
        cell = db.session.get(QuotaCell, cid)
        assert cell is not None
        assert quotas._from_responses(db.session.get(Survey, 1))[0][cid]["done"] == 3
        assert cell.done == 3


# --- from tests/audits/test_audit_small2.py: Small fixes from the audit of 2026-10-01 (S4, S6, S7, S8, S10, S11, D-1, D-3, D-4). ---


def test_whole_numbers_are_written_in_full():
    assert export._cell(1234567.0, False) == "1234567" and export._cell(10000001.0, True) == "10000001"
    assert export._cell(1234567.5, True) == "1234567,5" and export._cell(-3.0, False) == "-3"
    assert export._cell(0.5, False) == "0.5"


# --- from tests/audits/test_cr8_a.py: Code review 2026-10-07, package A "Falsche oder verlorene Daten": regression tests for T1, E1, E2, E8, E9, E10, ---


def test_t1_quota_helpers_read_compound_conditions():
    from app.quotas import _cond_reaches

    none = {"item": "q:7", "op": "eq", "value": "none"}
    other = {"item": "p:src", "op": "eq", "value": "x"}
    test = lambda part: part.get("item") == "q:7" and part.get("value") == "none"  # noqa: E731
    assert _cond_reaches(none, test)
    assert _cond_reaches({"conds": [none, other], "rule": "any"}, test)
    assert not _cond_reaches({"conds": [none, other], "rule": "all"}, test)  # not everybody passes both
    assert _cond_reaches({"conds": [none, dict(none)], "rule": "all"}, test)


def test_e11_census_marks_on_flow_nodes(app):
    from app import census, transfer

    raw = {"t": "root", "children": [
        {"t": "branch", "id": "b1", "by": "census", "cond": {"item": 5, "op": "lt", "value": "18"},
         "children": [{"t": "end", "id": "e1", "kind": "screenout", "text": {}, "by": "census"}]},
        {"t": "quota", "id": "q1", "quotas": [3], "by": "census",
         "census": {"table": "12411-0013", "date": "2024-12-31", "n": 500, "cross": "age_gender", "lo": 18, "hi": None,
                    "scheme": "decades", "regions": "states", "state": "federal_state", "age_item": 5, "gender_item": 6,
                    "sexes": {"f": "2", "m": "1"}, "evil": "<x>"}},
        {"t": "end", "id": "e2", "kind": "screenout", "text": {}, "by": "someone"}]}
    with app.test_request_context("/"):
        tree, _ = transfer._flow(raw, {}, {5: 50, 6: 60}, {3: 30}, set(), [])
    branch, quota, end = tree["children"]
    assert branch["by"] == census.MARK and branch["children"][0]["by"] == census.MARK
    assert quota["by"] == census.MARK and quota["quotas"] == [30]
    assert quota["census"]["age_item"] == 50 and quota["census"]["gender_item"] == 60 and "evil" not in quota["census"]
    assert "by" not in end


# --- from tests/audits/test_cr8_c.py: Code review 2026-10-07, package C: counters and changes at the same moment (D3, D4, T2, K11, T7, R7, F8, F13, G7, ---


def test_settle_takes_the_cells_in_their_order(monkeypatch):
    from types import SimpleNamespace

    order = []
    monkeypatch.setattr(quotas, "_bump", lambda cell, *c, **k: order.append(cell) or True)
    r = SimpleNamespace(id=1, source="real", is_preview=False, quota={
        "7": {"res": "in", "cell": 9, "held": True}, "3": {"res": "in", "cell": 2, "held": True},
        "5": {"res": "in", "cell": 5, "held": True}})
    quotas.settle(r, completed=False)
    assert order == [2, 5, 9] and list(r.quota) == ["7", "3", "5"]                   # the stored order is kept


def test_expired_reservations_are_committed_one_by_one(app, client, survey):
    from tests.quotas.test_quota_field import idle
    from tests.quotas.test_quotas import build, counters, screen, start

    build(app, client, cells=[("Männer", "m", 5, [(2, "eq", "1")])])
    uid = start(app, client)
    screen(app, uid, "1")
    screen(app, uid, "1")
    idle(app, 120)
    commits = []
    with app.app_context():
        event.listen(db.session(), "after_commit", lambda s: commits.append(1))
        assert quotas.release_expired() == 2
        assert len(commits) >= 2                                                    # each participation on its own
    assert counters(app)["running"] == 0


# --- from tests/audits/test_review_medium.py: The medium findings of the code review of 2026-10-02 (code-review-2026-10-02.md, M5 to M16). ---


def test_page_sent_twice_takes_one_quota_place(app, client, survey):
    test_quotas.build(app, client)
    uid = test_quotas.start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)  # on the screening page; the next "Weiter" passes the calculation
    _overlap(app, lambda r: MultiDict({"nav": "next", "i2": "1", "i3": "30", "pos": runtime.page_token(r, r.position)}))
    assert test_quotas.counters(app)["running"] == 1
    test_quotas.finish(uid, p)
    assert test_quotas.counters(app) == {"running": 0, "done": 1, "refused": 0, "over": 0}
