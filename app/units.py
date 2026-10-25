"""Units for numeric answers (sliders, constant sum): plain number (with a free unit text),
currency, or percent. Formatting follows the language: German 1.234,5 € and 25 %,
English €1,234.5 and 25%. Numbers are rounded half up (kaufmännisch) everywhere: half_up, fixed."""
import math
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

FORMATS = {"number": "Zahl", "currency": "Währung", "percent": "Prozent"}
CURRENCIES = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF"}

# the survey languages that write numbers like English: a decimal point and a comma between thousands (1,000.5);
# every other one writes a decimal comma (1.000,5). Reading and writing numbers for participants follow this
POINT_LANGUAGES = ("en", "zh-Hans", "ar")  # Arabic with Western digits, as online questionnaires write it


def writes_like_english(lang):
    """The language writes numbers with a decimal point and commas between thousands (POINT_LANGUAGES)."""
    return lang in POINT_LANGUAGES


def affixes(fmt, currency="EUR", unit="", lang="de"):
    """(prefix, suffix) around the number."""
    de = not writes_like_english(lang)
    if fmt == "percent":
        return ("", " %") if de else ("", "%")
    if fmt == "currency":
        sym = CURRENCIES.get(currency, "€")
        if de:
            return "", f" {sym}"
        return (f"{sym} " if sym.isalpha() else sym), ""
    return "", (f" {unit}" if unit else "")


def exact(number):
    """A number as the Decimal of its text with 12 significant digits: 0.1 + 0.2 -> 0.3, 1e-05 -> 0.00001."""
    return Decimal(format(float(number), ".12g"))


def decimals(step):
    """Number of decimals implied by a step such as 1, 0.5, 0.25 or 0.00001. Read from the Decimal, not from "%g":
    that writes 0.00001 as 1e-05 and so gave 0 decimals (audit 2026-10-23, M8)."""
    try:
        number = float(step)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number) or number == 0:
        return 0
    return max(0, -exact(number).normalize().as_tuple().exponent)


def class_of(value, start, width, count=None):
    """The number of the class [start + i·width, start + (i + 1)·width) that holds `value`, counted from 0; below
    start negative. A value on a class edge belongs to the upper class, also where binary floating point puts it
    a hair below: 0.3 with classes of 0.1 from 0 is class 3, though 0.3 / 0.1 is 2.9999999999999996 (audit
    2026-10-05, R-3/R-4). count: kept within 0 … count - 1 (the first and last class take what lies beyond)."""
    i = math.floor((value - start) / width + 1e-9)
    return i if count is None else min(max(i, 0), count - 1)


def percent(share):
    """A share (0 … 1) as a whole percentage, rounded half up: 0.125 -> 13 (round() gives 12)."""
    return int((Decimal(repr(float(share))) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def half_up(value, places=0):
    """A number rounded half up (kaufmännisch, DIN 1333) as a Decimal, read from its shortest decimal text:
    2.5 -> 3, 0.125 -> 0.13 and 1.005 -> 1.01 (round() and "%.2f" give 2, 0.12 and 1.00: half to even, and
    1.005 is a little less than 1.005 in binary). None for no finite number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    try:
        return Decimal(repr(number)).quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP)
    except InvalidOperation:  # more digits than a Decimal holds (far beyond any answer)
        return None


def round_half_up(value, places=0):
    """Like round(value, places), but half up: an int for places 0, else a float; None stays None."""
    d = half_up(value, places)
    if d is None:
        return value if value is None else float(value)
    return int(d) if not places else float(d)


def p_places(p, places=3, most=6, alpha=0.05):
    """Decimals of a p value: `places`, more where a p just below alpha would read as alpha (p = .04996 shows as
    .04996, not as .050 beside "significant"; audit 2026-10-05, I-7), at most `most`."""
    if p is None or p >= alpha:
        return places
    while places < most and half_up(p, places) >= Decimal(repr(alpha)):
        places += 1
    return places


def p_bare(p, lang="de"):
    """A p value in APA style without "p": no leading zero, "< ,001" below (German with decimal comma), and the
    decimals of p_places. One notation on every page and in tables (UI/UX audit 2026-10-09, X61, Ralf: "wie
    empfohlen"); "–" for no value."""
    if p is None:
        return "–"
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "–"
    if not math.isfinite(p):
        return "–"
    mark = "." if writes_like_english(lang) else ","
    if p < 0.001:
        return f"< {mark}001"
    text = fixed(p, p_places(p))
    if text.startswith("0."):
        text = text[1:]
    return text.replace(".", mark)


def fixed(value, places=2, signed=False):
    """A number with a fixed number of decimals and a decimal point, rounded half up: the "%.2f" of displays.
    signed: a plus before positive values ("%+.2f")."""
    if value is None:
        return ""
    d = half_up(value, places)
    if d is None:
        return f"{float(value):{'+' if signed else ''}.{places}f}"
    if d == 0:  # -0.004 rounds to 0: no sign at all, neither "-0.00" nor "+0.00" (audit 2026-10-05, R-7)
        return format(abs(d), f".{int(places)}f")
    return format(d, f"{'+' if signed else ''}.{int(places)}f")


def number(value, places=0, lang="de", signed=False):
    d = half_up(value, places)
    text = f"{abs(d):,.{places}f}" if d is not None else f"{abs(value):,.{places}f}"
    if not writes_like_english(lang):
        text = text.replace(",", " ").replace(".", ",").replace(" ", ".")
    # the sign of the rounded number: -0.4 with no places is "0", not "−0" (R-7)
    negative, positive = (d < 0, d > 0) if d is not None else (value < 0, value > 0)
    sign = "−" if negative else ("+" if signed and positive else "")
    return sign + text


def stat(value, places=2, lang="de", signed=False):
    """The one way figures are written on the results page, on dashboards and in the Word tables (UI/UX audit
    2026-10-05, V70): `places` decimals rounded half up, German with a decimal comma and a dot between thousands
    (93.367,1), English with a point and commas; a minus sign (−), no "−0"; "–" for no value."""
    if value is None or value == "":
        return "–"
    try:
        number_ = float(value)
    except (TypeError, ValueError):
        return "–"
    if not math.isfinite(number_):
        return "–"
    return number(number_, int(places), lang, signed)


def stat_places(values, places=2):
    """The decimals of one measure (V71): none for values of 1.000 and more (93.367 km, decimals say nothing there),
    else `places`."""
    vals = [abs(float(v)) for v in values if v is not None]
    return 0 if vals and max(vals) >= 1000 else places


def whole(values):
    """True if every value is a whole number: medians and ranges of such answers carry no decimals (V71)."""
    vals = [float(v) for v in values if v is not None]
    return bool(vals) and all(v == int(v) for v in vals if math.isfinite(v))


def fmt(value, fmt="number", currency="EUR", unit="", lang="de", places=0, signed=False):
    if value is None or value == "":
        return ""
    pre, suf = affixes(fmt, currency, unit, lang)
    return f"{pre}{number(float(value), places, lang, signed)}{suf}"


def tick_labels(values, signed=False, most=3, lang="de"):
    """The labels of the ticks of one axis, all with the same number of decimals: as many as the finest tick
    needs (at most `most`), so an axis reads 0,0 · 0,5 · 1,0 and not 0 · 0,5 · 1. signed: a plus before
    positive values."""
    values = [float(v) for v in values]
    places = next((d for d in range(most + 1) if all(abs(round(v, d) - v) < 1e-9 for v in values)), most)
    return [number(0.0 if round(v, places) == 0 else v, places, lang, signed) for v in values]


# the researchers' and admins' pages write numbers and dates in their page language (concept "Mehrsprachige
# Oberfläche", E11): English 1,234.5 and 12%, German 1.234,5 and 12 % as before. Participant pages (the survey's
# language, passed on explicitly), exports and stored values keep their own formats: the Jinja helpers in
# app/__init__.py are the only ones that read the page language, the functions here stay German unless told.
SURVEY_BLUEPRINTS = ("participate", "preview_share", "live_public", "board_share")


def page_lang(template=None):
    """The language numbers take on the page being built: "en" on a researcher or admin page in English, else
    "de" (German pages, participant pages and their templates wherever they are shown, outside a request).
    template: the name of the template asking; participant templates (also in the editor's preview) stay German."""
    from flask import has_request_context, request

    if not has_request_context() or request.blueprint in SURVEY_BLUEPRINTS:
        return "de"
    name = template or ""
    if name.startswith("participant/") or name.startswith("boards/view") or (
            name.startswith("items/") and "/participant" in name):
        return "de"
    from app import lang

    return "en" if lang.english() else "de"


def decimal_text(value, places=2, signed=False, lang="de"):
    """The "%.1f" of a display in a language: fixed() with a decimal comma in German and no thousands separator (as
    the templates wrote it by hand, fixed(1)|replace(".", ",")), a decimal point in English."""
    text = fixed(value, places, signed)
    return text if writes_like_english(lang) else text.replace(".", ",")


def whole_text(value, lang="de"):
    """A count with thousands separators: 93.367 in German, 93,367 in English (as the templates wrote it by hand:
    "{:,}".format(v)|replace(",", "."))."""
    text = f"{value:,}"
    return text if writes_like_english(lang) else text.replace(",", ".")


def percent_text(share, lang="de", places=1):
    """A share as a percentage with its sign, one decimal by default: 12,5 % in German, 12.5% in English (affixes).
    Shares in results, dashboards and downloads all keep one decimal, also when whole (30,0 %), so a column or a
    figure reads alike (Ralf, 2026-10-26; APA 7, 6.36 leaves the places open). places=0 for progress and other
    shares of the operation (|percent0). Rounded half up from the decimal text of the share: 0.6145 -> 61,5."""
    if share is None:
        return "–"
    pre, suf = affixes("percent", lang=lang)
    try:
        value = (Decimal(repr(float(share))) * 100).quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP)
    except (TypeError, ValueError, InvalidOperation):
        return "–"
    if value == 0:
        value = abs(value)  # no "-0,0 %"
    text = f"{value:.{int(places)}f}"
    return f"{pre}{text if writes_like_english(lang) else text.replace('.', ',')}{suf}"



# --- numbers researchers type -----------------------------------------------------------------------------------
# One reader for the number fields of the researchers' pages (editor, results page, administration; Mathe-Audit
# 2026-10-06, Z-3/Z-4): read like the answers of a number field (items/text._num) in the language of the page, so
# "1.000" is a thousand in German and "1,000" one in English (decisions.md). One separator before exactly three
# digits the other way round ("1,000" in German, "1.000" in English) is read as a decimal, as conditions read it,
# and the page says so after saving (entry_notes), as the pre-launch check does for conditions (conditions.number_problem).
_GROUPED = {",": re.compile(r"[+\-−–]?[1-9]\d{0,2},\d{3}"), ".": re.compile(r"[+\-−–]?[1-9]\d{0,2}\.\d{3}")}
_SYMBOLS = re.compile(r"[€$£%]|CHF")


def entry_lang(lang=None):
    """The language a number typed by a researcher is read in: `lang`, else the language of the page."""
    return lang or page_lang()


def shown_plain(value, lang="de"):
    """A number as typed, without thousands separators or exponent, with the decimal mark of `lang` ("1500,5")."""
    text = format(Decimal(repr(float(value))), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    text = "0" if text in ("-0", "") else text
    return text if writes_like_english(lang) else text.replace(".", ",")


def ambiguous_entry(text, lang="de"):
    """(read, whole) if `text` could be a decimal or a thousand in `lang` and is read as the decimal ("1,000" in
    German: 1 or 1000?), else None."""
    from app.items.text import _num

    text = re.sub(r"[\s  ]", "", _SYMBOLS.sub("", str(text or "")))
    if not _GROUPED["." if writes_like_english(lang) else ","].fullmatch(text):
        return None
    try:
        return _num(text, lang), text.replace(",", "").replace(".", "")
    except ValueError:
        return None


def entry_problem(text, lang="de"):
    """Why a number typed by a researcher cannot be read for sure, or None (the wording of conditions.number_problem)."""
    from app.lang import de_en

    found = ambiguous_entry(text, lang)
    if found is None:
        return None
    read, whole_ = found
    return de_en("„{text}“ ist nicht eindeutig ({read} oder {whole}?) und wird als {read} gelesen; bitte ohne "
                 "Tausendertrennzeichen schreiben, z. B. {whole}",
                 "“{text}” is ambiguous ({read} or {whole}?) and is read as {read}; please write it without a "
                 "thousands separator, e.g. {whole}", text=str(text).strip(), read=shown_plain(read, lang),
                 whole=whole_.lstrip("+").replace("−", "-").replace("–", "-"))


def read_entry(text, lang=None, default=None, known=None, note=True):
    """A number a researcher typed into a field, read in `lang` (None: the language of the page) like the answers of
    a number field: "1.000" and "1.299,50" in German, "1,000" and "1,299.50" in English; a currency or percent sign
    is left out. default: for an empty field and for what is no number. note: what cannot be read for sure goes to
    entry_notes (a value in `known`, the value stored so far, is not noted again: a field shows it as "1,125")."""
    from app.items.text import _num
    from app.lang import de_en

    lang = entry_lang(lang)
    raw = str(text if text is not None else "").strip()
    if not raw:
        return default
    try:
        value = _num(_SYMBOLS.sub("", raw).strip(), lang)
    except ValueError:
        if note:
            _note(de_en("„{text}“ ist keine Zahl und wurde nicht übernommen.", "“{text}” is not a number and was not "
                        "applied.", text=raw[:40]))
        return default
    olds = known if isinstance(known, (list, tuple, set)) else [known]
    if note and not any(o is not None and isinstance(o, (int, float)) and abs(float(o) - value) < 1e-12 for o in olds):
        problem = entry_problem(raw, lang)
        if problem:
            _note(problem)
    return value


def _note(text):
    from flask import g, has_app_context

    if not has_app_context():
        return
    notes = g.setdefault("entry_notes", [])
    if text not in notes:
        notes.append(text)


def note(text):
    """A note for the researcher about what the editor did not take from a form, said after saving like the notes of
    read_entry (entry_notes)."""
    _note(text)


def entry_notes():
    """What read_entry could not read for sure in this request (taken out: each is said once)."""
    from flask import g, has_app_context

    if not has_app_context():
        return []
    return g.pop("entry_notes", None) or []
