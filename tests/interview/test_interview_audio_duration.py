"""Interview recordings showed "0:00 / 0:00" in the player although they hold sound (Ralf, 2026-10-23): a WebM from
MediaRecorder names no Duration. app/webm_duration.py finds the length from the block timestamps and serves the file
with a head that names it; the stored file stays unchanged."""
import struct

from app import webm_duration
from tests.interview.test_interview_data import start
from tests.interview.test_interview import make

UNKNOWN = b"\x01\xff\xff\xff\xff\xff\xff\xff"


def el(eid, payload, unknown=False):
    return eid + (UNKNOWN if unknown else (1 << 56 | len(payload)).to_bytes(8, "big")) + payload


def block(rel):
    return el(b"\xa3", b"\x81" + struct.pack(">h", rel) + b"\x80" + b"opus" * 4)


def recorder_webm(clusters, info_extra=b""):
    """A WebM as Chrome's MediaRecorder writes it: Segment and Clusters of unknown size, Info without Duration."""
    head = el(b"\x1a\x45\xdf\xa3", el(b"\x42\x82", b"webm"))
    info = el(b"\x15\x49\xa9\x66", el(b"\x2a\xd7\xb1", (1000000).to_bytes(3, "big")) + el(b"\x4d\x80", b"Chrome") + info_extra)
    tracks = el(b"\x16\x54\xae\x6b", el(b"\xae", el(b"\xd7", b"\x01")))
    body = b"".join(el(b"\x1f\x43\xb6\x75", el(b"\xe7", tc.to_bytes(4, "big")) + b"".join(block(r) for r in rels), unknown=True)
                    for tc, rels in clusters)
    return head + b"\x18\x53\x80\x67" + UNKNOWN + info + tracks + body, len(head) + 12, len(head) + 12 + len(info)


def write(tmp_path, data, name="teil-1.webm"):
    full = tmp_path / name
    full.write_bytes(data)
    return str(full)


def test_length_from_the_last_block(tmp_path):
    data, info_start, info_end = recorder_webm([(0, [0, 20, 40]), (5000, [0, 980]), (12000, [0, 400, 3020])])
    full = write(tmp_path, data)
    head, offset, seconds = webm_duration.head_plan(full)
    assert seconds == 15.02 and offset == info_end
    assert head[:info_start] == data[:info_start] and b"\x44\x89\x88" + struct.pack(">d", 15020.0) in head
    joined = webm_duration.Joined(head, full, offset)
    try:
        served = joined.read()
    finally:
        joined.close()
    assert served == head + data[info_end:] and len(served) == joined.length == len(data) + len(head) - info_end
    assert webm_duration.head_plan(write(tmp_path, served, "served.webm")) is None  # now names its length


def test_files_that_name_their_length_or_are_no_recorder_webm_stay(tmp_path):
    named, _, _ = recorder_webm([(0, [0, 500])], info_extra=el(b"\x44\x89", struct.pack(">d", 500.0)))
    assert webm_duration.head_plan(write(tmp_path, named, "a.webm")) is None
    with_cues = recorder_webm([(0, [0, 500])])[0] + el(b"\x1c\x53\xbb\x6b", b"")
    assert webm_duration.head_plan(write(tmp_path, with_cues, "b.webm")) is None  # positions would shift
    assert webm_duration.head_plan(write(tmp_path, b"\x1a\x45\xdf\xa3AAAA", "c.webm")) is None
    assert webm_duration.head_plan(write(tmp_path, b"", "d.webm")) is None
    assert webm_duration.head_plan(str(tmp_path / "missing.webm")) is None


def test_a_last_piece_cut_in_the_middle(tmp_path):
    """While the interview runs the file ends somewhere in a block: the length counts the blocks that are whole."""
    data, _, _ = recorder_webm([(0, [0, 20]), (4000, [0, 700])])
    assert webm_duration.seconds(write(tmp_path, data[:-5])) == 4.0


def test_served_with_length_and_ranges(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    data, _, info_end = recorder_webm([(0, [0, 20]), (3000, [0, 1500])])
    url = f"/guide/{code}/interviews/{rid}/audio"
    for seq, i in enumerate(range(0, len(data), 60)):  # in pieces, as the recorder sends them
        assert client.post(f"{url}?part=1&seq={seq}", data=data[i:i + 60], content_type="audio/webm").status_code == 200
    r = client.get(f"{url}/1")
    whole = r.data
    r.close()
    assert r.status_code == 200 and r.mimetype == "audio/webm" and r.headers["Accept-Ranges"] == "bytes"
    assert b"\x44\x89\x88" + struct.pack(">d", 4500.0) in whole and whole.endswith(data[info_end:])
    assert int(r.headers["Content-Length"]) == len(whole) and r.headers["Cache-Control"] == "private, no-store"
    r = client.get(f"{url}/1", headers={"Range": "bytes=10-"})
    part = r.data
    r.close()
    assert r.status_code == 206 and part == whole[10:]
    r = client.get(f"{url}/1", headers={"Range": f"bytes={len(whole) - 30}-{len(whole) - 1}"})
    tail = r.data
    r.close()
    assert r.status_code == 206 and tail == whole[-30:]
    r = client.get(f"{url}/1?download=1")
    r.close()
    assert r.headers["Content-Disposition"].startswith("attachment") and ".webm" in r.headers["Content-Disposition"]


def test_players_load_the_length(app, client):
    """The interviewer's list loads the length; the researchers' list once its fold opens (app.js)."""
    code, sid = make(app, client)
    rid = start(app, client, code)
    data, _, _ = recorder_webm([(0, [0, 20])])
    client.post(f"/guide/{code}/interviews/{rid}/audio?part=1&seq=0", data=data, content_type="audio/webm")
    page = client.get(f"/guide/{code}/interviews").data.decode()
    assert 'preload="none" data-preload-open' in page
