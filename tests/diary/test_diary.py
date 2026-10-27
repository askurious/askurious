"""Diary studies (app/diary.py, app/diary_api.py, concepts/experiencesampling.md): creating one, the schedule, and the
way of the Android app: join, enrol, entry part, study, entries of a module, leaving, deleting a person."""
from datetime import timedelta, timezone

from app import diary, theme
from app.extensions import db
from app.models import Block, Enrolment, Response, Survey, utcnow
from tests.conftest import CONSENT_OK, experiments_on

API = "/api/diary/v1"


def _new(app, client, running=True):
    page = client.post("/diary/new", data={"title": "Lernen im Alltag", "language": "de"})
    assert page.status_code == 302 and "/setup/researchdesign" in page.headers["Location"]  # on into the wizard
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert survey.kind == "diary" and diary.modules(survey)[0]["key"] == "m1"
        if running:
            survey.status = Survey.RUNNING
            db.session.commit()
        return survey.code


def _bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def _path(url):
    from urllib.parse import urlsplit

    return urlsplit(url).path


def _fields(page):
    """A filled form for a participant page: the first option of each choice, 30 in each text or number field."""
    import re

    data = {}
    for tag in re.findall(r"<input[^>]*>", page):
        name = re.search(r'name="([^"]+)"', tag)
        kind = re.search(r'type="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if not name or name.group(1) in ("csrf_token", "page_lang", "more_info"):
            continue
        kind = kind.group(1) if kind else "text"
        if kind in ("radio", "checkbox", "hidden"):
            data.setdefault(name.group(1), value.group(1) if value else "1")
        elif kind in ("text", "number"):
            data.setdefault(name.group(1), "30")
    return data


def _finish(app, phone, code, first=None):
    """Answers the pages of the open participation of this browser until it ends."""
    for n in range(8):
        page = phone.get(f"/s/{code}").get_data(as_text=True)
        phone.post(f"/s/{code}", data={**_fields(page), **(first if n == 0 and first else {}), "nav": "next"})
        with app.app_context():
            r = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
            if r.status != Response.ACTIVE:
                return r
    raise AssertionError("the participation did not end")


def _enrol(app, code):
    phone = app.test_client()
    joined = phone.post(API + "/enrol", json={"code": code, "lang": "de"})
    assert joined.status_code == 200
    secret, url = joined.json["secret"], joined.json["url"]
    assert phone.get(_path(url)).status_code == 302
    entry = _finish(app, phone, code, first={**CONSENT_OK, "i1_c_age": "1"})
    assert entry.status == Response.COMPLETED and entry.occasion["part"] == "entry"
    return phone, secret


def test_a_diary_study_is_made_with_an_entry_part_and_a_module(app, client):
    code = _new(app, client, running=False)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        parts = {diary.part_of(b) for b in survey.blocks}
        assert parts == {"entry", "m1"}
        assert survey.repeat_marker is False
    assert client.get("/diary/1/schedule").status_code == 200
    assert "Tagebuchstudie" in client.get("/dashboard/").get_data(as_text=True)
    # the app hears nothing of it before data collection runs
    assert client.get(f"{API}/join/{code}").status_code == 409


def test_the_schedule_is_saved_and_a_module_added(app, client):
    _new(app, client, running=False)
    form = {"period_days": "7", "start": "same_day", "m1_name": "Moment", "m1_trigger": "fixed",
            "m1_times": "8:00, 20:30, 25:00, x", "m1_weekdays": ["1", "3"], "m1_expires": "30", "m1_day_from": "2",
            "m1_day_to": "", "m1_notify": "Kurz Zeit?"}
    client.post("/diary/1/schedule", data=form)
    client.post("/diary/1/schedule/module")
    with app.app_context():
        survey = db.session.get(Survey, 1)
        m1, m2 = diary.modules(survey)
        assert survey.sampling["period_days"] == 7 and survey.sampling["start"] == "same_day"
        assert m1["trigger"] == {"t": "fixed", "times": ["08:00", "20:30"], "weekdays": [1, 3]}
        assert m1["expires"] == 30 and m1["days"] == [2, None] and m1["notify"]["de"] == "Kurz Zeit?"
        assert m2["trigger"]["t"] == "self"
        block = next(b for b in survey.blocks if diary.part_of(b) == "m1")
        bid = block.id
    client.post("/diary/1/schedule", data={**form, f"part_{bid}": "m2", "m2_trigger": "self",
                                                  "m2_name": "Lernen", "m2_label": "Ich habe gelernt", "m2_max_per_day": "2"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert diary.part_of(db.session.get(Block, bid)) == "m2"
        assert diary.module(survey, "m2")["trigger"] == {"t": "self", "label": {"de": "Ich habe gelernt"}, "max_per_day": 2}


def test_the_app_joins_answers_the_entry_part_and_gets_the_study(app, client):
    code = _new(app, client)
    info = app.test_client().get(f"{API}/join/{code.upper()}").json
    assert info["title"] == "Lernen im Alltag" and info["period_days"] == 14
    assert info["modules"][0]["per_day"] == 3
    assert app.test_client().get(f"{API}/join/zzzzzz").status_code == 404

    phone, secret = _enrol(app, code)
    study = phone.post(API + "/study", json={"tz": "Europe/Berlin"}, headers=_bearer(secret)).json
    assert study["status"] == "active" and study["modules"][0]["trigger"]["times"] == ["09:00", "14:00", "19:00"]
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        assert person.first_day == diary.local_today(person)  # at once by default (diary.defaults)
        assert (person.last_day - person.first_day).days == 13
    assert phone.post(API + "/study", json={}, headers=_bearer("wrong")).status_code == 401
    # the entry part is done: it is not offered again
    assert phone.post(API + "/entry", json={"module": "entry"}, headers=_bearer(secret)).json["error"] == "done"


def test_the_app_gets_the_colours_of_the_survey(app, client):
    """The app wears the survey's look on its own screens (study card, bar above an entry, join screen): the colours
    of the participant pages, a second palette only for a look that follows the device, the corners."""
    code = _new(app, client)
    standard = app.test_client().get(f"{API}/join/{code}").json["look"]
    assert standard == {"colors": {"background": "#ffffff", "surface": "#ffffff", "surface_2": "#f7f7f4",
                                   "text": "#1d1d1b", "muted": "#66665f", "accent": "#2a5db0", "on_accent": "#ffffff",
                                   "accent_soft": "#e9f0fb", "border": "#d6d4cc", "error": "#b3261e"},
                        "colors_dark": None, "radius": 10}
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(code=code))
        survey.theme = {"preset": "custom", "colors": {"accent": "#0a7d5a", "background": "#fbf8f1", "text": "#222222",
                                                       "surface": "#ffffff"}, "radius": "square", "scheme": "auto"}
        db.session.commit()
    phone, secret = _enrol(app, code)
    look = phone.post(API + "/study", json={}, headers=_bearer(secret)).json["look"]
    assert look["colors"]["accent"] == "#0a7d5a" and look["colors"]["background"] == "#fbf8f1"
    assert look["radius"] == 2 and set(look["colors"]) == set(diary.LOOK_TOKENS)
    dark = look["colors_dark"]
    assert set(dark) == set(diary.LOOK_TOKENS) and theme.lightness(dark["background"]) < 0.5
    assert app.test_client().get(f"{API}/join/{code}").json["look"] == look


def _active_from_today(app):
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        person.first_day = diary.local_today(person)
        person.last_day = person.first_day + timedelta(days=13)
        person.status = Enrolment.ACTIVE
        db.session.commit()


def entry_person():
    return db.session.scalar(db.select(Enrolment))


def _prompt(app, minutes_ago=1, expires=60):
    """Make module m1 due once today, `minutes_ago` minutes before now in the person's time zone; returns the due time
    as the app sends it (epoch milliseconds). Only a time of the schedule is taken (review 2026-10-11, B2)."""
    from datetime import datetime, timezone

    with app.app_context():
        survey = db.session.get(Survey, 1)
        person = db.session.scalar(db.select(Enrolment))
        due = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=minutes_ago)
        local = due.astimezone(diary.zone(person.tz))
        if person.first_day and local.date() < person.first_day:  # just after midnight: the prompt was yesterday
            person.first_day = local.date()
        m1 = {**diary.module(survey, "m1"), "expires": expires,
              "trigger": {"t": "fixed", "times": [local.strftime("%H:%M")], "weekdays": [1, 2, 3, 4, 5, 6, 7]}}
        survey.sampling = {**survey.sampling, "modules": [m1] + [m for m in diary.modules(survey) if m["key"] != "m1"]}
        db.session.commit()
    return int(due.timestamp() * 1000)


def test_an_entry_shows_the_blocks_of_its_module_only(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app)
    out = phone.post(API + "/entry", json={"module": "m1", "due": due, "notified": due + 10_000},
                     headers=_bearer(secret))
    assert out.status_code == 200, out.json
    phone.get(_path(out.json["url"]))
    page = phone.get(f"/s/{code}").get_data(as_text=True)
    assert "Wie fühlen Sie sich gerade?" in page
    with app.app_context():
        survey = db.session.get(Survey, 1)
        entry = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
        shown = {p["block"] for p in entry.layout["pages"]}
        assert shown == diary.blocks_of(survey, "m1")
        assert entry.occasion["part"] == "m1" and entry.occasion["n"] == 1 and entry.occasion["day"] == diary.day_index(entry_person(), diary.local_today(entry_person()))
    # the same prompt again leads to the same entry; one past its time is refused
    again = phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret))
    assert again.status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 2
    late_due = _prompt(app, minutes_ago=180)
    late = phone.post(API + "/entry", json={"module": "m1", "due": late_due}, headers=_bearer(secret))
    assert late.status_code == 410 and late.json["error"] == "expired"
    assert phone.post(API + "/entry", json={"module": "m1"}, headers=_bearer(secret)).status_code == 400


def test_a_report_the_person_starts_has_a_daily_limit(app, client):
    code = _new(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.sampling = {**survey.sampling, "modules": diary.modules(survey) + [
            {"key": "m2", "name": {"de": "Lernen"}, "trigger": {"t": "self", "label": {"de": "Gelernt"}, "max_per_day": 2},
             "expires": 0, "days": [1, None], "notify": {}}]}
        db.session.commit()
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    study = phone.post(API + "/study", json={}, headers=_bearer(secret)).json
    assert study["modules"][1]["trigger"] == {"t": "self", "label": "Gelernt", "max_per_day": 2}
    for _ in range(2):
        assert phone.post(API + "/entry", json={"module": "m2"}, headers=_bearer(secret)).status_code == 200
        _send_open_reports(app)
    over = phone.post(API + "/entry", json={"module": "m2"}, headers=_bearer(secret))
    assert over.status_code == 409 and over.json["error"] == "daily_limit"  # not 429: the app does not retry it


def _send_open_reports(app):
    """The open entries count as sent off: a report counts for the daily limit only then (Abgleich 2026-10-15,
    T07-N-1)."""
    with app.app_context():
        db.session.execute(db.update(Response).where(Response.enrolment_id.is_not(None), Response.status == Response.ACTIVE,
                                                     Response.part != diary.ENTRY)
                           .values(status=Response.COMPLETED, completed_at=Response.started_at))
        db.session.commit()


def test_an_open_report_is_continued_and_does_not_use_up_the_daily_limit(app, client):
    """Abgleich 2026-10-15, T07-N-1: another app that opens the report screen again and again (the home-screen
    shortcut) neither spends the daily limit nor fills the data with empty entries; the open report goes on."""
    code = _new(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.sampling = {**survey.sampling, "modules": diary.modules(survey) + [
            {"key": "m2", "name": {"de": "Lernen"}, "trigger": {"t": "self", "label": {}, "max_per_day": 1},
             "expires": 0, "days": [1, None], "notify": {}}]}
        db.session.commit()
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    for _ in range(5):
        assert phone.post(API + "/entry", json={"module": "m2"}, headers=_bearer(secret)).status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(part="m2")) == 1
    _send_open_reports(app)
    over = phone.post(API + "/entry", json={"module": "m2"}, headers=_bearer(secret))
    assert over.status_code == 409 and over.json["error"] == "daily_limit"


def test_leaving_stops_the_study_and_a_deletion_takes_the_whole_person(app, client):
    from app import erasure

    code = _new(app, client)
    with app.app_context():  # the deletion code is off by default since the pre-test 2026-10-16
        db.session.get(Survey, 1).erase_code = True
        db.session.commit()
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app)
    phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret))
    assert phone.post(API + "/leave", json={}, headers=_bearer(secret)).json["status"] == "left"
    assert phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret)).json["error"] == "ended"
    with app.app_context():
        survey = db.session.get(Survey, 1)
        first, module_entry = sorted(db.session.scalars(db.select(Response)).all(), key=lambda r: r.id)
        assert erasure.code_for(survey, first) is not None
        assert erasure.code_for(survey, module_entry) is None  # one code per person, on the entry part
        erasure.erase(survey, [first], "code")
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
        assert db.session.scalar(db.select(db.func.count(Enrolment.id))) == 0


def test_prompts_follow_the_local_time_of_the_person(app):
    from datetime import date

    with app.app_context():
        survey = Survey(title="T", sampling={"modules": [
            {"key": "m1", "trigger": {"t": "fixed", "times": ["09:00"], "weekdays": [1, 2, 3, 4, 5, 6, 7]}, "days": [2, None]}]})
        person = Enrolment(tz="Europe/Berlin", first_day=date(2026, 10, 24), last_day=date(2026, 10, 26))
        due = diary.prompts(survey, person)
        # day 1 is left out (days from 2: on 24 October 09:00 would be 07:00 UTC); the clocks went back on 25 October
        assert [d.isoformat() for _, d in due] == ["2026-10-25T08:00:00", "2026-10-26T08:00:00"]
        assert diary.compliance(survey, person, now=utcnow().replace(year=2027)) == (0, 2)


def test_the_export_has_one_row_per_entry_with_person_part_and_time(app, client):
    from app import export

    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app, minutes_ago=2)
    url = phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret)).json["url"]
    phone.get(_path(url))
    _finish(app, phone, code)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        first, entry = export.rows(survey)
        person = db.session.scalar(db.select(Enrolment)).pid
        assert first["person"] == entry["person"] == person
        assert (first["teil"], entry["teil"], entry["eintrag"], entry["tag"], entry["ausloeser"]) == ("entry", "m1", 1, diary.day_index(entry_person(), diary.local_today(entry_person())), "fixed")
        assert 100 <= entry["verzoegerung_s"] <= 200 and len(entry["ortszeit"]) == 5
        assert export.is_public_column("person")  # published under new numbers (test_published_data_number_the_persons_anew)


# --- review 2026-10-11 (pruefung-tagebuchstudie-2026-10-11.md) ---------------------------------------------------

def test_the_diary_study_needs_no_experimental_feature(app, client):
    """For everyone since 2026-10-27 (Ralf): no experimental switch any more."""
    from app import experimental

    experiments_on(client)
    assert "diary" not in experimental.KEYS and "offline_interviews" not in experimental.KEYS
    assert client.get("/diary/new").status_code == 200
    dash = client.get("/dashboard/").get_data(as_text=True)
    assert "kind-diary" in dash and "badge-exp" not in dash.split('href="/diary/new"', 1)[1].split("</a>", 1)[0]


def test_the_plain_link_leads_to_the_join_page_not_to_the_questionnaire(app, client):
    code = _new(app, client)
    browser = app.test_client()
    out = browser.get(f"/s/{code}")
    assert out.status_code == 302 and f"/j/{code}" in out.headers["Location"]
    assert browser.post(f"/s/{code}", data={**CONSENT_OK, "nav": "next"}).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
    assert "Askurious" in browser.get(f"/j/{code}").get_data(as_text=True)


def test_only_times_of_the_schedule_make_entries(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app, minutes_ago=5, expires=0)
    for k in (1, 2, 7):  # minutes the schedule does not have
        out = phone.post(API + "/entry", json={"module": "m1", "due": due - k * 60_000}, headers=_bearer(secret))
        assert out.status_code == 400
    assert phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret)).status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 2  # entry part and the one real prompt


def test_too_many_wrong_keys_answer_429_and_spare_the_others(app, client):
    """Wrong secrets from one address answer 429 past the limit; a valid secret from the same address (another phone
    behind the same mobile network or WLAN) always goes through (audit 2026-10-11, M4)."""
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    app.config["TOKEN_TRY_LIMIT"] = 3
    where = {"REMOTE_ADDR": "203.0.113.7"}
    other = app.test_client()
    answers = [other.post(API + "/study", json={}, headers=_bearer("falsch"), environ_base=where).status_code
               for _ in range(5)]
    assert answers == [401, 401, 401, 429, 429]
    out = phone.post(API + "/study", json={}, headers=_bearer(secret), environ_base=where)
    assert out.status_code == 200
    for path in ("/entry", "/offline"):
        assert phone.post(API + path, json={"module": "m1"}, headers=_bearer(secret), environ_base=where).status_code != 429


def test_weekdays_are_whole_numbers_from_one_to_seven(app, client):
    _new(app, client, running=False)
    client.post("/diary/1/schedule", data={"period_days": "7", "m1_trigger": "fixed", "m1_times": "09:00",
                                           "m1_weekdays": ["12", "234", "3"], "m1_name": "x"})
    with app.app_context():
        assert diary.modules(db.session.get(Survey, 1))[0]["trigger"]["weekdays"] == [3]


def test_duplicate_and_file_keep_the_diary_study(app, client):
    from app import transfer
    from app.models import User

    _new(app, client, running=False)
    assert client.post("/surveys/1/duplicate", data={"title": "Kopie"}).status_code == 302
    with app.app_context():
        copy = db.session.get(Survey, 2)
        assert copy.kind == "diary" and diary.modules(copy)[0]["key"] == "m1"
        assert {diary.part_of(b) for b in copy.blocks} == {"entry", "m1"}
    with app.test_request_context():
        data = transfer.loads(transfer.dumps(db.session.get(Survey, 1)).encode())
        assert data["sampling"]["modules"][0]["key"] == "m1"
        new = transfer.create(data, db.session.get(User, 1))
        new = new[0] if isinstance(new, tuple) else new
        assert new.kind == "diary" and {diary.part_of(b) for b in new.blocks} == {"entry", "m1"}
    experiments_on(client)
    client.post("/surveys/1/duplicate", data={"title": "Noch eine"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 4  # no feature needed since 2026-10-27


def test_a_test_person_joins_the_draft_with_the_test_code(app, client):
    code = _new(app, client, running=False)
    with app.test_request_context():
        test = diary.test_code(db.session.get(Survey, 1))
    assert app.test_client().get(f"{API}/join/{code}").status_code == 409
    assert app.test_client().get(f"{API}/join/{code}-aaaaa").status_code == 404
    assert app.test_client().get(f"{API}/join/{test.upper()}").json["test"] is True
    phone = app.test_client()
    joined = phone.post(API + "/enrol", json={"code": test})
    assert joined.status_code == 200
    assert phone.get(_path(joined.json["url"])).status_code == 302
    entry = _finish(app, phone, code, first={**CONSENT_OK, "i1_c_age": "1"})
    assert entry.status == Response.COMPLETED and entry.source == "test"
    with app.app_context():
        assert db.session.scalar(db.select(Enrolment)).source == "test"
    assert test in client.get("/surveys/1/field").get_data(as_text=True)


def test_in_the_field_the_structure_is_fixed_and_old_times_still_count(app, client):
    from datetime import datetime

    _new(app, client)
    with app.app_context():
        bid = next(b.id for b in db.session.get(Survey, 1).blocks if diary.part_of(b) == "m1")
    client.post("/diary/1/schedule", data={"period_days": "3", "m1_trigger": "self", "m1_name": "Moment",
                                           "m1_times": "10:00", "m1_weekdays": ["1", "2", "3", "4", "5", "6", "7"],
                                           "m1_expires": "60", f"part_{bid}": "entry"})
    assert client.post("/diary/1/schedule/module").status_code == 302
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert diary.locked(survey) and survey.sampling["period_days"] == 14 and len(diary.modules(survey)) == 1
        assert diary.part_of(db.session.get(Block, bid)) == "m1"
        m1 = diary.module(survey, "m1")
        assert m1["trigger"] == {"t": "fixed", "times": ["10:00"], "weekdays": [1, 2, 3, 4, 5, 6, 7]}
        assert survey.sampling["earlier"][0]["modules"][0]["trigger"]["times"] == ["09:00", "14:00", "19:00"]
        # a person of the first days: their prompts before the change count by the old times
        until = datetime.fromisoformat(survey.sampling["earlier"][0]["until"])
        person = Enrolment(tz="UTC", first_day=until.date() - timedelta(days=2), last_day=until.date() + timedelta(days=1))
        due = diary.prompts(survey, person)
        assert all(d.strftime("%H:%M") in ("09:00", "14:00", "19:00") for _, d in due if d < until)
        assert all(d.strftime("%H:%M") == "10:00" for _, d in due if d >= until)


def test_erase_in_the_app_deletes_the_person_and_all_entries(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    assert phone.post(API + "/erase", json={}, headers=_bearer(secret)).json["status"] == "erased"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
        assert db.session.scalar(db.select(db.func.count(Enrolment.id))) == 0
    assert phone.post(API + "/study", json={}, headers=_bearer(secret)).status_code == 401


def test_the_sweep_drops_persons_who_never_finished_the_entry_part(app, client):
    code = _new(app, client)
    phone = app.test_client()
    assert phone.post(API + "/enrol", json={"code": code}).status_code == 200
    with app.app_context():
        assert diary.sweep() == 0
        assert diary.sweep(utcnow() + timedelta(days=diary.PENDING_DAYS + 1)) == 1
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
        assert db.session.scalar(db.select(db.func.count(Enrolment.id))) == 0


def test_the_sweep_brings_a_person_with_a_finished_entry_part_up_to_date(app, client):
    """Code review 2026-10-15, E7: a person whose entry part is completed but whose app never asked since stayed
    PENDING, and the hourly sweep read all their entries again for ever. The sweep now refreshes them."""
    code = _new(app, client)
    phone = app.test_client()
    joined = phone.post(API + "/enrol", json={"code": code, "lang": "de"})
    phone.get(_path(joined.json["url"]))  # the entry part begun
    with app.app_context():
        entry = db.session.scalar(db.select(Response))
        entry.status, entry.completed_at = Response.COMPLETED, utcnow()  # finished, as the app has not heard yet
        db.session.commit()
        assert db.session.scalar(db.select(Enrolment)).status == Enrolment.PENDING
        assert diary.sweep(utcnow() + timedelta(days=diary.PENDING_DAYS + 1)) == 0
        db.session.expire_all()
        person = db.session.scalar(db.select(Enrolment))
        assert person.status == Enrolment.ACTIVE and person.first_day is not None
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1


def test_published_data_number_the_persons_anew(app, client):
    from app import export

    code = _new(app, client)
    _enrol(app, code)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        lines = export.to_csv(survey, public=True).splitlines()
        head = lines[0].lstrip("﻿").split(",")
        assert "person" in head and "pid" not in head
        assert lines[1].split(",")[head.index("person")] == "1"
        assert "zufällige Nummer" in export.codebook_csv(survey, public=True)


def test_the_schedule_saves_by_itself_and_modules_are_added(app, client):
    _new(app, client, running=False)
    out = client.post("/diary/1/schedule", data={"autosave": "1", "period_days": "9", "m1_trigger": "fixed", "m1_name": "Moment",
                                                 "m1_times": "09:00", "m1_weekdays": ["1"]})
    assert out.status_code == 200 and out.json["status"] == "saved"
    client.post("/diary/1/schedule/module")
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert survey.sampling["period_days"] == 9 and len(diary.modules(survey)) == 2
    page = client.get("/diary/1/schedule").get_data(as_text=True)
    assert "data-autosave-form" in page and "Kurzbefragung hinzufügen" in page


def test_the_week_preview_follows_the_autosave(app, client):
    """Pre-test 2026-10-20, 20: one prompt a day at 18:00 saved, and the week preview still showed 09:00, 14:00 and
    19:00 until a reload. The autosave now brings the preview drawn anew (autosave.part), which app.js puts in place
    of the element [data-autosave-part="diary-week"]."""
    _new(app, client, running=False)
    page = client.get("/diary/1/schedule").get_data(as_text=True)
    assert 'data-autosave-part="diary-week"' in page and page.count("Wochenvorschau") >= 1
    out = client.post("/diary/1/schedule", data={"autosave": "1", "period_days": "7", "m1_trigger": "fixed", "m1_name": "Moment",
                                                 "m1_times": "18:00", "m1_weekdays": [str(n) for n in range(1, 8)]})
    assert out.json["status"] == "saved"
    week = out.json["parts"]["diary-week"]
    assert week.lstrip().startswith("<section") and 'data-autosave-part="diary-week"' in week
    assert week.count("18:00 Moment") == 7 and "09:00" not in week and "14:00" not in week
    assert "1 Abfrage, ~" in week and "1 Abfragen" not in week
    # the line "Etwa n Abfragen am Tag" in the form, too
    assert "Etwa 1 Abfrage am Tag." in out.json["parts"]["diary-per-day-m1"]
    assert 'data-autosave-part="diary-per-day-m1"' in page
    # the page drawn anew shows the same as the part
    assert client.get("/diary/1/schedule").get_data(as_text=True).count("18:00 Moment") == 7


# --- persons, not entries (Ralf, 2026-10-11) ----------------------------------------------------------------------

def _entries_of(app, phone, secret, code, n):
    """`n` completed entries of module m1 for the person of `secret`: the module made self-started for the test."""
    with app.app_context():
        survey = db.session.get(Survey, 1)
        m1 = {**diary.module(survey, "m1"), "trigger": {"t": "self", "label": {"de": "Jetzt"}, "max_per_day": 0}}
        survey.sampling = {**survey.sampling, "modules": [m1]}
        db.session.commit()
    for _ in range(n):
        url = phone.post(API + "/entry", json={"module": "m1"}, headers=_bearer(secret)).json["url"]
        phone.get(_path(url))
        _finish(app, phone, code)


def test_data_collection_and_the_sample_target_count_persons(app, client):
    from app import timeline

    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    _entries_of(app, phone, secret, code, 3)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 4
        assert survey.completed_count() == 1
        assert sum(n for (src, prev, st), n in survey.tally().items() if st == Response.COMPLETED) == 1
        tl = timeline.build(survey)
        assert tl["completed"] == 1 and tl["entries"] == 3
    page = client.get("/surveys/1/field").get_data(as_text=True)
    assert "Personen mit abgeschlossener Eingangsbefragung" in page and "Einträge" in page


def test_speeders_are_measured_against_the_median_of_their_part(app, client):
    from datetime import timedelta as td

    from app import export

    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    _entries_of(app, phone, secret, code, 3)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        rows = db.session.scalars(db.select(Response).order_by(Response.id)).all()
        first, entries = rows[0], rows[1:]
        first.completed_at = first.started_at + td(minutes=10)  # the long entry part
        for r, s in zip(entries, (20, 22, 24)):  # short modules, none a speeder against its own median
            r.completed_at = r.started_at + td(seconds=s)
        db.session.commit()
        assert export.median_seconds(survey, part="m1") == 22
        speed = export.Speed(survey)
        assert not any(speed.speeder(r) for r in entries)
        assert export.marked_ids(survey) == set()


def test_results_count_every_person_once(app, client):
    from app import results

    code = _new(app, client)
    for answers in (3, 1):  # two persons: one answers three times, one once
        phone, secret = _enrol(app, code)
        with app.app_context():
            person = db.session.scalars(db.select(Enrolment).order_by(Enrolment.id.desc())).first()
            person.first_day = diary.local_today(person)
            person.last_day = person.first_day + timedelta(days=13)
            person.status = Enrolment.ACTIVE
            db.session.commit()
        _entries_of(app, phone, secret, code, answers)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        out = results.compute(survey)
        assert out["diary"] == {"persons": 2, "entries": 4, "by_person": True}
        mood = next(e for e in out["items"] if e["item"].type == "rating")
        col = mood["columns"][0]
        assert col["n"] == 4 and col["weighted"] and col["wn"] == 2.0  # four entries, two persons of weight 1
    page = client.get("/surveys/1/results").get_data(as_text=True)
    assert "jede Person gleich gewichtet" in page and "2 Personen, 4 Einträge" in page


# --- reminder, week preview, missed prompts, offline (Ralf, 2026-10-11, "3 bis 6") -------------------------------

def test_the_reminder_is_set_and_sent_to_the_app_and_marked_on_the_entry(app, client):
    _new(app, client, running=False)
    client.post("/diary/1/schedule", data={"period_days": "14", "m1_trigger": "fixed", "m1_name": "Moment",
                                           "m1_times": "09:00", "m1_weekdays": ["1", "2", "3", "4", "5", "6", "7"],
                                           "m1_expires": "60", "m1_reminder": "15"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert diary.module(survey, "m1")["reminder"] == 15 and diary.reminder_of(diary.module(survey, "m1")) == 15
        assert diary.reminder_of({**diary.module(survey, "m1"), "reminder": 90}) == 0  # after the expiry: none
        survey.status = Survey.RUNNING
        db.session.commit()
        code = survey.code
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app, minutes_ago=20)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.sampling = {**survey.sampling, "modules": [{**diary.module(survey, "m1"), "reminder": 15}]}
        db.session.commit()
    study = phone.post(API + "/study", json={}, headers=_bearer(secret)).json
    assert study["modules"][0]["reminder"] == 15
    phone.post(API + "/entry", json={"module": "m1", "due": due, "reminded": True}, headers=_bearer(secret))
    with app.app_context():
        entry = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
        assert entry.occasion["reminded"] is True


def test_the_week_preview_shows_the_prompts_and_warns(app, client):
    _new(app, client, running=False)
    client.post("/diary/1/schedule", data={"period_days": "14", "m1_trigger": "fixed", "m1_name": "Moment",
                                           "m1_times": "08:00 10:00 12:00 14:00 16:00 18:00 20:00",
                                           "m1_weekdays": ["1", "2", "3", "4", "5", "6", "7"], "m1_expires": "60"})
    with app.app_context():
        days, warnings = diary.week_preview(db.session.get(Survey, 1))
        assert len(days) == 7 and len(days[0]["prompts"]) == 7 and days[0]["minutes"] > 0
        assert any("6" in w for w in warnings)
    page = client.get("/diary/1/schedule").get_data(as_text=True)
    assert "Wochenvorschau" in page and "08:00 Moment" in page


def test_the_csv_export_has_a_row_for_every_missed_prompt(app, client):
    from datetime import date

    from app import export

    code = _new(app, client)
    _enrol(app, code)
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        person.first_day, person.last_day, person.status = date(2026, 1, 5), date(2026, 1, 6), Enrolment.ACTIVE
        db.session.commit()
        survey = db.session.get(Survey, 1)
        lines = export.to_csv(survey).splitlines()
        head = lines[0].lstrip("﻿").split(",")
        states = [line.split(",")[head.index("abfrage")] for line in lines[1:]]
        assert states.count("verpasst") == 6 and states.count("beantwortet") == 1  # 3 a day for 2 days; the entry part
        assert "verpasst" in export.codebook_csv(survey)


def test_a_module_answered_offline_is_stored_with_its_time(app, client):
    from app import runtime

    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app, minutes_ago=10)
    kept = phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret)).json
    assert kept["offline"] is True and "data-app-offline" in kept["html"] and "Wie fühlen Sie sich gerade?" in kept["html"]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1  # the drawn page left nothing behind
        person = entry_person()  # the phone kept the page before the prompt came (an answer cannot begin before it)
        person.settings = {**(person.settings or {}), "offline_at": (utcnow() - timedelta(hours=1)).isoformat()}
        from app.models import BrowserReceipt
        for receipt in db.session.scalars(db.select(BrowserReceipt).filter_by(survey_id=person.survey_id,
                                                                              scope=f"offline:{person.id}")):
            receipt.data = {**receipt.data, "at": person.settings["offline_at"]}
        db.session.commit()
        item = next(i for b in db.session.get(Survey, 1).blocks for i in b.items if i.type == "rating")
        field = f"i{item.id}"
    sent = {"module": "m1", "due": due, "notified": due, "started": due + 60_000, "completed": due + 90_000,
            "version": kept["version"], "form": {field: ["4"], "nav": ["next"]}}
    assert phone.post(f"{API}/offline", json=sent, headers=_bearer(secret)).json["status"] == "stored"
    assert phone.post(f"{API}/offline", json=sent, headers=_bearer(secret)).json["error"] == "done"
    assert phone.post(f"{API}/offline", json={**sent, "version": "old"}, headers=_bearer(secret)).json["error"] == "changed"
    with app.app_context():
        entry = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
        assert entry.status == Response.COMPLETED and entry.occasion["offline"] is True
        assert runtime.load_answers(entry)[item.id] in (4, "4")
        assert int((entry.completed_at - entry.started_at).total_seconds()) == 30


# --- random prompts within time windows, quiet hours (Ralf, 2026-10-11, "weitermachen") ---------------------------

def test_random_times_match_the_vectors_the_app_checks():
    import json
    from datetime import date
    from pathlib import Path

    data = json.loads((Path(__file__).parents[1] / "diary_random_vectors.json").read_text(encoding="utf-8"))
    for case in data["cases"]:
        assert diary.random_times(case["seed"], case["key"], date.fromisoformat(case["day"]), case["trigger"],
                                  case["quiet"]) == case["times"]
    value, state = diary._splitmix(0)
    assert (str(diary._fnv("m1")), str(value), str(state)) == (
        data["check"]["fnv_m1"], data["check"]["splitmix_0_value"], data["check"]["splitmix_0_state"])


def test_random_times_keep_the_gap_the_windows_and_the_quiet_hours():
    from datetime import date

    trigger = {"per_day": 5, "windows": [["09:00", "21:00"]], "min_gap": 90}
    for seed in range(200):
        times = diary.random_times(seed, "m1", date(2026, 10, 24) + timedelta(days=seed % 30), trigger, ["12:00", "14:00"])
        minutes = [int(t[:2]) * 60 + int(t[3:]) for t in times]
        assert len(minutes) == 5 and minutes == sorted(minutes)
        assert all(540 <= m < 1260 and not 720 <= m < 840 for m in minutes)
        assert all(b - a >= 90 for a, b in zip(minutes, minutes[1:]))
    assert diary.random_times(1, "m1", date(2026, 10, 24), trigger) != diary.random_times(2, "m1", date(2026, 10, 24), trigger)


def test_a_random_module_is_saved_scheduled_and_its_prompts_accepted(app, client):
    _new(app, client, running=False)
    client.post("/diary/1/schedule", data={"period_days": "14", "m1_trigger": "random", "m1_name": "Moment",
                                           "m1_per_day": "4", "m1_windows": "08:00-12:00, 14:00 - 24:00",
                                           "m1_min_gap": "45", "m1_weekdays": ["1", "2", "3", "4", "5", "6", "7"],
                                           "m1_expires": "0"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert diary.module(survey, "m1")["trigger"] == {"t": "random", "per_day": 4, "windows": [["08:00", "12:00"], ["14:00", "00:00"]],
                                                          "min_gap": 45, "weekdays": [1, 2, 3, 4, 5, 6, 7]}
        days, _ = diary.week_preview(survey)
        assert all(len(d["prompts"]) == 4 and d["prompts"][0][1].endswith("~") for d in days)
        survey.status = Survey.RUNNING
        db.session.commit()
        code = survey.code
    phone, secret = _enrol(app, code)
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        # a zone where it is about 20:00 now: today's first prompt (08:00 to 11:30) is due and its day not yet over
        offset = (20 - utcnow().hour + 12) % 24 - 12
        person.tz = f"Etc/GMT{-offset:+d}" if offset else "UTC"
        person.first_day = diary.local_today(person) - timedelta(days=1)
        person.last_day = person.first_day + timedelta(days=13)
        person.status = Enrolment.ACTIVE
        db.session.commit()
        survey = db.session.get(Survey, 1)
        yesterday = diary.prompts(survey, person)[0][1]
        due = next(due for _, due, d in diary._schedule(survey, person) if d == diary.local_today(person))
        study = diary.study_json(db.session.get(Survey, 1), person, "de")
        assert study["seed"] == diary.seed_of(person) and study["modules"][0]["trigger"]["t"] == "random"
    # without an expiry a prompt is over at the end of its day (review 2026-10-15, G4)
    millis = int(yesterday.replace(tzinfo=timezone.utc).timestamp() * 1000)
    assert phone.post(API + "/entry", json={"module": "m1", "due": millis}, headers=_bearer(secret)).status_code == 410
    millis = int(due.replace(tzinfo=timezone.utc).timestamp() * 1000)
    assert phone.post(API + "/entry", json={"module": "m1", "due": millis}, headers=_bearer(secret)).status_code == 200
    assert phone.post(API + "/entry", json={"module": "m1", "due": millis + 60_000}, headers=_bearer(secret)).status_code == 400


def test_quiet_hours_apply_from_the_next_day(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    out = phone.post(API + "/study", json={"quiet": ["22:30", "7:00"]}, headers=_bearer(secret)).json
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        tomorrow = (diary.local_today(person) + timedelta(days=1)).isoformat()
    assert out["quiet"] == [{"from": tomorrow, "q": ["22:30", "07:00"]}]
    again = phone.post(API + "/study", json={"quiet": ["22:30", "07:00"]}, headers=_bearer(secret)).json
    assert again["quiet"] == out["quiet"]  # the same again changes nothing
    off = phone.post(API + "/study", json={"quiet": None}, headers=_bearer(secret)).json
    assert off["quiet"] == [{"from": tomorrow, "q": None}]  # changed on the same day: the newer one for that day
    assert phone.post(API + "/study", json={"quiet": ["25:00", "x"]}, headers=_bearer(secret)).json["quiet"] == off["quiet"]


def test_persons_who_stand_out_are_flagged_on_data_collection(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        person.first_day = diary.local_today(person) - timedelta(days=4)
        person.last_day = person.first_day + timedelta(days=13)
        person.status = Enrolment.ACTIVE
        db.session.commit()
    # the persons are on "Datenerhebung" since T5 (2026-10-17); the sources (Jaso) stay on "Zeitplan"
    page = client.get("/surveys/1/field").get_data(as_text=True)
    assert "Antwortquote unter 50" in page and "Tagen kein Eintrag" in page
    assert "Jaso" in client.get("/diary/1/schedule").get_data(as_text=True)


def test_icc1_and_the_course_over_the_days_on_the_results_page(app, client):
    assert diary.icc1({"a": [1, 1, 1], "b": [5, 5, 5]}) == 1.0  # all variance between persons
    assert abs(diary.icc1({"a": [1, 5, 1, 5], "b": [1, 5, 5, 1]}) or 0) < 0.35  # mostly within persons
    assert diary.icc1({"a": [3]}) is None
    code = _new(app, client)
    for answers in (3, 2):
        phone, secret = _enrol(app, code)
        with app.app_context():
            person = db.session.scalars(db.select(Enrolment).order_by(Enrolment.id.desc())).first()
            person.first_day = diary.local_today(person)
            person.last_day = person.first_day + timedelta(days=13)
            person.status = Enrolment.ACTIVE
            db.session.commit()
        _entries_of(app, phone, secret, code, answers)
    with app.app_context():
        course = diary.time_results(db.session.get(Survey, 1))
        assert course and course[0]["n"] == 5 and course[0]["persons"] == 2 and course[0]["by_day"][0][2] == 5
    page = client.get("/surveys/1/results").get_data(as_text=True)
    assert "Verlauf der Tagebuchstudie" in page and "ICC(1)" in page and "Shrout" in page


# --- steering per person, exit part, fast forward, invitations, method section, dummy data ("baue 2 bis 7") --------

def _person_row(app):
    with app.app_context():
        return db.session.scalar(db.select(Enrolment).order_by(Enrolment.id.desc()))


def test_a_person_is_paused_resumed_and_ended(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        person.first_day = diary.local_today(person) - timedelta(days=3)
        person.last_day = person.first_day + timedelta(days=13)
        person.status = Enrolment.ACTIVE
        db.session.commit()
        pid, before = person.id, len(diary.prompts(db.session.get(Survey, 1), person))
    client.post(f"/diary/1/person/{pid}/pause")
    study = phone.post(API + "/study", json={}, headers=_bearer(secret)).json
    assert study["status"] == "paused" and study["modules"] == [] and study["pauses"][0][1] is None
    with app.app_context():
        person = db.session.get(Enrolment, pid)
        assert len(diary.prompts(db.session.get(Survey, 1), person)) < before  # no prompts from today on
    client.post(f"/diary/1/person/{pid}/resume")
    assert phone.post(API + "/study", json={}, headers=_bearer(secret)).json["status"] == "active"
    client.post(f"/diary/1/person/{pid}/end")
    with app.app_context():
        person = db.session.get(Enrolment, pid)
        assert person.status == Enrolment.ENDED and person.last_day == diary.local_today(person)
    assert phone.post(API + "/study", json={}, headers=_bearer(secret)).json["status"] == "ended"


def test_end_for_everyone(app, client):
    code = _new(app, client)
    _enrol(app, code)
    _enrol(app, code)
    client.post("/diary/1/end-all")
    with app.app_context():
        assert {p.status for p in db.session.scalars(db.select(Enrolment))} == {Enrolment.ENDED}


def test_the_exit_part_opens_after_the_period_once(app, client):
    code = _new(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        block = Block(position=len(survey.blocks), titles={"de": "Abschluss"}, settings={"part": "exit"})
        block.items = [diary._mood_item(survey, "de")]
        survey.blocks.append(block)
        survey.sampling = {**survey.sampling, "exit": {"name": {"de": "Rückblick"}, "days": 14}}
        db.session.commit()
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    study = phone.post(API + "/study", json={}, headers=_bearer(secret)).json
    assert study["exit"]["name"] == "Rückblick" and study["exit"]["open"] is False
    assert phone.post(API + "/entry", json={"module": "exit"}, headers=_bearer(secret)).status_code == 409
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        person.first_day -= timedelta(days=20)
        person.last_day -= timedelta(days=20)
        db.session.commit()
    study = phone.post(API + "/study", json={}, headers=_bearer(secret)).json
    assert study["status"] == "finished" and study["exit"]["open"] is True
    url = phone.post(API + "/entry", json={"module": "exit"}, headers=_bearer(secret)).json["url"]
    phone.get(_path(url))
    _finish(app, phone, code)
    assert phone.post(API + "/study", json={}, headers=_bearer(secret)).json["exit"]["open"] is False


def test_a_fast_test_person_lives_a_day_in_ten_minutes(app, client):
    from datetime import datetime

    code = _new(app, client, running=False)
    with app.test_request_context():
        fast = diary.test_code(db.session.get(Survey, 1), fast=True)
    assert len(fast.split("-")[1]) == diary.FAST_SUFFIX
    phone = app.test_client()
    joined = phone.post(API + "/enrol", json={"code": fast})
    assert joined.status_code == 200
    secret = joined.json["secret"]
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        assert person.source == "test" and person.settings["clock"]["factor"] == diary.FAST_FACTOR
        t0 = datetime.fromisoformat(person.settings["clock"]["t0"])
        assert diary.virtual(person, t0 + timedelta(minutes=10)) == t0 + timedelta(days=1)
    clock = phone.post(API + "/study", json={}, headers=_bearer(secret)).json["clock"]
    assert clock["factor"] == diary.FAST_FACTOR and clock["t0"] > 0


def test_invitation_codes_join_once_and_can_be_required(app, client):
    code = _new(app, client)
    client.post("/diary/1/invites", data={"n": "3"})
    with app.app_context():
        codes = [f"{code}-{i['c']}" for i in diary.invites(db.session.get(Survey, 1))]
    assert len(codes) == 3 and all(len(c.split("-")[1]) == 8 for c in codes)
    page = client.get("/diary/1/invites/print").get_data(as_text=True)
    assert all(c in page for c in codes)
    with app.test_request_context():  # the cards' QR codes with a fixed mask (audit 2026-10-11, N14)
        url = diary.join_url(db.session.get(Survey, 1), codes[0])
    assert str(diary.qr_svg(url, mask=0)) in page
    import segno

    if segno.make(url, error="m").mask != 0:  # the best of all eight masks is not searched for any more
        assert str(diary.qr_svg(url)) not in page
    phone = app.test_client()
    assert phone.post(API + "/enrol", json={"code": codes[0]}).status_code == 200
    # a used code connects again only after a release (Abgleich 2026-10-15, NB-1)
    assert phone.post(API + "/enrol", json={"code": codes[0]}).json["error"] == "used"
    client.post(f"/diary/1/invites/{codes[0].split('-')[1]}/release")
    assert phone.post(API + "/enrol", json={"code": codes[0]}).json["rejoined"] is True
    client.post("/diary/1/schedule", data={"period_days": "14", "m1_trigger": "fixed", "m1_name": "Moment",
                                           "m1_times": "09:00", "m1_weekdays": ["1"], "invite_only": "1"})
    assert phone.post(API + "/enrol", json={"code": code}).json["error"] == "closed"
    assert phone.post(API + "/enrol", json={"code": codes[1]}).status_code == 200


def test_the_method_section_describes_the_diary_study(app, client):
    from app import methods

    code = _new(app, client)
    _enrol(app, code)
    _active_from_today(app)
    with app.app_context():
        f = methods._facts(db.session.get(Survey, 1), 0, ("real",))
        assert "Tagebuchstudie" in f["diary"]["de"] and "Android" in f["diary"]["de"]
        assert "Android only" in methods.facts_text(f) or "Android for" in methods.facts_text(f)
        assert f["diary"]["de"] in methods.draft(f)


def test_dummy_persons_answer_over_their_days(app, client):
    from datetime import datetime

    from app import dummy

    _new(app, client, running=False)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert dummy.generate_diary(survey, 3, seed=7) == 3
        persons = db.session.scalars(db.select(Enrolment).filter_by(source="dummy")).all()
        assert len(persons) == 3
        entries = db.session.scalars(db.select(Response).where(Response.source == "dummy",
                                                               Response.status == Response.COMPLETED)).all()
        modules_done = [r for r in entries if r.occasion["part"] == "m1"]
        assert len(modules_done) >= 3 and all(r.completed_at > r.started_at for r in modules_done)
        assert all(r.started_at >= datetime.fromisoformat(r.occasion["due"]) for r in modules_done)
    client.post("/surveys/1/dummy/delete", data={"source": "dummy"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Enrolment.id))) == 0


# --- the three parts (Ralf, 2026-10-11): entry survey, short surveys, exit survey ------------------------------------

def test_the_template_ticks_add_demographics_and_an_exit_survey(app, client):
    client.post("/diary/new", data={"title": "Mit allem", "language": "de", "demographics": "1", "exit": "1"})
    client.post("/diary/new", data={"title": "Schlank", "language": "de"})
    with app.app_context():
        full, slim = db.session.get(Survey, 1), db.session.get(Survey, 2)
        assert [diary.part_of(b) for b in full.blocks] == ["entry", "entry", "m1", "exit"]
        demo = next(b for b in full.blocks if (b.settings or {}).get("demographics"))
        assert diary.part_of(demo) == "entry" and {i.code for i in demo.items} >= {"age", "GES_1"}  # gender: RatSWD GES_1
        assert next(b for b in full.blocks if diary.part_of(b) == "exit").items[0].type == "text_input"
        assert [diary.part_of(b) for b in slim.blocks] == ["entry", "m1"]


def test_the_editor_shows_each_part_as_a_section(app, client):
    client.post("/diary/new", data={"title": "Teile", "language": "de", "demographics": "1", "exit": "1"})
    page = client.get("/surveys/1/editor/").get_data(as_text=True)
    heads = [page.index(f'id="part-{k}"') for k in ("entry", "m1", "exit")]
    assert heads == sorted(heads)
    assert "Eingangsbefragung" in page and "Kurzbefragung: Moment" in page and "Abschlussbefragung" in page
    assert "3× am Tag um 09:00, 14:00, 19:00" in page
    # the block "Moment" stands under its own head, the demographics under the entry survey
    assert heads[0] < page.index("Wie alt sind Sie?") < heads[1] < page.index("Wie fühlen Sie sich gerade?") < heads[2]
    assert "Getrennter Abschnitt" not in page and 'name="part"' in page


def test_a_block_changes_its_part_by_select_drag_and_new_block(app, client):
    _new(app, client, running=False)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        consent = next(b.id for b in survey.blocks if any(i.type == "consent" for i in b.items))
        mood = next(b.id for b in survey.blocks if diary.part_of(b) == "m1")
    client.post(f"/diary/1/block/{mood}/part", data={"part": "exit"})
    client.post(f"/diary/1/block/{consent}/part", data={"part": "m1"})  # the consent stays in the entry survey
    client.post("/surveys/1/editor/blocks", data={"part": "m1"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert diary.part_of(db.session.get(Block, mood)) == "exit"
        assert diary.part_of(db.session.get(Block, consent)) == "entry"
        new = max(survey.blocks, key=lambda b: b.id)
        assert diary.part_of(new) == "m1"
    # dropped next to the new block, "Moment" belongs to its short survey again
    client.post(f"/surveys/1/editor/blocks/{mood}/place", data={"target": new.id, "mode": "after"})
    with app.app_context():
        assert diary.part_of(db.session.get(Block, mood)) == "m1"
    # in the field the parts are fixed
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    client.post(f"/diary/1/block/{mood}/part", data={"part": "entry"})
    with app.app_context():
        assert diary.part_of(db.session.get(Block, mood)) == "m1"


def test_a_questionnaire_is_taken_over_as_entry_survey(app, client):
    from app.models import Item

    from app.models import SurveyMember, User

    _new(app, client, running=False)
    with app.app_context():
        user = db.session.get(User, 1)
        source = Survey(title="Vorher", created_by_id=user.id, languages=["de"], default_language="de")
        source.members.append(SurveyMember(orcid=user.orcid, user_id=user.id, name=user.name, invited_by_id=user.id,
                                           affiliation="", position=0, role="admin"))
        db.session.add(source)
        db.session.flush()
        sid = source.id
        block = Block(position=len(source.blocks), titles={"de": "Skala"})
        block.items = [Item(type="text_input", code="Q1", answer_mode="optional", config={}, texts={"de": {"question": "Wie heißt Ihr Kurs?"}})]
        source.blocks.append(block)
        db.session.commit()
        before = len(db.session.get(Survey, 1).blocks)
    page = client.get("/surveys/1/editor/").get_data(as_text=True)
    assert f'<option value="{sid}">' in page
    client.post("/diary/1/take-over", data={"part": "entry", "source": sid})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        new = [b for b in survey.blocks][before:] if len(survey.blocks) > before else []
        taken = [b for b in survey.blocks if any(i.code == "Q1" for i in b.items)]
        assert taken and diary.part_of(taken[0]) == "entry"
        assert sum(1 for b in survey.blocks for i in b.items if i.type == "consent") == 1  # no second consent
        assert len(survey.blocks) > before and new


def test_pages_that_do_not_apply_are_hidden(app, client):
    _new(app, client, running=False)
    field = client.get("/surveys/1/field").get_data(as_text=True)
    assert 'id="links"' in field and "Beitreten" in field
    assert "Links vom Panel einfügen" not in field and "Papierfragebogen drucken" not in field
    settings = client.get("/surveys/1").get_data(as_text=True)
    assert "Teilnahme im selben Browser" not in settings and "einmal je Sitzung" not in settings
    assert 'id="wellen"' not in client.get("/surveys/1/researchdesign").get_data(as_text=True)
    assert "/flow/quotas/" not in client.get("/diary/1/schedule").get_data(as_text=True)
    assert 'id="contact-data"' not in client.get("/surveys/1/data").get_data(as_text=True)


def test_a_short_survey_has_no_progress_bar_and_a_short_thanks(app, client):
    code = _new(app, client)
    with app.app_context():
        db.session.get(Survey, 1).show_progress = True
        db.session.commit()
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app)
    out = phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret))
    phone.get(_path(out.json["url"]))
    page = phone.get(f"/s/{code}").get_data(as_text=True)
    assert "Wie fühlen Sie sich gerade?" in page and "p-progress" not in page
    entry = _finish(app, phone, code)
    assert entry.status == Response.COMPLETED
    assert "Ihr Eintrag ist gespeichert." in phone.get(f"/s/{code}").get_data(as_text=True)


def test_copies_keep_the_exit_survey(app, client):
    client.post("/diary/new", data={"title": "Mit Abschluss", "language": "de", "exit": "1"})
    client.post("/diary/1/schedule", data={"exit_name": "Rückblick", "exit_days": "5", "m1_name": "Moment",
                                           "m1_trigger": "fixed", "m1_times": "09:00"})
    assert client.post("/surveys/1/duplicate", data={"title": "Kopie"}).status_code == 302
    with app.app_context():
        copy = db.session.get(Survey, 2)
        assert "exit" in {diary.part_of(b) for b in copy.blocks}
        assert copy.sampling["exit"]["name"]["de"] == "Rückblick" and copy.sampling["exit"]["days"] == 5


# --- six improvements after the three parts (Ralf, 2026-10-11) -------------------------------------------------------

def _full(app, client):
    client.post("/diary/new", data={"title": "Teile", "language": "de", "demographics": "1", "exit": "1"})


def test_the_duration_is_estimated_per_part(app, client):
    from app import duration

    _full(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        entry = duration.estimate(survey)["typical"]
        assert entry == duration.estimate(survey, part="entry")["typical"]
        parts = dict(diary.part_durations(survey))
        assert set(parts) == {"entry", "m1", "exit"}
        assert 0 < parts["m1"]["typical"] < entry  # one rating against consent, age and gender
    page = client.get("/surveys/1/editor/").get_data(as_text=True)
    figures = page[page.index("Kennzahlen"):]
    assert "Eingangsbefragung" in figures and "Kurzbefragung: Moment" in figures and "<dt>Geschätzte Dauer</dt>" not in figures


def test_the_flow_estimates_every_block_of_every_part(app, client):
    """Pre-test 2026-10-20, 19: on "Ablauf" the blocks of a short survey and of the exit survey read "ca. 0 s",
    as the estimate of the whole study (the entry part) gave seconds only for the blocks it summed."""
    import re

    from app import duration

    _full(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        per = duration.estimate(survey)["per_block"]
        assert {b.id for b in survey.blocks} <= set(per) and all(per[b.id] > 0 for b in survey.blocks)
        assert duration.estimate(survey)["typical"] == duration.estimate(survey, part="entry")["typical"]
    page = client.get("/surveys/1/flow/").get_data(as_text=True)
    assert re.findall(r"Element(?:e)? · ca\. [^<]*", page) and "ca. 0 s" not in page


def test_the_end_of_the_entry_survey_says_the_study_begins(app, client):
    code = _new(app, client)
    phone = app.test_client()
    joined = phone.post(API + "/enrol", json={"code": code, "lang": "de"})
    phone.get(_path(joined.json["url"]))
    _finish(app, phone, code, first={**CONSENT_OK, "i1_c_age": "1"})
    assert "Die Kurzbefragungen kommen ab heute in der App." in phone.get(f"/s/{code}").get_data(as_text=True)
    # from the next day on, if the researchers choose it
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.sampling = {**survey.sampling, "start": "next_day"}
        db.session.commit()
    assert "Die Kurzbefragungen kommen ab morgen in der App." in phone.get(f"/s/{code}").get_data(as_text=True)


def test_the_arrows_move_a_block_within_its_part(app, client):
    _full(app, client)
    client.post("/surveys/1/editor/blocks", data={"part": "m1"})  # a second block of "Moment", after the exit block
    with app.app_context():
        survey = db.session.get(Survey, 1)
        first, second = sorted(diary.blocks_of(survey, "m1"))
    client.post(f"/surveys/1/editor/blocks/{second}/move/up")
    with app.app_context():
        from app import flow

        order = [b for b in flow.block_order(flow.tree(db.session.get(Survey, 1))) if b in (first, second)]
        assert order == [second, first]
    client.post(f"/surveys/1/editor/blocks/{second}/move/up")  # at the top of its part: nothing changes
    with app.app_context():
        order = [b for b in flow.block_order(flow.tree(db.session.get(Survey, 1))) if b in (first, second)]
        assert order == [second, first]


def test_the_flow_offers_no_ends_or_quotas_in_a_diary_study(app, client):
    _full(app, client)
    page = client.get("/surveys/1/flow/").get_data(as_text=True)
    assert 'value="loop"' in page and 'value="quota"' not in page and 'value="end"' not in page
    assert client.post("/surveys/1/flow/add", data={"kind": "end"}).status_code in (400, 404)


def test_the_preview_runs_one_part(app, client):
    _full(app, client)
    page = client.get("/surveys/1/editor/preview?part=m1", follow_redirects=True).get_data(as_text=True)
    assert "Wie fühlen Sie sich gerade?" in page and "Wie alt sind Sie?" not in page
    editor = client.get("/surveys/1/editor/").get_data(as_text=True)
    assert "preview?lang=de&amp;part=exit" in editor or "part=exit" in editor


def test_the_results_are_grouped_by_part(app, client):
    from tests.conftest import fill_consent

    _full(app, client)
    fill_consent(client)  # dummies need a pre-launch check without errors; without data the page shows its way in (U26)
    client.post("/surveys/1/dummy", data={"n": "3"})
    page = client.get("/surveys/1/results").get_data(as_text=True)
    heads = [page.find(f'class="res-part-head" id="part-{k}"') for k in ("entry", "m1")]
    assert all(h >= 0 for h in heads) and heads == sorted(heads)


def test_the_consent_names_notifications_only_when_there_are_any(app, client):
    _new(app, client, running=False)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        consent = next(i for b in survey.blocks for i in b.items if i.type == "consent")
        scheduled = diary.modules(survey)[0]
        own = {"key": "m2", "name": {"de": "Melden"}, "trigger": {"t": "self", "label": {}, "max_per_day": 5},
               "expires": 0, "days": [1, None], "notify": {}}

        def body(mods, lang="de"):
            survey.sampling = {**survey.sampling, "modules": mods}
            return consent.kind.template_body(consent, lang)

        assert "Benachrichtigungen" in body([scheduled]) and "selbst beginnen" not in body([scheduled])
        assert "manche können Sie auch selbst beginnen" in body([scheduled, own])
        only_own = body([own])
        assert "Benachrichtigungen" not in only_own and "am Tag" not in only_own
        assert "beginnen Sie kurze Befragungen selbst in der App" in only_own
        assert "you start short surveys yourself in the app" in body([own], "en")
        assert "beginnst du kurze Befragungen selbst" in body([own], "de-du")
