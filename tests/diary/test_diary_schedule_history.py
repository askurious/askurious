"""What the schedule of a person counts from (code review 2026-10-15, G1, G2): the prompts before the person completed
the entry part are not due, and the history of the times, quiet hours and time zones keeps its first entry when it is
trimmed, so that a later set never counts for the first days of a study."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app import diary
from app.models import Enrolment, Response

MODULE = {"key": "m1", "name": {"de": "M"}, "expires": 60, "days": [1, None],
          "trigger": {"t": "fixed", "times": ["09:00", "14:00", "19:00"], "weekdays": [1, 2, 3, 4, 5, 6, 7]}}


def _survey(**more):
    return SimpleNamespace(sampling={"modules": [MODULE], "period_days": 3, "start": "same_day", **more},
                           status="running", closed_at=None)


def test_prompts_before_the_entry_part_was_completed_are_not_due(app):
    """G1: joined at 20:00 (UTC) with prompts at 9, 14 and 19: the first day has no prompt, none counted as missed."""
    with app.app_context():
        survey = _survey()
        person = Enrolment(tz="UTC", status=Enrolment.PENDING, settings={})
        entry = Response(occasion={"part": diary.ENTRY}, status=Response.COMPLETED,
                         completed_at=datetime(2026, 5, 4, 20, 0, 12))
        later = datetime(2026, 5, 5, 23, 0)
        assert diary.refresh(survey, person, now=later, entries=[entry]) == Enrolment.ACTIVE
        assert person.first_day == date(2026, 5, 4) and person.settings["entered_at"] == "2026-05-04T20:00:12"
        due = [d for _, d in diary.prompts(survey, person, until=later)]
        assert due == [datetime(2026, 5, 5, 9), datetime(2026, 5, 5, 14), datetime(2026, 5, 5, 19)]
        assert diary.compliance(survey, person, now=later, entries=[entry]) == (0, 3)
        assert not diary.is_prompt(survey, person, "m1", datetime(2026, 5, 4, 19))
        # a person who joined before the moment was kept: every prompt of the first day, as before
        person.settings = {}
        assert len(diary.prompts(survey, person, until=later)) == 6


def test_a_prompt_in_the_minute_of_the_entry_part_still_counts(app):
    with app.app_context():
        person = Enrolment(tz="UTC", status=Enrolment.ACTIVE, first_day=date(2026, 5, 4), last_day=date(2026, 5, 6),
                           settings={"entered_at": "2026-05-04T14:00:40"})
        assert [d.hour for _, d in diary.prompts(_survey(), person, until=datetime(2026, 5, 4, 23))] == [14, 19]


def _ms(at):
    return int(at.replace(tzinfo=timezone.utc).timestamp() * 1000)


def test_the_app_learns_from_when_the_prompts_count(app):
    """G1 and audit 2026-10-18 (follow-up of AND-02): joined at 20:00 the app computed the prompts of 9, 14 and 19
    from the same schedule and offered them, the server refused their answers with 400. The study JSON names the minute
    after the last prompt left out (not the moment of joining): with it the app keeps exactly the prompts that count."""
    with app.app_context():
        survey = _survey()
        person = Enrolment(tz="UTC", status=Enrolment.ACTIVE, first_day=date(2026, 5, 4), last_day=date(2026, 5, 6),
                           settings={"entered_at": "2026-05-04T20:00:12"})
        cut = diary.prompts_from(survey, person)
        assert cut == _ms(datetime(2026, 5, 4, 19, 1))
        earlier = Enrolment(tz="UTC", status=Enrolment.ACTIVE, first_day=date(2026, 5, 4), last_day=date(2026, 5, 6),
                            settings={})  # the whole schedule, as the app computes it
        everything = diary.prompts(survey, earlier, until=datetime(2026, 5, 6, 23))
        assert len(everything) == 9
        kept = [(k, d) for k, d in everything if _ms(d) >= cut]
        assert kept == diary.prompts(survey, person, until=datetime(2026, 5, 6, 23))
        for key, due in everything:  # what the app offers is what the server takes
            assert diary.is_prompt(survey, person, key, due) == (_ms(due) >= cut)
        # in the minute of a prompt: that prompt counts, the one before does not
        person.settings = {"entered_at": "2026-05-04T14:00:40"}
        assert diary.prompts_from(survey, person) == _ms(datetime(2026, 5, 4, 9, 1))
        # nothing left out: before the first prompt, or joined before the moment was kept
        person.settings = {"entered_at": "2026-05-04T08:00:00"}
        assert diary.prompts_from(survey, person) is None
        person.settings = {}
        assert diary.prompts_from(survey, person) is None


def _times(k):
    return [{**MODULE, "trigger": {**MODULE["trigger"], "times": [f"{k % 24:02d}:{(k // 24) % 60:02d}"]}}]


def test_no_set_of_times_is_dropped(app):
    """G2 and audit storage and calculations 2026-10-28, SD-N1: every earlier set stays, also in the middle: each
    decides which prompts were due on its days."""
    old = [{**MODULE, "trigger": {**MODULE["trigger"], "times": ["08:00"]}}]
    t0 = datetime(2026, 5, 1, 12)
    earlier = [{"until": t0.isoformat(), "modules": old}]
    current = [MODULE]
    for k in range(1, diary.MAX_EARLIER + 10):
        new = _times(k)
        earlier = diary._with_earlier(earlier, current, new, now=datetime.fromisoformat(earlier[-1]["until"]) + timedelta(days=1))
        current = new
    assert len(earlier) == diary.MAX_EARLIER + 10
    assert earlier[0] == {"until": t0.isoformat(), "modules": old}


def test_times_are_not_changed_past_the_ceiling_and_past_counts_stay(app):
    """SD-N1: past MAX_EARLIER sets the times are no longer changed (the rest of the form is saved), so a prompt of a
    day in the middle stays due at its time and stays answered."""
    from werkzeug.datastructures import MultiDict

    t0 = datetime(2026, 1, 1, 12)
    earlier = [{"until": (t0 + timedelta(days=k)).isoformat(), "modules": _times(k)} for k in range(diary.MAX_EARLIER)]
    survey = SimpleNamespace(sampling={"modules": [MODULE], "period_days": 90, "start": "same_day", "earlier": earlier},
                             status="running", closed_at=None, is_editable=False, has_real_responses=lambda: True,
                             blocks=[])
    person = SimpleNamespace(id=1, pid="sd", first_day=date(2026, 1, 1), last_day=date(2026, 3, 31), tz="UTC",
                             settings={})
    until = datetime(2026, 1, 5, 23, 59)
    with app.app_context():
        before = [(m, t) for m, t in diary.prompts(survey, person, until=until) if t.date() == date(2026, 1, 3)]
        form = MultiDict({"m1_trigger": "fixed", "m1_times": "07:15", "m1_weekdays": "1", "m1_expires": "60",
                          "m1_name": "Neu", "summary": "Text"})
        assert diary._save(survey, form, "de") is True
        assert survey.sampling["earlier"] == earlier and survey.sampling["modules"][0]["trigger"] == MODULE["trigger"]
        assert survey.sampling["modules"][0]["name"]["de"] == "Neu"  # the rest of the form is saved
        assert [(m, t) for m, t in diary.prompts(survey, person, until=until) if t.date() == date(2026, 1, 3)] == before
        survey.sampling = {**survey.sampling, "earlier": earlier[:-2]}
        assert diary._save(survey, form, "de") is False  # below the ceiling the change is taken
        assert survey.sampling["modules"][0]["trigger"]["times"] == ["07:15"]


def test_a_resume_of_the_first_days_stays(app):
    """SD-N1: resumed more than 50 times, the first resume still counts (only the last of each day is kept), so a
    prompt before it on that day stays not due."""
    from app.models import Enrolment

    with app.app_context():
        person = Enrolment(tz="UTC", status=Enrolment.ACTIVE, first_day=date(2026, 1, 1), settings={})
        first = datetime(2026, 1, 1, 12)
        for k in range(60):
            day = first + timedelta(days=k)
            for hour in (0, 3):  # twice a day: only the later one is kept
                person.status = Enrolment.PAUSED
                diary.resume(person, now=day + timedelta(hours=hour))
        kept = diary.resumes(person)
        assert len(kept) == 60 and kept[0] == (first + timedelta(hours=3)).isoformat()
        assert diary._resumed_days(person)[date(2026, 1, 1)] == first + timedelta(hours=3)


def test_a_set_of_times_changed_again_within_minutes_is_not_kept(app):
    """G2: the autosave of the tab "Zeitplan" while typing makes no version of its own; going back ends the
    excursion."""
    a = [MODULE]
    b = [{**MODULE, "trigger": {**MODULE["trigger"], "times": ["10:00"]}}]
    c = [{**MODULE, "trigger": {**MODULE["trigger"], "times": ["10:30"]}}]
    t0 = datetime(2026, 5, 1, 12)
    earlier = diary._with_earlier([], a, b, now=t0)
    assert earlier == [{"until": t0.isoformat(), "modules": a}]
    assert diary._with_earlier(earlier, b, c, now=t0 + timedelta(minutes=2)) == earlier  # b was typed on the way to c
    assert diary._with_earlier(earlier, b, a, now=t0 + timedelta(minutes=2)) == []  # back to a: never changed
    later = diary._with_earlier(earlier, b, c, now=t0 + timedelta(hours=2))
    assert later == earlier + [{"until": (t0 + timedelta(hours=2)).isoformat(), "modules": b}]


def test_the_quiet_hours_of_every_day_stay(app):
    """G2 and audit 2026-10-17, T2: past 30 changes no entry is dropped any more; a day in the middle keeps its quiet
    hours, so the prompts answered on it stay answered."""
    module = {"key": "m1", "expires": 30, "days": [1, None],
              "trigger": {"t": "random", "per_day": 3, "windows": [["09:00", "21:00"]], "min_gap": 0,
                          "weekdays": [1, 2, 3, 4, 5, 6, 7]}}
    survey = SimpleNamespace(sampling={"modules": [module]}, status="running", closed_at=None)
    person = SimpleNamespace(id=1, pid="t2", first_day=date(2026, 1, 1), last_day=date(2026, 4, 1), tz="UTC", settings={})
    day = date(2026, 1, 11)
    until = datetime(2026, 1, 11, 23, 59)
    with app.app_context():
        for k in range(40):
            diary.set_quiet(person, ["18:00", "21:00"] if k % 2 else ["09:00", "12:00"],
                            now=datetime(2026, 1, 1, 12) + timedelta(days=k))
            if k == 10:
                answered = [(m, t) for m, t in diary.prompts(survey, person, until=until) if t.date() == day]
        history = diary.quiet_history(person)
        assert len(history) == 40 and history[0] == {"from": "2026-01-02", "q": ["09:00", "12:00"]}
        assert diary.quiet_on(history, day) == ["18:00", "21:00"]  # set on 10 January, dropped before
        assert [(m, t) for m, t in diary.prompts(survey, person, until=until) if t.date() == day] == answered
        entries = [SimpleNamespace(status=Response.COMPLETED, occasion={"part": m, "due": t.isoformat()})
                   for m, t in answered]
        assert len(answered) == 3 and diary.compliance(survey, person, now=until, entries=entries)[0] == 3


def test_the_first_quiet_hours_stay_past_the_ceiling(app, monkeypatch):
    """Past MAX_PERSON_HISTORY (a study of years) the first entry still stays: it holds from the first days."""
    monkeypatch.setattr(diary, "MAX_PERSON_HISTORY", 30)
    with app.app_context():
        person = Enrolment(tz="UTC", status=Enrolment.ACTIVE, first_day=date(2026, 5, 1), settings={})
        for k in range(40):
            diary.set_quiet(person, ["22:00", "07:00"] if k % 2 == 0 else ["23:00", "06:00"],
                            now=datetime(2026, 5, 1, 12) + timedelta(days=k))
        history = diary.quiet_history(person)
        assert len(history) == 30 and history[0] == {"from": "2026-05-02", "q": ["22:00", "07:00"]}
        assert diary.quiet_on(history, date(2026, 5, 3)) == ["22:00", "07:00"]
