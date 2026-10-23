"""A cold quality page takes a shared slot; a cached page stays cheap (SP13 PF-5)."""
import pytest
from werkzeug.exceptions import HTTPException

from app import heavy, quality
from app.extensions import db
from app.models import Survey


POOL = {"sources": ["real"], "all": False}


def test_busy_quality_and_slot_release_without_a_request(app, client, survey, monkeypatch):
    with app.test_request_context():
        study = db.session.get(Survey, 1)
        monkeypatch.setattr(heavy, "hold", lambda: False)
        with pytest.raises(HTTPException) as failed:
            quality.page(study, POOL)
        assert failed.value.response.status_code == 503
    monkeypatch.undo()
    # Also release in a background/app context; request teardown alone would leak slots here.
    with app.app_context():
        study = db.session.get(Survey, 1)
        result = quality.page(study, POOL)
        from flask import g

        assert g.get("_heavy_place") is None
        monkeypatch.setattr(heavy, "hold", lambda: (_ for _ in ()).throw(AssertionError("cache took a slot")))
        assert quality.page(study, POOL) is result
