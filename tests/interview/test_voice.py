"""Voice message (experimental "voice", app/items/voice.py): only with the tick, recording sent with the page,
export and ZIP, microphone allowed only where it records, files deleted with the participation."""
import io
import os
import time
import zipfile

from app.extensions import db
from app.models import Response, Survey, User
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions

E = "/surveys/1/editor"
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 200  # the EBML header a WebM recording starts with
MP4 = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 100


def switch_on(app, keys=("voice",)):
    with app.app_context():
        for u in db.session.scalars(db.select(User)):
            u.experiments = list(keys)
        db.session.commit()


def _voice(app, client, **settings):
    switch_on(app)
    fill_consent(client)
    add(client, "voice")                                                       # item 2
    client.post(E + "/items/2", data={"code": "v", "question": "Erzählen Sie", "notice": "Wird gespeichert.",
                                      "answer_mode": "optional", "max_seconds": "60", "text_fallback": "1", **settings})


def _start(app, client):
    fill_questions(app)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert survey.status == "running"
        return f"/s/{survey.code}", survey.uid


def _participant(app, url):
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    return p


def _send(p, url, data=WEBM, **fields):
    form = {"nav": "next", "i2_seconds": "7", **fields}
    if data is not None:
        form["i2_audio"] = (io.BytesIO(data), "aufnahme.webm", "audio/webm")
    return p.post(url, data=form, content_type="multipart/form-data")


def _row(app):
    with app.app_context():
        r = db.session.scalars(db.select(Response).filter_by(is_preview=False).order_by(Response.id.desc())).first()
        return r.id, r.pid, r.answers


def _folder(app, uid, pid):
    return os.path.join(os.path.dirname(app.config["UPLOAD_FOLDER"]), "voice", uid, pid)


def test_for_everyone_without_a_tick(app, client, survey):
    """Experimental until 2026-10-16, since then for everyone (Ralf)."""
    assert "Sprachnachricht" in client.get(E + "/").data.decode()
    assert add(client, "voice").status_code == 302
    assert client.get(E + "/items/2").status_code == 200
    with app.app_context():
        from app.models import Item

        assert db.session.get(Item, 2).config == {"max_seconds": 60, "text_fallback": True}  # one minute by default
    switch_on(app, keys=())
    assert client.get(E + "/items/2").status_code == 200
    assert add(client, "voice").status_code == 302                           # no tick needed for another one


def test_settings_are_bounded(app, client, survey):
    _voice(app, client, max_seconds="9999", text_fallback="")
    with app.app_context():
        from app.models import Item

        item = db.session.get(Item, 2)
        assert item.config == {"max_seconds": 300, "text_fallback": False}
        assert item.texts["de"]["notice"] == "Wird gespeichert."


def test_recording_is_stored_exported_and_zipped(app, client, survey):
    _voice(app, client)
    url, uid = _start(app, client)
    p = _participant(app, url)
    page = p.get(url)
    html = page.data.decode()
    assert "data-voice" in html and 'name="i2_audio"' in html and "Wird gespeichert." in html
    assert "microphone=(self)" in page.headers["Permissions-Policy"]
    assert "media-src 'self' blob:" in page.headers["Content-Security-Policy"]
    assert _send(p, url, i2_text="und getippt").status_code == 302
    rid, pid, answers = _row(app)
    name = answers["2"]["audio"]["file"]
    assert name.startswith("2-") and name.endswith(".webm")
    assert answers["2"] == {"audio": {"file": name, "mime": "audio/webm", "bytes": len(WEBM), "seconds": 7},
                            "text": "und getippt"}
    with open(os.path.join(_folder(app, uid, pid), name), "rb") as f:
        assert f.read() == WEBM
    csv = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig")
    assert "v_audio" in csv and f"{pid}_v.webm" in csv and "und getippt" in csv
    z = client.get("/surveys/1/export/voice-messages.zip")
    assert z.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(z.data)).read(f"{pid}_v.webm") == WEBM


def test_other_pages_keep_the_microphone_off(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    url, _ = _start(app, client)
    assert "microphone=()" in app.test_client().get(url).headers["Permissions-Policy"]


def test_text_only_and_required(app, client, survey):
    _voice(app, client, answer_mode="required")
    url, _ = _start(app, client)
    p = _participant(app, url)
    r = _send(p, url, data=None)
    assert r.status_code == 200 and "Bitte sprechen Sie eine Antwort ein oder schreiben Sie sie auf" in r.data.decode()
    assert _send(p, url, data=None, i2_text="nur Text").status_code == 302
    assert _row(app)[2]["2"] == {"text": "nur Text"}


def test_no_audio_is_refused(app, client, survey):
    _voice(app, client)
    url, uid = _start(app, client)
    p = _participant(app, url)
    r = _send(p, url, data=b"<html>nicht hoeren</html>")
    assert r.status_code == 200 and "konnte nicht angenommen werden" in r.data.decode()
    assert _send(p, url, data=b"\x1a\x45\xdf\xa3" + b"\x00" * (8 * 1024 * 1024)).status_code in (200, 413)  # too long
    _, pid, _ = _row(app)
    assert not os.path.exists(os.path.join(_folder(app, uid, pid), "2.webm"))


def test_kept_replaced_and_deleted_on_the_page(app, client, survey):
    _voice(app, client)
    add(client, "page_break")
    add(client, "text_input")
    url, uid = _start(app, client)
    p = _participant(app, url)
    assert _send(p, url).status_code == 302
    _, pid, _ = _row(app)
    folder = _folder(app, uid, pid)
    p.post(url, data={"nav": "back"})
    html = p.get(url).data.decode()
    assert "Aufnahme gespeichert" in html and 'name="i2_keep" value="1"' in html
    assert _send(p, url, data=None, i2_keep="1").status_code == 302             # kept as it was
    original = _row(app)[2]["2"]["audio"]["file"]
    assert original.endswith(".webm")
    p.post(url, data={"nav": "back"})
    assert _send(p, url, data=MP4).status_code == 302                           # recorded anew, now as MP4
    assert sorted(os.listdir(folder)) == [_row(app)[2]["2"]["audio"]["file"]]
    assert _row(app)[2]["2"]["audio"]["mime"] == "audio/mp4"
    p.post(url, data={"nav": "back"})
    assert _send(p, url, data=None).status_code == 302                          # deleted on the page
    assert "2" not in _row(app)[2] or _row(app)[2]["2"] is None
    assert os.listdir(folder) == []


def test_files_go_with_the_participation(app, client, survey):
    _voice(app, client)
    url, uid = _start(app, client)
    p = _participant(app, url)
    _send(p, url)
    rid, pid, _ = _row(app)
    folder = _folder(app, uid, pid)
    assert os.path.isdir(folder)
    with app.app_context():
        db.session.delete(db.session.get(Response, rid))
        db.session.rollback()                                                   # not committed: stays
    assert os.path.isdir(folder)
    with app.app_context():
        db.session.delete(db.session.get(Response, rid))
        db.session.commit()
    assert not os.path.exists(folder)


def test_retention_and_the_sweep(app, client, survey):
    _voice(app, client)
    url, uid = _start(app, client)
    p = _participant(app, url)
    _send(p, url)
    _, pid, _ = _row(app)
    base = os.path.join(os.path.dirname(app.config["UPLOAD_FOLDER"]), "voice")
    stray = os.path.join(base, uid, "Q-never-stored")
    os.makedirs(stray)
    gone = os.path.join(base, "no-such-survey", "x")
    os.makedirs(gone)
    with app.app_context():
        from app.items import voice

        assert voice.run() == 1                                                   # the folder of a survey that is gone
        assert os.path.isdir(stray)                                               # a first page may still be sent
        old = time.time() - (voice.STALE_HOURS + 1) * 3600
        os.utime(stray, (old, old))
        assert voice.run() == 1 and not os.path.exists(stray)
        assert os.path.isdir(_folder(app, uid, pid))                              # held by its participation
        from app import retention

        retention.purge(db.session.get(Survey, 1))
        db.session.commit()
        retention.commit_files()
    assert not os.path.exists(os.path.join(base, uid))


def test_viewers_get_no_zip(app, client, survey):
    from app import surveys

    assert "surveys.export_voice" in surveys.DATA_GET_ENDPOINTS


def test_a_page_with_a_recording_may_be_larger(app, client, survey):
    _voice(app, client, max_seconds="300")  # five minutes may take some 5 MB (voice.limit)
    url, _ = _start(app, client)
    p = _participant(app, url)
    big = b"\x1a\x45\xdf\xa3" + b"\x00" * (3 * 1024 * 1024)                       # over PARTICIPANT_FORM_BYTES (2 MB)
    assert _send(p, url, data=big).status_code == 302
    assert _row(app)[2]["2"]["audio"]["bytes"] == len(big)
    assert p.post(url, data={"x": "y" * (3 * 1024 * 1024)}).status_code == 413     # a plain form stays small
