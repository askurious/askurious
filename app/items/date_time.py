"""A year, a month, a date, a time of day or a date with a time, picked from lists (nothing is typed).

Config "parts" says what is asked: "year" | "month" (month and year) | "date" | "time" | "datetime". Each part is
a list of its own (day, month, year, hour, minute), which phones show as their native wheel. The server checks
what the lists cannot: that all parts are chosen, that the day exists (30 February, 29 February outside a leap
year), that the year is one of the list, and that the answer is not in the future or not in the past
(config "span": "past" | "future" | "any").

Answers are stored and exported as in ISO 8601: 1987 (a year is a whole number), "1987-05", "1987-05-23",
"14:30", "1987-05-23T14:30". Such texts sort like the moments they name, which the conditions of the flow use
(compare()).

"Today" is not the same day everywhere: participants choose in their own time, the server only knows its own.
A day counts as "not in the future" once it has begun somewhere on earth (UTC+14) and as "not in the past"
while it lasts somewhere (UTC-12); the lists of years follow the same rule."""
import re
from datetime import date, datetime, timedelta, timezone

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_int, r_one  # rules for survey files, see base.py
from app.items.base import DONT_KNOW, AnswerError, ItemType, dont_know, in_lang, reader, whole
from app.lang import Both, de_en, english
from app.ui_i18n import tr

PARTS = {  # what is asked -> label in the editor
    "year": "Jahr",
    "month": "Monat und Jahr",
    "date": "Datum (Tag, Monat, Jahr)",
    "time": "Uhrzeit",
    "datetime": "Datum und Uhrzeit",
}
FIELDS = {"year": "y", "month": "my", "date": "dmy", "time": "hn", "datetime": "dmyhn"}  # the lists of each
SPANS = {"past": "Vergangenheit bis heute", "future": "ab heute", "any": "beliebig"}
STEPS = {1: "jede Minute", 5: "5 Minuten", 10: "10 Minuten", 15: "15 Minuten", 30: "30 Minuten"}
# the same labels in English, for the editor in English (app/lang.py)
PARTS_EN = {"year": "Year", "month": "Month and year", "date": "Date (day, month, year)", "time": "Time of day",
            "datetime": "Date and time"}
SPANS_EN = {"past": "past up to today", "future": "from today", "any": "any"}
STEPS_EN = {1: "every minute", 5: "5 minutes", 10: "10 minutes", 15: "15 minutes", 30: "30 minutes"}
PAST_YEARS = 110        # years back in the list unless "Jahr von" says otherwise (a year of birth)
FUTURE_YEARS = 10       # years ahead unless "Jahr bis" says otherwise
MIN_YEAR, MAX_YEAR = 1583, 2999  # the Gregorian calendar, which began in October 1582
MAX_YEARS = 300         # entries of the list of years
CODEBOOK = {  # parts -> (type, form of the values) in the codebook
    "year": ("Jahr", "JJJJ"),
    "month": ("Monat und Jahr", "JJJJ-MM"),
    "date": ("Datum", "JJJJ-MM-TT"),
    "time": ("Uhrzeit", "HH:MM (24 Stunden)"),
    "datetime": ("Datum und Uhrzeit", "JJJJ-MM-TTTHH:MM (24 Stunden)"),
}
# the form of the values in an English codebook (the type stays German: a key, export.TYPES_EN names it)
CODEBOOK_EN = {"year": "YYYY", "month": "YYYY-MM", "date": "YYYY-MM-DD", "time": "HH:MM (24 hours)",
               "datetime": "YYYY-MM-DDTHH:MM (24 hours)"}
ISO_RE = re.compile(r"(?:(?P<y>\d{4})(?:-(?P<m>\d{2})(?:-(?P<d>\d{2}))?)?)?(?:T?(?P<h>\d{2}):(?P<n>\d{2}))?")
MAX_BARS = 24           # bars of the distribution on the results page
DAY = 24 * 60           # minutes of the clock
MARDIA_JUPP = "Mardia, K. V., & Jupp, P. E. (2000). Directional statistics. Wiley. https://doi.org/10.1002/9780470316979"


def _now():
    """The moment in UTC (without time zone); a function of its own so tests can set the clock."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def window():
    """(earliest, latest): the local time where the day ends last (UTC-12) and where it begins first (UTC+14)."""
    now = _now()
    return now - timedelta(hours=12), now + timedelta(hours=14)


def iso(value):
    """A year, date or time someone typed into a condition, as answers are stored: "23.05.1987" -> "1987-05-23",
    "05/1987" -> "1987-05", "9:30" -> "09:30", "23.05.1987 9:30" -> "1987-05-23T09:30"; ISO texts stay.
    None if it is none of these."""
    day, _, clock = str(value or "").strip().replace("T", " ").partition(" ")
    if ":" in day and not clock:
        day, clock = "", day
    out = ""
    if day:
        m = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", day)
        if m:
            y, mo, d = m.groups()
        else:
            m = re.fullmatch(r"(?:(\d{1,2})\.)?(\d{1,2})[./](\d{4})", day)
            if not m:
                return None
            d, mo, y = m.groups()
        out = y + (f"-{int(mo):02}" if mo else "") + (f"-{int(d):02}" if d else "")
    if clock:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", clock.strip())
        if not m or (out and len(out) != 10):
            return None
        out += ("T" if out else "") + f"{int(m.group(1)):02}:{m.group(2)}"
    return out or None


def compare(answer, op, value):
    """A condition of the flow (eq, ne, lt, gt) on a stored date or time: True or False; None if the answer is
    none (a year is a plain number and compared as one). Both sides are compared as far as the shorter one
    goes: "= 1987" holds for every day of 1987, "< 05.1987" for everything before May 1987."""
    a = str(answer)
    if not ISO_RE.fullmatch(a) or ("-" not in a and ":" not in a):
        return None
    v = iso(value)
    if v is None:
        return None
    if (a[2] == ":") != (v[2] == ":"):  # a time of day against a date
        return op == "ne"
    n = min(len(a), len(v))
    a, v = a[:n], v[:n]
    return {"eq": a == v, "ne": a != v, "lt": a < v, "gt": a > v}[op]


def show(value, lang="de"):
    """A stored answer, or the start of one (a year, a month, an hour), as read in German: "23.05.1987",
    "05.1987", "14:30", "23.05.1987 14:30"; in English as ISO, as English participants enter it: "1987-05-23",
    "1987-05", "1987-05-23 14:30" ("12.05.1916" reads two ways in English; UI/UX audit 2026-10-07, W113)."""
    m = ISO_RE.fullmatch(str(value))
    if not m or not str(value):
        return str(value)
    if lang == "en":
        day = "-".join(x for x in (m.group("y"), m.group("m"), m.group("d")) if x)
        return f"{day} {m.group('h')}:{m.group('n')}".strip() if m.group("h") else day
    day = ".".join(x for x in (m.group("d"), m.group("m"), m.group("y")) if x)
    clock = f"{m.group('h')}:{m.group('n')}" if m.group("h") else ""
    return f"{day} {clock}".strip()


def median(values):
    """Median of sorted ISO answers of one shape (date, time, date and time, month, year), in that shape: with an
    even count the moment halfway between the two middle answers, rounded half up to the unit of the shape
    (2000-01-01 and 2000-12-31 give 2000-07-01, not the earlier of the two)."""
    n = len(values)
    lo, hi = values[(n - 1) // 2], values[n // 2]
    if lo == hi:
        return lo
    m = ISO_RE.fullmatch(lo)
    try:
        if m.group("d") and m.group("h"):  # date and time: minutes
            a, b = (datetime.fromisoformat(v) for v in (lo, hi))
            mid = a + timedelta(minutes=((b - a) // timedelta(minutes=1) + 1) // 2)
            return mid.strftime("%Y-%m-%dT%H:%M")
        if m.group("d"):  # date: days
            a, b = (date.fromisoformat(v).toordinal() for v in (lo, hi))
            return date.fromordinal((a + b + 1) // 2).isoformat()
        if m.group("h"):  # time of day: minutes
            a, b = (int(v[:2]) * 60 + int(v[3:5]) for v in (lo, hi))
            mid = (a + b + 1) // 2
            return f"{mid // 60:02d}:{mid % 60:02d}"
        if m.group("m"):  # month
            a, b = (int(v[:4]) * 12 + int(v[5:7]) - 1 for v in (lo, hi))
            mid = (a + b + 1) // 2
            return f"{mid // 12:04d}-{mid % 12 + 1:02d}"
        return f"{(int(lo) + int(hi) + 1) // 2:04d}"  # year
    except (ValueError, TypeError, AttributeError):
        return lo  # answers of different shapes: the lower middle one


def clock_order(values):
    """Times of day ("HH:MM", sorted) read on the clock (audit 2026-10-05): 23:00 and 01:00 are two hours apart, not
    22. The circle is cut at the largest gap between neighbouring answers; read from there the answers form an arc,
    whose middle is the circular median (the time with the least sum of distances on the clock; Mardia & Jupp,
    2000) as long as the arc spans at most half a day. Returns (the answers in the order of the arc, whether the
    arc runs over midnight, whether it spans more than half a day: then no time is clearly in the middle)."""
    minutes = [int(v[:2]) * 60 + int(v[3:5]) for v in values]
    gaps = [(b - a) for a, b in zip(minutes, minutes[1:])] + [minutes[0] + DAY - minutes[-1]]
    cut = max(range(len(gaps)), key=lambda i: gaps[i]) + 1  # the arc starts after the largest gap
    cut %= len(values)
    return values[cut:] + values[:cut], cut != 0, max(gaps) < DAY / 2


def clock_median(ordered):
    """Median of times of day in the order of their arc (clock_order): with an even count the minute halfway
    between the two middle answers along the arc, rounded half up."""
    n = len(ordered)
    lo, hi = ordered[(n - 1) // 2], ordered[n // 2]
    a, b = (int(v[:2]) * 60 + int(v[3:5]) for v in (lo, hi))
    if b < a:  # the two run over midnight
        b += DAY
    mid = (a + b + 1) // 2 % DAY
    return f"{mid // 60:02d}:{mid % 60:02d}"


def _periods(first, last, name):
    """The keys of every period from `first` to `last` (as cut from ISO texts: "10" hour, "2026-10-05" day,
    "2026-10" month, "2026" year, "202" decade), or None when the keys cannot be read."""
    from datetime import date, timedelta

    try:
        if name == "Stunde":
            return [f"{h:02d}" for h in range(int(first), int(last) + 1)]
        if name == "Tag":
            a, b = date.fromisoformat(first), date.fromisoformat(last)
            if (b - a).days > 400:
                return [None] * 401  # too many: a coarser step
            return [(a + timedelta(days=n)).isoformat() for n in range((b - a).days + 1)]
        if name == "Monat":
            y, m = int(first[:4]), int(first[5:7])
            out = []
            while f"{y:04d}-{m:02d}" <= last and len(out) < 1000:
                out.append(f"{y:04d}-{m:02d}")
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            return out
        if name in ("Jahr", "Jahrzehnt"):
            width = len(first)
            return [f"{n:0{width}d}" for n in range(int(first), int(last) + 1)]
    except ValueError:
        return None
    return None


@register
class DateTime(ItemType):
    key = "date_time"
    # fixed once real answers exist (audit 2026-10-10, N-3); "parts": a date would be read as a time (2026-10-12, N-7)
    read_keys = ("dont_know", "parts")
    import_rules = {"parts": r_one(*PARTS), "span": r_one(*SPANS), "year_min": r_int(MIN_YEAR, MAX_YEAR, none=True),
                    "year_max": r_int(MIN_YEAR, MAX_YEAR, none=True), "minute_step": r_one(*STEPS), "dont_know": r_flag}
    label = Both("Datum / Uhrzeit", "Date / time")
    short = Both("Jahr, Datum oder Uhrzeit", "Year, date or time")
    description = Both("Jahr, Monat, Datum oder Uhrzeit zum Auswählen aus Listen, z. B. Geburtsjahr; geprüft auf Gültigkeit",
                       "Year, month, date or time of day picked from lists, e.g. year of birth; checked for validity")
    order = 42
    fact_sheet = FactSheet(
        "classic",
        keywords=["Datum", "Date", "Uhrzeit", "Time", "Geburtsjahr", "Year of birth", "Kalender", "Calendar"],
        can=[("Jahr, Monat und Jahr, Datum, Uhrzeit oder Datum mit Uhrzeit.",
              "Year, month and year, date, time of day or date with time."),
             ("Zeitraum: Vergangenheit bis heute, ab heute oder beliebig.",
              "Range: past up to today, from today on or any."),
             ("Minutenschritte 1, 5, 10, 15 oder 30.", "Steps of 1, 5, 10, 15 or 30 minutes.")],
        good_for=[("Geburtsjahr, Datum eines Ereignisses, Uhrzeit des Aufstehens",
                   "Year of birth, date of an event, time of getting up"),
                  ("Angaben, die gültig sein müssen (kein 31. Februar)",
                   "Entries that must be valid (no 31 February)")],
        not_for=[("Dauern oder Mengen (dann Freitext mit Zahl oder Schieberegler)",
                  "Durations or amounts (then free text with a number or a slider)")],
        designs=["cross", "panel"],
        examples=[("In welchem Jahr sind Sie geboren?", "In which year were you born?")],
    )
    levels = ("metric",)
    # the editor's lists, labelled in the language of the page
    @property
    def parts(self):
        return PARTS_EN if english() else PARTS

    @property
    def spans(self):
        return SPANS_EN if english() else SPANS

    @property
    def steps(self):
        return STEPS_EN if english() else STEPS

    past_years, future_years = PAST_YEARS, FUTURE_YEARS

    def default_config(self):
        # dont_know: a tick "weiß nicht", set apart; the answer stays empty and the column <code>_dk says 1 (V41)
        return {"parts": "date", "span": "past", "year_min": None, "year_max": None, "minute_step": 5, "dont_know": False}

    # --- what is asked ------------------------------------------------------------------------

    def asked(self, item):
        chosen = (item.config or {}).get("parts")
        return chosen if chosen in PARTS else "date"

    def fields(self, item):
        """The lists of the item as letters: d(ay), m(onth), y(ear), h(our), n (minute)."""
        return FIELDS[self.asked(item)]

    def layout(self, item, lang):
        """The lists in the order they are shown: day, month, year; year first in Chinese. Then the time."""
        f = self.fields(item)
        order = "ymd" if (lang or "").startswith("zh") else "dmy"
        return [x for x in order if x in f] + [x for x in "hn" if x in f]

    def span(self, item):
        chosen = (item.config or {}).get("span")
        return chosen if chosen in SPANS and "y" in self.fields(item) else "any"

    def step(self, item):
        chosen = (item.config or {}).get("minute_step")
        return chosen if chosen in STEPS else 5

    def year_bounds(self, item):
        """(first, last) year of the list: as set in the editor, else PAST_YEARS back and FUTURE_YEARS ahead,
        and never beyond today where only the past or only the future is asked."""
        c, span = item.config or {}, self.span(item)
        early, late = window()
        lo = c.get("year_min") if c.get("year_min") is not None else (early.year if span == "future" else late.year - PAST_YEARS)
        hi = c.get("year_max") if c.get("year_max") is not None else (late.year if span == "past" else late.year + FUTURE_YEARS)
        if span == "past":
            hi = min(hi, late.year)
        if span == "future":
            lo = max(lo, early.year)
        return lo, hi

    def years(self, item):
        """The years to choose from: the latest first where the past is asked (a year of birth, "when did you
        last ..."), else the earliest first."""
        lo, hi = self.year_bounds(item)
        hi = min(hi, lo + MAX_YEARS - 1)
        return list(range(hi, lo - 1, -1)) if self.span(item) == "past" else list(range(lo, hi + 1))

    def minutes(self, item):
        return list(range(0, 60, self.step(item)))

    def months(self, lang):
        return [tr(lang, "Januar", "January"), tr(lang, "Februar", "February"), tr(lang, "März", "March"),
                tr(lang, "April", "April"), tr(lang, "Mai", "May"), tr(lang, "Juni", "June"),
                tr(lang, "Juli", "July"), tr(lang, "August", "August"), tr(lang, "September", "September"),
                tr(lang, "Oktober", "October"), tr(lang, "November", "November"), tr(lang, "Dezember", "December")]

    def choices(self, item, field, lang):
        """[(value, text)] of one list."""
        if field == "m":
            return list(enumerate(self.months(lang), start=1))
        if field == "y":
            return [(y, str(y)) for y in self.years(item)]
        if field == "d":
            return [(d, str(d)) for d in range(1, 32)]
        return [(x, f"{x:02}") for x in (range(24) if field == "h" else self.minutes(item))]

    def names(self, lang):
        """The name of each list: its first, empty entry and its label for screen readers."""
        return {"d": tr(lang, "Tag", "Day"), "m": tr(lang, "Monat", "Month"), "y": tr(lang, "Jahr", "Year"),
                "h": tr(lang, "Stunde", "Hour"), "n": tr(lang, "Minute", "Minute")}

    # --- editor -------------------------------------------------------------------------------

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        if not structural:
            return

        def year(name):
            y = whole((form.get(name) or "").strip())
            return min(max(y, MIN_YEAR), MAX_YEAR) if y is not None else None

        step = whole(form.get("minute_step") or "")
        item.config = {**(item.config or {}),
                       "parts": form.get("parts") if form.get("parts") in PARTS else "date",
                       "span": form.get("span") if form.get("span") in SPANS else "past",
                       "year_min": year("year_min"), "year_max": year("year_max"),
                       "minute_step": step if step in STEPS else 5, "dont_know": bool(form.get("dont_know"))}

    def validate(self, item):
        if "y" not in self.fields(item):
            return []
        c = item.config or {}
        lo, hi = self.year_bounds(item)
        problems = []
        if c.get("year_min") is not None and c.get("year_max") is not None and c["year_min"] > c["year_max"]:
            problems.append(de_en("„Jahr von“ ist größer als „Jahr bis“", "“Year from” is greater than “Year to”"))
        elif lo > hi:
            problems.append(de_en("„Jahr von“ liegt in der Zukunft, gefragt ist aber die Vergangenheit",
                                  "“Year from” lies in the future, but the past is asked") if self.span(item) == "past"
                            else de_en("„Jahr bis“ liegt in der Vergangenheit, gefragt ist aber die Zeit ab heute",
                                       "“Year to” lies in the past, but the time from today is asked"))
        elif hi - lo + 1 > MAX_YEARS:
            problems.append(de_en("zu viele Jahre in der Liste (höchstens {n})", "too many years in the list (at most {n})", n=MAX_YEARS))
        return problems

    def condition_ok(self, item, value):
        """True if a condition can compare this item's answers with `value` (flow.check)."""
        asked = self.asked(item)
        if asked == "year":
            return whole(str(value).strip()) is not None
        v = iso(value)
        return v is not None and (v[2] == ":") == (asked == "time")

    # --- participant side ---------------------------------------------------------------------

    def picked(self, item, value):
        """{list: chosen number or None} for the page: from a stored answer, or from what was sent when the
        page failed (display_value)."""
        if isinstance(value, dict):
            return {f: whole(str(value.get(f) or "")) for f in "dmyhn"}
        m = ISO_RE.fullmatch(str(value)) if value not in (None, "") else None
        return {f: int(m.group(f)) if m and m.group(f) else None for f in "dmyhn"}

    def display_value(self, item, form, meta=None):
        return {f: (form.get(f"i{item.id}_{f}") or "").strip() for f in self.fields(item)}

    def parse_answer(self, item, form, lang="de", meta=None):
        if dont_know(item) and form.get(f"i{item.id}_dk"):  # the tick wins over lists left on the page
            return DONT_KNOW
        fields = self.fields(item)
        raw = self.display_value(item, form)
        if not any(raw.values()):
            if item.required:
                raise AnswerError(tr(lang, "Bitte wählen Sie eine Antwort.", "Please choose an answer."))
            return None
        if not all(raw.values()):  # a date without its year is no answer, also where answering is voluntary
            raise AnswerError(tr(lang, "Bitte vervollständigen Sie Ihre Auswahl.", "Please complete your selection."))
        v = {f: whole(raw[f]) for f in fields}
        invalid = AnswerError(tr(lang, "Ungültiger Wert.", "Invalid value."))
        if any(x is None for x in v.values()):
            raise invalid
        if ("m" in v and not 1 <= v["m"] <= 12) or ("d" in v and not 1 <= v["d"] <= 31):
            raise invalid
        if "h" in v and (v["h"] > 23 or v["n"] > 59 or v["n"] % self.step(item)):
            raise invalid
        if "y" in v:
            lo, hi = self.year_bounds(item)
            if not lo <= v["y"] <= hi:
                raise AnswerError(tr(lang, "Bitte wählen Sie ein Jahr von {lo} bis {hi}.",
                                     "Please choose a year from {lo} to {hi}.", lo=lo, hi=hi))
        if "d" in v:
            try:
                date(v["y"], v["m"], v["d"])
            except ValueError:
                raise AnswerError(tr(lang, "Dieses Datum gibt es nicht.", "This date does not exist.")) from None
        if "y" in v:
            # compared as far as the answer goes: a year with the year, a day with the day, a moment to the minute
            moment = tuple(v[f] for f in "ymdhn" if f in v)
            early, late = (tuple((t.year, t.month, t.day, t.hour, t.minute)[:len(moment)]) for t in window())
            if self.span(item) == "past" and moment > late:
                raise AnswerError(tr(lang, "Die Angabe darf nicht in der Zukunft liegen.",
                                     "The answer must not be in the future."))
            if self.span(item) == "future" and moment < early:
                raise AnswerError(tr(lang, "Die Angabe darf nicht in der Vergangenheit liegen.",
                                     "The answer must not be in the past."))
        if fields == "y":
            return v["y"]
        day = "-".join(f"{v[f]:02}" for f in "ymd" if f in v)
        clock = f"{v['h']:02}:{v['n']:02}" if "h" in v else ""
        return day + ("T" if day and clock else "") + clock

    def pipeable(self, item):
        return True

    def pipe_text(self, item, answer, lang):
        """23.05.2000, 05.2000, 2000, 14:30 (ISO order 2000-05-23 in English and Chinese)."""
        if answer in (None, ""):
            return ""
        if answer == DONT_KNOW:
            return tr(lang, "weiß nicht", "don't know")
        day, _, clock = str(answer).partition("T") if "-" in str(answer) else ("", "", str(answer))
        if ":" not in clock and not day:  # a year alone
            day, clock = clock, ""
        if day and lang not in ("en",) and not lang.startswith("zh"):
            day = ".".join(reversed(day.split("-")))
        return ", ".join(p for p in (day, clock) if p)

    # --- duration, dummy data -------------------------------------------------------------------

    def seconds(self, item, read):
        return read + 1 + 2 * len(self.fields(item))

    def dummy_fields(self, item, rng, meta, answers=None):
        """A random answer the checks accept (an existing day, within the years and the span)."""
        fields, key = self.fields(item), f"i{item.id}"
        lo, hi = (self.year_bounds(item) if "y" in fields else (0, 0))
        hi = min(max(hi, lo), lo + MAX_YEARS - 1)
        draw = {"y": lambda: rng.randint(lo, hi), "m": lambda: rng.randint(1, 12), "d": lambda: rng.randint(1, 28),
                "h": lambda: rng.randint(0, 23), "n": lambda: rng.choice(self.minutes(item))}
        for _ in range(60):
            out = {f"{key}_{f}": str(draw[f]()) for f in fields}
            try:
                self.parse_answer(item, out)
                return out
            except AnswerError:  # e.g. a day of this year that is still to come
                continue
        early, late = window()  # a narrow range: the day that is today everywhere
        t = late if self.span(item) == "future" else early
        now = {"y": t.year, "m": t.month, "d": t.day, "h": t.hour, "n": t.minute - t.minute % self.step(item)}
        return {f"{key}_{f}": str(now[f]) for f in fields}

    # --- export -----------------------------------------------------------------------------

    def exact(self, item):
        """True if the answer names a day: together with other answers it can single out a person (a date of
        birth), so published data leave it out unless confirmed (export.open_columns)."""
        return "d" in self.fields(item)

    def dk_column(self, item):
        return f"{item.code}_dk"

    def open_columns(self, item):
        if not self.exact(item):
            return []  # a year or a month alone stays
        return [c for c, _ in self.export_columns(item) if c != self.dk_column(item)]  # a day, e.g. a date of birth

    def export_columns(self, item):
        cols = super().export_columns(item)
        if dont_know(item):  # the date column keeps dates only (a date variable in SPSS, Date in R)
            cols.append((self.dk_column(item), f"{item.text('question')} (weiß nicht)".lstrip()))
        return cols

    def export_values(self, item, answer):
        if not dont_know(item):
            return {item.code: None if answer == DONT_KNOW else answer}
        return {item.code: None if answer == DONT_KNOW else answer,
                self.dk_column(item): None if answer is None else int(answer == DONT_KNOW)}

    def missing_codes(self, item, column):
        return ()

    def date_kind(self, item, column):
        if column == self.dk_column(item):
            return None
        return {"month": "month", "date": "date", "time": "time", "datetime": "datetime"}.get(self.asked(item))

    def _range_note(self, item, ui=None):
        if "y" not in self.fields(item):
            return []
        c = item.config or {}
        lo, hi = self.year_bounds(item)
        fixed = c.get("year_min") is not None and c.get("year_max") is not None
        span = {"past": ("nicht in der Zukunft", "not in the future"), "future": ("nicht in der Vergangenheit", "not in the past"),
                "any": ("Vergangenheit und Zukunft", "past and future")}[self.span(item)]
        return [in_lang(ui, "Jahre {lo} bis {hi}", "years {lo} to {hi}", lo=lo, hi=hi)
                + ("" if fixed else in_lang(ui, " (Stand heute; die Liste wandert mit dem laufenden Jahr)",
                                            " (as of today; the list moves with the current year)")),
                in_lang(ui, *span)]

    def codebook_info(self, item, column, lang, ui=None):
        if column == self.dk_column(item):
            return "weiß nicht", in_lang(ui, "0 = Angabe gemacht; 1 = weiß nicht", "0 = answer given; 1 = don't know"), [in_lang(
                ui, "leer = nicht beantwortet; bei 1 ist {code} leer und zählt als fehlend",
                "empty = not answered; at 1, {code} is empty and counts as missing", code=item.code)]
        typ, values = CODEBOOK[self.asked(item)]
        if reader(ui) == "en":
            values = CODEBOOK_EN[self.asked(item)]
        notes = [in_lang(ui, "aus Listen gewählt, nicht getippt", "chosen from lists, not typed")] + self._range_note(item, ui)
        if "n" in self.fields(item) and self.step(item) > 1:
            notes.append(in_lang(ui, "Minuten in Schritten von {n}", "minutes in steps of {n}", n=self.step(item)))
        if "y" in self.fields(item) and self.asked(item) != "year":
            notes.append(in_lang(ui, "ISO 8601; in der .sav-Datei eine Datumsvariable", "ISO 8601; a date variable in the .sav file"))
        return typ, values, notes

    def questionnaire_lines(self, item, lang):
        lo_hi = "{}–{}".format(*self.year_bounds(item)) if "y" in self.fields(item) else ""
        span = {"past": ", not in the future", "future": ", not in the past", "any": ""}[self.span(item)]
        step = f", minutes in steps of {self.step(item)}" if "n" in self.fields(item) and self.step(item) > 1 else ""
        what = {"year": "year", "month": "month and year", "date": "date (day, month, year)", "time": "time of day",
                "datetime": "date and time of day"}[self.asked(item)]
        return [f"  - Chosen from lists: {what}" + (f", years {lo_hi}" if lo_hi else "") + span + step] + (
            [f"  - Plus \"don't know\": `{self.dk_column(item)}` = 1, `{item.code}` left empty (counts as missing)"]
            if dont_know(item) else [])

    # --- results page ---------------------------------------------------------------------------

    def result_column(self, item, column):
        # a year is a number (M, Md, SD); dates and times get their own summary (results()); "weiß nicht" is
        # counted there, its column has no summary of its own
        if column == self.dk_column(item):
            return None, "", None
        return ("auto", "", None) if self.asked(item) == "year" else (None, "", None)

    def results(self, item, data, passes=()):
        """Earliest, median and latest answer and their distribution over the rows in `data` (export rows after
        the results filter); in a loop every pass counts as one answer. The bars take the finest step that
        gives at most MAX_BARS of them: days, months, years or decades; a time of day: hours."""
        cols = [f"{item.code}_{n}" for n in passes] or [item.code]

        def valid(v):
            return v not in (None, "") and bool(ISO_RE.fullmatch(str(v)))

        values = sorted(str(r.get(c)) for r in data for c in cols if valid(r.get(c)))
        out = {"n": len(values), "persons": sum(1 for r in data if any(valid(r.get(c)) for c in cols)), "rows": []}
        if dont_know(item):  # counted apart, never among the dates (V41)
            dks = [f"{self.dk_column(item)}_{n}" for n in passes] or [self.dk_column(item)]
            out["dont_know"] = sum(1 for r in data for c in dks if str(r.get(c)) == "1")
        if not values:
            return out
        clock = values[0][2] == ":"
        steps = [(2, "Stunde")] if clock else [(10, "Tag"), (7, "Monat"), (4, "Jahr"), (3, "Jahrzehnt")]
        steps = [(n, name) for n, name in steps if n <= len(values[0])]
        for width, name in steps:
            counts = {}
            for v in values:
                counts[v[:width]] = counts.get(v[:width], 0) + 1
            # every period between the first and the last answer, the empty ones with 0: a gap stays visible
            # (UI/UX audit 2026-10-05, V59)
            every = _periods(min(counts), max(counts), name)
            last = (width, name) == steps[-1]
            if every is not None:
                if len(every) <= MAX_BARS or last:
                    if None not in every:
                        counts = {k: counts.get(k, 0) for k in every}
                    break
                continue
            if len(counts) <= MAX_BARS or last:
                break

        # in the language of the page (the results are kept per language, results_store): 14 Uhr or 14:00
        lang = "en" if english() else "de"

        def label(key):
            if name == "Stunde":
                return f"{key}:00" if lang == "en" else f"{key} Uhr"
            return f"{key}0–{key}9" if name == "Jahrzehnt" else show(key, lang)

        out.update(min=show(values[0], lang), max=show(values[-1], lang), median=show(median(values), lang), step=name,
                   rows=[{"label": label(k), "count": n, "share": n / len(values)} for k, n in sorted(counts.items())],
                   filled=True)
        if clock and len(values[0]) == 5:
            # on the clock: the answers over midnight (23:00, 01:00) have their median at midnight, not at noon;
            # earliest and latest are the ends of the arc
            ordered, out["over_midnight"], out["wide"] = clock_order(values)
            out.update(min=show(ordered[0], lang), max=show(ordered[-1], lang), median=clock_median(ordered))
        return out
