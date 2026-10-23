"""The sociodemographic standard variables of KonsortSWD in the data export (concept Demografie-Standards
2026-10-15, decision 5b): on request (a tick on the export page, like the scale scores) the data file and the
codebook get the target variables x_sex, x_doby, x_age, x_citsc, x_bornsc, x_mbornsc, x_fbornsc, x_hhsize,
x_marital and x_mainstatus, derived from the RatSWD standard questions (app/ratswd.py) and from the constructs
"Alter" and "Geschlecht". Codes and labels after Schneider et al. (2026), version 1.1, and its machine-readable
specification (Anhang 1); CC BY 4.0.

Missing values follow the KonsortSWD scheme for every variable: -1 no answer (refused, left empty), -2 don't know,
-3 not applicable (the question was not shown by its condition), -4 not included (the survey does not ask it),
-5 suppressed (not used here), -6 cannot be generated or coded. Note that the catalogue codes it the other way round
in the questions (-1 "Weiß nicht", -2 "Keine Angabe"): the derivation swaps them.

x_casmin (education after CASMIN) is left out: the specification maps the categories of the Demographische Standards
(maximal versions of school and vocational qualifications) to CASMIN; a derivation from the minimal versions BIL_1 and
BIL_4 is not documented there, and guessing one would not be the standard (it follows once KonsortSWD publishes it).
Occupation (ISCO-08, KldB 2010) needs coding of the open answer BER_1 and is not derived either."""
import re

CITATION = ("Schneider, S. L., Palm, L., Partsch, M. V., Ortmanns, V., & Müller, S. (2026). Soziodemographische "
            "Standardvariablen zur Output-Harmonisierung von Umfragedaten: Hintergrund und Validierungsergebnisse "
            "(Version 1.1). Zenodo. https://doi.org/10.5281/zenodo.18300602")

NO_ANSWER, DONT_KNOW, NOT_APPLICABLE, NOT_INCLUDED, SUPPRESSED, NOT_CODABLE = -1, -2, -3, -4, -5, -6
MISSING = [(NO_ANSWER, "Keine Angabe", "No answer"), (DONT_KNOW, "Weiß nicht", "Don't know"),
           (NOT_APPLICABLE, "Trifft nicht zu", "Not applicable"), (NOT_INCLUDED, "Nicht inkludiert", "Not included"),
           (SUPPRESSED, "Zurückgehalten", "Suppressed"), (NOT_CODABLE, "Nicht generierbar/codierbar",
                                                          "Cannot be generated/coded")]
MISSING_CODES = tuple(str(code) for code, _de, _en in MISSING)
# the catalogue's own missing codes of the RatSWD questions -> KonsortSWD
_RATSWD_MISSING = {"-1": DONT_KNOW, "-2": NO_ANSWER}

# (name, German label, English label, value labels [(code, German, English)] or None for a number, source keys)
VARIABLES = [
    ("x_sex", "Geschlecht", "Sex",
     [(1, "männlich", "male"), (2, "weiblich", "female"), (3, "divers", "“divers”"),
      (4, "kein Eintrag im Personenstandsregister", "no entry in register")], ("ratswd_ges1", "gender")),
    ("x_doby", "Geburtsjahr", "Year of birth", None, ("ratswd_alt1",)),
    ("x_age", "Alter in abgeschlossenen Jahren", "Age in completed years", None, ("ratswd_alt1", "age")),
    ("x_citsc", "Staatsbürgerschaft des Erhebungslandes", "Citizenship of survey country",
     [(10, "Ja - nfs", "Yes - nfs"), (11, "Ja, ausschließlich", "Yes, exclusively"),
      (12, "Ja, und andere Staatsbürgerschaft(en)", "Yes, and other citizenship(s)"), (20, "Nein - nfs", "No - nfs"),
      (21, "Nein, eine ausländische Staatsbürgerschaft", "No, one foreign citizenship"),
      (22, "Nein, mehrere ausländische Staatsbürgerschaften", "No, multiple foreign citizenships"),
      (23, "Nein, staatenlos", "No, stateless")], ("ratswd_sta1",)),
    ("x_bornsc", "In Erhebungsland geboren", "Born in survey country", [(0, "Nein", "No"), (1, "Ja", "Yes")],
     ("ratswd_geb1",)),
    ("x_mbornsc", "Mutter in Erhebungsland geboren", "Mother born in survey country",
     [(0, "Nein", "No"), (1, "Ja", "Yes")], ("ratswd_geb3",)),
    ("x_fbornsc", "Vater in Erhebungsland geboren", "Father born in survey country",
     [(0, "Nein", "No"), (1, "Ja", "Yes")], ("ratswd_geb5",)),
    ("x_hhsize", "Haushaltsgröße", "Household size", None, ("ratswd_hhg1",)),
    ("x_marital", "Familienstand", "Marital Status",
     [(100, "Ledig (nie verheiratet gewesen)", "single (never married)"),
      (200, "Verheiratet oder in eingetragener Lebenspartnerschaft, nfs", "married or civil partnership, nfs"),
      (300, "Geschieden oder eingetragene Lebenspartnerschaft aufgehoben, nfs",
       "divorced or civil partnership dissolved, nfs"),
      (400, "Verwitwet oder eingetragene*r Lebenspartner*in verstorben, nfs", "widowed or civil partner deceased, nfs")],
     ("ratswd_fam1",)),
    ("x_mainstatus", "Hauptstatus", "Main status",
     [(110, "Vollzeiterwerbstätig, nfs", "Full-time employed, nfs"), (120, "Teilzeiterwerbstätig, nfs", "Part-time employed, nfs"),
      (200, "Arbeitslos", "Unemployed"), (300, "Im Ruhestand, pensioniert", "Retired"),
      (400, "Studierende, Schüler*innen", "Student, pupil"), (600, "Sonstiges, nfs", "Other, nfs"),
      (610, "Dauerhaft erwerbsunfähig", "Unable to work due to long-standing health problems"),
      (620, "Haus-, Erziehungs- und Pflegearbeit leistend", "Fulfilling domestic tasks"),
      (630, "In freiwilligem Wehrdienst, Bundesfreiwilligendienst u.Ä.", "Voluntary military or civilian service")],
     ("ratswd_erw1",)),
]
NAMES = [name for name, *_ in VARIABLES]
NUMBERS = {"x_doby", "x_age", "x_hhsize"}  # metric; the others are categories

# answer code of the question -> code of the variable, per construct
_CODES = {
    "ratswd_ges1": {"1": 1, "2": 2, "3": 3},
    # Spiel et al.: 1 weiblich, 2 männlich, 3 divers; a self-description cannot be coded, "keine Angabe" is no answer
    "gender": {"1": 2, "2": 1, "3": 3, "4": NOT_CODABLE, "99": NO_ANSWER},
    "ratswd_sta1": {"1": 10, "2": 20},
    "ratswd_geb1": {"1": 1, "2": 0}, "ratswd_geb3": {"1": 1, "2": 0}, "ratswd_geb5": {"1": 1, "2": 0},
    "ratswd_fam1": {"1": 100, "2": 200, "3": 300, "4": 400},  # a civil partnership counts as a marriage (FAM_1)
    "ratswd_erw1": {"1": 110, "2": 120, "3": 400, "4": 300, "5": 610, "6": 620, "7": 200, "8": 630, "9": 600},
}
_MONTH = re.compile(r"(\d{4})-(\d{2})")


def sources(survey):
    """{construct key: the first question of the survey built from it} for the keys the variables read."""
    from app import constructs

    wanted = {k for *_, keys in VARIABLES for k in keys}
    out = {}
    for block in survey.blocks:
        for item in block.items:
            key = constructs.toolbox_key(item)
            if key in wanted and key not in out:
                out[key] = item
    return out


def available(survey):
    """Does the survey ask anything the variables are derived from (the export page offers the tick then)?"""
    return bool(sources(survey))


def columns(survey):
    """[(column, label)]: every variable, also those the survey does not ask (-4 "nicht inkludiert", as KonsortSWD
    wants it: the variable is there and says so)."""
    return [(name, f"KonsortSWD: {de}") for name, de, *_ in VARIABLES]


def _shown(item, answers, survey):
    """False if the question has a condition ("Nur zeigen, wenn …") that does not hold for these answers."""
    from app import item_condition

    cond = item_condition.of(item)
    if cond is None:
        return True
    from app import flow

    return flow.holds(cond, {int(k): v for k, v in answers.items() if str(k).isdigit()}, survey)


def _plain(answer):
    """The chosen answer code and the text typed next to it."""
    if isinstance(answer, dict) and "v" in answer:
        return str(answer["v"]), answer.get("t") or {}
    return (None if answer in (None, "") else str(answer)), {}


def _code(key, item, answers, survey):
    """The value of a categorical variable from the question `item` (construct `key`)."""
    if item is None:
        return NOT_INCLUDED
    chosen, _texts = _plain(answers.get(str(item.id)))
    if chosen is None:
        return NO_ANSWER if _shown(item, answers, survey) else NOT_APPLICABLE
    if chosen in _RATSWD_MISSING and key != "gender":
        return _RATSWD_MISSING[chosen]
    return _CODES[key].get(chosen, NOT_CODABLE)


def _hhsize(item, answers, survey):
    if item is None:
        return NOT_INCLUDED
    chosen, texts = _plain(answers.get(str(item.id)))
    if chosen is None:
        return NO_ANSWER if _shown(item, answers, survey) else NOT_APPLICABLE
    if chosen in _RATSWD_MISSING:
        return _RATSWD_MISSING[chosen]
    if chosen == "1":
        return 1
    typed = str(texts.get(chosen) or "").strip()  # "Mehrere Personen, und zwar: _ _"
    return int(typed) if typed.isdigit() and 2 <= int(typed) <= 99 else NOT_CODABLE


def _birth(item, answers, survey):
    """(year, month) of ALT_1, or a missing code."""
    if item is None:
        return NOT_INCLUDED
    answer = answers.get(str(item.id))
    if answer in (None, ""):
        return NO_ANSWER if _shown(item, answers, survey) else NOT_APPLICABLE
    m = _MONTH.fullmatch(str(answer))
    return (int(m.group(1)), int(m.group(2))) if m else NOT_CODABLE


def age_at(birth, when):
    """Age in completed years from (year, month) of birth at the date `when`: KonsortSWD subtracts one year for
    those whose month of birth is after the month of the interview (the day of birth is not asked)."""
    year, month = birth
    return when.year - year - (1 if month > when.month else 0)


def values(survey, found, response):
    """{variable: value} of one participation; found: sources(survey)."""
    answers = response.answers or {}
    out = {}
    for name, _de, _en, labels, keys in VARIABLES:
        key = next((k for k in keys if k in found), None)
        item = found.get(key) if key else None
        if name in ("x_doby", "x_age") and key == "ratswd_alt1":
            birth = _birth(item, answers, survey)
            when = response.completed_at or response.last_active_at or response.started_at
            if not isinstance(birth, tuple):
                out[name] = birth
            elif name == "x_doby":
                out[name] = birth[0]
            else:
                out[name] = age_at(birth, when) if when else NOT_CODABLE
        elif name == "x_age" and key == "age":  # the construct Alter: the age as typed (KonsortSWD: coded directly)
            typed = answers.get(str(item.id))
            if typed in (None, ""):
                out[name] = NO_ANSWER if _shown(item, answers, survey) else NOT_APPLICABLE
            else:
                out[name] = int(typed) if str(typed).strip().isdigit() else NOT_CODABLE
        elif name == "x_hhsize":
            out[name] = _hhsize(item, answers, survey)
        elif labels is None:
            out[name] = NOT_INCLUDED
        else:
            out[name] = _code(key, item, answers, survey) if key else NOT_INCLUDED
    return out


def value_labels(name, en=False):
    """The codebook list "1 = männlich; …; -1 = Keine Angabe; …" of a variable (the codes of a number: only the
    missing ones)."""
    labels = next(v[3] for v in VARIABLES if v[0] == name) or []
    pairs = [(code, e if en else d) for code, d, e in labels] + [(code, e if en else d) for code, d, e in MISSING]
    return "; ".join(f"{code} = {text}" for code, text in pairs)


def codebook(survey, ui=None):
    """Codebook rows (variable, label, type, values, notes) of the variables."""
    from app.items.base import in_lang, reader

    en = reader(ui) == "en"
    found = sources(survey)
    rows = []
    for name, de, en_label, labels, keys in VARIABLES:
        key = next((k for k in keys if k in found), None)
        if key is None:
            source = in_lang(ui, "nicht erhoben: alle −4", "not collected: all −4")
        else:
            source = in_lang(ui, "abgeleitet aus {code}", "derived from {code}", code=found[key].code)
            if name == "x_age" and key == "ratswd_alt1":
                source += in_lang(ui, " und dem Datum der Teilnahme (ein Jahr weniger, wenn der Geburtsmonat nach dem "
                                      "Monat der Teilnahme liegt)", " and the date of participation (one year less if the "
                                      "month of birth is after the month of participation)")
        # CC BY 4.0 asks for the source with its licence (copyright audit 2026-10-16, U26)
        notes = "; ".join([source, in_lang(ui, "KonsortSWD-Standardvariable, Schneider et al. (2026), Version 1.1, "
                                               "https://doi.org/10.5281/zenodo.18300602, CC BY 4.0",
                                           "KonsortSWD standard variable, Schneider et al. (2026), version 1.1, "
                                           "https://doi.org/10.5281/zenodo.18300602, CC BY 4.0")])
        rows.append((name, f"KonsortSWD: {en_label if en else de}", "KonsortSWD", value_labels(name, en), notes))
    return rows

