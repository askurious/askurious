"""M-04 (tester 2026-11-09): Chrome's MediaRecorder wrote the first Cluster at 0 and the next ones from 10:43 h on. The
recording held 60 minutes of sound, but the player showed 11:43:21, and the transcription service, which cuts a
recording after 3 hours by its timestamps, transcribed only the first block and reported "done" with no text.
app/webm_duration.py closes such jumps when the file is served and before it is sent; a long part without any text is
reported as an error."""
import struct

from app import audio_length, interview_transcribe, webm_duration
from tests.interview.test_interview_audio_duration import UNKNOWN, el, recorder_webm, write
from tests.interview.test_interview_robust import _R, _send, _tr, _whisper

JUMP = 38595119  # ms: where the second Cluster of M-04 stood


def _clusters(n, at=JUMP):
    """One Cluster at 0 (one block), then n Clusters of 5 s from `at` on."""
    return [(0, [0])] + [(at + i * 5000, list(range(0, 5000, 1000))) for i in range(n)]


def _served(full):
    head, offset, _seconds, patches = webm_duration.head_plan(full)
    joined = webm_duration.Joined(head, full, offset, patches)
    try:
        return joined.read()
    finally:
        joined.close()


def test_a_jump_is_closed_for_the_length_and_the_served_file(tmp_path):
    data, _, info_end = recorder_webm(_clusters(3))
    full = write(tmp_path, data)
    head, offset, seconds, patches = webm_duration.head_plan(full)
    # 0 → one block; then 3 Clusters of 5 s right after it (20 ms later): 0.02 + 14 s + the last block's 0
    assert seconds == 0.02 + 10 + 4 and len(patches) == 3
    assert audio_length.seconds(full, "audio/webm") == seconds
    served = _served(full)
    assert len(served) == len(head) + len(data) - info_end
    assert [c[4] for c in webm_duration._clusters(served, len(head))] == [0, 20, 5020, 10020]  # they follow each other
    assert webm_duration.has_jumps(full) and not webm_duration.has_jumps(write(tmp_path, served, "s.webm"))


def test_ranges_of_the_served_file_carry_the_closed_timestamps(tmp_path):
    data, _, _ = recorder_webm(_clusters(4))
    full = write(tmp_path, data)
    whole = _served(full)
    head, offset, _s, patches = webm_duration.head_plan(full)
    for start in range(0, len(whole), 7):  # every cut through a patched Timecode
        joined = webm_duration.Joined(head, full, offset, patches)
        try:
            joined.seek(start)
            got = b""
            while len(got) < 11:  # a read may stop at the end of the head, as RawIOBase allows
                more = joined.read(11 - len(got))
                if not more:
                    break
                got += more
            assert got == whole[start:start + 11]
        finally:
            joined.close()


def test_small_gaps_and_files_without_jumps_stay(tmp_path):
    data, _, _ = recorder_webm([(0, [0, 980]), (1000, [0, 980]), (20000, [0, 980])])  # 19 s: a pause, no jump
    head, offset, seconds, patches = webm_duration.head_plan(write(tmp_path, data))
    assert seconds == 20.98 and patches == ()


def test_a_cluster_id_inside_the_sound_is_not_taken_for_a_cluster(tmp_path):
    fake = b"\x1f\x43\xb6\x75\x84" + b"\xe7\x84" + (JUMP * 9).to_bytes(4, "big")  # known size that ends nowhere
    sound = el(b"\xa3", b"\x81" + struct.pack(">h", 0) + b"\x80" + fake)
    head = el(b"\x1a\x45\xdf\xa3", el(b"\x42\x82", b"webm"))
    info = el(b"\x15\x49\xa9\x66", el(b"\x2a\xd7\xb1", (1000000).to_bytes(3, "big")))
    body = b"".join(el(b"\x1f\x43\xb6\x75", el(b"\xe7", tc.to_bytes(4, "big")) + sound, unknown=True) for tc in (0, 4000))
    data = head + b"\x18\x53\x80\x67" + UNKNOWN + info + body
    _h, _o, seconds, patches = webm_duration.head_plan(write(tmp_path, data))
    assert seconds == 4.0 and patches == ()


def test_the_part_is_sent_with_the_jump_closed(app, client, monkeypatch, tmp_path):
    data, _, info_end = recorder_webm(_clusters(3))
    sent = []

    def answer(name, form):
        return _R({"segments": [{"start": 0.0, "end": 4.0, "text": " Gesagt."}]})

    code, sid, rid, calls = _whisper(app, client, monkeypatch, answer, parts=0)
    _send(client, code, rid, 1, 0, data, rec="r1")
    real = interview_transcribe._transcribe_file

    def spy(path, *a, **k):
        with open(path, "rb") as f:
            sent.append(f.read())
        return real(path, *a, **k)

    monkeypatch.setattr(interview_transcribe, "_transcribe_file", spy)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert tr["state"] == "done" and "Gesagt." in tr["text"]
    assert len(sent) == 1 and sent[0] != data and sent[0].endswith(data[-100:])
    assert (JUMP).to_bytes(4, "big") not in sent[0] and b"\x44\x89\x88" in sent[0]


def test_a_long_part_without_text_is_an_error_not_done(app, client, monkeypatch):
    data, _, _ = recorder_webm([(i * 5000, list(range(0, 5000, 1000))) for i in range(20)])  # 99 s, no jump

    def answer(name, form):
        return _R({"segments": [], "text": ""})

    code, sid, rid, calls = _whisper(app, client, monkeypatch, answer, parts=0)
    _send(client, code, rid, 1, 0, data, rec="r1")
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert tr["state"] == "error" and "kein Text erkannt" in tr["error"] and "Teil 1" in tr["error"]


def test_a_short_silent_part_is_still_done(app, client, monkeypatch):
    """Teil 1 of M-04 held 1.2 KB and one block: no text from it is no error."""
    def answer(name, form):
        return _R({"segments": [], "text": ""})

    code, sid, rid, calls = _whisper(app, client, monkeypatch, answer)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    assert _tr(app, rid)["state"] == "done"
