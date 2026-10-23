"""Time on a page, set per block (Block.settings "min_seconds", "max_seconds") for each of its pages:

  minimum  "Next" works only once the page has been on screen that long (a stimulus that has to be looked at,
           a text that has to be read). The page counts the seconds down next to the button; a "Next" that
           comes earlier all the same (no JavaScript) gets the page once more, with a note.
  maximum  a timer: the page counts down and goes on by itself when the time is up, with what is answered by
           then and without asking for the rest. Without JavaScript nothing goes on by itself.

The clock is the server's: Response.paradata["_hold"] = {"pos", "at"} says since when the page at `pos` is
on screen. Loading it again, an error or the other language do not start it anew; leaving the page (Next,
Back) does. Pages that went on by the timer are counted in Response.paradata["_timeup"] (export column
COLUMN).

Never timed: the consent, tasks with pages of their own (choice tasks, the timed assignment), the separated
section (it keeps nothing but its answers), dummy data. "Back" and the language switch are never held.

More time (WCAG 2.2.1, accessibility concept D4/E4): a time limit may be extended unless the time is essential. A
block says so with the tick "Die Zeit gehört zum Versuch" (Block.settings "time_essential", off by default). Without
it the page offers "Mehr Zeit" 20 s before the end, each time for the same time again, up to MORE_LIMIT times (WCAG 2.2.1
asks for at least ten extensions; UI/UX audit 2026-10-16, 80); the page sends time_more=<how often> from then on,
Response.paradata["_hold"]["more"] keeps that number for reloads of the page, and the pages whose time was extended
are counted in Response.paradata["_more"] (export column COLUMN_MORE). With the tick the time
stays as set, and the page says beforehand how much time there is.
"""
import math
from datetime import datetime

from app.ui_i18n import tr

MIN_LIMIT = 600    # seconds a page may hold "Next" at most
MAX_LIMIT = 3600   # seconds a timer may run at most
MORE_LIMIT = 10    # how often "Mehr Zeit" may extend one page (WCAG 2.2.1: at least ten times)
TOLERANCE = 1.5    # seconds the browser's clock may be ahead of the server's (the way of the page and back)
KEYS = ("min_seconds", "max_seconds")
COLUMN = ("time_up", "Höchstzeit: Zahl der Seiten, die nach Ablauf ihrer Zeit von selbst weitergingen")
COLUMN_EN = "Time limit: number of pages that went on automatically when their time was up"  # an English codebook
ESSENTIAL = "time_essential"  # Block.settings: the time is part of the experiment, no "Mehr Zeit" (D4)
COLUMN_MORE = ("time_more", "Höchstzeit: Zahl der Seiten, auf denen „Mehr Zeit“ genommen wurde")
COLUMN_MORE_EN = "Time limit: number of pages on which “More time” was taken"


def of(block):
    """{"min": seconds | None, "max": seconds | None} as set for the block."""
    saved = getattr(block, "settings", None) or {}

    def seconds(key, limit):
        value = saved.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= limit else None

    low, high = seconds("min_seconds", MIN_LIMIT), seconds("max_seconds", MAX_LIMIT)
    return {"min": low, "max": high if high is None or low is None or high > low else None}


def from_form(form, only=None):
    """What the block's form says: {key: seconds | None} for the fields that were sent (and are in `only`,
    if given)."""
    out = {}
    for key, limit in zip(KEYS, (MIN_LIMIT, MAX_LIMIT)):
        if key not in form or (only and key not in only):
            continue
        raw = (form.get(key) or "").strip()
        try:
            out[key] = min(max(int(raw), 1), limit) if raw else None
        except ValueError:
            out[key] = None
    return out


def essential(block):
    """The block's time is part of the experiment (the tick "Die Zeit gehört zum Versuch"): no "Mehr Zeit"."""
    return (getattr(block, "settings", None) or {}).get(ESSENTIAL) is True


def clean(settings):
    """The two times of stored block settings as they may be (file import): {key: seconds}, and the tick
    ESSENTIAL where it is set."""
    settings = settings if isinstance(settings, dict) else {}
    found = of(type("Saved", (), {"settings": settings}))
    return {**{key: found[name] for key, name in zip(KEYS, ("min", "max")) if found[name]},
            **({ESSENTIAL: True} if settings.get(ESSENTIAL) is True else {})}


def conflict(settings):
    """The timer of these settings would not run longer than "Next" is held."""
    low, high = (settings or {}).get("min_seconds"), (settings or {}).get("max_seconds")
    return bool(low and high and high <= low)


def any_timer(survey):
    """A block of the survey has a timer (the export has COLUMN then)."""
    return any(of(block)["max"] for block in survey.blocks)


def any_more(survey):
    """A block of the survey has a timer that can be extended (the export has COLUMN_MORE then)."""
    return any(of(block)["max"] and not essential(block) for block in survey.blocks)


def any_time(survey):
    """A block of the survey holds "Next" or has a timer (the methods text says so)."""
    return any(of(block)["min"] or of(block)["max"] for block in survey.blocks)


def page_times(response, block, page_items, position=None):
    """The times that hold for this page of this participation, or None: {"min", "max", "essential", "more"};
    with "Mehr Zeit" taken on the page at `position` (more), "max" is the extended time."""
    if not hasattr(response, "pid") or getattr(response, "source", "") == "dummy":
        return None
    times = held(block, page_items)
    if not times:
        return None
    times = {**times, "essential": bool(times["max"]) and essential(block), "more": 0}
    hold = (getattr(response, "paradata", None) or {}).get("_hold") or {}
    if times["max"] and not times["essential"]:
        times["max"] = max(30, times["max"])
    taken = taken_more(hold.get("more"))
    if times["max"] and not times["essential"] and position is not None and hold.get("pos") == position and taken:
        times["max"], times["more"] = (1 + taken) * times["max"], taken
    return times


def taken_more(value):
    """How often "Mehr Zeit" was taken: a number from the page or the store (True from before it could be taken more
    than once counts as 1), at most MORE_LIMIT."""
    if value is True:
        return 1
    try:
        return max(0, min(MORE_LIMIT, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def extend(response, position, times, key, wanted=1):
    """The page sent "Mehr Zeit" (field time_more = how often it was taken): kept with the page's clock; the page is
    counted once under `key` (the page's key, as for time_up), however often it was extended. False if it cannot be
    extended or nothing new was taken."""
    data = response.paradata or {}
    hold = data.get("_hold") or {}
    wanted = taken_more(wanted)
    if not times or not times["max"] or times["essential"] or wanted <= times["more"] or hold.get("pos") != position:
        return False
    counted = data.get("_more") or []
    response.paradata = {**data, "_hold": {**hold, "more": wanted},
                         "_more": counted if times["more"] else [*counted, key][-500:]}
    return True


def offer(times):
    """What the page needs for "Mehr Zeit" and for its note beforehand: {"essential", "extended", "extra": seconds
    "Mehr Zeit" adds (0: none to offer), "total": the time as set}."""
    taken = (times or {}).get("more") or 0
    total = times["max"] // (1 + taken) if times and times["max"] else 0
    essential_ = bool(times and times.get("essential"))
    return {"essential": essential_, "extended": bool(taken), "taken": taken, "limit": MORE_LIMIT, "total": total,
            "extra": total if total and not essential_ and taken < MORE_LIMIT else 0}


def held(block, page_items):
    """The times set for this page of the block, or None: the consent and tasks with pages of their own are
    never held (also the relative speed index of quality assurance leaves out exactly these pages)."""
    if not page_items or any(i.type == "consent" or i.kind.alone_on_page or i.kind.ends_page for i in page_items):
        return None
    times = of(block)
    return times if times["min"] or times["max"] else None


def shown(response, position, now):
    """The page at `position` is sent to the browser: its clock starts, unless it runs already."""
    data = response.paradata or {}
    if (data.get("_hold") or {}).get("pos") != position:
        response.paradata = {**data, "_hold": {"pos": position, "at": now.isoformat()}}


def waited(response, position, now):
    """Seconds the page at `position` has been on screen; None if its clock was never started."""
    hold = (response.paradata or {}).get("_hold") or {}
    if hold.get("pos") != position or not hold.get("at"):
        return None
    return max(0.0, (now - datetime.fromisoformat(hold["at"])).total_seconds())


def left(response):
    """The page is left: the next visit starts its clock anew."""
    if "_hold" in (getattr(response, "paradata", None) or {}):  # the separated section has no paradata
        response.paradata = {k: v for k, v in response.paradata.items() if k != "_hold"}


def too_early(times, seconds):
    return bool(times and times["min"] and seconds is not None and seconds < times["min"] - TOLERANCE)


def time_up(times, seconds, form):
    """The page says its timer ran out (field time_up), and by the server's clock it did."""
    return bool(times and times["max"] and form.get("time_up") and seconds is not None
                and seconds >= times["max"] - TOLERANCE)


def note_time_up(response, key):
    data = response.paradata or {}
    response.paradata = {**data, "_timeup": [*(data.get("_timeup") or []), key][-500:]}


def remaining(times, seconds):
    """What the page counts down: {"min": seconds "Next" is still held, "max": seconds until it goes on}."""
    gone = seconds or 0
    return {"min": max(0, math.ceil(times["min"] - gone)) if times["min"] else 0,
            "max": max(1, math.ceil(times["max"] - gone)) if times["max"] else 0}


def message(lang, seconds):
    return tr(lang, "Diese Seite bleibt noch {n} Sekunden stehen. Danach geht es mit „Weiter“ weiter.",
              "This page stays for another {n} seconds. After that, “Next” takes you on.", n=max(1, seconds))


def export_value(response):
    return len((response.paradata or {}).get("_timeup") or [])


def export_more(response):
    return len((response.paradata or {}).get("_more") or [])


def codebook_note(survey, lang=None, ui=None):
    """Which pages have a timer, for the codebook: block names in the survey language `lang` (None: the default
    language), the text in the reader's language `ui` (None: the page's; app.items.base.reader)."""
    from app.items.base import reader

    parts = []
    for n, block in enumerate(survey.blocks, start=getattr(survey, "block_start", 1)):
        times = of(block)
        if times["max"]:
            name = (block.titles or {}).get(lang or survey.default_language) or f"Block {n}"
            fixed = essential(block)
            parts.append(f"{name}: {times['max'] if fixed else max(30, times['max'])} s" + ((" (fixed, part of the experiment)" if reader(ui) == "en"
                                                         else " (fest, gehört zum Versuch)") if fixed else ""))
    if reader(ui) == "en":
        return ("Pages with a time limit go on automatically when it is up, with the answers given by then; missing "
                "required answers are then not asked for again. Time limit per page: " + "; ".join(parts)
                + ". Unless the time is part of the experiment, the page offers “More time” at least 20 s before the end, up to ten times, "
                "for the same time again (column time_more). 0 = no page ran out; without JavaScript no time runs out.")
    return ("Seiten mit Höchstzeit gehen nach deren Ablauf von selbst weiter, mit den bis dahin gegebenen Antworten; "
            "fehlende Pflichtantworten werden dann nicht nachgefordert. Höchstzeit je Seite: " + "; ".join(parts)
            + ". Gehört die Zeit nicht zum Versuch, bietet die Seite mindestens 20 s vor Ablauf bis zu zehnmal „Mehr Zeit“ an, für "
            "dieselbe Zeit noch einmal (Spalte time_more). 0 = keine Seite lief ab; ohne JavaScript läuft keine Zeit ab.")
