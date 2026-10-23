"""Categorising open answers, the rest after stage 2: association tasks (every word a unit, the person gets the
categories of all of them), questions in loops (every pass a unit, the categories per pass in the export and on the
results page) and the methods section (app/coding.py methods_sentences)."""
import csv
import io

from app.extensions import db
from app.models import Block, Response, Survey
from tests.conftest import add, experiments_on, fill_table
from tests.interview.test_coding import _add, _assign, _codings


def _rows(client):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))


def _respond(app, answers_list):
    with app.app_context():
        for n, answers in enumerate(answers_list):
            db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED,
                                    pid=f"p{n}", qversion=1, answers=answers, layout={}))
        db.session.commit()


def test_association_words_are_units(app, client, survey):
    experiments_on(client, "coding")
    add(client, "association")
    client.post("/surveys/1/editor/items/2", data={"code": "assoc", "question": "Woran denken Sie bei Robotern?", "slots": "3"})
    _respond(app, [{"2": [{"t": "Arbeit", "v": 1}, {"t": "Angst", "v": -2}]},
                   {"2": [{"t": "arbeit", "v": 2}]},
                   {"2": [{"t": "Kino", "v": 0}, {"t": "Angst", "v": -1}, {"t": "Zukunft", "v": 1}]},
                   {}])
    key = "2_assoc"
    page = client.get(f"/surveys/1/categorise/{key}").data.decode()
    assert "Zukunft" in page and "Arbeit" in page
    _add(client, "Wirtschaft", key=key)
    _add(client, "Gefühl", key=key)
    assert _assign(client, "arbeit", 1, key=key).get_json()["changed"] == 2  # both persons that named it
    _assign(client, "angst", 2, key=key)
    got = _codings(app)
    assert got["p0"] == {f"{key}#1": [1], f"{key}#2": [2]} and got["p2"] == {f"{key}#2": [2]}
    rows = {r["pid"]: r for r in _rows(client)}
    assert (rows["p0"]["assoc_assoc_k1"], rows["p0"]["assoc_assoc_k2"]) == ("1", "1")  # the person: all their words
    # "Kino", "Zukunft" still open: the person counts as categorised only once every word is (audit 2026-10-10, B-11)
    assert (rows["p2"]["assoc_assoc_k1"], rows["p2"]["assoc_assoc_k2"]) == ("", "")
    assert rows["p3"]["assoc_assoc_k1"] == ""
    with app.app_context():
        from app import results

        entry = next(e for e in results.compute(db.session.get(Survey, 1))["items"] if e["item"].id == 2)
    cl = entry["codings"][0]
    assert cl["n"] == 2 and cl["open"] == 1 and {r["label"]: r["count"] for r in cl["rows"]}["Gefühl"] == 1
    assert "alle Begriffe" in {r[0]: r for r in csv.reader(io.StringIO(
        client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}["assoc_assoc_k1"][4]


def test_loop_passes_are_units(app, client, survey):
    from tests.editor.test_loops import loop_url

    experiments_on(client, "coding")
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "why", "question": "Warum {{robot}}?"})
    fill_table(client, loop_url(client), robot=["Pepper", "Nao"])
    with app.app_context():
        from app import loops

        pepper, nao = [r["id"] for r in loops.config(db.session.get(Block, 2))["rows"]]
    _respond(app, [{f"2#{pepper}": "Zu teuer", f"2#{nao}": "Niedlich"}, {f"2#{pepper}": "niedlich"}])
    key = "2"
    page = client.get(f"/surveys/1/categorise/{key}").data.decode()
    assert "Niedlich" in page and "Zu teuer" in page
    _add(client, "Preis")
    _add(client, "Aussehen")
    assert _assign(client, "niedlich", 2).get_json()["changed"] == 2
    _assign(client, "zu teuer", 1)
    assert _codings(app)["p0"] == {f"{key}#{pepper}": [1], f"{key}#{nao}": [2]}
    rows = {r["pid"]: r for r in _rows(client)}
    assert (rows["p0"]["why_k1_1"], rows["p0"]["why_k2_2"]) == ("1", "1")
    assert (rows["p1"]["why_k2_1"], rows["p1"]["why_k1_2"]) == ("1", "")  # no answer in pass 2
    book = {r[0]: r for r in csv.reader(io.StringIO(client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")))}
    assert "Pepper" in book["why_k1_1"][1]
    with app.app_context():
        from app import results

        entry = next(e for e in results.compute(db.session.get(Survey, 1))["items"] if e["item"].id == 2)
    assert "codings" not in entry and [cl["pass"] for cl in entry["codings_loop"]] == ["1", "2"]
    html = client.get("/surveys/1/results").data.decode()
    assert "Kategorisieren" in html and "_2" in html


def test_methods_section_names_categories_ai_and_alpha(app, client, survey):
    from tests.interview.test_coding import _survey
    from tests.interview.test_coding_stage2 import _second_person

    key = _survey(app, client)
    _add(client, "Preis")
    _add(client, "Lieferung")
    _assign(client, "zu teuer", 1)
    _assign(client, "lieferung dauert", 2)
    _assign(client, "toll", 99)
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(pid="p2"))
        r.coding = {**r.coding, key + "~ki": 1}
        db.session.commit()
    other, uid = _second_person(app)
    client.post(f"/surveys/1/categorise/{key}/second/start", data={"coder": str(uid)})
    for norm, cat in (("zu teuer", 1), ("lieferung dauert", 2), ("toll", 99)):
        other.post(f"/surveys/1/categorise/{key}/assign", data={"norm": norm, "slot": "second", "cat": str(cat)})
    other.post(f"/surveys/1/categorise/{key}/second/finish")
    with app.app_context():
        from app import coding

        found = coding.methods_sentences(db.session.get(Survey, 1))
    assert len(found) == 1
    de = found[0]["text_de"]
    assert "2 inhaltlichen Kategorien" in de and "kategorisiert sind 5 von 5" in de
    assert "Bei 1 Person wurde" in de
    assert "Krippendorffs α = 1,00" in de and "Übereinstimmung 100 %" in de
    assert "Krippendorff's α = 1.00" in found[0]["text_en"]
    page = client.get("/surveys/1/methods").data.decode()
    assert "Offene Antworten" in page and "Krippendorffs α" in page
