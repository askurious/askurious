"""Transactional, pool-specific invalidation without transferring answers (SP13 PF-3/PF-5)."""
from sqlalchemy import event, text

from app import quality, results_store
from app.data_revision import revision
from app.extensions import db
from app.models import Response, Survey

POOL = {"sources": ["real"], "all": False, "drop": False, "speeder": None}


def test_bulk_changes_rollback_and_pool_moves(app, client, survey):
    with app.app_context():
        study = db.session.get(Survey, 1)
        answer = Response(survey_id=1, source="real", status=Response.COMPLETED, answers={"x": "a"})
        db.session.add(answer)
        db.session.commit()
        initial = results_store._participations(study, POOL)
        before = revision(study)
        db.session.execute(db.update(Response).where(Response.id == answer.id).values(answers={"x": "b"}))
        assert results_store._participations(study, POOL)[1] > initial[1]
        assert revision(study) > before
        db.session.rollback()
        assert results_store._participations(study, POOL) == initial
        db.session.execute(db.update(Response).where(Response.id == answer.id).values(source="test"))
        db.session.commit()
        moved = results_store._participations(study, POOL)
        assert moved[0] == 0 and moved[1] > initial[1]
        db.session.execute(db.update(Response).where(Response.id == answer.id).values(source="real"))
        db.session.commit()
        returned = results_store._participations(study, POOL)
        assert returned[0] == 1 and returned[1] > moved[1]
        db.session.execute(db.delete(Response).where(Response.id == answer.id))
        db.session.commit()
        assert results_store._participations(study, POOL)[1] > returned[1]


def test_unfinished_answers_do_not_invalidate_the_completed_pool(app, client, survey):
    with app.app_context():
        study = db.session.get(Survey, 1)
        initial = results_store._participations(study, POOL)
        answer = Response(survey_id=1, source="real", status=Response.ACTIVE)
        db.session.add(answer)
        db.session.commit()
        assert results_store._participations(study, POOL) == initial
        db.session.execute(db.update(Response).where(Response.id == answer.id).values(status=Response.COMPLETED))
        db.session.commit()
        assert results_store._participations(study, POOL)[0] == 1


def test_raw_sql_is_tracked_and_stamps_read_no_response_payload(app, client, survey):
    with app.app_context():
        study = db.session.get(Survey, 1)
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers={"x": "a"}))
        db.session.commit()
        old = quality.stamp(study)
        db.session.execute(text("UPDATE response SET answers=:value WHERE survey_id=1"), {"value": '{"x":"b"}'})
        db.session.commit()
        statements = []
        def note(conn, cursor, statement, *args):
            statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", note)
        try:
            assert quality.stamp(study) != old
            results_store.fingerprint(study, POOL)
        finally:
            event.remove(db.engine, "before_cursor_execute", note)
        reads = [s for s in statements if "from response" in s.lower()]
        assert reads and all("answers" not in s.lower() and "paradata" not in s.lower() for s in reads)


def test_missing_triggers_cannot_silently_serve_stale_caches(app):
    import pytest
    from app.data_revision import validate
    from app.migrations import SchemaMismatch

    with app.app_context(), db.engine.begin() as connection:
        sql = "DROP TRIGGER revision_response_update" if connection.dialect.name == "sqlite" else "DROP TRIGGER revision_response ON response"
        connection.execute(text(sql))
        with pytest.raises(SchemaMismatch, match="Missing cache revision triggers"):
            validate(connection)


def test_reused_counters_after_a_restore_fork_do_not_reuse_old_cache_keys(app, client, survey):
    """Restored counters can reach the same number again with different data; change nonces separate the branches."""
    from app.models import ResponseRevision

    with app.app_context():
        study = db.session.get(Survey, 1)
        answer = Response(survey_id=1, source="real", status=Response.COMPLETED, answers={"x": "before"})
        db.session.add(answer)
        db.session.commit()
        before = results_store.fingerprint(study, POOL)
        counter = db.session.scalar(db.select(ResponseRevision.revision).where(ResponseRevision.survey_id == 1,
                                                                              ResponseRevision.status == "completed"))
        db.session.execute(db.update(Response).where(Response.id == answer.id).values(answers={"x": "different branch"}))
        db.session.execute(db.update(ResponseRevision).where(ResponseRevision.survey_id == 1).values(revision=counter))
        db.session.commit()
        assert results_store.fingerprint(study, POOL) != before


def test_saves_of_one_survey_count_in_separate_rows(app, client, survey):
    """Abgleich 2026-10-15 T05-N-1: a saved participation no longer writes the survey's row or one row per pool; it
    counts in the row of its pool and id (data_revision.SHARDS), and the stamps still change with every save."""
    from app import data_revision
    from app.models import ResponseRevision

    with app.app_context():
        study = db.session.get(Survey, 1)
        rows = [Response(survey_id=1, source="real", status=Response.ACTIVE, answers={}) for _ in range(3)]
        db.session.add_all(rows)
        db.session.commit()
        survey_row = db.session.execute(text("SELECT data_rev, data_nonce FROM survey WHERE id=1")).one()
        before = data_revision.stamp(study)
        db.session.execute(db.update(Response).where(Response.id == rows[1].id).values(answers={"1": 2}))
        db.session.commit()
        assert db.session.execute(text("SELECT data_rev, data_nonce FROM survey WHERE id=1")).one() == survey_row
        shards = set(db.session.scalars(db.select(ResponseRevision.shard).where(ResponseRevision.survey_id == 1)))
        assert shards == {r.id % data_revision.SHARDS for r in rows}
        assert data_revision.stamp(study) != before and data_revision.revision(study) == 4
