"""Recordings made elsewhere (Zoom, Teams, Webex, BBB) added to an interview: concept
konzept-online-interviews-2026-10-15.md, decisions of Ralf 2026-10-15 (1c, 2a optional, 3a, 4a)."""
from app.extensions import db
from app.models import Response
from tests.interview.test_interview import make
from tests.interview.test_interview_data import WEBM, start
from tests.interview.test_interview_robust import _R

M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A mp42isom"
MP4_VIDEO = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 40 + b"hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide"
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb" + b"\x00" * 20
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 20


def _up(client, code, rid, data, mime, part=1, seq=0, speaker="", all_="1", origin="zoom"):
    return client.post(f"/guide/{code}/interviews/{rid}/audio?source=upload&part={part}&seq={seq}&rec=u{part}&start=0"
                       f"&origin={origin}&speaker={speaker}&all={all_}", data=data, content_type=mime)


def _audio(app, rid):
    with app.app_context():
        return db.session.get(Response, rid).interview


def test_upload_page_and_link_need_the_recording_consent(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    page = client.get(f"/guide/{code}/interviews/{rid}/audio/upload")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "audio-upload.js" in text and 'name="all"' in text and "Zoom, lokale Aufzeichnung" in text
    assert "nur befragte Person (B)" in text
    assert "Aufnahme hochladen" in client.get(f"/guide/{code}/interviews").get_data(as_text=True)
    no = start(app, client, code, audio="")
    assert client.get(f"/guide/{code}/interviews/{no}/audio/upload").status_code == 403


def test_uploaded_parts_keep_origin_speaker_and_consent_of_all(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    assert _up(client, code, rid, M4A, "audio/mp4", all_="").status_code == 403   # § 201 StGB: everybody who speaks
    assert _up(client, code, rid, M4A, "audio/mp4", speaker="I").status_code == 200
    assert _up(client, code, rid, M4A + b"more", "audio/mp4", seq=1, speaker="I", all_="").status_code == 200
    assert _up(client, code, rid, MP3, "audio/mpeg", part=2, speaker="B", origin="teams").status_code == 200
    assert _up(client, code, rid, WAV, "audio/wav", part=3, speaker="X", origin="nowhere").status_code == 200
    m = _audio(app, rid)
    assert m["consent"]["all"] and m["consent"]["upload"]
    one, two, three = m["audio"]
    assert (one["source"], one["origin"], one["speaker"], one["pieces"]) == ("upload", "zoom", "I", 2)
    assert two["file"].endswith(".mp3") and (two["origin"], two["speaker"]) == ("teams", "B")
    assert three["file"].endswith(".wav") and (three["origin"], three["speaker"]) == ("other", "")
    assert client.get(f"/guide/{code}/interviews/{rid}/audio/2").status_code == 200
    listing = client.get(f"/guide/{code}/interviews").get_data(as_text=True)
    assert "Microsoft Teams · nur befragte Person (B)" in listing


def test_a_file_with_a_picture_is_refused(app, client):
    """3a: only sound; the browser checks first, the server again on the first piece."""
    code, sid = make(app, client)
    rid = start(app, client, code)
    r = _up(client, code, rid, MP4_VIDEO, "audio/mp4")
    assert r.status_code == 415 and r.json["error"] == "video"
    r = _up(client, code, rid, WEBM + b"\x86\x85V_VP9", "audio/webm")
    assert r.status_code == 415
    assert not _audio(app, rid).get("audio")
    assert _up(client, code, rid, M4A, "audio/webm").status_code == 415           # the container must be the named one


def test_one_tick_for_the_consent_of_everyone_who_speaks(app, client):
    """4a, Ralf 2026-10-15: one tick "Einwilligung aller Sprechenden liegt vor" instead of two; it is required for
    every interview and stands for everybody who speaks (§ 201 StGB), so the recording needs no second tick."""
    code, sid = make(app, client)
    page = client.get(f"/guide/{code}/interviews").get_data(as_text=True)
    assert "Einwilligung aller Sprechenden liegt vor" in page and "Alle Sprechenden haben eingewilligt" not in page
    assert '<option value="gemischt"' in client.get(f"/guide/{code}/settings").get_data(as_text=True)
    for mode in ("video", "gemischt", "praesenz"):
        assert start(app, client, code, mode=mode, consent="") is None              # without the tick: no interview
        rid = start(app, client, code, mode=mode)
        assert rid and _audio(app, rid)["consent"]["all"] and _audio(app, rid)["mode"] == mode


def test_one_voice_parts_are_labelled_and_interleaved_by_time(app, client, monkeypatch):
    """2a: Zoom's file per person gets its speaker; their paragraphs stand in the order they were said. A part with
    several voices keeps its own block."""
    from app import interview_transcribe

    code, sid = make(app, client)
    rid = start(app, client, code)
    _up(client, code, rid, M4A, "audio/mp4", part=1, speaker="I")
    _up(client, code, rid, MP3, "audio/mpeg", part=2, speaker="B")
    _up(client, code, rid, WAV, "audio/wav", part=3)
    app.config.update(WHISPER_URL="http://whisper.intern:8000")
    said = {"teil-1": [(0, 3, " Wie geht es Ihnen?"), (10, 12, " Und dann?")],
            "teil-2": [(4, 8, " Gut, danke."), (13, 18, " Dann ging ich.")],
            "teil-3": [(0, 2, " Nachtrag.")]}

    def fake_post(url, headers=None, timeout=None, files=None, data=None):
        segments = said[files["file"][0].split("-u")[0]]
        return _R({"segments": [{"start": s, "end": e, "text": t} for s, e, t in segments]})

    monkeypatch.setattr(interview_transcribe.requests, "post", fake_post)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    text = _audio(app, rid)["transcript"]["text"]
    assert text == ("I: Wie geht es Ihnen? #00:00:03-0#\n\nB: Gut, danke. #00:00:08-0#\n\nI: Und dann? #00:00:12-0#\n\n"
                    "B: Dann ging ich. #00:00:18-0#\n\nNachtrag. #00:00:02-0#")
