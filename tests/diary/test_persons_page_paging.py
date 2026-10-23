"""The block "Teilnehmende" of a diary study on "Datenerhebung" in pages (performance audit 2026-10-18, PERF-05): it
read every person with every entry and its answers on every visit (1.5 s and 1.4 MB at 1240 persons) and stored the
status of persons whose entry part had been completed. Now one page of persons, the entries of those only, the same
number of queries for 30 as for 90 persons, and a GET writes nothing."""
import secrets
from datetime import timedelta

from sqlalchemy import event

from app.extensions import db
from app.models import Enrolment, Response, Survey, utcnow
from tests.conftest import experiments_on

PATH = "/surveys/1/field"


def _study(app, client):
    experiments_on(client, "diary")
    client.post("/diary/new", data={"title": "Stimmung im Alltag", "language": "de"})


def _persons(app, n, start=0, pending=False):
    with app.app_context():
        survey = db.session.get(Survey, 1)
        for p in range(start, start + n):
            person = Enrolment(survey_id=survey.id, secret_hash=secrets.token_hex(32), pid=f"pp{p:04d}",
                               status=Enrolment.PENDING if pending else Enrolment.ACTIVE, source="real",
                               tz="Europe/Berlin", first_day=None if pending else (utcnow() - timedelta(days=2)).date())
            db.session.add(person)
            db.session.flush()
            db.session.add(Response(survey_id=survey.id, enrolment_id=person.id, pid=f"pp{p:04d}-e", source="real",
                                    status=Response.COMPLETED, occasion={"part": "entry"}, answers={"1": "ja"},
                                    layout={}, started_at=utcnow() - timedelta(days=2),
                                    completed_at=utcnow() - timedelta(days=2), qversion=1))
        db.session.commit()


def _get(app, client, path=PATH):
    seen = []

    def note(conn, cursor, statement, parameters, context, executemany):
        seen.append((" ".join(statement.split()), parameters))

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", note)
    try:
        response = client.get(path)
    finally:
        event.remove(engine, "before_cursor_execute", note)
    assert response.status_code == 200
    return response.data.decode(), seen


def test_one_page_of_persons_with_their_entries_only(app, client):
    _study(app, client)
    _persons(app, 30)
    client.get(PATH)
    _, few = _get(app, client)
    _persons(app, 60, start=30)
    page, many = _get(app, client)
    assert len(many) == len(few), (len(few), len(many))
    assert page.count('<td class="mono small">pp') == 10  # the first page: 10 rows (tables.ROW_SIZES)
    assert "90 Personen" in page and "pp0000" in page and "pp0010" not in page
    assert 'href="/surveys/1/field?dp_page=2#persons"' in page
    answers = [(sql, params) for sql, params in many if "response.answers" in sql]
    assert answers and all(len([p for p in (params or ()) if isinstance(p, int)]) <= 11 for _, params in answers)
    second = client.get(PATH + "?dp_page=2").data.decode()
    assert "pp0010" in second and "pp0000" not in second
    fifty = client.get(PATH + "?dp_n=50").data.decode()
    assert fifty.count('<td class="mono small">pp') == 50


def test_the_page_writes_nothing(app, client):
    """A person whose entry part is completed shows as active, but the GET stores nothing: the app's requests and
    the housekeeping store the change (diary.sweep)."""
    _study(app, client)
    _persons(app, 3, pending=True)
    page, seen = _get(app, client)
    assert page.count(">aktiv<") == 3
    assert not [sql for sql, _ in seen if sql.startswith(("INSERT", "UPDATE", "DELETE"))
                and "rate_hit" not in sql and "session" not in sql.split()[2]], seen
    with app.app_context():
        assert {e.status for e in db.session.scalars(db.select(Enrolment))} == {Enrolment.PENDING}
