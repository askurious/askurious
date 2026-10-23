"""The central directory of an uploaded ZIP is counted from its end record before zipfile reads it (audit 2026-10-13,
DA-4): 38 MB of ZIP64 entries pointing to one file were 850,000 ZipInfo objects, ~300 MB, before MAX_MEMBERS was
checked."""
import io
import struct
import zipfile

import pytest

from app import survey_files, zip_limits


def _flood(n):
    """A ZIP whose directory names `n` entries that all point to one local header (ZIP64 end record)."""
    name = b"a"
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, 0, 0, 0, 1, 0) + name
    cd = b"".join(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0) + name
                  for _ in range(n))
    z64 = struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, n, n, len(cd), len(local))
    loc = struct.pack("<IIQI", 0x07064B50, 0, len(local) + len(cd), 1)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    return local + cd + z64 + loc + eocd


def test_directory_is_read_from_the_end_record():
    assert zip_limits.directory(_flood(20_000))[0] == 20_000
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k in range(3):
            z.writestr(f"f{k}.json", "{}")
    entries, size = zip_limits.directory(buf.getvalue())
    assert entries == 3 and 0 < size < 3 * zip_limits.PER_ENTRY
    assert zip_limits.directory(b"not a zip") is None


def test_flooded_directory_is_refused_before_zipfile_reads_it(app, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("zipfile read the directory")

    raw = _flood(survey_files.MAX_MEMBERS + 1)
    monkeypatch.setattr(survey_files.zipfile, "ZipFile", never)
    with app.test_request_context():
        with pytest.raises(survey_files.Refused):
            survey_files.read(raw)


def test_directory_larger_than_its_entries_need_is_refused():
    """The count in the end record can be small while the directory goes on: zipfile reads by its size."""
    raw = bytearray(_flood(10))
    raw[-98 + 32:-98 + 40] = struct.pack("<Q", 5)                                    # "5 entries"
    assert not zip_limits.too_many(bytes(raw), 5)
    raw[-98 + 40:-98 + 48] = struct.pack("<Q", 5 * zip_limits.PER_ENTRY + 1)          # but a long directory
    assert zip_limits.too_many(bytes(raw), 5)


def test_signature_in_the_offset_field_does_not_hide_the_directory():
    """Abgleich 2026-10-15 T03-N2: "PK\x05\x06" written into the offset field of the real end record made the last
    signature lie within its last 22 bytes; directory() gave None and too_many() let the file through, while zipfile
    found the real record at the end and read every entry."""
    raw = bytearray(_flood(20))
    eocd = len(raw) - 22
    raw[eocd + 16:eocd + 20] = b"PK\x05\x06"  # the offset field of the (ZIP64) end record
    assert zip_limits.directory(bytes(raw))[0] == 20
    assert zip_limits.too_many(bytes(raw), 5)


def test_a_file_without_a_fitting_end_record_is_refused():
    """Abgleich 2026-10-15 T03-N2: no plausible end record (a signature whose comment runs past the file) is refused,
    not let through."""
    raw = bytearray(_flood(3))
    raw[-2:] = struct.pack("<H", 500)  # a comment of 500 bytes that is not there
    assert zip_limits.directory(bytes(raw)) is None and zip_limits.too_many(bytes(raw), 1000)
