import io
import zipfile

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add


def test_spss_zip_with_labels(app, client, survey):
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "gender"), ("question", "Geschlecht?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "weiblich"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "männlich's")]))
    add(client, "likert")                                           # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "TA"), ("points", "5"), ("labeling", "full"), ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Aussage A")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        from datetime import datetime

        for g in ("1", "2"):
            s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2), started_at=datetime(2026, 9, 2),
                                        answers={"2": g, "3": {"s1": 4}}))
        db.session.commit()
    r = client.get("/surveys/1/export/spss.zip")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.data))
    names = sorted(z.namelist())
    assert names[0].endswith(".dat") and names[1].endswith(".sps")
    sps = z.read(names[1]).decode("utf-8-sig")
    assert "GET DATA /TYPE=TXT" in sps and f"/FILE='{names[0]}'" in sps
    assert "    gender F8.0" in sps and "    lang A8" in sps           # integers without decimals, text as string
    assert "/gender 1 'weiblich' 2 'männlich''s'" in sps            # value labels, quotes escaped
    assert "/TA_1 1 'stimme überhaupt nicht zu'" in sps
    assert "(ORDINAL)" in sps and "TA_1" in sps.split("(ORDINAL)")[0].split("VARIABLE LEVEL")[-1]
    csv_text = z.read(names[0]).decode("utf-8-sig")
    assert csv_text.splitlines()[0].startswith("resp_id,")


def test_sav_file_with_labels_levels_and_long_names(app, client, survey, tmp_path):
    pyreadstat = __import__("pytest").importorskip("pyreadstat")
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "gender"), ("question", "Geschlecht?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "weiblich"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "männlich")]))
    add(client, "likert")                                           # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "Technikbereitschaft"), ("points", "5"), ("labeling", "full"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Aussage A")]))
    add(client, "text_input")                                       # 4
    client.post("/surveys/1/editor/items/4", data={"code": "kommentar", "question": "Ihr Kommentar"})
    long_text = "Grüße " * 80                                       # 480 bytes: cut to 255
    with app.app_context():
        s = db.session.get(Survey, 1)
        from datetime import datetime

        for g, t, c in (("1", 4, "gut ☺"), ("2", None, long_text)):
            s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2), started_at=datetime(2026, 9, 2),
                                        answers={"2": g, "3": {"s1": t} if t else {}, "4": c}))
        db.session.commit()
    r = client.get("/surveys/1/export/data.sav")
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith('.sav"')
    path = tmp_path / "d.sav"
    path.write_bytes(r.data)
    df, meta = pyreadstat.read_sav(str(path))
    assert len(df) == 2 and "Technikbereitschaft_1" in df.columns     # long names kept
    assert meta.column_names_to_labels["gender"] == "Geschlecht?"
    assert meta.variable_value_labels["gender"] == {1.0: "weiblich", 2.0: "männlich"}
    assert meta.variable_value_labels["Technikbereitschaft_1"][1.0] == "stimme überhaupt nicht zu"
    assert meta.variable_measure["gender"] == "nominal" and meta.variable_measure["Technikbereitschaft_1"] == "ordinal"
    assert list(df["gender"]) == [1.0, 2.0] and df["Technikbereitschaft_1"].isna().tolist() == [False, True]
    # a text over 255 bytes is kept whole (very long string, audit 2026-10-05 S33)
    assert df["kommentar"][0] == "gut ☺" and df["kommentar"][1].startswith("Grüße") and len(df["kommentar"][1].encode()) > 255
    assert meta.file_encoding.upper().replace("-", "") == "UTF8"


def test_sav_names_are_valid():
    from app import sav

    taken = set()
    assert [sav.valid_name(n, taken) for n in ("1st", "a-b", "AND", "a-b", "x" * 70)] == \
        ["v_1st", "a_b", "v_AND", "a_b_2", "x" * 64]


def test_spss_levels_follow_the_question(app, client, survey):
    add(client, "likert")                                           # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "A"), ("points", "5"), ("level", "metric"), ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "x")]))
    add(client, "likert")                                           # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "B"), ("points", "5"), ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "y")]))
    add(client, "single_choice")                                    # 4
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "edu"), ("question", "Abschluss?"), ("level", "ordinal"), ("option_id", "a"), ("option_code", "1"),
        ("option_label", "Hauptschule"), ("option_id", "b"), ("option_code", "2"), ("option_label", "Abitur")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        from datetime import datetime

        s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2), started_at=datetime(2026, 9, 2),
                                    answers={"2": {"s1": 4}, "3": {"s1": 2}, "4": "2"}))
        db.session.commit()
    z = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/spss.zip").data))
    sps = z.read(sorted(z.namelist())[1]).decode("utf-8-sig")
    level = {line.split("(")[1].rstrip(")."): line for line in sps.splitlines() if line.startswith("VARIABLE LEVEL")}
    assert " A_1 " in level["SCALE"] + " " and " B_1" in level["ORDINAL"] and " edu" in level["ORDINAL"]
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "Messniveau metrisch" in book and "Messniveau ordinal" in book


def test_postal_code_text_field_stays_text(app, client, survey):
    add(client, "text_input")                                       # 2
    client.post("/surveys/1/editor/items/2", data={"code": "plz", "question": "PLZ?", "validation": "plz_de"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        from datetime import datetime

        s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2),
                                    started_at=datetime(2026, 9, 2), answers={"2": "01067"}))
        db.session.commit()
    z = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/spss.zip").data))
    sps = z.read(next(n for n in z.namelist() if n.endswith(".sps"))).decode("utf-8-sig")
    assert "    plz A8" in sps  # digits, but a code: leading zeros must survive


def test_missing_codes_reach_spss(app, client, survey, tmp_path):
    pyreadstat = __import__("pytest").importorskip("pyreadstat")
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "wissen"), ("question", "Wie gut?"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "schlecht"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "gut"),
        ("option_id", "d"), ("option_code", "99"), ("option_label", "weiß nicht"), ("option_missing", "d")]))
    with app.app_context():
        from datetime import datetime

        s = db.session.get(Survey, 1)
        for v in ("1", "2", "99"):
            s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2),
                                        started_at=datetime(2026, 9, 2), answers={"2": v}))
        db.session.commit()
    z = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/spss.zip").data))
    sps = z.read(next(n for n in z.namelist() if n.endswith(".sps"))).decode("utf-8-sig")
    assert "MISSING VALUES wissen (99)." in sps and "99 'weiß nicht'" in sps
    path = tmp_path / "data.sav"
    path.write_bytes(client.get("/surveys/1/export/data.sav").data)
    df, meta = pyreadstat.read_sav(str(path), user_missing=True)
    assert meta.missing_ranges["wissen"] == [{"lo": 99.0, "hi": 99.0}] and list(df["wissen"]) == [1.0, 2.0, 99.0]
    df, _ = pyreadstat.read_sav(str(path))                          # as SPSS analyses it: 99 is missing
    assert df["wissen"].isna().sum() == 1


def test_the_data_file_of_the_spss_zip_does_not_open_in_a_spreadsheet(app, client, survey):
    """Audit 2026-10-18, INJ-2: the SPSS ZIP held its data as <name>.csv with typed answers as they are (SPSS must read
    them unchanged), and a double click opened it in Excel, which runs "=HYPERLINK(…)" as a formula. The file is
    <name>.dat now, read by the syntax under that name; the data export (CSV) keeps its guard."""
    from tests.conftest import add

    add(client, "text")
    r = client.get("/surveys/1/export/spss.zip")
    z = zipfile.ZipFile(io.BytesIO(r.data))
    names = z.namelist()
    assert not any(n.endswith(".csv") for n in names)
    data = next(n for n in names if n.endswith(".dat"))
    sps = z.read(next(n for n in names if n.endswith(".sps"))).decode("utf-8-sig")
    assert f"/FILE='{data}'" in sps and "Datenexport (CSV)" in sps
    page = client.get("/surveys/1/data").data.decode()
    assert "SPSS-Syntax mit Datendatei (.zip)" in page and "nur für SPSS" in page
