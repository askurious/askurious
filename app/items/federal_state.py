"""German federal state (Bundesland) of the participant, e.g. place of residence.

A single choice with a fixed list: the 16 states in alphabetical order of their German names, optionally
followed by "Ich wohne nicht in Deutschland" and "keine Angabe". The answer codes are the state keys of the
official municipality key (Amtlicher Gemeindeschlüssel, 01 to 16) without the leading zero, so the data can
be joined with official statistics; 0 = not in Germany, 99 = no answer (counts as missing).

Because the options sit in the config like those of a single choice, conditions in the flow and quotas work
on the states without code of their own. Besides radio buttons and the drop-down list of the single choice
the states can be shown as a simple map to click on (config "display": "map", see outline() and
items/federal_state/participant.html). The results page counts the answers per state and colours the
state outlines on the map (app/data/states_de.json.gz, see areas(), results() and
surveys/_state_results.html)."""
import functools
import gzip
import json
import math
import os

from flask import current_app

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.choice import LAYOUT_RULE
from app.items.base import Unfit, r_one  # rules for survey files, see base.py
from app.items.base import in_lang
from app.items.choice import SingleChoice
from app.items.map_marker import OSM_ATTRIBUTION, OSM_TILES
from app.items.postal_code import classes
from app.lang import Both, de_en
from app.ui_i18n import tr

AREAS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "states_de.json.gz")
# the attribution the BKG prescribes for its administrative areas, word for word: "© BKG (Jahr des letzten
# Datenbezugs) dl-de/by-2-0, Datenquellen: ...", "BKG" linked to bkg.bund.de and the licence to govdata (terms of use of
# the VG data, nutzungsbedingungen_vg250.pdf, as of 19.08.2025; Ralf 2026-10-21; before: "GeoBasis-DE / BKG", copyright
# audit 2026-10-16, U7). The year is that of our last download, 2026-10-02 (licence audit 2026-10-23, O10), not the
# data's own date 31.12.2025. That the outlines are simplified and reprojected the templates say in their language
BKG = ('<a href="https://www.bkg.bund.de/" target="_blank" rel="noopener">BKG</a> (2026) '
       '<a href="https://www.govdata.de/dl-de/by-2-0" target="_blank" rel="noopener">dl-de/by-2-0</a>, Datenquellen: '
       '<a href="https://sgx.geodatenzentrum.de/web_public/gdz/datenquellen/datenquellen_vg_nuts.pdf" target="_blank" rel="noopener">VG2500</a>')

# (state key of the AGS, German name, English name), in alphabetical order of the German names
STATES = [("8", "Baden-Württemberg", "Baden-Württemberg"), ("9", "Bayern", "Bavaria"), ("11", "Berlin", "Berlin"),
          ("12", "Brandenburg", "Brandenburg"), ("4", "Bremen", "Bremen"), ("2", "Hamburg", "Hamburg"),
          ("6", "Hessen", "Hesse"), ("13", "Mecklenburg-Vorpommern", "Mecklenburg-Western Pomerania"),
          ("3", "Niedersachsen", "Lower Saxony"), ("5", "Nordrhein-Westfalen", "North Rhine-Westphalia"),
          ("7", "Rheinland-Pfalz", "Rhineland-Palatinate"), ("10", "Saarland", "Saarland"),
          ("14", "Sachsen", "Saxony"), ("15", "Sachsen-Anhalt", "Saxony-Anhalt"),
          ("1", "Schleswig-Holstein", "Schleswig-Holstein"), ("16", "Thüringen", "Thuringia")]
ABROAD = {"id": "abroad", "code": "0"}
NO_ANSWER = {"id": "none", "code": "99"}
STATE_CODES = tuple(code for code, _, _ in STATES)


def _options(abroad, no_answer):
    return ([{"id": f"s{code}", "code": code} for code in STATE_CODES]
            + ([dict(ABROAD)] if abroad else []) + ([dict(NO_ANSWER)] if no_answer else []))


def names(lang):
    """{option id: label} of all options in `lang` (the catalog has the names in the other languages)."""
    out = {f"s{code}": tr(lang, de, en) for code, de, en in STATES}
    out[ABROAD["id"]] = tr(lang, "Ich wohne nicht in Deutschland", "I do not live in Germany")
    out[NO_ANSWER["id"]] = tr(lang, "keine Angabe", "prefer not to say")
    return out


@functools.cache
def areas():
    """Outlines of the 16 states (the server-drawn result maps and the participant's map), built by tools/build_states_de.py:
    {"arcs": [delta-encoded rings in 1e-5 degrees], "areas": {state code: {"type", "arcs"}}}, the format of
    the postal code areas. None while the file is missing: the results page then shows no map."""
    if not os.path.exists(AREAS):
        return None
    with gzip.open(AREAS, "rt", encoding="utf-8") as fh:
        return json.load(fh)


# Where the name of a state stands on the map to click on: (longitude, latitude), chosen by hand, because the
# middle of a state is often a bad place (Berlin lies in the middle of Brandenburg, Bremen in Niedersachsen)
LABEL_AT = {"1": (9.75, 54.2), "2": (10.02, 53.56), "3": (9.75, 52.62), "4": (8.75, 53.12), "5": (7.55, 51.5),
            "6": (9.0, 50.6), "7": (7.35, 49.95), "8": (9.05, 48.55), "9": (11.6, 48.95), "10": (6.95, 49.38),
            "11": (13.4, 52.5), "12": (13.85, 51.95), "13": (12.6, 53.75), "14": (13.3, 51.05), "15": (11.65, 52.05),
            "16": (11.0, 50.9)}
SMALL = ("2", "4", "10", "11")  # too small to hit on a phone: a larger invisible circle takes the click as well
UNITS = 100                     # map units per degree of latitude (about 1.1 km per unit)


@functools.cache
def outline():
    """The states as a small drawing for the participant page: {"width", "height", "paths": {code: SVG path},
    "at": {code: (x, y)}}. Plain projection (longitudes shortened by the cosine of the middle latitude), points
    snapped to whole units and points on a straight line dropped, so a border two states share stays one line.
    None while the outline file is missing."""
    topo = areas()
    if not topo:
        return None
    rings = {}
    for code, geom in topo["areas"].items():
        for poly in geom["arcs"]:
            for ids in poly:
                x = y = 0
                pts = []
                for i in ids:
                    for dx, dy in topo["arcs"][i]:
                        x, y = x + dx, y + dy
                        pts.append((x / 1e5, y / 1e5))
                rings.setdefault(code, []).append(pts)
    lons = [lon for rs in rings.values() for pts in rs for lon, _ in pts]
    lats = [lat for rs in rings.values() for pts in rs for _, lat in pts]
    west, north, margin = min(lons), max(lats), 4
    squeeze = math.cos(math.radians((north + min(lats)) / 2))

    def project(lon, lat):
        return round((lon - west) * squeeze * UNITS) + margin, round((north - lat) * UNITS) + margin

    def straight(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) == (b[1] - a[1]) * (c[0] - a[0])

    paths = {}
    for code, rs in rings.items():
        parts = []
        for pts in rs:
            ring = []
            for p in (project(lon, lat) for lon, lat in pts):
                if not ring or p != ring[-1]:
                    ring.append(p)
            if ring[0] == ring[-1]:
                ring.pop()
            ring = [p for n, p in enumerate(ring) if not straight(ring[n - 1], p, ring[(n + 1) % len(ring)])]
            if len(ring) < 3:
                continue  # an island smaller than a unit
            steps = " ".join(f"{b[0] - a[0]} {b[1] - a[1]}" for a, b in zip(ring, ring[1:]))
            parts.append(f"M{ring[0][0]} {ring[0][1]}l{steps}z")
        paths[code] = "".join(parts)
    return {"width": project(max(lons), north)[0] + margin, "height": project(west, min(lats))[1] + margin,
            "paths": paths, "at": {code: project(*LABEL_AT[code]) for code in paths},
            "proj": {"west": west, "north": north, "squeeze": squeeze, "units": UNITS, "margin": margin}}


def project(lon, lat):
    """A point in the units of the drawing of outline() (x, y as floats), e.g. for the squares of a heatmap; None
    while the outline file is missing."""
    drawing = outline()
    if drawing is None:
        return None
    p = drawing["proj"]
    return (lon - p["west"]) * p["squeeze"] * p["units"] + p["margin"], (p["north"] - lat) * p["units"] + p["margin"]


def label_lines(name):
    """A state's name in one or two lines for the map: a long double name breaks after its hyphen."""
    head, hyphen, tail = name.partition("-")
    return [head + hyphen, tail] if hyphen and tail and len(name) > 12 else [name]


def _offered(options):
    """The options of a survey file as this type stores them: the 16 states and those of the two extra answers
    the file names."""
    if not isinstance(options, list):
        raise Unfit
    ids = {o.get("id") for o in options if isinstance(o, dict) and isinstance(o.get("id"), str)}
    return _options(ABROAD["id"] in ids, NO_ANSWER["id"] in ids)


@register
class FederalState(SingleChoice):
    key = "federal_state"
    # the states are fixed: of the file only which of the two extra answers are offered
    import_rules = {"layout": LAYOUT_RULE, "display": r_one("dropdown", "map"), "options": lambda value: _offered(value)}
    label = Both("Bundesland", "Federal state")
    short = Both("Eins der 16 Bundesländer", "One of the 16 German states")
    description = Both("Auswahl aus den 16 deutschen Bundesländern, z. B. des Wohnorts; Auswertung mit Karte",
                       "Choice of the 16 German federal states, e.g. of residence; analysed with a map")
    order = 42
    fact_sheet = FactSheet(
        "classic",
        keywords=["Bundesland", "Federal state", "Länder", "States", "Deutschlandkarte", "Map of Germany"],
        can=[("Als Liste, als Dropdown oder als Karte.", "As a list, as a drop-down or as a map."),
             ("Mit „Ausland“ und „keine Angabe“.", "With “abroad” and “no answer”.")],
        good_for=[("Region in Deutschland für Quoten, Gewichtung und Vergleiche",
                   "Region in Germany for quotas, weighting and comparisons")],
        not_for=[("Befragungen außerhalb Deutschlands", "Surveys outside Germany")],
        designs=["cross", "perception"],
        examples=[("Quote nach Bundesland in einer Wahrnehmungsbefragung",
                   "Quota by federal state in a public perception survey")],
    )
    levels = ("nominal",)
    supports_missing = False  # fixed: "keine Angabe" (99) counts as missing
    supports_text = False
    supports_images = False
    supports_search = False  # 16 states: a list, a drop-down or the map

    supports_map = True  # the editor offers "Karte zum Anklicken" next to radio buttons and drop-down list

    def as_map(self, item):
        return (item.config or {}).get("display") == "map" and outline() is not None

    def set_display(self, item, form):
        super().set_display(item, form)
        if form.get("display") == "map":
            item.config = {**item.config, "display": "map"}

    def map_states(self, item, lang):
        """The states for the map to click on: [{"code", "label", "lines", "path", "x", "y", "small"}]."""
        drawing = outline()
        return [{"code": o["code"], "label": o["label"], "lines": label_lines(o["label"]), "path": drawing["paths"][o["code"]],
                 "x": drawing["at"][o["code"]][0], "y": drawing["at"][o["code"]][1], "small": o["code"] in SMALL}
                for o in self.options(item, lang) if o["code"] in drawing["paths"]]

    outline = staticmethod(outline)

    def default_config(self):
        return {"options": _options(True, False), "layout": "vertical"}

    def default_texts(self, lang):
        return {"question": tr(lang, "In welchem Bundesland wohnen Sie?", "In which German federal state do you live?"),
                "options": names(lang)}

    def offers(self, item, option):
        return any(o["id"] == option["id"] for o in item.config.get("options", []))

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt["question"] = form.get("question", "").strip()
        texts[lang] = lt
        item.texts = texts
        if structural:
            item.config = {**item.config, "options": _options(bool(form.get("abroad")), bool(form.get("no_answer")))}

    def pipe_live(self, item, lang):
        return None if (item.config or {}).get("display") == "map" else super().pipe_live(item, lang)

    def options(self, item, lang, answers=None):
        """aside: "Ich wohne nicht in Deutschland" and "keine Angabe", shown as answers set aside (S8)."""
        stored, fixed = item.t(lang).get("options") or {}, names(lang)
        return [{**o, "label": stored.get(o["id"]) or fixed.get(o["id"], ""), "aside": o["id"] in (ABROAD["id"], NO_ANSWER["id"])}
                for o in item.config.get("options", [])]

    def missing_codes(self, item, column):
        return (NO_ANSWER["code"],) if self.offers(item, NO_ANSWER) else ()

    def codebook_info(self, item, column, lang, ui=None):
        values = "; ".join(f"{o['code']} = {o['label']}" for o in self.options(item, lang))
        notes = [in_lang(ui, "Codes 1 bis 16: Länderschlüssel des Amtlichen Gemeindeschlüssels (AGS) ohne führende Null",
                         "codes 1 to 16: state key of the official municipality key (Amtlicher Gemeindeschlüssel, AGS) "
                         "without a leading zero")]
        if self.offers(item, NO_ANSWER):
            notes.append(in_lang(ui, "als fehlend gewertet: {code}", "counted as missing: {code}", code=NO_ANSWER['code']))
        return "Bundesland", values, notes

    def questionnaire_lines(self, item, lang):
        return [f"  - `{o['code']}` {o['label']}" for o in self.options(item, lang)]

    def dummy_fields(self, item, rng, meta, answers=None):
        return {f"i{item.id}": rng.choice([o["code"] for o in item.config.get("options", [])])}

    # --- results page -------------------------------------------------------------------

    def results(self, item, data, passes=(), lang="de"):
        """Answers per state for the map, over the rows in `data` (export rows after the results filter). In a
        loop every pass counts as one answer. Shares refer to the answers that name a state."""
        cols = [f"{item.code}_{n}" for n in passes] or [item.code]
        answers = [str(r.get(c)) for r in data for c in cols if r.get(c) not in (None, "")]
        counts = {code: answers.count(code) for code in STATE_CODES}
        n = sum(counts.values())
        labels = {o["code"]: o["label"] for o in self.options(item, lang)}
        cfg = current_app.config
        return {
            "topology": areas(), "counts": counts, "n": n,
            "names": {code: labels.get(code, "") for code in STATE_CODES},
            "classes": classes([v for v in counts.values() if v]),
            "empty": sum(1 for v in counts.values() if not v),
            "abroad": answers.count(ABROAD["code"]), "no_answer": answers.count(NO_ANSWER["code"]),
            # the base of the shares in the list of frequencies: every answer but "keine Angabe" (also "nicht in
            # Deutschland"); the map names its shares on the same base, so both say the same (D-12)
            "base": n + answers.count(ABROAD["code"]),
            "persons": sum(1 for r in data if any(r.get(c) not in (None, "") for c in cols)),
            "map": {"center": [51.1657, 10.4515], "zoom": 6,
                    "tiles": cfg.get("MAP_TILE_URL", OSM_TILES),
                    "attribution": cfg.get("MAP_TILE_ATTRIBUTION", OSM_ATTRIBUTION) + " · " + de_en("Ländergrenzen", "State borders") + ": © " + BKG},
        }
