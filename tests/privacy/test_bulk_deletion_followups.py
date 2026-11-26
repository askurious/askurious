"""Deleting a survey or all its answers at once (lifecycle.delete, retention.purge) reaches what the deletion of a
single participation reaches: transcriptions still under way for interview recordings are cancelled, the folders of
voice messages and comment screenshots go with the commit, and reports being written in the background stop."""
import os

from app import interview_audio, lifecycle, report_jobs, retention, whisper_jobs
from app.extensions import db
from app.models import Response, Survey

JOBS = {"1": {"id": "a" * 32, "service": "svc"}, "2": {"id": "b" * 32, "service": "svc"}}


def _with_running_transcription(app):
    with app.app_context():
        db.session.add(Response(survey_id=1, language="de", source="real", status="completed", answers={}, pid="p1",
                                interview={"transcript": {"jobs": JOBS}}))
        db.session.add(Response(survey_id=1, language="de", source="real", status="completed", answers={}, pid="p2"))
        db.session.commit()


def _cancelled(monkeypatch):
    seen = []
    monkeypatch.setattr(whisper_jobs, "cancel_records", lambda records, **kw: seen.append(records) or True)
    return seen


def test_deleting_the_survey_cancels_transcriptions_under_way(app, client, survey, monkeypatch):
    _with_running_transcription(app)
    seen = _cancelled(monkeypatch)
    with app.app_context():
        lifecycle.delete(db.session.get(Survey, 1), force=True)
        assert seen == []                                                   # only once the deletion stands
        db.session.commit()
    assert [sorted(r["id"] for r in records.values()) for records in seen] == [["a" * 32, "b" * 32]]


def test_the_end_of_the_answers_period_cancels_them_too(app, client, survey, monkeypatch):
    _with_running_transcription(app)
    seen = _cancelled(monkeypatch)
    with app.app_context():
        retention.purge(db.session.get(Survey, 1))
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=1)) == 0
    assert len(seen) == 1 and len(seen[0]) == 2


def test_a_deletion_that_is_rolled_back_cancels_nothing(app, client, survey, monkeypatch):
    _with_running_transcription(app)
    seen = _cancelled(monkeypatch)
    with app.app_context():
        interview_audio.cancel_jobs_where(Response.survey_id == 1)
        db.session.rollback()
        db.session.commit()
    assert seen == []


def test_the_folders_of_a_survey_name_voice_messages_and_comment_screenshots(app, client, survey):
    from app import comment_shots
    from app.items import voice

    with app.app_context():
        uid = db.session.get(Survey, 1).uid
        folders = lifecycle.survey_folders(uid)
        assert os.path.join(voice.root(), uid) in folders and os.path.join(comment_shots.root(), uid) in folders
        for folder in (os.path.join(voice.root(), uid, "p1"), os.path.join(comment_shots.root(), uid)):
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "x.bin"), "wb") as f:
                f.write(b"x")
        lifecycle.delete(db.session.get(Survey, 1), force=True)
        db.session.commit()                                                 # the folders go with the commit
        assert not os.path.exists(os.path.join(voice.root(), uid))
        assert not os.path.exists(os.path.join(comment_shots.root(), uid))


def test_removing_the_results_folder_stops_reports_in_the_background(app, client, survey, monkeypatch):
    stopped = []
    monkeypatch.setattr(report_jobs, "forget_uid", lambda uid: stopped.append(uid))
    with app.app_context():
        uid = db.session.get(Survey, 1).uid
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", uid)
        os.makedirs(folder, exist_ok=True)
        assert lifecycle.remove_folders([folder, os.path.join(app.config["UPLOAD_FOLDER"], uid)]) is True
    assert stopped == [uid]
