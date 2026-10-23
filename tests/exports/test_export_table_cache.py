"""File formats share the private table; invalidation, signatures and erasure remain mandatory (SP13 PF-4)."""
import os
from pathlib import Path

from app import export, export_tables, results_store
from app.extensions import db
from app.models import Response, Survey


def test_formats_reuse_rows_and_bulk_edit_and_forget_remove_the_old_state(app, client, survey, monkeypatch):
    with app.test_request_context():
        study = db.session.get(Survey, 1)
        answer = Response(survey_id=1, source="real", status=Response.COMPLETED)
        db.session.add(answer)
        db.session.commit()
        calls = []
        original = export._rows
        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)
        monkeypatch.setattr(export, "_rows", counted)
        export.to_csv(study)
        export.spss_sav(study)
        export.r_rds(study)
        export.jamovi_omv(study)
        assert len(calls) == 1
        path = Path(results_store._path(study, name="export-table"))
        assert path.is_file()
        # A modified cache never reaches pickle loading.
        path.write_bytes(b"tampered")
        export.to_csv(study)
        assert len(calls) == 2
        db.session.execute(db.update(Response).where(Response.id == answer.id).values(answers={"2": "changed"}))
        db.session.commit()
        export.to_csv(study)
        assert len(calls) == 3
        export_tables.forget(study)
        assert not path.exists()


def test_cache_expires_and_estimate_files_invalidate_it(app, client, survey, monkeypatch):
    with app.test_request_context():
        study = db.session.get(Survey, 1)
        calls = []
        original = export._rows
        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)
        monkeypatch.setattr(export, "_rows", counted)
        export.to_csv(study)
        path = Path(results_store._path(study, name="export-table"))
        (path.parent / "item_2.json").write_text('{"result": 1}', encoding="utf-8")
        export.to_csv(study)
        assert len(calls) == 2
        os.utime(path, (0, 0))
        export_tables.sweep()
        assert not path.exists()
