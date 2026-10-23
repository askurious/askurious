"""A small writer for R data files (.rds) holding one data frame, without dependencies.

Follows R's serialization format as R Internals ("Serialization Formats") describes it (own implementation, the
format only, no code of src/main/serialize.c): gzip around
"X\\n" (XDR, big-endian), format version 2 (read by every R since 1.4), then the object: per item a word of
flags (type, "has attributes", "is an object") and its content, attributes as a pairlist behind it.

Variables are dicts: name, label, kind ("int" | "num" | "str" | "date"), value_labels {number: text} or, for a
"str" column of answer codes, {code: text}.
What a column becomes in R:
  "str"   character; with value labels a labelled character vector as haven::read_sav() gives one (class
          haven_labelled, attribute "labels"), still the plain codes without haven
  "date"  Date (values: ISO dates "1987-05-23")
  "int"   integer, "num" double
  with value labels: the numbers as they are, labelled like the columns haven::read_sav() gives (class
          haven_labelled, attribute "labels"), so haven::as_factor(), labelled and sjlabelled know them, and
          without those packages the column still computes as the plain numbers it holds
Every column carries its description as attribute "label" (shown by RStudio's viewer). Missing = NA.
"""
import gzip
import io
import struct
from datetime import date

from app.omv import finite_number

NILVALUE, SYM, LIST, CHAR, INT, REAL, STR, VEC = 254, 1, 2, 9, 13, 14, 16, 19
IS_OBJECT, HAS_ATTR, HAS_TAG = 1 << 8, 1 << 9, 1 << 10
UTF8, ASCII = 1 << 3, 1 << 6          # how a string is encoded, in the "levels" part of its flags
NA_INT = -2147483648
NA_REAL = struct.pack(">Q", 0x7FF00000000007A2)  # R's NA: a NaN with the payload 1954
WRITER, MIN_READER = 0x00040201, 0x00020300      # written "by" R 4.2.1, readable from R 2.3.0
EPOCH = date(1970, 1, 1)


def _int(n):
    return struct.pack(">i", n)


def _char(text):
    if text is None:
        return _int(CHAR) + _int(-1)  # NA_character_
    data = str(text).replace("\0", "").encode("utf-8")
    return _int(CHAR | ((ASCII if data.isascii() else UTF8) << 12)) + _int(len(data)) + data


def _strings(texts, attrs=None):
    return _vector(STR, len(texts), b"".join(_char(t) for t in texts), attrs)


def _ints(numbers, attrs=None):
    return _vector(INT, len(numbers), b"".join(_int(NA_INT if n is None else n) for n in numbers), attrs)


def _reals(numbers, attrs=None):
    return _vector(REAL, len(numbers), b"".join(NA_REAL if n is None else struct.pack(">d", n) for n in numbers), attrs)


def _vector(kind, length, body, attrs=None):
    """A vector with its attributes ([(name, serialized value)]); one named "class" makes it an object."""
    attrs = attrs or []
    flags = kind | (HAS_ATTR if attrs else 0) | (IS_OBJECT if any(name == "class" for name, _ in attrs) else 0)
    return _int(flags) + _int(length) + body + _pairlist(attrs)


def _pairlist(attrs):
    if not attrs:
        return b""
    out = b"".join(_int(LIST | HAS_TAG) + _int(SYM) + _char(name) + value for name, value in attrs)
    return out + _int(NILVALUE)


def _days(value):
    try:
        return float((date.fromisoformat(str(value)[:10]) - EPOCH).days)
    except ValueError:
        return None


def _column(var, values):
    label = [("label", _strings([var["label"]]))] if var.get("label") else []
    kind, labels = var["kind"], var.get("value_labels") or {}
    if kind == "str":
        texts = [None if v in (None, "") else str(v) for v in values]
        if labels:
            named = _strings(list(labels), [("names", _strings(list(labels.values())))])
            return _strings(texts, label + [("labels", named), ("class", _strings(["haven_labelled", "vctrs_vctr", "character"]))])
        return _strings(texts, label)
    if kind == "date":
        return _reals([None if v in (None, "") else _days(v) for v in values], label + [("class", _strings(["Date"]))])
    numbers = [finite_number(v) for v in values]
    if labels:
        codes = sorted(labels)
        named = _reals([float(c) for c in codes], [("names", _strings([labels[c] for c in codes]))])
        return _reals(numbers, label + [("labels", named), ("class", _strings(["haven_labelled", "vctrs_vctr", "double"]))])
    if kind == "int" and all(n is None or abs(n) < 2147483647 for n in numbers):
        return _ints([None if n is None else int(n) for n in numbers], label)
    return _reals(numbers, label)


def write(variables, rows):
    """The .rds file as bytes: a data frame with one column per variable. `rows` are lists of values in the
    order of `variables` (None or "" = missing)."""
    columns = b"".join(_column(var, [row[n] for row in rows]) for n, var in enumerate(variables))
    frame = _vector(VEC, len(variables), columns, [
        ("names", _strings([var["name"] for var in variables])),
        ("class", _strings(["data.frame"])),
        ("row.names", _ints([None, -len(rows)])),  # the compact form of 1..n
    ])
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as z:
        z.write(b"X\n" + _int(2) + _int(WRITER) + _int(MIN_READER) + frame)
    return out.getvalue()
