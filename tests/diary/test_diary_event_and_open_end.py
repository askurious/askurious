"""Diary studies: a module is either timed or started on an event (a study may have both kinds; Philipp, 2026-10-15,
before, a timed module could also carry a button), a study period without end, and the sentence about the study in
the app (app/diary.py: self_start, open_end, summary_of; Philipp, 2026-10-13)."""
from datetime import datetime, timedelta

from app import diary
from app.extensions import db
from app.models import Block, Enrolment, Response, Survey
from tests.diary.test_diary import API, _active_from_today, _bearer, _enrol, _new, _prompt

FORM = {"period_days": "14", "start": "next_day", "m1_name": "Moment", "m1_trigger": "fixed", "m1_times": "09:00",
        "m1_weekdays": ["1", "2", "3", "4", "5", "6", "7"], "m1_expires": "60", "m1_day_from": "1", "m1_day_to": ""}


def test_a_module_is_either_timed_or_started_on_an_event(app, client):
    _new(app, client, running=False)
    # the box "Bei Ereignis" is gone: a timed module stays timed, whatever a stale form sends along
    client.post("/diary/1/schedule", data={**FORM, "m1_event": "1", "m1_label": "Gerade passiert", "m1_max_per_day": "2"})
    with app.app_context():
        m1 = diary.module(db.session.get(Survey, 1), "m1")
        assert m1["trigger"]["t"] == "fixed" and "self" not in m1 and diary.self_start(m1) is None
    page = client.get("/diary/1/schedule").get_data(as_text=True)
    assert "data-event-check" not in page and 'name="m1_event"' not in page
    # a module that is not timed is one on an event, with the label of its button
    client.post("/diary/1/schedule", data={**FORM, "m1_trigger": "self", "m1_label": "Melden"})
    with app.app_context():
        m1 = diary.module(db.session.get(Survey, 1), "m1")
        assert m1["trigger"]["t"] == "self" and "self" not in m1 and diary.self_start(m1)["label"] == {"de": "Melden"}
    page = client.get("/diary/1/schedule").get_data(as_text=True)
    assert "Kurz halten" in page  # the hint to a short label for small screens


def test_a_button_stored_on_a_timed_module_before_is_no_longer_read(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app)
    assert phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret)).status_code == 200
    with app.app_context():
        survey = db.session.get(Survey, 1)
        m1 = {**diary.module(survey, "m1"), "self": {"label": {"de": "Jetzt melden"}, "max_per_day": 2}}
        survey.sampling = {**survey.sampling, "modules": [m1]}
        db.session.commit()
        assert diary.self_start(m1) is None
    study = phone.post(API + "/study", json={}, headers=_bearer(secret)).json
    assert study["modules"][0]["trigger"]["t"] == "fixed" and "self" not in study["modules"][0]
    assert app.test_client().get(f"{API}/join/{code}").json["modules"][0]["self"] is False
    # timed only: no entry without a prompt
    assert phone.post(API + "/entry", json={"module": "m1"}, headers=_bearer(secret)).status_code == 400


def test_a_study_without_end_runs_until_the_researchers_end_it(app, client):
    code = _new(app, client, running=False)
    client.post("/diary/1/schedule", data={**FORM, "period_kind": "open", "summary": "Kurz am Tag, so lange Sie mögen."})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert diary.open_end(survey) and survey.sampling["period_days"] == 14
        assert diary.summary_of(survey, "de") == "Kurz am Tag, so lange Sie mögen."
        block = Block(position=len(survey.blocks), titles={"de": "Abschluss"}, settings={"part": "exit"})
        block.items = [diary._mood_item(survey, "de")]
        survey.blocks.append(block)
        survey.status = Survey.RUNNING
        db.session.commit()
    page = client.get("/diary/1/schedule").get_data(as_text=True)
    assert '<option value="open" selected>' in page
    info = app.test_client().get(f"{API}/join/{code}").json
    assert info["open_end"] is True and info["period_days"] == 0 and info["description"] == "Kurz am Tag, so lange Sie mögen."
    phone, secret = _enrol(app, code)
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        assert diary.refresh(db.session.get(Survey, 1), person) == Enrolment.ACTIVE
        assert person.first_day and person.last_day is None
        # far past the 14 days: still active, with prompts every day up to today
        person.first_day -= timedelta(days=40)
        entered = datetime.fromisoformat(person.settings["entered_at"]) - timedelta(days=40)
        person.settings = {**person.settings, "entered_at": entered.isoformat()}  # joined 40 days ago (G1)
        db.session.commit()
        survey = db.session.get(Survey, 1)
        assert diary.refresh(survey, person) == Enrolment.ACTIVE
        days = {d for _, _, d in diary._schedule(survey, person)}
        assert person.first_day in days and len(days) >= 40
        assert max(days) <= diary.local_today(person) + timedelta(days=1)
    study = phone.post(API + "/study", json={}, headers=_bearer(secret)).json
    assert study["open_end"] is True and study["last_day"] is None and study["status"] == "active"
    assert study["summary"] == "Kurz am Tag, so lange Sie mögen." and study["exit"] is None
    # the researchers end it: the last day is today and the exit part opens
    with app.app_context():
        pid = db.session.scalar(db.select(Enrolment)).id
    client.post(f"/diary/1/person/{pid}/end")
    study = phone.post(API + "/study", json={}, headers=_bearer(secret)).json
    assert study["status"] == "ended" and study["last_day"] is not None and study["exit"]["open"] is True


def test_the_consent_and_the_copy_know_the_study_without_end(app, client):
    _new(app, client, running=False)
    client.post("/diary/1/schedule", data={**FORM, "period_kind": "open", "summary": "Ein Satz."})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        kept = diary.clean(survey.sampling)
        assert kept["open_end"] is True and kept["summary"] == {"de": "Ein Satz."}
        assert "einmal, wenn Sie die Teilnahme der Person beenden" in diary.when_asked(diary.EXIT, survey)
        assert "ohne festes Ende" in diary.method_facts(survey)["de"]
        # the consent: no fixed number of days
        consent = next(i for b in survey.blocks for i in b.items if i.type == "consent")
        body = consent.kind.template_body(consent, "de")
        assert "ohne festes Ende: bis die Forschenden sie beenden" in body and "für jede Person 14 Tage" not in body
        assert "manche können Sie auch selbst beginnen" not in body
        assert "with no fixed end" in consent.kind.template_body(consent, "en")
        assert "oder du aussteigst" in consent.kind.template_body(consent, "de-du")
        assert "в приложении «Askurious»" in consent.kind.template_body(consent, "ru")
    # switching back to days keeps the number of days
    client.post("/diary/1/schedule", data={**FORM, "period_kind": "days"})
    with app.app_context():
        assert not diary.open_end(db.session.get(Survey, 1))
