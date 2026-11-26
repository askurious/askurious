"""The results as tables for programs (app/results_tables.py; Ralf 2026-11-26): a ZIP of CSV files beside the PDF
report, the core files unrounded from the kept results, the tables of the results page as they stand there, a README
and an index; aggregates only, no free texts."""
import csv
import io
import zipfile
from types import SimpleNamespace

from app import results_tables
from app.extensions import db
from app.models import Item, Response, Survey, new_pid
from tests.conftest import add, fill_consent


def _survey(app, client):
    fill_consent(client)
    add(client, "single_choice")
    add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        choice = db.session.scalar(db.select(Item).filter_by(type="single_choice"))
        text = db.session.scalar(db.select(Item).filter_by(type="text_input"))
        choice.code, text.code = "rad", "warum"
        codes = [o["code"] for o in choice.config["options"]]
        for n in range(20):
            s.responses.append(Response(pid=new_pid(s.code), source="real", is_preview=False, language="de",
                                        status="completed", answers={str(choice.id): codes[0] if n < 8 else codes[1],
                                                                     str(text.id): f"geheim {n}"}))
        db.session.commit()


def _read(z, name, delimiter=","):
    text = z.read(name).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


def test_zip_with_frequencies_readme_and_index(app, client, survey):
    _survey(app, client)
    r = client.get("/surveys/1/export/results-tables.zip")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert r.headers["Content-Disposition"].endswith('.zip"') and "ergebnistabellen" in r.headers["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.data))
    names = z.namelist()
    assert names[:2] == ["README.txt", "tabellen.csv"] and "haeufigkeiten.csv" in names
    rows = [x for x in _read(z, "haeufigkeiten.csv") if x["variable"] == "rad"]
    assert {x["n"] for x in rows} >= {"8", "12"}
    first = next(x for x in rows if x["n"] == "8")
    assert first["anteil"] == "0.4" and first["art"] == "nominal" and first["n_gueltig"] == "20" and first["fehlend"] == "0"
    assert 0.19 < float(first["ci_low"]) < 0.4 < float(first["ci_high"]) < 0.65  # unrounded, a decimal point
    readme = z.read("README.txt").decode("utf-8-sig")
    assert "N = 20 Teilnahmen" in readme and "haeufigkeiten.csv" in readme and "Dezimalpunkt" in readme
    index = _read(z, "tabellen.csv")
    assert {"datei": "haeufigkeiten.csv", "art": "berechnet"}.items() <= next(
        x for x in index if x["datei"] == "haeufigkeiten.csv").items()
    assert all(x["datei"] in names for x in index)
    assert b"geheim" not in b"".join(z.read(n) for n in names)  # no free texts


def test_separator_of_the_export_page_and_button(app, client, survey):
    _survey(app, client)
    z = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/results-tables.zip?sep=semicolon").data))
    assert _read(z, "haeufigkeiten.csv", ";")[0]["frage"] == "rad"
    assert "Semikolon" in z.read("README.txt").decode("utf-8-sig")
    page = client.get("/surveys/1/data").data.decode()
    assert "/surveys/1/export/results-tables.zip" in page and "Tabellen (ZIP)" in page


def _book(data):
    """{sheet name: [[cell values]]} of an .xlsx, numbers as float, read with the standard library only; and whether
    any cell holds a formula."""
    import re
    from xml.etree import ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    out = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
        for i, sheet in enumerate(ET.fromstring(z.read("xl/workbook.xml")).iter(f"{ns}sheet"), 1):
            root = ET.fromstring(z.read(f"xl/worksheets/sheet{i}.xml"))
            assert not list(root.iter(f"{ns}f"))  # never a formula
            rows = []
            for row in root.iter(f"{ns}row"):
                cells = {}
                for c in row.iter(f"{ns}c"):
                    t, v = c.find(f"{ns}is/{ns}t"), c.find(f"{ns}v")
                    cells[re.match(r"[A-Z]+", c.get("r")).group(0)] = (
                        t.text if t is not None else float(v.text) if v is not None else "")
                rows.append([cells.get(chr(65 + n), "") for n in range(max((ord(k) - 64 for k in cells), default=0))])
            out[sheet.get("name")] = rows
    return out


def test_excel_file_with_a_sheet_per_table(app, client, survey):
    """The third button (Ralf, 2026-11-26): the tables of the ZIP as one .xlsx, a sheet per table, written by
    app/xlsx.py; numbers as numbers, unrounded; no free texts."""
    _survey(app, client)
    r = client.get("/surveys/1/export/results-tables.xlsx")
    assert r.status_code == 200 and r.mimetype == results_tables.XLSX_MIME
    assert r.headers["Content-Disposition"].endswith('.xlsx"') and "ergebnistabellen" in r.headers["Content-Disposition"]
    book = _book(r.data)
    names = list(book)
    assert names[:3] == ["README", "tabellen", "haeufigkeiten"]
    head, *rows = book["haeufigkeiten"]
    assert tuple(head) == results_tables.FILES["haeufigkeiten.csv"][0]
    first = next(dict(zip(head, x)) for x in rows if x[head.index("variable")] == "rad" and x[head.index("n")] == 8)
    assert first["anteil"] == 0.4 and first["n_gueltig"] == 20 and first["fehlend"] == 0 and first["art"] == "nominal"
    assert 0.19 < first["ci_low"] < 0.4 < first["ci_high"] < 0.65 and round(first["ci_low"], 3) != first["ci_low"]
    readme = "\n".join(x[0] for x in book["README"] if x)
    assert "N = 20 Teilnahmen" in readme and "haeufigkeiten (" in readme and "ein Blatt je Tabelle" in readme
    assert ".csv" not in readme and "README.txt" not in readme
    index = {x[0]: x for x in book["tabellen"][1:]}
    assert book["tabellen"][0][:3] == ["blatt", "zeilen", "art"]
    assert set(index) == set(names[2:]) and index["haeufigkeiten"][1:3] == [len(rows), "berechnet"]
    assert b"geheim" not in b"".join(zipfile.ZipFile(io.BytesIO(r.data)).read(n) for n in
                                     zipfile.ZipFile(io.BytesIO(r.data)).namelist())
    page = client.get("/surveys/1/data").data.decode()
    assert "/surveys/1/export/results-tables.xlsx" in page and "Tabellen (Excel)" in page


def test_excel_values_and_sheets_of_shown_tables(app, monkeypatch):
    import lxml.html
    from datetime import datetime

    from app import results_pdf

    assert [results_tables._value(v) for v in (None, True, False, 2.0, 1 / 3, float("nan"), float("inf"), 7, "=1+1")] == [
        "", 1, 0, 2, 1 / 3, "", "", 7, "=1+1"]
    assert all(len(results_tables.sheet_name(f)) <= 31 for f in results_tables.FILES)
    root = lxml.html.fromstring("""<main><section id="sem"><h3>Pfade</h3><table><thead><tr><th>Pfad</th><th>β</th></tr></thead>
        <tbody><tr><td>=A → B</td><td>0,41</td></tr></tbody></table></section>
        <section id="eine-sehr-lange-kennung-eines-abschnitts-der-ergebnisseite"><h3>Lang</h3><table><tbody><tr><td>a</td><td>b</td></tr>
        </tbody></table></section></main>""")
    monkeypatch.setattr(results_pdf, "page_tree", lambda survey: root)
    e = {"item": SimpleNamespace(code="alter"), "question": "Alter", "columns": [
        {"type": "num", "code": "alter", "label": "Alter", "n": 10, "mean": 41.25, "sd": float("nan"), "gini": None}]}
    with app.test_request_context():
        body = results_tables.build_xlsx(SimpleNamespace(title="Studie"), "en", data={"items": [e], "n_rows": 3},
                                         pool={"sources": ["real"], "all": False}, computed_at=datetime(2026, 11, 26, 9, 0))
    book = _book(body)
    assert list(book)[:4] == ["README", "tabellen", "kennwerte", "001-sem"] and all(len(n) <= 31 for n in book)
    head, row = book["kennwerte"]
    values = dict(zip(head, row + [""] * len(head)))
    assert values["m"] == 41.25 and values["n"] == 10 and values["sd"] == "" and values["label"] == "Alter"
    assert book["001-sem"] == [["Pfad", "β"], ["=A → B", "0,41"]]            # as shown: text, "=" stays text
    long = list(book)[4]
    assert book[long] == [["", ""], ["a", "b"]] and long.startswith("002-eine-sehr-lange")  # no head: an empty one
    assert book["tabellen"][1:] == [["kennwerte", 1.0, "berechnet", "", ""], ["001-sem", 1.0, "wie angezeigt", "Pfade", "sem"],
                                    [long, 1.0, "wie angezeigt", "Lang",
                                     "eine-sehr-lange-kennung-eines-abschnitts-der-ergebnisseite"]]
    readme = "\n".join(x[0] for x in book["README"] if x)
    assert "Data basis: N = 3 participations" in readme and "Sheets 001-… to 002-… (2 sheets)" in readme


def _entry(**parts):
    return {"item": SimpleNamespace(code="cbc"), "question": "Wahl", **parts}


def test_choice_models_and_segments_as_long_tables():
    level = {"mean": 0.6, "lo": 0.5, "hi": 0.7, "sd": 0.4, "q1": 0.3, "median": 0.55, "q3": 0.9, "best": 0.55}
    hb = {"persons": 40, "sets": 480, "converged": True, "kept": 2000, "settings": {"burn": 1000, "draws": 2000},
          "convergence": {"chains": 4, "rhat_max": 1.012, "ess_min": 412.5}, "rlh": {"mean": 0.61, "chance": 0.25},
          "attributes": [{"code": "wer", "label": "Kommunikator", "importance": 0.6, "importance_sd": 0.1,
                          "importance_q": [0.5, 0.6, 0.7], "imp_lo": 0.55, "imp_hi": 0.65,
                          "levels": [{**level, "label": "Wissenschaftler"},
                                     {**level, "label": "Politiker", "mean": -0.6, "lo": None, "hi": None, "separated": True}]}],
          "price": {"label": "Preis", "mean": -0.05, "lo": -0.06, "hi": -0.04, "sd": 0.02, "importance": 0.4},
          "asc": {"mean": -0.5, "lo": -0.7, "hi": -0.3, "sd": 0.8},
          "interactions": [{"label": "A × B", "a_levels": ["a1", "a2"], "b_levels": ["b1"], "cells": [[0.2], [-0.2]],
                            "lo": [[0.1], [-0.3]], "hi": [[0.3], [-0.1]]}],
          "diffs": {"attributes": [{"code": "wer", "label": "Kommunikator", "levels": [{"label": "Wissenschaftler", "mean": 41.5}]}]}}
    model = {"persons": 40, "sets": 480, "rho2": 0.2134,
             "attributes": [{"code": "wer", "label": "Kommunikator", "importance": 0.7,
                             "levels": [{"label": "Wissenschaftler", "beta": 0.31234567, "se": 0.05}]}]}
    lc = {"result": {"K": 2, "suggested": 2,
                     "solutions": [{"K": 1, "ll": -500.0, "params": 3, "bic": 1010.0, "aic3": 1009.0, "caic": 1013.0,
                                    "entropy": None, "smallest": 1.0, "runs": 5, "found": 5, "converged": True},
                                   {"K": 2, "ll": -450.0, "params": 7, "bic": 930.5, "aic3": 921.0, "caic": 937.5,
                                    "entropy": 0.83, "smallest": 0.4, "runs": 5, "found": 4, "converged": True}],
                     "segments": [{"index": 0, "name": "Preisbewusste", "share": 0.6, "lo": 0.5, "hi": 0.7, "persons": 24,
                                   "sure": 20, "rlh": 0.5},
                                  {"index": 1, "name": "Segment 2", "share": 0.4, "lo": 0.3, "hi": 0.5, "persons": 16,
                                   "sure": 12, "rlh": 0.6}],
                     "attributes": [{"code": "wer", "label": "Kommunikator", "importance": [0.3, 0.8],
                                     "levels": [{"label": "Wissenschaftler", "values": [
                                         {"beta": 0.1, "se": 0.02, "wtp": 3.5, "wtp_lo": 1.0, "wtp_hi": 6.0},
                                         {"beta": 0.9, "se": 0.03, "separated": True}]}]}],
                     "price": {"label": "Preis", "beta": [-0.2, -0.01], "se": [0.01, 0.02], "importance": [0.7, 0.2]},
                     "asc": [{"beta": -1.0, "se": 0.1}, {"beta": 0.5, "se": 0.2}],
                     "covariates": {"reference": "Preisbewusste", "against": ["Segment 2"],
                                    "rows": [{"label": "Alter", "cells": [{"g": 0.4, "se": 0.1, "or": 1.49, "p": 0.0001}]}]}},
          "profile": [{"label": "Geschlecht", "blocks": [
              {"kind": "shares", "code": "sex", "label": "Geschlecht", "test": {"F": 4.2, "df": 1, "df2": 38, "p": 0.047},
               "rows": [{"label": "weiblich", "shares": [0.7, 0.3], "count": 21}]},
              {"kind": "mean", "code": "alter", "label": "Alter", "test": {"small": True},
               "per": [{"mean": 31.0, "sd": 4.0}, {"mean": 52.5, "sd": 6.0}]}]}],
          "hb_means": {"kind": "cbc", "rows": [{"label": "Kommunikator", "levels": [{"label": "Wissenschaftler", "values": [0.2, 0.8]}]}]}}
    ww = {"result": {"persons": 40, "flagged": 1, "confirm": {"mode": "tasks", "splits": 8},
                     "rows": [{"label": "A × B", "simple": {"p": 0.01, "estimable": True}, "hb": {"estimable": False},
                               "p_holm": 0.03, "gain": 0.4, "confirm": {"mean": 0.02}, "status": "confirmed",
                               "status_label": "bestätigt"}]}}
    t = results_tables.core({"items": [_entry(cbc={"model": model, "hb": {"result": hb}, "ww": ww, "lc": lc})]}).rows

    by = {(r["skala"], r["art"], r.get("stufe") or r.get("merkmal")): r for r in t["wahlmodell_hb.csv"]}
    assert by[("roh", "teilnutzen", "Wissenschaftler")]["mittel"] == 0.6 and by[("roh", "teilnutzen", "Politiker")]["begrenzt"]
    assert by[("roh", "wichtigkeit", "Kommunikator")]["median"] == 0.6 and by[("roh", "wichtigkeit", "Preis")]["mittel"] == 0.4
    assert by[("roh", "preis", "Preis")]["ci_low"] == -0.06 and ("roh", "konstante", None) in by
    assert by[("roh", "wechselwirkung", "a2 × b1")]["ci_high"] == -0.1
    assert by[("normiert", "teilnutzen", "Wissenschaftler")]["mittel"] == 41.5
    assert t["wahlmodell_logit.csv"][0]["beta"] == 0.31234567 and t["wahlmodell_logit.csv"][1]["wichtigkeit"] == 0.7
    figures = {(r["modell"], r["kennwert"]): r["wert"] for r in t["wahlmodell_kennwerte.csv"]}
    assert figures[("logit", "pseudo_r2")] == 0.2134 and figures[("hb", "rhat_max")] == 1.012
    assert figures[("hb", "ketten")] == 4 and figures[("hb", "konvergiert")] is True
    assert t["wechselwirkungen.csv"][0] == {"frage": "cbc", "merkmalspaar": "A × B", "p_einfach": 0.01, "p_hb": None,
                                            "p_holm": 0.03, "zuwachs_r2": 0.4, "delta_ll": 0.02, "status": "confirmed",
                                            "ergebnis": "bestätigt"}
    shown = [r for r in t["segmente_guete.csv"] if r["gezeigt"]]
    assert len(t["segmente_guete.csv"]) == 2 and shown[0]["k"] == 2 and shown[0]["bic"] == 930.5 and shown[0]["entropie"] == 0.83
    assert [r["name"] for r in t["segmente.csv"]] == ["Preisbewusste", "Segment 2"]
    kinds = {(r["art"], r["segment"]) for r in t["segmente_parameter.csv"]}
    assert {("teilnutzen", 1), ("wichtigkeit", 2), ("preis", 1), ("konstante", 2), ("hb_mittel", 2)} <= kinds
    part = next(r for r in t["segmente_parameter.csv"] if r["art"] == "teilnutzen" and r["segment"] == 1)
    assert part["zahlungsbereitschaft"] == 3.5 and part["name"] == "Preisbewusste"
    assert t["segmente_kovariaten.csv"][0]["odds_ratio"] == 1.49 and t["segmente_kovariaten.csv"][0]["segment"] == "Segment 2"
    share = next(r for r in t["segmente_profile.csv"] if r["art"] == "anteil" and r["segment"] == 1)
    assert share["wert"] == 0.7 and share["test_p"] == 0.047 and share["antwort"] == "weiblich"
    mean = next(r for r in t["segmente_profile.csv"] if r["art"] == "mittel" and r["segment"] == 2)
    assert mean["wert"] == 52.5 and mean["test_p"] is None  # cells too small for a test: no test values


def test_numbers_scales_and_cells():
    scale = {"k": 3, "n": 50, "alpha": 0.81, "alpha_ci": (0.7, 0.88), "omega": 0.83, "omega_ci": None, "mean": 3.2,
             "sd": 0.7, "mean_ci": (3.0, 3.4),
             "item_stats": [{"code": "s_1", "text": "Aussage", "reverse": True, "n": 50, "mean": 3.0, "sd": 1.0,
                             "rit": 0.55, "alpha_without": 0.79, "loading": 0.7}]}
    e = {"item": SimpleNamespace(code="s"), "question": "Skala", "scale": scale, "columns": [
        {"type": "num", "code": "alter", "label": "Alter", "unit": "Jahre", "n": 10, "mean": 41.25, "sd": 3.5,
         "median": 40, "min": 30, "max": 55, "ci": (38.7, 43.8), "gini": None},
        {"type": "ord", "code": "s_1", "label": "Aussage", "n": 9, "rows": [
            {"value": "1", "label": "nie", "count": 3, "share": 1 / 3, "ci": (0.12, 0.65)}],
         "missing": [{"value": "-99", "label": "weiß nicht", "count": 1}]}],
         "multi": [{"pass": None, "n": 12, "rows": [{"value": "a", "label": "Rad", "count": 6, "n": 12, "share": 0.5,
                                                     "ci": (0.25, 0.75)}]}]}
    t = results_tables.core({"items": [e]}).rows
    assert t["kennwerte.csv"][0]["m"] == 41.25 and t["kennwerte.csv"][0]["einheit"] == "Jahre"
    assert t["skalen.csv"][0]["alpha_ci_high"] == 0.88 and t["skalen.csv"][0]["omega_ci_low"] is None
    assert t["skalen_items.csv"][0]["trennschaerfe"] == 0.55 and t["skalen_items.csv"][0]["umgepolt"] is True
    freq = t["haeufigkeiten.csv"]
    assert [r["art"] for r in freq] == ["ordinal", "ordinal", "mehrfach"] and freq[1]["fehlend"] and freq[1]["n"] == 1
    assert freq[2]["variable"] == "s_a" and freq[2]["n_gueltig"] == 12
    body = results_tables.csv_bytes(("a", "b", "c", "d"), [{"a": 1 / 3, "b": True, "c": None, "d": 2.0}], "semicolon")
    assert body.decode("utf-8-sig").splitlines() == ["a;b;c;d", "0.3333333333333333;1;;2"]


def test_results_zip_with_a_read_token(app, client, survey):
    """The data API serves the core files (E10): the tables "as shown" need the results page of a signed-in person."""
    import re

    _survey(app, client)
    page = client.post("/surveys/1/data-access/new", data={"name": "Laptop"}).get_data(as_text=True)
    token = re.search(r'value="(askdt_[\w-]+)"', page).group(1)
    script = app.test_client()
    assert script.get("/api/v1/surveys/1/results.zip").status_code in (401, 403)
    r = script.get("/api/v1/surveys/1/results.zip?lang=en", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.mimetype == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert "haeufigkeiten.csv" in z.namelist() and not [n for n in z.namelist() if n.startswith("wie_angezeigt/")]
    assert "Data basis: N = 20 participations" in z.read("README.txt").decode("utf-8-sig")
    assert "/api/v1/surveys/1/results.zip" in client.get("/surveys/1/data").get_data(as_text=True)


def test_model_columns_named_under_the_data_pool(app, client, survey, monkeypatch, tmp_path):
    """The export page says which model columns the data set carries and which estimate is out of date (E1, E2):
    export.model_state, sent with the counts."""
    from app import export, maxdiff_hb

    _survey(app, client)
    add(client, "maxdiff")
    stored = tmp_path / "item.json"
    stored.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(maxdiff_hb, "_paths", lambda item, app=None: (str(stored), str(stored) + ".run"))
    with app.app_context():
        s = db.session.get(Survey, 1)
        code = db.session.scalar(db.select(Item).filter_by(type="maxdiff")).code
        monkeypatch.setattr(maxdiff_hb, "current", lambda item: None)
        assert export.model_state(s) == [{"code": code, "model": "hb", "state": "stale"}]
        monkeypatch.setattr(maxdiff_hb, "current", lambda item: {"pool": {"sources": ["dummy"]}})
        assert export.model_state(s, ["real"])[0]["state"] == "other"
        assert export.model_state(s, ["real", "dummy"])[0]["state"] == "current"
    monkeypatch.setattr(maxdiff_hb, "current", lambda item: {"pool": {"sources": ["real"]}})
    counts = client.get("/surveys/1/data-counts").get_json()
    assert counts["models"] == [{"code": code, "model": "hb", "state": "current"}] and counts["total"] == 20
    page = client.get("/surveys/1/data").get_data(as_text=True)
    assert "data-model-line" in page and "_hb_" in page


def test_tables_of_the_results_page_as_they_stand(app, monkeypatch):
    """Every table of the results page goes into the folder "wie_angezeigt": head and rows as text, named after its
    section, listed in tabellen.csv with its heading."""
    import lxml.html

    from app import results_pdf

    root = lxml.html.fromstring("""<main><section id="sem"><h3>Pfade</h3><table><thead><tr><th>Pfad</th><th>β</th></tr></thead>
        <tbody><tr><td>A → B</td><td>0,41</td></tr><tr><td></td><td></td></tr></tbody></table></section>
        <section id="hypothesen"><h3>Leer</h3><table><tbody></tbody></table></section></main>""")
    assert results_tables.shown(root) == [("Pfade", "sem", ["Pfad", "β"], [["A → B", "0,41"]])]
    monkeypatch.setattr(results_pdf, "page_tree", lambda survey: root)
    from datetime import datetime

    with app.test_request_context():
        body = results_tables.build(SimpleNamespace(title="Studie"), "de", data={"items": [], "n_rows": 3},
                                    pool={"sources": ["real"], "all": False}, computed_at=datetime(2026, 11, 26, 9, 0))
    z = zipfile.ZipFile(io.BytesIO(body))
    assert z.namelist() == ["README.txt", "tabellen.csv", "wie_angezeigt/001-sem.csv"]
    assert z.read("wie_angezeigt/001-sem.csv").decode("utf-8-sig").splitlines() == ["Pfad,β", "A → B,\"0,41\""]
    index = _read(z, "tabellen.csv")
    assert index == [{"datei": "wie_angezeigt/001-sem.csv", "zeilen": "1", "art": "wie angezeigt", "titel": "Pfade",
                      "abschnitt": "sem"}]
    assert "wie_angezeigt/ (1 Dateien)" in z.read("README.txt").decode("utf-8-sig")
