"""The indexes of response (performance audit 2026-10-18, PERF-02 and PERF-10).

PERF-02: the counts of participations leave out the further words of a live vote. Read from params["live_extra"] (JSON)
every count read every row instead of the index ix_response_counts; now the column live_extra, the last column of that
index, so that the survey's counts (Survey.tally), the dashboard and the administration's list read only the index.
PERF-10: no single-column index that is the leading column of another index of response (ix_response_survey_id,
ix_response_enrolment_id went in migration 0088), and the look-ups by person still use an index."""
import re
from datetime import timedelta

from sqlalchemy import event, inspect

from app.extensions import db
from app.models import Response, Survey, mark_live_extra, utcnow
from tests.conftest import needs_sqlite


def _live_vote(app, people=2, extra=3):
    with app.app_context():
        s = Survey(title="Wortwolke", status=Survey.RUNNING, live={"run": {"id": 1, "name": "Durchgang"}},
                   opened_at=utcnow() - timedelta(days=1))
        db.session.add(s)
        db.session.flush()
        for i in range(people + extra):
            params = {"live_run": 1} if i < people else {"live_run": 1, "live_extra": True, "live_main": "p"}
            db.session.add(Response(survey_id=s.id, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    params=params))
        db.session.commit()
        return s.id


def test_params_set_the_column_live_extra(app):
    sid = _live_vote(app)
    with app.app_context():
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=sid).order_by(Response.id)).all()
        assert [r.live_extra for r in rows] == [False, False, True, True, True]
        rows[0].params = {**rows[0].params, "live_extra": True}
        rows[2].params = {"live_run": 1}
        db.session.commit()
        assert [r.live_extra for r in rows] == [True, False, False, True, True]
        assert db.session.get(Survey, sid).completed_count() == 2


def test_the_housekeeping_marks_further_words_an_older_program_stored(app):
    """During a rolling deploy the participant container not yet restarted stores further words without the column
    (its server_default is false); mark_live_extra sets it from params, only in live votes."""
    sid = _live_vote(app, people=1, extra=2)
    with app.app_context():
        db.session.execute(db.update(Response.__table__).values(live_extra=False))  # as the old code wrote them
        plain = Survey(title="Fragebogen")
        db.session.add(plain)
        db.session.flush()
        db.session.execute(db.insert(Response.__table__).values(
            survey_id=plain.id, language="de", source="real", status="completed", answers={},
            started_at=utcnow(), params={"live_extra": True}))  # not a live vote: no further word of one
        db.session.commit()
        assert db.session.get(Survey, sid).completed_count() == 3
        assert mark_live_extra() == 2
        assert db.session.get(Survey, sid).completed_count() == 1
        assert mark_live_extra() == 0


def test_the_housekeeping_takes_only_the_json_value_true(app):
    """A value that is no boolean (a URL parameter of that name) neither stops the housekeeping (a cast failed on
    PostgreSQL) nor marks the row (audit data storage 2026-10-21, B14)."""
    sid = _live_vote(app, people=1, extra=1)
    with app.app_context():
        db.session.execute(db.update(Response.__table__).values(live_extra=False))
        for value in ("nein", "1", "true", 1):
            db.session.execute(db.insert(Response.__table__).values(
                survey_id=sid, language="de", source="real", status="completed", answers={}, started_at=utcnow(),
                params={"live_extra": value}))
        db.session.commit()
        assert mark_live_extra() == 1


def _plans(app, run):
    """EXPLAIN QUERY PLAN of every count over response that run() sends."""
    seen = []

    def before(conn, cursor, statement, parameters, context, executemany):
        text = " ".join(statement.split()).lower()
        if "from response" in text and "count(" in text and not text.startswith("explain"):
            seen.append((statement, parameters))

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", before)
    try:
        run()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    plans = []
    with app.app_context(), db.engine.connect() as connection:
        for statement, parameters in seen:
            rows = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
            plans.append((" ".join(statement.split()), [r[-1] for r in rows]))
    return plans


def _reads_rows(plan):
    """The steps of a plan that read rows of response itself, not only an index of it."""
    return [step for step in plan if re.match(r"(SCAN|SEARCH) response\b(?!_)", step) and "COVERING INDEX" not in step]


@needs_sqlite
def test_the_counts_read_only_the_index(app, client, survey):
    """Survey.tally, the cards of the dashboard and the list of Administration > Befragungen: every count of
    participations a covering index, also with further words of a live vote among them (PERF-02)."""
    from app import dashboard

    sid = _live_vote(app)

    def run():
        with app.test_request_context():
            s = db.session.get(Survey, sid)
            s.tally()
            s.completed_count()
            dashboard.completed_counts([sid, survey])
        assert client.get("/admin/surveys").status_code == 200
        assert client.get("/dashboard/").status_code == 200

    plans = _plans(app, run)
    # the counts that leave further words out (the periods of the administration's dashboard search by their time
    # indexes, ix_response_started_at and ix_response_completed_at, and stay out of this)
    counts = [(sql, plan) for sql, plan in plans if "live_extra" in sql and "_at >=" not in sql]
    assert len(counts) >= 4, plans
    wrong = [(sql, plan) for sql, plan in counts if _reads_rows(plan)]
    assert not wrong, wrong
    assert any("COVERING INDEX ix_response_counts" in " ".join(plan) for _, plan in plans)
    assert not any("params" in sql for sql, _ in plans)  # the JSON of the rows is not part of a count


def test_no_index_of_response_repeats_the_start_of_another(app):
    """A single-column index whose column leads a composite index of the same table only costs every insert and save
    (PERF-10). Exceptions would be named here."""
    with app.app_context():
        found = inspect(db.engine).get_indexes("response")
    names = {i["name"] for i in found}
    assert "ix_response_survey_id" not in names and "ix_response_enrolment_id" not in names
    for single in [i for i in found if len(i["column_names"]) == 1 and not i.get("unique")]:
        column = single["column_names"][0]
        others = [i["name"] for i in found if i is not single and len(i["column_names"]) > 1
                  and i["column_names"][0] == column and not (i.get("dialect_options") or {}).get("sqlite_where")]
        assert not others, (single["name"], others)


@needs_sqlite
def test_look_ups_by_survey_and_by_person_keep_an_index(app):
    """Without the two single-column indexes: by survey through an index that begins with it, by person (also IN) through the
    partial index ix_response_diary_started (enrolment_id IS NOT NULL is implied by = and IN)."""
    with app.app_context(), db.engine.connect() as connection:
        def plan(sql):
            return " ".join(r[-1] for r in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + sql))

        by_survey = plan("SELECT * FROM response WHERE survey_id = 1")  # ix_response_counts or ix_response_start_flood
        assert by_survey.startswith("SEARCH response USING INDEX ix_response_") and "(survey_id=?)" in by_survey
        assert "ix_response_diary_started" in plan("SELECT * FROM response WHERE enrolment_id = 1")
        assert "ix_response_diary_started" in plan("SELECT * FROM response WHERE enrolment_id IN (1, 2)")
        assert "ix_response_diary_started" in plan("DELETE FROM response WHERE enrolment_id = 1") or \
            "ix_response_diary_started" in plan("SELECT id FROM response WHERE enrolment_id = 1")


@needs_sqlite
def test_the_paper_check_reads_an_index_not_the_rows(app):
    """Audit 2026-10-22, G-03: export.has_paper (every export and results computation) found no index for channel and
    read every participation of the survey when there was no paper questionnaire (50 ms at 10 000)."""
    with app.app_context(), db.engine.connect() as connection:
        plan = " ".join(r[-1] for r in connection.exec_driver_sql(
            "EXPLAIN QUERY PLAN SELECT response.id FROM response WHERE response.survey_id = 1 "
            "AND response.channel = 'paper' LIMIT 1"))
        assert "ix_response_channel" in plan, plan
