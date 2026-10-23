"""A small writer for jamovi files (.omv), without dependencies.

Writes the file format of jamovi (own implementation, checked by opening the files in jamovi): a ZIP with
- meta: the manifest (jamovi-Archive-Version 11.0, read by jamovi 1.8 and later),
- metadata.json: row count and one entry per column (data type, measurement level, description, missing values),
- xdata.json: the levels of nominal and ordinal columns as [value, label, import value, pinned],
- data.bin: the columns one after the other, little-endian: doubles (missing = NaN) for decimals, otherwise
  32-bit integers (missing = -2147483648),
- strings.bin: the texts of ID columns, zero-terminated; data.bin holds their byte offsets.

Variables are dicts: name, label, kind ("int" | "num" | "text" | "id"), measure ("nominal" | "ordinal" |
"continuous"), value_labels {whole number: text} (for "text": {code: text}), missing [numbers] (for "text" and "id": [codes]).
"text" is a column of few distinct texts (a nominal factor; with value labels its levels show the labels and keep
the code as import value), "id" one of free texts; jamovi reads at most 511 bytes of each, longer texts are cut.
"""
import io
import json
import math
import struct
import zipfile

INT_MISSING = -2147483648
INT_MAX = 2147483647
MAX_STRING = 511  # texts cut to 511 bytes plus the terminator: longer ones jamovi does not read whole
MEASURES = {"nominal": "Nominal", "ordinal": "Ordinal", "continuous": "Continuous"}


def _cut(text, limit=MAX_STRING):
    """UTF-8 bytes of text without zero bytes, at most `limit` bytes, never cutting a character in half."""
    data = str(text).replace("\0", "").encode("utf-8")
    if len(data) <= limit:
        return data
    return data[:limit].decode("utf-8", "ignore").encode("utf-8")


def finite_number(value):
    """The value as a float, None if it is missing or not a finite number."""
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _whole(number):
    return number is None or (number.is_integer() and -INT_MAX <= number <= INT_MAX)


def write(variables, rows, creator="Askurious"):
    """The .omv file as bytes. `rows` are lists of values in the order of `variables` (None = missing)."""
    fields, xdata, data, strings = [], {}, io.BytesIO(), io.BytesIO()
    for n, var in enumerate(variables):
        values = [row[n] for row in rows]
        kind = var["kind"]
        measure = MEASURES.get(var.get("measure"), "Nominal")
        description = var.get("label") or ""
        labels = var.get("value_labels") or {}
        levels = None
        if kind in ("int", "num"):
            numbers = [finite_number(v) for v in values]
            if kind == "int" and not all(_whole(x) for x in numbers):
                kind = "num"  # a whole number too large for 32 bits
        if kind == "num":
            # jamovi knows decimals as continuous only
            data_type, measure, typ = "Decimal", "Continuous", "number"
            data.write(struct.pack(f"<{len(numbers)}d", *[math.nan if x is None else x for x in numbers]))
        elif kind == "int":
            data_type, typ = "Integer", "integer"
            codes = [INT_MISSING if x is None else int(x) for x in numbers]
            if measure != "Continuous":  # every value in the data needs a level, labelled or not
                present = sorted({int(k) for k in labels} | {c for c in codes if c != INT_MISSING})
                names = {int(k): str(text) for k, text in labels.items()}
                # pinned: a labelled value stays a level even if nobody chose it
                levels = [[c, names.get(c, str(c)), str(c), c in names] for c in present]
            data.write(struct.pack(f"<{len(codes)}i", *codes))
        elif kind == "text":
            data_type, measure, typ = "Text", "Nominal", "integer"
            texts = [None if v in (None, "") else _cut(v).decode("utf-8") for v in values]
            names = {str(k): str(text) for k, text in labels.items()}
            # labelled codes in their order first (pinned: they stay a level even if nobody chose them), then the rest
            order = [*names, *sorted({t for t in texts if t is not None} - set(names))]
            index = {t: i for i, t in enumerate(order)}
            levels = [[i, names.get(t, t), t, t in names] for t, i in index.items()]
            labels = {}  # shown as levels, not again in the description
            data.write(struct.pack(f"<{len(texts)}i", *[INT_MISSING if t is None else index[t] for t in texts]))
        else:
            data_type, measure, typ = "Text", "ID", "string"
            offsets = []
            for v in values:
                text = b"" if v is None else _cut(v)
                if not text:
                    offsets.append(INT_MISSING)
                    continue
                offsets.append(strings.tell())
                strings.write(text + b"\0")
            data.write(struct.pack(f"<{len(offsets)}i", *offsets))
        if labels and not levels:
            # a continuous column has no levels in jamovi: the value labels stay readable in the description
            listed = "; ".join(f"{int(k) if float(k).is_integer() else k} = {text}" for k, text in labels.items())
            description = f"{description} [{listed}]" if description else listed
        if kind in ("int", "num"):
            missing = [f"== {int(m) if float(m).is_integer() else float(m)}" for m in (var.get("missing") or [])]
        else:  # a text column: its codes quoted ("k" = keine Angabe; audit 2026-10-21, C1)
            missing = ["== '" + str(m).replace("'", "") + "'" for m in (var.get("missing") or [])]
        field = {"name": var["name"], "id": n + 1, "columnType": "Data", "dataType": data_type, "measureType": measure,
                 "formula": "", "formulaMessage": "", "parentId": 0, "width": 100, "type": typ,
                 "importName": var["name"], "description": description, "transform": 0, "edits": [],
                 "missingValues": missing, "trimLevels": True}
        fields.append(field)
        if levels:
            xdata[var["name"]] = {"labels": levels}

    metadata = {"dataSet": {"rowCount": len(rows), "columnCount": len(fields), "removedRows": [], "addedRows": [],
                            "fields": fields, "transforms": [], "weights": None}}
    manifest = ("Manifest-Version: 1.0\nData-Archive-Version: 1.0.2\njamovi-Archive-Version: 11.0\n"
                f"Created-By: {creator}\n")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta", manifest)
        z.writestr("metadata.json", json.dumps(metadata))
        z.writestr("xdata.json", json.dumps(xdata))
        z.writestr("data.bin", data.getvalue())
        if strings.tell():
            z.writestr("strings.bin", strings.getvalue())
    return out.getvalue()
