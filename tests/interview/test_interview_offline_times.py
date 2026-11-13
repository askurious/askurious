"""Times of an offline interview as the browser sends them (app/static/interview-offline.js: Date.toISOString(), so
ISO with "Z"), or with an offset: the sync takes them in as UTC without a zone, like every other time stored."""
from datetime import datetime

from app.extensions import db
from app.models import Response
from tests.interview.test_interview_offline_mode import _on, _pack, _sync


def _row(app, sid):
    with app.app_context():
        return db.session.scalars(db.select(Response).filter_by(survey_id=sid)).one()


def test_start_and_end_with_z_as_the_browser_sends_them(app, client):
    code, sid = _on(app, client)
    pack = _pack(client, code, n=1)
    slot = pack["slots"][0]
    r = _sync(client, code, pack, slot, started="2026-10-22T10:00:00.123Z")
    assert r.status_code == 200, r.data
    r = _sync(client, code, pack, slot, started="2026-10-22T10:00:00.123Z", finish=True, ended="2026-10-22T11:30:00.000Z")
    assert r.status_code == 200 and r.json["done"] is True
    row = _row(app, sid)
    assert row.started_at == datetime(2026, 10, 22, 10, 0, 0, 123000)
    assert row.completed_at == datetime(2026, 10, 22, 11, 30)
    assert row.started_at.tzinfo is None and row.completed_at.tzinfo is None


def test_start_and_end_with_an_offset_become_utc(app, client):
    code, sid = _on(app, client)
    pack = _pack(client, code, n=1)
    slot = pack["slots"][0]
    r = _sync(client, code, pack, slot, started="2026-10-22T12:00:00+02:00", finish=True, ended="2026-10-22T13:15:00+02:00")
    assert r.status_code == 200 and r.json["done"] is True, r.data
    row = _row(app, sid)
    assert row.started_at == datetime(2026, 10, 22, 10, 0)
    assert row.completed_at == datetime(2026, 10, 22, 11, 15)
