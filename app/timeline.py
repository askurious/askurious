"""Timeline of a survey for the Export page: phases (draft, running, paused, closed, planned
field end), status events, and a histogram of real participations.

Rendered as inline SVG on the server; colours come from CSS classes so dark mode works."""
from collections import Counter
from datetime import datetime, time, timedelta, timezone

from markupsafe import Markup, escape

from app.lang import de_en, english
from app.models import Response, Survey, utcnow
from app.timeutil import LOCAL_TZ, MONTHS_EN, fmt, to_local
from app.units import page_lang

REASONS = {"manual": "geschlossen", "time": "Feldende erreicht", "sample": "Stichprobe erreicht", "paused": "pausiert",
           "quota": "Quoten erfüllt"}
REASONS_EN = {"manual": "closed", "time": "end of fieldwork reached", "sample": "sample reached", "paused": "paused",
              "quota": "quotas met"}
W, H = 1000, 200
LEFT, RIGHT = 36, 12
HIST_TOP, HIST_BOTTOM = 24, 128  # room above the bars for the label "heute"
BAND_TOP, BAND_H = 140, 16


STATE_EVENTS = ("start", "close", "edit")
# entries that change no state (lifecycle.note, lifecycle.note_texts)
NOTES = {"links": "Abschlusslinks geändert", "quota": "Quoten geändert", "texts": "Text korrigiert", "run": "Neuer Durchgang"}
NOTES_EN = {"links": "completion links changed", "quota": "quotas changed", "texts": "text corrected", "run": "new run"}


def _shown():
    """REASONS and NOTES in the language of the page (the keys stay)."""
    return (REASONS_EN, NOTES_EN) if english() else (REASONS, NOTES)


def events(survey):
    """[(datetime, event, reason)] in time order; derived from opened/closed for older surveys."""
    out = [(survey.created_at, "create", "")]
    log = survey.status_log or []
    if log:
        out += [(datetime.fromisoformat(e["at"]), e["event"], e.get("reason", "")) for e in log]
    else:
        if survey.opened_at:
            out.append((survey.opened_at, "start", ""))
        if survey.closed_at and survey.status == Survey.CLOSED:
            out.append((survey.closed_at, "close", survey.close_reason))
    return out[:1] + sorted(out[1:], key=lambda e: e[0])  # "created" always first (log has second precision)


def phases(survey, now):
    """[(start, end, state)] with state draft | running | paused | closed | planned."""
    evs, out, state, since = events(survey), [], "draft", survey.created_at
    for at, event, reason in evs[1:]:
        if event not in STATE_EVENTS:  # a note in the log (lifecycle.note), no change of state
            continue
        if at > since:
            out.append((since, at, state))
        state = ("running" if event == "start" else "draft" if event == "edit"
                 else "paused" if reason == "paused" else "closed")
        since = at
    out.append((since, now, state))
    if state == "running" and survey.field_end and survey.field_end > now:
        out.append((now, survey.field_end, "planned"))
    return out


def _real_times(survey):
    """(status, started, completed, online) of the real participations: the times only, not the answers; online is
    False for a paper questionnaire (channel "paper"), whose start is its takeover and so has no completion time. A
    diary study (app/diary.py) counts persons: the participation of each person's entry part, not their entries."""
    from app.extensions import db

    online = Response.channel.is_(None).label("online")
    from app.models import counted_once

    query = db.select(Response.status, Response.started_at, Response.completed_at, online).filter(
        Response.survey_id == survey.id, Response.source == "real", Response.is_preview.is_(False))
    if survey.sampling is not None:
        query = query.where(Response.part_expr() == "entry")
    return counted_once(("real_times", survey.id), lambda: db.session.execute(query).all())


def _bins(survey, start, end):
    """Bin width (days) and counts of completed and other real participations per bin."""
    days = max((to_local(end).date() - to_local(start).date()).days + 1, 1)
    width = 1 if days <= 120 else 7
    first = to_local(start).date()
    completed, other = Counter(), Counter()
    for status, started_at, completed_at, _online in _real_times(survey):
        at = completed_at if status == Response.COMPLETED else started_at
        if not at:
            continue
        idx = (to_local(at).date() - first).days // width
        (completed if status == Response.COMPLETED else other)[idx] += 1
    n = (days + width - 1) // width
    return width, first, [(completed[i], other[i]) for i in range(n)]


def _day_start_utc(d):
    """Midnight of a local date as naive UTC: where a bar of that day begins on the time axis."""
    return datetime.combine(d, time(0), tzinfo=LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)


def build(survey, now=None):
    now = now or utcnow()
    lang = page_lang()
    reasons, notes = _shown()
    ph = phases(survey, now)
    start, end = survey.created_at, max(p[1] for p in ph)
    if end - start < timedelta(days=7):
        end = start + timedelta(days=7)
    width, first, bins = _bins(survey, start, end)
    span = (end - start).total_seconds()

    def x(dt):
        return LEFT + (W - LEFT - RIGHT) * max(0.0, min(1.0, (dt - start).total_seconds() / span))

    # "nicht beendet" hatched in the accent colour: a light fill alone stayed under 3:1 (UI/UX audit 2026-10-05, V76)
    parts = ['<defs><pattern id="tl-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
             '<rect class="tl-hatch-ground" width="5" height="5"/><line class="tl-hatch-line" x1="0" y1="0" x2="0" y2="5"/></pattern></defs>']
    any_real = any(c + o for c, o in bins)
    # histogram: one bar per day (or week), completed solid, other real participations light on top
    top = max([c + o for c, o in bins] + [1])
    scale = (HIST_BOTTOM - HIST_TOP) / top
    for i, (c, o) in enumerate(bins):
        if not c + o:
            continue
        day = first + timedelta(days=i * width)
        label = (de_en("{day} (Woche ab)", "week from {day}", day=fmt(_day_start_utc(day), False, lang)) if width == 7
                 else fmt(_day_start_utc(day), False, lang))
        # on the same time axis as phases, events and dates: from the local midnight of its first day to the next
        x0, x1 = x(_day_start_utc(day)), x(_day_start_utc(day + timedelta(days=width)))
        bx, bw = x0 + (x1 - x0) * 0.12, max((x1 - x0) * 0.76, 1)

        tip = escape(de_en("{label}: {c} abgeschlossen, {o} abgebrochen, Screen-out oder Quote voll",
                           "{label}: {c} completed, {o} broken off, screen-out or quota full", label=label, c=c, o=o))
        if c:
            parts.append(f'<rect class="tl-bar" x="{bx:.1f}" y="{HIST_BOTTOM - c * scale:.1f}" width="{bw:.1f}" '
                         f'height="{c * scale:.1f}"><title>{tip}</title></rect>')
        if o:
            parts.append(f'<rect class="tl-bar-other" x="{bx:.1f}" y="{HIST_BOTTOM - (c + o) * scale:.1f}" width="{bw:.1f}" '
                         f'height="{o * scale:.1f}"><title>{tip}</title></rect>')
    # y axis: 0 and max; without any participation no axis, a sentence instead
    parts.append(f'<line class="tl-axis" x1="{LEFT}" y1="{HIST_BOTTOM}" x2="{W - RIGHT}" y2="{HIST_BOTTOM}"/>')
    # labels are plain HTML over the drawing (the SVG stretches in width; text must not)
    labels_html = ([(LEFT - 6, HIST_TOP + 8, "end", str(top)), (LEFT - 6, HIST_BOTTOM, "end", "0")] if any_real
                   else [((LEFT + W - RIGHT) / 2, (HIST_TOP + HIST_BOTTOM) / 2 + 8, "middle", de_en("noch keine Teilnahmen", "no participations yet"))])
    # phase band
    names = {"draft": de_en("Entwurf", "draft"), "running": de_en("läuft", "running"), "paused": de_en("pausiert", "paused"),
             "closed": de_en("geschlossen", "closed"), "planned": de_en("geplantes Feldende", "planned end of fieldwork")}
    for a, b, state in ph:
        if b <= a:
            continue
        # a planned field end at midnight shows as the last minute of its day (timeutil.day_end_utc, FUNC-06)
        shown = b - timedelta(microseconds=1) if state == "planned" else b
        tip = escape(de_en("{name}: {a} bis {b}", "{name}: {a} to {b}", name=names[state], a=fmt(a, True, lang), b=fmt(shown, True, lang)))
        parts.append(f'<rect class="tl-{state}" x="{x(a):.1f}" y="{BAND_TOP}" width="{max(x(b) - x(a), 1.5):.1f}" '
                     f'height="{BAND_H}" rx="3"><title>{tip}</title></rect>')
    # event markers
    labels = {"create": de_en("angelegt", "created"), "start": de_en("gestartet", "started"),
              "edit": de_en("zur Bearbeitung geöffnet", "opened for editing"), **notes}
    closed = de_en("geschlossen", "closed")
    for at, event, reason in events(survey):
        text = labels.get(event) or reasons.get(reason, closed)
        parts.append(f'<line class="tl-event" x1="{x(at):.1f}" y1="{HIST_TOP}" x2="{x(at):.1f}" y2="{BAND_TOP + BAND_H}">'
                     f'<title>{escape(f"{text}: {fmt(at, True, lang)}")}</title></line>')
    parts.append(f'<line class="tl-now" x1="{x(now):.1f}" y1="{HIST_TOP - 6}" x2="{x(now):.1f}" y2="{BAND_TOP + BAND_H + 4}">'
                 f'<title>{de_en("heute", "today")}</title></line>')
    labels_html.append((x(now), HIST_TOP - 8, "middle", de_en("heute", "today")))
    # date ticks at local midnight, as on the dashboards "4.10." (V76): every day, or every 2nd, 7th, 14th … so that
    # about eight are labelled; a short stroke at every midnight in between
    first_day, last_day = to_local(start).date() + timedelta(days=1), to_local(end).date()
    days = (last_day - first_day).days + 1
    step = next((n for n in (1, 2, 7, 14, 28, 56, 91) if days / n <= 8), 182)
    for k in range(max(days, 0)):
        day = first_day + timedelta(days=k)
        at = _day_start_utc(day)
        if step <= 2 or days <= 60:
            parts.append(f'<line class="tl-tick" x1="{x(at):.1f}" y1="{HIST_BOTTOM}" x2="{x(at):.1f}" y2="{HIST_BOTTOM + 4}"/>')
        if k % step == 0:
            year = day.month == 1 and day.day <= step  # the year at the first tick of January
            tick = (f"{day.day} {MONTHS_EN[day.month - 1]}" + (f" {day:%y}" if year else "") if lang == "en"
                    else f"{day.day}.{day.month}." + (f"{day:%y}" if year else ""))
            labels_html.append((x(at), H - 8, "middle", tick))
    per = de_en("Tag", "day") if width == 1 else de_en("Woche", "week")
    svg = (f'<svg class="timeline-svg" viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" '
           f'aria-label="{escape(de_en("Zeitverlauf der Befragung mit Teilnahmen pro {bin}", "Timeline of the survey with participations per {bin}", bin=per))}">'
           + "".join(parts) + "</svg>")
    shift = {"start": "0", "middle": "-50%", "end": "-100%"}
    svg += "".join(f'<span class="tl-label-html" style="left:{100 * lx / W:.2f}%; top:{ly}px; transform:translate({shift[a]}, -100%)">'
                   f'{escape(text)}</span>' for lx, ly, a, text in labels_html)
    svg = f'<div class="timeline-wrap" style="height:{H}px">{svg}</div>'

    done = [(started, completed, online) for status, started, completed, online in _real_times(survey)
            if status == Response.COMPLETED]
    running_days = sum((b - a).total_seconds() for a, b, s in ph if s == "running") / 86400
    return {
        "svg": Markup(svg),
        "bin": per,
        "events": [(at, (labels.get(e) + (de_en(" (Fassung {n})", " (revision {n})", n=r[1:]) if e == "edit" and r
                                          else f": {r}" if e in NOTES and r else ""))
                    if e in labels
                    else reasons.get(r, closed)) for at, e, r in events(survey)],
        "field_end": survey.field_end if survey.status == Survey.RUNNING else None,
        "completed": len(done),
        # a diary study: the entries of the persons beside them (app/diary.py)
        "entries": __import__("app.diary", fromlist=["entry_tally"]).entry_tally(survey) if survey.sampling is not None else None,
        "running_days": running_days,
        "per_day": len(done) / running_days if running_days >= 1 else None,
        # a completion before its start (clock or data error) is no duration, as in export.completion_seconds
        # online only: paper sheets have no completion time (their start is the takeover), as in
        # export.completion_seconds (audit 2026-10-12, S-7)
        "median_minutes": _median([(completed - started).total_seconds() / 60 for started, completed, online in done
                                   if online and completed and started and completed >= started]),
    }


def _median(values):
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
