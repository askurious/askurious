"""A small writer for SPSS system files (.sav), without dependencies.

Uncompressed, little-endian, UTF-8 (character code 65001), an own implementation after the published description of
the format (no code of another program):
header, variable records (with continuation records for strings over 8 bytes), value labels, machine info,
display parameters (measurement level), long variable names, very long strings, encoding, long string value
labels, then the cases. Strings up to 32767 bytes: one over 255 bytes is a "very long string", stored in segments
of 255 bytes (record 7.14) as SPSS itself writes it; SPSS reads such files directly (File > Open > Data).

Variables are dicts: name, label, kind ("num" | "str"), decimals (numeric), width (string, bytes),
value_labels {number: text} (numeric) or {code: text} (string, e.g. the answer codes "w", "m"), measure
("nominal" | "ordinal" | "scale"), date ("date" | "time" | "datetime" | "month": a numeric variable shown as a
date, its values from date_number()), missing (numbers, or codes of up to 8 bytes of a string; at most three; record
7.22 for strings wider than 8 bytes), missing_range (low, high) of a numeric variable (low None = LOWEST; then at
most one value in missing).
"""
import re
import struct
from datetime import datetime

SYSMIS = -1.7976931348623157e308
HIGHEST = 1.7976931348623157e308
LOWEST = struct.unpack("<d", bytes.fromhex("feffffffffffefff"))[0]  # second-lowest double
MEASURES = {"nominal": 1, "ordinal": 2, "scale": 3}
RESERVED = {"ALL", "AND", "BY", "EQ", "GE", "GT", "LE", "LT", "NE", "NOT", "OR", "TO", "WITH"}
MAX_STRING = 255     # bytes of a plain string variable (and of what one segment of a very long one holds)
MAX_LONG = 32767     # bytes of a very long string
SEGMENT = 252        # SPSS counts the segments of a very long string per 252 bytes of its width
EPOCH = datetime(1582, 10, 14)  # dates are seconds since then
# format type and width of the date variables: SDATE10 (1987/05/23), TIME5 (14:30), DATETIME17 (23-MAY-1987 14:30),
# MOYR8 (MAY 1987)
DATE_FORMATS = {"date": (39, 10), "time": (21, 5), "datetime": (22, 17), "month": (28, 8)}


def _i(*values):
    return struct.pack(f"<{len(values)}i", *values)


def _cut(text, limit):
    """UTF-8 bytes of text, at most `limit` bytes, never cutting a character in half."""
    data = str(text or "").encode("utf-8")
    if len(data) <= limit:
        return data
    return data[:limit].decode("utf-8", "ignore").encode("utf-8")


def _pad(data, size, fill=b" "):
    return data + fill * (size - len(data))


def valid_name(name, taken):
    """An SPSS variable name (letters, digits, _ . @ # $; starts with a letter; max. 64 bytes), unique."""
    n = re.sub(r"[^A-Za-z0-9_.@#$]", "_", str(name or "")) or "v"
    if not n[0].isalpha():
        n = "v_" + n
    n = n.rstrip(".")[:64]
    if n.upper() in RESERVED:
        n = "v_" + n
    base, k = n, 1
    while n.upper() in taken:
        k += 1
        suffix = f"_{k}"
        n = base[:64 - len(suffix)] + suffix
    taken.add(n.upper())
    return n


def spss_names(names):
    """{name: SPSS variable name} for the columns of one file (valid_name): a reserved word (TO, BY, ...) gets "v_"
    in front, a name over 64 bytes is cut, and one that then equals another gets "_2", "_3". The result depends on
    the set of names only, not on their order, so the .sav, the .sps and the codebook agree: names that are valid
    already keep themselves, the others follow in sorted order."""
    names = list(dict.fromkeys(str(n) for n in names))
    valid = [n for n in names if valid_name(n, set()) == n]
    count = {}
    for n in valid:
        count[n.upper()] = count.get(n.upper(), 0) + 1
    out, taken = {}, set()
    for n in valid:
        if count[n.upper()] == 1:  # SPSS does not tell case apart: two such names are renamed below
            out[n] = n
            taken.add(n.upper())
    for n in sorted(n for n in names if n not in out):
        out[n] = valid_name(n, taken)
    return out


def _segments(width):
    """Widths of the segments a string variable of `width` bytes is stored in: itself up to 255 bytes, else
    segments of width 255 and a shorter last one (each segment holds 255 bytes of the text, the last the rest)."""
    if width <= MAX_STRING:
        return [width]
    n = (width + SEGMENT - 1) // SEGMENT
    return [MAX_STRING] * (n - 1) + [width - (n - 1) * SEGMENT]


def _short_names(names):
    """Unique 8-byte names for the dictionary; the long names follow in record 7.13."""
    out, taken = [], set()
    for i, name in enumerate(names):
        short = re.sub(r"[^A-Z0-9_]", "", name.upper())[:8]
        if not short or not short[0].isalpha() or short in taken:
            short = f"V{i + 1:05d}"[:8]
        taken.add(short)
        out.append(short)
    return out


def _segment_names(shorts, parts):
    """Per variable the 8-byte names of the further segments of a very long string (none for the others)."""
    taken, out = set(shorts), []
    for short, widths in zip(shorts, parts):
        names = []
        for k in range(1, len(widths)):
            name, n = short[:8 - len(str(k))] + str(k), 0
            while name in taken:
                n += 1
                name = f"S{n:07d}"
            taken.add(name)
            names.append(name)
        out.append(names)
    return out


def date_number(kind, text):
    """A date or time written as ISO text as the number SPSS keeps: seconds since 14 October 1582, a time of
    day in seconds since midnight; a month is its first day. None if the text is no such date."""
    try:
        if kind == "time":
            hours, minutes = str(text).split(":")
            return int(hours) * 3600 + int(minutes) * 60
        return (datetime.fromisoformat(str(text) + ("-01" if kind == "month" else "")) - EPOCH).total_seconds()
    except (ValueError, TypeError):
        return None


def _format(var):
    if var.get("date"):
        typ, width = DATE_FORMATS[var["date"]]
        return (typ << 16) | (width << 8)
    if var["kind"] == "str":
        return (1 << 16) | (var["width"] << 8)
    return (5 << 16) | (min(int(var.get("print_width") or 8), 40) << 8) | var.get("decimals", 0)


def _string_missing(var):
    """The missing values of a string variable as SPSS keeps them: at most three, each padded to 8 bytes (longer
    codes cannot be declared)."""
    if var["kind"] != "str":
        return []
    codes = [_cut(m, 8) for m in (var.get("missing") or []) if len(str(m).encode("utf-8")) <= 8]
    return [_pad(c, 8) for c in codes[:3]]


def _missing(var):
    """([8-byte values], n_missing_values) of the variable record: numbers up to three discrete values, or with
    missing_range (low, high; low None = LOWEST) -2 for the range alone and -3 with one discrete value; strings up
    to 8 bytes wide up to three values (wider strings: record 7.22, n 0 here)."""
    if var["kind"] == "str":
        codes = _string_missing(var)
        return (codes, len(codes)) if var["width"] <= 8 else ([], 0)
    discrete = [float(m) for m in (var.get("missing") or [])]
    span = var.get("missing_range")
    if span:
        low, high = span
        values = [LOWEST if low is None else float(low), float(high)] + discrete[:1]
        return [struct.pack("<d", v) for v in values], -len(values)
    discrete = discrete[:3]
    return [struct.pack("<d", v) for v in discrete], len(discrete)


def write(variables, rows, file_label=""):
    """The .sav file as bytes. `rows` are lists of values in the order of `variables` (None = missing)."""
    for var in variables:
        if var["kind"] == "str":
            var["width"] = max(1, min(int(var.get("width") or 1), MAX_LONG))
    parts = [_segments(var["width"]) if var["kind"] == "str" else [0] for var in variables]
    units = [[(w + 7) // 8 if var["kind"] == "str" else 1 for w in widths] for var, widths in zip(variables, parts)]
    shorts = _short_names([v["name"] for v in variables])
    more = _segment_names(shorts, parts)
    now = datetime.now()
    out = [b"$FL2", _pad(b"@(#) SPSS DATA FILE Askurious", 60),
           _i(2, sum(sum(u) for u in units), 0, 0, len(rows)), struct.pack("<d", 100.0),
           now.strftime("%d %b %y").encode("ascii"), now.strftime("%H:%M:%S").encode("ascii"),
           _pad(_cut(file_label, 64), 64), b"\0\0\0"]

    index, positions = 1, []  # dictionary index (1-based, counting continuation records) per variable
    for var, short, widths, sizes, names in zip(variables, shorts, parts, units, more):
        positions.append(index)
        label = _cut(var.get("label") or "", 255)
        gone, count = _missing(var)
        for k, (width, size) in enumerate(zip(widths, sizes)):  # one record per segment, the label on the first
            fmt = _format({**var, "width": width})
            labelled = bool(label) and k == 0
            out.append(_i(2, width if var["kind"] == "str" else 0, int(labelled), count if k == 0 else 0, fmt, fmt))
            out.append(_pad((short if k == 0 else names[k - 1]).encode("ascii"), 8))
            if labelled:
                out.append(_i(len(label)) + _pad(label, (len(label) + 3) // 4 * 4))
            if k == 0:
                out += gone  # user-missing values: a range first (low, high), then discrete ones
            for _ in range(size - 1):
                out.append(_i(2, -1, 0, 0, 0, 0) + b" " * 8)
            index += size

    long_labels = []  # value labels of strings over 8 bytes go into record 7.21
    for var, pos in zip(variables, positions):
        labels = var.get("value_labels") or {}
        if not labels or (var["kind"] == "str" and var["width"] > MAX_STRING):
            continue
        if var["kind"] == "str" and var["width"] > 8:
            long_labels.append(var)
            continue
        rec = [_i(3, len(labels))]
        for value, text in labels.items():
            data = _cut(text, 120)
            raw = _pad(_cut(value, 8), 8) if var["kind"] == "str" else struct.pack("<d", float(value))
            rec.append(raw + bytes([len(data)]) + _pad(data, (len(data) + 8) // 8 * 8 - 1))
        out += rec + [_i(4, 1, pos)]

    out.append(_i(7, 3, 4, 8, 20, 0, 0, -1, 1, 1, 2, 65001))                         # machine integers
    out.append(_i(7, 4, 8, 3) + struct.pack("<3d", SYSMIS, HIGHEST, LOWEST))         # machine floats
    display = []
    for var, widths in zip(variables, parts):
        for width in widths:  # one entry per segment
            shown = (DATE_FORMATS[var["date"]][1] if var.get("date") else 8) if var["kind"] == "num" else min(max(width, 8), 40)
            display += [MEASURES.get(var.get("measure"), 1), shown, 0 if var["kind"] == "str" else 1]
    out.append(_i(7, 11, 4, len(display), *display))
    longs = "\t".join(f"{s}={v['name']}" for s, v in zip(shorts, variables)).encode("utf-8")
    out.append(_i(7, 13, 1, len(longs)) + longs)
    very_long = b"".join(f"{s}={v['width']}".encode("ascii") + b"\0\t"
                         for s, v, widths in zip(shorts, variables, parts) if len(widths) > 1)
    if very_long:
        out.append(_i(7, 14, 1, len(very_long)) + very_long)
    out.append(_i(7, 20, 1, 5) + b"UTF-8")
    long_missing = b""  # missing values of strings over 8 bytes go into record 7.22
    for var in variables:
        codes = _string_missing(var)
        if codes and var["width"] > 8:
            name = var["name"].encode("utf-8")
            long_missing += _i(len(name)) + name + bytes([len(codes)]) + _i(8) + b"".join(codes)
    if long_missing:
        out.append(_i(7, 22, 1, len(long_missing)) + long_missing)
    if long_labels:
        rec = b""
        for var in long_labels:
            name, width = var["name"].encode("utf-8"), var["width"]
            rec += _i(len(name)) + name + _i(width, len(var["value_labels"]))
            for value, text in var["value_labels"].items():
                data = _cut(text, 120)
                rec += _i(width) + _pad(_cut(value, width), width) + _i(len(data)) + data
        out.append(_i(7, 21, 1, len(rec)) + rec)
    out.append(_i(999, 0))

    for row in rows:
        for var, sizes, value in zip(variables, units, row):
            if var["kind"] == "str":
                data = _cut("" if value is None else value, var["width"])
                if len(sizes) == 1:
                    out.append(_pad(data, sizes[0] * 8))
                    continue
                for k, size in enumerate(sizes):  # every segment holds 255 bytes, the last one the rest
                    out.append(_pad(data[k * MAX_STRING:(k + 1) * MAX_STRING], size * 8))
            else:
                try:
                    number = SYSMIS if value in (None, "") else float(value)
                except (TypeError, ValueError, OverflowError):  # also a whole number too large for a float
                    number = SYSMIS
                out.append(struct.pack("<d", number))
    return b"".join(out)
