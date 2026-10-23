"""The count of the export page (/data-counts) is kept (performance audit 2026-10-18, PERF-01): it read every
participation with all answers and built the quality context anew on every load of the page and every change of its
form, 1.9 s at 10 000 participations, and took one of the places for heavy evaluations each time. Now kept with the
results under the fingerprint of the data; asked again with nothing changed it reads no answer."""
import time
from datetime import timedelta

from sqlalchemy import event

from app.extensions import db
from app.models import Response, utcnow
from tests.conftest import add


def _participations(app, n, start=0):
    with app.app_context():
        now = utcnow()
        for k in range(start, start + n):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers={"2": str(k % 5)},
                                    started_at=now - timedelta(seconds=60 + k % 40), completed_at=now,
                                    last_active_at=now, pid=f"c{k}"))
        db.session.commit()


def _ask(app, client, query="source=real"):
    seen = []

    def note(conn, cursor, statement, *_):
        seen.append(" ".join(statement.split()))

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", note)
    began = time.process_time()
    try:
        response = client.get("/surveys/1/data-counts?" + query)
    finally:
        event.remove(engine, "before_cursor_execute", note)
    assert response.status_code == 200
    return response.json, seen, time.process_time() - began


def test_asked_again_the_count_reads_no_participation(app, client, survey):
    add(client, "text_input")
    _participations(app, 1500)
    first, first_sql, first_cpu = _ask(app, client)
    assert first == {"total": 1500, "marked": first["marked"], "included": 1500}
    assert any("response.answers" in s for s in first_sql)
    again, again_sql, again_cpu = _ask(app, client)
    assert again == first
    assert not [s for s in again_sql if "response.answers" in s], again_sql
    assert len(again_sql) <= 30, again_sql
    assert again_cpu < first_cpu * 0.5, (first_cpu, again_cpu)
    # the other choice of the marked ones: the same kept count
    dropped, dropped_sql, _ = _ask(app, client, "source=real&drop=1")
    assert dropped["total"] == 1500 and dropped["included"] == 1500 - dropped["marked"]
    assert not [s for s in dropped_sql if "response.answers" in s]


def test_a_new_participation_counts_at_once(app, client, survey):
    add(client, "text_input")
    _participations(app, 20)
    assert _ask(app, client)[0]["total"] == 20
    _participations(app, 1, start=20)
    assert _ask(app, client)[0]["total"] == 21
    with app.app_context():
        db.session.execute(db.delete(Response).where(Response.pid == "c0"))
        db.session.commit()
    assert _ask(app, client)[0]["total"] == 20
