"""Integrity of the entries of a diary study (audit 2026-10-11, M1, M2, M3, M5, N4, N5, N7; decisions 1a and 2a): the
server's time of arrival and the rules for entries sent later, one entry per prompt, the app's uuid, time zones per
day, invitation codes in a table, a ceiling for "0 = ohne Grenze", and nothing due after leaving or closing."""
import uuid as uuids
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app import diary
from app.extensions import db
from app.models import DiaryInvite, Enrolment, Response, Survey, utcnow
from tests.diary.test_diary import API, _active_from_today, _bearer, _enrol, _new, _prompt, entry_person


def _rating_field(app):
    with app.app_context():
        item = next(i for b in db.session.get(Survey, 1).blocks for i in b.items if i.type == "rating")
        return f"i{item.id}"


def _kept_page(app, phone, secret, fetched_hours_ago=1):
    """The offline page of m1, as if the phone had fetched it `fetched_hours_ago` hours ago."""
    kept = phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret)).json
    with app.app_context():
        person = entry_person()
        assert person.settings["offline_at"]  # the server notes when the page was fetched
        person.settings = {**person.settings, "offline_at": (utcnow() - timedelta(hours=fetched_hours_ago)).isoformat()}
        from app.models import BrowserReceipt
        for receipt in db.session.scalars(db.select(BrowserReceipt).filter_by(survey_id=person.survey_id,
                                                                              scope=f"offline:{person.id}")):
            receipt.data = {**receipt.data, "at": person.settings["offline_at"]}
        db.session.commit()
    return kept["version"]


def _offline(phone, secret, version, field, due, started, **more):
    sent = {"module": "m1", "due": due, "notified": due, "started": started, "completed": started + 30_000,
            "version": version, "form": {field: ["4"], "nav": ["next"]}, **more}
    return phone.post(f"{API}/offline", json=sent, headers=_bearer(secret))


def test_an_entry_sent_later_is_kept_with_the_arrival_and_marked(app, client):
    from app import export

    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app, minutes_ago=20)
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    before = utcnow().replace(microsecond=0)
    assert _offline(phone, secret, version, field, due, due + 60_000).json["status"] == "stored"
    with app.app_context():
        entry = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
        assert entry.received_at >= before and entry.received_at > entry.completed_at  # the server's own time
        first = db.session.get(Response, 1)
        assert first.received_at == first.completed_at  # answered online: arrival = completion on the server
        survey = db.session.get(Survey, 1)
        rows = [r for r in export.rows(survey) if r.get("teil") == "m1"]
        assert rows[0]["nachgereicht"] == 1 and rows[0]["eingang"] == entry.received_at.isoformat(timespec="minutes")
        assert "eingang" in export.codebook_csv(survey)
    page = client.get("/surveys/1/quality").get_data(as_text=True)
    assert "Nachgereichte Einträge" in page and "1 von 1" in page


def test_entries_sent_later_need_a_start_before_expiry_and_after_the_page(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app, minutes_ago=120, expires=30)
    version, field = _kept_page(app, phone, secret, fetched_hours_ago=3), _rating_field(app)
    # online the prompt is over; sent later with a made-up start shortly after the prompt it is taken only if true:
    assert phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret)).status_code == 410
    assert _offline(phone, secret, version, field, due, due + 45 * 60_000).status_code == 410  # after the expiry
    # a start before the phone fetched the page cannot be (the phone fetched it an hour ago)
    version = _kept_page(app, phone, secret, fetched_hours_ago=1)
    assert _offline(phone, secret, version, field, due, due + 60_000).json["error"] == "expired"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1


def test_one_prompt_makes_one_entry_also_with_seconds(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app)
    urls = [phone.post(API + "/entry", json={"module": "m1", "due": due + k * 1000}, headers=_bearer(secret))
            for k in (0, 1, 2, 59)]
    assert [u.status_code for u in urls] == [200] * 4
    with app.app_context():
        entries = db.session.scalars(db.select(Response).filter_by(part="m1")).all()
        assert len(entries) == 1 and entries[0].due_at.second == 0
        assert entries[0].occasion["due"].endswith(":00")
        person = entry_person()
        # the database itself refuses a second row of the same prompt (a race of two requests)
        twin = Response(survey_id=1, enrolment_id=person.id, occasion={"part": "m1"}, due_at=entries[0].due_at,
                        answers={}, pid="twin-1")
        db.session.add(twin)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()


def test_compliance_counts_each_prompt_once(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app)
    with app.app_context():
        person = entry_person()
        at = datetime.fromtimestamp(due / 1000, timezone.utc).replace(tzinfo=None)
        for k in (0, 1):  # two entries of one prompt, as the rule before the audit let through
            db.session.add(Response(survey_id=1, enrolment_id=person.id, status=Response.COMPLETED, answers={},
                                    pid=f"old-{k}", occasion={"part": "m1", "due": (at + timedelta(seconds=k)).isoformat()},
                                    completed_at=utcnow()))
        db.session.commit()
        answered, due_n = diary.compliance(db.session.get(Survey, 1), entry_person())
        assert answered == 1 <= due_n


def test_the_same_uuid_stores_once(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app, minutes_ago=20)
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    uid = str(uuids.uuid4())
    for _ in range(3):  # sent again after a lost reply
        assert _offline(phone, secret, version, field, due, due + 60_000, uuid=uid).json["status"] == "stored"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(part="m1")) == 1
        assert db.session.scalar(db.select(Response.uuid).filter_by(part="m1")) == uid
    # an entry address: the same uuid gives the same entry; a malformed uuid counts as none
    with app.app_context():
        survey = db.session.get(Survey, 1)
        m2 = {"key": "m2", "name": {"de": "M"}, "trigger": {"t": "self", "label": {}, "max_per_day": 0}, "expires": 0,
              "days": [1, None], "notify": {}}
        survey.sampling = {**survey.sampling, "modules": diary.modules(survey) + [m2]}
        db.session.commit()
    other = str(uuids.uuid4())
    one = phone.post(API + "/entry", json={"module": "m2", "uuid": other}, headers=_bearer(secret))
    two = phone.post(API + "/entry", json={"module": "m2", "uuid": other.upper()}, headers=_bearer(secret))
    assert one.status_code == two.status_code == 200
    with app.app_context():  # sent off: a new start begins a new entry (an open one would be continued, T07-N-1)
        db.session.execute(db.update(Response).where(Response.part == "m2").values(status=Response.COMPLETED))
        db.session.commit()
    phone.post(API + "/entry", json={"module": "m2", "uuid": "kein-uuid"}, headers=_bearer(secret))
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(part="m2")) == 2


def test_a_new_time_zone_counts_from_the_next_day(app, client):
    """Decision 2a: a trip does not rewrite the days before it (the repro: compliance 12/12 fell to 0/11)."""
    from app import export

    code = _new(app, client)
    phone, secret = _enrol(app, code)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        person = entry_person()
        person.first_day = diary.local_today(person) - timedelta(days=4)
        person.last_day = person.first_day + timedelta(days=13)
        person.status = Enrolment.ACTIVE
        db.session.commit()
        for n, (key, due) in enumerate(diary.prompts(survey, person, until=utcnow())):
            db.session.add(Response(survey_id=survey.id, enrolment_id=person.id, status=Response.COMPLETED, answers={},
                                    pid=f"e-{n}", started_at=due, completed_at=due + timedelta(minutes=2), due_at=due,
                                    occasion={"part": key, "due": due.isoformat(), "tz": person.tz}))
        db.session.commit()
        before = diary.compliance(survey, entry_person())
        missed_before = len(diary.missed_rows(survey, export.rows(survey)))
        assert before[0] == before[1] >= 12
    assert phone.post(API + "/study", json={"tz": "America/Los_Angeles"}, headers=_bearer(secret)).status_code == 200
    with app.app_context():
        survey = db.session.get(Survey, 1)
        person = entry_person()
        history = person.settings["tz"]
        today = diary.local_today(person)  # still in the old zone: the new one counts from tomorrow
        assert history == [{"from": None, "tz": "Europe/Berlin"},
                           {"from": (today + timedelta(days=1)).isoformat(), "tz": "America/Los_Angeles"}]
        assert diary.zone_on(person, today).key == "Europe/Berlin"
        assert diary.compliance(survey, person) == before  # nothing answered became missed
        assert len(diary.missed_rows(survey, export.rows(survey))) == missed_before
        # from the next day on, the prompts come at the same local times in the new zone
        day = today + timedelta(days=2)
        later = [due for _, due in diary.prompts(survey, person) if diary.prompt_day(person, due) == day]
        local = [d.replace(tzinfo=timezone.utc).astimezone(diary.zone("America/Los_Angeles")).strftime("%H:%M") for d in later]
        assert local == ["09:00", "14:00", "19:00"]


def test_an_invitation_code_joins_once_even_at_the_same_moment(app, client):
    code = _new(app, client)
    client.post("/diary/1/invites", data={"n": "2"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        codes = [i["c"] for i in diary.invites(survey)]
        assert db.session.scalar(db.select(db.func.count(DiaryInvite.id))) == 2
        assert "invites" not in (survey.sampling or {})  # the schedule is not written by a join
        survey.sampling = {**survey.sampling, "rejoin": "release"}  # a used code does not connect again unreleased
        person = Enrolment(survey_id=1, secret_hash="x" * 64, pid="p-race", status=Enrolment.PENDING)
        db.session.add(person)
        db.session.flush()
        assert diary.use_invite(survey, f"{code}-{codes[0]}", person) is True
        assert diary.use_invite(survey, f"{code}-{codes[0]}", person) is False  # the second of two joins loses
        db.session.commit()
    used = app.test_client().post(API + "/enrol", json={"code": f"{code}-{codes[0]}"})
    assert used.status_code == 409 and used.json["error"] == "used"
    assert app.test_client().post(API + "/enrol", json={"code": f"{code}-{codes[1]}"}).status_code == 200
    with app.app_context():
        from app import lifecycle

        lifecycle.delete(db.session.get(Survey, 1), force=True)
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(DiaryInvite.id))) == 0


def test_without_a_limit_a_self_started_module_still_has_a_ceiling(app, client, monkeypatch):
    from app import diary_api

    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        m1 = {**diary.module(survey, "m1"), "trigger": {"t": "self", "label": {}, "max_per_day": 0}}
        survey.sampling = {**survey.sampling, "modules": [m1]}
        db.session.commit()
    monkeypatch.setattr(diary_api, "SELF_CEILING", 3)
    from tests.diary.test_diary import _send_open_reports

    answers = []
    for _ in range(4):
        answers.append(phone.post(API + "/entry", json={"module": "m1"}, headers=_bearer(secret)).status_code)
        _send_open_reports(app)  # a report counts once it is sent off (Abgleich 2026-10-15, T07-N-1)
    assert answers == [200, 200, 200, 409]  # the daily limit (409 "daily_limit" since V4)


def test_nothing_is_due_after_leaving_or_closing(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    with app.app_context():
        person = entry_person()
        person.first_day = diary.local_today(person) - timedelta(days=6)
        person.last_day = person.first_day + timedelta(days=13)
        person.status = Enrolment.ACTIVE
        db.session.commit()
        due_now = diary.compliance(db.session.get(Survey, 1), entry_person())[1]
    assert phone.post(API + "/leave", json={}, headers=_bearer(secret)).json["status"] == "left"
    with app.app_context():
        person = entry_person()
        survey = db.session.get(Survey, 1)
        assert diary.compliance(survey, person, now=utcnow() + timedelta(days=5))[1] == due_now
        person.settings = {**person.settings, "left_at": (utcnow() - timedelta(days=3)).isoformat()}
        db.session.commit()
        assert diary.compliance(survey, entry_person())[1] < due_now
    assert phone.post(API + "/entry", json={"module": "m1"}, headers=_bearer(secret)).json["error"] == "ended"


def test_nothing_comes_in_after_data_collection_ended(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        # due once over (audit 2026-10-12, D-15): the prompt of a minute ago expires within the hour
        assert diary.compliance(survey, entry_person(), now=utcnow() + timedelta(hours=2))[1] == 1
        survey.status, survey.closed_at = Survey.CLOSED, utcnow() - timedelta(minutes=30)
        db.session.commit()
        assert diary.compliance(survey, entry_person())[1] == 0  # the prompt a minute ago came after the end
    out = phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret))
    assert out.status_code == 409 and out.json["error"] == "closed"
    assert phone.post(API + "/offline", json={"module": "m1"}, headers=_bearer(secret)).json["error"] == "closed"
