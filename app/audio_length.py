"""The length of a stored interview recording in seconds, read from its container without decoding the sound.

The interview list shows the interview clock as "Dauer". A recording made elsewhere and uploaded afterwards
("Telefon oder Diktiergerät") never ran that clock, so the list said "0 Min." next to a player showing 23:28
(R-08, Ralf 2026-11-05); then the list shows the recording's length instead. The app image has no ffmpeg, so each
container the upload takes (app/interview_audio.MIMES) is read here: WebM (app/webm_duration.py, or the Duration it
names), MP4/M4A (mvhd), Ogg (last granule position), WAV (data size / byte rate) and MP3 (Xing/Info or VBRI frame
count, else the bit rate of the first frame). Unknown or unreadable files give None.
"""
import os
import struct
import threading
from collections import OrderedDict

from app import webm_duration

_cache = OrderedDict()
_lock = threading.Lock()
CACHE_SIZE = 512
TAIL = 65536


def seconds(full, mime):
    try:
        stat = os.stat(full)
    except OSError:
        return None
    key = (full, stat.st_size, stat.st_mtime_ns)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    reader = READERS.get(mime)
    try:
        with open(full, "rb") as f:
            value = reader(f, stat.st_size, full) if reader and stat.st_size else None
    except Exception:  # a damaged or still arriving file: no length, never an error page for the list
        value = None
    if value is not None and not (0 < value < 7 * 86400):
        value = None
    with _lock:
        _cache[key] = value
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return value


def _webm(f, size, full):
    found = webm_duration.seconds(full)  # a recorder's WebM without Duration
    if found is not None:
        return found
    buf = f.read(min(size, TAIL))  # a WebM that names its length in Info
    eid, esize, pos = webm_duration._element(buf, 0)
    pos += esize
    eid, _ssize, pos = webm_duration._element(buf, pos)
    if eid != webm_duration.SEGMENT:
        return None
    scale, duration = 1000000, None
    while pos < len(buf):
        eid, esize, pos = webm_duration._element(buf, pos)
        if esize is None:
            return None
        if eid == webm_duration.INFO:
            inner, end = pos, pos + esize
            while inner < end:
                cid, csize, inner = webm_duration._element(buf, inner)
                data = buf[inner:inner + csize]
                if cid == webm_duration.TIMECODE_SCALE:
                    scale = int.from_bytes(data, "big") or 1000000
                elif cid == webm_duration.DURATION:
                    duration = struct.unpack(">f" if csize == 4 else ">d", data)[0]
                inner += csize
            return duration * scale / 1e9 if duration else None
        pos += esize
    return None


def _boxes(f, start, end):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        size, kind = struct.unpack(">I4s", f.read(8))
        head = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head:
            return
        yield kind, pos + head, pos + size
        pos += size


def _mp4(f, size, full):
    for kind, start, end in _boxes(f, 0, size):
        if kind == b"moov":
            for inner, istart, _iend in _boxes(f, start, end):
                if inner == b"mvhd":
                    f.seek(istart)
                    version = f.read(4)[0]
                    if version == 1:
                        f.seek(16, 1)
                        scale, duration = struct.unpack(">IQ", f.read(12))
                    else:
                        f.seek(8, 1)
                        scale, duration = struct.unpack(">II", f.read(8))
                    return duration / scale if scale else None
            return None
    return None


def _ogg(f, size, full):
    head = f.read(min(size, 4096))
    if b"OpusHead" in head:
        at = head.index(b"OpusHead")
        rate, skip = 48000, struct.unpack("<H", head[at + 10:at + 12])[0]
    elif b"\x01vorbis" in head:
        at = head.index(b"\x01vorbis")
        rate, skip = struct.unpack("<I", head[at + 12:at + 16])[0], 0
    else:
        return None
    f.seek(max(0, size - TAIL))
    tail = f.read()
    at = tail.rfind(b"OggS")
    if at < 0 or not rate:
        return None
    granule = struct.unpack("<q", tail[at + 6:at + 14])[0]
    return (granule - skip) / rate if granule > skip else None


def _wav(f, size, full):
    head = f.read(12)
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None
    rate = None
    pos = 12
    while pos + 8 <= size:
        f.seek(pos)
        kind, length = struct.unpack("<4sI", f.read(8))
        if kind == b"fmt ":
            rate = struct.unpack("<I", f.read(12)[8:12])[0]  # byte rate
        elif kind == b"data":
            return min(length, size - pos - 8) / rate if rate else None
        pos += 8 + length + (length & 1)
    return None


# MPEG audio: bit rates (kbit/s) by [version is 1][layer index], sample rates by version
_RATES = {(True, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],      # MPEG-1 layer III
          (False, 3): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],          # MPEG-2/2.5 layer III
          (True, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],      # MPEG-1 layer II
          (False, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]}
_SAMPLE = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _mp3(f, size, full):
    head = f.read(10)
    start = 0
    if head[:3] == b"ID3":
        start = 10 + ((head[6] & 0x7F) << 21 | (head[7] & 0x7F) << 14 | (head[8] & 0x7F) << 7 | (head[9] & 0x7F))
    f.seek(start)
    buf = f.read(4096)
    at = next((i for i in range(len(buf) - 4) if buf[i] == 0xFF and buf[i + 1] & 0xE0 == 0xE0), None)
    if at is None:
        return None
    b1, b2, b3 = buf[at + 1], buf[at + 2], buf[at + 3]
    version, layer = (b1 >> 3) & 3, (b1 >> 1) & 3  # version 3 = MPEG-1; layer 1 = III, 2 = II
    if version == 1 or layer not in (1, 2):
        return None
    layer = 3 if layer == 1 else 2
    mpeg1 = version == 3
    bitrate = _RATES[(mpeg1, layer)][b2 >> 4] * 1000
    rate = _SAMPLE[version][(b2 >> 2) & 3] if ((b2 >> 2) & 3) < 3 else None
    if not bitrate or not rate:
        return None
    per_frame = 1152 if layer == 2 or mpeg1 else 576
    mono = (b3 >> 6) == 3
    side = (17 if mono else 32) if mpeg1 else (9 if mono else 17)
    for tag, offset in ((b"Xing", 4 + side), (b"Info", 4 + side)):
        if buf[at + offset:at + offset + 4] == tag and buf[at + offset + 7] & 1:
            frames = struct.unpack(">I", buf[at + offset + 8:at + offset + 12])[0]
            return frames * per_frame / rate
    if buf[at + 36:at + 40] == b"VBRI":
        frames = struct.unpack(">I", buf[at + 50:at + 54])[0]
        return frames * per_frame / rate
    return (size - start - at) * 8 / bitrate


READERS = {"audio/webm": _webm, "audio/mp4": _mp4, "audio/ogg": _ogg, "audio/wav": _wav, "audio/mpeg": _mp3}
