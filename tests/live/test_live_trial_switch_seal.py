"""A run of a live vote switched between real and trial ("Probe") afterwards keeps the seal chain whole
(app/seal.py, _after_flush): the cases that leave the real data get a note "deleted" with the reason "live: Probe",
those that come in are sealed with "live: echt". Before, the check reported cases missing without a note, and votes
of a trial run became real data without an entry (business logic audit 2026-11-11)."""
from app import seal
from app.extensions import db
from app.models import Survey
from tests.live.test_live import add, make, send, steer
from tests.live.test_live_audit_f import _live


def _check(app, sid):
    with app.app_context():
        s = db.session.get(Survey, sid)
        row = seal.check_quick(s)
        db.session.commit()
        return row.chain_ok, row.data_ok, row.problems, [(e.kind, e.reason) for e in seal.entries(s.uid)]


def test_switching_a_run_between_real_and_trial_keeps_the_chain(app, client):
    code, sid, block = make(app, client)
    q = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    with app.app_context():
        seal.enable(db.session.get(Survey, sid))
        db.session.commit()
    steer(client, code, action="start")
    send(app.test_client(), code, {f"i{q}": "1"})
    send(app.test_client(), code, {f"i{q}": "2"})
    run = _live(app, sid)["run"]["id"]
    assert _check(app, sid)[:3] == (True, True, [])
    steer(client, code, action="run_test", run=run, on="1")                 # the real run becomes a trial
    ok, data_ok, problems, entries = _check(app, sid)
    assert (ok, data_ok, problems) == (True, True, []), problems
    assert [reason for kind, reason in entries if kind == "deleted"] == [seal.TRIAL, seal.TRIAL]
    steer(client, code, action="run_test", run=run)                          # and real again
    ok, data_ok, problems, entries = _check(app, sid)
    assert (ok, data_ok, problems) == (True, True, []), problems
    assert [reason for kind, reason in entries if kind == "sealed"][-2:] == [seal.REAL, seal.REAL]
    st = steer(client, code, action="new_run", name="Probe", test="1")      # a trial run with one vote turned real
    send(app.test_client(), code, {f"i{q}": "1"})
    steer(client, code, action="run_test", run=st["run"]["id"])
    ok, data_ok, problems, entries = _check(app, sid)
    assert (ok, data_ok, problems) == (True, True, []), problems
    assert entries[-1] == ("sealed", seal.REAL)
