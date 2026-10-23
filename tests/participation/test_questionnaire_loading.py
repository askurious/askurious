"""Questionnaire loading stays constant with the number of blocks (SP13 PF-1/PF-8)."""
from sqlalchemy import event

from app import links, runtime
from app.extensions import db
from app.models import Block, Item, Survey


def test_blocks_and_items_are_loaded_in_batches(app, client, survey):
    with app.app_context():
        study = db.session.get(Survey, 1)
        for n in range(30):
            block = Block(survey_id=1, position=n + 10)
            block.items.append(Item(type="info", position=1, code=f"info{n}"))
            db.session.add(block)
        study.status = Survey.RUNNING
        db.session.commit()
        db.session.remove()
        statements = []
        def note(conn, cursor, statement, *args):
            statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", note)
        try:
            study = links.resolve("1")
            list(runtime.pages(study))
        finally:
            event.remove(db.engine, "before_cursor_execute", note)
        assert len([s for s in statements if "FROM item" in s]) == 1
        assert len([s for s in statements if "FROM block" in s]) == 1
        assert len([s for s in statements if "FROM survey_label" in s]) == 2  # alias + batch of canonical labels
        assert "codings" not in statements[0].split("FROM")[0]


def test_signature_is_reused_then_invalidated_by_bulk_structure_changes(app, client, survey, monkeypatch):
    with app.app_context():
        study = db.session.get(Survey, 1)
        study.status = Survey.RUNNING
        db.session.commit()
        calls = []
        original = runtime._pages_shape
        def counted(s):
            calls.append(1)
            return original(s)
        monkeypatch.setattr(runtime, "_pages_shape", counted)
        first = runtime._signature(study)
        assert runtime._signature(study) == first and len(calls) == 1
        block = study.blocks[0]
        db.session.execute(db.update(Block).where(Block.id == block.id).values(randomize_items=True))
        db.session.commit()
        db.session.expire_all()
        assert runtime._signature(study) != first and len(calls) == 2
        study.flow = {"type": "sequence", "children": []}
        db.session.commit()
        runtime._signature(study)
        assert len(calls) == 3


def test_core_sql_with_loaded_objects_never_caches_the_old_structure(app, client, survey):
    from sqlalchemy import text

    with app.app_context():
        study = db.session.get(Survey, 1)
        study.status = Survey.RUNNING
        db.session.commit()
        block = study.blocks[0]
        first = runtime._signature(study)
        db.session.execute(text("UPDATE block SET randomize_items=1 WHERE id=:bid"), {"bid": block.id})
        # No commit/expire: the relationship is still loaded with the old value.
        changed = runtime._signature(study)
        assert changed != first
        db.session.execute(text("UPDATE survey SET flow=:value WHERE id=1"),
                           {"value": '{"t":"seq","children":[]}'})
        assert runtime._signature(study) != changed
