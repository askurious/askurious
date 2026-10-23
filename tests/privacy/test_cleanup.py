"""The notes of file cleanups still owed after a deletion (app/cleanup.py)."""
import threading
import time


def test_two_deletions_at_once_keep_both_in_the_note(app, monkeypatch):
    """Code review 2026-10-15, D4: note() read the note and wrote it back without a lock; two deletions at once kept
    only one's IDs, and a cleanup of the other that failed was never tried again."""
    from app import cleanup

    read = cleanup.read

    def slow(uid):
        found = read(uid)
        time.sleep(0.3)  # both have read before either writes, without the lock
        return found

    monkeypatch.setattr(cleanup, "read", slow)

    def deletion(pid):
        with app.app_context():
            cleanup.note("u1", pids=[pid])

    workers = [threading.Thread(target=deletion, args=(pid,)) for pid in ("a", "b")]
    for w in workers:
        w.start()
    for w in workers:
        w.join(30)
    with app.app_context():
        assert read("u1") == {"pids": ["a", "b"]}
        cleanup.done("u1", pids=["a"])
        assert read("u1") == {"pids": ["b"]} and cleanup.owed() == ["u1"]


def _deleted_with_files(app, client, monkeypatch):
    """A closed survey with real answers, its saved data file and HB result, deleted with all data while the
    folders cannot be removed (the process ending right after the commit, or a file that resists)."""
    import os
    import shutil

    from app import lifecycle
    from app.extensions import db
    from app.models import Survey
    from tests.conftest import CONSENT_OK, add, fill_consent

    client.post("/surveys/new", data={"title": "Mit Daten", "languages": ["de"], "default_language": "de"})
    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "why", "question": "Warum?"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        key = db.session.get(Survey, 1).public_key
    p = app.test_client()
    p.get(f"/s/{key}")
    p.post(f"/s/{key}", data=CONSENT_OK)
    p.post(f"/s/{key}", data={"nav": "next", "i2": "Antwort"})
    client.post("/surveys/1/close")
    with app.app_context():
        s = db.session.get(Survey, 1)
        uid, base = s.uid, app.config["UPLOAD_FOLDER"]
        files = [os.path.join(base, "snapshots", uid, "v1_x", "data.csv"), os.path.join(base, "hb", uid, "item_2.json")]
        for path in files:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("pid,why\nabc,Antwort\n")
        monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: None)
        lifecycle.delete(s, force=True)
        db.session.commit()
    monkeypatch.undo()
    return uid, files


def test_files_of_a_deleted_survey_go_in_the_next_housekeeping(app, client, monkeypatch):
    """Gesamtaudit 2026-10-18, FUNC-04: lifecycle.delete removed the folders only after the commit, ignoring errors,
    and wrote no note; cleanup.retry took a note of a survey that was gone as done. A process ending right after the
    commit left the saved data files and HB results of a deleted survey on the disk for good. Now a note "deleted" is
    written before the commit, and the next housekeeping removes the folders."""
    import os

    from app import cleanup, lifecycle

    uid, files = _deleted_with_files(app, client, monkeypatch)
    with app.app_context():
        assert all(os.path.exists(p) for p in files)
        assert cleanup.DELETED in cleanup.read(uid).get("periods", [])
    with app.test_request_context():
        lifecycle._last_rare = None
        lifecycle.close_due_surveys()  # every housekeeping step, retention.run -> cleanup.retry included
        assert [p for p in files if os.path.exists(p)] == []
        assert cleanup.read(uid) == {}


def test_a_deletion_without_trouble_leaves_no_note(app, client, survey):
    """FUNC-04: the note of a deletion goes as soon as its folders are gone after the commit."""
    import os

    from app import cleanup, lifecycle
    from app.extensions import db
    from app.models import Survey

    with app.app_context():
        s = db.session.get(Survey, 1)
        uid = s.uid
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", uid)
        os.makedirs(folder, exist_ok=True)
        lifecycle.delete(s)
        db.session.commit()
        assert not os.path.exists(folder) and cleanup.read(uid) == {} and uid not in cleanup.owed()


def test_a_deletion_rolled_back_keeps_survey_and_files(app, client, survey):
    """FUNC-04: a deletion whose commit never came keeps the survey with its folders; its note goes once it is
    clearly stale (cleanup.DELETION_SETTLED), never with the folders of a survey still there."""
    import os

    from app import cleanup, lifecycle
    from app.extensions import db
    from app.models import Survey, utcnow

    with app.app_context():
        s = db.session.get(Survey, 1)
        uid = s.uid
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", uid)
        os.makedirs(folder, exist_ok=True)
        lifecycle.delete(s)
        db.session.rollback()
        assert db.session.get(Survey, 1) is not None and os.path.isdir(folder)
        assert cleanup.DELETED in cleanup.read(uid).get("periods", [])
        cleanup.retry(utcnow())
        assert os.path.isdir(folder) and cleanup.DELETED in cleanup.read(uid).get("periods", [])  # too fresh
        old = time.time() - cleanup.DELETION_SETTLED - 60
        os.utime(cleanup._path(uid), (old, old))
        cleanup.retry(utcnow())
        assert os.path.isdir(folder) and cleanup.read(uid) == {}
