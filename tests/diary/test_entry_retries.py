"""Idempotent entry retries and server time-zone history on isolated databases."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from app import diary
from app.extensions import db
from app.models import Survey, Response, utcnow
from tests.diary.test_diary import API, _new, _enrol, _active_from_today, _bearer
from tests.diary.test_diary_integrity import _kept_page, _rating_field, _offline

def setup_self(app, client, limit=1):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        modules = diary.modules(survey)
        modules[0] = {**modules[0], "trigger": {"t": "self", "label": {}, "max_per_day": limit}}
        survey.sampling = {**survey.sampling, "modules": modules}
        db.session.commit()
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    at = int((utcnow()-timedelta(minutes=1)).replace(tzinfo=timezone.utc).timestamp()*1000)
    return phone, secret, version, field, at

def test_lost_entry_reply_is_completed_by_same_offline_uuid(app, client):
    phone, secret, version, field, at = setup_self(app, client)
    # Server accepted the opening request, but phone receives no reply and uses its kept page.
    assert phone.post(API+'/entry', json={"module":"m1", "uuid":"11111111-1111-1111-1111-111111111111"}, headers=_bearer(secret)).status_code == 200
    reply = _offline(phone, secret, version, field, None, at, uuid='11111111-1111-1111-1111-111111111111')
    print('lost entry reply then offline:', reply.status_code, reply.json)
    assert reply.status_code == 200 and reply.json['status'] == 'stored'
    with app.app_context():
        rows = db.session.scalars(db.select(Response).filter_by(part='m1')).all()
        assert len(rows)==1 and rows[0].status==Response.COMPLETED and rows[0].answers[field[1:]] == 4

def test_online_retry_with_same_uuid_creates_one_active_entry(app, client):
    phone, secret, version, field, at = setup_self(app, client, limit=0)
    for _ in range(2):
        assert phone.post(API+'/entry',json={"module":"m1", "uuid":"11111111-1111-1111-1111-111111111111"},headers=_bearer(secret)).status_code == 200
    with app.app_context():
        rows=db.session.scalars(db.select(Response).filter_by(part='m1')).all()
        print('online retry: number of active entries=',len(rows))
        assert len(rows)==1 and rows[0].uuid == "11111111-1111-1111-1111-111111111111"


def test_uuid_cannot_be_reused_for_another_module(app, client):
    phone, secret, version, field, at = setup_self(app, client)
    uid = "11111111-1111-1111-1111-111111111111"
    assert phone.post(API+'/entry', json={"module":"m1", "uuid":uid}, headers=_bearer(secret)).status_code == 200
    assert phone.post(API+'/entry', json={"module":"entry", "uuid":uid}, headers=_bearer(secret)).status_code == 409
    assert phone.post(API+'/offline', json={"module":"entry", "uuid":uid}, headers=_bearer(secret)).status_code == 409


def test_timezone_history_is_in_study_payload(app, client):
    phone, secret, *_ = setup_self(app, client)
    with app.app_context():
        from app.models import Enrolment
        person = db.session.scalar(db.select(Enrolment))
        person.tz = "UTC"
        diary.set_tz(person, "America/Los_Angeles", now=datetime(2026,10,17,8))
        db.session.commit()
    reply = phone.post(API+'/study', json={}, headers=_bearer(secret)).json
    assert reply['tz_history'] == [{"from":None,"tz":"UTC"}, {"from":"2026-10-18","tz":"America/Los_Angeles"}]
