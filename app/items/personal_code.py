"""Personal code (self-generated identification code, SGIC): a code that participants put together from a few short
questions they answer the same way every time (first letter of the mother's first name, day of their birthday ...), so
that the answers of a pre and a post survey can be linked without a name, an e-mail address or a token (Kearney et al.,
1984; Yurek et al., 2008; Direnga et al., 2016). Askurious stays anonymous by design: no IP address, no fingerprint, and
invitation mails deliberately do not tie the token to the participation, so this code is the way to link waves
(app/waves.py: "Wellen zusammenführen" on the Export page).

The researchers tick 3 to 6 parts from a fixed list (PARTS); every part text is translated for every survey language
(app/ui_catalog.json) and is not edited per survey, so all waves ask the same. The default is the five-part code of
Direnga et al. (2016, Fig. 1) with one letter per name instead of two: mother's first name, birthday, father's first
name, older siblings, place of birth. The parts stand in the order of PARTS, which keeps letters and digits alternating
for the default (as Direnga et al. arranged them, for readability).

Each part has one field with its constraints (one letter, two digits for a day or a month, one digit for a count) and
a tick "weiß ich nicht"; a part not known is written as ? (?? for two digits) in the export: an X, as on paper codes,
would be the same as a mother called Xenia (audit 2026-10-10, N-11). The stored answer has DONT_KNOW, so codes of
earlier participations read the new way too; an X typed into a part of digits still counts as "weiß nicht". The
answer is stored as {part key: normalised value or DONT_KNOW}. Normalising a letter: upper case; a Latin letter with a
diacritic becomes its base letter (Ä -> A, É -> E, ß -> S, Ø -> O, Ł -> L), so "Ärzte" and "Arzte" give the same code
whoever types it how; a letter of another script (Cyrillic, Greek, a Chinese character) stays as typed, in upper case.
Digits are ASCII only; a day or month typed with one digit gets its leading zero (7 -> 07).

Export: <code> with the whole code (the parts in their order, e.g. "M07K1A"), and with the tick "Teile als eigene
Spalten" one column per part (<code>_<PARTS column>). All of it is text (leading zeros stay) and counts as an open
answer: a public data package (Zenodo, Coscine) has it only with the explicit tick, like postal codes and free text.
The code names nobody by itself, but together with another data set it could (decision in notes of 2026-10-09). It is
an answer, not an identifier from the link: the period "Kennungen und Kontaktdaten" does not empty it, since the
waves must still be linked months later; the period "Alle Teilnahmen" removes it with the answers."""
import unicodedata

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_list, r_one  # rules for survey files, see base.py
from app.items.base import DONT_KNOW, AnswerError, ItemType, in_lang
from app.lang import Both, de_en
from app.ui_i18n import tr

# (key, kind, end of the export column, German, English): the parts a code can have, in the order they stand in it
PARTS = (
    ("mother_first", "letter", "mo1", "Erster Buchstabe des Vornamens Ihrer Mutter", "First letter of your mother’s first name"),
    ("birth_day", "day", "bday", "Tag Ihres Geburtstags (01 bis 31)", "Day of the month of your birthday (01 to 31)"),
    ("father_first", "letter", "fa1", "Erster Buchstabe des Vornamens Ihres Vaters", "First letter of your father’s first name"),
    ("older_siblings", "digit", "sib", "Anzahl Ihrer älteren Geschwister (0 bis 9)", "Number of your older siblings (0 to 9)"),
    ("birthplace_first", "letter", "pl1", "Erster Buchstabe Ihres Geburtsorts", "First letter of the place where you were born"),
    ("mother_second", "letter", "mo2", "Zweiter Buchstabe des Vornamens Ihrer Mutter", "Second letter of your mother’s first name"),
    ("birth_month", "month", "bmon", "Monat Ihres Geburtstags (01 bis 12)", "Month of your birthday (01 to 12)"),
    ("father_second", "letter", "fa2", "Zweiter Buchstabe des Vornamens Ihres Vaters", "Second letter of your father’s first name"),
    ("older_brothers", "digit", "bro", "Anzahl Ihrer älteren Brüder (0 bis 9)", "Number of your older brothers (0 to 9)"),
    ("birthplace_second", "letter", "pl2", "Zweiter Buchstabe Ihres Geburtsorts", "Second letter of the place where you were born"),
    ("older_sisters", "digit", "sis", "Anzahl Ihrer älteren Schwestern (0 bis 9)", "Number of your older sisters (0 to 9)"),
    ("birth_year_last", "digit", "byr", "Letzte Ziffer Ihres Geburtsjahrs", "Last digit of the year you were born"),
)
KEYS = tuple(p[0] for p in PARTS)
KIND = {p[0]: p[1] for p in PARTS}
COLUMN = {p[0]: p[2] for p in PARTS}
WIDTH = {"letter": 1, "digit": 1, "day": 2, "month": 2}
DEFAULT_PARTS = ["mother_first", "birth_day", "father_first", "older_siblings", "birthplace_first"]
MIN_PARTS, MAX_PARTS = 3, 6
PLACEHOLDER = "?"  # a part not known: ?, or ?? for two digits; no letter or digit can be one (N-11)
PAPER = "X"  # "weiß nicht" as paper codes write it: typed into a part of digits it counts as the tick
# Latin letters that do not fall apart into a base letter and a mark (NFKD): their base letter by hand
LATIN = {"ß": "S", "ẞ": "S", "Ø": "O", "Æ": "A", "Œ": "O", "Ł": "L", "Đ": "D", "Þ": "T", "Ð": "D", "İ": "I", "ı": "I"}

# How often each answer of a part is, for the estimate of shared codes in the editor (collision): letters are far
# from equally common, birthdays nearly are. Rounded shares in per cent, normalised in share(); a Festlegung von
# Askurious from German first name, place name and family statistics, good enough to show the order of magnitude, not
# more. First letters of mothers' and fathers' first names differ (M and S lead for women, M, K, H, J for men); the
# second letter of a name or a place is mostly a vowel or r/l/h. Days: 29, 30 and 31 are in fewer months.
FREQ = {
    "mother_first": {"A": 9, "B": 6, "C": 6, "D": 4, "E": 5, "F": 1.5, "G": 4, "H": 4, "I": 3.5, "J": 4, "K": 8, "L": 3,
                     "M": 11, "N": 3.5, "O": 0.5, "P": 3, "Q": 0.05, "R": 4, "S": 12, "T": 2.5, "U": 2.5, "V": 1, "W": 1,
                     "X": 0.05, "Y": 0.5, "Z": 0.2},
    "father_first": {"A": 6, "B": 4, "C": 3, "D": 5, "E": 4, "F": 5, "G": 4, "H": 7, "I": 1, "J": 7, "K": 8, "L": 3,
                     "M": 10, "N": 2, "O": 2, "P": 6, "Q": 0.05, "R": 7, "S": 6, "T": 5, "U": 2, "V": 1, "W": 6,
                     "X": 0.05, "Y": 0.5, "Z": 0.2},
    "birthplace_first": {"A": 4, "B": 10, "C": 1, "D": 5, "E": 3, "F": 5, "G": 5, "H": 9, "I": 1, "J": 0.5, "K": 7,
                         "L": 5, "M": 7, "N": 4, "O": 3, "P": 3, "Q": 0.05, "R": 4, "S": 9, "T": 2, "U": 1, "V": 1, "W": 7,
                         "X": 0.05, "Y": 0.05, "Z": 1},
    "name_second": {"A": 25, "E": 18, "I": 14, "O": 10, "U": 6, "R": 10, "L": 5, "H": 4, "N": 2, "Y": 1, "W": 1, "T": 1,
                    "S": 1, "M": 0.5, "D": 0.5, "C": 0.5, "B": 0.3, "K": 0.3, "G": 0.2, "V": 0.2, "P": 0.2, "F": 0.2},
    "place_second": {"E": 20, "A": 18, "O": 12, "R": 10, "I": 8, "U": 7, "L": 6, "H": 5, "N": 3, "T": 2, "C": 2, "S": 2,
                     "Y": 1, "W": 1, "M": 1, "D": 0.5, "B": 0.5, "P": 0.5, "K": 0.5, "G": 0.5},
    "birth_day": {**{f"{d:02d}": 12 for d in range(1, 29)}, "29": 11.25, "30": 11, "31": 7},
    "birth_month": {f"{m:02d}": 1 for m in range(1, 13)},
    "older_siblings": {"0": 45, "1": 33, "2": 13, "3": 5, "4": 2, "5": 1, "6": 0.5, "7": 0.3, "8": 0.1, "9": 0.1},
    "older_brothers": {"0": 65, "1": 26, "2": 6, "3": 2, "4": 0.6, "5": 0.2, "6": 0.1, "7": 0.05, "8": 0.03, "9": 0.02},
    "older_sisters": {"0": 65, "1": 26, "2": 6, "3": 2, "4": 0.6, "5": 0.2, "6": 0.1, "7": 0.05, "8": 0.03, "9": 0.02},
    "birth_year_last": {str(d): 1 for d in range(10)},
}
FREQ_OF = {**{k: k for k in KEYS}, "mother_second": "name_second", "father_second": "name_second",
           "birthplace_second": "place_second"}
DEFAULT_N = 50  # sample size of the estimate when the survey sets no "max_responses"
POSSIBLE = {"letter": 26, "day": 31, "month": 12, "digit": 10}  # answers of a part, for the universe of codes
N_LOW, N_HIGH = 10, 50000  # the estimate's slider, logarithmic


def shares(key):
    """The shares of the answers of part `key` (summing to 1), most common first."""
    raw = FREQ[FREQ_OF[key]]
    total = sum(raw.values())
    return sorted((v / total for v in raw.values()), reverse=True)


def collision(parts, n):
    """Estimate for n participants who all answer every part: (chance that at least two share a code, expected share
    of participants whose code someone else has too). Parts count as independent. The codes are grouped by their
    probability (bins of 1/100 of a decade), so that 6 parts need no 300 million codes; within a bin the shares are
    nearly equal. Same formulas in app.js (sgicCollision)."""
    import math
    bins = {0: (1, 1.0)}  # bin -> (number of codes, their total probability)
    for key in parts:
        out = {}
        for c, m in bins.values():
            p = m / c
            for q in shares(key):
                b = round(math.log10(p * q) * 100)
                oc, om = out.get(b, (0, 0.0))
                out[b] = (oc + c, om + m * q)
        bins = out
    pairs = n * (n - 1) / 2
    q2 = sum(m * m / c for c, m in bins.values())  # chance that two people share their code
    any_shared = -math.expm1(-pairs * q2)
    shared = sum(m * -math.expm1((n - 1) * math.log1p(-m / c)) for c, m in bins.values()) if n > 1 else 0.0
    return any_shared, shared


KEARNEY = ("Kearney, K. A., Hopkins, R. H., Mauss, A. L., & Weisheit, R. A. (1984). Self-generated identification codes "
           "for anonymous collection of longitudinal questionnaire data. Public Opinion Quarterly, 48(1B), 370–378. "
           "https://doi.org/10.1093/poq/48.1b.370")
YUREK = ("Yurek, L. A., Vasey, J., & Sullivan Havens, D. (2008). The use of self-generated identification codes in "
         "longitudinal research. Evaluation Review, 32(5), 435–452. https://doi.org/10.1177/0193841X08316676")
SCHNELL = ("Schnell, R., Bachteler, T., & Reiher, J. (2010). Improving the use of self-generated identification codes. "
           "Evaluation Review, 34(5), 391–418. https://doi.org/10.1177/0193841X10387576")
DIRENGA = ("Direnga, J., Timmermann, D., Lund, J., & Kautz, C. (2016). Design and application of self-generated "
           "identification codes (SGICs) for matching longitudinal data. In Proceedings of the 44th SEFI Annual "
           "Conference (pp. 1–8). SEFI. https://www.sefi.be/wp-content/uploads/2017/09/"
           "direnga-design-and-application-of-self-generated-identification-codes-172_a.pdf")

MESSAGES = (
    ("required", "Bitte füllen Sie jeden Teil des Codes aus oder kreuzen Sie „weiß nicht“ an.",
     "Please fill in every part of the code or tick “don’t know”."),
    ("letter", "Bitte geben Sie bei „{part}“ genau einen Buchstaben ein.", "Please enter exactly one letter for “{part}”."),
    ("day", "Bitte geben Sie bei „{part}“ einen Tag von 01 bis 31 ein.", "Please enter a day from 01 to 31 for “{part}”."),
    ("month", "Bitte geben Sie bei „{part}“ einen Monat von 01 bis 12 ein.", "Please enter a month from 01 to 12 for “{part}”."),
    ("digit", "Bitte geben Sie bei „{part}“ eine Ziffer von 0 bis 9 ein.", "Please enter a digit from 0 to 9 for “{part}”."),
)


def message(lang, key, **values):
    return next(tr(lang, de, en, **values) for k, de, en in MESSAGES if k == key)


def part_label(key, lang):
    return next(tr(lang, de, en) for k, _kind, _col, de, en in PARTS if k == key)


def letter(text):
    """One letter as the code holds it: upper case, a Latin letter without its diacritic; None if `text` is not one
    letter."""
    text = unicodedata.normalize("NFC", "".join((text or "").split()))
    if len(text) != 1 or not text.isalpha():
        return None
    if text in LATIN or text.upper() in LATIN:
        return LATIN.get(text) or LATIN[text.upper()]
    base = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    out = (base if len(base) == 1 and base.isalpha() else text).upper()
    # one character in the code, also after upper(): a ligature ("ﬁ" -> "FI") would make it a
    # place longer and split() would no longer find its parts (audit 2026-10-12, M-8)
    return out if len(out) == 1 else None


def value(kind, text):
    """The typed value of a part normalised, or None if it does not fit the part."""
    if kind == "letter":
        return letter(text)
    text = "".join((text or "").split())
    if not text or len(text) > WIDTH[kind] or any(c not in "0123456789" for c in text):
        return None
    n = int(text)
    if kind == "day":
        return f"{n:02d}" if 1 <= n <= 31 else None
    if kind == "month":
        return f"{n:02d}" if 1 <= n <= 12 else None
    return str(n)


def code_of(parts, answer):
    """The whole code from a stored answer ({part: value or DONT_KNOW}), "" when there is none."""
    if not isinstance(answer, dict) or not answer:
        return ""
    out = []
    for key in parts:
        v = answer.get(key)
        out.append(PLACEHOLDER * WIDTH[KIND[key]] if v == DONT_KNOW or not v else str(v))
    return "".join(out)


def split(parts, code):
    """The code cut back into its parts: [values] in the order of `parts`; None if the length does not fit."""
    if not isinstance(code, str) or len(code) != sum(WIDTH[KIND[k]] for k in parts):
        return None
    out, at = [], 0
    for key in parts:
        w = WIDTH[KIND[key]]
        out.append(code[at:at + w])
        at += w
    return out


def scheme_text(parts, ui=None):
    """The scheme in words for the codebook: "1. M: first letter of your mother's first name (1 letter); ..."."""
    sizes = {"letter": ("1 Buchstabe", "1 letter"), "digit": ("1 Ziffer", "1 digit"), "day": ("2 Ziffern", "2 digits"),
             "month": ("2 Ziffern", "2 digits")}
    lang = "en" if in_lang(ui, "de", "en") == "en" else "de"
    return "; ".join(f"{n}. {part_label(k, lang)} ({in_lang(ui, *sizes[KIND[k]])})" for n, k in enumerate(parts, start=1))


@register
class PersonalCode(ItemType):
    key = "personal_code"
    label = Both("Persönlicher Code", "Personal code")
    short = Both("Code, um Wellen zu verknüpfen", "Code to link waves")
    description = Both("Selbst gebildeter Code aus kurzen Fragen (Anfangsbuchstaben, Geburtstag), um Wellen anonym zu verknüpfen",
                       "Self-generated code from short questions (initials, birthday) to link waves anonymously")
    order = 43
    fact_sheet = FactSheet(
        "classic",
        keywords=["SGIC", "Code", "Zuordnungscode", "Identification code", "Vorher-Nachher", "Pre-post", "Panel", "Wellen", "Waves"],
        can=[("Bildet einen Code aus 3 bis 6 kurzen Fragen, die man jedes Mal gleich beantwortet (Anfangsbuchstabe des "
              "Vornamens der Mutter, Tag des Geburtstags …).",
              "Builds a code from 3 to 6 short questions that one answers the same way every time (first letter of the "
              "mother’s first name, day of the birthday …)."),
             ("Ein Feld je Teil mit Prüfung (ein Buchstabe, zwei Ziffern) und „weiß nicht“ (? im Code).",
              "One field per part with a check (one letter, two digits) and “don’t know” (? in the code)."),
             ("Export: eine Spalte mit dem vereinheitlichten Code, auf Wunsch die Teile einzeln; Wellen werden auf der "
              "Export-Seite darüber zusammengeführt.",
              "Export: one column with the normalised code, the parts on their own if wanted; waves are merged by it on "
              "the Export page.")],
        good_for=[("Vorher-Nachher-Befragungen, Lehrevaluation und Interventionen über ein Semester, anonym",
                   "Pre-post surveys, course evaluation and interventions over a term, anonymously"),
                  ("Mehrere Wellen ohne Namen, E-Mail-Adresse oder Panel-Kennung verknüpfen",
                   "Linking several waves without a name, email address or panel ID")],
        not_for=[("Kleine Gruppen, in denen sich Personen am Code erkennen ließen (dann weniger Teile)",
                  "Small groups in which people could be recognised by the code (then fewer parts)"),
                 ("Panels mit Kennung im Link (dann die Kennung als URL-Parameter)",
                  "Panels with an ID in the link (then the ID as a URL parameter)")],
        designs=["within", "panel"],
        examples=[("Code aus Mutter, Geburtstag, Vater, älteren Geschwistern und Geburtsort, vor und nach dem Seminar",
                   "Code from mother, birthday, father, older siblings and place of birth, before and after the seminar")],
    )
    result_kind = None  # an identifier, not an answer to summarise: the matching report of the waves is its analysis
    scale_keys = ("parts",)  # fixed once real answers exist: another scheme would not link the waves any more
    part_table = PARTS  # the editor lists them all to tick
    scheme_sources = (DIRENGA, SCHNELL)  # cited next to the choice of parts
    import_rules = {"parts": r_list(MAX_PARTS, r_one(*KEYS), low=MIN_PARTS), "export_parts": r_flag}

    def default_config(self):
        return {"parts": list(DEFAULT_PARTS), "export_parts": False}

    def default_texts(self, lang):
        return {"question": tr(lang, "Bitte bilden Sie Ihren persönlichen Code. Er verbindet Ihre Antworten mit denen einer "
                                     "späteren Befragung, ohne dass wir Ihren Namen kennen.",
                               "Please create your personal code. It links your answers with those of a later survey "
                               "without us knowing your name.")}

    def parts(self, item):
        """The ticked parts in the order of PARTS (the order of the code)."""
        chosen = set((item.config or {}).get("parts") or [])
        return [k for k in KEYS if k in chosen]

    def scheme(self, item):
        """What two waves must share to be linked: the parts in their order."""
        return tuple(self.parts(item))

    def fields(self, item, lang):
        """[{"key", "kind", "label", "width", "column"}] of the parts, for the page."""
        return [{"key": k, "kind": KIND[k], "label": part_label(k, lang), "width": WIDTH[KIND[k]], "column": COLUMN[k]}
                for k in self.parts(item)]

    # --- editor -----------------------------------------------------------------------------------------

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        if not structural or "parts_shown" not in form:
            return
        chosen = set(form.getlist("part"))
        item.config = {**(item.config or {}), "parts": [k for k in KEYS if k in chosen],
                       "export_parts": bool(form.get("export_parts"))}

    def validate(self, item):
        n = len(self.parts(item))
        if n < MIN_PARTS:
            return [de_en("mindestens {n} Teile ankreuzen: Mit weniger tragen zu viele Personen denselben Code",
                          "tick at least {n} parts: with fewer, too many people share the same code", n=MIN_PARTS)]
        if n > MAX_PARTS:
            return [de_en("höchstens {n} Teile: Jeder weitere macht den Code fehleranfälliger und die Seite länger",
                          "at most {n} parts: each further one makes the code more error-prone and the page longer", n=MAX_PARTS)]
        return []

    def check_warnings(self, item):
        others = [i for b in item.survey.blocks for i in b.items if i.type == self.key and i.id != item.id]
        if others:
            return [de_en("mehr als ein persönlicher Code in der Befragung: Zum Zusammenführen der Wellen zählt der erste",
                          "more than one personal code in the survey: merging the waves uses the first one")]
        return []

    def source_groups(self, item):
        return [(de_en("Persönlicher Code: Teile, Länge und Abgleich", "Personal code: parts, length and matching"),
                 [KEARNEY, YUREK, SCHNELL, DIRENGA])]

    def references(self, item):
        return [DIRENGA]

    def shares_json(self):
        """{part: [shares]} for the estimate of shared codes in the editor (app.js, sgicCollision)."""
        import json
        return json.dumps({k: [round(x, 6) for x in shares(k)] for k in KEYS}, separators=(",", ":"))

    # the slider runs over log10(n) * 100: from 100 (10) to 470 (50,000)
    slider_min, slider_max = 100, 470

    def estimate_start(self, item):
        """(n, slider position) the estimate starts with: the survey's maximum of responses, else DEFAULT_N."""
        import math
        n = min(max(int(getattr(item.survey, "max_responses", None) or DEFAULT_N), N_LOW), N_HIGH)
        return n, round(math.log10(n) * 100)

    def sizes_json(self):
        """{part: how many answers it has} for the universe of codes in the editor (A to Z, 01 to 31 ...)."""
        import json
        return json.dumps({k: POSSIBLE[KIND[k]] for k in KEYS}, separators=(",", ":"))

    # --- participant side ---------------------------------------------------------------------------------

    def _sent(self, item, form):
        """{part: (typed text, "weiß ich nicht" ticked)} as the form sent them."""
        return {k: (form.get(f"i{item.id}_{k}", "") or "", bool(form.get(f"i{item.id}_{k}_dk"))) for k in self.parts(item)}

    def parse_answer(self, item, form, lang="de", meta=None):
        sent = self._sent(item, form)
        if not any(text.strip() or dk for text, dk in sent.values()):
            if item.required:
                raise AnswerError(message(lang, "required"))
            return None
        out = {}
        for key, (text, dk) in sent.items():
            if dk:
                out[key] = DONT_KNOW
                continue
            typed = text.strip().upper()
            if typed in (PLACEHOLDER, PLACEHOLDER * 2) or (KIND[key] != "letter" and typed in (PAPER, PAPER * 2)):
                out[key] = DONT_KNOW  # ? or (where no letter goes) X typed as on paper codes: the same as the tick
                continue
            if not text.strip():
                # a code with a gap links nothing: fill it in or say "weiß ich nicht", also where the question is optional
                raise AnswerError(message(lang, "required"))
            v = value(KIND[key], text)
            if v is None:
                raise AnswerError(message(lang, KIND[key], part=part_label(key, lang)))
            out[key] = v
        return out

    def display_value(self, item, form, meta=None):
        """What was typed, shown again after a failed "Next": {part: text}, with DONT_KNOW for a tick."""
        return {k: DONT_KNOW if dk else text for k, (text, dk) in self._sent(item, form).items()}

    def is_missing(self, item, answer):
        return not isinstance(answer, dict) or any(not answer.get(k) for k in self.parts(item))

    # --- export -------------------------------------------------------------------------------------------

    def export_columns(self, item):
        cols = [(item.code, de_en("Persönlicher Code", "Personal code"))]
        if (item.config or {}).get("export_parts"):
            lang = item.survey.default_language if item.block is not None else "de"
            cols += [(f"{item.code}_{COLUMN[k]}", f"{de_en('Persönlicher Code', 'Personal code')}: {part_label(k, lang)}")
                     for k in self.parts(item)]
        return cols

    def open_columns(self, item):
        return [c for c, _ in self.export_columns(item)]  # with another data set the code could point to a person

    def export_values(self, item, answer):
        parts = self.parts(item)
        out = {item.code: code_of(parts, answer) or None}
        if (item.config or {}).get("export_parts"):
            for key in parts:
                v = answer.get(key) if isinstance(answer, dict) else None
                out[f"{item.code}_{COLUMN[key]}"] = (PLACEHOLDER * WIDTH[KIND[key]] if v == DONT_KNOW else v) or None
        return out

    def codebook_info(self, item, column, lang, ui=None):
        parts = self.parts(item)
        if column == item.code:
            return "Text", in_lang(ui, "{n} Zeichen", "{n} characters", n=sum(WIDTH[KIND[k]] for k in parts)), [
                in_lang(ui, "Teile in dieser Reihenfolge: {scheme}", "parts in this order: {scheme}", scheme=scheme_text(parts, ui)),
                in_lang(ui, "? (bei zwei Ziffern ??) = weiß nicht; X ist ein Buchstabe", "? (?? for two digits) = don't know; X is a letter"),
                in_lang(ui, "vereinheitlicht: Großbuchstaben, Buchstaben mit Akzent oder Umlaut als Grundbuchstabe (Ä = A, "
                            "É = E, ß = S), Buchstaben anderer Schriften wie eingegeben; Tag und Monat zweistellig",
                        "normalised: upper case, letters with an accent or umlaut as their base letter (Ä = A, É = E, ß = S), "
                        "letters of other scripts as typed; day and month with two digits"),
                in_lang(ui, "leer = nicht beantwortet", "empty = not answered")]
        for key in parts:
            if column == f"{item.code}_{COLUMN[key]}":
                size = {"letter": in_lang(ui, "1 Buchstabe", "1 letter"), "digit": "0–9", "day": "01–31", "month": "01–12"}
                return "Text", size[KIND[key]], [in_lang(ui, "Teil von {code}; ? = weiß nicht", "part of {code}; ? = don't know",
                                                         code=item.code)]
        return None

    def questionnaire_lines(self, item, lang):
        return [f"  - {part_label(k, lang)} (" + {"letter": "1 letter", "digit": "1 digit", "day": "01–31",
                                                    "month": "01–12"}[KIND[k]] + ", or don't know = ?)" for k in self.parts(item)]

    # --- dummy data, duration --------------------------------------------------------------------------------

    def dummy_fields(self, item, rng, meta, answers=None):
        draw = {"letter": lambda: rng.choice("ABCDEFGHIJKLMNOPRSTUVW"), "digit": lambda: str(rng.choice((0, 0, 1, 1, 2, 3))),
                "day": lambda: f"{rng.randint(1, 31):02d}", "month": lambda: f"{rng.randint(1, 12):02d}"}
        out = {}
        for key in self.parts(item):
            if rng.random() < 0.03:
                out[f"i{item.id}_{key}_dk"] = "1"
            else:
                out[f"i{item.id}_{key}"] = draw[KIND[key]]()
        return out

    def seconds(self, item, read):
        return read + 5 * len(self.parts(item))  # recalling and typing one letter or number each
