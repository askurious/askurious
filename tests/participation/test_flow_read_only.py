"""Drawing a layout reads the flow and writes neither survey.flow nor block.position (flow.normalize_for_reading;
access audit 2026-11-11, B2 stage 2): the participants' role is to lose UPDATE on survey.flow. Before, every layout
wrote the normalized tree back, for a diary entry (diary_api.enrol, entry) as for a page (participate.page)."""
import random

from sqlalchemy import event

from app.extensions import db
from app.models import Survey


def _updates(app):
    seen = []

    def watch(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith(("UPDATE SURVEY", "UPDATE BLOCK")):
            seen.append(statement)
    return seen, watch


def test_a_layout_of_a_flow_not_normalized_writes_nothing(app, client, survey):
    from app import runtime

    with app.app_context():
        s = db.session.get(Survey, 1)
        stored = {"t": "root", "children": []}  # the blocks are missing: normalize() would add them
        s.flow = stored
        db.session.commit()
        seen, watch = _updates(app)
        event.listen(db.engine, "before_cursor_execute", watch)
        try:
            layout = runtime.build_layout(s, random.Random(1))
            db.session.commit()
        finally:
            event.remove(db.engine, "before_cursor_execute", watch)
        assert seen == []
        assert layout["pages"], "the layout is drawn with the blocks the tree lacked"
        db.session.expire_all()
        assert db.session.get(Survey, 1).flow == stored


def test_a_change_of_the_same_request_is_still_written(app, client, survey):
    """The researchers' side: a flow changed in this request and then laid out is written as before."""
    from app import runtime

    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": []}
        runtime.build_layout(s, random.Random(1))
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(Survey, 1).flow["children"], "normalized and written with the change"

