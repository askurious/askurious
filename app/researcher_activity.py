"""When researchers work: how many signed-in browser sessions were active in each hour, nothing more (Ralf
2026-11-24: an activity monitor to plan maintenance windows; "bewusst nicht wissen, wer, wann genau was").

One row per hour (its start in UTC) with a number. The number goes up by one when a sign-in session (LoginSession,
app/sessions.py) is used for the first time in that hour: at the sign-in, and when "zuletzt aktiv" of the session
is written in a new hour (sessions.open_row, at most every five minutes). So it counts sessions active in the hour,
and the table holds no account, no session, no address and no moment finer than the hour. Administration >
Befragungen sums the hours of the period by local weekday and hour (admin_stats.compute, "Wann Forschende
arbeiten"); cells under admin_stats.MIN_CELL stay light there. Rows go after KEEP_DAYS (housekeeping, sweep).
"""
from contextlib import nullcontext
from datetime import timedelta

from flask import current_app

from app.extensions import db

KEEP_DAYS = 400  # the longest period of the dashboard (12 months) and a little more


def hour_of(t):
    """The start of the hour of a naive UTC moment."""
    return t.replace(minute=0, second=0, microsecond=0)


def count(now):
    """One more session active in the hour of `now`, in the request's transaction (it commits with "zuletzt aktiv").
    Never raises: the count must not cost anyone a page."""
    from sqlalchemy.dialects import postgresql, sqlite

    from app.models import ResearcherActivity

    t = ResearcherActivity.__table__
    try:
        con = db.session.connection()
        # as limits._take_counted: a savepoint on PostgreSQL, none on SQLite
        with nullcontext() if con.dialect.name == "sqlite" else con.begin_nested():
            insert = (postgresql if con.dialect.name == "postgresql" else sqlite).insert(t)
            con.execute(insert.values(hour=hour_of(now), n=1).on_conflict_do_update(
                index_elements=["hour"], set_={"n": t.c.n + 1}))
    except Exception as e:  # noqa: BLE001 - see the docstring
        current_app.logger.warning("Researcher activity not counted: %s", type(e).__name__)


def heat(start, end, to_local):
    """[weekday][local hour] -> sessions active, summed over the hours from start to end (naive UTC)."""
    from app.models import ResearcherActivity

    grid = [[0] * 24 for _ in range(7)]
    rows = db.session.execute(db.select(ResearcherActivity.hour, ResearcherActivity.n).where(
        ResearcherActivity.hour >= hour_of(start), ResearcherActivity.hour <= end)).all()
    for hour, n in rows:
        local = to_local(hour)
        grid[local.weekday()][local.hour] += n
    return grid


def sweep(now):
    """Remove the hours older than KEEP_DAYS (housekeeping)."""
    from app.models import ResearcherActivity

    gone = db.session.execute(db.delete(ResearcherActivity).where(
        ResearcherActivity.hour < now - timedelta(days=KEEP_DAYS)).execution_options(synchronize_session=False)).rowcount
    if gone:
        db.session.commit()
    return gone
