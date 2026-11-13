"""The length of a WebM that a browser recorder wrote, put into its head when the file is served.

MediaRecorder (Chrome, Edge, Firefox) writes a live stream: the Segment has an unknown size and its Info names no
Duration, because the length is not known while recording. A player then shows "0:00 / 0:00" (or no end at all)
until it has played the whole file, and cannot jump. Interview recordings come in such pieces (app/interview_audio.py),
so their parts were shown with 0:00 although they hold sound (Ralf, 2026-10-23).

head_plan() reads the file once (the timestamps of its blocks, not their sound) and returns a new head with a Duration
element; Joined serves that head and then the rest of the file unchanged, so the stored file stays as it was (pieces
are appended to it by byte offset). Only a stream with an unknown Segment size and without SeekHead or Cues is
changed by a longer head: there the bytes after Info have no recorded positions that it would shift. A file with
SeekHead or Cues gets a head of the same length instead (Duration in the room of the program names). Anything else,
or a file that cannot be read as WebM, is served as it is.
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


def _cluster_last(buf, pos, size):
    """(latest block time, end) of the Cluster whose data begins at pos; None when it holds no whole block."""
    end = len(buf) if size is None else min(len(buf), pos + size)
    base, last = 0, None
    try:
        while pos < end:
            cstart = pos
            cid, csize, pos = _element(buf, pos)
            if size is None and cid in LEVEL1:
                pos = cstart
                break
            if csize is None:
                raise ValueError("unknown size in a cluster")
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
        pos = len(buf)
    return last, pos


TAIL = 4 * 1024 * 1024  # bytes before the end searched for the last Cluster
CLUSTER_ID = CLUSTER.to_bytes(4, "big")


def _tail(buf, first):
    """(latest block time, Cues or SeekHead after it) from the last Clusters only, or None to read them all.
    Reading every block of a long recording took 8.5 s on the server (D-01, 98 minutes, 37 MB; Ralf 2026-11-05),
    and the player showed 0:00 until then. A candidate counts only when its Timecode comes first and every element
    after it parses up to the end of the file."""
    low = max(first, len(buf) - TAIL)
    at = len(buf)
    while True:
        at = buf.rfind(CLUSTER_ID, low, at)
        if at < 0:
            return None
        try:
            eid, size, pos = _element(buf, at)
            cid, _csize, _ = _element(buf, pos)
            if eid != CLUSTER or cid != TIMECODE:
                raise ValueError("no cluster")
            last, pos = _cluster_last(buf, pos, size)
            indexed = False
            while pos < len(buf):  # what follows: Cues, Tags, or the next Cluster still arriving
                try:
                    eid, esize, pos = _element(buf, pos)
                except _Short:
                    break
                if eid == CLUSTER and esize is None:
                    break  # a Cluster without a whole block yet (its piece is still arriving)
                if eid not in LEVEL1 or esize is None:
                    raise ValueError("not a level-1 element")
                indexed = indexed or eid in (SEEKHEAD, CUES)
                pos += esize
            if last is not None and last > 0:
                return last, indexed
        except (ValueError, _Short):
            pass


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
    indexed = False
    while pos < len(buf):
        try:
            start = pos
            eid, size, pos = _element(buf, pos)
        except _Short:
            break
        if eid in (SEEKHEAD, CUES):  # positions recorded: only a head of the same length may be set (head_plan)
            if size is None:
                return None
            indexed = True
            pos += size
            continue
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
        if eid == CLUSTER and info is not None and last is None:
            found = _tail(buf, start)
            if found is not None:
                last, indexed = found[0], indexed or found[1] or buf.find(CUES.to_bytes(4, "big"), start) >= 0
                break
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
    return info[0], info[1], scale, last, indexed


JUMP_SECONDS = 30  # a Cluster that begins this much later than the one before: a jump in the timestamps, no sound
FRAME_SECONDS = 0.02  # where the Cluster after a jump is put: one Opus frame after the last block before it


def _ends_well(buf, end):
    """Whether a Cluster of known size ends at the end of the file (or beyond: still arriving) or at a level-1 element."""
    if end >= len(buf):
        return True
    try:
        eid, _size, _pos = _element(buf, end)
    except (ValueError, _Short):
        return False
    return eid in LEVEL1


def _clusters(buf, start):
    """[(start of the data, size or None, position and width of its Timecode, Timecode)] of every Cluster after
    `start`, found by their ID without reading the blocks (C speed). A candidate counts only with a Timecode right
    after its head and an unknown size (MediaRecorder) or a size that ends where the next level-1 element begins."""
    found, at = [], start
    while True:
        at = buf.find(CLUSTER_ID, at)
        if at < 0:
            return found
        try:
            eid, size, pos = _element(buf, at)
            cid, csize, tpos = _element(buf, pos)
            if (cid == TIMECODE and csize is not None and 1 <= csize <= 8 and tpos + csize <= len(buf)
                    and (size is None or _ends_well(buf, pos + size))):
                found.append((pos, size, tpos, csize, _uint(buf[tpos:tpos + csize])))
                at = tpos + csize
                continue
        except (ValueError, _Short):
            pass
        at += 4


def _jump_patches(buf, start, scale):
    """([(byte offset, new Timecode bytes)], units taken out) that close the jumps in the Cluster timestamps.

    Chrome's MediaRecorder sometimes writes the first Cluster at 0 and the next ones at the clock of the audio
    source, hours later (M-04, tester 2026-11-09: 60 minutes of sound, the second Cluster at 10:43 h, shown as
    11:43:21). A player then shows the wrong length, and the transcription service, which cuts a recording after
    3 hours by its timestamps, got only the first block of it and found no speech. A jump forward by more than
    JUMP_SECONDS is closed: the Cluster after it and all later ones move back so that it follows the last block
    before it. A smaller Timecode always fits in the bytes of the old one, so no position in the file moves."""
    clusters = _clusters(buf, start)
    jump, frame = JUMP_SECONDS * 1e9 / scale, max(1, round(FRAME_SECONDS * 1e9 / scale))
    patches, shift = [], 0
    for i, (_pos, _size, tpos, width, value) in enumerate(clusters):
        if i and value - clusters[i - 1][4] > jump:
            before = clusters[i - 1]
            last, _end = _cluster_last(buf, before[0], before[1])
            last = before[4] if last is None else last
            shift = max(shift, value - (last - shift + frame))
        if shift:
            patches.append((tpos, max(0, value - shift).to_bytes(width, "big")))
    return patches, shift


MUXING_APP, WRITING_APP, VOID = 0x4D80, 0x5741, 0xEC
SPARE = {MUXING_APP, WRITING_APP, VOID}  # children of Info that only name programs (or fill room)


def _same_length_head(buf, info_start, info_end, duration):
    """The head up to the end of Info with Duration in place of MuxingApp, WritingApp and Void, the rest of the room
    filled with a Void, so Info keeps its length and no recorded position (SeekHead, Cues) moves; None without room.
    Recordings with Cues at their end (L-02, tester 2026-11-05) showed 0:00 because no head was set for them."""
    eid, size, payload = _element(buf, info_start)
    kept, room, inner = [], 0, payload
    while inner < info_end:
        start = inner
        cid, csize, inner = _element(buf, inner)
        if csize is None:
            return None
        inner += csize
        if cid in SPARE:
            room += inner - start
        else:
            kept.append(bytes(buf[start:inner]))
    left = room - len(duration)
    if left < 0 or left == 1:
        return None
    void = b""
    if left:
        if left - 2 < 127:
            void = bytes([VOID, 0x80 | (left - 2)]) + bytes(left - 2)
        else:
            void = bytes([VOID, 0x40 | ((left - 3) >> 8), (left - 3) & 0xFF]) + bytes(left - 3)
    head = bytes(buf[:payload]) + b"".join(kept) + duration + void
    return head if len(head) == info_end else None


def _encode_size(n):
    return (0x01 << 56 | n).to_bytes(8, "big")  # 8-byte size: room for any Info


def head_plan(full):
    """(new head, byte offset in the file where the rest begins, seconds, patches of that rest) for a recorder WebM,
    or None. The patches [(byte offset in the file, bytes)] close jumps in the timestamps (_jump_patches)."""
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
                    info_start, info_end, scale, last, indexed = found
                    eid, size, payload = _element(buf, info_start)
                    # Cues name the times of Clusters: a file with them keeps its timestamps
                    patches, shift = ([], 0) if indexed else _jump_patches(buf, info_end, scale)
                    last -= shift
                    seconds_units = float(last)  # in TimecodeScale units, as Duration counts
                    duration = bytes([0x44, 0x89, 0x88]) + struct.pack(">d", seconds_units)
                    if indexed:
                        head = _same_length_head(buf, info_start, info_end, duration)
                        plan = (head, info_end, last * scale / 1e9, ()) if head else None
                    elif last > 0:
                        body = bytes(buf[payload:info_end]) + duration
                        plan = (bytes(buf[:info_start]) + bytes([0x15, 0x49, 0xA9, 0x66]) + _encode_size(len(body)) + body, info_end,
                                last * scale / 1e9, tuple(patches))
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


def has_jumps(full):
    """Whether the timestamps of a recorder WebM jump (head_plan closes them when the file is served)."""
    plan = head_plan(full)
    return bool(plan and plan[3])


def write_served(full, target):
    """Write the file as it is served (head with its length, jumps closed) to `target`; False when served as is."""
    plan = head_plan(full)
    if plan is None:
        return False
    joined = Joined(plan[0], full, plan[1], plan[3])
    try:
        with open(target, "wb") as out:
            while True:
                chunk = joined.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    finally:
        joined.close()
    return True


class Joined(io.RawIOBase):
    """A read-only file: `head`, then the file at `full` from byte `offset` on (for Range requests too), with
    `patches` [(byte offset in the file, bytes)] put over the bytes they name."""

    def __init__(self, head, full, offset, patches=()):
        super().__init__()
        self.head, self.offset, self.patches = head, offset, tuple(patches)
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
            at = self.offset + self.pos - len(self.head)
            self.file.seek(at)
            got = self.file.read(min(n, self.offset + self.rest - at))
            for where, value in self.patches:
                if where + len(value) > at and where < at + len(got):
                    got = got if isinstance(got, bytearray) else bytearray(got)
                    lo, hi = max(where, at), min(where + len(value), at + len(got))
                    got[lo - at:hi - at] = value[lo - where:hi - where]
        out[:len(got)] = got
        self.pos += len(got)
        return len(got)

    def close(self):
        self.file.close()
        super().close()
