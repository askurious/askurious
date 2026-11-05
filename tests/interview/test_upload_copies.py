"""The same recording uploaded twice (tester 2026-11-05: an upload broke off, the file was uploaded again, two players
showed, and every transcription failed on the broken first copy): the upload goes on where it broke off or is
refused as a duplicate, a broken-off copy is left out of the transcription, and a part that fails no longer stops
the others."""
from app import interview_audio, interview_transcribe
from tests.interview.test_interview_online_upload import M4A, _audio
from tests.interview.test_interview_robust import _R, _tr, _whisper
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start

PIECE = 16
FILE = (M4A + b"x" * 48)[:48]  # three pieces


def _send(client, code, rid, part, seq, rec, size=len(FILE), data=None):
    size_arg = f"&size={size}" if size else ""
    body = FILE[seq * PIECE:(seq + 1) * PIECE] if data is None else data
    return client.post(f"/guide/{code}/interviews/{rid}/audio?source=upload&part={part}&seq={seq}&rec={rec}&start=0"
                       f"&origin=zoom&speaker=&all=1{size_arg}", data=body, content_type="audio/mp4")


def _fake_whisper(app, monkeypatch, calls):
    app.config.update(WHISPER_URL="http://whisper.intern:8000", WHISPER_RETRY_WAITS=())

    def fake_post(url, headers=None, timeout=None, files=None, data=None):
        calls.append(files["file"][0])
        return _R({"segments": [{"start": 0.0, "end": 4.0, "text": f" Text {files['file'][0][:6]}."}]})

    monkeypatch.setattr(interview_transcribe.requests, "post", fake_post)


def test_the_same_file_goes_on_where_its_upload_broke_off(app, client, monkeypatch):
    monkeypatch.setattr(interview_audio, "MAX_PIECE", PIECE)
    code, sid = make(app, client)
    rid = start(app, client, code)
    assert _send(client, code, rid, 1, 0, "a").status_code == 200
    assert _send(client, code, rid, 1, 1, "a").status_code == 200       # then the connection broke off
    again = _send(client, code, rid, 2, 0, "b")                         # the same file once more
    assert again.status_code == 409 and again.json["error"] == "resume"
    assert (again.json["resume_part"], again.json["seq"], again.json["rec"]) == (1, 2, "a")
    assert _send(client, code, rid, 1, 2, "a").status_code == 200
    one, = _audio(app, rid)["audio"]
    assert (one["bytes"], one["size"]) == (48, 48) and not interview_audio.skipped(one)
    twice = _send(client, code, rid, 2, 0, "c")                         # complete: refused as a duplicate
    assert twice.status_code == 409 and (twice.json["error"], twice.json["same_part"]) == ("duplicate", 1)
    assert len(_audio(app, rid)["audio"]) == 1
    other = M4A[:12] + b"y" * (PIECE - 12)                              # another file with the same type starts a part
    assert _send(client, code, rid, 2, 0, "d", data=other).status_code == 200


def test_a_broken_off_copy_is_left_out_of_the_transcription(app, client, monkeypatch):
    """Uploads from before the fix (no size): part 1 broke off after two pieces, part 2 is the whole file."""
    monkeypatch.setattr(interview_audio, "MAX_PIECE", PIECE)
    code, sid = make(app, client)
    rid = start(app, client, code)
    for seq in (0, 1):
        assert _send(client, code, rid, 1, seq, "a", size=None).status_code == 200
    for seq in (0, 1, 2):
        assert _send(client, code, rid, 2, seq, "b", size=None).status_code == 200
    calls = []
    _fake_whisper(app, monkeypatch, calls)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert calls == [_audio(app, rid)["audio"][1]["file"]]
    assert tr["state"] == "done" and "Text teil-2." in tr["text"] and "fehlt" not in tr["text"]
    one, two = _audio(app, rid)["audio"]
    assert one["copy_of"] == 2 and "copy_of" not in two
    page = client.get(f"/guide/{code}/interviews").get_data(as_text=True)
    assert "Teil 1 ist eine abgebrochene oder doppelte Kopie von Teil 2" in page
    with app.app_context():
        from app.extensions import db
        from app.models import Response
        info = interview_transcribe.progress_info(db.session.get(Response, rid))
    assert (info["done"], info["total"]) == (1, 1)


def test_a_failing_part_does_not_stop_the_others(app, client, monkeypatch):
    def answer(name, data):
        if name.startswith("teil-1"):
            return _R({}, status=413)
        return _R({"segments": [{"start": 0.0, "end": 4.0, "text": " Zweiter Teil."}]})

    code, sid, rid, calls = _whisper(app, client, monkeypatch, answer, parts=2)
    app.config.update(WHISPER_RETRY_WAITS=())
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert [c[0][:6] for c in calls] == ["teil-1", "teil-2"]
    assert tr["state"] == "error" and tr["partial"] and "Zweiter Teil." in tr["text"] and "[Teil 1 fehlt]" in tr["text"]
    assert "Teil 1" in tr["error"] and "413" in tr["error"]
    with app.app_context():
        from app.extensions import db
        from app.models import Response
        info = interview_transcribe.progress_info(db.session.get(Response, rid))
    assert info["text"].startswith("Transkription fehlgeschlagen:") and "Teil 1" in info["text"]


def test_a_downloaded_part_uploaded_again_is_a_copy(app, client, monkeypatch):
    """S-04: the browser's recording was downloaded (with the head that names its length) and uploaded again."""
    import os

    from tests.interview.test_interview_data import WEBM
    from tests.interview.test_interview_robust import _send as record

    code, sid = make(app, client)
    rid = start(app, client, code)
    body = WEBM + os.urandom(20000)
    assert record(client, code, rid, 1, 0, body, rec="r1").status_code == 200
    copy = M4A[:0] + WEBM + b"\x00" * 18 + body[4:]  # a head 18 bytes longer, as the download sets it
    r = client.post(f"/guide/{code}/interviews/{rid}/audio?source=upload&part=2&seq=0&rec=u2&start=0&origin=other"
                    f"&speaker=&all=1&size={len(copy)}", data=copy, content_type="audio/webm")
    assert r.status_code == 200
    one, two = _audio(app, rid)["audio"]
    assert two["copy_of"] == 1 and "copy_of" not in one and interview_audio.skipped(two) == "copy"
    other = WEBM + os.urandom(20000)                  # another recording of nearly the same length is no copy
    r = client.post(f"/guide/{code}/interviews/{rid}/audio?source=upload&part=3&seq=0&rec=u3&start=0&origin=other"
                    f"&speaker=&all=1&size={len(other)}", data=other, content_type="audio/webm")
    assert r.status_code == 200 and "copy_of" not in _audio(app, rid)["audio"][2]


def test_a_job_sent_for_a_copy_is_cancelled(app, client, monkeypatch):
    """Deploy 2026-11-05 (S-04): part 2 had been sent to the job service just before the new code found it to be a
    copy; the run then skipped it, but its job waited in the queue and held up R-07."""
    monkeypatch.setattr(interview_audio, "MAX_PIECE", PIECE)
    code, sid = make(app, client)
    rid = start(app, client, code)
    for seq in (0, 1):
        assert _send(client, code, rid, 1, seq, "a", size=None).status_code == 200
    for seq in (0, 1, 2):
        assert _send(client, code, rid, 2, seq, "b", size=None).status_code == 200
    calls, cancelled = [], []
    _fake_whisper(app, monkeypatch, calls)
    from app import whisper_jobs
    monkeypatch.setattr(whisper_jobs, "cancel_records", lambda records, *a, **k: cancelled.append(dict(records)) or True)
    record = {"id": "b" * 32, "service": "x", "bytes": 32}
    with app.app_context():
        from app.extensions import db
        from app.models import Response
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, jobs={"1": record, "2": {**record, "id": "c" * 32}})
        db.session.commit()
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    assert cancelled == [{"1": record}]                          # part 1 is the copy of the longer part 2
    assert "1" not in _tr(app, rid).get("jobs", {})
