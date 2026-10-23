"""JSON null in the columns SQL tells the kinds of surveys apart by (audit 2026-10-21, B3): a Python None written to a
JSON column is stored as the text 'null', not as SQL NULL. `IS NOT NULL` counted such a survey as a live vote,
interview or diary study while Survey.kind (Python) read None and called it a questionnaire."""
import pytest

from app import dashboard, kind_access
from app.extensions import db, json_given, json_missing
from app.models import Survey

COLUMNS = ("live", "interview", "sampling")


def _stored(sid, column):
    return db.session.execute(db.text(f"SELECT CAST({column} AS TEXT) FROM survey WHERE id = :id"), {"id": sid}).scalar()


@pytest.mark.parametrize("column", COLUMNS)
def test_json_null_is_empty_for_every_filter_of_the_kinds(app, client, survey, column):
    with app.app_context():
        db.session.execute(db.update(Survey).where(Survey.id == 1).values(**{column: None}))
        db.session.commit()
        assert _stored(1, column) == "null"                    # JSON null, not SQL NULL: what the filters must read
        s = db.session.get(Survey, 1)
        assert s.kind == "survey"

        def ids(*where):
            return set(db.session.scalars(db.select(Survey.id).where(*where)))

        assert ids(json_missing(getattr(Survey, column))) == {1}
        assert ids(json_given(getattr(Survey, column))) == set()
        for kind in ("live", "interview", "diary"):
            assert 1 not in ids(kind_access.kind_clause(kind)), kind
        assert 1 in ids(kind_access.kind_clause("survey"))
        for kind, wanted in (("live", False), ("leitfaden", False), ("tagebuch", False), ("fragebogen", True)):
            found = db.session.scalars(dashboard._of_kind(db.select(Survey.id), kind)).all()
            assert (1 in found) is wanted, kind


def test_a_real_value_still_counts(app, client, survey):
    with app.app_context():
        db.session.execute(db.update(Survey).where(Survey.id == 1).values(sampling={"mode": "fixed"}))
        db.session.commit()
        assert db.session.get(Survey, 1).kind == "diary"
        assert db.session.scalars(db.select(Survey.id).where(kind_access.kind_clause("diary"))).all() == [1]
        assert db.session.scalars(db.select(Survey.id).where(json_given(Survey.sampling))).all() == [1]
