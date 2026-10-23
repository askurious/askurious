"""R export (.rds): app/rds.py writes a data frame with descriptions and value labels."""
import gzip
import io
import math
import struct
from datetime import datetime

from werkzeug.datastructures import MultiDict

from app import rds
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add


def read(body):
    """The file as R reads it (unserialize, XDR version 2): {column: (values, attributes)} in column order."""
    data = io.BytesIO(gzip.decompress(body))
    assert data.read(2) == b"X\n" and struct.unpack(">3i", data.read(12))[0] == 2

    def word():
        return struct.unpack(">i", data.read(4))[0]

    def item():
        flags = word()
        kind = flags & 0xFF
        if kind == rds.NILVALUE:
            return None
        if kind == rds.CHAR:
            n = word()
            return None if n == -1 else data.read(n).decode("utf-8")
        if kind == rds.SYM:
            return item()
        if kind == rds.LIST:
            out = {}
            while kind == rds.LIST:
                assert flags & rds.HAS_TAG
                name = item()
                out[name] = item()
                flags = word()
                kind = flags & 0xFF
            assert kind == rds.NILVALUE
            return out
        n = word()
        if kind == rds.INT:
            values = [None if v == rds.NA_INT else v for v in struct.unpack(f">{n}i", data.read(4 * n))]
        elif kind == rds.REAL:
            raw = [data.read(8) for _ in range(n)]
            values = [None if r == rds.NA_REAL else struct.unpack(">d", r)[0] for r in raw]
            assert not any(v is not None and math.isnan(v) for v in values)
        else:
            assert kind in (rds.STR, rds.VEC)
            values = [item() for _ in range(n)]
        attrs = item() if flags & rds.HAS_ATTR else {}
        assert bool(flags & rds.IS_OBJECT) == ("class" in attrs)
        return values, attrs

    columns, attrs = item()
    assert data.read() == b"" and attrs["class"][0] == ["data.frame"]
    assert attrs["row.names"][0] == [None, -len(columns[0][0])] if columns else True
    return dict(zip(attrs["names"][0], columns))


def test_rds_writer_types_labels_and_missing_values():
    variables = [
        {"name": "n", "label": "Antwort", "kind": "int", "value_labels": {1.0: "ja", 2.0: "nein"}},
        {"name": "count", "label": "", "kind": "int"},
        {"name": "big", "label": "", "kind": "int"},
        {"name": "x", "label": "Wert", "kind": "num"},
        {"name": "t", "label": "Text", "kind": "str"},
        {"name": "d", "label": "", "kind": "date"},
    ]
    rows = [[1, 3, 5_000_000_000, 1.5, "Grüße\0 ☺", "1970-01-02"], [None, None, None, "", "", None], [2, 7, 2, "x", "plain", "kein Datum"]]
    cols = read(rds.write(variables, rows))
    assert list(cols) == ["n", "count", "big", "x", "t", "d"]
    values, attrs = cols["n"]
    assert values == [1.0, None, 2.0] and attrs["label"][0] == ["Antwort"]
    assert attrs["labels"][0] == [1.0, 2.0] and attrs["labels"][1]["names"][0] == ["ja", "nein"]
    assert attrs["class"][0] == ["haven_labelled", "vctrs_vctr", "double"]
    assert cols["count"] == ([3, None, 7], {})                     # whole numbers: integer, no attribute without a label
    assert cols["big"][0] == [5e9, None, 2.0]                      # too large for R's integers: double
    assert cols["x"][0] == [1.5, None, None]
    assert cols["t"][0] == ["Grüße ☺", None, "plain"]
    assert cols["d"][0] == [1.0, None, None] and cols["d"][1]["class"][0] == ["Date"]
    assert read(rds.write(variables, []))["n"][0] == []


def test_rds_export_of_a_survey(app, client, survey):
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "gender"), ("question", "Geschlecht?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "weiblich"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "männlich")]))
    add(client, "likert")                                           # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "A"), ("points", "5"), ("labeling", "full"), ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Aussage A")]))
    add(client, "text_input")                                       # 4
    client.post("/surveys/1/editor/items/4", data={"code": "kommentar", "question": "Ihr Kommentar"})
    long_text = "Grüße " * 200
    with app.app_context():
        s = db.session.get(Survey, 1)
        for g, a, c in (("1", 4, "gut ☺"), ("2", None, long_text)):
            s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2), started_at=datetime(2026, 9, 2),
                                        answers={"2": g, "3": {"s1": a} if a else {}, "4": c}))
        db.session.commit()
    html = client.get("/surveys/1/data").data.decode()
    assert "R (.rds)" in html and 'export/data.rds"' in html
    r = client.get("/surveys/1/export/data.rds")
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith('.rds"')
    cols = read(r.data)
    values, attrs = cols["gender"]
    assert values == [1.0, 2.0] and attrs["label"][0] == ["Geschlecht?"]
    assert attrs["labels"][1]["names"][0] == ["weiblich", "männlich"]
    values, attrs = cols["A_1"]
    assert values == [4.0, None] and len(attrs["labels"][0]) == 5
    assert cols["kommentar"][0][0] == "gut ☺" and len(cols["kommentar"][0][1]) > 1000   # texts stay whole
    assert cols["lang"][0] == ["de", "de"]


def test_viewer_cannot_download_rds(app, client, survey):
    from tests.accounts.test_roles import _join, _login_other

    _join(app, "viewer")
    _login_other(app, client)
    assert client.get("/surveys/1/export/data.rds").status_code == 302
    assert 'export/data.rds"' not in client.get("/surveys/1/data", follow_redirects=True).data.decode()


def test_every_missing_code_is_na_in_r(app, client, survey, monkeypatch):
    """SPSS declares three missing codes per variable; R gets all of them as NA."""
    from app import export

    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict(
        [("code", "q"), ("question", "Frage?")]
        + [x for n in range(1, 7) for x in (("option_id", f"o{n}"), ("option_code", str(n)), ("option_label", f"Antwort {n}"))]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for code in ("1", "3", "4", "5", "6"):
            s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2), started_at=datetime(2026, 9, 2),
                                        answers={"2": code}))
        db.session.commit()
        kind = type(s.blocks[1].items[0].kind)
        monkeypatch.setattr(kind, "missing_codes", lambda self, item, column: ["3", "4", "5", "6"])
        values, attrs = read(export.r_rds(s))["q"]
    assert values == [1.0, None, None, None, None]
    assert attrs["labels"][0] == [1.0, 2.0]
