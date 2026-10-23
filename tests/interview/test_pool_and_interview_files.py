"""Audit of 2026-10-05, package C: formula protection in the interview CSVs (E-5), the headers of pool.csv (E-14),
the own key of the pool pseudonyms (E-8) and the participations of the admin construct pool without what quality
control marks (E-6)."""
from datetime import timedelta

from app.extensions import db
from app.models import Construct, Item, Response, Survey, utcnow


def test_interview_csvs_have_the_formula_protection(app, client, monkeypatch):
    from app import interview, interview_results
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start, switch_on

    code, sid = make(app, client)
    switch_on(app)
    rid = start(app, client, code)
    with app.app_context():
        block = str(interview.guide_blocks(db.session.get(Survey, sid))[0].id)
    url = f"/guide/{code}/interviews/{rid}/summary"
    client.post(url, data={"key": block, "text": "- pflegt seit 2023"})
    client.post(url, data={"key": "case", "text": "=Typ"})
    matrix = client.get(f"/guide/{code}/interviews/summaries.csv").data.decode("utf-8-sig")
    assert "I01;'- pflegt seit 2023;" in matrix and matrix.rstrip().endswith(";'=Typ")
    real = interview_results.overview_row
    monkeypatch.setattr(interview_results, "overview_row", lambda survey, r: {
        **real(survey, r), interview_results.overview_columns(survey)[-1][0]: "=HYPERLINK(\"x\")"})
    overview = client.get(f"/guide/{code}/interviews/cases.csv").data.decode("utf-8-sig")
    assert "'=HYPERLINK" in overview and ";=HYPERLINK" not in overview


def test_pool_csv_is_not_cached_and_names_its_charset(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gender"})
    r = client.get("/constructs/gender/pool.csv")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert r.headers["Content-Type"] == "text/csv; charset=utf-8"


def test_pool_pseudonyms_have_their_own_key(app):
    from app import constructs

    with app.app_context():
        app.config["POOL_KEY"] = ""
        before = constructs._participant_ref(7), constructs._study_ref(3)
        app.config["SECRET_KEY"] = "ein-neuer-schluessel"
        assert (constructs._participant_ref(7), constructs._study_ref(3)) != before  # without POOL_KEY: SECRET_KEY, as before
        app.config["POOL_KEY"] = "pool-schluessel"
        kept = constructs._participant_ref(7), constructs._study_ref(3)
        app.config["SECRET_KEY"] = "noch-ein-schluessel"
        assert (constructs._participant_ref(7), constructs._study_ref(3)) == kept  # a new SECRET_KEY keeps them


def test_admin_pool_counts_without_the_marked(app, client, survey):
    from app import construct_pool

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gender"})
    with app.app_context():
        gender = db.session.get(Item, 2)
        now = utcnow()
        for seconds in (300, 300, 300, 300, 300, 5):  # the last one is a speeder
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=now,
                                    started_at=now - timedelta(seconds=seconds),
                                    answers={"2": gender.config["options"][0]["code"]}))
        db.session.get(Survey, 1).status = Survey.CLOSED
        db.session.commit()
        row = next(r for r in construct_pool.overview() if r["construct"].key == "gender")
        assert row["n"] == 5
        report = construct_pool.report(db.session.scalars(db.select(Construct).filter(Construct.key == "gender")).one())
        assert report["n"] == 5 and [s["participations"] for s in report["studies"]] == [5]
        assert report["studies"][0]["months"] == f"{now:%Y-%m}"
