"""The contract between the server and the Android app of diary studies (architecture concept 2026-10-12, K1).

The app computes the prompts of a person itself (askurious-android, Schedule.kt) from the study the server sends
(diary.study_json); the server computes the same list to check that a prompt was due (diary.prompts). The vectors in
tests/diary_schedule_vectors.json hold studies as the app gets them and the prompts the server computes for them. This
file checks that the server still gives exactly those prompts; the app's ScheduleVectorsTest checks the same file,
copied unchanged into app/src/test/resources. A change of the rule therefore fails here first: regenerate with
`python tests/diary/test_diary_app_contract.py --update`, copy the file into the app and change Schedule.kt with it.

The cases cover what has gone wrong before or is easy to get wrong: fixed times on chosen weekdays, a module only on
some days, random prompts in two windows and over midnight, a change of the quiet hours during the study, pauses
(closed and open), the changes of summer time (a time that does not exist and one that exists twice) and a time zone
far from Berlin."""
import json
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

VECTORS = Path(__file__).parents[1] / "diary_schedule_vectors.json"
# the app's copy, when the app repository lies next to this one (checked only then)
APP_COPY = Path(__file__).resolve().parents[3] / "askurious-android" / "app" / "src" / "test" / "resources" / VECTORS.name


def _fixed(key, times, weekdays=range(1, 8), days=(1, 100000), expires=60, reminder=0):
    return {"key": key, "name": key, "trigger": {"t": "fixed", "times": list(times), "weekdays": list(weekdays)},
            "expires": expires, "reminder": reminder, "days": list(days), "notify": "", "offline": False}


def _random(key, per_day, windows, min_gap, weekdays=range(1, 8), days=(1, 100000)):
    return {"key": key, "name": key, "trigger": {"t": "random", "per_day": per_day, "windows": [list(w) for w in windows],
                                                 "min_gap": min_gap, "weekdays": list(weekdays)},
            "expires": 30, "reminder": 10, "days": list(days), "notify": "", "offline": False}


def _self(key):
    return {"key": key, "name": key, "trigger": {"t": "self", "label": key, "max_per_day": 3},
            "expires": 0, "reminder": 0, "days": [1, 100000], "notify": "", "offline": False}


def _study(first, last, modules, seed=0, quiet=(), pauses=()):
    """A study in the shape of diary.study_json (only what the schedule reads, and status active)."""
    return {"code": "T1", "title": "", "status": "active", "first_day": first, "last_day": last, "modules": modules,
            "seed": seed, "quiet": list(quiet), "pauses": [list(p) for p in pauses], "exit": None, "clock": None,
            "min_app_version": 0}


CASES = [
    ("fixed times on weekdays, a module only on some days", "Europe/Berlin",
     _study("2026-06-01", "2026-06-14", [_fixed("m1", ["08:00", "20:30"], weekdays=[1, 3, 5]),
                                         _fixed("m2", ["12:15"], days=(3, 5)), _self("s1")])),
    ("random prompts in two windows, quiet hours change during the study", "Europe/Berlin",
     _study("2026-07-06", "2026-07-15", [_random("r1", 5, [["08:00", "12:00"], ["14:00", "22:00"]], 45)],
            seed=1234567890123,
            quiet=[{"from": "2026-07-06", "q": ["22:00", "07:00"]}, {"from": "2026-07-10", "q": ["12:00", "13:30"]},
                   {"from": "2026-07-13", "q": None}])),
    ("random prompts over midnight with quiet hours over midnight", "Europe/Berlin",
     _study("2026-12-29", "2027-01-02", [_random("r2", 6, [["20:00", "02:00"]], 30)], seed=42,
            quiet=[{"from": "2026-12-29", "q": ["23:00", "00:30"]}])),
    ("pauses of the researchers, one closed and one still open", "Europe/Berlin",
     _study("2026-09-01", "2026-09-20", [_fixed("m1", ["09:00"]), _random("r1", 2, [["10:00", "18:00"]], 60)],
            seed=777, pauses=[["2026-09-04", "2026-09-06"], ["2026-09-15", None]])),
    ("end of summer time: 02:30 exists twice", "Europe/Berlin",
     _study("2026-10-23", "2026-10-27", [_fixed("m1", ["00:30", "02:30", "03:30"]),
                                         _random("r1", 3, [["01:00", "04:00"]], 20)], seed=9007199254740991)),
    ("start of summer time: 02:30 does not exist", "Europe/Berlin",
     _study("2027-03-26", "2027-03-30", [_fixed("m1", ["01:30", "02:30", "03:30"]),
                                         _random("r1", 3, [["01:00", "04:00"]], 20)], seed=31337)),
    ("a time zone far from Berlin, weekdays and days on a random module", "America/Los_Angeles",
     _study("2026-11-01", "2026-11-12", [_random("r1", 4, [["07:00", "23:30"]], 90, weekdays=[2, 4, 6, 7], days=(2, 9)),
                                         _fixed("m1", ["23:59"], weekdays=[7])], seed=5550123)),
    ("random prompts whose slots are shorter than the gap", "Asia/Tokyo",
     _study("2026-08-10", "2026-08-12", [_random("r1", 8, [["10:00", "11:00"]], 30)], seed=1)),
]


def _person(study, tz):
    return SimpleNamespace(first_day=date.fromisoformat(study["first_day"]), last_day=date.fromisoformat(study["last_day"]),
                           tz=tz, pid="vector", settings={"quiet": study["quiet"], "pauses": study["pauses"]})


def server_prompts(study, tz, monkeypatch=None):
    """The prompts the server computes for this study: [[module key, due as "YYYY-MM-DDTHH:MM:SSZ"]]."""
    from app import diary
    survey = SimpleNamespace(sampling={"modules": study["modules"]})
    seed_of = diary.seed_of
    diary.seed_of = lambda enrolment: study["seed"]
    try:
        out = diary.prompts(survey, _person(study, tz))
    finally:
        diary.seed_of = seed_of
    return [[key, due.strftime("%Y-%m-%dT%H:%M:%SZ")] for key, due in out]


def build():
    return {"note": "Generated by tests/diary/test_diary_app_contract.py --update; the app keeps an unchanged copy "
                    "(app/src/test/resources). Each prompt: module key and due time in UTC.",
            "cases": [{"name": name, "tz": tz, "study": study, "prompts": server_prompts(study, tz)}
                      for name, tz, study in CASES]}


def _vectors():
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_the_server_gives_the_prompts_of_the_vectors(app):
    with app.app_context():
        for case in _vectors()["cases"]:
            assert server_prompts(case["study"], case["tz"]) == case["prompts"], case["name"]


def test_the_vectors_hold_every_case_and_are_not_empty(app):
    data = _vectors()
    assert [c["name"] for c in data["cases"]] == [name for name, _, _ in CASES]
    with app.app_context():
        assert data["cases"] == build()["cases"], "the cases changed: python tests/diary/test_diary_app_contract.py --update"
    assert all(c["prompts"] for c in data["cases"])


def _sent_study(app, client):
    """The study as the app gets it over the API, for an active person."""
    from tests.diary.test_diary import API, _active_from_today, _bearer, _enrol, _new
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    page = phone.post(API + "/study", json={"tz": "Europe/Berlin", "lang": "de"}, headers=_bearer(secret))
    assert page.status_code == 200
    return page.json


def test_the_vectors_have_the_shape_the_server_sends(app, client):
    """The vectors are only worth something while the app gets the same keys from the server (diary.study_json)."""
    sent = _sent_study(app, client)
    assert set(_vectors()["cases"][0]["study"]) <= set(sent)
    for case in _vectors()["cases"]:
        for m in case["study"]["modules"]:
            assert set(m) == set(sent["modules"][0]), case["name"]


def test_the_server_sends_the_oldest_app_version_it_accepts(app, client):
    assert _sent_study(app, client)["min_app_version"] == 0
    app.config["DIARY_MIN_APP_VERSION"] = 7
    try:
        from app import diary
        from app.extensions import db
        from app.models import Enrolment, Survey
        with app.app_context():
            person = db.session.scalar(db.select(Enrolment))
            assert diary.study_json(db.session.get(Survey, 1), person, "de")["min_app_version"] == 7
    finally:
        app.config["DIARY_MIN_APP_VERSION"] = 0

def test_the_app_keeps_an_unchanged_copy():
    """Only when the app repository lies next to this one (a developer machine); elsewhere there is nothing to see."""
    import pytest
    if not APP_COPY.exists():
        pytest.skip("askurious-android not next to this repository")
    assert json.loads(APP_COPY.read_text(encoding="utf-8"))["cases"] == _vectors()["cases"], \
        "copy tests/diary_schedule_vectors.json into askurious-android/app/src/test/resources"


if __name__ == "__main__" and "--update" in sys.argv:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        VECTORS.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written: {VECTORS}")
