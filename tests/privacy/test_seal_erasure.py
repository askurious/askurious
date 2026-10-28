"""A participant deletes their own answers while the seal chain is on (app/seal.py): the chain gets its "deleted"
entry and the case's entries lose salt and participation id (seal._forget). Under the participants' own database
role (app/participant_grants.py) that needs UPDATE (salt, response_id) on seal_entry; without it the deletion was
refused and rolled back, and the answers stayed (2026-10-28). This folder is checked against GRANTS by
tests/conftest.py (_participant_sql), so the requests below fail here if a right is missing."""
from app import seal
from app.extensions import db
from app.models import Response, SealEntry, Survey
from tests.conftest import experiments_on
from tests.diary.test_diary import API, _bearer, _enrol, _new
from tests.privacy.test_erasure import CODE, build, stored, take


def _entries(app):
    with app.app_context():
        uid = db.session.get(Survey, 1).uid
        return [(e.kind, e.salt, e.response_id) for e in db.session.scalars(
            db.select(SealEntry).filter_by(survey_uid=uid).order_by(SealEntry.seq))]


def test_the_deletion_code_deletes_a_sealed_case_on_the_participants_side(app, client, survey):
    experiments_on(client, "seal")
    uid = build(app, client)
    client.post("/surveys/1/seal-chain")
    end = take(app.test_client(), uid, "meins")
    take(app.test_client(), uid, "anderes")
    assert [k for k, _s, _r in _entries(app)] == ["enabled", "sealed", "sealed"]
    code = CODE.search(end).group(1)
    done = app.test_client().post(f"/s/{uid}/delete", data={"sure": "1", "code": code}).data.decode()
    assert "unwiederbringlich gelöscht" in done
    assert stored(app) == ["anderes"]
    entries = _entries(app)
    assert entries[-1][0] == "deleted"
    assert entries[1][1:] == (None, None)                       # the deleted case: no salt, no participation id
    assert entries[2][1] is not None and entries[2][2] is not None  # the other case keeps both
    with app.app_context():
        assert seal.check(db.session.get(Survey, 1))["problems"] == []


def test_the_diary_app_deletes_a_sealed_person(app, client):
    code = _new(app, client)
    experiments_on(client, "seal")
    client.post("/surveys/1/seal-chain")
    phone, secret = _enrol(app, code)
    assert "sealed" in [k for k, _s, _r in _entries(app)]
    assert phone.post(API + "/erase", json={}, headers=_bearer(secret)).json["status"] == "erased"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
    entries = _entries(app)
    assert entries[-1][0] == "deleted" and all(r is None for _k, _s, r in entries)
