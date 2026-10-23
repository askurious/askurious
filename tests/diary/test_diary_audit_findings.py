"""Diary: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.helpers.audit_audit_2026_10_12_c import API, Enrolment, Response, SimpleNamespace, Survey, _activate, _active_from_today, _enrol, _new, _path, _prompt, date, datetime, db, diary, os, timedelta, timezone, utcnow  # noqa: F401
from tests.helpers.audit_cr8_h import _fake, ai, pytest  # noqa: F401


# --- from tests/audits/test_audit_2026_10_12_c.py: Audit data and calculations 2026-10-12, package C (diary study): E-1, E-2, D-1 to D-7, D-10, D-12 to D-15 and ---


def test_sweep_goes_the_way_of_an_erasure(app, client):
    from app import diary_api, snapshots  # noqa: F401

    code = _new(app, client)
    phone = app.test_client()
    joined = phone.post(API + "/enrol", json={"code": code, "lang": "de"})
    phone.get(_path(joined.json["url"]))  # the entry part begun, never finished
    with app.test_request_context():
        survey = db.session.get(Survey, 1)
        begun = db.session.scalar(db.select(Response))
        assert begun is not None and begun.status == Response.ACTIVE
        name = snapshots.save(survey)
        path = os.path.join(snapshots.folder(survey), name, "data.csv")
        with open(path, encoding="utf-8-sig") as f:
            assert begun.pid in f.read()
        pid = begun.pid
        assert diary.sweep(now=utcnow() + timedelta(days=diary.PENDING_DAYS + 1)) == 1
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
        assert db.session.scalar(db.select(db.func.count(Enrolment.id))) == 0
        with open(path, encoding="utf-8-sig") as f:
            assert pid not in f.read()


def test_a_gap_that_cannot_be_kept_is_named(app, client):
    trigger = {"per_day": 3, "windows": [["09:00", "10:00"]], "min_gap": 60}
    assert not diary.gap_kept(trigger)
    assert diary.gap_kept({"per_day": 3, "windows": [["09:00", "21:00"]], "min_gap": 60})
    _new(app, client)
    with app.test_request_context():
        survey = db.session.get(Survey, 1)
        m1 = {**diary.module(survey, "m1"), "trigger": {**trigger, "t": "random", "weekdays": [1, 2, 3, 4, 5, 6, 7]}}
        survey.sampling = {**survey.sampling, "modules": [m1]}
        db.session.commit()
        facts = diary.method_facts(survey)
        assert "ohne gesicherten Mindestabstand" in facts["de"] and "mindestens 60 Minuten" not in facts["de"]
        assert any("zu kurz" in w for w in diary.week_preview(survey)[1])


def test_end_stops_the_later_prompts_of_the_day(app, client):
    code = _new(app, client)
    _enrol(app, code)
    _activate(app, days_ago=1)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        person = db.session.scalar(db.select(Enrolment))
        tz = diary.zone(person.tz)
        later = (datetime.now(timezone.utc) + timedelta(minutes=30)).astimezone(tz)
        if later.date() != diary.local_today(person):
            return  # just before midnight: not to be checked
        m1 = {**diary.module(survey, "m1"), "expires": 10,
              "trigger": {"t": "fixed", "times": [later.strftime("%H:%M")], "weekdays": [1, 2, 3, 4, 5, 6, 7]}}
        survey.sampling = {**survey.sampling, "modules": [m1]}
        diary.end(person)
        db.session.commit()
        assert person.settings.get("ended_at")
        rows = diary.missed_rows(survey, [], now=utcnow() + timedelta(hours=2))
        due = later.astimezone(timezone.utc).replace(tzinfo=None, second=0, microsecond=0).isoformat(timespec="minutes")
        assert due not in [r["faellig"] for r in rows]
        assert diary.compliance(survey, person, now=utcnow() + timedelta(hours=2))[1] == 1  # yesterday's only


def test_random_prompts_keep_their_times_with_their_own_key(app):
    sampling = {"modules": [{"key": "m1", "trigger": {"t": "random", "per_day": 4, "windows": [["09:00", "21:00"]],
                                                     "min_gap": 60, "weekdays": [1, 2, 3, 4, 5, 6, 7]},
                             "expires": 30, "days": [1, None]}], "period_days": 7}
    survey = SimpleNamespace(sampling=sampling, status="running", closed_at=None)
    person = SimpleNamespace(pid="abc123", first_day=date(2026, 10, 5), last_day=date(2026, 10, 11), tz="Europe/Berlin",
                             settings={}, id=1)
    with app.app_context():
        app.config["DIARY_KEY"] = ""
        before = diary.prompts(survey, person)
        app.config["DIARY_KEY"] = app.secret_key  # set to the key of before: the times stay (the default)
        assert diary.prompts(survey, person) == before
        app.secret_key = "another-key-after-rotation"
        app.config["SECRET_KEY"] = app.secret_key
        assert diary.prompts(survey, person) == before  # a new SECRET_KEY moves nothing


def test_random_prompts_are_named_random(app, client):
    from app import export

    _new(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        m1 = {**diary.module(survey, "m1"), "trigger": {"t": "random", "per_day": 2, "windows": [["09:00", "21:00"]],
                                                         "min_gap": 60, "weekdays": [1, 2, 3, 4, 5, 6, 7]}}
        survey.sampling = {**survey.sampling, "modules": [m1]}
        db.session.commit()
        old = SimpleNamespace(occasion={"part": "m1", "trigger": "fixed"}, enrolment_id=1, started_at=None, status="completed")
        assert export._diary_values(old, {}, {"m1": "random"})["ausloeser"] == "random"
        selfish = SimpleNamespace(occasion={"part": "m1", "trigger": "self"}, enrolment_id=1, started_at=None, status="completed")
        assert export._diary_values(selfish, {}, {"m1": "random"})["ausloeser"] == "self"


def test_an_open_prompt_does_not_lower_compliance(app, client):
    code = _new(app, client)
    _enrol(app, code)
    _active_from_today(app)
    _prompt(app, minutes_ago=1, expires=60)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        person = db.session.scalar(db.select(Enrolment))
        assert diary.compliance(survey, person) == (0, 0)  # still open
        assert diary.compliance(survey, person, now=utcnow() + timedelta(hours=2)) == (0, 1)


def test_resume_on_the_same_day_keeps_the_pause_until_then(app, client):
    code = _new(app, client)
    _enrol(app, code)
    _active_from_today(app)
    _prompt(app, minutes_ago=30, expires=10)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        person = db.session.scalar(db.select(Enrolment))
        assert diary.compliance(survey, person) == (0, 1)
        diary.pause(person)
        diary.resume(person)
        db.session.commit()
        assert diary.compliance(survey, person) == (0, 0)  # paused until the resume a moment ago
        assert diary.missed_rows(survey, []) == []


# --- from tests/audits/test_cr8_h.py: Code review 2026-10-07, package H (AI calls and the external signature): I1 no SDK retries and limits below the ---


def test_i3_text_review_prompt_review_and_vignettes(app, client, survey, monkeypatch):
    from app import text_review, vignettes_ai

    with app.app_context():
        items = [i for b in db.session.get(Survey, 1).blocks for i in b.items]
        out = text_review._suggestions({"parts": [{"item": ["x"], "id": ["q"], "suggestion": "neu"}, "kaputt"]}, items)
        assert out == []
        assert text_review._suggestions({"parts": 7}, items[:1]) == []
        _fake(monkeypatch, '{"factors": [{"name": "Alter", "levels": "jung, alt"}]}')
        with pytest.raises(ai.AIError):  # a text instead of a list is no levels "j", "u", "n", "g"
            vignettes_ai.suggest_factors(db.session.get(Survey, 1), {"attributes": []})
