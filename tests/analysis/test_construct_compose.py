"""Several toolbox constructs as one data set for a meta-analysis (app/construct_compose.py, Administration >
Konstrukte > Zusammenstellung)."""
import csv
import io
import random
import zipfile

from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.analysis.test_construct_pool import _kut_survey

URL = "/admin/constructs/compilation"


def _two_surveys(app, client):
    """Two closed surveys with KUT; the first also asks gender (option 1 for everyone). 4 and 3 participations;
    one of the first has no gender answer, and a dummy participation never counts."""
    rng = random.Random(3)
    first, second = _kut_survey(app, client, "Erste"), _kut_survey(app, client, "Zweite")
    with app.app_context():
        block = db.session.get(Survey, first).blocks[-1].id
    client.post(f"/surveys/{first}/editor/blocks/{block}/items", data={"type": "construct:gender"})
    with app.app_context():
        for sid, n in ((first, 4), (second, 3)):
            s = db.session.get(Survey, sid)
            kut = next(i for b in s.blocks for i in b.items if i.type == "likert")
            gender = next((i for b in s.blocks for i in b.items if i.type == "single_choice"), None)
            for k in range(n):
                answers = {str(kut.id): {st["id"]: rng.randint(1, 5) for st in kut.config["statements"]}}
                if gender is not None and k:
                    answers[str(gender.id)] = gender.config["options"][0]["code"]
                db.session.add(Response(survey_id=sid, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                        language="de", answers=answers))
            db.session.add(Response(survey_id=sid, source="dummy", status=Response.COMPLETED, completed_at=utcnow(),
                                    language="de", answers={}))
            s.status = Survey.CLOSED
        db.session.commit()


def test_two_constructs_as_one_data_set(app, client):
    _two_surveys(app, client)
    assert "Mehrere Konstrukte zusammenstellen" in client.get("/admin/constructs").get_data(as_text=True)
    page = client.get(URL).get_data(as_text=True)
    assert 'value="kut"' in page and 'value="gender"' in page
    page = client.get(URL, query_string={"k": ["kut", "gender"]}).get_data(as_text=True)
    assert "7 Teilnahmen aus 2 Befragungen" in page and "Erste" not in page  # pseudonyms only
    # only participations with both constructs
    page = client.get(URL, query_string={"k": ["kut", "gender"], "vollstaendig": "1"}).get_data(as_text=True)
    assert "3 Teilnahmen aus 1 Befragung" in page

    r = client.get(URL + ".zip", query_string={"k": ["kut", "gender"]})
    assert r.status_code == 200 and r.mimetype == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert set(z.namelist()) == {"daten.csv", "daten.sav", "daten.omv", "daten.rds", "codebuch.csv", "quellen.bib", "README.md"}
    rows = list(csv.DictReader(io.StringIO(z.read("daten.csv").decode("utf-8-sig"))))
    assert len(rows) == 7 and len({(r["study_id"], r["participant_id"]) for r in rows}) == 7
    assert all(r["study_id"].startswith("S") and r["participant_id"].startswith("P") for r in rows)
    header = list(rows[0])
    assert header[:4] == ["study_id", "participant_id", "language", "month"]
    assert "kut_version" in header and "kut_adapted" in header and "gender" in header and "gender_version" in header
    kut_cols = [h for h in header if h not in ("study_id", "participant_id", "language", "month")
                and not h.startswith("gender") and not h.startswith("kut_version") and not h.startswith("kut_adapted")]
    assert kut_cols and all(r[c] for r in rows for c in kut_cols)  # every participation has KUT
    assert sum(1 for r in rows if r["gender"]) == 3  # gender: answered in the first survey by three
    assert z.read("daten.sav")[:4] == b"$FL2" and z.read("daten.rds")[:2] == b"\x1f\x8b"
    readme = z.read("README.md").decode()
    assert "2 Befragungen, 7 Teilnahmen" in readme and "Qualitätssicherung" in readme and "Zweite" not in readme
    assert "@" in z.read("quellen.bib").decode()  # BibTeX entries of the constructs
    book = z.read("codebuch.csv").decode("utf-8-sig")
    assert "Teilnahme (Pseudonym)" in book
    assert any(a.action == "Konstrukt-Pool (Zusammenstellung)" for a in _log(app))


def _log(app):
    from app.models import AccessLog

    with app.app_context():
        return db.session.scalars(db.select(AccessLog)).all()


def test_filters_and_who_may(app, client):
    _two_surveys(app, client)
    page = client.get(URL, query_string={"k": "kut", "lang": "en"}).get_data(as_text=True)
    assert "Mit diesen Filtern kommt keine Teilnahme zusammen" in page
    page = client.get(URL, query_string={"k": "kut", "von": "2999-01"}).get_data(as_text=True)
    assert "0 Teilnahmen" in page
    assert client.get(URL + ".zip").status_code == 404
    assert client.get(URL + ".zip", query_string={"k": "gibtsnicht"}).status_code == 404
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    other.post("/terms/", data={"accept": "1"})
    assert other.get(URL).status_code == 403
    assert other.get(URL + ".zip", query_string={"k": "kut"}).status_code == 403
