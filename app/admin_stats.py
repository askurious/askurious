"""The dashboard of Administration > Befragungen (Ralf, 2026-10-17; concept admin-dashboard-konzept.md): surveys and
incoming participations of the whole platform in a chosen period, compared with the period before.

Only counts, never answers; only real participations (no test runs, dummy data or preview), as in the list below
the dashboard. Surveys whose researchers keep them out of the admins' overview (Survey.admin_hidden) are counted but
never named. Breakdowns of participations show a number under MIN_CELL as "< 5" (small_cell), the totals stay exact.
The figures are kept for CACHE_SECONDS per period, so several admins do not make the database count them anew."""
import time
from datetime import date, datetime, timedelta, timezone

from markupsafe import Markup, escape

from app.lang import de_en, english
from app.timeutil import LOCAL_TZ, to_local

MIN_CELL = 5          # a number of participations under this is shown as "< 5" in a breakdown
CACHE_SECONDS = 60    # the page asks again every minute (app.js, data-admin-dash)
ACTIVE_MINUTES = 5    # "gerade aktiv": a participation with a page in the last five minutes
QUIET_DAYS = 14       # a running survey without a new participation for this long is counted as quiet
ABANDON_HOURS = 24    # a participation without a page for this long counts as abandoned (as on "Datenerhebung")
PERIODS = ("24h", "7", "30", "365", "since")  # also the switch of Administration > KI (app/ai_stats.py)
DEFAULT = "30"
WEEKDAYS = (("Mo", "Mon"), ("Di", "Tue"), ("Mi", "Wed"), ("Do", "Thu"), ("Fr", "Fri"), ("Sa", "Sat"), ("So", "Sun"))


def period_labels():
    """{key: label} of the switch, in the language of the page; "since" is the date field. "24 Stunden" instead of
    "Heute", the same on Befragungen and KI (Ralf, 2026-10-19)."""
    return {"24h": de_en("24 Stunden", "24 hours"), "7": de_en("7 Tage", "7 days"), "30": de_en("30 Tage", "30 days"),
            "365": de_en("12 Monate", "12 months")}


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _local_midnight_utc(d):
    return datetime(d.year, d.month, d.day, tzinfo=LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)


def window(key, since=None, now=None):
    """The chosen period: {"key", "start", "end", "prev" (start of the period before, None for "seit"), "step",
    "since" (date)}. All times naive UTC; "24h" is the last 24 hours, "seit" starts at local midnight of its date."""
    now = now or _utc_now()
    if key not in PERIODS:
        key = DEFAULT
    if key == "since":
        if not isinstance(since, date):
            key = DEFAULT
        else:
            since = min(since, to_local(now).date())
            start = _local_midnight_utc(since)
            days = (now - start).total_seconds() / 86400
            step = "hour" if days <= 2 else "day" if days <= 62 else "week" if days <= 400 else "month"
            return {"key": key, "start": start, "end": now, "prev": None, "step": step, "since": since}
    if key == "24h":
        start = now - timedelta(days=1)
        return {"key": key, "start": start, "end": now, "prev": start - timedelta(days=1), "step": "hour", "since": None}
    days = int(key)
    start = now - timedelta(days=days)
    return {"key": key, "start": start, "end": now, "prev": start - timedelta(days=days),
            "step": "day" if days <= 62 else "week", "since": None}


def read_since(text):
    """The date of the field "seit" (YYYY-MM-DD), None when there is none or it does not read."""
    try:
        return date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None


def _buckets(w):
    """[(start, end)] as naive UTC, cut at local hours, days, Mondays or first days of the month."""
    local_start, local_end = to_local(w["start"]), to_local(w["end"])
    if w["step"] == "hour":
        t = local_start.replace(minute=0, second=0, microsecond=0)
    else:
        t = local_start.replace(hour=0, minute=0, second=0, microsecond=0)
        if w["step"] == "week":
            t -= timedelta(days=t.weekday())
        elif w["step"] == "month":
            t = t.replace(day=1)
    out = []
    while t < local_end:
        if w["step"] == "hour":
            nxt = (t.astimezone(timezone.utc) + timedelta(hours=1)).astimezone(LOCAL_TZ)
        elif w["step"] == "day":
            nxt = datetime(t.year, t.month, t.day, tzinfo=LOCAL_TZ) + timedelta(days=1)
        elif w["step"] == "week":
            nxt = datetime(t.year, t.month, t.day, tzinfo=LOCAL_TZ) + timedelta(days=7)
        else:
            nxt = datetime(t.year + (t.month == 12), t.month % 12 + 1, 1, tzinfo=LOCAL_TZ)
        out.append((t.astimezone(timezone.utc).replace(tzinfo=None), nxt.astimezone(timezone.utc).replace(tzinfo=None)))
        t = nxt
    return out


def _real():
    from app.models import Response

    from app import plugins

    # further words of a live vote are no participations; imports of an add-on module (app/plugins.py) neither
    return (Response.source == "real", Response.is_preview.is_(False), Response.not_live_extra(),
            *plugins.counted_responses())


def overview(app, key=DEFAULT, since=None, now=None, org_id=None):
    """The figures of the dashboard, kept for CACHE_SECONDS per period and organisation (per app: tests start their
    own)."""
    cache = app.extensions.setdefault("admin_stats", {})
    ident = (key, since, org_id)
    hit = cache.get(ident)
    if now is None and hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    data = compute(key, since, now, org_id)
    if now is None:
        if len(cache) > 32:  # every "seit" date is a key of its own
            cache.clear()
        cache[ident] = (time.monotonic(), data)
    return data


def compute(key=DEFAULT, since=None, now=None, org_id=None):
    """org_id: only the surveys of this organisation and its people (Verwaltung > Befragungen, app/org_admin.py);
    None: the whole platform."""
    from app import orgs
    from app.extensions import db, json_given
    from app.models import Response, Survey, SurveyMember, User

    now = now or _utc_now()
    w = window(key, since, now)
    start, prev = w["start"], w["prev"]
    from app import plugins

    # what an add-on module keeps for itself (imported analyses) is no survey of the platform (Ralf, 2026-10-22)
    scope = ((orgs.column(Survey.org_id) == org_id,) if org_id is not None else ()) + plugins.counted_surveys()
    count = lambda *where: db.session.scalar(db.select(db.func.count(Survey.id)).where(*scope, *where)) or 0  # noqa: E731
    in_scope = (Response.survey_id.in_(db.select(Survey.id).where(*scope)),) if scope else ()
    people_here = (User.id.in_(orgs.user_ids_in([org_id])),) if org_id is not None else ()

    surveys = {"all": count(), "draft": count(Survey.status == Survey.DRAFT),
               "running": count(Survey.status == Survey.RUNNING), "closed": count(Survey.status == Survey.CLOSED),
               "opened": count(Survey.opened_at >= start), "ended": count(Survey.closed_at >= start,
                                                                          Survey.close_reason != "paused")}

    # the participations that matter for the period and the one before: one query with the few columns needed
    lower = prev or start
    rows = db.session.execute(
        db.select(Response.survey_id, Response.started_at, Response.completed_at, Response.status, Response.last_active_at)
        .where(*_real(), *in_scope, db.or_(Response.started_at >= lower, Response.completed_at >= lower))).all()
    in_period = lambda t: t is not None and start <= t <= now  # noqa: E731
    in_prev = lambda t: prev is not None and t is not None and prev <= t < start  # noqa: E731
    started = [r for r in rows if in_period(r.started_at)]
    completed = [r for r in rows if r.status == Response.COMPLETED and in_period(r.completed_at)]
    parts = {"started": len(started), "completed": len(completed),
             "rate": (sum(1 for r in started if r.status == Response.COMPLETED) / len(started)) if started else None,
             "prev_started": sum(1 for r in rows if in_prev(r.started_at)) if prev else None,
             "prev_completed": sum(1 for r in rows if r.status == Response.COMPLETED and in_prev(r.completed_at))
             if prev else None}

    with_input = {r.survey_id for r in started} | {r.survey_id for r in completed}
    quiet_since = now - timedelta(days=QUIET_DAYS)
    recent = db.select(Response.id).where(Response.survey_id == Survey.id, *_real(), Response.started_at >= quiet_since)
    quiet = count(Survey.status == Survey.RUNNING, db.or_(Survey.opened_at.is_(None), Survey.opened_at < quiet_since),
                  ~db.exists(recent))
    # last_active_at has no index (it changes with every page); a participation still at it began within two days
    active_now = db.session.scalar(db.select(db.func.count(Response.id)).where(
        *_real(), *in_scope, Response.started_at >= now - timedelta(days=2),
        Response.last_active_at >= now - timedelta(minutes=ACTIVE_MINUTES))) or 0

    # over time: completed by the time they were completed, the rest by the time they began
    buckets = []
    for b_start, b_end in _buckets(w):
        done = sum(1 for r in completed if b_start <= r.completed_at < b_end)
        open_ = sum(1 for r in started if r.status != Response.COMPLETED and b_start <= r.started_at < b_end)
        buckets.append({"start": b_start, "end": b_end, "done": done, "open": open_})

    # how the participations begun in the period ended so far
    abandon = now - timedelta(hours=ABANDON_HOURS)
    outcome = {"completed": 0, "abandoned": 0, "active": 0, Response.SCREENOUT: 0, Response.QUOTAFULL: 0,
               Response.EXCLUDED: 0, Response.DECLINED: 0}
    for r in started:
        if r.status == Response.ACTIVE:
            outcome["abandoned" if (r.last_active_at or r.started_at) < abandon else "active"] += 1
        elif r.status in outcome:
            outcome[r.status] += 1

    # when they come in: local weekday x hour of the completed ones
    heat = [[0] * 24 for _ in range(7)]
    for r in completed:
        local = to_local(r.completed_at)
        heat[local.weekday()][local.hour] += 1

    # by kind of survey: the columns live, interview, sampling tell them apart (Survey.kind)
    kind_of = db.case((json_given(Survey.live), "live"), (json_given(Survey.interview), "interview"),
                      (json_given(Survey.sampling), "diary"), else_="survey")
    kinds = {k: {"surveys": 0, "running": 0, "completed": 0} for k in ("survey", "live", "interview", "diary")}
    survey_kind = {}
    for sid, kind, status in db.session.execute(db.select(Survey.id, kind_of, Survey.status).where(*scope)):
        survey_kind[sid] = kind
        kinds[kind]["surveys"] += 1
        kinds[kind]["running"] += status == Survey.RUNNING
    for r in completed:
        kinds[survey_kind.get(r.survey_id, "survey")]["completed"] += 1

    people = {
        "new_surveys": count(Survey.created_at >= start),
        "new_accounts": db.session.scalar(db.select(db.func.count(User.id)).where(*people_here, User.pending.is_(False), User.created_at >= start)) or 0,
        "signed_in": db.session.scalar(db.select(db.func.count(User.id)).where(*people_here, User.last_login_at >= start)) or 0,
        "with_running": db.session.scalar(
            db.select(db.func.count(db.distinct(SurveyMember.user_id))).join(Survey, Survey.id == SurveyMember.survey_id)
            .where(*scope, Survey.status == Survey.RUNNING, SurveyMember.user_id.is_not(None))) or 0,
    }
    if org_id is None and orgs.several():
        # the guests are no organisation, and a survey without org_id belongs to the starting one (Ralf 2026-10-19:
        # "1 von 7" with six organisations)
        org_ids = {o.id for o in orgs.all_orgs(guests=False)}
        main = orgs.default_id()
        with_org = ({main if o is None else o for o in db.session.scalars(db.select(Survey.org_id).where(Survey.id.in_(with_input)))}
                    if with_input else set())
        people["orgs"] = (len(with_org & org_ids), len(org_ids))
    return {"window": w, "now": now, "surveys": surveys, "parts": parts, "with_input": len(with_input),
            "quiet": quiet, "active_now": active_now, "buckets": buckets, "outcome": outcome, "heat": heat,
            "kinds": kinds, "people": people}


def small_cell(n):
    """A number of participations in a breakdown: under MIN_CELL (and above 0) as "< 5"."""
    return f"< {MIN_CELL}" if 0 < n < MIN_CELL else n


def outcome_labels():
    return {"completed": de_en("abgeschlossen", "completed"),
            "abandoned": de_en("abgebrochen", "abandoned"),
            "active": de_en("läuft noch", "still in progress"),
            "screenout": de_en("Screenout", "Screen-out"),
            "quotafull": de_en("Quote voll", "Quota full"),
            "excluded": de_en("Ausschluss", "Excluded"),
            "declined": de_en("Einwilligung abgelehnt", "Consent declined")}


def kind_labels():
    return {"survey": de_en("Fragebogen", "Questionnaire"), "live": de_en("Live-Abstimmung", "Live vote"),
            "interview": de_en("Interviewleitfaden", "Interview guide"), "diary": de_en("Tagebuchstudie", "Diary survey")}


def _bucket_label(b, step):
    local = to_local(b["start"])
    if step == "hour":
        return f"{local:%H}"
    if step == "month":
        from app.timeutil import MONTHS_EN

        return f"{MONTHS_EN[local.month - 1]} {local:%y}" if english() else f"{local:%m}/{local:%y}"
    return f"{local.day}/{local.month}" if english() else f"{local.day}.{local.month}."


def _small_text(n):
    from app import units

    shown = small_cell(n)
    return shown if isinstance(shown, str) else units.whole_text(shown, "en" if english() else "de")


def chart(data, width=1000, height=200):
    """Stacked bars: completed (dark blue) under begun and not completed (light blue), per bucket; the numbers in the
    tooltip, small ones as "< 5"."""
    buckets, step = data["buckets"], data["window"]["step"]
    top = max([b["done"] + b["open"] for b in buckets] + [0])
    if not top:
        return Markup('<p class="muted">{}</p>').format(de_en("Im gewählten Zeitraum kamen keine Teilnahmen an.",
                                                             "No participations came in during the chosen period."))
    n, left, bottom = len(buckets), 44, height - 22
    bw = (width - left - 4) / n
    every = max(1, round(n / 12))
    parts = [f'<line x1="{left}" y1="{bottom}" x2="{width - 4}" y2="{bottom}" class="aic-axis"/>',
             f'<text x="{left - 6}" y="14" text-anchor="end" class="aic-tick">{escape(_small_text(top) if top >= MIN_CELL else "")}</text>',
             f'<text x="{left - 6}" y="{bottom}" text-anchor="end" class="aic-tick">0</text>']
    done_label, open_label = de_en("abgeschlossen", "completed"), de_en("begonnen, nicht abgeschlossen", "begun, not completed")
    for i, b in enumerate(buckets):
        x = left + i * bw + 1
        label = _bucket_label(b, step)
        y = bottom
        for value, cls in ((b["done"], "adash-done"), (b["open"], "adash-open")):
            if not value:
                continue
            h = value / top * (bottom - 10)
            y -= h
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(bw - 2, 1):.1f}" height="{h:.1f}" class="{cls}">'
                         f'<title>{escape(label)}: {escape(_small_text(b["done"]))} {escape(done_label)}, '
                         f'{escape(_small_text(b["open"]))} {escape(open_label)}</title></rect>')
        if i % every == 0:
            parts.append(f'<text x="{left + (i + 0.5) * bw:.1f}" y="{height - 6}" text-anchor="middle" class="aic-tick">{escape(label)}</text>')
    course = escape(de_en("Eingehende Teilnahmen im Verlauf", "Incoming participations over time"))
    legend = (f'<span class="aic-key"><i class="adash-done"></i>{escape(done_label)}</span>'
              f'<span class="aic-key"><i class="adash-open"></i>{escape(open_label)}</span>')
    return Markup(f'<svg viewBox="0 0 {width} {height}" class="aic-chart" role="img" aria-label="{course}">{"".join(parts)}</svg>'
                  f'<p class="aic-legend">{legend}</p>')


def heat_level(n, top):
    """0 (none or fewer than five) to 4 (most) for the shade of a cell of the weekday x hour map."""
    if n < MIN_CELL or not top:
        return 0
    return min(4, 1 + int(3 * n / top))


def clock(dt):
    """HH:MM in local time of a stored (naive UTC) moment: "Stand 17:42"."""
    return f"{to_local(dt):%H:%M}"


def weekdays():
    return [en if english() else de for de, en in WEEKDAYS]


def change(current, before):
    """"+26 %" or "−8 %" against the period before; "" when there is nothing to compare with (none before)."""
    from app import units

    if not before:
        return ""
    share = (current - before) / before
    sign = "+" if share > 0 else "−" if share < 0 else "±"
    return sign + units.percent_text(abs(share), "en" if english() else "de")
