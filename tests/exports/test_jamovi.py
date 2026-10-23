import io
import json
import math
import struct
import zipfile
from datetime import datetime

from werkzeug.datastructures import MultiDict

from app import omv
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add


def read(body):
    """The .omv file read back after the format description of app/omv.py: fields, levels and the values per column."""
    z = zipfile.ZipFile(io.BytesIO(body))
    assert "jamovi-Archive-Version: 11.0" in z.read("meta").decode()
    meta = json.loads(z.read("metadata.json"))["dataSet"]
    levels = {name: x["labels"] for name, x in json.loads(z.read("xdata.json")).items()}
    data, n = io.BytesIO(z.read("data.bin")), meta["rowCount"]
    strings = z.read("strings.bin") if "strings.bin" in z.namelist() else b""
    fields, values = {}, {}
    for f in meta["fields"]:
        fields[f["name"]] = f
        if f["dataType"] == "Decimal":
            values[f["name"]] = [None if math.isnan(x) else x for x in struct.unpack(f"<{n}d", data.read(8 * n))]
            continue
        raw = [None if x == omv.INT_MISSING else x for x in struct.unpack(f"<{n}i", data.read(4 * n))]
        if f["measureType"] == "ID" and f["dataType"] == "Text":
            raw = [None if x is None else strings[x:strings.index(b"\0", x)].decode() for x in raw]
        elif f["dataType"] == "Text":
            raw = [None if x is None else levels[f["name"]][x][1] for x in raw]
        values[f["name"]] = raw
    assert data.read() == b"" and meta["columnCount"] == len(fields)
    return fields, levels, values


def test_omv_file_with_levels_descriptions_and_texts(app, client, survey):
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "gender"), ("question", "Geschlecht?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "weiblich"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "männlich"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "divers")]))
    add(client, "likert")                                           # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "A"), ("points", "5"), ("labeling", "full"), ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Aussage A")]))
    add(client, "likert")                                           # 4
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "B"), ("points", "5"), ("labeling", "full"), ("level", "metric"), ("stmt_id", "s1"), ("stmt_code", ""),
        ("stmt_text", "Aussage B")]))
    add(client, "text_input")                                       # 5
    client.post("/surveys/1/editor/items/5", data={"code": "kommentar", "question": "Ihr Kommentar"})
    long_text = "Grüße " * 200                                      # 1400 bytes: cut to 511
    with app.app_context():
        s = db.session.get(Survey, 1)
        for g, a, c in (("1", 4, "gut ☺"), ("2", None, long_text)):
            s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2), started_at=datetime(2026, 9, 2),
                                        answers={"2": g, "3": {"s1": a} if a else {}, "4": {"s1": 3}, "5": c}))
        db.session.commit()
    html = client.get("/surveys/1/data").data.decode()
    assert "jamovi (.omv)" in html and 'export/data.omv"' in html and "SPSS und JASP (.sav)" in html and 'export/data.sav"' in html
    r = client.get("/surveys/1/export/data.omv")
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith('.omv"')
    fields, levels, values = read(r.data)
    assert (fields["gender"]["dataType"], fields["gender"]["measureType"]) == ("Integer", "Nominal")
    assert fields["gender"]["description"] == "Geschlecht?" and values["gender"] == [1, 2]
    # all labelled values are levels and pinned, also the one nobody chose
    assert levels["gender"] == [[1, "weiblich", "1", True], [2, "männlich", "2", True], [3, "divers", "3", True]]
    assert fields["A_1"]["measureType"] == "Ordinal" and values["A_1"] == [4, None]
    assert levels["A_1"][0][:2] == [1, "stimme überhaupt nicht zu"] and len(levels["A_1"]) == 5
    # a metric question is continuous; jamovi has no levels there, the labels stay in the description
    assert fields["B_1"]["measureType"] == "Continuous" and "B_1" not in levels
    assert "1 = stimme überhaupt nicht zu" in fields["B_1"]["description"]
    assert (fields["kommentar"]["dataType"], fields["kommentar"]["measureType"]) == ("Text", "ID")
    assert values["kommentar"][0] == "gut ☺" and values["kommentar"][1].startswith("Grüße")
    assert len(values["kommentar"][1].encode()) <= omv.MAX_STRING
    assert fields["lang"]["dataType"] == "Text" and fields["lang"]["measureType"] == "Nominal"   # few texts: a factor
    assert values["lang"] == ["de", "de"]
    assert [f["id"] for f in fields.values()] == list(range(1, len(fields) + 1))


def test_omv_writer_types_and_missing_values():
    variables = [
        {"name": "n", "label": "", "kind": "int", "measure": "nominal", "value_labels": {1: "ja", -9: "k. A."}, "missing": [-9.0]},
        {"name": "big", "label": "", "kind": "int", "measure": "continuous"},
        {"name": "x", "label": "Wert", "kind": "num", "measure": "ordinal"},
        {"name": "t", "label": "", "kind": "text", "measure": "nominal"},
        {"name": "free", "label": "", "kind": "id", "measure": "nominal"},
    ]
    rows = [[1, 5_000_000_000, 1.5, "b", "a\0b"], [-9, None, "", "a", ""], [7, 2, "x", None, None]]
    fields, levels, values = read(omv.write(variables, rows))
    assert fields["n"]["missingValues"] == ["== -9"]
    assert levels["n"] == [[-9, "k. A.", "-9", True], [1, "ja", "1", True], [7, "7", "7", False]]   # unlabelled value: a level too
    assert fields["big"]["dataType"] == "Decimal" and values["big"] == [5e9, None, 2.0]             # too large for 32 bits
    assert fields["x"]["measureType"] == "Continuous" and values["x"] == [1.5, None, None]         # decimals: continuous only
    assert values["t"] == ["b", "a", None] and [lv[1] for lv in levels["t"]] == ["a", "b"]
    assert values["free"] == ["ab", None, None]
    empty = zipfile.ZipFile(io.BytesIO(omv.write(variables, [])))
    assert "strings.bin" not in empty.namelist() and empty.read("data.bin") == b""


def test_viewer_cannot_download_omv(app, client, survey):
    from tests.accounts.test_roles import _join, _login_other

    _join(app, "viewer")
    _login_other(app, client)
    assert client.get("/surveys/1/export/data.omv").status_code == 302   # refused with a message, like the other data files
    html = client.get("/surveys/1/data", follow_redirects=True).data.decode()
    assert 'export/data.omv"' not in html


def test_more_than_three_missing_codes_and_number_formats(app, client, survey):
    """Mathe-Audit X-7 and X-6: jamovi takes every code that counts as missing (SPSS only three); SPSS shows numbers
    with the decimals they have (up to four) and as wide as the longest (F8.2 cut 84.5678 and 123456.5000)."""
    from app import sav

    add(client, "single_choice")                                                 # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "q"), ("question", "Frage"), ("option_id", "a"), ("option_code", "1"), ("option_label", "ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "nein"),
        ("option_id", "c"), ("option_code", "97"), ("option_label", "weiß nicht"),
        ("option_id", "d"), ("option_code", "98"), ("option_label", "keine Angabe"),
        ("option_id", "e"), ("option_code", "99"), ("option_label", "möchte nicht"),
        ("option_id", "f"), ("option_code", "96"), ("option_label", "trifft nicht zu"),
        ("option_missing", "c"), ("option_missing", "d"), ("option_missing", "e"), ("option_missing", "f")]))
    add(client, "text_input")                                                    # 3
    client.post("/surveys/1/editor/items/3", data={"code": "preis", "question": "Preis?", "validation": "decimal"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        for code, price in (("1", "84.5678"), ("96", "123456.5")):
            s.responses.append(Response(status=Response.COMPLETED, source="real", completed_at=datetime(2026, 9, 1),
                                        answers={"2": code, "3": price}))
        db.session.commit()
    fields, _, _ = read(client.get("/surveys/1/export/data.omv").data)
    assert sorted(fields["q"]["missingValues"]) == ["== 96", "== 97", "== 98", "== 99"]
    sps = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/spss.zip").data))
    syntax = sps.read(next(n for n in sps.namelist() if n.endswith(".sps"))).decode("utf-8-sig")
    assert "    preis F11.4" in syntax and "    q F8.0" in syntax
    assert sav._format({"kind": "num", "decimals": 4, "print_width": 11}) == (5 << 16) | (11 << 8) | 4
