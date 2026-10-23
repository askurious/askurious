"""Categorising open answers (app/coding.py, stage 1): the scheme with "Sonstiges" and "nicht verwertbar", assigning
per text, only finished participations, the results, the export columns and the codebook, the coding guide, the
rights and that the assignments go with the participation."""
import csv
import io

from app.extensions import db
from app.models import Response, Survey, SurveyMember
from tests.conftest import add, experiments_on

OTHER = "0000-0002-1825-0097"
TEXTS = ["Zu teuer", "zu  teuer", "Lieferung dauert", "Toll", None, "Zu teuer"]


def _survey(app, client, statuses=None):
    """Survey 1 with a free text (item 2, code "why") and participations with TEXTS; returns the key. The feature
    "coding" is on (app/experimental.py)."""
    experiments_on(client, "coding")
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "why", "question": "Warum?"})
    with app.app_context():
        for n, text in enumerate(TEXTS):
            status = (statuses or {}).get(n, Response.COMPLETED)
            answers = {"2": text} if text is not None else {}
            db.session.add(Response(survey_id=1, source="real", is_preview=False, status=status, pid=f"p{n}",
                                    qversion=1, answers=answers, layout={}))
        db.session.commit()
    return "2"


def _add(client, name, key="2"):
    return client.post(f"/surveys/1/categorise/{key}/add", data={"name": name})


def _assign(client, norm, *cats, key="2"):
    return client.post(f"/surveys/1/categorise/{key}/assign", data={"norm": norm, "cat": [str(c) for c in cats]},
                       headers={"Accept": "application/json"})


def _codings(app):
    with app.app_context():
        return {r.pid: r.coding for r in db.session.scalars(db.select(Response).order_by(Response.id))}


def _csv(client, kind):
    r = client.get(f"/surveys/1/export/{kind}.csv")
    assert r.status_code == 200
    return list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))


def test_scheme_starts_with_other_and_unusable(app, client, survey):
    key = _survey(app, client)
    page = client.get(f"/surveys/1/categorise/{key}").data.decode()
    assert "Kategorisieren" in page and "Noch keine Kategorien" in page and "zu teuer" in page.lower()
    _add(client, "Preis")
    _add(client, "Lieferung")
    _add(client, "preis")  # refused: the same name
    with app.app_context():
        found = db.session.get(Survey, 1).codings[key]
    assert [c["id"] for c in found["cats"]] == [1, 2, 98, 99] and found["next"] == 3
    assert [c["name"] for c in found["cats"]][:2] == ["Preis", "Lieferung"]
    # the two fixed ones cannot be removed, an own one can
    client.post(f"/surveys/1/categorise/{key}/remove/98")
    client.post(f"/surveys/1/categorise/{key}/remove/2")
    with app.app_context():
        assert [c["id"] for c in db.session.get(Survey, 1).codings[key]["cats"]] == [1, 98, 99]


def test_one_click_codes_every_participation_with_the_text(app, client, survey):
    key = _survey(app, client, statuses={5: Response.ACTIVE})
    _add(client, "Preis")
    _add(client, "Lieferung")
    r = _assign(client, "zu teuer", 1)
    assert r.get_json() == {"status": "saved", "changed": 2}  # p0 and p1 (spaces, case); p5 is still under way
    got = _codings(app)
    assert got["p0"] == {key: [1]} and got["p1"] == {key: [1]} and got["p5"] is None
    _assign(client, "lieferung dauert", 2, 1)
    assert _codings(app)["p2"] == {key: [1, 2]}
    # unknown categories are dropped, none at all is "open" again
    _assign(client, "lieferung dauert", 77)
    assert _codings(app)["p2"] is None
    page = client.get(f"/surveys/1/categorise/{key}").data.decode()
    assert "1 von 3 kategorisiert" in page


def test_only_one_category_when_ticked(app, client, survey):
    key = _survey(app, client)
    _add(client, "Preis")
    _add(client, "Lieferung")
    _assign(client, "lieferung dauert", 1, 2)
    form = {"name_1": "Preis", "name_2": "Lieferung", "name_98": "Sonstiges", "name_99": "nicht verwertbar", "single": "1"}
    client.post(f"/surveys/1/categorise/{key}/scheme", data=form)
    with app.app_context():
        assert not db.session.get(Survey, 1).codings[key]["single"]  # refused: an answer has two
    _assign(client, "lieferung dauert", 2)
    client.post(f"/surveys/1/categorise/{key}/scheme", data={**form, "definition_1": "Kosten, Preis", "group_1": "Kauf"})
    with app.app_context():
        found = db.session.get(Survey, 1).codings[key]
    assert found["single"] and found["cats"][0]["definition"] == "Kosten, Preis" and found["cats"][0]["group"] == "Kauf"
    assert _assign(client, "toll", 1, 2).status_code == 400


def test_export_codebook_and_results(app, client, survey):
    key = _survey(app, client)
    _add(client, "Preis")
    _add(client, "Lieferung")
    _assign(client, "zu teuer", 1)
    _assign(client, "lieferung dauert", 1, 2)
    _assign(client, "toll", 99)
    rows = _csv(client, "data")
    head = rows[0]
    assert head[head.index("why") + 1:head.index("why") + 5] == ["why_k1", "why_k2", "why_k98", "why_k99"]
    by = {r[head.index("pid")]: dict(zip(head, r)) for r in rows[1:]}
    assert (by["p0"]["why_k1"], by["p0"]["why_k2"]) == ("1", "0")
    assert (by["p2"]["why_k1"], by["p2"]["why_k2"]) == ("1", "1")
    assert by["p4"]["why_k1"] == ""  # no answer: empty
    book = {r[0]: r for r in _csv(client, "codebook")}
    assert "Preis" in book["why_k1"][1] and "0 = nicht zugeordnet" in book["why_k1"][3]
    with app.app_context():
        from app import results

        entry = next(e for e in results.compute(db.session.get(Survey, 1))["items"] if e["item"].id == 2)
        cl = entry["codings"][0]
    assert cl["n"] == 5 and cl["open"] == 0 and cl["mentions"] == 6  # three "zu teuer", one with two, "toll"
    rows_by = {r["label"]: r for r in cl["rows"]}
    assert rows_by["Preis"]["count"] == 4 and rows_by["Lieferung"]["count"] == 1
    assert [r["label"] for r in cl["rows"]][-2:] == ["Sonstiges", "nicht verwertbar"]  # last, whatever their count
    html = client.get("/surveys/1/results").data.decode()
    assert "Kategorisieren" in html and "Preis" in html
    guide = client.get("/surveys/1/export/kodierleitfaden.md").data.decode()
    assert "# Kodierleitfaden" in guide and "| 1 | Preis |" in guide and "Mayring" in guide
    # one category per answer: one column with the code
    form = {"name_1": "Preis", "name_2": "Lieferung", "name_98": "Sonstiges", "name_99": "nicht verwertbar", "single": "1"}
    _assign(client, "lieferung dauert", 2)
    client.post(f"/surveys/1/categorise/{key}/scheme", data=form)
    rows = _csv(client, "data")
    head = rows[0]
    assert "why_kat" in head and "why_k1" not in head
    by = {r[head.index("pid")]: dict(zip(head, r)) for r in rows[1:]}
    assert by["p2"]["why_kat"] == "2" and by["p3"]["why_kat"] == "99"
    book = {r[0]: r for r in _csv(client, "codebook")}
    assert "1 = Preis" in book["why_kat"][3]


def test_rights_and_deletion(app, client, survey):
    key = _survey(app, client)
    _add(client, "Preis")
    _assign(client, "toll", 1)
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, role="viewer", position=1))
        db.session.commit()
    viewer = app.test_client()
    viewer.get(f"/auth/dev-login?orcid={OTHER}&name=Vera")
    viewer.post("/terms/", data={"accept": "1"})
    assert viewer.get(f"/surveys/1/categorise/{key}").status_code in (302, 403)
    assert viewer.post(f"/surveys/1/categorise/{key}/assign", data={"norm": "zu teuer", "cat": "1"}).status_code in (302, 403)
    assert _codings(app)["p0"] is None
    assert "Kategorisieren</a>" not in viewer.get("/surveys/1/results").data.decode()
    # a column that cannot be categorised, or one that does not exist
    assert client.get("/surveys/1/categorise/1").status_code == 404
    assert client.get("/surveys/1/categorise/999").status_code == 404
    # the assignment lives in the participation: deleting it removes it
    with app.app_context():
        db.session.delete(db.session.scalar(db.select(Response).filter_by(pid="p3")))
        db.session.commit()
        assert not any((r.coding or {}) for r in db.session.scalars(db.select(Response)))


def test_experimental(app, client, survey):
    """Off: no button, no page; a survey that has categories keeps them for everyone on it."""
    key = _survey(app, client)
    experiments_on(client)
    assert client.get(f"/surveys/1/categorise/{key}").status_code == 404
    assert "Kategorisieren</a>" not in client.get("/surveys/1/results").data.decode()
    experiments_on(client, "coding")
    _add(client, "Preis")
    experiments_on(client)
    assert client.get(f"/surveys/1/categorise/{key}").status_code == 200
    assert "Kategorisieren</a>" in client.get("/surveys/1/results").data.decode()
