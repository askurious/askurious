"""Timeline and totals share one fetch and preserve local dates across DST (SP13 PF-6)."""
from datetime import datetime

from sqlalchemy import event

from app import timeline
from app.extensions import db
from app.models import Response, Survey


def test_one_response_query_and_local_day_at_clock_change(app, client, survey):
    with app.test_request_context():
        study = db.session.get(Survey, 1)
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED,
                                started_at=datetime(2026, 10, 24, 22, 30), completed_at=datetime(2026, 10, 24, 22, 45)))
        db.session.commit()
        statements = []
        def note(conn, cursor, statement, *args):
            if "FROM response" in statement:
                statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", note)
        try:
            width, first, bins = timeline._bins(study, datetime(2026, 10, 24), datetime(2026, 10, 26))
            timeline._real_times(study)
        finally:
            event.remove(db.engine, "before_cursor_execute", note)
        assert len(statements) == 1 and width == 1
        assert bins[1] == (1, 0)  # 22:45 UTC on the 24th is 00:45 Berlin on the 25th
