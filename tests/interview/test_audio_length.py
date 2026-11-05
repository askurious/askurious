"""R-08 (Ralf 2026-11-05): an uploaded recording of 23:28 stood as "0 Min." in the interview list, because "Dauer" is
the interview clock and that never ran. app/audio_length.py reads the length from each container the upload takes."""
import struct

from app import audio_length
from tests.interview.test_interview import make
from tests.interview.test_interview_audio_duration import el, recorder_webm
from tests.interview.test_interview_data import start


def write(tmp_path, name, data):
    full = tmp_path / name
    full.write_bytes(data)
    return str(full)


def box(kind, payload):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def test_mp4_from_mvhd(tmp_path):
    mvhd = box(b"mvhd", b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">II", 1000, 1408000) + b"\x00" * 80)
    data = box(b"ftyp", b"M4A \x00\x00\x00\x00") + box(b"mdat", b"\x00" * 500) + box(b"moov", mvhd)
    assert audio_length.seconds(write(tmp_path, "a.m4a", data), "audio/mp4") == 1408.0


def test_wav_from_data_size(tmp_path):
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
    data = b"RIFF" + struct.pack("<I", 0) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", 64000) + b"\x00" * 64000
    assert audio_length.seconds(write(tmp_path, "a.wav", data), "audio/wav") == 2.0


def page(granule, payload):
    return b"OggS\x00\x00" + struct.pack("<q", granule) + b"\x00" * 12 + b"\x01" + bytes([len(payload)]) + payload


def test_ogg_opus_from_the_last_granule(tmp_path):
    head = page(0, b"OpusHead\x01\x01" + struct.pack("<H", 312) + struct.pack("<I", 48000) + b"\x00\x00\x00")
    data = head + page(48000 * 30, b"x" * 20) + page(48000 * 60 + 312, b"y" * 20)
    assert audio_length.seconds(write(tmp_path, "a.ogg", data), "audio/ogg") == 60.0


def test_mp3_with_xing_and_without(tmp_path):
    frame = bytes([0xFF, 0xFB, 0x90, 0xC4])  # MPEG-1 layer III, 128 kbit/s, 44.1 kHz, mono
    xing = frame + b"\x00" * 17 + b"Xing" + struct.pack(">I", 1) + struct.pack(">I", 3828) + b"\x00" * 300
    assert round(audio_length.seconds(write(tmp_path, "a.mp3", b"ID3\x04\x00\x00\x00\x00\x00\x00" + xing), "audio/mpeg"), 1) == 100.0
    cbr = frame + b"\x00" * (16000 * 30 - 4)  # 30 s at 128 kbit/s
    assert audio_length.seconds(write(tmp_path, "b.mp3", cbr), "audio/mpeg") == 30.0


def test_webm_recorder_and_named(tmp_path):
    data, _, _ = recorder_webm([(0, [0, 20]), (5000, [0, 980])])
    assert audio_length.seconds(write(tmp_path, "a.webm", data), "audio/webm") == 5.98
    named, _, _ = recorder_webm([(0, [0, 500])], info_extra=el(b"\x44\x89", struct.pack(">d", 90500.0)))
    assert audio_length.seconds(write(tmp_path, "b.webm", named), "audio/webm") == 90.5


def test_unreadable_gives_none(tmp_path):
    assert audio_length.seconds(write(tmp_path, "x.m4a", b"nonsense"), "audio/mp4") is None
    assert audio_length.seconds(str(tmp_path / "missing.ogg"), "audio/ogg") is None
    assert audio_length.seconds(write(tmp_path, "y.bin", b"abc"), "audio/x-unknown") is None


def test_list_shows_the_recording_when_the_clock_never_ran(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    data, _, _ = recorder_webm([(i * 30000, [0]) for i in range(60)] + [(1800000, [0, 1000])])  # 30 min 1 s
    url = f"/guide/{code}/interviews/{rid}/audio?source=upload&part=1&seq=0&rec=u1&start=0&origin=phone&speaker=&all=1"
    assert client.post(url, data=data, content_type="audio/webm").status_code == 200
    html = client.get(f"/guide/{code}/interviews").data.decode()
    assert "30 Min." in html and "(Aufnahme)" in html


def test_a_cut_webm_gives_none(tmp_path):
    assert audio_length.seconds(write(tmp_path, "c.webm", b"\x1a\x45\xdf\xa3AAAA"), "audio/webm") is None
