"""A monitoring feed: the use of the platform in the last days as plain counts, for the operators' own tools (a script on
their web space polls this address, lays the numbers out as a picture and hands it to a small desk display).

``now``     the local time of the platform, "2026-10-21T16:35:12"
``days``    the last 31 local days, today last: {"date": "2026-10-21", "done": finished participations,
            "opened": surveys opened}
``quarters`` finished participations in each of the last 96 quarter hours (24 hours, UTC-aligned, the last one the
            quarter hour now, still running)
``hours``   finished participations in each of the last 168 local hours, the last one the hour now (so 24 numbers are
            a day, and the same position in each 24 is the same time of day)
``active``  participations with a page in the last five minutes

Only counts of real participations and surveys (the rule of Administration > Befragungen, admin_stats), never answers
or names. The address holds a long random token that a site admin makes, changes and removes under Administration >
Server (the block "Monitoring Feed"); it is kept locked in the table site_setting (app/secrets_box.py). Without a
token, or with a wrong one, the address does not exist (404, as for any other address). The answer is computed once a
minute whoever asks.

The state of the services is not part of it: it comes from the status page (/status/<token>.json, app/status_node.py),
which keeps running when the platform does not. That token is a line of the server's .env (STATUS_JSON_TOKEN), because
the status page cannot read this database; the administration only shows its address."""
import json
import secrets
import time
from bisect import bisect_right
from datetime import timedelta

from flask import Blueprint, abort, current_app, jsonify, url_for
from markupsafe import Markup

from app import admin_stats, secrets_box
from app.timeutil import to_local

bp = Blueprint("monitoring_feed", __name__, url_prefix="/api/monitoring")

SETTING = "monitoring_feed_token"      # the key in site_setting; the value is the token, locked
MIN_TOKEN = 24                         # characters of a token the status page accepts (STATUS_JSON_TOKEN)
DAYS, HOURS, QUARTERS = 31, 7 * 24, 96      # a month of days, a week of hours, a day of quarter hours
KEPT_SECONDS = 60                      # the answer is computed once a minute whatever the number of requests


# ---- the token ------------------------------------------------------------------------------------------------------
def token():
    """The token in clear text, or "" when there is none (never made, removed, or locked with a key that is gone)."""
    from app.extensions import db
    from app.models import SiteSetting

    row = db.session.get(SiteSetting, SETTING)
    return secrets_box.decrypt(row.value) if row and row.value else ""


def _store(value, user_id):
    from app.extensions import db
    from app.models import SiteSetting, utcnow

    row = db.session.get(SiteSetting, SETTING)
    if row is None:
        row = SiteSetting(key=SETTING)
        db.session.add(row)
    row.value, row.changed_by_id, row.changed_at = value, user_id, utcnow()
    db.session.commit()
    current_app.extensions.pop("monitoring_feed", None)    # an answer kept for a token that no longer counts


def make_token(user_id):
    """A new token, the old one (if any) no longer opens the address. Returns the token."""
    new = secrets.token_urlsafe(24)
    _store(secrets_box.encrypt(new), user_id)
    return new


def remove_token(user_id):
    _store("", user_id)


def feed_url():
    """The address of the feed with its token (for the admins), "" when there is no token."""
    current = token()
    return url_for("monitoring_feed.feed", given=current, _external=True) if current else ""


def status_json_url():
    """The address of the services' state on the status page with its token (a line of the .env), "" without either."""
    base, wanted = (current_app.config.get("STATUS_URL") or "").rstrip("/"), current_app.config.get("STATUS_JSON_TOKEN") or ""
    return f"{base}/status/{wanted}.json" if base and len(wanted) >= MIN_TOKEN else ""


SETUP_KIND = "askurious-dashboard-feeds"


def setup_text(feed, status):
    """What the QR code for the Android widget carries: both addresses with their tokens in one small JSON, so the app
    sets itself up with one scan. {"askurious": "askurious-dashboard-feeds", "v": 1, "feed": ..., "status": ...}"""
    return json.dumps({"askurious": SETUP_KIND, "v": 1, "feed": feed, "status": status}, separators=(",", ":"))


def setup_qr():
    """The QR code of both addresses as inline SVG, "" unless both exist (the widget needs both)."""
    feed, status = feed_url(), status_json_url()
    if not (feed and status):
        return ""
    import segno

    return Markup(segno.make(setup_text(feed, status), error="m").svg_inline(scale=5, border=2, dark="#1c1c1a", omitsize=True))


def _allowed(given):
    wanted = token()
    return len(wanted) >= MIN_TOKEN and secrets.compare_digest(given.encode(), wanted.encode())


# ---- the answer -----------------------------------------------------------------------------------------------------
def _last(counts, n):
    """The last n of counts, filled up with zeros in front."""
    return [0] * (n - len(counts)) + list(counts[-n:])


def payload(days, dates, opened, hours, quarters, active, now):
    """The answer. days: finished participations per day (oldest first, today last), dates: the date of each day,
    opened: surveys opened per day, hours: per hour (the last one the hour now), quarters: per quarter hour (the last one
    the quarter hour now), active: taking part, now: local time."""
    days, opened, dates = _last(days, DAYS), _last(opened, DAYS), ([""] * DAYS + list(dates))[-DAYS:]
    return {"v": 5, "now": now, "days": [{"date": d, "done": n, "opened": o} for d, n, o in zip(dates, days, opened)],
            "hours": _last(hours, HOURS), "quarters": _last(quarters, QUARTERS), "active": active}


def hourly(now):
    """Finished participations in each of the last HOURS local hours (the hour now last) and in each of the last QUARTERS
    quarter hours (UTC-aligned, the quarter hour now last): one query for the week, binned twice."""
    from app.extensions import db
    from app.models import Response

    spans = admin_stats._buckets({"start": now - timedelta(hours=HOURS), "end": now, "step": "hour"})[-HOURS:]
    if not spans:
        return [], []
    starts = [s for s, _ in spans]
    hours = [0] * len(spans)
    quarter = timedelta(minutes=15)
    q_now = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    q_first = q_now - (QUARTERS - 1) * quarter
    quarters = [0] * QUARTERS
    for done in db.session.scalars(db.select(Response.completed_at).where(
            *admin_stats._real(), Response.status == Response.COMPLETED,
            Response.completed_at >= starts[0], Response.completed_at < spans[-1][1])):
        hours[max(0, bisect_right(starts, done) - 1)] += 1
        if done >= q_first:
            quarters[min(QUARTERS - 1, int((done - q_first) / quarter))] += 1
    return hours, quarters


def _figures():
    """The figures of the platform from admin_stats (kept 60 s there too), the hours and the surveys opened per day."""
    from app.extensions import db
    from app.models import Survey

    now = admin_stats._utc_now()
    week = admin_stats.overview(current_app, "30")
    spans = [(b["start"], b["end"]) for b in week["buckets"]][-DAYS:]
    from app import plugins

    counted = plugins.counted_surveys()  # not the analyses an add-on module imports
    opened = [db.session.scalar(db.select(db.func.count(Survey.id)).where(Survey.opened_at >= s, Survey.opened_at < e,
                                                                         *counted)) or 0
              for s, e in spans]
    hours, quarters = hourly(now)
    return payload([b["done"] for b in week["buckets"]], [to_local(s).date().isoformat() for s, _ in spans], opened,
                   hours, quarters, week["active_now"], to_local(now).strftime("%Y-%m-%dT%H:%M:%S"))


@bp.route("/<given>")
def feed(given):
    if not _allowed(given):
        abort(404)
    kept = current_app.extensions.get("monitoring_feed")
    if kept is None or time.monotonic() - kept[0] > KEPT_SECONDS:
        kept = current_app.extensions["monitoring_feed"] = (time.monotonic(), _figures())
    response = jsonify(kept[1])
    response.headers.update({"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"})
    return response
