"""A change of a survey's state (start, close, pause, opening for editing) holds the survey's row and reads it anew
first (app/lifecycle.py, _hold_row): of two overlapping starts the second sees the first and is refused, instead of
freezing the questionnaire a second time and losing the first one's entry in the status log (function audit
2026-11-11). On PostgreSQL; the stand-in below plays the other request that committed meanwhile."""
import pytest

from app import edit_guard, lifecycle
from app.extensions import db
from app.models import Survey


@pytest.fixture
def postgres_like(app, monkeypatch):
    """_hold_row as on PostgreSQL, the lock reading the row as another request has just committed it."""
    calls = []
    with app.app_context():
        monkeypatch.setattr(db.engine.dialect, "name", "postgresql")

    def lock(obj):
        calls.append(obj.id)
        if getattr(obj, "_then", None):
            obj._then(obj)
        return obj

    monkeypatch.setattr(edit_guard, "lock", lock)
    return calls


def test_a_second_start_sees_the_first_after_the_lock(app, survey, postgres_like):
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.DRAFT
        s._then = lambda row: setattr(row, "status", Survey.RUNNING)  # the other start committed meanwhile
        with pytest.raises(lifecycle.LifecycleError) as e:
            lifecycle.start(s, None, None)
        assert "läuft bereits" in str(e.value) and postgres_like == [1]


def test_close_and_reopen_take_the_row_first(app, survey, postgres_like):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status = Survey.RUNNING
        lifecycle.close(s)
        assert postgres_like == [1]
        s._then = lambda row: setattr(row, "status", Survey.DRAFT)  # opened for editing by someone else meanwhile
        with pytest.raises(lifecycle.LifecycleError):
            lifecycle.reopen_for_editing(s)
        assert postgres_like == [1, 1]
