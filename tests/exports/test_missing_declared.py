"""Codes that count as missing arrive as missing in SPSS, R and jamovi (audit Datenspeicherung + Berechnungen
2026-10-21, C1 and C2): answer codes of letters ("k" = keine Angabe) on text columns, and more than three negative
codes (the KonsortSWD codes -1 ... -6 of the x_ variables) as a range in SPSS. Also the formula guard of the CSV
with leading spaces (C4) and the codebook notes on resp_id, empty whole questions and rounded percentages (C6, C3,
E11)."""
import io
import zipfile
from datetime import datetime

import pytest
from werkzeug.datastructures import MultiDict

from app import export, sav
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add
from tests.exports.test_jamovi import read as read_omv
from tests.exports.test_rds import read as read_rds

pyreadstat = pytest.importorskip("pyreadstat")


def _letters(app, client, codes=("w", "m", "k"), answers=("w", "k", "m", "w")):
    """A single choice `g` with answer codes of letters, the last one counting as missing, and four participations."""
    add(client, "single_choice")                                                 # 2
    form = [("code", "g"), ("question", "Geschlecht?")]
    for n, code in enumerate(codes):
        form += [("option_id", f"o{n}"), ("option_code", code), ("option_label", f"Antwort {code}")]
    form.append(("option_missing", f"o{len(codes) - 1}"))
    client.post("/surveys/1/editor/items/2", data=MultiDict(form))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for code in answers:
            s.responses.append(Response(status=Response.COMPLETED, source="real", started_at=datetime(2026, 9, 2),
                                        completed_at=datetime(2026, 9, 2), answers={"2": code}))
        db.session.commit()


def _texts(column):
    """A string column as read by pyreadstat, missing (NaN) as None."""
    return [v if isinstance(v, str) and v else None for v in column.tolist()]


def _sps(client, **args):
    z = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/spss.zip", query_string=args).data))
    return z.read(next(n for n in z.namelist() if n.endswith(".sps"))).decode("utf-8-sig")


def test_letter_code_counted_as_missing_is_missing_in_every_file(app, client, survey, tmp_path):
    _letters(app, client)
    path = tmp_path / "d.sav"
    path.write_bytes(client.get("/surveys/1/export/data.sav").data)
    _, meta = pyreadstat.read_sav(str(path), user_missing=True)
    assert meta.missing_ranges["g"] == [{"lo": "k", "hi": "k"}]               # pyreadstat lists string codes so
    df, _ = pyreadstat.read_sav(str(path))                                       # as SPSS counts: k is missing
    assert _texts(df["g"]) == ["w", None, "m", "w"]
    assert "MISSING VALUES g ('k')." in _sps(client)
    values, _ = read_rds(client.get("/surveys/1/export/data.rds").data)["g"]
    assert values == ["w", None, "m", "w"]
    fields, _, _ = read_omv(client.get("/surveys/1/export/data.omv").data)
    assert fields["g"]["missingValues"] == ["== 'k'"]


def test_letter_code_on_a_wide_text_column(app, client, survey, tmp_path):
    """A text column wider than 8 bytes keeps its missing codes in record 7.22."""
    _letters(app, client, codes=("weiblich", "maennlich", "k"), answers=("weiblich", "k", "maennlich"))
    path = tmp_path / "d.sav"
    path.write_bytes(client.get("/surveys/1/export/data.sav").data)
    _, meta = pyreadstat.read_sav(str(path), user_missing=True)
    assert meta.missing_ranges["g"] == [{"lo": "k", "hi": "k"}]
    df, _ = pyreadstat.read_sav(str(path))
    assert _texts(df["g"]) == ["weiblich", None, "maennlich"]


def test_more_than_three_negative_codes_become_a_range(app, client, survey, tmp_path):
    """x_age of a survey without an age question is -4 ("nicht inkludiert") everywhere: missing in SPSS, not a mean
    of -4."""
    _letters(app, client)
    path = tmp_path / "s.sav"
    path.write_bytes(client.get("/surveys/1/export/data.sav", query_string={"standard": "1"}).data)
    df, _ = pyreadstat.read_sav(str(path))
    assert df["x_age"].isna().all()
    _, meta = pyreadstat.read_sav(str(path), user_missing=True)
    low, high = meta.missing_ranges["x_age"][0]["lo"], meta.missing_ranges["x_age"][0]["hi"]
    assert low < -1e300 and high == -1
    assert "MISSING VALUES x_age (LOWEST THRU -1)." in _sps(client, standard="1")


def test_spss_missing_rules():
    assert export.spss_missing([9.0], [1.0, 2.0]) == ([9.0], None)
    assert export.spss_missing([-1.0, -2.0, -3.0, -4.0], [1.0, 5.0]) == ([], (None, -1.0))
    # a valid value below the codes: the range starts at the lowest code
    assert export.spss_missing([-1.0, -2.0, -3.0, -4.0], [-10.0, 1.0]) == ([], (-4.0, -1.0))
    # a valid value inside: a range over the rest and one discrete code
    assert export.spss_missing([-1.0, -3.0, -4.0, -5.0], [-2.0, 1.0]) == ([-1.0], (None, -3.0))
    # mixed codes: the first three, and the codebook says so
    assert export.spss_missing([96.0, 97.0, 98.0, 99.0], [1.0]) == ([96.0, 97.0, 98.0], None)
    assert export.spss_short(["96", "97", "98", "99"]) and not export.spss_short(["-1", "-2", "-3", "-4"])
    assert export.spss_short(["keine_angabe"]) and not export.spss_short(["k", "9"])


def test_sav_writer_range_and_one_discrete_value(tmp_path):
    variables = [{"name": "a", "kind": "num", "missing": [-98.0], "missing_range": (-6.0, -1.0)},
                 {"name": "b", "kind": "str", "width": 3, "missing": ["k", "x"]}]
    path = tmp_path / "w.sav"
    path.write_bytes(sav.write(variables, [[-98, "k"], [-3, "abc"], [2, "x"]]))
    df, meta = pyreadstat.read_sav(str(path))
    assert df["a"].isna().tolist() == [True, True, False]
    assert _texts(df["b"]) == [None, "abc", None]


def test_formula_guard_ignores_leading_spaces():
    assert export.safe_text(" =cmd|' /C calc'!A0") == "' =cmd|' /C calc'!A0"
    assert export.safe_text(" +HYPERLINK(1)") == "' +HYPERLINK(1)"
    assert export.safe_text(" -3") == " -3" and export.safe_text("  ") == "  " and export.safe_text("a = b") == "a = b"


def test_codebook_notes(app, client, survey):
    _letters(app, client)
    add(client, "text_input")                                                    # 3
    client.post("/surveys/1/editor/items/3", data={"code": "txt", "question": "Text"})
    with app.app_context():
        book = {row[0]: row for row in export.codebook(db.session.get(Survey, 1))}
        assert "laufende Nummer in diesem Export" in book["resp_id"][4] and "pid verwenden" in book["resp_id"][4]
        assert "„nicht gezeigt“ ebenso leer wie „gesehen, nicht beantwortet“" in book["resp_id"][4]
        assert "Apostroph" in book["txt"][4]
        en = {row[0]: row for row in export.codebook(db.session.get(Survey, 1), lang="en")}
        assert "to join different exports use pid" in en["resp_id"][4] and "apostrophe" in en["txt"][4]
        public = export.codebook_csv(db.session.get(Survey, 1), public=True)  # a public package has no pid
        assert "nicht über resp_id zusammenführen" in public and "pid verwenden" not in public
