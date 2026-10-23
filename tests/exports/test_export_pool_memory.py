"""The memory of the exports (performance audit 2026-10-18, PERF-09): the participations of a data pool are filtered by
the database, so broken-off ones and those under review are not read with all their answers for an export of the
completed ones; a report process can be given an upper limit of memory (REPORT_JOB_MEMORY_MB, Linux)."""
import os

import pytest
from sqlalchemy import event

from app import export, report_jobs
from app.extensions import db
from app.models import Response, Survey, utcnow


def test_only_the_pool_is_read(app, survey):
    with app.app_context():
        for k in range(30):
            status = Response.COMPLETED if k < 10 else ("review" if k < 15 else Response.ACTIVE)
            db.session.add(Response(survey_id=1, source="real", status=status, answers={"x": "y" * 100},
                                    completed_at=utcnow() if status == Response.COMPLETED else None, pid=f"m{k}"))
        db.session.commit()
        db.session.expunge_all()
        loaded = []

        def note(target, context):
            loaded.append(target.id)

        event.listen(Response, "load", note)
        try:
            s = db.session.get(Survey, 1)
            assert len(export._responses(s, False, ("real",))) == 10 and len(loaded) == 10
            assert len(export._responses(s, True, ("real",))) == 25  # with the broken-off ones, never "review"
        finally:
            event.remove(Response, "load", note)


def test_the_memory_limit_of_a_report_process(monkeypatch):
    assert report_jobs.limit_memory(0) is False and report_jobs.limit_memory("x") is False
    if os.name == "nt":
        assert report_jobs.limit_memory(512) is False
        return
    resource = pytest.importorskip("resource")
    set_to = []
    monkeypatch.setattr(resource, "getrlimit", lambda kind: (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    monkeypatch.setattr(resource, "setrlimit", lambda kind, value: set_to.append((kind, value)))
    assert report_jobs.limit_memory(512) is True
    assert set_to == [(resource.RLIMIT_AS, (512 * 1024 * 1024, resource.RLIM_INFINITY))]
