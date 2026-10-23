"""What comes into an interview from files (security and code audit 2026-10-13): the recording is checked by its
first bytes, not by what the browser declares (DA-6), and an uploaded transcript loses control characters like the
values of forms (DA-8)."""
from app.extensions import db
from app.interview_transcribe import read_upload
from app.models import Response
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start

WEBM = b"\x1a\x45\xdf\xa3"


def test_a_recording_starts_with_the_container_it_names(app, client):
    code, _sid = make(app, client)
    rid = start(app, client, code)
    url = f"/guide/{code}/interviews/{rid}/audio"
    send = lambda part, seq, data, mime="audio/webm": client.post(f"{url}?part={part}&seq={seq}&start=0", data=data,  # noqa: E731
                                                                  content_type=mime)
    assert send(1, 0, b"<html><script>x</script>").status_code == 415      # no audio at all
    assert send(1, 0, b"OggS" + b"x" * 20).status_code == 415               # Ogg declared as WebM
    assert send(1, 0, WEBM + b"AAAA").json["next"] == 1
    assert send(1, 1, b"BBBB").json["next"] == 2                            # further pieces continue the stream
    assert send(2, 0, b"OggS" + b"x" * 20, "audio/ogg").json["next"] == 1
    assert send(3, 0, b"\0\0\0\x18ftypM4A ", "audio/mp4").json["next"] == 1


def test_an_uploaded_transcript_has_no_control_characters(app, client):
    assert read_upload("t.txt", b"I: Hallo\x00 Welt\x01\x0b\nB: Ja\tgut") == "I: Hallo Welt\nB: Ja\tgut"
    srt = b"1\n00:00:01,000 --> 00:00:02,000\nI: Eins\x07\n"
    assert "\x07" not in read_upload("t.srt", srt)
    code, _sid = make(app, client)
    rid = start(app, client, code)
    import io

    client.post(f"/guide/{code}/interviews/{rid}/transcript/upload", data={"file": (io.BytesIO(b"A\x00B\x1fC"), "t.txt")},
                content_type="multipart/form-data")
    with app.app_context():
        assert db.session.get(Response, rid).interview["transcript"]["text"] == "ABC"
