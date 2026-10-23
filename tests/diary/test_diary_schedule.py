"""The schedule of a diary study under load (audit 2026-10-11, H2, M10, N13, C8): prompts() walks each set of times
only over its days and gives the same prompts as the former rule; a request of the app stays fast at the largest
schedule allowed; a brake per person; the part of an entry as a column; the course over time kept with the results."""
import random
import time as clock
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from app import diary
from app.extensions import db
from app.models import Enrolment, Response, Survey
from tests.diary.test_diary import API, _active_from_today, _bearer, _enrol, _new, _prompt


# --- the former rule, as it was before the audit, to compare with ------------------------------------------------

def _old_fixed(mods, enrolment, tz, until):
    out = []
    d = enrolment.first_day
    seed, history, stops = None, diary.quiet_history(enrolment), diary.pauses(enrolment)
    while d <= enrolment.last_day:
        day = diary.day_index(enrolment, d)
        if diary.paused_on(stops, d):
            d += timedelta(days=1)
            continue
        for m in mods:
            t = m.get("trigger") or {}
            if t.get("t") not in diary.SCHEDULED or not diary.active_on(m, day) \
                    or d.isoweekday() not in (t.get("weekdays") or range(1, 8)):
                continue
            if t.get("t") == "random":
                seed = diary.seed_of(enrolment) if seed is None else seed
                times = diary.random_times(seed, m["key"], d, t, diary.quiet_on(history, d))
            else:
                times = t.get("times") or []
            for hhmm in times:
                h, mi = (int(x) for x in hhmm.split(":"))
                due = datetime.combine(d, time(h, mi), tz).astimezone(timezone.utc).replace(tzinfo=None)
                if until is None or due <= until:
                    out.append((m["key"], due))
        d += timedelta(days=1)
    return out


def _old_prompts(survey, enrolment, until=None):
    if not enrolment.first_day or not enrolment.last_day:
        return []
    tz = diary.zone(enrolment.tz)
    out = []
    for mods, start, end in diary._versions(survey):
        out += [(k, due) for k, due in _old_fixed(mods, enrolment, tz, until)
                if (start is None or due >= start) and (end is None or due < end)]
    return sorted(out, key=lambda p: p[1])


def _old_is_prompt(survey, enrolment, key, due):
    due = due.replace(second=0, microsecond=0)
    return any(k == key and d.replace(second=0, microsecond=0) == due for k, d in _old_prompts(survey, enrolment))


def _module(rng, key):
    weekdays = sorted(rng.sample(range(1, 8), rng.randint(1, 7)))
    first = rng.randint(1, 4)
    days = [first, rng.choice([None, first + rng.randint(0, 20)])]
    if rng.random() < 0.5:
        times = sorted({f"{rng.randint(0, 23):02d}:{rng.choice([0, 15, 30, 59]):02d}" for _ in range(rng.randint(1, 5))})
        trigger = {"t": "fixed", "times": times, "weekdays": weekdays}
    elif rng.random() < 0.85:
        a, b = rng.randint(0, 22), rng.randint(0, 23)
        trigger = {"t": "random", "per_day": rng.randint(1, 8), "windows": [[f"{a:02d}:00", f"{b:02d}:30"]],
                   "min_gap": rng.choice([0, 30, 60]), "weekdays": weekdays}
    else:
        trigger = {"t": "self", "label": {}, "max_per_day": 3}
    return {"key": key, "name": {"de": key}, "trigger": trigger, "expires": 60, "days": days}


def _random_case(rng):
    """A survey (only its sampling) and a person with a random schedule, earlier sets, pauses and quiet hours."""
    first = date(2026, 3, 20) + timedelta(days=rng.randint(0, 200))  # also over the changes of summer time
    period = rng.randint(1, 40)
    zones = ["Europe/Berlin", "America/Los_Angeles", "Asia/Tokyo", "Pacific/Kiritimati", "Pacific/Pago_Pago", "UTC"]
    person = SimpleNamespace(first_day=first, last_day=first + timedelta(days=period - 1), tz=rng.choice(zones),
                             pid=f"p{rng.randint(0, 10 ** 9)}", settings={})
    if rng.random() < 0.5:
        a = first + timedelta(days=rng.randint(0, period))
        person.settings["pauses"] = [[a.isoformat(), rng.choice([None, (a + timedelta(days=rng.randint(0, 5))).isoformat()])]]
    if rng.random() < 0.5:
        person.settings["quiet"] = [{"from": (first + timedelta(days=rng.randint(0, period))).isoformat(),
                                     "q": rng.choice([["22:00", "07:00"], ["12:00", "13:30"], None])}]
    earlier, at = [], datetime.combine(first, time(0)) + timedelta(hours=rng.randint(-48, 48))
    for _ in range(rng.randint(0, 8)):
        at += timedelta(minutes=rng.choice([2, 30, 600, 1440 * 3]))
        earlier.append({"until": at.isoformat(), "modules": [_module(rng, f"m{k}") for k in range(1, rng.randint(2, 4))]})
    survey = SimpleNamespace(sampling={"modules": [_module(rng, f"m{k}") for k in range(1, rng.randint(2, 5))],
                                       "earlier": earlier})
    return survey, person


def test_prompts_are_the_same_as_with_the_former_rule(app):
    rng = random.Random(20261011)
    with app.app_context():
        checked = 0
        for _ in range(120):
            survey, person = _random_case(rng)
            old = _old_prompts(survey, person)
            assert diary.prompts(survey, person) == old
            for until in (datetime.combine(person.first_day, time(0)) + timedelta(hours=rng.randint(-30, 24 * 45)),
                          datetime.combine(person.last_day, time(23, 59))):
                assert diary.prompts(survey, person, until=until) == _old_prompts(survey, person, until=until)
            for key, due in old[:: max(1, len(old) // 15)]:
                assert diary.is_prompt(survey, person, key, due)
                checked += 1
            for key, due in [(k, d + timedelta(minutes=1)) for k, d in old[:5]] + [("m1", datetime.combine(person.first_day, time(9)))]:
                assert diary.is_prompt(survey, person, key, due) == _old_is_prompt(survey, person, key, due)
        assert checked > 300


def test_an_entry_stays_fast_with_the_largest_schedule_allowed(app, client):
    """The worst case the schedule allows: a year, twelve random modules with twenty prompts a day, and as many
    earlier sets of times as are kept. One request of the app with a time that is no prompt took 10-33 s of CPU
    before the audit."""
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        person = db.session.scalar(db.select(Enrolment))
        person.first_day = diary.local_today(person) - timedelta(days=200)
        person.last_day = person.first_day + timedelta(days=diary.MAX_PERIOD - 1)
        mods = [{"key": f"m{k}", "name": {"de": f"M{k}"}, "expires": 0, "days": [1, None],
                 "trigger": {"t": "random", "per_day": diary.MAX_PER_DAY, "windows": [["06:00", "23:00"]], "min_gap": 0,
                             "weekdays": [1, 2, 3, 4, 5, 6, 7]}} for k in range(1, diary.MAX_MODULES + 1)]
        start = datetime.combine(person.first_day, time(0))
        earlier = [{"until": (start + timedelta(days=4 * n + 1)).isoformat(), "modules": mods}
                   for n in range(diary.MAX_EARLIER)]
        survey.sampling = {**survey.sampling, "modules": mods, "earlier": earlier}
        db.session.commit()
        due = diary.prompts(survey, person, until=diary.virtual(person))[-1][1]
    millis = int(due.replace(tzinfo=timezone.utc).timestamp() * 1000)
    began = clock.process_time()
    wrong = phone.post(API + "/entry", json={"module": "m3", "due": millis + 60_000}, headers=_bearer(secret))
    right = phone.post(API + "/entry", json={"module": "m1", "due": millis}, headers=_bearer(secret))
    took = clock.process_time() - began
    assert wrong.status_code == 400
    assert right.status_code in (200, 400)  # 400 if the newest prompt belongs to another module
    assert took < 2.0, f"two entries took {took:.2f} s"


def test_a_person_is_braked_on_entries(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    app.config["DIARY_PERSON_LIMIT"] = 3
    due = _prompt(app)
    answers = [phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret)).status_code
               for _ in range(4)]
    assert answers == [200, 200, 200, 429]
    assert phone.post(API + "/offline", json={"module": "m1"}, headers=_bearer(secret)).status_code == 429
    # the others are not braked: the study itself still answers
    assert phone.post(API + "/study", json={}, headers=_bearer(secret)).status_code == 200


def test_the_part_is_a_column_and_counted_by_the_database(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app)
    assert phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret)).status_code == 200
    with app.app_context():
        survey = db.session.get(Survey, 1)
        rows = db.session.execute(db.select(Response.part, Response.occasion).order_by(Response.id)).all()
        assert [p for p, _ in rows] == ["entry", "m1"] and all(p == occ["part"] for p, occ in rows)
        person = db.session.scalar(db.select(Enrolment))
        # an entry an older program wrote during a rolling deploy: no part in the column, the JSON counts
        db.session.add(Response(survey_id=survey.id, enrolment_id=person.id, source="real", status=Response.COMPLETED,
                                answers={}, completed_at=survey.created_at, pid="old-1"))
        db.session.flush()
        db.session.execute(db.update(Response).where(Response.pid == "old-1").values(occasion={"part": "m1"}, part=None))
        db.session.commit()
        db.session.expire_all()
        assert sum(n for (src, prev, st), n in survey.tally().items() if src == "real") == 1  # the entry part only
        assert diary.entry_tally(survey) == 1  # the completed module entry of the older program
    from app import timeline

    with app.test_request_context():
        assert len(timeline._real_times(db.session.get(Survey, 1))) == 1


def test_entry_and_study_do_not_materialise_the_entry_history(app, client, monkeypatch):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app)
    calls = []
    real = diary._entries
    monkeypatch.setattr(diary, "_entries", lambda person, light=False: calls.append(light) or real(person, light))
    assert phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret)).status_code == 200
    assert calls == []
    calls.clear()
    assert phone.post(API + "/study", json={}, headers=_bearer(secret)).status_code == 200
    assert calls == []


def test_the_method_section_reads_the_entries_in_one_query(app, client, monkeypatch):
    code = _new(app, client)
    for _ in range(3):
        _enrol(app, code)
    calls = []
    monkeypatch.setattr(diary, "_entries", lambda *a, **k: calls.append(a) or [])
    with app.app_context():
        facts = diary.method_facts(db.session.get(Survey, 1))
    assert calls == [] and "Tagebuchstudie" in facts["de"]


def test_the_course_over_time_is_kept_with_the_results(app, client, monkeypatch):
    from tests.diary.test_diary import _entries_of

    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    _entries_of(app, phone, secret, code, 2)
    app.config["RESULTS_WAIT_PAGE"] = False
    built = []
    real = diary.time_results
    monkeypatch.setattr(diary, "time_results", lambda *a, **k: built.append(k.get("rows") is not None) or real(*a, **k))
    first = client.get("/surveys/1/results")
    assert first.status_code == 200
    assert built == [True]  # computed once, from the rows the page read
    again = client.get("/surveys/1/results")
    assert again.status_code == 200 and built == [True]  # kept: not computed again
