"""All datetimes are stored as naive UTC and shown in the researcher's local zone."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Berlin")


def to_local(dt):
    """A stored naive UTC datetime in local time; one that knows its zone already (e.g. the time of an estimation,
    to_local once before) is only moved into the local zone, not read as UTC a second time."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def local_today(now=None):
    """Today's date in local time (Europe/Berlin), not the server's: the containers run in UTC, where the server's
    date is still yesterday until 01:00 (winter) or 02:00 (summer) German time (audit 2026-10-13, CF-3). now: naive
    UTC."""
    return to_local(now or datetime.now(timezone.utc).replace(tzinfo=None)).date()


def start_of_local_day_utc(now=None):
    """Midnight of today in local time, as naive UTC: "heute" starts there, not at midnight UTC (W97)."""
    local = to_local(now or datetime.now(timezone.utc).replace(tzinfo=None))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)


def end_of_local_day_utc(d):
    """A date picked in a form means 'until the end of that day' in local time: 23:59 there, for the ends of an
    account and a course, shown as their date. The field end takes day_end_utc."""
    local = datetime.combine(d, time(23, 59), tzinfo=LOCAL_TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def day_end_utc(d):
    """'Until the end of that day' in local time as an exclusive end: midnight at its end, as naive UTC. The field end
    "bis 31.10." closes at 24:00, not at 23:59 (audit 2026-10-18, FUNC-06); also on the days the clock changes."""
    local = datetime.combine(d + timedelta(days=1), time(0), tzinfo=LOCAL_TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def last_day(end):
    """The local date an end stands for: the day before for an exclusive end at local midnight (day_end_utc), else
    its own day (an end from before at 23:59). For showing a field end and filling the date field with it."""
    if end is None:
        return None
    local = to_local(end)
    if (local.hour, local.minute, local.second, local.microsecond) == (0, 0, 0, 0):
        return (local - timedelta(days=1)).date()
    return local.date()


def local_to_utc(d, t):
    """A date and a time picked in a form, meant in local time, as naive UTC ("Start planen": Monday 8:00 in Germany)."""
    return datetime.combine(d, t, tzinfo=LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)


def clock(iso):
    """HH:MM in local time for an ISO timestamp (as written by the background jobs, in UTC)."""
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ).strftime("%H:%M")


MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt(dt, with_time=True, lang="de"):
    """A stored (UTC) datetime in local time: German 06.10.2026 14:05, English 6 Oct 2026, 14:05 (24-hour clock;
    the researchers' pages in English, concept "Mehrsprachige Oberfläche", E11). Without time the date alone. A
    datetime with a zone is shown in local time as well."""
    if getattr(dt, "tzinfo", None) is not None:
        return fmt_local(dt.astimezone(LOCAL_TZ), with_time, lang)
    return fmt_local(to_local(dt), with_time, lang)


def fmt_local(loc, with_time=True, lang="de"):
    """fmt() of a datetime already in local time (the names of snapshot files, a local "now"); a date alone without
    the time. One place for the format of dates on the researchers' pages (UI/UX audit 2026-10-07, W113)."""
    if loc is None:
        return ""
    if not hasattr(loc, "hour"):
        with_time = False
    if lang == "en":
        day = f"{loc.day} {MONTHS_EN[loc.month - 1]} {loc.year}"
        return f"{day}, {loc:%H:%M}" if with_time else day
    return loc.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")
