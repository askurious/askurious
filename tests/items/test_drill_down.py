"""Drill-down (items/drill_down.py): table in the editor, answers, export, codebook, translation, dummy data, page."""
import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import AnswerError, FormRefused
from app.items.drill_down import list_code, read_table
from app.models import Item, Response, Survey
from tests.conftest import add

TABLE = "Marke\tModell\tBaujahr\nVW\tGolf\t2019\nVW\tGolf\t2020\nVW\tPolo\t2021\nOpel\tCorsa\t2020\nSonstige\n"


def setup_drill(client, table=TABLE, required=True):
    add(client, "drill_down")                                                      # item 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "auto"), ("question", "Welches Auto fahren Sie?"), ("answer_mode", "required" if required else "optional"),
        ("table", table)]))


def ids(item, *labels):
    """The ids of the entries along these labels from the top."""
    kids, out, parent = item.kind.children(item), [], ""
    for label in labels:
        parent = next(e["id"] for e in kids[parent] if e["label"] == label)
        out.append(parent)
    return out


def sent(item, path):
    return MultiDict({f"i{item.id}_{n}": eid for n, eid in enumerate(path)})


def test_read_table_separators_and_ragged_rows():
    assert read_table("A;B\n x ; y \n\nz;") == (["A", "B"], [["x", "y"], ["z"]])
    assert read_table('A,B\n"a, b",c') == (["A", "B"], [["a, b", "c"]])
    assert read_table("A\tB\nx\t\ty") == (["A", "B"], [["x"]])                     # a row ends at its first empty cell
    assert list_code("Baujahr (ab)", 2, set()) == "baujahr_ab" and list_code("Größe", 0, set()) == "groesse"
    assert list_code("2. Ebene", 1, set()) == "l2" and list_code("Marke", 0, {"marke"}) == "marke_2"


def test_editor_builds_the_hierarchy(app, client, survey):
    setup_drill(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == []
        assert [(x["code"], x["label"]) for x in item.kind.lists(item)] == [("marke", "Marke"), ("modell", "Modell"), ("baujahr", "Baujahr")]
        assert [(e["code"], e["label"], e["list"]) for e in item.kind.entries(item) if e["list"] == 0] == [
            ("1", "VW", 0), ("2", "Opel", 0), ("3", "Sonstige", 0)]
        assert item.kind.paths(item) == [["VW", "Golf", "2019"], ["VW", "Golf", "2020"], ["VW", "Polo", "2021"],
                                         ["Opel", "Corsa", "2020"], ["Sonstige"]]
        # the same label under two parents is two entries ("2020" under Golf and under Corsa)
        assert [e["code"] for e in item.kind.entries(item) if e["list"] == 2] == ["1", "2", "3", "4"]
        assert item.kind.table(item, "de").splitlines()[0] == "Marke;Modell;Baujahr"
        old = {e["label"]: e["id"] for e in item.kind.entries(item) if e["list"] == 1}
    # a row added in the middle: the entries found again keep their ids, the codes are numbered anew (no real answers)
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "auto"), ("question", "Welches Auto fahren Sie?"), ("answer_mode", "required"),
        ("table", TABLE.replace("VW\tPolo", "VW\tID.3\t2022\nVW\tPolo"))]))
    with app.app_context():
        item = db.session.get(Item, 2)
        models = {e["label"]: (e["id"], e["code"]) for e in item.kind.entries(item) if e["list"] == 1}
        assert models["Golf"][0] == old["Golf"] and models["Polo"][0] == old["Polo"]
        assert [models[k][1] for k in ("Golf", "ID.3", "Polo", "Corsa")] == ["1", "2", "3", "4"]


def test_answers_export_and_codebook(app, client, survey):
    setup_drill(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        golf = ids(item, "VW", "Golf", "2020")
        assert item.kind.parse_answer(item, sent(item, golf)) == golf
        with pytest.raises(AnswerError, match="noch etwas bei „Baujahr“"):        # the list still to choose (X36)
            item.kind.parse_answer(item, sent(item, golf[:2]))
        with pytest.raises(AnswerError, match="Ungültige"):                         # Corsa is no VW
            item.kind.parse_answer(item, sent(item, [golf[0], ids(item, "Opel", "Corsa")[1]]))
        with pytest.raises(AnswerError):
            item.kind.parse_answer(item, MultiDict())
        other = ids(item, "Sonstige")                                                # a branch that ends on the first list
        assert item.kind.parse_answer(item, sent(item, other)) == other and not item.kind.is_missing(item, other)
        assert item.kind.is_missing(item, golf[:2]) and not item.kind.is_missing(item, golf)
        assert [c for c, _ in item.kind.export_columns(item)] == ["auto_marke", "auto_modell", "auto_baujahr"]
        assert item.kind.export_values(item, golf) == {"auto_marke": "1", "auto_modell": "1", "auto_baujahr": "2"}
        assert item.kind.export_values(item, other) == {"auto_marke": "3", "auto_modell": None, "auto_baujahr": None}
        assert item.kind.export_values(item, None) == {"auto_marke": None, "auto_modell": None, "auto_baujahr": None}
        kind, values, notes = item.kind.codebook_info(item, "auto_baujahr", "de")
        assert kind == "Auswahl" and values == "1 = 2019 (Golf); 2 = 2020 (Golf); 3 = 2021 (Polo); 4 = 2020 (Corsa)"
        assert "Liste 3 von 3" in notes[0] and "list 3 of 3" in item.kind.codebook_info(item, "auto_baujahr", "de", ui="en")[2][0]
        assert item.kind.result_column(item, "auto_marke") == ("categorical", "", {"1": "VW", "2": "Opel", "3": "Sonstige"})
        assert item.kind.pipe_text(item, golf, "de") == "2020"
        assert item.kind.pipe_part_text(item, golf, "auto_modell", "de") == "Golf"


def test_optional_partial_answer(app, client, survey):
    setup_drill(client, required=False)
    with app.app_context():
        item = db.session.get(Item, 2)
        part = ids(item, "VW", "Polo")
        assert item.kind.parse_answer(item, sent(item, part)) == part and item.kind.is_missing(item, part)
        assert item.kind.parse_answer(item, MultiDict()) is None


def test_other_language_relabels_in_place(app, client, survey):
    setup_drill(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de", "en"]
        db.session.commit()
        item = db.session.get(Item, 2)
        before = (item.config["lists"], item.config["entries"])
    client.post("/surveys/1/editor/items/2?lang=en", data=MultiDict([
        ("code", "auto"), ("question", "Which car do you drive?"), ("answer_mode", "required"), ("lang", "en"),
        ("table", "Brand;Model;Year\nVW;Golf;2019\nVW;Golf;2020\nVW;Polo;2021\nOpel;Corsa;2020\nOther;;\nextra row;x;y")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert (item.config["lists"], item.config["entries"]) == before              # the structure stays
        assert [x["label"] for x in item.kind.lists(item, "en")] == ["Brand", "Model", "Year"]
        assert item.kind.paths(item, "en")[-1] == ["Other"]
        assert item.kind.paths(item, "de")[-1] == ["Sonstige"]


def test_validation_and_survey_file_check(app, client, survey):
    setup_drill(client, table="Fach\nPsychologie\nInformatik")
    with app.app_context():
        item = db.session.get(Item, 2)
        problems = item.kind.validate(item)
        assert any("zwei Listen" in p for p in problems) and any("eine Ebene" in p for p in problems)
        item.config = {"lists": [{"id": "l1", "code": "a"}, {"id": "l2", "code": "b"}],
                       "entries": [{"id": "e1", "code": "1", "list": 0}, {"id": "e2", "code": "1", "list": 1, "parent": "e9"}]}
        assert item.kind.import_problems(item)
        item.config["entries"][1]["parent"] = "e1"
        assert item.kind.import_problems(item) == []


def test_page_and_dummy_data(app, client, survey):
    setup_drill(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "data-drill" in html and 'name="i2_0"' in html and 'name="i2_2"' in html and "<optgroup" in html and "Corsa" in html
    editor = client.get("/surveys/1/editor/items/2").data.decode()
    assert "data-table-file" in editor and "auto_baujahr" in editor
    with app.app_context():
        from app import dummy

        dummy.generate(db.session.get(Survey, 1), 10, seed=4)
        db.session.commit()
        item = db.session.get(Item, 2)
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(isinstance(a, list) and not item.kind.is_missing(item, a) for a in answers)


# --- code audit 2026-10-08 (N1, N4, N6, N10 area elemente; E2 area editor) ------------------------------------------

def _real(app, answers, **survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        for k, v in survey.items():
            setattr(s, k, v)
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, pid="p1",
                                qversion=1, answers={str(k): v for k, v in answers.items()}, layout={}))
        db.session.commit()


def _save(client, table, lang=None, auto=True):
    data = [("code", "auto"), ("question", "Welches Auto fahren Sie?"), ("answer_mode", "required"), ("table", table)]
    if auto:
        data += [("autosave", "1"), ("opened_running", "1")]
    url = "/surveys/1/editor/items/2" + (f"?lang={lang}" if lang else "")
    return client.post(url, data=MultiDict(data + ([("lang", lang)] if lang else [])))


def _labels(app, *paths):
    with app.app_context():
        item = db.session.get(Item, 2)
        labels = item.t("de")["entries"]
        return [[labels.get(i) for i in p] for p in paths]


def test_running_survey_takes_only_wordings_at_the_same_place(app, client, survey):
    """N1: a row added in the middle of a running survey's table would put the labels on other entries."""
    setup_drill(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        polo, opel = ids(item, "VW", "Polo", "2021"), ids(item, "Opel", "Corsa", "2020")
        config = item.config
    _real(app, {2: polo}, status=Survey.RUNNING)
    r = _save(client, TABLE.replace("VW\tPolo", "VW\tID.3\t2022\nVW\tPolo")).get_json()
    assert r["status"] == "invalid" and "Feldstart" in r["message"]
    assert _labels(app, polo, opel) == [["VW", "Polo", "2021"], ["Opel", "Corsa", "2020"]]
    # an entry named differently in two of its rows, a row less, a column more: refused too
    for table in (TABLE.replace("VW\tGolf\t2020", "VW\tGolf2\t2020"), TABLE.replace("VW\tPolo\t2021\n", ""),
                  TABLE.replace("Baujahr", "Baujahr\tFarbe")):
        assert _save(client, table).get_json()["status"] == "invalid"
    # wordings at the same place are taken, and the page's own table unchanged saves the rest of the form
    assert _save(client, TABLE.replace("Opel\tCorsa", "Opel\tCorsa F")).get_json()["status"] == "saved"
    assert _labels(app, polo, opel) == [["VW", "Polo", "2021"], ["Opel", "Corsa F", "2020"]]
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config == config
        shown = item.kind.table(item, "de")
    assert _save(client, shown).get_json()["status"] == "saved"
    # without the page saving by itself: a message instead of the change
    r = _save(client, TABLE.replace("VW\tPolo", "VW\tID.3\t2022\nVW\tPolo"), auto=False)
    assert r.status_code in (302, 400)
    assert _labels(app, polo) == [["VW", "Polo", "2021"]]


def test_reopened_survey_with_real_answers_keeps_its_labels(app, client, survey):
    """N1: opened for editing again with real answers, a row deleted falls back to "texts only"; that fallback
    must not shift the labels either."""
    setup_drill(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        polo, opel = ids(item, "VW", "Polo", "2021"), ids(item, "Opel", "Corsa", "2020")
    _real(app, {2: polo}, qversion=2)
    r = _save(client, TABLE.replace("VW\tPolo\t2021\n", "")).get_json()
    assert r["status"] == "invalid" and "Feldstart" in r["message"]
    assert _labels(app, polo, opel) == [["VW", "Polo", "2021"], ["Opel", "Corsa", "2020"]]


def test_table_beyond_the_limit_is_refused_whole(app, client, survey):
    """E2: more than MAX_ENTRIES entries were cut without a word (the last state missing, districts as leaves)."""
    setup_drill(client)
    lines = ["Land;Kreis;Gemeinde"] + [f"Land {s};Kreis {s}-{k};Gemeinde {s}-{k}-{g}"
                                       for s in range(16) for k in range(25) for g in range(28)]
    r = _save(client, "\n".join(lines)).get_json()
    assert r["status"] == "invalid" and "11.616" in r["message"] and "10.000" in r["message"]
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.paths(item)[0] == ["VW", "Golf", "2019"]                  # the table before stays
    assert "10.000" in client.get("/surveys/1/editor/items/2").data.decode()      # the field names the limit


def test_unclosed_quote_names_its_line(app, client, survey):
    """N4: a cell beginning with a quote took the rest of the table into one cell, a long table gave a 500."""
    with app.test_request_context():
        with pytest.raises(FormRefused, match="Zeile 3"):
            read_table('Marke;Modell\nVW;Golf\n"Alfa;Giulia\nOpel;Corsa\nBMW;3er\n')
    assert read_table('Marke;Modell\n"Alfa;Romeo";Giulia') == (["Marke", "Modell"], [["Alfa;Romeo", "Giulia"]])
    setup_drill(client)
    big = 'Marke;Modell\n"Alfa;Giulia\n' + "\n".join(f"Marke{n};Modell{n}" for n in range(9000))
    r = _save(client, big).get_json()
    assert r["status"] == "invalid" and "Zeile 2" in r["message"]
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.paths(item)[0] == ["VW", "Golf", "2019"]


def test_corrected_wording_above_keeps_the_translations_below(app, client, survey):
    """N6: "VW" -> "Volkswagen" in the draft made VW and everything below new entries without translations."""
    setup_drill(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de", "en"]
        db.session.commit()
    english = "Brand;Model;Year\nVW;Golf;2019\nVW;Golf;2020\nVW;Polo;2021\nOpel;Corsa;2020\nOther"
    _save(client, english, lang="en", auto=False)
    _save(client, TABLE.replace("VW\t", "Volkswagen\t"), auto=False)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.paths(item, "de")[0] == ["Volkswagen", "Golf", "2019"]
        assert item.kind.paths(item, "en") == [["VW", "Golf", "2019"], ["VW", "Golf", "2020"], ["VW", "Polo", "2021"],
                                               ["Opel", "Corsa", "2020"], ["Other"]]
        golf = ids(item, "Volkswagen", "Golf", "2019")
    # rows only swapped: the entries go with their labels, not with the place
    swapped = TABLE.replace("VW\t", "Volkswagen\t").replace("Golf\t2019\nVolkswagen\tGolf\t2020",
                                                            "Golf\t2020\nVolkswagen\tGolf\t2019")
    _save(client, swapped, auto=False)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert ids(item, "Volkswagen", "Golf", "2019") == golf


class _Counted(list):
    reads = 0

    def __iter__(self):
        _Counted.reads += 1
        return super().__iter__()


def test_export_builds_the_codes_once(app, client, survey):
    """N10: the code table of all entries was built anew per participation, is_missing built children() per call."""
    from app.items import base

    setup_drill(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        golf = ids(item, "VW", "Golf", "2019")
        entries = _Counted(item.config["entries"])
        item.config = {**item.config, "entries": entries}
        _Counted.reads = 0
        with base.export_plan():
            for _ in range(50):
                assert item.kind.export_values(item, golf)["auto_baujahr"] == "1"
                assert not item.kind.is_missing(item, golf) and item.kind.is_missing(item, golf[:2])
        assert _Counted.reads <= 4
        db.session.rollback()
