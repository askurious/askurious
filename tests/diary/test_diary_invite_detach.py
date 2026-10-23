"""A personal invitation code lets go of its person when the person is deleted, and SQLite never gives a deleted
person's id to the next one: an old code (a printed card, a release left on the list) can never connect a later person
to someone else's participation (audit 2026-10-23, S-01)."""
from datetime import timedelta

from app import diary
from app.extensions import db
from app.models import DiaryInvite, Enrolment, Survey, utcnow
from tests.diary.test_diary import API, _new
from tests.diary.test_diary_rejoin import _codes, _personal


def _two_codes(app, client):
    code = _new(app, client)
    _personal(client)
    client.post("/diary/1/invites", data={"n": "2"})
    first, second = _codes(app, code)
    return code, first, second


def _invite(app, c):
    with app.app_context():
        invite = db.session.scalar(db.select(DiaryInvite).filter_by(code=c))
        return invite.enrolment_id, invite.released_at


def test_the_sweep_lets_the_code_go_and_the_next_person_gets_a_new_id(app, client):
    """audit 2026-10-23, S-01: the hourly sweep deletes a person who never finished the entry part; the released code
    named the next person afterwards, who got the same id on SQLite."""
    code, first, second = _two_codes(app, client)
    assert app.test_client().post(API + "/enrol", json={"code": f"{code}-{first}"}).status_code == 200
    client.post(f"/diary/1/invites/{first}/release")
    with app.app_context():
        gone_id = db.session.scalar(db.select(Enrolment.id))
        assert diary.sweep(utcnow() + timedelta(days=diary.PENDING_DAYS + 1)) == 1
        assert db.session.scalar(db.select(db.func.count(Enrolment.id))) == 0
    assert _invite(app, first) == (None, None)  # no person, no release left
    assert app.test_client().post(API + "/enrol", json={"code": f"{code}-{second}"}).status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(Enrolment.id)) > gone_id  # never the id of the person who went
    stranger = app.test_client()
    assert stranger.get(API + f"/join/{code}-{first}").json["error"] == "used"
    assert stranger.post(API + "/enrol", json={"code": f"{code}-{first}"}).json["error"] == "used"
    assert client.post(f"/diary/1/invites/{first}/release").status_code == 404  # nobody to connect again


def test_deleting_the_persons_of_a_study_lets_the_codes_go(app, client):
    """audit 2026-10-23, S-01: retention (diary.forget without the codes) deleted the persons past the mapper events
    and left the codes naming their ids."""
    code, first, second = _two_codes(app, client)
    app.test_client().post(API + "/enrol", json={"code": f"{code}-{first}"})
    client.post(f"/diary/1/invites/{first}/release")
    with app.app_context():
        gone_id = db.session.scalar(db.select(Enrolment.id))
        diary.forget(1)
        db.session.commit()
    assert _invite(app, first) == (None, None)
    app.test_client().post(API + "/enrol", json={"code": f"{code}-{second}"})
    with app.app_context():
        assert db.session.scalar(db.select(Enrolment.id)) > gone_id
        assert [i["pid"] for i in diary.invites(db.session.get(Survey, 1))] == [None, db.session.scalar(db.select(Enrolment.pid))]


def test_a_reference_left_from_before_never_connects_a_later_person(app, client):
    """audit 2026-10-23, S-01: a code that still names an id now held by someone who joined after the code was used
    (left from a deletion before this fix) neither shows nor connects that person, and the sweep lets it go once."""
    code, first, second = _two_codes(app, client)
    app.test_client().post(API + "/enrol", json={"code": f"{code}-{second}"})
    with app.app_context():
        later = db.session.scalar(db.select(Enrolment))
        invite = db.session.scalar(db.select(DiaryInvite).filter_by(code=first))
        invite.used_at, invite.enrolment_id, invite.released_at = later.created_at - timedelta(days=3), later.id, utcnow()
        db.session.commit()
        survey = db.session.get(Survey, 1)
        assert diary._rejoin_person(survey, invite) is None
        assert diary.invites(survey)[0]["pid"] is None
    assert app.test_client().get(API + f"/join/{code}-{first}").json["error"] == "used"
    with app.app_context():
        diary.sweep()
        assert diary.detach_dangling_invites() == 0  # nothing left a second time
    assert _invite(app, first) == (None, None)
    assert _invite(app, second)[0] is not None  # the code of the person who is there keeps them
