"""Quota assistant "Bevölkerung Deutschland": quotas by age, gender and federal state with the shares of the
official population figures.

The figures are the population update of the Statistisches Bundesamt on the basis of the census of 2022
(table 12411-0013: states × sex × years of age), kept in app/data/population_de.json.gz (tools/
build_population_de.py). They know two sexes: the entries "divers" and "ohne Angabe" of the registers are
spread over male and female by the office itself. So there are two cells for gender; who answers "divers" or
"keine Angabe" takes part and is counted in no cell of gender.

`plan` turns the choices of the assistant (sample size, crossing, age span, age groups) into quotas with their
cells and targets; it needs no survey. Crossed cells take their share from the joint distribution of the
table, not from the product of the margins (quotas.generate_cells), and the targets of every quota add up to
the sample size (largest remainders first).

`steps` says what the assistant would do to a survey, `apply` does it: the three questions (constructs age and
gender, element Bundesland) on the first page behind the consent, screen-outs for who is not part of the
population (not living in Germany, outside the age span), the quotas, and behind that page one quota calculation
with an end "Quote voll".
What the assistant made is marked (the codes of its quotas, "by" on its flow nodes), so a second run replaces
it and leaves everything else alone.
"""
import functools
import gzip
import json
import os
import re

from app.lang import de_en, english
from app.units import fixed

DATA = os.path.join(os.path.dirname(__file__), "data", "population_de.json.gz")
MARK = "census"  # "by" on the flow nodes the assistant made
SOURCE_URL = "https://genesis.destatis.de/datenbank/online/statistic/12411/table/12411-0013"
LICENCE_URL = "https://www.govdata.de/dl-de/by-2-0"

# how the three attributes are crossed
CROSS = {"none": "nicht gekreuzt: je eine Quote für Geschlecht, Alter und Bundesland",
         "age_gender": "Alter × Geschlecht gekreuzt, Bundesland für sich",
         "age_state": "Bundesland × Alter gekreuzt, Geschlecht für sich",
         "gender_state": "Bundesland × Geschlecht gekreuzt, Alter für sich",
         "full": "voll gekreuzt: Bundesland × Alter × Geschlecht"}
# the page asks which attributes to cross (ticks): the attributes each crossing takes together
ATTRIBUTES = {"age": "Alter", "gender": "Geschlecht", "state": "Bundesland"}
CROSSED = {"none": (), "age_gender": ("age", "gender"), "age_state": ("age", "state"), "gender_state": ("gender", "state"),
           "full": ("age", "gender", "state")}
AGE_FROM = (18, 16)
AGE_TO = (None, 69, 74, 79)
# age groups: (name, first year of each group from 18 on, where the grouping comes from). The decades are the
# quota groups of the media analysis of the agma (ma Out of Home: 14–19, 20–29, 30–39, …, 60–69, 70 and older,
# crossed with gender), with the youngest group starting at 18 as in the GLES Tracking; "gles" is the grouping
# of that study as it stands (GLES Tracking T52, ZA7710, study description, table 4).
SCHEMES = {
    "decades": ("Zehnjahresgruppen", (18, 30, 40, 50, 60, 70),
                "Zehnjahresgruppen wie die Quotengruppen der Media-Analyse der agma, jüngste Gruppe ab 18 Jahren"),
    "gles": ("GLES Tracking", (18, 30, 40, 50, 60), "Quotengruppen des GLES Tracking (GESIS)"),
    "census5": ("Zensus 2022, 5 Altersklassen", (18, 30, 50, 65), "Altersklassen des Zensus 2022 (ab 18 Jahren)"),
    "census11": ("Zensus 2022, 11 Altersklassen", (18, 25, 30, 40, 50, 65, 75), "Altersklassen des Zensus 2022 (ab 18 Jahren)"),
    "allbus": ("ALLBUS", (18, 30, 45, 60, 75), "Altersgruppen der Allgemeinen Bevölkerungsumfrage der Sozialwissenschaften (ALLBUS)"),
}
SEXES = (("m", "männlich"), ("f", "weiblich"))
SEX_CODES = {"m": "m", "f": "w"}       # in the codes of the cells
GENDER_ANSWERS = {"m": "2", "f": "1"}  # answer codes of the construct gender (constructs.GENDER_OPTIONS)
# the constructs that ask the gender, with their codes of male and female: Spiel et al. and GES_1 of the RatSWD, the
# gender question of new surveys (app/ratswd.py, wizard.DEFAULT_GENDER)
GENDER_CONSTRUCTS = {"gender": GENDER_ANSWERS, "ratswd_ges1": {"m": "1", "f": "2"}}
# codes of the cells per state: the abbreviations of ISO 3166-2:DE
STATE_SLUGS = {"8": "bw", "9": "by", "11": "be", "12": "bb", "4": "hb", "2": "hh", "6": "he", "13": "mv", "3": "ni",
               "5": "nw", "7": "rp", "10": "sl", "14": "sn", "15": "st", "1": "sh", "16": "th"}
# how the federal states are put together: (name, where the grouping comes from, [(code of the cell, name,
# state codes)]); None for the 16 states one by one. The question always asks for the state; only the cells
# of the quotas take several states together.
REGIONS = {
    "states": ("einzeln (16 Bundesländer)", "", None),
    "nielsen": ("Nielsen-Gebiete (8 Regionen)", "Nielsen-Gebiete, wie sie die Media-Analyse der agma verwendet", [
        ("n1", "Nielsen I (Schleswig-Holstein, Hamburg, Niedersachsen, Bremen)", ("1", "2", "3", "4")),
        ("n2", "Nielsen II (Nordrhein-Westfalen)", ("5",)),
        ("n3a", "Nielsen IIIa (Hessen, Rheinland-Pfalz, Saarland)", ("6", "7", "10")),
        ("n3b", "Nielsen IIIb (Baden-Württemberg)", ("8",)),
        ("n4", "Nielsen IV (Bayern)", ("9",)),
        ("n5", "Nielsen V (Berlin)", ("11",)),
        ("n6", "Nielsen VI (Mecklenburg-Vorpommern, Brandenburg, Sachsen-Anhalt)", ("13", "12", "15")),
        ("n7", "Nielsen VII (Thüringen, Sachsen)", ("16", "14"))]),
    "ba": ("Regionaldirektionen der Bundesagentur für Arbeit (10 Regionen)", "Regionaldirektionen der Bundesagentur für Arbeit", [
        ("nord", "Nord (Schleswig-Holstein, Hamburg, Mecklenburg-Vorpommern)", ("1", "2", "13")),
        ("ni_hb", "Niedersachsen-Bremen", ("3", "4")),
        ("nw", "Nordrhein-Westfalen", ("5",)),
        ("he", "Hessen", ("6",)),
        ("rp_sl", "Rheinland-Pfalz-Saarland", ("7", "10")),
        ("bw", "Baden-Württemberg", ("8",)),
        ("by", "Bayern", ("9",)),
        ("be_bb", "Berlin-Brandenburg", ("11", "12")),
        ("st_th", "Sachsen-Anhalt-Thüringen", ("15", "16")),
        ("sn", "Sachsen", ("14",))]),
    "east_west": ("West und Ost (2 Regionen)", "West- und Ostdeutschland nach dem Statistischen Bundesamt (Ost mit ganz Berlin)", [
        ("west", "Westdeutschland", ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10")),
        ("ost", "Ostdeutschland mit Berlin", ("11", "12", "13", "14", "15", "16"))]),
}
# where each grouping is documented, shown next to the choices: {key of SCHEMES or REGIONS: [(what it says
# there, the source, its address)]}
_AGMA = ("Arbeitsgemeinschaft Media-Analyse (agma): ma Out of Home, Datenerhebung",
         "https://www.agma-mmc.de/media-analyse/ma-out-of-home/datenerhebung")
_GLES = ("GLES (2022): GLES Tracking Juni 2022, T52. GESIS, Köln. ZA7710, Version 2.0.0, Studienbeschreibung, Tabelle 4",
         "https://doi.org/10.4232/1.14173")
_ZENSUS = ("Statistische Ämter des Bundes und der Länder: Zensus 2022, Zensusdatenbank, Tabelle 1000A-2026",
           "https://ergebnisse.zensus2022.de/datenbank/online/statistic/1000A/table/1000A-2026")
SOURCES = {
    "decades": [("Quotengruppen 14–19, 20–29, 30–39, 40–49, 50–59, 60–69, 70 Jahre und älter (Alter × Geschlecht gekreuzt)", *_AGMA),
                ("jüngste Gruppe 18–29 wie in der Quotierung des GLES Tracking", *_GLES)],
    "gles": [("Quotierung nach 18–29, 30–39, 40–49, 50–59 Jahren, 60 Jahre und älter", *_GLES)],
    "census5": [("„Alter (5 Altersklassen)“: unter 18, 18–29, 30–49, 50–64, 65 Jahre und älter", *_ZENSUS)],
    "census11": [("„Alter (11 Altersklassen)“, ab 18: 18–24, 25–29, 30–39, 40–49, 50–64, 65–74, 75 Jahre und älter", *_ZENSUS)],
    "allbus": [("kategorisierte Altersvariable agec: 18–29, 30–44, 45–59, 60–74, 75–89, über 89 Jahre",
                "GESIS: Allgemeine Bevölkerungsumfrage der Sozialwissenschaften (ALLBUS), Variable agec", "https://www.gesis.org/allbus")],
    "states": [("16 Länder nach dem Länderschlüssel des Amtlichen Gemeindeschlüssels",
                "Statistisches Bundesamt (Destatis): Genesis-Online, Tabelle 12411-0013", SOURCE_URL)],
    "nielsen": [("Einteilung in acht Nielsen-Gebiete mit ihren Bundesländern",
                 "Arbeitsgemeinschaft Media-Analyse (agma): Glossar, Nielsen-Gebiete",
                 "https://www.agma-mmc.de/service/glossar/eintrag/nielsen-gebiete")],
    "ba": [("zehn Regionaldirektionen mit ihren Bundesländern", "Bundesagentur für Arbeit: Regionaldirektionen",
            "https://www.arbeitsagentur.de/ueber-uns/regionaldirektionen")],
    "east_west": [("Westdeutschland ohne Berlin; Ostdeutschland: neue Länder und Berlin (Ost und West)",
                   "Statistisches Bundesamt (Destatis): Glossar, Gebietsstände",
                   "https://www.destatis.de/DE/Themen/Gesellschaft-Umwelt/Einkommen-Konsum-Lebensbedingungen/Glossar/gebietsstaende-evs-lwr.html")],
}
QUOTAS = {"de_geschlecht": "Bevölkerung DE: Geschlecht", "de_alter": "Bevölkerung DE: Alter",
          "de_land": "Bevölkerung DE: Bundesland", "de_alter_geschlecht": "Bevölkerung DE: Alter × Geschlecht",
          "de_land_alter": "Bevölkerung DE: Bundesland × Alter", "de_land_geschlecht": "Bevölkerung DE: Bundesland × Geschlecht",
          "de_land_alter_geschlecht": "Bevölkerung DE: Bundesland × Alter × Geschlecht"}
MAX_N = 1_000_000
# the labels of the page in English (the German ones above stay the standard, also in the method texts)
CROSS_EN = {"none": "not crossed: one quota each for gender, age and federal state",
            "age_gender": "age × gender crossed, federal state on its own",
            "age_state": "federal state × age crossed, gender on its own",
            "gender_state": "federal state × gender crossed, age on its own",
            "full": "fully crossed: federal state × age × gender"}
ATTRIBUTES_EN = {"age": "Age", "gender": "Gender", "state": "Federal state"}
SCHEMES_EN = {"decades": ("Ten-year groups", "ten-year groups like the quota groups of the agma media analysis, youngest group from 18"),
              "gles": ("GLES Tracking", "quota groups of the GLES Tracking (GESIS)"),
              "census5": ("Census 2022, 5 age classes", "age classes of the 2022 census (from 18)"),
              "census11": ("Census 2022, 11 age classes", "age classes of the 2022 census (from 18)"),
              "allbus": ("ALLBUS", "age groups of the German General Social Survey (ALLBUS)")}
REGIONS_EN = {"states": ("one by one (16 federal states)", ""),
              "nielsen": ("Nielsen areas (8 regions)", "Nielsen areas as used by the agma media analysis"),
              "ba": ("Regional directorates of the Federal Employment Agency (10 regions)",
                     "regional directorates of the Federal Employment Agency"),
              "east_west": ("West and East (2 regions)",
                            "West and East Germany as defined by the Federal Statistical Office (East with all of Berlin)")}


def attributes():
    """ATTRIBUTES in the language of the page."""
    return ATTRIBUTES_EN if english() else ATTRIBUTES


def cross_label(key):
    return (CROSS_EN if english() else CROSS)[key]


def scheme_name(key):
    return SCHEMES_EN[key][0] if english() else SCHEMES[key][0]


def scheme_origin(key):
    return SCHEMES_EN[key][1] if english() else SCHEMES[key][2]


def region_name(key):
    return REGIONS_EN[key][0] if english() else REGIONS[key][0]


def region_origin(key):
    return REGIONS_EN[key][1] if english() else REGIONS[key][1]
SMALL = 5  # a target below this is named in the preview


@functools.cache
def population():
    """The figures: {"table", "date", "retrieved", "top_age", "states": {state code: {"m": [persons per year
    of age], "f": [...]}}}; the last entry of a list is "top_age years and more"."""
    with gzip.open(DATA, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def source_groups():
    """[(heading, [citations])] for the one fold of sources of the assistant (_sources.html, grouped): where each
    grouping of ages and federal states is documented, and the table of the figures themselves."""
    def cited(key):
        return [f"{said}: {where}. {url}" for said, where, url in SOURCES[key]]

    return ([(de_en("Altersgruppen: {name}", "Age groups: {name}", name=scheme_name(key)), cited(key)) for key in SCHEMES]
            + [(de_en("Bundesländer: {name}", "Federal states: {name}", name=region_name(key)), cited(key)) for key in REGIONS]
            + [(de_en("Anteile nach Alter, Geschlecht und Bundesland (in jeder Einteilung aus der amtlichen Tabelle nach "
                      "Einzeljahren und Ländern; belegt ist oben die Einteilung)",
                      "Shares by age, gender and federal state (in every grouping from the official table by single "
                      "years and states; the grouping is documented above)"), [f"{source()}. {SOURCE_URL}"])])


def source(lang="de"):
    """Where the figures come from, as the licence asks for it."""
    data = population()
    day, got = (".".join(reversed(data[key].split("-"))) for key in ("date", "retrieved"))
    if lang in ("de", "de-du"):
        return (f"Statistisches Bundesamt (Destatis), Genesis-Online, Tabelle {data['table']} (Bevölkerungsfortschreibung "
                f"auf Grundlage des Zensus 2022, Stichtag {day}), Abruf {got}; Datenlizenz by-2-0; eigene Berechnung")
    return (f"Statistisches Bundesamt (Destatis), Genesis-Online, table {data['table']} (population update based on the "
            f"2022 census, reference date {data['date']}), retrieved {data['retrieved']}; Datenlizenz by-2-0; own calculation")


def reference_date():
    return ".".join(reversed(population()["date"].split("-")))


# --- the choices ----------------------------------------------------------------------------------------


def options(form):
    """The choices of the assistant from a form or query string, each one of the offered values:
    {"n", "cross", "lo", "hi", "scheme", "regions", "state", "age", "gender", "gf", "gm", "move", "others"}. n is 0 while no sample size is given. state names
    the question the federal state comes from (see `questions`): "" for the one the assistant finds itself, the
    id of a question, or "new" for a new element Bundesland. The crossing comes
    as its key ("cross") or as the ticked attributes ("x", several): two of them are crossed and the third
    stands alone, all three are fully crossed, one or none means nothing is crossed."""
    raw = (form.get("n") or "").strip()
    lo, hi = form.get("lo", type=int), form.get("hi", type=int)
    state = (form.get("state") or "").strip()
    ticked = {x for x in form.getlist("x") if x in ATTRIBUTES} if hasattr(form, "getlist") else set()
    cross = next((key for key, attrs in CROSSED.items() if len(ticked) > 1 and set(attrs) == ticked), None)
    # ASCII digits only: "²".isdigit() is true, and int("²") raises
    return {"n": min(int(raw), MAX_N) if raw.isascii() and raw.isdigit() and len(raw) < 12 else 0,
            "cross": cross or (form.get("cross") if form.get("cross") in CROSS else "none"),
            "lo": lo if lo in AGE_FROM else AGE_FROM[0],
            "hi": hi if hi in AGE_TO else None,
            "scheme": form.get("scheme") if form.get("scheme") in SCHEMES else "decades",
            "regions": form.get("regions") if form.get("regions") in REGIONS else "states",
            "state": state if state == "new" or state.isdigit() else "",
            "age": _pick(form.get("age")), "gender": _pick(form.get("gender")),
            "gf": (form.get("gf") or "").strip()[:40], "gm": (form.get("gm") or "").strip()[:40],
            # UI/UX audit 2026-10-07, W18: the questions there stay where they are unless the tick moves them into a
            # block of their own behind the consent; quotas of other origin on the same attributes are replaced
            # unless "zusätzlich anlegen" is chosen
            "move": form.get("move") == "1",
            "others": "add" if form.get("others") == "add" else "replace"}


def _pick(raw):
    """A question chosen on the page: its id, "new" for a new construct, "" for the one the assistant finds itself."""
    raw = (raw or "").strip()
    return raw if raw == "new" or (raw.isascii() and raw.isdigit() and len(raw) < 12) else ""


def age_groups(scheme, lo=18, hi=None):
    """[(from, to)] of the age groups between lo and hi; to is None for "and older"."""
    starts = [lo] + [s for s in SCHEMES[scheme][1][1:] if s > lo and (hi is None or s <= hi)]
    return [(a, starts[k + 1] - 1 if k + 1 < len(starts) else hi) for k, a in enumerate(starts)]


def age_label(group):
    a, b = group
    return f"ab {a}" if b is None else f"{a} bis {b}"


def _age_code(group):
    a, b = group
    return f"a{a}plus" if b is None else f"a{a}_{b}"


def scheme_label(scheme):
    """A grouping as the assistant offers it: "Zehnjahresgruppen: 18–29, 30–39, …, 70 und älter"."""
    said = [de_en("{a} und älter", "{a} and older", a=a) if b is None else f"{a}–{b}" for a, b in age_groups(scheme)]
    return f"{scheme_name(scheme)}: " + ", ".join(said)


def span_label(lo, hi):
    """The age span in German (also for the method texts, app/methods.py)."""
    return f"ab {lo} Jahren" if hi is None else f"von {lo} bis {hi} Jahren"


def span_shown(lo, hi):
    """The age span as the page shows it, in the language of the page."""
    if not english():
        return span_label(lo, hi)
    return f"aged {lo} and over" if hi is None else f"aged {lo} to {hi}"


# --- the plan -------------------------------------------------------------------------------------------


def _rounded(counts, n):
    """Targets for the counts that add up to n: the exact shares rounded down, the rest to the largest
    remainders."""
    whole = sum(counts)
    exact = [n * c / whole for c in counts]
    targets = [int(x) for x in exact]
    for k in sorted(range(len(exact)), key=lambda k: exact[k] - targets[k], reverse=True)[:n - sum(targets)]:
        targets[k] += 1
    return targets


def _persons(state, sex, group):
    data = population()
    a, b = group
    rows = data["states"][state][sex]
    return sum(rows[a:(data["top_age"] if b is None else b) + 1])


def places(regions="states"):
    """The cells of the federal states: [(code, name, state codes)], one per state or one per region."""
    from app.items.federal_state import STATES

    return REGIONS[regions][2] or [(STATE_SLUGS[code], de, (code,)) for code, de, _ in STATES]


def plan(opts):
    """The quotas the choices give: {"quotas": [{"code", "name", "no_cell", "attrs", "cells": [{"code", "name",
    "place", "states", "age", "sex", "persons", "share", "target"}]}], "persons", "groups", "errors", "hints"}.
    A cell names what it stands for: states (the codes of its state, or of the states of its region; place is
    their name), age ((from, to)), sex ("m" | "f"), each None where the
    quota does not ask for it. share is in percent of the population in the age span."""
    from app.items.federal_state import STATES

    groups = age_groups(opts["scheme"], opts["lo"], opts["hi"])
    states = [code for code, _, _ in STATES]
    regions = places(opts.get("regions", "states"))
    sexes = [key for key, _ in SEXES]
    sex_names = dict(SEXES)

    def cell(place=None, age=None, sex=None):
        slug, where, codes = place or (None, None, None)
        persons = sum(_persons(s, x, g) for s in (codes or states) for x in ([sex] if sex else sexes)
                      for g in ([age] if age else groups))
        name = " / ".join(part for part in (where, age_label(age) if age else None, sex_names.get(sex)) if part)
        code = "_".join(part for part in (slug, _age_code(age) if age else None, SEX_CODES.get(sex)) if part)
        return {"code": code, "name": name, "place": where, "states": codes, "age": age, "sex": sex, "persons": persons}

    by_sex = ("de_geschlecht", "continue", ("sex",), [cell(sex=x) for x in sexes])
    by_age = ("de_alter", "end", ("age",), [cell(age=g) for g in groups])
    by_state = ("de_land", "end", ("state",), [cell(p) for p in regions])
    if opts["cross"] == "full":
        wanted = [("de_land_alter_geschlecht", "continue", ("state", "age", "sex"),
                   [cell(p, g, x) for p in regions for g in groups for x in sexes])]
    elif opts["cross"] == "age_gender":
        wanted = [("de_alter_geschlecht", "continue", ("age", "sex"), [cell(age=g, sex=x) for g in groups for x in sexes]), by_state]
    elif opts["cross"] == "age_state":
        wanted = [("de_land_alter", "end", ("state", "age"), [cell(p, g) for p in regions for g in groups]), by_sex]
    elif opts["cross"] == "gender_state":
        wanted = [("de_land_geschlecht", "continue", ("state", "sex"), [cell(p, sex=x) for p in regions for x in sexes]), by_age]
    else:
        wanted = [by_sex, by_age, by_state]
    persons = sum(c["persons"] for c in by_age[3])
    out = {"quotas": [], "persons": persons, "groups": groups, "errors": [], "hints": []}
    for code, no_cell, attrs, cells in wanted:
        targets = _rounded([c["persons"] for c in cells], opts["n"]) if opts["n"] else [0] * len(cells)
        for c, target in zip(cells, targets):
            c["target"], c["share"] = target, 100 * c["persons"] / persons
        # with the states put together the quota is called after regions
        name = QUOTAS[code] if opts.get("regions", "states") == "states" else QUOTAS[code].replace("Bundesland", "Region")
        out["quotas"].append({"code": code, "name": name, "no_cell": no_cell, "attrs": attrs, "cells": cells})
        if not opts["n"]:
            continue
        empty = sum(1 for c in cells if c["target"] == 0)
        small = sum(1 for c in cells if 0 < c["target"] < SMALL)
        if empty:
            out["errors"].append(de_en("„{name}“: {empty} von {n} Teilquoten hätten das Soll 0; ihre Personen würden "
                                       "immer abgewiesen. Bitte eine größere Stichprobe wählen oder weniger kreuzen.",
                                       "“{name}”: {empty} of {n} sub-quotas would have a target of 0; their people would "
                                       "always be turned away. Please choose a larger sample or cross less.",
                                       name=name, empty=empty, n=len(cells)))
        elif small:
            out["hints"].append(de_en("„{name}“: {small} von {n} Teilquoten haben ein Soll unter {limit} "
                                      "(kleinstes Soll {least}). So kleine Teilquoten füllen sich nur langsam und zufällig.",
                                      "“{name}”: {small} of {n} sub-quotas have a target below {limit} "
                                      "(smallest target {least}). Sub-quotas this small fill only slowly and by chance.",
                                      name=name, small=small, n=len(cells), limit=SMALL,
                                      least=min(c["target"] for c in cells)))
    if opts["cross"] == "full":
        out["hints"].append(de_en("Voll gekreuzt schließt die Befragung nicht von selbst, wenn alle Teilquoten voll sind (wer „divers“ "
                                  "oder „keine Angabe“ wählt, kommt weiter hinein). Bei Bedarf in den Einstellungen das Ende „nach N "
                                  "abgeschlossenen Teilnahmen“ setzen.",
                                  "Fully crossed, the survey does not close automatically when all sub-quotas are full (anyone who "
                                  "chooses “diverse” or “no answer” still gets in). If needed, set the end “after N completed "
                                  "participations” in the settings."))
    if opts["hi"] is None:
        share = 100 * sum(_persons(s, x, (70, None)) for s in states for x in sexes) / persons
        out["hints"].append(de_en("{share} % der Bevölkerung {span} sind 70 Jahre und älter. Online ist diese "
                                  "Gruppe schwer zu erreichen; mit einer Obergrenze wird die Grundgesamtheit kleiner, aber erreichbar.",
                                  "{share}% of the population {span} are 70 or older. This group is hard to reach "
                                  "online; with an upper limit the population gets smaller but reachable.",
                                  share=fixed(share, 0), span=span_shown(opts["lo"], None)))
    return out


def grid(quota):
    """A crossed quota as a table for the preview: {"rows": [label], "cols": [label], "cells": [[cell]]}; the
    first attribute down, the others across. None for a quota of one attribute."""
    if len(quota["attrs"]) < 2:
        return None

    def label(c, attrs):
        if len(attrs) > 1:  # two attributes across: short, "18–29 m", "70+ w"
            a, b = c["age"]
            return (f"{a}+" if b is None else f"{a}–{b}") + " " + SEX_CODES[c["sex"]]
        parts = {"state": c["place"], "age": age_label(c["age"]) if c["age"] else None,
                 "sex": dict(SEXES).get(c["sex"])}
        return parts[attrs[0]]

    first, rest = quota["attrs"][:1], quota["attrs"][1:]
    rows, cols, table = [], [], {}
    for c in quota["cells"]:
        r, k = label(c, first), label(c, rest)
        if r not in rows:
            rows.append(r)
        if k not in cols:
            cols.append(k)
        table[(r, k)] = c
    return {"rows": rows, "cols": cols, "cells": [[table[(r, k)] for k in cols] for r in rows]}


# --- the survey -----------------------------------------------------------------------------------------


def _ordered(survey):
    return [item for block in sorted(survey.blocks, key=lambda b: b.position) for item in block.items]


def state_sources(survey):
    """The questions that can give the federal state, in the order of the questionnaire: the elements Bundesland,
    the postal code questions (the state the answered postal code lies in) and the location elements (the state the
    shared position lies in, items/geolocation.py)."""
    return [item for item in _ordered(survey) if item.type in ("federal_state", "postal_code", "geolocation")]


# self-built questions the assistant takes for age and gender (UI/UX audit 2026-10-05, V18): an age question asks
# for a whole number, and its code or wording says so; a gender question is a single choice with female and male
_AGE_WORDS = re.compile(r"(^|[^a-zäöüß])(alter|age|wie alt|how old|lebensalter)([^a-zäöüß]|$)", re.I)
_FEMALE = re.compile(r"^\s*(weiblich|frau|female|woman|feminin|féminin|femenino|femminile|vrouw)", re.I)
_MALE = re.compile(r"^\s*(männlich|maennlich|mann|male|man|masculin|masculino|maschile)\b", re.I)


def _construct(item):
    return ((item.config or {}).get("construct") or {}).get("key")


def _is_construct(item, key):
    """Is `item` built from a construct for age or gender ("age", "gender": either gender construct)?"""
    found = _construct(item)
    return found in GENDER_CONSTRUCTS if key == "gender" else found == key


def _wording(item):
    from app.items.base import plain

    if item.block is not None:  # with the standard wording, which is not stored (S9)
        return plain(item.text("question") or "")
    texts = item.texts or {}
    return plain((next(iter(texts.values()), {}) or {}).get("question") or "")


def age_sources(survey):
    """The questions that can give the age in years: the construct Alter and every question for a whole number."""
    return [item for item in _ordered(survey) if _construct(item) == "age"
            or (item.type == "text_input" and (item.config or {}).get("validation") == "integer")]


def gender_sources(survey):
    """The questions that can give the gender: the construct Geschlecht and every single choice."""
    return [item for item in _ordered(survey) if _is_construct(item, "gender") or item.type == "single_choice"]


def gender_codes(item, opts=None):
    """{"f": answer code, "m": answer code} of a gender question: the construct by its fixed codes, a self-built one
    by what was chosen on the page (gf, gm) or else by the wording of its answers (weiblich, männlich). A sex
    without a code is missing."""
    from app import flow
    from app.items.base import plain

    if item is None:
        return {}
    codes = {code: label for code, label in flow.answer_codes(item)}
    if _is_construct(item, "gender"):
        return {k: v for k, v in GENDER_CONSTRUCTS[_construct(item)].items() if v in codes}
    opts = opts or {}
    out = {}
    for sex, key, words in (("f", "gf", _FEMALE), ("m", "gm", _MALE)):
        if opts.get(key) in codes:
            out[sex] = opts[key]
        else:
            out[sex] = next((code for code, label in codes.items() if words.match(plain(label))), None)
    return {sex: code for sex, code in out.items() if code is not None}


def _looks_like(item, key):
    if key == "age":
        return bool(_AGE_WORDS.search(item.code or "") or _AGE_WORDS.search(_wording(item)))
    return len(gender_codes(item)) == 2


def questions(survey, state="", age="", gender=""):
    """The three questions as far as the survey has them: {"age": Item | None, "gender": ..., "state": ...}.
    Age and gender: the chosen question (`age`, `gender`: the id of one of `age_sources` / `gender_sources`), "new"
    for a new construct, else the first construct Alter / Geschlecht and then the first self-built question that
    looks like one (a whole number named Alter, a single choice with weiblich and männlich). The state comes from
    the chosen question (`state`, the id of one of `state_sources`), else from the first element Bundesland, else
    from the first postal code question; "new" asks for a new element Bundesland as long as the survey has none."""
    out = {"age": None, "gender": None, "state": None}
    for key, wanted, sources in (("age", age, age_sources(survey)), ("gender", gender, gender_sources(survey))):
        if wanted == "new":
            continue
        chosen = next((item for item in sources if str(item.id) == str(wanted)), None) if wanted else None
        out[key] = chosen or next((item for item in sources if _is_construct(item, key)), None)             or next((item for item in sources if _construct(item) is None and _looks_like(item, key)), None)
    sources = state_sources(survey)
    elements = [item for item in sources if item.type == "federal_state"]
    chosen = next((item for item in sources if str(item.id) == state), None)
    if chosen is None and not (state == "new" and not elements):
        chosen = next(iter(elements or sources), None)
    out["state"] = chosen
    return out


def chosen_questions(survey, opts):
    """`questions` for the choices of the page."""
    return questions(survey, opts.get("state", ""), opts.get("age", ""), opts.get("gender", ""))


def described(item, key):
    """How the preview names a question: "Konstrukt Alter" or "Frage Alter"."""
    if english():
        return ("construct " if _is_construct(item, key) else "question ") + ATTRIBUTES_EN[key].lower()
    return ("Konstrukt " if _is_construct(item, key) else "Frage ") + ATTRIBUTES[key]


def others_on(survey, found_):
    """Quotas not made by the assistant that already ask for one of the three questions: [(quota, [attribute names])]."""
    from app import flow

    own = {q.id for q in own_quotas(survey)}
    ids = {key: item.id for key, item in found_.items() if item is not None}
    out = []
    for quota in survey.quotas:
        if quota.id in own:
            continue
        refs = {str(c.get("item")) for cell in quota.cells for c in (cell.conds or [])}
        hit = [attributes()[key] for key, i in ids.items() if str(i) in refs or f"{flow.STATE_REF}{i}" in refs]
        if hit:
            out.append((quota, hit))
    return out


def from_postal_code(item):
    """True if the state is worked out from the answer (a postal code, a location), read as flow.STATE_REF + id."""
    return item is not None and item.type in ("postal_code", "geolocation")


def from_location(item):
    return item is not None and item.type == "geolocation"


def own_quotas(survey):
    return [q for q in survey.quotas if q.code in QUOTAS]


def _layout(survey):
    """Where the questions go: (index among the top-level nodes of the flow right behind the consent, the
    block "Demographische Angaben" at the top level or None, whether it already stands at that index)."""
    from app import flow

    top = flow.tree(survey).get("children", [])
    consent = survey.consent_item
    at = next((k + 1 for k, n in enumerate(top) if consent is not None and n["t"] == "block" and n["block"] == consent.block_id), 0)
    blocks = {b.id: b for b in survey.blocks}
    block = next((blocks[n["block"]] for n in top if n["t"] == "block" and n["block"] in blocks
                  and (blocks[n["block"]].settings or {}).get("demographics")), None)
    in_place = block is not None and at < len(top) and top[at]["t"] == "block" and top[at]["block"] == block.id
    return at, block, in_place


def problems(survey, opts):
    """Why the assistant cannot run on this survey with these choices: [message]."""
    from app import quotas

    found = chosen_questions(survey, opts)
    out = []
    age, gender = found["age"], found["gender"]
    if age is not None and (age.type != "text_input" or (age.config or {}).get("validation") != "integer"):
        out.append(de_en("Die Frage „{code}“ ({what}) fragt keine ganze Zahl ab; damit lassen sich keine Altersgruppen bilden.",
                         "The question “{code}” ({what}) does not ask for a whole number; no age groups can be formed "
                         "from it.", code=age.code, what=described(age, "age")))
    if gender is not None and len(gender_codes(gender, opts)) < 2:
        out.append(de_en("Bei der Frage „{code}“ ({what}) ist nicht zugeordnet, welche Antwort weiblich und welche männlich heißt.",
                         "For the question “{code}” ({what}) it is not set which answer means female and which male.",
                         code=gender.code, what=described(gender, "gender")) if not _is_construct(gender, "gender") else
                   de_en("Die Frage „{code}“ (Konstrukt Geschlecht) hat die Antworten männlich und weiblich nicht mehr.",
                         "The question “{code}” (construct gender) no longer has the answers male and female.",
                         code=gender.code))
    elif gender is not None and len(set(gender_codes(gender, opts).values())) < 2:
        out.append(de_en("Bei der Frage „{code}“ ist weiblich und männlich dieselbe Antwort zugeordnet.",
                         "For the question “{code}”, female and male are set to the same answer.", code=gender.code))
    replaced = len(others_on(survey, found)) if opts.get("others", "replace") == "replace" else 0
    if len(survey.quotas) - len(own_quotas(survey)) - replaced + len(plan(opts)["quotas"]) > quotas.MAX_QUOTAS:
        out.append(de_en("Höchstens {n} Quoten je Befragung; bitte zuerst eine löschen.",
                         "At most {n} quotas per survey; please delete one first.", n=quotas.MAX_QUOTAS))
    return out


def steps(survey, opts):
    """What `apply` would do, in words for the preview: [text]."""
    from app.items.federal_state import ABROAD, NO_ANSWER
    from app.wizard import DEMOGRAPHICS

    found = chosen_questions(survey, opts)
    postal = from_postal_code(found["state"])
    _, block, in_place = _layout(survey)
    title = block.title() if block is not None else DEMOGRAPHICS["de"]
    out = []
    en = english()
    q = ("“", "”") if en else ("„", "“")

    def quoted(text):
        return f"{q[0]}{text}{q[1]}"

    old = own_quotas(survey)
    if old:
        out.append(de_en("Die bisherigen Quoten des Assistenten werden ersetzt: {names}. Andere Quoten bleiben, wie sie sind.",
                         "The assistant's previous quotas are replaced: {names}. Other quotas stay as they are.",
                         names=", ".join(quoted(q_.name) for q_ in old)))
    # quotas of other origin on the same attributes: a box with the choice above the preview (W18), not a step here
    move = opts.get("move")
    if block is None and (move or any(found[k] is None for k in ("age", "gender", "state"))):
        out.append(de_en("Ein neuer Block „{title}“ entsteht direkt hinter der Einwilligung.",
                         "A new block “{title}” is created right after the consent.", title=title))
    elif block is not None and move and not in_place:
        out.append(de_en("Der Block „{title}“ rückt direkt hinter die Einwilligung.",
                         "The block “{title}” moves right after the consent.", title=title))
    names = {"age": described(found["age"], "age") if found["age"] is not None else de_en("Konstrukt Alter", "construct age"),
             "gender": described(found["gender"], "gender") if found["gender"] is not None else
             de_en("Konstrukt Geschlecht", "construct gender"),
             "state": (de_en("Standort, daraus das Bundesland", "location, the federal state from it") if from_location(found["state"]) else
                       de_en("Postleitzahl, daraus das Bundesland", "postcode, the federal state from it")) if postal else
             de_en("Element Bundesland", "element federal state")}
    for key in ("age", "gender", "state"):
        item = found[key]
        if item is None:
            out.append(de_en("Das {what} wird in den Block „{title}“ eingefügt.", "The {what} is inserted into the block “{title}”.",
                             what=names[key], title=title))
        elif move and (block is None or item.block_id != block.id):
            out.append(de_en("Die Frage „{code}“ ({what}) rückt aus dem Block „{was}“ in den Block „{title}“.",
                             "The question “{code}” ({what}) moves from the block “{was}” into the block “{title}”.",
                             code=item.code, what=names[key], was=item.block.title(), title=title))
        else:
            out.append(de_en("Die Frage „{code}“ ({what}) ist vorhanden und bleibt, wo sie ist.",
                             "The question “{code}” ({what}) is there and stays where it is.", code=item.code, what=names[key]))
    for key in ("age", "state"):
        if found[key] is not None and found[key].answer_mode != "required":
            out.append(de_en("„{code}“ wird zur Pflichtfrage: Ohne Antwort lässt sich niemand einer Teilquote zuordnen.",
                             "“{code}” becomes a required question: without an answer nobody can be assigned to a sub-quota.",
                             code=found[key].code))
    state = found["state"]
    if from_location(state):
        ip = (state.config or {}).get("fallback") == "ip"
        out.append(de_en("Das Bundesland ergibt sich aus dem Standort in „{code}“; ein Element Bundesland wird nicht "
                         "eingefügt. Wer den Standort nicht teilt, hat kein Bundesland und endet als Screen-out",
                         "The federal state comes from the location in “{code}”; no element federal state is inserted. "
                         "Anyone who does not share the location has no federal state and ends as a screen-out", code=state.code)
                   + (de_en(", außer die IP-Adresse ergibt eins (Ersatz im Element).",
                            ", unless the IP address gives one (fallback in the element).") if ip
                      else de_en(" (im Element lässt sich als Ersatz die IP-Adresse wählen).",
                                 " (the IP address can be chosen as a fallback in the element).")))
    elif postal:
        out.append(de_en("Das Bundesland ergibt sich aus der Postleitzahl in „{code}“; ein Element Bundesland wird nicht eingefügt.",
                         "The federal state comes from the postcode in “{code}”; no element federal state is inserted.",
                         code=state.code))
    elif state is not None:
        offered = {o["id"] for o in (state.config or {}).get("options", [])}
        if ABROAD["id"] not in offered:
            out.append(de_en("„{code}“ bekommt die Antwort „Ich wohne nicht in Deutschland“ (führt zum Screen-out).",
                             "“{code}” gets the answer “Ich wohne nicht in Deutschland” (leads to a screen-out).", code=state.code))
        if NO_ANSWER["id"] in offered:
            out.append(de_en("„{code}“ verliert die Antwort „keine Angabe“.", "“{code}” loses the answer “keine Angabe”.",
                             code=state.code))
    outside = [(de_en("keinen Standort in Deutschland teilt", "does not share a location in Germany") if from_location(state) else
                de_en("eine Postleitzahl nennt, die es in Deutschland nicht gibt", "gives a postcode that does not exist in Germany"))
               if postal else de_en("nicht in Deutschland wohnt", "does not live in Germany"),
               de_en("jünger als {n} ist", "is younger than {n}", n=opts["lo"])] + (
                  [de_en("älter als {n} ist", "is older than {n}", n=opts["hi"])] if opts["hi"] else [])
    out.append(de_en("Vor der Quotenberechnung endet die Teilnahme als Screen-out für alle, die nicht zur Grundgesamtheit gehören: wer ",
                     "Before the quota calculation, the participation ends as a screen-out for everyone outside the population: anyone who ")
               + ", ".join(outside[:-1]) + de_en(" oder ", " or ") + outside[-1] + ".")
    from app import flow

    empty = next((n for n in flow.walk_nodes(survey, "quota") if not n.get("quotas")), None)
    if not move and any(found[k] is not None and (block is None or found[k].block_id != block.id) for k in ("age", "gender", "state")):
        out.append(de_en("Screen-out und Quotenberechnung stehen im Ablauf hinter dem Block mit der letzten der drei Fragen.",
                         "Screen-out and quota calculation come in the flow after the block with the last of the three "
                         "questions."))
    out.append((de_en("Die leere Quotenberechnung im Ablauf wird dafür verwendet und direkt dahinter gesetzt",
                      "The empty quota calculation in the flow is used for this and placed right after it") if empty is not None
                else de_en("Direkt dahinter steht die Quotenberechnung", "The quota calculation comes right after it"))
               + de_en(", gefolgt von einem Ende mit einem Auslöser je Teilquote: Ist die Teilquote der Person voll, endet die "
                       "Teilnahme dort als „Quote voll“, und jeder Auslöser kann einen eigenen Text und Abschlusslink bekommen. Passt "
                       "ihr in einer Quote, die alle abdeckt, keine Teilquote, endet sie als Screen-out.",
                       ", followed by an end with one trigger per sub-quota: if the person's sub-quota is full, the participation "
                       "ends there as “quota full”, and each trigger can get its own text and completion link. If no sub-quota of a "
                       "quota that covers everyone fits, it ends as a screen-out."))
    out.append(de_en("Wer „divers“ oder „keine Angabe“ beim Geschlecht wählt, nimmt teil und zählt in keiner Teilquote des Geschlechts.",
                     "Anyone who chooses “divers” or “keine Angabe” for gender takes part and counts in no sub-quota of gender.")
               + ("" if opts["cross"] == "full" else
                  de_en(" Diese Personen belegen aber Plätze in den Quoten ohne Geschlecht (Alter, Bundesland bzw. Region): "
                        "Sind diese voll, schließt die Befragung, und die Geschlechtsquote bleibt um diese Personen unter Soll.",
                        " These people do take places in the quotas without gender (age, federal state or region): when these "
                        "are full, the survey closes, and the gender quota stays below target by these people.")))
    return out


def _free_code(survey, base):
    used = {i.code for b in survey.blocks for i in b.items}
    if base not in used:
        return base
    n = 2
    while f"{base}{n}" in used:
        n += 1
    return f"{base}{n}"


def _new_item(survey, key):
    from app import constructs, items
    from app.models import Item

    if key == "state":
        kind = items.get("federal_state")
        return Item(type=kind.key, answer_mode="required", code=_free_code(survey, "bundesland"),
                    config=kind.default_config(), texts={l: kind.default_texts(l) for l in survey.languages})
    kind_key, code, config, texts = constructs.build(key, survey.languages)
    return Item(type=kind_key, answer_mode="required" if key == "age" else "nudge", code=_free_code(survey, code),
                config=config, texts=texts)


def _clear(survey):
    """Take away what an earlier run made: its quotas, its screen-outs and its end "Quote voll". Its quota
    calculation stays, now empty: `apply` takes over the first empty one it finds."""
    import copy

    from app import flow, quotas

    for quota in own_quotas(survey):
        quotas.remove(survey, quota)
    root = copy.deepcopy(flow.tree(survey))

    def clean(node):
        kept = []
        for child in node.get("children", []):
            if child.get("by") == MARK and child["t"] != "quota":
                # a screen-out goes with its end; blocks someone put into it stay in the flow
                kept.extend(n for n in child.get("children", []) if n.get("by") != MARK)
                continue
            if child["t"] in flow.GROUPS:
                clean(child)
            kept.append(child)
        node["children"] = kept

    clean(root)
    survey.flow = root


def cell_conds(cell, age, gender, state, sexes=None):
    """The conditions of a planned cell (`plan`) on the three questions: age and gender by their ids, state by the
    id of the element Bundesland or flow.STATE_REF + the id of a postal code question. The weighting
    (app/weights.py) finds a person's group by them as well. sexes: the answer codes of female and male
    ({"f", "m"}, `gender_codes`); the codes of the construct Geschlecht without."""
    from app import conditions

    out = []
    if cell["states"]:  # one state, or one of the states of a region
        codes = cell["states"]
        out.append({"item": state, "op": "eq", "value": codes[0]} if len(codes) == 1
                   else {"item": state, "op": "in", "value": ", ".join(codes)})
    if cell["age"]:
        a, b = cell["age"]
        out.append(conditions.range_cond(age, str(a), "" if b is None else str(b)))
    if cell["sex"]:
        out.append({"item": gender, "op": "eq", "value": (sexes or GENDER_ANSWERS)[cell["sex"]]})
    return out


def apply(survey, opts):
    """Put the questions, the screen-outs, the quotas, the quota calculation and the end into the survey (a draft). The caller
    has checked `problems` and the errors of `plan`, and commits. Returns the quotas made."""
    from app import flow
    from app.extensions import db
    from app.items import federal_state
    from app.models import Block, Quota, QuotaCell
    from app.quotas import DEFAULT_HOLD
    from app.wizard import DEMOGRAPHICS

    from app import quotas as quotas_mod

    found = chosen_questions(survey, opts)
    others = [quota for quota, _ in others_on(survey, found)] if opts.get("others", "replace") == "replace" else []
    _clear(survey)
    for quota in others:  # "bestehende Quote ersetzen", chosen above the preview (UI/UX audit 2026-10-07, W18)
        quotas_mod.remove(survey, quota)
    planned = plan(opts)
    postal = from_postal_code(found["state"])
    move = bool(opts.get("move"))
    _, block, _ = _layout(survey)
    created = False
    if block is None and (move or any(found[k] is None for k in ("age", "gender", "state"))):
        block = Block(position=len(survey.blocks), settings={"demographics": True},
                      titles={l: DEMOGRAPHICS.get(l, DEMOGRAPHICS["en"]) for l in survey.languages})
        survey.blocks.append(block)
        created = True
    rows = list(block.items) if block is not None else []
    for key in ("age", "gender", "state"):
        item = found[key]
        if item is None:
            item = found[key] = _new_item(survey, key)
            rows.append(item)
        elif move and item.block is not block:
            source = item.block
            rest = [i for i in source.items if i is not item]
            rows.append(item)
            source.items = rest
            for position, other in enumerate(rest):
                other.position = position
    if block is not None:
        block.items = rows
        for position, item in enumerate(rows):
            item.position = position
    for key in ("age", "state"):
        found[key].answer_mode = "required"
    if not postal:
        from app import answer_lock

        state_item = found["state"]
        before, options = answer_lock.shape(state_item), federal_state._options(True, False)
        if state_item.id is not None and answer_lock.refused_shape(survey, state_item, before, answer_lock.shape_of(
                state_item, state_item.code, {**(state_item.config or {}), "options": options})):
            # real answers in a survey opened for editing again keep their options (app/answer_lock.py): only
            # "abroad" is added, which the screen-out needs
            had = (state_item.config or {}).get("options") or []
            options = had + [o for o in options if o["code"] == federal_state.ABROAD["code"]
                             and all(h.get("code") != o["code"] for h in had)]
        state_item.config = {**(state_item.config or {}), "options": options}
    db.session.flush()  # the ids of the new block and questions

    age, gender, state = found["age"].id, found["gender"].id, found["state"].id
    if postal:  # the state the answered postal code (the shared location) lies in
        state = f"{flow.STATE_REF}{state}"
    sexes = gender_codes(found["gender"], opts)

    def conds(cell):
        return cell_conds(cell, age, gender, state, sexes)

    made = []
    for spec in planned["quotas"]:
        quota = Quota(code=spec["code"], name=spec["name"], position=len(survey.quotas), mode=Quota.HARD, tolerance=10,
                      tolerance_pct=True, no_cell="continue", hold_minutes=DEFAULT_HOLD, count_at="complete")
        quota.cells = [QuotaCell(name=c["name"], code=c["code"], conds=conds(c), target=c["target"], closed=False, running=0,
                                 done=0, refused=0, over=0, position=n) for n, c in enumerate(spec["cells"])]
        survey.quotas.append(quota)
        made.append(quota)
    db.session.flush()

    def screen_out(name, cond):
        return {"t": "branch", "id": flow._nid("b"), "name": name, "cond": cond, "by": MARK,
                "children": [{"t": "end", "id": flow._nid("e"), "text": {}, "kind": "screenout", "by": MARK}]}

    # a postal code that does not exist in Germany has no state: the same people the answer "abroad" stands for
    # a location outside Germany has the state "0", a refusal none
    location = from_location(found["state"])
    nodes = ([screen_out("Standort ohne Bundesland", {"item": state, "op": "empty", "value": ""}),
              screen_out("Standort nicht in Deutschland", {"item": state, "op": "eq", "value": "0"})] if location
             else [screen_out("Postleitzahl ohne Bundesland", {"item": state, "op": "empty", "value": ""})] if postal
             else [screen_out("Wohnt nicht in Deutschland", {"item": state, "op": "eq", "value": federal_state.ABROAD["code"]})])
    nodes += [
              screen_out(f"Jünger als {opts['lo']}", {"item": age, "op": "lt", "value": str(opts["lo"])})]
    if opts["hi"]:
        nodes.append(screen_out(f"Älter als {opts['hi']}", {"item": age, "op": "gt", "value": str(opts["hi"])}))
    data = population()
    root = flow.normalize(survey)
    top = root["children"]
    # a quota calculation without a quota (added by hand, or left by an earlier run) is taken over and moved
    # behind the questions; else a new one
    check, parent = next(((n, p) for n, p, _ in flow.walk(root) if n["t"] == "quota" and not n.get("quotas")), (None, None))
    if check is None:
        check = {"t": "quota", "id": flow._nid("q")}
    else:
        parent["children"].remove(check)
    check.update(quotas=[q.id for q in made], by=MARK,
                 census={"table": data["table"], "date": data["date"], "n": opts["n"], "cross": opts["cross"], "lo": opts["lo"],
                         "hi": opts["hi"], "scheme": opts["scheme"], "regions": opts.get("regions", "states"),
                         "state": ("geolocation" if location else "postal_code") if postal else "federal_state",
                         # the questions it took, so that the weighting (app/weights.py) takes the same
                         "age_item": age, "gender_item": gender, "sexes": sexes})
    nodes.append(check)
    # behind it one end with a trigger for every cell (Teilquote) of every quota: that cell full ends the
    # participation as "Quote voll", and each trigger can get an end text and a completion link of its own in the
    # flow. Before them: whoever fits no cell of a quota that covers the whole population is screened out (nobody
    # should, after the screen-outs above; with it the survey can close by itself once such a quota is full)
    wide = [quota.id for spec, quota in zip(planned["quotas"], made) if spec["no_cell"] == "end"]
    blank = {"text": {}, "link": "", "link_mode": "now"}
    triggers = ([{"id": flow._nid("c"), "when": "nocell", "quotas": wide, **blank}] if wide else []) \
        + [{"id": flow._nid("c"), "when": "cellfull", "quota": quota.id, "cells": [cell.code], **blank}
           for quota in made for cell in quota.cells]
    nodes.append({"t": "end", "id": flow._nid("e"), "cases": triggers, "by": MARK})
    consent = survey.consent_item

    def after_consent():
        return next((k + 1 for k, n in enumerate(top) if consent is not None and n["t"] == "block" and n["block"] == consent.block_id), 0)

    if block is not None and (move or created):  # the block of the three questions right behind the consent
        own = next(n for n in top if n["t"] == "block" and n["block"] == block.id)
        top.remove(own)
        at = after_consent()
        top.insert(at, own)
    # the screen-outs and the quota calculation after the last of the three questions, wherever it is asked
    asked = {found[k].block_id for k in ("age", "gender", "state")}  # flushed above: every question has its block

    def holds(node):
        return (node["t"] == "block" and node["block"] in asked) or any(
            n["t"] == "block" and n["block"] in asked for n, _, _ in flow.walk(node))

    places = [k for k, n in enumerate(top) if holds(n)]
    at = (max(places) + 1) if places else after_consent()
    top[at:at] = nodes
    survey.flow = root
    flow.normalize(survey)
    return made


def clean_facts(raw, items):
    """The choices kept with the assistant's quota calculation (`census` on the node), from a survey file: only the
    offered values; the questions as those of the new survey (items: id in the file -> new id). None if the file
    holds none that fit."""
    if not isinstance(raw, dict) or not isinstance(raw.get("table"), str) or not isinstance(raw.get("date"), str) \
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw["date"]):
        return None

    def item(old):
        return items.get(old) if isinstance(old, int) and not isinstance(old, bool) else None

    sexes = raw.get("sexes") if isinstance(raw.get("sexes"), dict) else {}
    n = raw.get("n")
    return {"table": raw["table"][:40], "date": raw["date"],
            "n": n if isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= MAX_N else 0,
            "cross": raw.get("cross") if raw.get("cross") in CROSS else "none",
            "lo": raw.get("lo") if raw.get("lo") in AGE_FROM else AGE_FROM[0],
            "hi": raw.get("hi") if raw.get("hi") in AGE_TO else None,
            "scheme": raw.get("scheme") if raw.get("scheme") in SCHEMES else "decades",
            "regions": raw.get("regions") if raw.get("regions") in REGIONS else "states",
            "state": raw.get("state") if raw.get("state") in ("geolocation", "postal_code", "federal_state") else "federal_state",
            "age_item": item(raw.get("age_item")), "gender_item": item(raw.get("gender_item")),
            "sexes": {k: sexes[k][:40] for k in ("f", "m") if isinstance(sexes.get(k), str)}}


def facts(survey):
    """What the methods section says about the quotas of the assistant: the choices kept with its quota calculation
    ({"table", "date", "n", "cross", "lo", "hi", "scheme", "regions", "state"}), or None when the survey has none."""
    from app import flow

    for node in flow.walk_nodes(survey, "quota"):
        if node.get("by") == MARK and node.get("census") and node.get("quotas"):
            return node["census"]
    return None
