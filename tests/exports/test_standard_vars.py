"""The KonsortSWD standard variables x_… in the data export (app/standard_vars.py; concept Demografie-Standards
2026-10-15, decision 5b): derived from the RatSWD standard questions, missing values −1 … −6 after Schneider et al.
(2026), behind a tick on the export page."""
import csv
import io
from datetime import datetime

from app import ratswd, standard_vars
from app.extensions import db
from app.models import Response, Survey


def test_age_in_completed_years():
    assert standard_vars.age_at((1990, 5), datetime(2026, 5, 31)) == 36    # the month of birth: no year less
    assert standard_vars.age_at((1990, 6), datetime(2026, 5, 1)) == 35     # birthday month still to come
    assert standard_vars.age_at((1990, 4), datetime(2026, 5, 1)) == 36


def _setup(app, client, parts=None):
    client.post("/surveys/1/editor/demographics", data={"part": parts or ratswd.PARTS})
    with app.app_context():
        s = db.session.get(Survey, 1)
        return {i.code: str(i.id) for b in s.blocks for i in b.items}


def _add(app, answers, done=datetime(2026, 10, 15, 12, 0)):
    with app.app_context():
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, started_at=done,
                                completed_at=done, answers=answers))
        db.session.commit()


def _rows(client, **args):
    text = client.get("/surveys/1/export/data.csv", query_string=args).data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def test_values_with_missing_codes(app, client, survey):
    ids = _setup(app, client)
    _add(app, {ids["ALT_1"]: "1990-11", ids["GES_1"]: "2", ids["ERW_1"]: "1", ids["STA_1"]: "-1", ids["GEB_1"]: "2",
               ids["GEB_3"]: "-2", ids["HHG_1"]: {"v": "2", "t": {"2": "4"}}, ids["FAM_1"]: "2", ids["HNE_3"]: "1"})
    _add(app, {ids["ALT_1"]: "2000-01", ids["GES_1"]: "3", ids["ERW_1"]: "7", ids["HHG_1"]: {"v": "2", "t": {"2": "viele"}},
               ids["FAM_1"]: "-1"})
    plain = _rows(client)
    assert not any(c.startswith("x_") for c in plain[0])                       # only on request
    first, second = _rows(client, standard="1")
    assert list(first)[-len(standard_vars.NAMES):] == standard_vars.NAMES     # at the end, all of them
    assert {k: first[k] for k in standard_vars.NAMES} == {
        "x_sex": "2", "x_doby": "1990", "x_age": "35", "x_citsc": "-2",           # RatSWD −1 "weiß nicht" -> −2
        "x_bornsc": "0", "x_mbornsc": "-1",                                       # RatSWD −2 "keine Angabe" -> −1
        "x_fbornsc": "-1",                                                        # shown, not answered
        "x_hhsize": "4", "x_marital": "200", "x_mainstatus": "110"}
    assert {k: second[k] for k in ("x_sex", "x_age", "x_hhsize", "x_marital", "x_mainstatus")} == {
        "x_sex": "3", "x_age": "26", "x_hhsize": "-6", "x_marital": "-2", "x_mainstatus": "200"}


def test_not_shown_and_not_included(app, client, survey):
    # ERW_2 is no variable; BER_1 neither: but x_mainstatus without ERW_1 is "nicht inkludiert", and a question left
    # out by its condition is "trifft nicht zu"
    ids = _setup(app, client, ["ges", "geb35"])
    _add(app, {ids["GES_1"]: "1"})
    row, = _rows(client, standard="1")
    assert (row["x_sex"], row["x_mainstatus"], row["x_doby"], row["x_age"], row["x_mbornsc"]) == ("1", "-4", "-4", "-4", "-1")
    with app.app_context():
        from app.models import Item

        geb5 = db.session.get(Item, int(ids["GEB_5"]))
        geb5.config = {**geb5.config, "when": {"item": int(ids["GES_1"]), "op": "eq", "value": "2"}}
        db.session.commit()
    row, = _rows(client, standard="1")
    assert row["x_fbornsc"] == "-3"


def test_gender_and_age_constructs_are_read_too(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gender"})
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:age"})
    _add(app, {"2": "1", "3": "44"})                                           # Spiel et al.: 1 = weiblich
    row, = _rows(client, standard="1")
    assert (row["x_sex"], row["x_age"], row["x_doby"]) == ("2", "44", "-4")


def test_codebook_page_and_spss(app, client, survey):
    assert 'name="standard"' not in client.get("/surveys/1/data").data.decode()   # nothing to derive from
    _setup(app, client, ["ges", "alt", "fam1"])
    page = client.get("/surveys/1/data").data.decode()
    assert 'name="standard"' in page and "KonsortSWD-Zielvariablen anhängen" in page and "Ohne x_casmin" in page
    book = client.get("/surveys/1/export/codebook.csv", query_string={"standard": "1"}).data.decode("utf-8-sig")
    rows = {r["variable"]: r for r in csv.DictReader(io.StringIO(book))}
    assert rows["x_sex"]["values"].startswith("1 = männlich; 2 = weiblich; 3 = divers; 4 = kein Eintrag")
    assert "-1 = Keine Angabe; -2 = Weiß nicht; -3 = Trifft nicht zu; -4 = Nicht inkludiert" in rows["x_sex"]["values"]
    assert "abgeleitet aus GES_1" in rows["x_sex"]["notes"] and "Schneider et al. (2026)" in rows["x_sex"]["notes"]
    assert "Monat der Teilnahme" in rows["x_age"]["notes"] and "nicht erhoben" in rows["x_citsc"]["notes"]
    assert "x_casmin" not in rows
    assert "x_sex" not in client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    _add(app, {})
    r = client.get("/surveys/1/export/data.sav", query_string={"standard": "1"})
    assert r.status_code == 200 and b"x_mainstatus" in r.data
