"""The size of a ZIP file's central directory, read before zipfile reads it (audit 2026-10-13, DA-4).

zipfile.ZipFile builds one ZipInfo object for every entry of the central directory before anyone can count them:
38 MB of ZIP64 entries that all point to the same file are 850,000 entries, some 300 MB and 2 to 3 s per upload.
The end-of-central-directory record (the last 64 KB of the file) names the number of entries and the size of the
directory; a file whose directory holds more than a reader allows is refused before zipfile reads it."""
import struct

EOCD = b"PK\x05\x06"
EOCD64 = b"PK\x06\x06"
LOCATOR64 = b"PK\x06\x07"
PER_ENTRY = 256  # bytes of the directory per allowed entry: 46 fixed, the name and the extra fields of a real file


def directory(raw):
    """(entries, directory size in bytes) as the end record of the ZIP names them, or None if there is none that fits.
    Found in the order the ZIP format allows, the one the reader of the upload follows: first the last 22 bytes as
    an end record without comment, only then the last signature in the last 64 KB, whose comment has to end within
    the file. Before, the last signature
    counted at once, and a signature written into the offset field of the real end record hid it (None, not counted)
    while zipfile read the whole directory anyway (audit 2026-10-15, T03-N2)."""
    if len(raw) >= 22 and raw[-22:-18] == EOCD and raw[-2:] == b"\0\0":
        tail, at = raw[-22:], 0
    else:
        tail = raw[-(22 + (1 << 16)):]
        at = tail.rfind(EOCD)
        if at < 0 or len(tail) - at < 22 or at + 22 + struct.unpack("<H", tail[at + 20:at + 22])[0] > len(tail):
            return None
    _, _, _, _, entries, size, _, _ = struct.unpack("<4sHHHHIIH", tail[at:at + 22])
    # ZIP64: its record and locator stand right before the end record, where zipfile looks for them too; without
    # them the values above count (0xFFFF entries, a directory of 4 GB: refused)
    start = len(raw) - len(tail) + at - 20 - 56
    if start >= 0 and raw[start:start + 4] == EOCD64 and raw[start + 56:start + 60] == LOCATOR64:
        entries, size = struct.unpack("<QQ", raw[start + 32:start + 48])
    return entries, size


def too_many(raw, max_entries):
    """True if the ZIP's directory names more than max_entries entries or is larger than they need, or if no end
    record fits (a file zipfile could only read by a record this check did not see; audit 2026-10-15, T03-N2)."""
    found = directory(raw)
    return found is None or found[0] > max_entries or found[1] > max_entries * PER_ENTRY
