"""Postal code (Postleitzahl) of the participant, e.g. place of residence.

Only German postal codes for now: exactly five digits, spaces are ignored. The answer is stored and
exported as text, so leading zeros stay ("01067" is Dresden, not 1067). A code missing from the
GeoNames table (app/data/plz_de.tsv) is only a soft warning, since the table can lag behind new codes.
The export adds the place name from that table. The results page colours the postal code areas from
OpenStreetMap (app/data/plz_areas, see region_file()) by the number of answers and counts them per postal
zone (first digit), region (first two digits) and code instead of computing a meaningless mean
(see results() and surveys/_postal_results.html)."""
import functools
import gzip
import json
import os
import re

from flask import current_app

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import AnswerError, ItemType, in_lang
from app.items.map_marker import OSM_ATTRIBUTION, OSM_TILES
from app.lang import Both, de_en
from app.ui_i18n import pick, tr

PLZ_RE = re.compile(r"^[0-9]{5}\Z")  # ASCII only: \d also takes e.g. full-width digits
TOP = 20  # most frequent postal codes on the results page
DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "plz_de.tsv")
AREAS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "plz_areas")
STATES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "plz_states_de.tsv")
OSM_AREAS = ('<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> '
             '(ODbL, via <a href="https://github.com/yetzt/postleitzahlen" target="_blank" rel="noopener">yetzt/postleitzahlen</a>)')

# Leitzonen of the Deutsche Post with a few larger cities each, as orientation on the results page
ZONES = {
    "0": "Dresden, Leipzig, Chemnitz, Halle",
    "1": "Berlin, Potsdam, Rostock, Schwerin",
    "2": "Hamburg, Bremen, Kiel, Lübeck",
    "3": "Hannover, Kassel, Bielefeld, Magdeburg",
    "4": "Düsseldorf, Essen, Dortmund, Münster",
    "5": "Köln, Bonn, Aachen, Koblenz, Trier",
    "6": "Frankfurt, Mainz, Mannheim, Saarbrücken",
    "7": "Stuttgart, Karlsruhe, Freiburg",
    "8": "München, Augsburg, Ulm, Rosenheim",
    "9": "Nürnberg, Würzburg, Regensburg, Erfurt",
}

MESSAGES = {
    "de": {"required": "Bitte geben Sie eine Postleitzahl ein.",
           "format": "Bitte geben Sie eine fünfstellige Postleitzahl ein.",
           "unknown": "Die Postleitzahl {plz} ist uns nicht bekannt. Bitte prüfen Sie sie; Sie können sie auch so übernehmen."},
    "en": {"required": "Please enter a postal code.",
           "format": "Please enter a five-digit postal code.",
           "unknown": "We do not know the postal code {plz}. Please check it; you can also keep it as it is."},
}


def normalize(value):
    """'52 062 ' -> '52062'; anything else is returned stripped, for the format check."""
    return re.sub(r"\s+", "", str(value or ""))


@functools.cache
def table():
    """{plz: (place, lat, lon)} for all German postal codes (GeoNames, see app/data/README.md)."""
    out = {}
    with open(DATA, encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            plz, place, lat, lon = line.rstrip("\n").split("\t")
            out[plz] = (place, float(lat), float(lon))
    return out


@functools.cache
def _states():
    """{plz: state key of the AGS} for all German postal codes, built by tools/build_plz_states_de.py from the
    areas of the codes and the outlines of the states; {} while the file is missing."""
    if not os.path.exists(STATES):
        return {}
    with open(STATES, encoding="utf-8") as fh:
        next(fh)
        return dict(line.rstrip("\n").split("\t") for line in fh if line.strip())


def state(plz):
    """The federal state of a postal code (the code of the element Bundesland: "5" = Nordrhein-Westfalen), or
    None for a code that does not exist. A code whose area crosses a border has the state with the larger part."""
    return _states().get(normalize(plz))


def known(plz):
    """An existing postal code: in the GeoNames table, or with an area in OpenStreetMap (some twenty codes
    are missing in the table)."""
    return plz in table() or plz in area_codes()


def place(plz):
    return (table().get(plz) or ("",))[0]


def region_file(prefix):
    """Path of the areas of one postal region (first two digits), built by tools/build_plz_areas.py: a small
    topology {"arcs": [delta-encoded arcs in 1e-5 degrees], "areas": {plz: {"type", "arcs"}}}, gzipped. None
    if `prefix` is not two digits or the file is missing. The browser loads these files itself (app/geo.py,
    static/postal_map.js); the server no longer reads them for a page: all 95 take 480 MB as Python objects."""
    path = os.path.join(AREAS, f"{prefix}.json.gz")
    return path if re.fullmatch(r"[0-9]{2}", str(prefix)) and os.path.exists(path) else None


@functools.cache
def area_codes():
    """All postal codes with an area in OpenStreetMap, as a frozenset: the list tools/build_plz_areas.py
    writes next to the region files (codes.txt). Without the list the region files are read once."""
    listed = os.path.join(AREAS, "codes.txt")
    if os.path.exists(listed):
        with open(listed, encoding="utf-8") as fh:
            return frozenset(line.strip() for line in fh if line.strip())
    codes = set()
    for name in sorted(os.listdir(AREAS)) if os.path.isdir(AREAS) else []:
        if region_file(name[:2]) and name.endswith(".json.gz"):
            with gzip.open(os.path.join(AREAS, name), "rt", encoding="utf-8") as fh:
                codes.update(json.load(fh)["areas"])
    return frozenset(codes)


@functools.cache
def region_total():
    """Number of postal regions (first two digits) that exist: in the GeoNames table or with an area (96; the
    areas alone know 95)."""
    return len({plz[:2] for plz in table()} | {plz[:2] for plz in area_codes()})


@functools.cache
def _codes_by_region():
    out = {}
    for plz in sorted(area_codes()):
        out.setdefault(plz[:2], []).append(plz)
    return out


@functools.cache
def areas_version():
    """Changes when the region files do: part of their address, so browsers may keep them for a year."""
    import hashlib

    names = sorted(n for n in os.listdir(AREAS) if n.endswith(".json.gz")) if os.path.isdir(AREAS) else []
    return hashlib.sha256("".join(f"{n}:{os.path.getsize(os.path.join(AREAS, n))};" for n in names).encode()).hexdigest()[:12]


def random_code(rng):
    """An existing postal code for dummy data: a random region, then a random code with an area there, so
    dummy answers show up on the results map. Without area files: a code from the GeoNames table."""
    regions = _codes_by_region()
    if regions:
        return rng.choice(regions[rng.choice(sorted(regions))])
    return rng.choice(sorted(table()))


# the sequential scale of the researcher UI for the map, few to many (app.css --seq-1 to --seq-5, UI/UX audit
# 2026-10-07, S11): light and dark, every style; postal_map.js reads the colour of the page for them
SHADES = tuple(f"var(--seq-{i})" for i in range(1, 6))


def classes(counts):
    """Up to five colour classes over the answers per postal code: [{"lo", "hi", "color"}]. Few distinct
    counts get one class each; otherwise the breaks split the distinct counts evenly (not the areas, which
    are mostly 1), so high counts still stand out."""
    distinct = sorted(set(counts))
    if not distinct:
        return []
    k = len(SHADES)
    if len(distinct) <= k:
        bounds = [(v, v) for v in distinct]
    else:
        his = [distinct[(len(distinct) * i) // k - 1] for i in range(1, k)] + [distinct[-1]]
        bounds = list(zip([distinct[0]] + [h + 1 for h in his[:-1]], his))
    return [{"lo": lo, "hi": hi, "color": c} for (lo, hi), c in zip(bounds, SHADES[k - len(bounds):])]


def _counts(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return counts


@register
class PostalCode(ItemType):
    key = "postal_code"
    import_rules = {}
    label = Both("Postleitzahl", "Postal code")
    short = Both("Deutsche Postleitzahl", "German postal code")
    description = Both("Fünfstellige deutsche Postleitzahl, z. B. des Wohnorts; Auswertung nach Zone, Region und PLZ",
                       "Five-digit German postal code, e.g. of residence; analysed by zone, region and postal code")
    order = 41
    fact_sheet = FactSheet(
        "classic",
        keywords=["PLZ", "Postleitzahl", "Postcode", "ZIP code", "Wohnort", "Region"],
        can=[("Fünfstellige deutsche Postleitzahl.", "Five-digit German postal code."),
             ("Auswertung nach Zone, Region und Postleitzahl.", "Analysed by zone, region and postal code.")],
        good_for=[("Wohnort in Deutschland für regionale Vergleiche und Quoten",
                   "Place of residence in Germany for regional comparisons and quotas")],
        not_for=[("Befragungen außerhalb Deutschlands", "Surveys outside Germany"),
                 ("Eine grobe Region, wenn eine Postleitzahl zu genau ist (dann Bundesland)",
                  "A rough region when a postal code is too exact (then federal state)")],
        designs=["cross", "perception"],
        examples=[("Wie lautet die Postleitzahl Ihres Wohnorts?", "What is the postal code of where you live?")],
    )
    result_kind = "postal"  # no mean of postal codes: frequencies per zone, region and code (results())
    zones = ZONES

    def default_texts(self, lang):
        return {"question": tr(lang, "Wie lautet die Postleitzahl Ihres Wohnorts?", "What is the postal code of your place of residence?")}

    def result_column(self, item, column):
        return None, "", None

    def parse_answer(self, item, form, lang="de", meta=None):
        m = pick(MESSAGES, lang)
        value = normalize(form.get(f"i{item.id}", ""))
        if not value:
            if item.required:
                raise AnswerError(m["required"])
            return None
        if not PLZ_RE.match(value):
            raise AnswerError(m["format"])
        return value

    def pipeable(self, item):
        return True

    def pipe_text(self, item, answer, lang):
        return str(answer or "")

    def soft_warning(self, item, answer, lang):
        if answer and not known(answer):
            return pick(MESSAGES, lang)["unknown"].format(plz=answer)
        return None

    def open_columns(self, item):
        return [c for c, _ in self.export_columns(item)]  # code and place name

    def export_columns(self, item):
        return [(item.code, item.text("question")), (f"{item.code}_ort", "Ort zur Postleitzahl (GeoNames)")]

    def export_values(self, item, answer):
        return {item.code: answer, f"{item.code}_ort": place(answer) if answer else None}

    def codebook_info(self, item, column, lang, ui=None):
        if column == f"{item.code}_ort":
            return "Text", "", [in_lang(ui, "aus der PLZ-Tabelle von GeoNames (CC BY 4.0), nicht von Teilnehmenden eingegeben",
                                        "from the postal code table of GeoNames (CC BY 4.0), not typed by participants"),
                                in_lang(ui, "leer, wenn die PLZ dort fehlt; „(Großkunde)“ bei PLZ nur für Firmen und Behörden",
                                        "empty if the postal code is missing there; “(Großkunde)” for postal codes of "
                                        "companies and authorities only")]
        return "Postleitzahl", in_lang(ui, "5 Ziffern (Deutschland)", "5 digits (Germany)"), [
            in_lang(ui, "als Text exportiert, damit führende Nullen erhalten bleiben", "exported as text so that leading zeros are kept")]

    # --- results page -------------------------------------------------------------------

    def results(self, item, data, passes=()):
        """Map areas and frequencies over the rows in `data` (export rows after the results filter). In a loop every
        pass counts as one answer."""
        cols = [f"{item.code}_{n}" for n in passes] or [item.code]
        codes = [normalize(r.get(c)) for r in data for c in cols]
        codes = [c for c in codes if PLZ_RE.match(c)]
        persons = sum(1 for r in data if any(PLZ_RE.match(normalize(r.get(c))) for c in cols))
        # five digits that are no German postal code (e.g. 00000; the participant was asked to check):
        # counted apart, so they form no zone or region of their own and do not enter the shares
        unknown = sum(1 for c in codes if not known(c))
        codes = [c for c in codes if known(c)]
        n = len(codes)

        def rows(counts, labels=None, order=None):
            keys = order if order is not None else sorted(counts, key=lambda k: (-counts[k], k))
            return [{"value": k, "label": (labels or {}).get(k, ""), "count": counts.get(k, 0),
                     "share": counts.get(k, 0) / n if n else 0} for k in keys]

        zone_counts = _counts(c[0] for c in codes)
        region_counts = _counts(c[:2] for c in codes)
        code_counts = _counts(codes)
        with_area = area_codes()
        on_map = sorted(k for k in code_counts if k in with_area)
        cfg = current_app.config
        return {
            # the regions whose boundaries the map needs; the browser loads them (see region_file)
            "area_regions": sorted({k[:2] for k in on_map}), "areas_version": areas_version(),
            "counts": code_counts, "classes": classes(code_counts.values()), "places": {k: place(k) for k in on_map},
            "without_area": sum(v for k, v in code_counts.items() if k not in with_area),
            "map": {"center": [51.1657, 10.4515], "zoom": 6,
                    "tiles": cfg.get("MAP_TILE_URL", OSM_TILES),
                    "attribution": cfg.get("MAP_TILE_ATTRIBUTION", OSM_ATTRIBUTION) + " · " + de_en("PLZ-Gebiete", "Postcode areas") + ": " + OSM_AREAS},
            "n": n, "unknown": unknown, "persons": persons, "distinct": len(code_counts),
            "regions_n": len(region_counts), "regions_total": region_total(),
            "zones": rows(zone_counts, ZONES, order=sorted(ZONES)),
            "regions": rows(region_counts, order=sorted(region_counts)),
            "top": [{**r, "label": place(r["value"])} for r in rows(code_counts)[:TOP]],
            "singles": sum(1 for v in code_counts.values() if v == 1),
        }
