"""Day limits in German time, not in the server's UTC (security and code audit 2026-10-13, CF-3): between midnight
and 01:00 (winter) or 02:00 (summer) German time date.today() on the UTC containers is still yesterday."""
from datetime import date, datetime

from app import cookies, hosts, timeutil

AFTER_MIDNIGHT = datetime(2026, 10, 19, 22, 30)  # naive UTC: 00:30 on 20 October in Germany (summer time)


def test_local_today_is_the_german_day():
    assert timeutil.local_today(AFTER_MIDNIGHT) == date(2026, 10, 20)
    assert timeutil.local_today(datetime(2026, 12, 31, 23, 30)) == date(2027, 1, 1)   # winter: UTC+1
    assert timeutil.local_today(datetime(2026, 12, 31, 22, 30)) == date(2026, 12, 31)


def test_handover_and_old_cookie_end_at_german_midnight(app, monkeypatch):
    monkeypatch.setattr(hosts, "local_today", lambda: date(2026, 10, 20))
    monkeypatch.setattr(cookies, "local_today", lambda: date(2026, 10, 20))
    app.config.update(PARTICIPANT_HANDOVER_UNTIL="2026-10-19", PARTICIPANT_OLD_COOKIE_UNTIL="2026-10-19")
    with app.app_context():
        assert not hosts._handover_open()
        assert not cookies._until(app, "PARTICIPANT_OLD_COOKIE_UNTIL")
        app.config.update(PARTICIPANT_HANDOVER_UNTIL="2026-10-20", PARTICIPANT_OLD_COOKIE_UNTIL="2026-10-20")
        assert hosts._handover_open() and cookies._until(app, "PARTICIPANT_OLD_COOKIE_UNTIL")


def test_no_server_day_left_where_people_see_a_date():
    """date.today() is the server's day; the pages and the files people get take the German one."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    found = [str(p.relative_to(root)) for p in root.rglob("*.py") if re.search(r"\bdate\.today\(\)", p.read_text("utf-8"))]
    assert found == []


def test_the_field_end_of_a_day_is_midnight_at_its_end():
    """Gesamtaudit 2026-10-18, FUNC-06: "Feldende 31.10." ended at 23:59 German time; the last minute of the day was
    missing. The end is now midnight at the end of the day (exclusive), also on the days the clock changes, and it
    still reads as the day chosen."""
    assert timeutil.day_end_utc(date(2026, 10, 31)) == datetime(2026, 10, 31, 23, 0)   # winter: 24:00 = 23:00 UTC
    assert timeutil.day_end_utc(date(2026, 10, 24)) == datetime(2026, 10, 24, 22, 0)   # summer: 22:00 UTC
    assert timeutil.day_end_utc(date(2026, 10, 25)) == datetime(2026, 10, 25, 23, 0)   # 25 hours: back to winter
    assert timeutil.day_end_utc(date(2027, 3, 28)) == datetime(2027, 3, 28, 22, 0)     # 23 hours: on to summer
    for d in (date(2026, 10, 31), date(2026, 10, 25), date(2027, 3, 28), date(2026, 12, 31)):
        assert timeutil.last_day(timeutil.day_end_utc(d)) == d
        assert timeutil.last_day(timeutil.end_of_local_day_utc(d)) == d  # a field end saved before, at 23:59
    assert timeutil.last_day(None) is None


def test_a_field_end_closes_only_after_the_day(app, client, survey):
    """FUNC-06: a participation at 23:59:30 on the last day still finds the survey open; the pages show the day."""
    from app import lifecycle
    from app.extensions import db
    from app.models import Survey
    from tests.conftest import add, fill_consent

    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "why", "question": "Warum?"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-31"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.field_end == datetime(2031, 1, 31, 23, 0)
    page = client.get("/surveys/1/field").data.decode()
    assert "31.01.2031" in page and "01.02.2031" not in page
    with app.test_request_context():
        lifecycle._close_due(datetime(2031, 1, 31, 22, 59, 30))   # 23:59:30 German time: still open
        assert db.session.get(Survey, 1).status == Survey.RUNNING
        lifecycle._close_due(datetime(2031, 1, 31, 23, 0))
        assert db.session.get(Survey, 1).status == Survey.CLOSED
