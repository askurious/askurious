"""The length of a WebM that a browser recorder wrote, put into its head when the file is served.

MediaRecorder (Chrome, Edge, Firefox) writes a live stream: the Segment has an unknown size and its Info names no
Duration, because the length is not known while recording. A player then shows "0:00 / 0:00" (or no end at all)
until it has played the whole file, and cannot jump. Interview recordings come in such pieces (app/interview_audio.py),
so their parts were shown with 0:00 although they hold sound (Ralf, 2026-10-23).

head_plan() reads the file once (the timestamps of its blocks, not their sound) and returns a new head with a Duration
element; Joined serves that head and then the rest of the file unchanged, so the stored file stays as it was (pieces
are appended to it by byte offset). Only a stream with an unknown Segment size and without SeekHead or Cues is
changed: there the bytes after Info have no recorded positions that the longer head would shift. Anything else, or a
file that cannot be read as WebM, is served as it is.
"""
import io
import mmap
import os
import struct
import threading
from collections import OrderedDict

EBML = 0x1A45DFA3
SEGMENT = 0x18538067
INFO = 0x1549A966
SEEKHEAD = 0x114D9B74
CUES = 0x1C53BB6B
CLUSTER = 0x1F43B675
TIMECODE_SCALE = 0x2AD7B1
DURATION = 0x4489
TIMECODE = 0xE7
SIMPLEBLOCK = 0xA3
BLOCKGROUP = 0xA0
BLOCK = 0xA1
# Children of a Segment: an unknown-size Cluster ends where one of these begins.
LEVEL1 = {INFO, SEEKHEAD, CUES, CLUSTER, 0x1654AE6B, 0x1254C367, 0x1941A469, 0x1043A770}

_cache = OrderedDict()
_lock = threading.Lock()
CACHE_SIZE = 64


class _Short(Exception):
    """The file ends inside an element (the last piece is still arriving)."""


def _vint(buf, pos, keep_marker):
    if pos >= len(buf):
        raise _Short
    first = buf[pos]
    length = 1
    while length <= 8 and not first & (0x80 >> (length - 1)):
        length += 1
    if length > 8:
        raise ValueError("bad vint")
    if pos + length > len(buf):
        raise _Short
    value = first if keep_marker else first & (0xFF >> length)
    for b in buf[pos + 1:pos + length]:
        value = (value << 8) | b
    if not keep_marker and value == (1 << (7 * length)) - 1:
        value = None  # all ones: unknown size
    return value, pos + length


def _element(buf, pos):
    """(id, size or None, start of the data)."""
    eid, pos = _vint(buf, pos, True)
    size, pos = _vint(buf, pos, False)
    return eid, size, pos


def _uint(data):
    return int.from_bytes(data, "big") if data else 0


def _block_time(buf, pos, end):
    """The timecode of a (Simple)Block relative to its cluster."""
    _track, pos = _vint(buf, pos, False)
    if pos + 2 > end:
        raise _Short
    return struct.unpack(">h", buf[pos:pos + 2])[0]


def _scan(buf):
    """(end of the Segment head before Info, start and end of Info, its payload, TimecodeScale, last timecode) or None."""
    eid, size, pos = _element(buf, 0)
    if eid != EBML or size is None:
        return None
    pos += size
    eid, size, pos = _element(buf, pos)
    if eid != SEGMENT or size is not None:
        return None
    info = None
    scale = 1000000
    last = None
    while pos < len(buf):
        try:
            start = pos
            eid, size, pos = _element(buf, pos)
        except _Short:
            break
        if eid in (SEEKHEAD, CUES):
            return None
        if eid == INFO:
            if size is None or pos + size > len(buf) or info is not None:
                return None
            end, inner = pos + size, pos
            while inner < end:
                cid, csize, inner = _element(buf, inner)
                if csize is None:
                    return None
                if cid == DURATION:
                    return None  # already named
                if cid == TIMECODE_SCALE:
                    scale = _uint(buf[inner:inner + csize]) or 1000000
                inner += csize
            info = (start, end)
            pos = end
            continue
        if eid == CLUSTER:
            end = len(buf) if size is None else min(len(buf), pos + size)
            base = 0
            try:
                while pos < end:
                    cstart = pos
                    cid, csize, pos = _element(buf, pos)
                    if size is None and cid in LEVEL1:
                        pos = cstart
                        break
                    if csize is None:
                        return None
                    if pos + csize > len(buf):
                        raise _Short
                    if cid == TIMECODE:
                        base = _uint(buf[pos:pos + csize])
                    elif cid == SIMPLEBLOCK:
                        t = base + _block_time(buf, pos, pos + csize)
                        last = t if last is None else max(last, t)
                    elif cid == BLOCKGROUP:
                        inner, gend = pos, pos + csize
                        while inner < gend:
                            gid, gsize, inner = _element(buf, inner)
                            if gid == BLOCK and gsize is not None:
                                t = base + _block_time(buf, inner, inner + gsize)
                                last = t if last is None else max(last, t)
                            inner += gsize or 0
                    pos += csize
            except _Short:
                break
            continue
        if size is None:
            return None
        pos += size
    if info is None or last is None or last <= 0:
        return None
    return info[0], info[1], scale, last


def _encode_size(n):
    return (0x01 << 56 | n).to_bytes(8, "big")  # 8-byte size: room for any Info


def head_plan(full):
    """(new head, byte offset in the file where the unchanged rest begins) for a recorder WebM, or None."""
    try:
        stat = os.stat(full)
    except OSError:
        return None
    key = (full, stat.st_size, stat.st_mtime_ns)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    plan = None
    if stat.st_size:
        try:
            with open(full, "rb") as f, mmap.mmap(f.fileno(), stat.st_size, access=mmap.ACCESS_READ) as buf:
                found = _scan(buf)
                if found:
                    info_start, info_end, scale, last = found
                    eid, size, payload = _element(buf, info_start)
                    seconds_units = float(last)  # in TimecodeScale units, as Duration counts
                    duration = bytes([0x44, 0x89, 0x88]) + struct.pack(">d", seconds_units)
                    body = bytes(buf[payload:info_end]) + duration
                    plan = (bytes(buf[:info_start]) + bytes([0x15, 0x49, 0xA9, 0x66]) + _encode_size(len(body)) + body, info_end,
                            last * scale / 1e9)
        except (OSError, ValueError, _Short):
            plan = None
    with _lock:
        _cache[key] = plan
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return plan


def seconds(full):
    """The length of a recorder WebM in seconds, or None when it names its own or cannot be read."""
    plan = head_plan(full)
    return plan[2] if plan else None


class Joined(io.RawIOBase):
    """A read-only file: `head`, then the file at `full` from byte `offset` on (for Range requests too)."""

    def __init__(self, head, full, offset):
        super().__init__()
        self.head, self.offset = head, offset
        self.file = open(full, "rb")
        self.rest = os.fstat(self.file.fileno()).st_size - offset
        self.length = len(head) + self.rest
        self.pos = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, pos, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.length}[whence]
        self.pos = max(0, base + pos)
        return self.pos

    def readinto(self, out):
        if self.pos >= self.length:
            return 0
        n = min(len(out), self.length - self.pos)
        if self.pos < len(self.head):
            got = self.head[self.pos:self.pos + n]
        else:
            self.file.seek(self.offset + self.pos - len(self.head))
            got = self.file.read(min(n, self.offset + self.rest - self.file.tell()))
        out[:len(got)] = got
        self.pos += len(got)
        return len(got)

    def close(self):
        self.file.close()
        super().close()
