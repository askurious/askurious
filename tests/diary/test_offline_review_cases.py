"""Offline answers of a diary study: the clock check against the kept page, review cases in the compliance and the long
export, review cases without a kept page, and kept pages an answer may still wait for (audit Datenspeicherung und
Berechnungen 2026-10-21, F1, F2, F5, F6)."""
from datetime import datetime, timedelta, timezone

from app import diary
from app.extensions import db
from app.models import BrowserReceipt, Item, Response, Survey, utcnow
from tests.diary.test_diary import _active_from_today, _enrol, _new, _prompt, entry_person
from tests.diary.test_diary_integrity import _kept_page, _offline, _rating_field


def _setup(app, client, minutes_ago=20, expires=60):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    return phone, secret, _prompt(app, minutes_ago=minutes_ago, expires=expires)


def _receipt_at(app):
    with app.app_context():
        person = entry_person()
        row = db.session.scalar(db.select(BrowserReceipt).filter_by(scope=f"offline:{person.id}"))
        return datetime.fromisoformat(row.data["at"])


def _ms(moment):
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _latest():
    return db.session.scalar(db.select(Response).order_by(Response.id.desc()))


def test_a_phone_clock_a_little_behind_the_kept_page_is_tolerated(app, client):
    """F1: the page was fetched just now and the phone's clock lags 20 s: its start lies before the receipt's time.
    Before, that was 410 "expired" for good; now the clock may differ by LATE_SECONDS as everywhere else."""
    from app.diary_api import LATE_SECONDS

    phone, secret, due = _setup(app, client)
    version, field = _kept_page(app, phone, secret, fetched_hours_ago=0), _rating_field(app)
    at = _receipt_at(app)
    sent = _offline(phone, secret, version, field, due, _ms(at - timedelta(seconds=20)))
    assert sent.status_code == 200 and sent.json["status"] == "stored", sent.json
    # a start clearly before the page was fetched is still refused
    with app.app_context():
        db.session.delete(_latest())
        db.session.commit()
    early = _offline(phone, secret, version, field, due, _ms(at - timedelta(seconds=LATE_SECONDS + 60)))
    assert early.status_code == 410 and early.json["error"] == "expired"


def _review_case(app, client):
    """An offline answer with an invalid rating to a prompt that is over (due 90 minutes ago, open for 60)."""
    phone, secret, due = _setup(app, client, minutes_ago=90, expires=60)
    version, field = _kept_page(app, phone, secret, fetched_hours_ago=2), _rating_field(app)
    sent = _offline(phone, secret, version, field, due, due + 60_000, form={field: ["999"]})
    assert sent.status_code == 200 and sent.json["status"] == "review", sent.json
    return phone, secret, due, field


def test_a_review_case_is_neither_answered_nor_missed(app, client):
    """F2: the person answered, the answer waits as a review case. Before: compliance (0, 1) and "abgebrochen" in the
    long export; now it is left out of the rate, counted apart and marked "zur_pruefung"."""
    from app import export

    _review_case(app, client)
    with app.app_context():
        survey, person = db.session.get(Survey, 1), entry_person()
        assert diary.compliance(survey, person) == (0, 0)
        assert diary.compliance(survey, person, with_review=True) == (0, 0, 1)
        rows = diary.missed_rows(survey, [])
        assert [(r["teil"], r["abfrage"]) for r in rows] == [("m1", "zur_pruefung")]
        assert export._diary_values(_latest(), {})["abfrage"] == "zur_pruefung"
        facts = diary.method_facts(survey)
        assert "zur Prüfung" in facts["de"] and "under review" in facts["en"]


def test_an_accepted_review_case_counts_as_answered(app, client):
    _phone, _secret, _due, field = _review_case(app, client)
    with app.app_context():
        rid = _latest().id
    client.post("/diary/1/offline-reviews", data={"response": rid, field: "4", "clarification": "Typo, meant 4."})
    with app.app_context():
        assert db.session.get(Response, rid).status == Response.COMPLETED
        assert diary.compliance(db.session.get(Survey, 1), entry_person(), with_review=True) == (1, 1, 0)


def _unpaged_case(app, client):
    """F5: an older app without a kept page sends an invalid value against the current questionnaire."""
    from app import runtime

    phone, secret, due = _setup(app, client)
    field = _rating_field(app)
    with app.app_context():
        signature = runtime._signature(db.session.get(Survey, 1))
    sent = _offline(phone, secret, signature, field, due, due + 60_000, form={field: ["999"]})
    assert sent.status_code == 200 and sent.json["status"] == "review", sent.json
    with app.app_context():
        row = _latest()
        assert row.occasion["review"]["page"] is None and row.occasion["review"]["version"] == signature
        return row.id, field


def test_a_review_case_without_a_kept_page_can_be_accepted_while_the_questionnaire_is_unchanged(app, client):
    """F5: before, such a case always got "Diese Fassung passt nicht zum aktuellen Fragebogen" and stayed excluded."""
    rid, field = _unpaged_case(app, client)
    page = client.get("/diary/1/offline-reviews").get_data(as_text=True)
    assert "Ohne gespeicherten Seitenstand (ältere App) gesendet" in page
    client.post("/diary/1/offline-reviews", data={"response": rid, field: "4", "clarification": "Confirmed: 4."})
    with app.app_context():
        row = db.session.get(Response, rid)
        assert row.status == Response.COMPLETED and row.answers[field[1:]] == 4


def test_a_review_case_without_a_kept_page_says_why_it_cannot_be_accepted_after_a_change(app, client):
    rid, field = _unpaged_case(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        block = next(b for b in survey.blocks if diary.part_of(b) == "m1")
        db.session.add(Item(block_id=block.id, type="info", code="notice", position=9, config={},
                            texts={"de": {"question": "Neu"}}))
        db.session.commit()
    page = client.get("/diary/1/offline-reviews").get_data(as_text=True)
    assert "Sie lässt sich daher nicht übernehmen" in page
    answer = client.post("/diary/1/offline-reviews", data={"response": rid, field: "4", "clarification": "x"},
                         follow_redirects=True).get_data(as_text=True)
    assert "Ohne gespeicherten Seitenstand lässt sich diese Antwort nicht übernehmen" in answer
    with app.app_context():
        assert db.session.get(Response, rid).status == "review"


def test_kept_pages_an_answer_may_wait_for_are_not_trimmed(app, client):
    """F6: more than MAX_PAGES pages fetched within OFFLINE_DAYS (many modules) all stay; older ones beyond the limit
    still go."""
    from app import offline_answers

    _setup(app, client)
    with app.app_context():
        survey, person = db.session.get(Survey, 1), entry_person()
        scope = offline_answers.scope(person)
        now = utcnow()
        recent = [BrowserReceipt(survey_id=survey.id, scope=scope, key=f"r{n:03d}", data={"part": "m1"},
                                 expires_at=now + timedelta(days=30, minutes=-n)) for n in range(70)]
        old = [BrowserReceipt(survey_id=survey.id, scope=scope, key=f"o{n:03d}", data={"part": "m1"},
                              expires_at=now + timedelta(days=20, minutes=-n)) for n in range(70)]
        new = BrowserReceipt(survey_id=survey.id, scope=scope, key="new", data={"part": "m1"},
                             expires_at=now + timedelta(days=30))
        db.session.add_all(recent + old + [new])
        db.session.flush()
        offline_answers._keep_newest(survey, person, new)
        db.session.commit()
        keys = set(db.session.scalars(db.select(BrowserReceipt.key).filter_by(scope=scope)))
        assert {r.key for r in recent} <= keys
        assert len({o.key for o in old} & keys) == offline_answers.MAX_PAGES - 1
