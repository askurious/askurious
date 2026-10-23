"""Guessing the codes of a diary study from many senders (audit 2026-10-18, BIZ-07): the test codes have ten secret
characters (49 bits) instead of five, wrong test and invitation codes count per study for all senders together
(STUDY_CODE_LIMIT), and wrong codes of an IPv6 sender count per /48 as well (CODE_WIDE_LIMIT): a hoster's /48 holds
65 536 senders of their own for the limit per /64."""
from app import diary, limits
from app.extensions import db
from app.models import Survey
from tests.diary.test_diary import API, _new


def _fresh_process():
    for store in (limits._asked, limits._fallback, limits._load_fallback):
        store.clear()


def test_test_codes_are_long(app, client):
    _new(app, client)
    with app.test_request_context():
        survey = db.session.get(Survey, 1)
        test, fast = diary.test_code(survey), diary.test_code(survey, fast=True)
    assert len(test.split("-")[1]) >= 10 and len(fast.split("-")[1]) >= 10
    assert diary.TEST_CODE.match(test) and diary.TEST_CODE.match(fast)
    assert app.test_client().get(f"{API}/join/{test}").json["test"] is True
    assert app.test_client().get(f"{API}/join/{fast}").json["test"] is True


def test_wrong_codes_of_a_study_count_for_all_senders(app, client):
    code = _new(app, client)
    app.config.update(STUDY_CODE_LIMIT=5, CODE_LIMIT=100)
    with app.test_request_context():
        test = diary.test_code(db.session.get(Survey, 1))
    answers = []
    for n in range(6):  # six senders of their own, one wrong code each
        _fresh_process()
        answers.append(app.test_client().get(f"{API}/join/{code}-{n:010d}",
                                             environ_base={"REMOTE_ADDR": f"2001:db8:{n:x}:1::1"}).status_code)
    assert answers == [404] * 5 + [429]
    # past the limit the study's test code waits too, the study's own code does not
    assert app.test_client().get(f"{API}/join/{test}", environ_base={"REMOTE_ADDR": "203.0.113.70"}).status_code == 429
    assert app.test_client().get(f"{API}/join/{code}", environ_base={"REMOTE_ADDR": "203.0.113.70"}).status_code == 200
    assert app.test_client().post(f"{API}/enrol", json={"code": test}).status_code == 429
    assert app.test_client().get(f"/j/{test}", environ_base={"REMOTE_ADDR": "203.0.113.71"}).status_code == 429


def test_wrong_codes_of_one_ipv6_48_are_added_up(app, client):
    _new(app, client)
    app.config.update(CODE_LIMIT=100, CODE_WIDE_LIMIT=3, STUDY_CODE_LIMIT=0)
    answers = []
    for n in range(4):  # four /64 of the same /48
        _fresh_process()
        answers.append(app.test_client().get(f"{API}/join/nosuchcode{n}",
                                             environ_base={"REMOTE_ADDR": f"2001:db8:7:{n:x}::1"}).status_code)
    assert answers == [404, 404, 404, 429]
    other = app.test_client().get(f"{API}/join/nosuchcode", environ_base={"REMOTE_ADDR": "2001:db8:8::1"})
    assert other.status_code == 404  # another /48 goes on
    assert app.test_client().get(f"{API}/join/nosuchcode", environ_base={"REMOTE_ADDR": "198.51.100.9"}).status_code == 404
