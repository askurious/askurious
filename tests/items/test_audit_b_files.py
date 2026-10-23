"""Audit 2026-10-05, package B: what a deletion leaves on the disk.

L-1   a deletion on request and both retention periods remove every file of kept results (results.bin,
      results-weighted.bin, compare.bin), not only the first
L-8   file cleanups that failed after the commit are noted and done by the next housekeeping run; once the period
      "answers" is over and no participation is left, data files of saved states still lying there go
I-16  "Durchgang löschen" and "Alles zurück" of a live vote remove kept results and states of the dashboards too
"""
import os
from datetime import timedelta

from app import cleanup, erasure, results_store, retention
from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.privacy.test_erasure import CODE, build, take

KEPT = ("results.bin", "results-weighted.bin", "compare.bin")


def _kept_files(app, uid):
    """The three files of kept results and a dashboard state, as the results page and the dashboards write them."""
    hb = os.path.join(app.config["UPLOAD_FOLDER"], "hb", uid)
    os.makedirs(hb, exist_ok=True)
    for name in KEPT + ("board-1.json",):
        with open(os.path.join(hb, name), "wb") as f:
            f.write(b"Geheimwort")
    return hb


def _snapshot(app, uid, pid="x-1"):
    folder = os.path.join(app.config["UPLOAD_FOLDER"], "snapshots", uid, "v1_20300101-000000")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "data.csv"), "w", encoding="utf-8") as f:
        f.write(f"resp_id,pid,why\n1,{pid},Geheimwort\n")
    with open(os.path.join(folder, "long_x.csv"), "w", encoding="utf-8") as f:
        f.write("resp_id,a\n1,Geheimwort\n")
    return folder


def test_l1_kept_results_are_real_files_of_the_store(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        names = {os.path.basename(results_store._path(s, None, n)) for n in ("results", "compare")}
        names.add(os.path.basename(results_store._path(s, {"weighted": True})))
        assert names == set(KEPT)


def test_l1_erasure_by_code_removes_all_kept_results(app, client, survey):
    uid = build(app, client)
    code = CODE.search(take(app.test_client(), uid, "Geheimwort")).group(1)
    with app.app_context():
        s = db.session.get(Survey, 1)
        hb = _kept_files(app, s.uid)
    assert "unwiederbringlich gelöscht" in app.test_client().post(f"/s/{uid}/delete", data={"sure": "1", "code": code}).data.decode()
    assert os.listdir(hb) == []


def test_l1_erasure_on_request_removes_all_kept_results(app, client, survey):
    uid = build(app, client)
    take(app.test_client(), uid, "Geheimwort")
    with app.app_context():
        s = db.session.get(Survey, 1)
        hb = _kept_files(app, s.uid)
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=1)).all()
        erasure.erase(s, rows, "request")
    assert os.listdir(hb) == []


def _closed_with_periods(app, client, days):
    uid = build(app, client)
    take(app.test_client(), uid, "Geheimwort")
    client.post("/surveys/1/close")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.retention = {"personal": 1, "answers": 12}
        s.closed_at = utcnow() - timedelta(days=days)
        db.session.commit()
        return s.uid


def test_l1_both_periods_remove_all_kept_results(app, client, survey):
    uid = _closed_with_periods(app, client, 40)  # "personal" is over
    with app.app_context():
        hb = _kept_files(app, uid)
        db.session.scalar(db.select(Response)).params = {"pid": "panel-1"}
        db.session.commit()
        assert retention.run() == 1
        assert os.listdir(hb) == []
        hb = _kept_files(app, uid)
        db.session.get(Survey, 1).closed_at = utcnow() - timedelta(days=800)  # "answers" is over
        db.session.commit()
        assert retention.run() == 1
        assert os.listdir(hb) == []


def test_l8_a_failed_cleanup_after_erasure_is_done_by_the_next_run(app, client, survey, monkeypatch):
    uid = build(app, client)
    code = CODE.search(take(app.test_client(), uid, "Geheimwort")).group(1)
    take(app.test_client(), uid, "bleibt")
    with app.app_context():
        s = db.session.get(Survey, 1)
        pid = db.session.scalar(db.select(Response.pid).filter(Response.answers.is_not(None)).order_by(Response.id))
        hb, folder = _kept_files(app, s.uid), _snapshot(app, s.uid, pid)
        survey_uid = s.uid

    def broken(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(erasure, "_forget_in_files", broken)
    app.test_client().post(f"/s/{uid}/delete", data={"sure": "1", "code": code})
    monkeypatch.undo()
    with app.app_context():
        assert cleanup.read(survey_uid) == {"pids": [pid]}  # noted, the files are still there
        assert "Geheimwort" in open(os.path.join(folder, "data.csv"), encoding="utf-8").read()
        retention.run()
        assert cleanup.read(survey_uid) == {} and cleanup.owed() == []
    assert os.listdir(hb) == []
    assert "Geheimwort" not in open(os.path.join(folder, "data.csv"), encoding="utf-8").read()


def test_l8_a_note_of_a_deletion_that_never_came_about_changes_nothing(app, client, survey):
    uid = build(app, client)
    take(app.test_client(), uid, "Geheimwort")
    with app.app_context():
        s = db.session.get(Survey, 1)
        pid = db.session.scalar(db.select(Response.pid))
        folder = _snapshot(app, s.uid, pid)
        cleanup.note(s.uid, pids=[pid], period="answers")  # as if the commit after the note had failed
        retention.run()
        assert cleanup.owed() == []
        assert "Geheimwort" in open(os.path.join(folder, "data.csv"), encoding="utf-8").read()


def test_l8_a_failed_cleanup_after_a_period_is_done_by_the_next_run(app, client, survey, monkeypatch):
    survey_uid = _closed_with_periods(app, client, 800)
    with app.app_context():
        folder = _snapshot(app, survey_uid)
        real = retention._clean_files

        def broken(*a, **k):
            raise OSError("locked")

        monkeypatch.setattr(retention, "_clean_files", broken)
        assert retention.run() == 1
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
        assert cleanup.read(survey_uid) == {"periods": ["answers"]} and os.path.exists(os.path.join(folder, "data.csv"))
        monkeypatch.setattr(retention, "_clean_files", real)
        retention.run()
        assert cleanup.owed() == [] and sorted(os.listdir(folder)) == []


def test_l8_saved_states_left_after_the_period_answers_go(app, client, survey):
    """No note (an older server, or the note itself was lost), no participation, but the data files still there."""
    survey_uid = _closed_with_periods(app, client, 800)
    with app.app_context():
        assert retention.run() == 1
        folder = _snapshot(app, survey_uid)
        hb = _kept_files(app, survey_uid)
        assert retention._files_left(survey_uid)
        assert retention.run() == 0  # nothing in the database went, but the files did
        assert os.listdir(folder) == [] and os.listdir(hb) == []
        assert not retention._files_left(survey_uid)


def test_i16_deleting_a_live_run_removes_kept_results(app, client):
    from tests.live.test_live import add, make, send, steer

    code, sid, block = make(app, client, "Aufräumen")
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    steer(client, code, action="start")
    send(app.test_client(), code, {f"i{q}": "4"})
    old = steer(client, code, action="new_run", name="Runde 2")["runs"][0]["id"]
    with app.app_context():
        hb = _kept_files(app, db.session.get(Survey, sid).uid)
    client.post(f"/presenter/{code}/control", data={"action": "run_delete", "run": old})
    assert os.listdir(hb) == []
    send(app.test_client(), code, {f"i{q}": "2"})
    _kept_files(app, os.path.basename(hb))
    steer(client, code, action="restart")
    assert os.listdir(hb) == []
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 0
