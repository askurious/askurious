"""Data audit 2026-10-05, package J: SPSS, R and jamovi files and the codebook (E-3, E-4, E-7, E-9, E-11, E-13,
S27, S28, S30/T-27, S31, S32, S33)."""
import csv
import io
import json
import zipfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pyreadstat
from werkzeug.datastructures import MultiDict

from app import design, export, sav
from app.extensions import db
from app.models import ContactEntry, Item, Response, SplitSession, Survey
from tests.conftest import CONSENT_OK, add, fill_consent, split_node

E = "/surveys/1/editor"
LONG = "Ä" * 140 + " Ende"                       # 285 bytes in UTF-8
LONG_CODE = "b" * 63                             # a statement column "<code>_1" has 65 characters


def _edges(app, client):
    """Items 2 free text, 3 letter codes, 4 named TO, 5 likert with a long code, 6 number field with only a maximum;
    three completed participations."""
    add(client, "text_input")
    add(client, "single_choice")
    add(client, "single_choice")
    add(client, "likert")
    add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        it = {i.id: i for b in s.blocks for i in b.items}
        it[2].code = "frei"
        it[3].code = "geschl"
        it[3].config = {**it[3].config, "options": [{"id": "a", "code": "w"}, {"id": "b", "code": "m"}, {"id": "c", "code": "d"}]}
        it[3].texts = {"de": {"question": "Geschlecht", "options": {"a": "weiblich", "b": "männlich", "c": "divers"}}}
        it[4].code = "TO"
        it[4].config = {**it[4].config, "options": [{"id": "a", "code": "1"}, {"id": "b", "code": "2"}]}
        it[5].code = LONG_CODE
        it[6].code = "zahl"
        it[6].config = {**it[6].config, "validation": "integer", "min": None, "max": 100.0}
        db.session.commit()
        t0 = datetime(2026, 10, 1, 10, 0)
        sid = it[5].config["statements"][0]["id"]
        for k, (txt, g) in enumerate([(LONG, "w"), ("Zeile 1\r\nZeile 2\nZeile 3", "d"), ("ok", "m")]):
            db.session.add(Response(survey_id=1, source="real", status="completed", pid=f"1-p{k}", language="de",
                                    answers={"1": True, "2": txt, "3": g, "4": "1", "5": {sid: 3}, "6": 7},
                                    started_at=t0, completed_at=t0 + timedelta(seconds=300 + k)))
        db.session.commit()


def _sav(body, tmp_path):
    path = tmp_path / "d.sav"
    path.write_bytes(body)
    return pyreadstat.read_sav(str(path))


def _sps(survey):
    z = zipfile.ZipFile(io.BytesIO(export.spss_zip(survey)))
    names = sorted(z.namelist())
    return z.read(names[0]).decode("utf-8-sig"), z.read(names[1]).decode("utf-8-sig")


def test_spss_names_for_reserved_words_and_long_names_agree_with_the_codebook(app, client, survey, tmp_path):
    """E-3: TO and a name over 64 characters get the same SPSS name in the .sps (and its CSV) as in the .sav, the
    codebook names it; CSV, R and jamovi keep the name of the questionnaire."""
    _edges(app, client)
    long_col = f"{LONG_CODE}_1"
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        names = sav.spss_names([c for c, _ in export.columns(s)])
        assert names["TO"] == "v_TO" and len(names[long_col]) == 64 and names["frei"] == "frei"
        data, syntax = _sps(s)
        header = data.splitlines()[0].split(",")
        assert "v_TO" in header and "TO" not in header and names[long_col] in header and long_col not in header
        assert "    v_TO F8.0" in syntax and "\n    TO " not in syntax and "/TO " not in syntax
        assert "*   TO -> v_TO." in syntax and f"*   {long_col} -> {names[long_col]}." in syntax
        assert f"    {names[long_col]} F8.0" in syntax
        df, meta = _sav(export.spss_sav(s), tmp_path)
        assert "v_TO" in df.columns and names[long_col] in df.columns
        book = {r[0]: r for r in csv.reader(io.StringIO(export.codebook_csv(s).lstrip("﻿")))}
        assert "in SPSS (.sav, .sps) als „v_TO“ (SPSS-Schlüsselwort)" in book["TO"][4]
        assert f"als „{names[long_col]}“ (SPSS erlaubt höchstens 64 Zeichen)" in book[long_col][4]
        assert "in SPSS" not in book["frei"][4]
        assert "TO" in export.to_csv(s).splitlines()[0].split(",")  # the data export keeps the name
        found = [f.message for f in design.check(s) if f.severity == design.WARNING and "SPSS-Export" in f.message]
        assert found and "„TO“ → „v_TO“" in found[0]
        assert not [f for f in design.check(s) if f.severity == design.ERROR and "SPSS" in f.message]


def test_editor_refuses_a_new_spss_keyword_but_keeps_an_old_one(app, client, survey):
    """E-3: a new variable name BY is refused; a question that is already called TO keeps its name when saved."""
    add(client, "text_input")                                                   # 2
    client.post(E + "/items/2", data={"code": "BY", "question": "Frage"})
    with app.app_context():
        assert db.session.get(Item, 2).code != "BY"
        db.session.get(Item, 2).code = "TO"
        db.session.commit()
    client.post(E + "/items/2", data={"code": "TO", "question": "Frage neu"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.code == "TO" and item.text("question") == "Frage neu"


def test_spss_csv_has_one_line_per_case(app, client, survey):
    """E-4: GET DATA /DELCASE=LINE reads a case per line: line breaks in typed texts become spaces there."""
    _edges(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        data, syntax = _sps(s)
        assert len(data.strip().split("\r\n")) == 4 and "Zeile 1 Zeile 2 Zeile 3" in data
        assert "\n" not in data.replace("\r\n", "")
        # the SPSS data file is a .dat since the audit 2026-10-18 (no .csv a spreadsheet opens with formulas)
        assert "Zeilenumbrüche in getippten Texten stehen in dieser Datei als Leerzeichen" in syntax
        assert ".dat'" in syntax and ".csv" not in syntax
        assert "Zeile 1\r\nZeile 2" in export.to_csv(s)                         # the data export keeps them


def test_long_texts_and_letter_codes_in_sav_rds_omv(app, client, survey, tmp_path):
    """S33: the .sav keeps a text over 255 bytes whole, jamovi cuts at 511 bytes and says so (label and codebook).
    S31: answer codes of letters get value labels in .sav, .sps, .rds and .omv."""
    from tests.exports.test_jamovi import read as read_omv
    from tests.exports.test_rds import read as read_rds

    _edges(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        df, meta = _sav(export.spss_sav(s), tmp_path)
        assert df["frei"][0] == LONG
        assert meta.variable_value_labels["geschl"] == {"w": "weiblich", "m": "männlich", "d": "divers"}
        assert list(df["geschl"]) == ["w", "d", "m"]
        _, syntax = _sps(s)
        assert "/geschl 'w' 'weiblich' 'm' 'männlich' 'd' 'divers'" in syntax
        values, attrs = read_rds(export.r_rds(s))["geschl"]
        assert values == ["w", "d", "m"] and attrs["class"][0] == ["haven_labelled", "vctrs_vctr", "character"]
        assert attrs["labels"][0] == ["w", "m", "d"] and attrs["labels"][1]["names"][0] == ["weiblich", "männlich", "divers"]
        fields, _, omv_values = read_omv(export.jamovi_omv(s))
        assert omv_values["geschl"] == ["weiblich", "divers", "männlich"] and fields["geschl"]["measureType"] == "Nominal"
        book = {r[0]: r for r in export.codebook(s)}
        assert export.OMV_TEXT_NOTE in book["frei"][4]
        # a text over jamovi's limit: cut there, with a note in the description
        r = db.session.scalar(db.select(Response).filter_by(pid="1-p2"))
        r.answers = {**r.answers, "2": "x" * 600}
        db.session.commit()
        fields, _, omv_values = read_omv(export.jamovi_omv(s))
        assert len(omv_values["frei"][2]) == 511 and "gekürzt (Grenze von jamovi)" in fields["frei"]["description"]


def test_sav_writer_very_long_strings_and_string_labels(tmp_path):
    """S33/S31 in the writer itself: segments of a very long string, labels on short and long text codes."""
    text = "Ü" * 2000 + "!"
    variables = [{"name": "t", "label": "Text", "kind": "str", "width": len(text.encode())},
                 {"name": "k", "label": "Kurz", "kind": "str", "width": 1, "value_labels": {"a": "ja"}},
                 {"name": "lang", "label": "Lang", "kind": "str", "width": 12, "value_labels": {"abcdefghijkl": "lang"}},
                 {"name": "n", "label": "Zahl", "kind": "num", "value_labels": {1: "eins"}}]
    df, meta = _sav(sav.write(variables, [[text, "a", "abcdefghijkl", 1], [None, None, None, None]]), tmp_path)
    assert list(df.columns) == ["t", "k", "lang", "n"] and df["t"][0] == text and df["t"][1] == ""
    assert meta.variable_value_labels == {"k": {"a": "ja"}, "lang": {"abcdefghijkl": "lang"}, "n": {1.0: "eins"}}
    assert meta.column_names_to_labels["t"] == "Text"


def test_codebook_limits_loop_values_and_map_modes(app, client, survey):
    """S32: a number field with only a maximum ("bis 100", not "None bis 100.0"); ";" in a loop table does not
    split the entries; a map's follow-up question names its mode once."""
    _edges(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        book = {r[0]: r for r in export.codebook(s)}
        assert book["zahl"][3] == "bis 100" and "None" not in book["zahl"][3]
    from tests.analysis.test_vignettes import _table
    from app import flow

    nid = _table(app, client, crossing=False)
    with app.app_context():
        import copy

        s = db.session.get(Survey, 1)
        tree = copy.deepcopy(s.flow)
        node = flow.find(tree, nid)[0]
        node["rows"][0]["values"]["c3"] = {"de": "Bild; mit Semikolon"}
        s.flow = tree
        db.session.commit()
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        row = next(r for r in export.codebook(s) if r[0] == "tab_order")
        assert row[3].count(";") == len(node["rows"]) - 1 and "Bild, mit Semikolon" in row[3]


def test_map_follow_up_mode_is_not_named_twice(app, client, survey):
    from tests.items.test_map_marker import setup

    setup(client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        notes = [r[4] for r in export.codebook(s) if r[0].startswith("site_1_")]
        assert notes and not any("freiwillig; freiwillig" in n or "Pflicht; Pflicht" in n for n in notes)
        assert all("Marker setzen:" in n for n in notes)


def test_codebook_explains_minus_77_of_a_carried_statement(app, client, survey):
    """E-7: a Likert statement carried from an earlier question (shown to those who chose it) has -77 in the
    export; the codebook says what it means."""
    add(client, "multiple_choice")                                              # 2
    add(client, "likert")                                                       # 3
    with app.app_context():
        source, matrix = db.session.get(Item, 2), db.session.get(Item, 3)
        source.code = "geraete"
        matrix.code = "zufr"
        matrix.config = {**matrix.config, "carry": {"item": 2, "which": "chosen"}, "statements": [
            {"id": "c1", "code": "zufr_handy", "reverse": False, "src": {"item": 2, "code": "1", "which": "chosen"}},
            {"id": "s2", "code": "zufr_all", "reverse": False}]}
        db.session.commit()
        assert matrix.kind.missing_codes(matrix, "zufr_handy") == (-77,)
    with app.test_request_context():
        book = {r[0]: r for r in export.codebook(db.session.get(Survey, 1))}
        assert "-77 = nicht gezeigt" in book["zufr_handy"][3]
        assert "nur gezeigt, wenn in geraete gewählt" in book["zufr_handy"][4] and "zählt als fehlend" in book["zufr_handy"][4]
        assert "-77" not in book["zufr_all"][3]


def test_vignette_long_format_has_the_values_of_the_participation(app, client, survey):
    """E-9: an assignment task in a vignette loop: its sides (_swap) are in the long format as in the wide export."""
    from tests.analysis.test_vignettes import _with_answers

    add(client, "assign")                                                       # 2, in the loop block
    node, _ = _with_answers(app, client, persons=3)
    with app.app_context():
        item = db.session.scalar(db.select(Item).filter_by(block_id=2, type="assign"))
        els = item.config["elements"]
        for r in db.session.scalars(db.select(Response).filter_by(survey_id=1)):
            rid = r.layout["loops"][node["id"]][0]
            r.answers = {**r.answers, f"{item.id}#{rid}": [{"s": e["id"], "c": "L", "rt": 500} for e in els]}
        db.session.commit()
        code = item.code
    lines = list(csv.reader(io.StringIO(client.get(f"/surveys/1/export/vignettes/{node['id']}.csv")
                                        .get_data(as_text=True).lstrip("﻿"))))
    head = lines[0]
    swap = head.index(f"{code}_{els[0]['code']}_swap")
    assert {row[swap] for row in lines[1:] if row[head.index("position")] == "1"} <= {"0", "1"}
    assert {row[swap] for row in lines[1:] if row[head.index("position")] == "1"}


def test_acbc_price_cap_set_and_shown_position(app, client, survey, monkeypatch):
    """S27: "no fixed limit" and "not asked" differ (_price_cap_set). S28: alt in the long format is the position
    shown on the page, without the concepts the rules left out."""
    from tests.analysis.test_acbc import setup, start, walk

    setup(client, rules=True, price=True, screens=4)
    uid = start(app, client)
    walk(app, uid, screen_answer=lambda c: "1", must="1", cap="none")
    walk(app, uid, screen_answer=lambda c: "1", must="1", cap="lowest")
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert [(w["robo_price_cap"] == "", w["robo_price_cap_set"]) for w in wide] == [(True, "0"), (False, "1")]
    with app.test_request_context():
        item = db.session.get(Item, 2)
        info = item.kind.codebook_info(item, "robo_price_cap_set", "de")
        assert info[1] == "1 = Grenze genannt; 0 = keine feste Grenze" and "nicht gefragt" in info[2][0]
        kind = item.kind
        concepts = [{"p": [1, 0, 0, 0]}, {"p": [1, 2, 1, 0]}, {"p": [0, 1, 1, 1]}, {"p": [2, 1, 0, 0]},
                    {"p": [1, 1, 1, 2]}, {"p": [0, 2, 0, 1]}]
        answer = {"seed": 1, "k": 3, "byo": {"a1": "a1l0", "a2": "a2l1", "a3": "a3l0", "a4": "a4l2"},
                  "concepts": concepts, "s": {str(n): 1 for n in range(6)}}
        plan_of = type(kind).plan

        def plan(self, it, a):  # concept 1 of the second page broke a rule and was not shown
            found = plan_of(self, it, a)
            found.concepts[3] = {**found.concepts[3], "auto": True}
            return found

        monkeypatch.setattr(type(kind), "plan", plan)
        rows = [r for r in kind.long_rows(item, SimpleNamespace(answers={"2": answer})) if r[0] == "screen"]
        assert [(r[1], r[2], r[3]) for r in rows] == [(1, 1, 1), (1, 2, 2), (1, 3, 3), (2, "", 4), (2, 1, 5), (2, 2, 6)]


def _separated(app, client):
    """consent | block 2: ASKU (scale 1 to 5) | split | block 3: randomized Likert, placement of the ASKU value."""
    fill_consent(client)
    client.post(E + "/blocks/2/items", data={"type": "construct:asku"})                     # 2
    client.post(E + "/blocks")                                                              # block 3
    add(client, "likert", block=3)                                                          # 3
    add(client, "score_band", block=3)                                                      # 4
    form = MultiDict([("code", "band"), ("question", "Ihre Einordnung"), ("source", "2"), ("basis", "value")])
    for upper, label in (("2,5", "niedrig"), ("3,5", "mittel"), ("", "hoch"), ("", "")):
        form.add("band_id", ""); form.add("band_upper", upper); form.add("band_label", label); form.add("band_text", "")
    client.post(E + "/items/4", data=form)
    client.post(E + "/flow/add", data={"kind": "split"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        matrix = db.session.get(Item, 3)
        matrix.code = "m"
        matrix.config = {**matrix.config, "randomize": True, "statements": [
            {"id": f"s{n}", "code": "", "reverse": False} for n in range(1, 7)]}
        matrix.texts = {"de": {**(matrix.texts or {}).get("de", {}), "statements": {f"s{n}": f"Aussage {n}" for n in range(1, 7)}}}
        sid = split_node(s)["id"]
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                                            {"t": "split", "id": sid}, {"t": "block", "block": 3}]}
        from app import flow

        flow.normalize(s)
        db.session.commit()
        sids = [st["id"] for st in db.session.get(Item, 2).config["statements"]]
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code, sids


def test_separated_section_keeps_orders_and_places_a_value_from_the_main_part(app, client, survey):
    """S30/T-27: the separated section shows its statements in the order drawn with the layout, and a placement
    whose source is in the main part reads that answer without storing it in the section."""
    uid, sids = _separated(app, client)
    url = f"/s/{uid}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data={"nav": "next", **{f"i2_{sid}": "4" for sid in sids}})
    page = p.get(url).data.decode()
    assert "getrennt von Ihren bisherigen Antworten" in page
    assert 'name="i4_value" value="4.000"' in page and 'name="i4_band" value="3"' in page
    with app.app_context():
        main = db.session.scalar(db.select(Response).filter_by(survey_id=1, is_preview=False))
        split = db.session.scalar(db.select(SplitSession))
        order = main.layout["stmt"]["3"]
        assert split.layout["stmt"] == {"3": order} and "2" not in (split.answers or {})
    shown = [page.index(f"Aussage {sid[1:]}") for sid in order]
    assert shown == sorted(shown)                                   # on the page in the order drawn
    p.post(url, data={"nav": "next", "i4_value": "1.000", "i4_band": "1", **{f"i3_{sid}": "2" for sid in order}})
    with app.app_context():
        __import__("app.contact", fromlist=["bundle"]).bundle()  # PostgreSQL: the finished section waits (K11)
        entry = db.session.scalar(db.select(ContactEntry))
        assert entry.answers["4"] == {"value": 4.0, "band": 3} and "2" not in entry.answers
        assert db.session.scalar(db.select(SplitSession)) is None
        data = __import__("app.contact", fromlist=["to_csv"]).to_csv(db.session.get(Survey, 1))
        head, row = list(csv.reader(io.StringIO(data.lstrip("﻿"))))
        assert row[head.index("band_band")] == "3" and not any(h.startswith("entfernt_") for h in head)


def test_separated_assignment_sides_from_the_kept_seed(app, client, survey):
    """S30: an assignment task in the separated section: the entry keeps the random id of the section (not of the
    participation), so the export names the sides as shown; an entry without it leaves them empty."""
    from app import contact
    from app.participate import _finish_split

    fill_consent(client)
    client.post(E + "/blocks")                                                              # block 3
    add(client, "assign", block=3)                                                          # 2
    client.post(E + "/flow/add", data={"kind": "split"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        sid = split_node(s)["id"]
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                                            {"t": "split", "id": sid}, {"t": "block", "block": 3}]}
        from app import flow

        flow.normalize(s)
        db.session.commit()
        item = db.session.get(Item, 2)
        els = item.config["elements"]
        answer = [{"s": e["id"], "c": "L", "rt": 400} for e in els]
        assert contact.needs_seed(s)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        split = SplitSession(survey_id=1, language="de", layout={}, answers={"2": answer}, position=0)
        db.session.add(split)
        db.session.commit()
        seed = split.id
        _finish_split(s, split)
        db.session.add(ContactEntry(id="zz", survey_id=1, email="", comment="", answers={"2": answer}))  # stored before
        db.session.commit()
        contact.bundle()  # PostgreSQL: the finished section waits for the bundle (K11)
        entries = db.session.scalars(db.select(ContactEntry)).all()
        assert sorted(contact.SEED in e.answers for e in entries) == [False, True]
        head, *rows = list(csv.reader(io.StringIO(contact.to_csv(s).lstrip("﻿"))))
        col = head.index(f"{item.code}_{els[0]['code']}_swap")
        expected = str(int(item.kind.swapped(item, seed, [e["id"] for e in els])[els[0]["id"]]))
        assert sorted(r[col] for r in rows) == sorted(["", expected])
        assert not any(h.startswith("entfernt_") for h in head)
