"""Free text answers (with optional input validation) and plain information text."""
import functools
import math
import re

from app import units
from app.emails import is_email
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_int, r_num, r_one, r_text  # rules for survey files, see base.py
from app.items.base import MAX_NUMBER, PLAIN_NUMBER_RE, AnswerError, ItemType, whole
from app.lang import Both, de_en
from app.ui_i18n import pick

VALIDATIONS = {
    "none": "keine",
    "integer": "ganze Zahl",
    "decimal": "Dezimalzahl",
    "email": "E-Mail-Adresse",
    "plz_de": "Postleitzahl (DE)",
    "regex": "eigener regulärer Ausdruck",
}
# what a field asks for, as the HTML token "autocomplete" (WCAG 1.3.5): browsers and assistive tools fill it in. Optional,
# empty by default because surveys are anonymous (UI/UX audit 2026-10-16, 103); {token: (German, English)}
PURPOSES = {
    "name": ("Name", "Name"),
    "email": ("E-Mail-Adresse", "Email address"),
    "tel": ("Telefonnummer", "Phone number"),
    "postal-code": ("Postleitzahl", "Postcode"),
    "bday-year": ("Geburtsjahr", "Year of birth"),
    "country-name": ("Land", "Country"),
}
PLZ_RE = re.compile(r"^[0-9]{5}\Z")  # ASCII only: \d also takes e.g. full-width digits
MAX_PATTERN = 300
MAX_REGEX_INPUT = 500  # own patterns check short inputs (codes, ids); longer answers are refused before matching


@functools.lru_cache(maxsize=256)
def risky_pattern(pattern):
    r"""True for patterns whose matching time can explode with the input (a repetition inside an unbounded
    repetition, e.g. "(a+)+" or "(\w*)*"): one answer could then keep a server process busy for minutes.
    Such patterns are reported by the pre-launch check and not applied to answers."""
    try:
        from re import _parser as parser
    except ImportError:  # Python < 3.11
        import sre_parse as parser
    try:
        tree = parser.parse(pattern)
    except Exception:
        return False  # invalid patterns are reported separately

    def unbounded(op, av):
        return str(op) in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT") and av[1] >= 100  # MAXREPEAT or large

    def walk(items, inside):
        for op, av in items:
            name = str(op)
            if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
                loop = unbounded(op, av)
                if loop and inside:
                    return True
                if walk(av[2], inside or loop):
                    return True
            elif name == "SUBPATTERN":
                if walk(av[3], inside):
                    return True
            elif name == "BRANCH":
                if any(walk(branch, inside) for branch in av[1]):
                    return True
            elif name in ("ASSERT", "ASSERT_NOT"):
                if walk(av[1], inside):
                    return True
            elif name == "ATOMIC_GROUP":
                if walk(av, inside):
                    return True
        return False

    return walk(tree, False) or _slow_on_probes(pattern, tree)


try:  # google-re2: matching time grows linearly with the input, whatever the pattern (audit 2026-10-13, KR-1)
    import re2
except ImportError:  # pragma: no cover - only on a machine without the wheel; then the checks above guard `re`
    re2 = None

# Python's \w, \d and \s read every script (ä, é, ١); RE2's read ASCII only. The translation keeps what
# researchers wrote meaning the same: outside a class as a class of its own, inside a class as its members.
_RE2_CLASSES = {"w": r"\p{L}\p{N}_", "d": r"\p{Nd}", "s": r"\t\n\x0b\f\r\x1c-\x1f\x85\p{Z}"}


def _to_re2(pattern):
    r"""The pattern in RE2's syntax where the two differ only in spelling: Unicode \w \d \s (and \W \D \S outside a
    class), \Z as \z (the same at the end of a full match). Everything else is left for RE2 to accept or refuse."""
    out, i, in_class = [], 0, False
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            nxt = pattern[i + 1]
            if nxt in _RE2_CLASSES:
                out.append(_RE2_CLASSES[nxt] if in_class else "[" + _RE2_CLASSES[nxt] + "]")
            elif nxt.lower() in _RE2_CLASSES and not in_class:
                out.append("[^" + _RE2_CLASSES[nxt.lower()] + "]")
            elif nxt == "Z":
                out.append(r"\z")
            else:
                out.append(ch + nxt)
            i += 2
            continue
        if ch == "[" and not in_class:
            in_class = True
            out.append(ch)
            i += 1
            if i < len(pattern) and pattern[i] == "^":
                out.append("^")
                i += 1
            if i < len(pattern) and pattern[i] == "]":  # "[]" or "[^]": the first "]" is a literal
                out.append(r"\]")
                i += 1
            continue
        if ch == "]" and in_class:
            in_class = False
        out.append(ch)
        i += 1
    return "".join(out)


@functools.lru_cache(maxsize=256)
def _compiled_re2(pattern):
    """The RE2 form of a researcher's pattern, or None if RE2 cannot read it (back-references, lookaround, possessive
    or atomic groups, verbose mode, a repetition over 1000)."""
    options = re2.Options()
    options.log_errors = False  # a refused pattern is a pre-launch check message, not a line in the server log
    try:
        return re2.compile(_to_re2(pattern), options)
    except Exception:
        return None


def pattern_problem(pattern):
    """What keeps a researcher's pattern from being applied: "invalid" (Python cannot read it), "unsupported" (RE2
    cannot), "risky" (only without RE2: it can backtrack for minutes), or None. Such a pattern is reported by the
    pre-launch check and not applied to answers, so participants are never blocked by it."""
    try:
        re.compile(pattern)
    except (re.error, RecursionError, OverflowError):
        return "invalid"
    if re2 is not None:
        return None if _compiled_re2(pattern) is not None else "unsupported"
    return "risky" if risky_pattern(pattern) else None


def pattern_matches(pattern, value):
    """Whether the whole answer fits the researcher's pattern; None when the pattern is not applied
    (pattern_problem). With RE2 the time is linear in the answer, so no pattern can keep a worker busy (KR-1)."""
    if pattern_problem(pattern):
        return None
    if re2 is not None:
        return _compiled_re2(pattern).fullmatch(value) is not None
    return re.fullmatch(pattern, value) is not None


PROBE_SECONDS = 0.03   # one match of at most MAX_REGEX_INPUT characters takes microseconds unless the pattern backtracks


def _probe_letters(tree):
    """Characters the pattern itself reads (its literals, the first of each class, one per category), for inputs
    that keep it busy; at most six."""
    found = []

    def add(ch):
        if ch not in found and ch not in ("\n", "\x00"):
            found.append(ch)

    def walk(items):
        for op, av in items:
            name = str(op)
            if name == "LITERAL":
                add(chr(av))
            elif name == "ANY":
                add("a")
            elif name == "IN":
                for kind, value in av:
                    kind = str(kind)
                    if kind == "LITERAL":
                        add(chr(value))
                    elif kind == "RANGE":
                        add(chr(value[0]))
                    elif kind == "CATEGORY":
                        add({"CATEGORY_DIGIT": "1", "CATEGORY_SPACE": " "}.get(str(value), "a"))
            elif name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
                walk(av[2])
            elif name == "SUBPATTERN":
                walk(av[3])
            elif name == "BRANCH":
                for branch in av[1]:
                    walk(branch)
            elif name in ("ASSERT", "ASSERT_NOT"):
                walk(av[1])
            elif name == "ATOMIC_GROUP":
                walk(av)

    try:
        walk(tree)
    except Exception:  # an unknown node of a newer parser: the plain probe below still runs
        pass
    return (found or ["a"])[:6]


def _slow_on_probes(pattern, tree):
    """Try the pattern on inputs made to keep it busy: runs of the characters it reads, ending in one it cannot
    match, growing from 1 to MAX_REGEX_INPUT characters. The structural check above knows one shape of a
    dangerous pattern; "(a|a)*b", "a*a*a*a*b" or "(.*a){12}b" have other shapes and take seconds to minutes on
    40 characters. The first probe over PROBE_SECONDS (measured twice) ends the test, so a dangerous pattern
    costs a fraction of a second here, once per server process, instead of minutes per answer."""
    import time

    try:
        compiled = re.compile(pattern)
    except (re.error, RecursionError, OverflowError):
        return False  # invalid patterns are reported separately
    letters = _probe_letters(tree)
    units = letters + [a + b for a in letters[:3] for b in letters[:3] if a != b]
    lengths, n = [], 1
    while n < MAX_REGEX_INPUT:  # every length while times can double per character, then in steps of a third
        lengths.append(n)
        n = n + 1 if n < 40 else int(n * 1.3) + 1
    lengths.append(MAX_REGEX_INPUT)

    def seconds(text):
        started = time.perf_counter()
        compiled.fullmatch(text)
        return time.perf_counter() - started

    for unit in units:
        for tail in ("\x00", "\n", ""):
            for length in lengths:
                text = (unit * (length // len(unit) + 1))[:max(length - len(tail), 0)] + tail
                if seconds(text) > PROBE_SECONDS and seconds(text) > PROBE_SECONDS:  # twice: not a hiccup of the machine
                    return True
    return False


def unsupported_pattern_text():
    return de_en("regulärer Ausdruck nutzt eine Schreibweise, die hier nicht geprüft werden kann (z. B. Rückverweis "
                 "„\\1“, Vorausschau „(?=…)“ oder Rückschau „(?<=…)“); er wird nicht angewendet, bitte umschreiben",
                 "regular expression uses a construct that cannot be checked here (e.g. a back-reference “\\1”, "
                 "lookahead “(?=…)” or lookbehind “(?<=…)”); it is not applied, please rewrite it")


MESSAGES = {
    "de": {
        "integer": "Bitte geben Sie eine ganze Zahl ein.",
        "decimal": "Bitte geben Sie eine Zahl ein.",
        "range": "Bitte geben Sie eine Zahl von {lo} bis {hi} ein.",
        "min": "Bitte geben Sie eine Zahl ab {lo} ein.",
        "max": "Bitte geben Sie eine Zahl bis {hi} ein.",
        "email": "Bitte geben Sie eine gültige E-Mail-Adresse ein.",
        "plz_de": "Bitte geben Sie eine fünfstellige Postleitzahl ein.",
        "regex": "Die Eingabe hat nicht das erwartete Format.",
        "required": "Bitte geben Sie eine Antwort ein.",
        "too_long": "Die Antwort ist zu lang.",
    },
    "en": {
        "integer": "Please enter a whole number.",
        "decimal": "Please enter a number.",
        "range": "Please enter a number from {lo} to {hi}.",
        "min": "Please enter a number of at least {lo}.",
        "max": "Please enter a number of at most {hi}.",
        "email": "Please enter a valid email address.",
        "plz_de": "Please enter a five-digit postal code.",
        "regex": "The input does not have the expected format.",
        "required": "Please enter an answer.",
        "too_long": "The answer is too long.",
    },
}


def _num(value, lang="de"):
    """Parse '1.5', '1,5', ' 3 ', '1.000', '1.299,5' -> float; raises ValueError (also for "nan" and
    "inf", which float() accepts).

    Both separators: the last one is the decimal separator. One separator several times: thousands.
    A single one: the decimal separator, except the language's thousands separator ("." in German,
    "," in English) before exactly three digits and after one to three digits not starting with 0:
    "1.000" is 1000 in German and "1,000" is 1000 in English (and in every language that writes numbers like
    English, units.writes_like_english), while "1.5", "1,5" and "0.125" stay decimals. With both separators the
    thousands come in groups of three ("1.299,5"; "1,5.3" is no number). A dash as autocorrection writes it
    ("–5") is a minus like "−"."""
    from app.units import writes_like_english

    v = re.sub(r"[\s\u00a0\u202f'’]", "", value).replace("−", "-").replace("–", "-")
    if "," in v and "." in v:
        dec = "," if v.rfind(",") > v.rfind(".") else "."
        group = "." if dec == "," else ","
        if not re.fullmatch(r"[+-]?\d{1,3}(" + re.escape(group) + r"\d{3})+" + re.escape(dec) + r"\d*", v):
            raise ValueError(value)  # "1,5.3", "1.2,5": thousands not in groups of three
        v = v.replace(group, "").replace(dec, ".")
    elif v.count(",") > 1 or v.count(".") > 1:
        if not re.fullmatch(r"[+-]?\d{1,3}([.,]\d{3})+", v):
            raise ValueError(value)  # "1.2.3" is no number
        v = v.replace(",", "").replace(".", "")
    elif "," in v or "." in v:
        sep = "," if "," in v else "."
        head, tail = v.split(sep)
        digits = head.lstrip("+-")
        thousands = sep == ("," if writes_like_english(lang) else ".")
        if thousands and len(tail) == 3 and tail.isdigit() and 1 <= len(digits) <= 3 and not digits.startswith("0"):
            v = head + tail
        else:
            v = f"{head}.{tail}"
    if not PLAIN_NUMBER_RE.fullmatch(v):
        raise ValueError(value)
    number = float(v)
    if not math.isfinite(number) or abs(number) > MAX_NUMBER:
        raise ValueError(value)
    return number


def _fmt(n, lang="de"):
    """A number for a text shown to participants: whole numbers without ".0", a decimal comma except in the
    languages that write numbers like English (units.writes_like_english)."""
    from app.units import writes_like_english

    if n is not None and float(n).is_integer():
        return str(int(n))
    return str(n) if writes_like_english(lang) else str(n).replace(".", ",")


@register
class TextInput(ItemType):
    key = "text_input"
    read_keys = ("validation",)  # free text or a number: how the column is read (audit 2026-10-12, M-3)
    import_rules = {"multiline": r_flag, "max_length": r_int(1, 20000), "validation": r_one(*VALIDATIONS),
                    "pattern": r_text(MAX_PATTERN), "min": r_num(-MAX_NUMBER, MAX_NUMBER, none=True),
                    "max": r_num(-MAX_NUMBER, MAX_NUMBER, none=True), "ratio": r_flag, "purpose": r_one("", *PURPOSES)}

    def open_columns(self, item):
        if (item.config or {}).get("validation") in ("integer", "decimal"):
            return []
        return [c for c, _ in self.export_columns(item)]

    def import_problems(self, item):
        c = item.config
        if c["validation"] != "regex":
            return []
        if c["max_length"] > MAX_REGEX_INPUT:
            return [de_en("Mit einem regulären Ausdruck sind höchstens {n} Zeichen je Antwort möglich.",
                          "With a regular expression, at most {n} characters per answer are possible.", n=MAX_REGEX_INPUT)]
        problem = pattern_problem(c["pattern"])  # would be matched against whatever participants type
        if problem == "invalid":
            return [de_en("Der reguläre Ausdruck ist ungültig.", "The regular expression is invalid.")]
        if problem == "unsupported":
            return [unsupported_pattern_text()]
        if problem == "risky":
            return [de_en("Der reguläre Ausdruck enthält eine Wiederholung in einer Wiederholung; bitte in der Quelle vereinfachen.", "The regular expression contains a repetition within a repetition; please simplify it in the source.")]
        return []
    ratio_scale = True  # numbers typed in: amounts and counts by default; a year has no true zero (is_ratio)
    label = Both("Freitext", "Free text")
    short = Both("Offene Antwort", "Open answer")
    description = Both("Offene Antwort, optional mit Eingabeprüfung (Zahl, Bereich, E-Mail, Muster)",
                       "Open answer, optionally with an input check (number, range, email, pattern)")
    order = 40
    fact_sheet = FactSheet(
        "classic",
        keywords=["Offene Frage", "Open question", "Textfeld", "Text field", "Zahl", "Number", "E-Mail", "Email"],
        can=[("Einzeilig oder mehrzeilig, mit Höchstlänge.", "Single line or several lines, with a maximum length."),
             ("Eingabeprüfung: keine, ganze Zahl, Dezimalzahl, E-Mail-Adresse, deutsche Postleitzahl oder eigener regulärer Ausdruck.",
              "Input check: none, whole number, decimal number, email address, German postal code or a regular expression of your own.")],
        good_for=[("Offene Fragen, Begründungen und Kommentare", "Open questions, reasons and comments"),
                  ("Zahlen ohne vorgegebene Kategorien, z. B. Alter in Jahren oder Stunden pro Woche",
                   "Numbers without given categories, e.g. age in years or hours per week")],
        not_for=[("Antworten, die sich als Liste vorgeben lassen (dann Einfach- oder Mehrfachauswahl)",
                  "Answers that can be given as a list (then single or multiple choice)"),
                 ("Ein Datum oder eine Uhrzeit (dann Datum / Uhrzeit)", "A date or a time of day (then date / time)")],
        designs=["cross", "perception", "delphi"],
        examples=[("Delphi: Begründen Sie bitte Ihre Einschätzung.", "Delphi: please give reasons for your estimate."),
                  ("Wie viele Stunden nutzen Sie das Internet pro Woche?",
                   "How many hours a week do you use the internet?")],
    )

    def default_config(self):
        return {"multiline": False, "max_length": 2000, "validation": "none", "min": None, "max": None, "pattern": ""}

    def default_texts(self, lang):
        return {"question": "", "validation_message": "", "prefix": "", "suffix": ""}

    levels = ("nominal", "metric")

    def level_choices(self, item):
        return ()  # follows from the input check: numbers are metric, texts nominal

    def default_level(self, item):
        return "metric" if item.config.get("validation") in ("integer", "decimal") else "nominal"

    def is_ratio(self, item):
        """Typed numbers: amounts and counts (Gini on the results page) unless the editor's tick was taken off,
        e.g. for a year of birth."""
        return (item.config or {}).get("ratio", True) is not False

    def result_column(self, item, column):
        """Results page: the text after the box serves as the unit (e.g. "m", "kg", "Euro")."""
        return self.result_kind, (item.text("suffix") or "").strip(), None

    def purposes(self):
        """The purposes a field can name for the browser (editor, UI/UX audit 2026-10-16, 103)."""
        return PURPOSES

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        texts = dict(item.texts or {})
        texts[lang] = {**texts.get(lang, {}), "validation_message": form.get("validation_message", "").strip()[:300],
                       # short text before and after the input box, e.g. "ca." … "m" (per language)
                       "prefix": form.get("prefix", "").strip()[:100], "suffix": form.get("suffix", "").strip()[:100]}
        item.texts = texts
        if structural:
            ml = form.get("max_length", "").strip()
            validation = form.get("validation", "none")
            config = {
                "multiline": bool(form.get("multiline")),
                "max_length": min(whole(ml), 20000) if (whole(ml) or 0) > 0 else 2000,
                "validation": validation if validation in VALIDATIONS else "none",
                "pattern": form.get("pattern", "").strip()[:MAX_PATTERN],
                "min": None,
                "max": None,
            }
            for k in ("min", "max"):
                # in the language of the page, as the answers in the language of the survey: "10,000" is ten thousand
                # in English, "10.000" in German; "10,000" in German is read as 10 with a note (Mathe-Audit
                # 2026-10-06, Z-3)
                config[k] = units.read_entry(form.get(k, ""), known=(item.config or {}).get(k))
            if config["validation"] in ("integer", "decimal", "email", "plz_de"):
                config["multiline"] = False
            if config["validation"] == "regex":
                config["max_length"] = min(config["max_length"], MAX_REGEX_INPUT)
            # what the structure form does not hold stays: the summary choice, the link to a construct or linked
            # element (app/linked.py) and the analysis level
            item.config = {**config, **{k: item.config[k] for k in ("ratio", "construct", "level", "live_multi", "live_max", "purpose")
                                        if k in (item.config or {})}}
        if form.get("live_multi_shown"):  # a word cloud of a live vote: several words, at most so many (app/live/)
            n = whole(form.get("live_max", "").strip())
            item.config = {**item.config, "live_multi": bool(form.get("live_multi")),
                           "live_max": min(n, 100) if n and n > 0 else None}
        if form.get("purpose_shown"):  # only a hint for the browser (autocomplete): changeable at any time
            purpose = form.get("purpose", "")
            item.config = {**item.config, "purpose": purpose if purpose in PURPOSES else ""}
        if form.get("ratio_shown"):  # only how the results are summarised: changeable at any time
            item.config = {**item.config, "ratio": bool(form.get("ratio"))}

    def validate(self, item):
        c = item.config
        problems = []
        if c.get("validation") == "regex":
            if not c.get("pattern"):
                problems.append(de_en("regulärer Ausdruck fehlt", "regular expression is missing"))
            else:
                try:
                    re.compile(c["pattern"])
                except re.error as e:
                    problems.append(de_en("regulärer Ausdruck ist ungültig ({error})", "regular expression is invalid ({error})", error=e))
                else:
                    if pattern_problem(c["pattern"]) == "unsupported":
                        problems.append(unsupported_pattern_text())
                    elif pattern_problem(c["pattern"]) == "risky":
                        problems.append(de_en("regulärer Ausdruck enthält eine Wiederholung in einer Wiederholung "
                                              "(z. B. „(a+)+“); er wird nicht angewendet, bitte vereinfachen",
                                              "regular expression contains a repetition within a repetition "
                                              "(e.g. “(a+)+”); it is not applied, please simplify it"))
        if c.get("min") is not None and c.get("max") is not None and c["min"] > c["max"]:
            problems.append(de_en("„von“ ist größer als „bis“", "“from” is greater than “to”"))
        return problems

    def input_mode(self, item):
        return {"integer": "numeric", "decimal": "decimal", "email": "email", "plz_de": "numeric"}.get(
            item.config.get("validation"), "text")

    def range_hint(self, item, lang):
        c = item.config
        if c.get("validation") not in ("integer", "decimal"):
            return ""
        lo, hi = c.get("min"), c.get("max")
        m = pick(MESSAGES, lang)
        if lo is not None and hi is not None:
            return m["range"].format(lo=_fmt(lo, lang), hi=_fmt(hi, lang))
        if lo is not None:
            return m["min"].format(lo=_fmt(lo, lang))
        if hi is not None:
            return m["max"].format(hi=_fmt(hi, lang))
        return ""

    def shown(self, item, value, lang):
        """A stored answer as it goes back into the field: a number the way the participant would type it
        ("1,5" in German, "2" instead of "2.0"); anything else (text, or the raw input after an error) unchanged."""
        if value is None:
            return ""
        if isinstance(value, float):
            return _fmt(value, lang)
        return value

    def parse_answer(self, item, form, lang="de", meta=None):
        m = pick(MESSAGES, lang)
        c = item.config
        # browsers send line breaks as \r\n but count them as one character for maxlength
        value = form.get(f"i{item.id}", "").replace("\r\n", "\n").strip()
        if not value:
            if item.required:
                # a number with a range says the range only, not "an answer" right above it (W56)
                raise AnswerError(self.range_hint(item, lang) or m["required"])
            return None
        if len(value) > c.get("max_length", 2000):
            raise AnswerError(m["too_long"])
        kind = c.get("validation", "none")
        if kind in ("integer", "decimal"):
            try:
                number = _num(value, lang)
            except ValueError:
                raise AnswerError(m[kind]) from None
            if kind == "integer" and not number.is_integer():
                raise AnswerError(m["integer"])
            lo, hi = c.get("min"), c.get("max")
            if (lo is not None and number < lo) or (hi is not None and number > hi):
                raise AnswerError(self.range_hint(item, lang))
            return int(number) if kind == "integer" else number
        if kind == "email" and not is_email(value):  # the one rule of all e-mail fields (app/emails.py)
            raise AnswerError(m["email"])
        if kind == "plz_de" and not PLZ_RE.match(value):
            raise AnswerError(m["plz_de"])
        if kind == "regex" and c.get("pattern"):
            if len(value) > MAX_REGEX_INPUT:
                raise AnswerError(m["too_long"])
            # a pattern that is not applied (broken, unsupported, risky) is reported by the pre-launch check; it never
            # blocks participants
            if pattern_matches(c["pattern"], value) is False:
                raise AnswerError(item.text("validation_message", lang) or m["regex"])
        return value

    def pipeable(self, item):
        return True

    def pipe_text(self, item, answer, lang):
        from app.items.base import number_text, typed_text

        return number_text(answer, lang) if isinstance(answer, (int, float)) else typed_text(answer)

    def pipe_live(self, item, lang):
        return {"kind": "text", "item": item.id}


@register
class InfoText(ItemType):
    key = "info"
    import_rules = {}
    label = Both("Text", "Text")
    short = Both("Text ohne Antwort", "Text without an answer")
    description = Both("Text ohne Antwort, z. B. Instruktionen; mit Absätzen, Aufzählungen und Zwischenüberschriften",
                       "Text without an answer, e.g. instructions; with paragraphs, lists and subheadings")
    order = 50
    fact_sheet = FactSheet(
        "structure",
        keywords=["Text", "Instruktion", "Instruction", "Hinweis", "Note", "Überschrift", "Heading"],
        can=[("Text ohne Antwort, zum Beispiel Instruktionen, mit Absätzen, Aufzählungen und Zwischenüberschriften.",
              "Text without an answer, for example instructions, with paragraphs, lists and subheadings."),
             ("Mit Platzhaltern aus Schleifen und früheren Antworten.",
              "With placeholders from loops and earlier answers.")],
        good_for=[("Instruktionen, Übergänge und Szenarien", "Instructions, transitions and scenarios"),
                  ("Textstimuli in Experimenten und Vignetten", "Text stimuli in experiments and vignettes")],
        not_for=[("Die Einwilligung (dann Einwilligung)", "The consent (then consent)")],
        designs=["framing", "ab", "vignettes", "within"],
        examples=[("Framing: dieselbe Nachricht als Gewinn oder als Verlust",
                   "Framing: the same message as a gain or as a loss")],
    )
    collects_answer = False
