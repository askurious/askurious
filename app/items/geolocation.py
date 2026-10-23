"""Location (Standort): participants share the position of their device through the browser (W3C Geolocation API).

Nothing happens on its own: the page asks for the position only when the person taps "Standort übermitteln", the
browser then asks for permission itself, and the person may as well say no ("Ich möchte meinen Standort nicht
angeben"). With the tick "korrigieren" a small map shows the point found and the radius of its accuracy, and the
point can be dragged to the right place. Every way of answering is stored ("way"), so a refusal is an answer too.

The researcher picks what is stored (config "store"): the exact position, rounded to about 100 m or 1 km (default:
privacy by default, UI/UX audit 2026-10-05, V40; the exact position is a deliberate choice), only the postal code and federal state, or only the federal state the point lies in. The
federal state is found on this server in the outlines of the element Bundesland (app/data/states_de.json.gz, BKG
VG2500), the postal code in the areas of the postal codes (app/data/plz_areas, OpenStreetMap); no request to anybody. Who does not
share the position gets an empty answer, or (config "fallback": "ip") country and federal state estimated from the
IP address like app/geoip.py, stored without coordinates and marked as such ("src": "ip").

The browser may only ask on pages that carry the element (Permissions-Policy, see allow() and create_app). The
consent names the element and what it stores (items/consent.py "location"); the retention period "personal"
removes exact and 100 m coordinates and keeps postal code and federal state (coarsen(), app/retention.py).

  answer = {"way": "device" | "corrected" | "refused" | "denied" | "unavailable" | "timeout",
            "lat": float, "lon": float, "acc": int metres, "state": "5" | "0" (not in Germany) | None,
            "plz": "52062" (not with "state"),
            "src": "pos" | "ip"}   (None: not answered; coordinates only where shared and stored)
Export: <code>_way, <code>_lat, <code>_lon (rounded levels), <code>_acc, <code>_state, <code>_state_src, <code>_plz."""
import functools
import gzip
import heapq
import json
import math
import random

from flask import current_app, g, has_request_context, request

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import AnswerError, ItemType, in_lang, load_json, r_flag, r_one
from app.items.map_marker import DEFAULT_CENTER, DEFAULT_ZOOM, OSM_ATTRIBUTION, OSM_TILES, valid_position
from app.lang import Both, de_en, english
from app.ui_i18n import tr

STORES = {"exact": "genaue Position", "100m": "auf etwa 100 m gerundet", "1km": "auf etwa 1 km gerundet",
          "plz": "nur Postleitzahl und Bundesland", "state": "nur das Bundesland"}
DIGITS = {"exact": 6, "100m": 3, "1km": 2}  # decimals of a degree: 0.001° of latitude is about 111 m
PERSONAL_STORES = ("exact", "100m")  # positions that can point to a house: they go with the period "personal"
FALLBACKS = {"none": "Antwort bleibt leer", "ip": "Land und Bundesland aus der IP-Adresse schätzen"}
WAYS = {"device": 1, "corrected": 2, "refused": 3, "denied": 4, "unavailable": 5, "timeout": 6}
WAY_LABELS = {1: "Position des Geräts", 2: "Position des Geräts, von Hand korrigiert", 3: "selbst nicht angegeben",
              4: "vom Browser abgelehnt", 5: "nicht verfügbar", 6: "Zeitlimit überschritten"}
SHARED = ("device", "corrected")
SOURCES = {"pos": 1, "ip": 2}
SOURCE_LABELS = {1: "aus der Position", 2: "aus der IP-Adresse geschätzt"}
# the same in English, for researcher pages in English (UI audit 2026-10-12, U52)
WAY_LABELS_EN = {1: "position of the device", 2: "position of the device, corrected by hand", 3: "chose not to say",
                 4: "refused by the browser", 5: "not available", 6: "time limit exceeded"}
SOURCE_LABELS_EN = {1: "from the position", 2: "estimated from the IP address"}
MAX_ACCURACY = 1_000_000  # metres; a browser that reports more says nothing useful
TIMEOUT_MS = 30_000       # GPS inside a building takes a while (concept O6)
MAX_RESULT_POINTS = 20000
RESULT_VIEWS = {"heat": "Heatmap", "markers": "Alle Positionen"}
# the editor's and the results page's labels in English (app/lang.py)
STORES_EN = {"exact": "exact position", "100m": "rounded to about 100 m", "1km": "rounded to about 1 km",
             "plz": "only postal code and federal state", "state": "only the federal state"}
FALLBACKS_EN = {"none": "answer stays empty", "ip": "estimate country and federal state from the IP address"}
RESULT_VIEWS_EN = {"heat": "Heatmap", "markers": "All positions"}
W3C = "W3C. (2024). Geolocation (W3C Recommendation). https://www.w3.org/TR/geolocation/"
ZANDBERGEN = ("Zandbergen, P. A. (2009). Accuracy of iPhone locations: A comparison of assisted GPS, WiFi and cellular "
              "positioning. Transactions in GIS, 13(s1), 5–25. https://doi.org/10.1111/j.1467-9671.2009.01152.x")
KEUSCH = ("Keusch, F., Struminskaya, B., Antoun, C., Couper, M. P., & Kreuter, F. (2019). Willingness to participate in "
          "passive mobile data collection. Public Opinion Quarterly, 83(S1), 210–235. https://doi.org/10.1093/poq/nfz007")
STRUMINSKAYA = ("Struminskaya, B., Lugtig, P., Toepoel, V., Schouten, B., Giesen, D., & Dolmans, R. (2021). Sharing data "
                "collected with smartphone sensors: Willingness, participation, and nonparticipation bias. Public Opinion "
                "Quarterly, 85(S1), 423–462. https://doi.org/10.1093/poq/nfab025")
DE_MONTJOYE = ("de Montjoye, Y.-A., Hidalgo, C. A., Verleysen, M., & Blondel, V. D. (2013). Unique in the crowd: The "
               "privacy bounds of human mobility. Scientific Reports, 3, 1376. https://doi.org/10.1038/srep01376")


def _float(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


# --- the federal state of a point (outlines of the element Bundesland) ---------------------------------------------

@functools.cache
def _rings():
    """[(state code, (west, south, east, north), [(lon, lat), ...])] of every ring of the 16 states; [] while the
    outline file is missing."""
    from app.items.federal_state import areas

    topo = areas()
    if not topo:
        return []
    out = []
    for code, geom in topo["areas"].items():
        for poly in geom["arcs"]:
            for ids in poly:
                x = y = 0
                pts = []
                for i in ids:
                    for dx, dy in topo["arcs"][i]:
                        x, y = x + dx, y + dy
                        pts.append((x / 1e5, y / 1e5))
                if len(pts) > 2:
                    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
                    out.append((code, (min(lons), min(lats), max(lons), max(lats)), pts))
    return out


def _inside(lon, lat, pts):
    """Ray casting (even-odd): True if the point lies inside the ring."""
    hit, j = False, len(pts) - 1
    for i in range(len(pts)):
        (xi, yi), (xj, yj) = pts[i], pts[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def state_of(lat, lon):
    """The state key of a point ("5" for Nordrhein-Westfalen, the codes of the element Bundesland), "0" outside
    Germany, None without the outline file. Rings of one state count even-odd, so the hole Berlin leaves in
    Brandenburg stays Berlin. The outlines are generalised (1 : 2 500 000): a point a few hundred metres from a
    border can land in the neighbour."""
    rings = _rings()
    if not rings:
        return None
    hits = {}
    for code, (w, s, e, n), pts in rings:
        if w <= lon <= e and s <= lat <= n and _inside(lon, lat, pts):
            hits[code] = not hits.get(code, False)
    inside = [code for code, on in hits.items() if on]
    return inside[0] if inside else "0"


# --- the postal code of a point (areas of the postal codes, OpenStreetMap) -------------------------------------------

NEAR = 12  # postal codes whose centre is nearest: their regions are searched for the area holding the point


@functools.lru_cache(maxsize=4)
def _plz_rings(prefix):
    """[(plz, (west, south, east, north), [(lon, lat), ...])] of the rings of one postal region (first two digits);
    [] without its file. A few regions at a time: all of them take far too much memory (postal_code.region_file)."""
    from app.items import postal_code

    path = postal_code.region_file(prefix)
    if path is None:
        return []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        topo = json.load(fh)
    arcs = {}

    def arc(i):
        if i not in arcs:
            x = y = 0
            pts = []
            for dx, dy in topo["arcs"][i]:
                x, y = x + dx, y + dy
                pts.append((x / 1e5, y / 1e5))
            arcs[i] = pts
        return arcs[i]

    out = []
    for plz, geom in topo["areas"].items():
        for poly in (geom["arcs"] if geom["type"] == "MultiPolygon" else [geom["arcs"]]):
            for ids in poly:
                pts = []
                for n, i in enumerate(ids):
                    part = arc(~i)[::-1] if i < 0 else arc(i)
                    pts += part[1:] if n else part  # consecutive arcs share their end point
                if len(pts) > 2:
                    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
                    out.append((plz, (min(lons), min(lats), max(lons), max(lats)), pts))
    return out


def plz_of(lat, lon):
    """The German postal code whose area holds the point, or None (outside Germany, or no area found). The regions
    of the nearest postal code centres (GeoNames) are searched, so only one or two files are read."""
    from app.items import postal_code

    squeeze = math.cos(math.radians(lat))
    near = heapq.nsmallest(NEAR, postal_code.table().items(),
                           key=lambda kv: (kv[1][1] - lat) ** 2 + ((kv[1][2] - lon) * squeeze) ** 2)
    for prefix in dict.fromkeys(plz[:2] for plz, _ in near):
        hits = {}
        for plz, (w, s, e, n), pts in _plz_rings(prefix):
            if w <= lon <= e and s <= lat <= n and _inside(lon, lat, pts):
                hits[plz] = not hits.get(plz, False)
        inside = [plz for plz, on in hits.items() if on]
        if inside:
            return inside[0]
    return None


# --- pages that may ask for the position --------------------------------------------------------------------------

def used(survey):
    return survey is not None and any(i.type == "geolocation" for b in survey.blocks for i in b.items)


def allow():
    """This page may ask for the position (Permissions-Policy in create_app; every other page may not)."""
    if has_request_context():
        g.geolocation = True


def items_of(survey):
    return [i for b in survey.blocks for i in b.items if i.type == "geolocation"]


def store(item):
    value = (item.config or {}).get("store")
    return value if value in STORES else "exact"


def fallback(item):
    value = (item.config or {}).get("fallback")
    return value if value in FALLBACKS else "none"


def ip_fallback(survey):
    """True if a location element of the survey estimates country and state from the IP address on a refusal."""
    return any(fallback(i) == "ip" for i in items_of(survey))


def finest(survey):
    """The finest storage level among the survey's location elements (for the consent), or None without one."""
    stored = {store(i) for i in items_of(survey)}
    return next((s for s in STORES if s in stored), None)


def personal_items(survey):
    """The location elements whose coordinates go with the retention period "personal"."""
    return [i for i in items_of(survey) if store(i) in PERSONAL_STORES]


def position_columns(survey):
    """The export columns that hold coordinates of the period "personal" (blanked in the saved data files)."""
    return {f"{i.code}_{axis}" for i in personal_items(survey) for axis in ("lat", "lon")}


def coarsen(survey):
    """The period "personal" is over: exact and 100 m coordinates leave the stored answers, postal code, state and
    the accuracy stay. Written straight into the table, with the row's version raised: a request that loaded the
    participation before cannot write the exact position back (audit 2026-10-21, B5). Returns the number of
    participations changed."""
    from app.extensions import db
    from app.models import Response

    keys = {str(i.id) for i in personal_items(survey)}
    if not keys:
        return 0
    table, n = Response.__table__, 0
    for rid, answers in db.session.execute(db.select(Response.id, Response.answers).filter(Response.survey_id == survey.id)).all():
        changed = False
        new = dict(answers or {})
        for key, value in (answers or {}).items():
            if key.split("#")[0] in keys and isinstance(value, dict) and ("lat" in value or "lon" in value):
                new[key] = {k: v for k, v in value.items() if k not in ("lat", "lon")}
                changed = True
        if changed:
            db.session.execute(db.update(table).where(table.c.id == rid)
                               .values(answers=new, version=table.c.version + 1))
            n += 1
    return n


def _cut(value, level):
    """A stored answer brought to the storage level `level`: coordinates rounded to its decimals, or gone with the
    postal code where the level keeps neither."""
    if not isinstance(value, dict):
        return value
    new = dict(value)
    for axis in ("lat", "lon"):
        if axis in new:
            v = _float(new[axis])
            if level in DIGITS and v is not None:
                new[axis] = round(v, DIGITS[level])
            else:
                new.pop(axis)
    if level == "state":
        new.pop("plz", None)
    return new


def coarsen_item(item, level):
    """The storage level of `item` became coarser while answers exist: bring every stored answer to the new level at
    once, so nothing finer stays than the element now names (and than the period "personal" and the export look
    for; audit 2026-10-10, N-1). Written straight into the table like coarsen(), with the version raised (B5).
    Returns the participations changed."""
    from app.extensions import db
    from app.models import Response

    key, table, n = str(item.id), Response.__table__, 0
    for rid, answers in db.session.execute(db.select(Response.id, Response.answers).filter(
            Response.survey_id == item.survey.id)).all():
        new = dict(answers or {})
        for k, value in (answers or {}).items():
            if k.split("#")[0] == key and isinstance(value, dict):
                new[k] = _cut(value, level)
        if new != (answers or {}):
            db.session.execute(db.update(table).where(table.c.id == rid)
                               .values(answers=new, version=table.c.version + 1))
            n += 1
    if n:
        from app import seal

        db.session.flush()
        seal.amend_changed(item.survey, "geo_coarsened")  # the seal chain records the changed answers (app/seal.py)
    return n


def held(survey):
    """How many participations still hold coordinates of the period "personal" (Export page)."""
    from app.extensions import db
    from app.models import Response

    keys = {str(i.id) for i in personal_items(survey)}
    if not keys:
        return 0
    return sum(1 for (answers,) in db.session.execute(db.select(Response.answers).filter(Response.survey_id == survey.id))
               if any(k.split("#")[0] in keys and isinstance(v, dict) and "lat" in v for k, v in (answers or {}).items()))


@register
class Geolocation(ItemType):
    key = "geolocation"
    label = Both("Standort", "Location")
    short = Both("Position des Geräts erfassen", "Record the device's position")
    description = Both("Die Position des Geräts erfassen, nach Freigabe durch die teilnehmende Person (GPS, WLAN)",
                       "Record the position of the device once the participant allows it (GPS, Wi-Fi)")
    order = 66
    fact_sheet = FactSheet(
        "spatial",
        keywords=["GPS", "Standort", "Location", "Position", "Geo", "Koordinaten", "Coordinates"],
        can=[("Genau, auf etwa 100 m oder auf etwa 1 km gerundet.", "Exact, rounded to about 100 m or to about 1 km."),
             ("Verweigert jemand die Freigabe, bleibt die Antwort leer oder wird aus der IP-Adresse geschätzt.",
              "If someone refuses to share it, the answer stays empty or is estimated from the IP address.")],
        good_for=[("Wo eine Befragung ausgefüllt wird, z. B. vor Ort an einem Ereignis",
                   "Where a survey is filled in, e.g. on site at an event")],
        not_for=[("Orte, die Teilnehmende selbst wählen (dann Karte mit Markern)",
                  "Places that participants choose themselves (then map with markers)"),
                 ("Wohnort (dann Postleitzahl oder Bundesland)",
                  "Place of residence (then postal code or federal state)")],
        designs=["cross"],
        examples=[("Befragung auf einem Festival: In welchem Bereich wurde sie ausgefüllt?",
                   "Survey at a festival: in which area was it filled in?")],
    )
    result_kind = "auto"
    # the lists of the editor and the results page, labelled in the language of the page
    @property
    def result_views(self):
        return RESULT_VIEWS_EN if english() else RESULT_VIEWS

    @property
    def stores(self):
        return STORES_EN if english() else STORES

    @property
    def fallbacks(self):
        return FALLBACKS_EN if english() else FALLBACKS

    allowed_in_loop = False       # one position per person; a loop would ask the browser again and again
    allowed_in_separated = False  # the position belongs to the answers, not to the details kept apart
    import_rules = {"store": r_one(*STORES), "fallback": r_one(*FALLBACKS), "correct": r_flag}

    def default_config(self):
        return {"store": "1km", "fallback": "none", "correct": True}

    def default_texts(self, lang):
        # the purpose belongs to the request: it decides whether people share (Keusch et al., 2019; V39)
        return {"question": tr(lang, "Bitte übermitteln Sie Ihren Standort. Wir brauchen ihn, um die Antworten nach Regionen auszuwerten.",
                               "Please share your location. We need it to analyse the answers by region.")}

    def stored_note(self, item, lang):
        """What is kept of the position, in the participant's words, right under the request (V39)."""
        level = store(item)
        if level == "exact":
            return tr(lang, "Gespeichert wird die genaue Position.", "The exact position is stored.")
        if level == "100m":
            return tr(lang, "Gespeichert wird nur: die Position, auf etwa 100 m gerundet.", "Only stored: the position, rounded to about 100 m.")
        if level == "1km":
            return tr(lang, "Gespeichert wird nur: die Position, auf etwa 1 km gerundet.", "Only stored: the position, rounded to about 1 km.")
        if level == "plz":
            return tr(lang, "Gespeichert werden nur Postleitzahl und Bundesland, keine Koordinaten.",
                      "Only the postal code and the federal state are stored, no coordinates.")
        return tr(lang, "Gespeichert wird nur das Bundesland, keine Koordinaten.", "Only the federal state is stored, no coordinates.")

    # what a question of its own must name to say what is stored itself (stored_said): a word for keeping and the level
    STORE_WORDS = ("gespeichert", "speichern", "stored", "store ", "saved", "kept")
    LEVEL_WORDS = {"exact": ("genaue position", "genauen standort", "exact position", "exact location"),
                   "100m": ("100 m",), "1km": ("1 km",),
                   "plz": ("postleitzahl", "plz", "postal code", "postcode", "zip code"),
                   "state": ("bundesland", "federal state")}

    def stored_said(self, item, lang):
        """Whether the question already says what is stored ("Gespeichert werden nur Postleitzahl und Bundesland."):
        then the note under it would only repeat it (UI audit 2026-10-12, U46). In the languages without these words: the
        note's own sentence (up to its comma) in the question."""
        from app.items.base import plain

        question = plain(item.text("question", lang) or "").lower()
        if not question:
            return False
        if any(w in question for w in self.STORE_WORDS) and any(
                w in question for w in self.LEVEL_WORDS.get(store(item), ())):
            return True
        own = self.stored_note(item, lang).lower().split(",")[0].rstrip(".")
        return bool(own) and own in question

    def correct(self, item):
        return bool((item.config or {}).get("correct", True))

    def has_positions(self, item):
        """True if the element keeps coordinates (exact or rounded), so a heatmap can be drawn."""
        return store(item) in DIGITS

    # --- editor ---------------------------------------------------------------------------------------------------

    def read_shape(self, item):
        """The storage level as its place in STORES (coarser is larger): once real answers exist only coarser, as in
        a running survey; finer would let the codebook name more decimals than earlier answers have (audit
        2026-10-12, N-13)."""
        return {"store": list(STORES).index(store(item))}

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        config = dict(item.config or {})
        if structural:
            config["fallback"] = form.get("fallback") if form.get("fallback") in FALLBACKS else fallback(item)
        if lang == item.survey.default_language:  # the fields of the default language (a translation shows them off)
            chosen, before = form.get("store"), store(item)
            if chosen in STORES and (structural or list(STORES).index(chosen) >= list(STORES).index(before)):
                config["store"] = chosen  # once the survey runs only coarser: the consent named the level
                if list(STORES).index(chosen) > list(STORES).index(before) and item.id:
                    coarsen_item(item, chosen)  # what is stored already follows at once (audit 2026-10-10, N-1)
            config["correct"] = bool(form.get("correct"))  # presentation only
        item.config = config

    def validate(self, item):
        if item.survey.consent_item is None:
            return [de_en("Standort: Die Befragung braucht eine Einwilligung am Anfang; sie nennt, was vom Standort "
                          "gespeichert wird.",
                          "Location: the survey needs a consent at the start; it names what is stored of the location.")]
        return []

    def source_groups(self, item):
        return [(de_en("Genauigkeit der Ortung im Browser", "Accuracy of locating in the browser"), [W3C, ZANDBERGEN]),
                (de_en("Bereitschaft, den Standort zu teilen", "Willingness to share the location"), [KEUSCH, STRUMINSKAYA]),
                (de_en("Genaue Orte machen Personen erkennbar", "Exact places make people identifiable"), [DE_MONTJOYE])]

    # --- participant side -----------------------------------------------------------------------------------------

    def settings(self, item):
        """What the page's script needs, as one JSON object for data-geo."""
        cfg = current_app.config
        return {"correct": self.correct(item), "timeout": TIMEOUT_MS, "center": list(DEFAULT_CENTER), "zoom": DEFAULT_ZOOM,
                "tiles": cfg.get("MAP_TILE_URL", OSM_TILES), "attribution": cfg.get("MAP_TILE_ATTRIBUTION", OSM_ATTRIBUTION)}

    def _read(self, raw):
        data = load_json(raw or "", None)
        return data if isinstance(data, dict) else None

    uses_answers = True  # the stored answer, for a page shown again without coordinates (parse_answer)

    def parse_answer(self, item, form, lang="de", meta=None, answers=None):
        data = self._read(form.get(f"i{item.id}"))
        way = (data or {}).get("way")
        if way not in WAYS:
            if item.required:
                raise AnswerError(tr(lang, "Bitte übermitteln Sie Ihren Standort oder wählen Sie, dass Sie ihn nicht angeben möchten.",
                                     "Please share your location or choose not to give it."))
            return None
        if way in SHARED:
            lat, lon, acc = _float(data.get("lat")), _float(data.get("lon")), _float(data.get("acc"))
            if store(item) not in DIGITS and lat is None and lon is None:
                # the stored answer again (the page shown once more, "Zurück"): it holds no coordinates to look at.
                # The server keeps it, so state and postal code come from there, never from the form (audit
                # 2026-10-10, N-7); without one the form's values are not taken
                kept = (answers or {}).get(item.id, (answers or {}).get(str(item.id)))
                if isinstance(kept, dict) and kept.get("way") in SHARED and kept.get("src") == "pos":
                    return kept
            if not valid_position(lat, lon):
                raise AnswerError(tr(lang, "Der Standort konnte nicht gelesen werden. Bitte versuchen Sie es noch einmal.",
                                     "The location could not be read. Please try again."))
            answer = {"way": way, "state": state_of(lat, lon), "src": "pos"}
            if store(item) != "state" and answer["state"] not in (None, "0"):
                plz = plz_of(lat, lon)
                if plz:
                    answer["plz"] = plz
            if acc is not None and 0 <= acc <= MAX_ACCURACY:
                answer["acc"] = int(round(acc))
            level = store(item)
            if level in DIGITS:
                answer["lat"], answer["lon"] = round(lat, DIGITS[level]), round(lon, DIGITS[level])
            return answer
        answer = {"way": way}
        if fallback(item) == "ip":
            country, state = self._ip_region(item)
            if country:
                answer.update(state=(state or None) if country == "DE" else "0", src="ip")
        return answer

    @staticmethod
    def _plz(value):
        from app.items import postal_code

        return isinstance(value, str) and postal_code.PLZ_RE.match(value) is not None and postal_code.known(value)

    @staticmethod
    def _state(value):
        from app.items.federal_state import STATE_CODES

        return value if value == "0" or value in STATE_CODES else None

    def _ip_region(self, item):
        """(country, state) of the request's address (app/geoip.py); ("", "") outside a request or for a local one."""
        if not has_request_context():
            return "", ""
        from app import geoip

        return geoip.lookup(request.remote_addr)

    def display_value(self, item, form, meta=None):
        return self._read(form.get(f"i{item.id}"))

    def is_missing(self, item, answer):
        return not (isinstance(answer, dict) and answer.get("way") in WAYS)  # a refusal is an answer

    def pipeable(self, item):
        return False

    # --- export ---------------------------------------------------------------------------------------------------

    def open_columns(self, item):
        # coordinates at any level, the postal code
        return [c for c, _ in self.export_columns(item) if c.endswith(("_lat", "_lon", "_plz"))]

    def export_columns(self, item):
        code = item.code
        cols = [(f"{code}_way", de_en("Standort: Weg der Antwort", "Location: way of the answer"))]
        if store(item) in DIGITS:
            cols += [(f"{code}_lat", de_en("Standort: Breitengrad (WGS84)", "Location: latitude (WGS84)")),
                     (f"{code}_lon", de_en("Standort: Längengrad (WGS84)", "Location: longitude (WGS84)"))]
        cols += [(f"{code}_acc", de_en("Standort: Genauigkeit der Ortung (Radius in m, 95 %)",
                                       "Location: accuracy of the fix (radius in m, 95%)")),
                 (f"{code}_state", de_en("Standort: Bundesland", "Location: federal state")),
                 (f"{code}_state_src", de_en("Standort: Herkunft des Bundeslands", "Location: source of the federal state"))]
        if store(item) != "state":
            cols.append((f"{code}_plz", de_en("Standort: Postleitzahl", "Location: postal code")))
        return cols

    def export_values(self, item, answer):
        code, a = item.code, answer if isinstance(answer, dict) else {}
        out = {f"{code}_way": WAYS.get(a.get("way")), f"{code}_acc": a.get("acc"),
               f"{code}_state": int(a["state"]) if str(a.get("state") or "").isdigit() else None,
               f"{code}_state_src": SOURCES.get(a.get("src")) if a.get("state") else None}
        level = store(item)
        if level in DIGITS:  # never more decimals than the codebook names (audit 2026-10-10, N-1)
            lat, lon = _float(a.get("lat")), _float(a.get("lon"))
            out[f"{code}_lat"] = round(lat, DIGITS[level]) if lat is not None else None
            out[f"{code}_lon"] = round(lon, DIGITS[level]) if lon is not None else None
        if store(item) != "state":
            out[f"{code}_plz"] = a.get("plz")
        return out

    def codebook_info(self, item, column, lang, ui=None):
        code = item.code
        if column == f"{code}_way":
            return "Auswahl", "; ".join(f"{k} = {v}" for k, v in WAY_LABELS.items()), [
                in_lang(ui, "1 und 2: Position geteilt; 3: „nicht angeben“ gewählt; 4 bis 6: Der Browser lieferte keine Position",
                        "1 and 2: position shared; 3: “do not say” chosen; 4 to 6: the browser gave no position"),
                in_lang(ui, "leer: nicht beantwortet", "empty: not answered")]
        if column in (f"{code}_lat", f"{code}_lon"):
            digits = DIGITS.get(store(item), 6)
            notes = [in_lang(ui, "Grad (WGS84, {digits} Nachkommastellen; {store})", "degrees (WGS84, {digits} decimal places; {store})",
                             digits=digits, store=in_lang(ui, STORES[store(item)], STORES_EN[store(item)])),
                     in_lang(ui, "leer, wenn keine Position geteilt wurde", "empty if no position was shared")]
            if store(item) in PERSONAL_STORES:
                notes.append(in_lang(ui, "wird mit der Frist für personenbezogene Angaben gelöscht; Postleitzahl und Bundesland bleiben",
                                     "deleted with the period for personal data; postal code and federal state remain"))
            if column.endswith("_lat"):
                return "Zahl", in_lang(ui, "-90 bis 90", "-90 to 90"), notes
            return "Zahl", in_lang(ui, "-180 bis 180", "-180 to 180"), notes
        if column == f"{code}_acc":
            return "Ganzzahl", in_lang(ui, "Meter", "metres"), [
                in_lang(ui, "Radius um die gemeldete Position, in dem die wahre Position mit 95 % Wahrscheinlichkeit liegt "
                            "(Angabe des Geräts, W3C Geolocation)",
                        "radius around the reported position within which the true position lies with 95 % probability "
                        "(given by the device, W3C Geolocation)"),
                in_lang(ui, "bei 2 (korrigiert): Radius der ursprünglichen Ortung", "at 2 (corrected): radius of the original location")]
        if column == f"{code}_state":
            from app.items.federal_state import STATES

            return "Auswahl", "0 = nicht in Deutschland; " + "; ".join(f"{c} = {de}" for c, de, _ in sorted(STATES, key=lambda s: int(s[0]))), [
                in_lang(ui, "Schlüssel des Amtlichen Gemeindeschlüssels wie beim Element Bundesland",
                        "key of the official municipality key (AGS) as with the element federal state"),
                in_lang(ui, "aus der Position: Umrisse BKG VG2500 (generalisiert, nahe einer Grenze unsicher)",
                        "from the position: outlines BKG VG2500 (generalised, uncertain near a border)")]
        if column == f"{code}_state_src":
            return "Auswahl", "; ".join(f"{k} = {v}" for k, v in SOURCE_LABELS.items()), [
                in_lang(ui, "2: Ersatz bei Ablehnung, Schätzung aus der IP-Adresse (DB-IP Lite), deutlich ungenauer",
                        "2: fallback on refusal, estimate from the IP address (DB-IP Lite), much less precise")]
        if column == f"{code}_plz":
            return "Text", in_lang(ui, "fünfstellig", "five digits"), [
                in_lang(ui, "Postleitzahl, in deren Gebiet die Position liegt (Gebiete aus OpenStreetMap, auf dem eigenen "
                            "Server ermittelt)",
                        "postal code whose area the position lies in (areas from OpenStreetMap, found on our own server)"),
                in_lang(ui, "leer außerhalb Deutschlands und ohne geteilte Position", "empty outside Germany and without a shared position"),
                in_lang(ui, "bleibt nach Ablauf der Frist für personenbezogene Angaben erhalten",
                        "kept after the period for personal data ends")]
        return None

    def result_column(self, item, column):
        code = item.code
        if column in (f"{code}_lat", f"{code}_lon", f"{code}_plz"):
            return None, "", None  # on the map, see results(); single postal codes stay in the export
        # the keys as text, as results.frequencies counts the values (code review 2026-10-07, R2)
        if column == f"{code}_way":
            return "categorical", "", {str(k): de_en(v, WAY_LABELS_EN[k]) for k, v in WAY_LABELS.items()}
        if column == f"{code}_state":
            from app.items.federal_state import STATES

            return "categorical", "", {"0": de_en("nicht in Deutschland", "not in Germany"), **{str(int(c)): de for c, de, _ in STATES}}
        if column == f"{code}_state_src":
            return "categorical", "", {str(k): de_en(v, SOURCE_LABELS_EN[k]) for k, v in SOURCE_LABELS.items()}
        return "auto", "m", None

    def column_level(self, item, column):
        return "metric" if column.endswith(("_lat", "_lon", "_acc")) and not column.endswith("_plz") else "nominal"

    def results(self, item, table, passes=(), lang=None):
        """The shared positions of the rows in `table` for the map of the results page (surveys/_map_results.html),
        in the shape of the map with markers: one point per person."""
        code = item.code
        points = []
        for row in table:
            lat, lon = _float(row.get(f"{code}_lat")), _float(row.get(f"{code}_lon"))
            if valid_position(lat, lon):
                points.append({"lat": lat, "lon": lon, "p": row.get("resp_id"), "k": 1, "w": 1, "a": []})
        n = len(points)
        if n > MAX_RESULT_POINTS:
            keep = sorted(random.Random(item.id).sample(range(n), MAX_RESULT_POINTS))
            points = [points[i] for i in keep]
        settings = {"center": list(DEFAULT_CENTER), "zoom": DEFAULT_ZOOM, "shape": "circle", "color": "#d62828",
                    "tiles": current_app.config.get("MAP_TILE_URL", OSM_TILES),
                    "attribution": current_app.config.get("MAP_TILE_ATTRIBUTION", OSM_ATTRIBUTION), "view": "heat"}
        return {"points": points, "n": n, "persons": n, "truncated": n > len(points), "mean": 1.0 if n else None,
                "summary": [], "settings": settings}

    # --- duration, dummy data -------------------------------------------------------------------------------------

    def seconds(self, item, read):
        return read + 15  # tap, the browser's question, waiting for the fix

    def dummy_fields(self, item, rng, meta, answers=None):
        """Mostly shared positions around a few German cities, some refusals and failures."""
        roll = rng.random()
        if roll < 0.7:
            lat, lon = rng.choice([(50.776, 6.084), (52.52, 13.405), (48.137, 11.575), (53.551, 9.993), (50.938, 6.96)])
            data = {"way": "device" if rng.random() < 0.85 else "corrected", "lat": lat + rng.gauss(0, 0.05),
                    "lon": lon + rng.gauss(0, 0.08), "acc": round(rng.choice([8, 15, 30, 65, 150, 900]) * rng.uniform(0.7, 1.4))}
        else:
            data = {"way": rng.choice(["refused", "refused", "denied", "unavailable", "timeout"])}
        return {f"i{item.id}": json.dumps(data)}

    def questionnaire_lines(self, item, lang):
        return [f"*Standort des Geräts nach Freigabe; gespeichert: {STORES[store(item)]}"
                + ("; bei Ablehnung Land und Bundesland aus der IP-Adresse" if fallback(item) == "ip" else "") + "*"]



def comparison(survey, sources=("real",), include_incomplete=False):
    """For the page "Qualitätssicherung": the state of each shared location against the state the same person names
    in a postal code question or the element Bundesland, over the participations of the data pool:
    [{"code", "against", "what", "n", "same", "other", "abroad"}] (n: both given, the location in Germany or
    abroad). None without a location element or without such a question. It describes the sample and marks
    nobody: people answer from elsewhere (work, holidays), and the postal code may be an old one."""
    from app import export, flow
    from app.items import postal_code
    from app.items.choice import selected
    from app.items.federal_state import STATE_CODES

    in_loop = flow.loop_blocks(survey)
    items = [i for b in survey.blocks if b.id not in in_loop for i in b.items]
    locations = [i for i in items if i.type == "geolocation"]
    named = [i for i in items if i.type in ("postal_code", "federal_state")]
    if not locations or not named:
        return None
    responses = export._responses(survey, include_incomplete, sources)
    rows = []
    for loc in locations:
        for other in named:
            row = {"code": loc.code, "against": other.code,
                   "what": "Postleitzahl" if other.type == "postal_code" else "Bundesland",
                   "n": 0, "same": 0, "other": 0, "abroad": 0}
            for r in responses:
                answers = r.answers or {}
                here = answers.get(str(loc.id))
                state = here.get("state") if isinstance(here, dict) and here.get("src") == "pos" else None
                said = answers.get(str(other.id))
                said = postal_code.state(said) if other.type == "postal_code" else selected(said)
                if state is None or str(said) not in STATE_CODES:
                    continue
                row["n"] += 1
                row["abroad" if state == "0" else "same" if state == str(said) else "other"] += 1
            rows.append(row)
    return rows
