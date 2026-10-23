"""Map with markers: participants place their own markers on an OpenStreetMap map (e.g. preferred
sites for infrastructure). Markers can be dragged and removed; a search box moves the map to a place.

The researcher sets how many markers each participant may place (min/max), the start view
(centre and zoom), shape and colour of the markers, and optionally a few follow-up questions per
marker (short free text, a rating scale or a single choice). Leaflet is bundled in static/vendor (no CDN); tiles and
the place search come from OpenStreetMap / Nominatim unless MAP_TILE_URL / MAP_SEARCH_URL point
elsewhere. The search only runs on an explicit submit, at most once per second (Nominatim usage policy).

Stored answer: [{"lat": .., "lon": .., "q": {question id: value}}, ...] in the order placed; a scale and a
single choice store a number (the point, the place of the option).
Export (wide, one row per participant): <code>_n, then per marker slot 1..max <code>_<k>_lat,
<code>_<k>_lon and <code>_<k>_<question code>, plus <code>_json with all markers. Long format (data page, one
row per marker): long_columns and long_rows."""
import json
import math
import random
import re
import statistics

from flask import current_app

from app import units
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en, english
from app.request_values import clean_text
from app.items.base import r_colour, r_flag, r_id, r_int, r_list, r_maybe, r_num, r_one, r_rows, r_text  # rules for survey files, see base.py
from app.items.base import CODE_RE, AnswerError, ItemType, in_lang, load_json, prepared, whole, read_cfg
from app.items.base import form_ids, new_id as _new_id
from app.ui_i18n import tr

SHAPES = {"pin": "Stecknadel", "circle": "Kreis", "square": "Quadrat", "diamond": "Raute",
          "triangle": "Dreieck", "star": "Stern"}
QUESTION_KINDS = {"text": "Freitext (kurz)", "scale": "Skala", "choice": "Einfachauswahl"}
RESULT_VIEWS = {"heat": "Heatmap", "markers": "Alle Marker"}
# the same lists for the English editor and results page (properties of MapMarker)
SHAPES_EN = {"pin": "Pin", "circle": "Circle", "square": "Square", "diamond": "Diamond", "triangle": "Triangle", "star": "Star"}
QUESTION_KINDS_EN = {"text": "Free text (short)", "scale": "Scale", "choice": "Single choice"}
RESULT_VIEWS_EN = {"heat": "Heatmap", "markers": "All markers"}
MAX_RESULT_POINTS = 20000  # markers drawn on the results page
DEFAULT_COLOR = "#d62828"
DEFAULT_CENTER = [51.1657, 10.4515]  # Germany
DEFAULT_ZOOM = 6
MAX_SLOTS = 20       # markers per participant; also the number of export slots
MAX_QUESTIONS = 5    # follow-up questions per marker
MAX_TEXT = 500       # characters of a free-text answer
MAX_OPTIONS = 20     # options of a single choice per marker
MAX_OPTION = 100     # characters of one option
SCALE_POINTS = range(2, 12)
HEX = re.compile(r"^#[0-9a-fA-F]{6}\Z")

OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_ATTRIBUTION = ('&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">'
                   'OpenStreetMap contributors</a>')  # as the OSM copyright page asks (copyright audit 2026-10-16, U17)
NOMINATIM = "https://nominatim.openstreetmap.org/search"


def _float(value):
    try:
        v = float(str(value).replace(",", ".").replace("−", "-"))
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None  # no NaN / inf


def _int(value, lo, hi, default):
    v = str(value or "").strip()
    return min(max(int(v), lo), hi) if whole(v[1:] if v.startswith("-") else v) is not None else default


def _option_lines(text):
    """The options of a single choice from its field: one per line, empty lines left out."""
    return [line.strip()[:MAX_OPTION] for line in str(text or "").splitlines() if line.strip()][:MAX_OPTIONS]


def valid_position(lat, lon):
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180


EARTH_RADIUS_M = 6371008.8  # mean earth radius (IUGG): R1 = 6 371 008.7714 m of GRS80 (MORITZ)
MORITZ = ("Moritz, H. (2000). Geodetic Reference System 1980. Journal of Geodesy, 74(1), 128–133. "
          "https://doi.org/10.1007/s001900050278")


def distance_m(a, b):
    """Great-circle distance ("as the crow flies") between two (lat, lon) points in metres (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


SINNOTT = "Sinnott, R. W. (1984). Virtues of the haversine. Sky and Telescope, 68(2), 159."
HELD_KARP = ("Held, M., & Karp, R. M. (1962). A dynamic programming approach to sequencing problems. Journal of the "
             "Society for Industrial and Applied Mathematics, 10(1), 196–210. https://doi.org/10.1137/0110015")
CROES = ("Croes, G. A. (1958). A method for solving traveling-salesman problems. Operations Research, 6(6), 791–812. "
         "https://doi.org/10.1287/opre.6.6.791")
PRIM = ("Prim, R. C. (1957). Shortest connection networks and some generalizations. Bell System Technical Journal, "
        "36(6), 1389–1401. https://doi.org/10.1002/j.1538-7305.1957.tb01515.x")
WASSERMAN_FAUST = ("Wasserman, S., & Faust, K. (1994). Social network analysis: Methods and applications. Cambridge "
                   "University Press. https://doi.org/10.1017/CBO9780511815478")
DENSITY_M = 500           # default threshold: two markers at most this far apart count as connected
MAX_DENSITY_M = 1_000_000
EXACT_PATH = 10           # shortest path exact (Held-Karp) up to this many markers, above: heuristic
NETWORK = ("mindist_m", "path_m", "mst_m", "density")  # export suffixes after _spread_m and _maxdist_m


def _points(markers):
    pts = [(_float(m.get("lat")), _float(m.get("lon"))) for m in markers or [] if isinstance(m, dict)]
    return [p for p in pts if valid_position(*p)]


def _matrix(pts):
    n = len(pts)
    d = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d[i][j] = d[j][i] = distance_m(pts[i], pts[j])
    return d


def spread(markers):
    """Spatial spread of one person's markers: (mean, max) straight-line distance over all pairs of
    markers in whole metres, or (None, None) with fewer than two markers."""
    pts = _points(markers)
    dists = [distance_m(pts[i], pts[j]) for i in range(len(pts)) for j in range(i + 1, len(pts))]
    if not dists:
        return None, None
    return round(sum(dists) / len(dists)), round(max(dists))


def _held_karp(d):
    """Length of the shortest open path through all points (start and end free), exact: Held & Karp (1962)."""
    n = len(d)
    full = (1 << n) - 1
    inf = float("inf")
    best = [[inf] * n for _ in range(1 << n)]  # best[set][j]: shortest path over `set` that ends in j
    for j in range(n):
        best[1 << j][j] = 0.0
    for mask in range(1, full + 1):
        row = best[mask]
        for j in range(n):
            here = row[j]
            if here == inf:
                continue
            dj = d[j]
            for k in range(n):
                if not mask >> k & 1:
                    nxt = best[mask | 1 << k]
                    if here + dj[k] < nxt[k]:
                        nxt[k] = here + dj[k]
    return min(best[full])


def _path_heuristic(d):
    """Shortest open path, approximately: nearest neighbour from every start, each improved by 2-opt
    (Croes, 1958) until no reversed section makes it shorter; the shortest of these."""
    n = len(d)
    best = None
    for start in range(n):
        path, left = [start], set(range(n)) - {start}
        while left:
            nxt = min(left, key=lambda k: d[path[-1]][k])
            path.append(nxt)
            left.remove(nxt)
        better = True
        while better:
            better = False
            for i in range(n - 1):
                for j in range(i + 1, n):
                    # reversing path[i..j] changes the edge before i and the one after j (an open path has none at its ends)
                    old = (d[path[i - 1]][path[i]] if i else 0.0) + (d[path[j]][path[j + 1]] if j < n - 1 else 0.0)
                    new = (d[path[i - 1]][path[j]] if i else 0.0) + (d[path[i]][path[j + 1]] if j < n - 1 else 0.0)
                    if new < old - 1e-9:
                        path[i:j + 1] = path[i:j + 1][::-1]
                        better = True
        total = sum(d[path[i]][path[i + 1]] for i in range(n - 1))
        best = total if best is None else min(best, total)
    return best


def shortest_path_m(d):
    return _held_karp(d) if len(d) <= EXACT_PATH else _path_heuristic(d)


def _mst(d):
    """Total length of the minimum spanning tree (Prim, 1957)."""
    n = len(d)
    inside, reach, total = {0}, list(d[0]), 0.0
    while len(inside) < n:
        k = min((j for j in range(n) if j not in inside), key=lambda j: reach[j])
        total += reach[k]
        inside.add(k)
        reach = [min(reach[j], d[k][j]) for j in range(n)]
    return total


def network(markers, threshold_m=DENSITY_M):
    """Network figures of one person's markers, straight-line: shortest distance between two markers, shortest
    open path through all, length of the minimum spanning tree (whole metres), and the density: the share of
    pairs at most `threshold_m` apart (graph density, Wasserman & Faust, 1994; 3 decimals). None with fewer than 2."""
    pts = _points(markers)
    if len(pts) < 2:
        return dict.fromkeys(NETWORK)
    d = _matrix(pts)
    pairs = [d[i][j] for i in range(len(pts)) for j in range(i + 1, len(pts))]
    return {"mindist_m": round(min(pairs)), "path_m": round(shortest_path_m(d)), "mst_m": round(_mst(d)),
            "density": round(sum(1 for x in pairs if x <= threshold_m) / len(pairs), 3)}


@register
class MapMarker(ItemType):
    key = "map_marker"
    import_rules = {"min": r_int(0, MAX_SLOTS, none=True), "max": r_int(1, MAX_SLOTS), "zoom": r_int(1, 19),
                    "center": r_list(2, r_num(-180, 180), low=2), "search": r_flag, "color": r_colour, "shape": r_one(*SHAPES),
                    "result_view": r_one(*RESULT_VIEWS), "density_m": r_int(1, MAX_DENSITY_M),
                    "questions": r_rows(MAX_QUESTIONS, id=r_id, code=r_text(64), kind=r_one(*QUESTION_KINDS),
                                        points=r_int(SCALE_POINTS[0], SCALE_POINTS[-1]), required=r_flag,
                                        options=r_maybe(r_list(MAX_OPTIONS, r_id)))}

    def import_problems(self, item):
        return [] if valid_position(*read_cfg(item)["center"]) else [de_en("Der Kartenmittelpunkt liegt nicht auf der Erde.", "The centre of the map is not on the earth.")]
    label = Both("Karte mit Markern", "Map with markers")
    short = Both("Marker auf einer Karte setzen", "Place markers on a map")
    description = Both("Auf einer OpenStreetMap-Karte eigene Marker setzen, verschieben und löschen; optional Fragen je Marker",
                       "Place, move and delete markers of one's own on an OpenStreetMap map; optional questions per marker")
    order = 64
    fact_sheet = FactSheet(
        "spatial",
        keywords=["Karte", "Map", "OpenStreetMap", "Marker", "Orte", "Places", "Geo"],
        can=[("Marker als Stecknadel, Kreis, Quadrat oder Raute.", "Markers as a pin, circle, square or diamond."),
             ("Optional je Marker eine Frage: Freitext, Skala oder Einfachauswahl.",
              "Optionally a question per marker: free text, scale or single choice."),
             ("Auswertung als Heatmap oder als alle Marker.", "Analysed as a heatmap or as all markers.")],
        good_for=[("Orte, die Menschen mit etwas verbinden: Angsträume, Lieblingsorte, Wege",
                   "Places people link with something: places of fear, favourite places, routes")],
        not_for=[("Wo das Gerät gerade ist (dann Standort)", "Where the device is right now (then location)")],
        designs=["perception", "cross"],
        examples=[("Wo in Ihrer Stadt fühlen Sie sich abends unsicher?",
                   "Where in your city do you feel unsafe in the evening?")],
    )
    result_kind = "map"  # no per-column statistics: a map with all markers (results.compute, _map_results.html)
    # the lists of the editor and the results page, labelled in the language of the page
    @property
    def result_views(self):
        return RESULT_VIEWS_EN if english() else RESULT_VIEWS

    @property
    def shapes(self):
        return SHAPES_EN if english() else SHAPES

    @property
    def question_kinds(self):
        return QUESTION_KINDS_EN if english() else QUESTION_KINDS

    max_slots = MAX_SLOTS
    scale_points = SCALE_POINTS

    def default_config(self):
        return {"min": 1, "max": 3, "shape": "pin", "color": DEFAULT_COLOR, "center": list(DEFAULT_CENTER),
                "zoom": DEFAULT_ZOOM, "search": True, "questions": [], "result_view": "heat", "density_m": DENSITY_M}

    def default_texts(self, lang):
        return {"question": "", "questions": {}}

    # --- editor -------------------------------------------------------------------------

    def read_shape(self, item):
        """Per follow-up question its kind, points and options (a single choice stores the place of the option, from
        1: options only added at the end), and the maximum number of markers (only more): once real answers exist they
        stay, or stored answers would be read as other options or leave the export (audit 2026-10-12, M-2)."""
        out = {"markers": self.limits(item)[1]}
        for q in read_cfg(item).get("questions") or []:
            out[f"kind|{q['id']}"] = q.get("kind")
            out[f"points|{q['id']}"] = q.get("points") if q.get("kind") == "scale" else None
            out[f"options|{q['id']}"] = list(q.get("options") or []) if q.get("kind") == "choice" else None
        return out

    def update_from_form(self, item, form, lang, structural):
        config = dict(item.config)
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt["question"] = form.get("question", "").strip()
        qtexts = dict(lt.get("questions", {}))
        ids, labels = form_ids(form.getlist("mq_id")), form.getlist("mq_text")
        lefts, rights = form.getlist("mq_left"), form.getlist("mq_right")
        option_lists = form.getlist("mq_options")
        old = {q["id"]: q for q in config.get("questions", [])}
        if structural:
            hi = _int(form.get("max"), 1, MAX_SLOTS, 3)
            lo = _int(form.get("min"), 0, MAX_SLOTS, 0)
            lat, lon = _float(form.get("center_lat")), _float(form.get("center_lon"))
            config.update(min=lo or None, max=hi, search=bool(form.get("search")),
                          zoom=_int(form.get("zoom"), 1, 19, config.get("zoom", DEFAULT_ZOOM)))
            if valid_position(lat, lon):
                config["center"] = [round(lat, 5), round(lon, 5)]
            codes, kinds = form.getlist("mq_code"), form.getlist("mq_kind")
            points, required = form.getlist("mq_points"), form.getlist("mq_required")
            questions = []
            for n, qid in enumerate(ids):
                code, text = (codes[n] if n < len(codes) else "").strip(), (labels[n] if n < len(labels) else "").strip()
                if not (code or text) or len(questions) >= MAX_QUESTIONS:
                    continue
                qid = qid or _new_id()
                kind = kinds[n] if n < len(kinds) and kinds[n] in QUESTION_KINDS else "text"
                question = {"id": qid, "code": code, "kind": kind,
                            "points": _int(points[n] if n < len(points) else "", 2, 11, 5),
                            "required": (required[n] if n < len(required) else "") == "1"}
                qtexts[qid] = self._qtext(text, lefts, rights, n)
                if kind == "choice":
                    # one option per line; the options keep their ids by their place, so translations stay
                    lines = _option_lines(option_lists[n] if n < len(option_lists) else "")
                    before = (old.get(qid) or {}).get("options") or []
                    if lang != item.survey.default_language and before:  # a translation: the wording, not the options
                        question["options"] = before
                        qtexts[qid]["options"] = {o: (lines[k] if k < len(lines) else "") for k, o in enumerate(before)}
                    else:
                        question["options"] = [before[k] if k < len(before) else _new_id() for k in range(len(lines))]
                        qtexts[qid]["options"] = dict(zip(question["options"], lines))
                questions.append(question)
            config["questions"] = questions
        else:
            known = {q["id"] for q in config.get("questions", [])}
            for n, qid in enumerate(ids):
                if qid in known:
                    qtexts[qid] = self._qtext((labels[n] if n < len(labels) else "").strip(), lefts, rights, n)
                    if old[qid].get("kind") == "choice":  # the survey runs: the wording of the options, not their number
                        lines = _option_lines(option_lists[n] if n < len(option_lists) else "")
                        kept = old[qid].get("options") or []
                        if len(lines) != len(kept):
                            # a line taken out or added would move every later wording onto another stored place
                            # (audit 2026-10-12, M-2): the wordings stay as they were
                            units.note(de_en("Zusatzfrage „{text}“: Die Zahl der Optionen bleibt, solange die Befragung "
                                             "läuft oder echte Antworten hat (gespeichert ist die Nummer der Option). "
                                             "Die Wortlaute wurden nicht geändert; je Zeile lässt sich der Wortlaut "
                                             "einer Option ändern.",
                                             "Follow-up question “{text}”: the number of options stays while the survey "
                                             "runs or has real answers (the number of the option is stored). The "
                                             "wordings were not changed; the wording of an option can be changed line by "
                                             "line.", text=qtexts[qid].get("text") or old[qid].get("code") or ""))
                            qtexts[qid]["options"] = ((lt.get("questions") or {}).get(qid) or {}).get("options") or {}
                            continue
                        qtexts[qid]["options"] = {o: lines[k] for k, o in enumerate(kept)}
        if lang == item.survey.default_language:  # presentation only: may change at any time
            color = form.get("color", "")
            config["color"] = color if HEX.match(color) else config.get("color", DEFAULT_COLOR)
            config["shape"] = form.get("shape") if form.get("shape") in SHAPES else config.get("shape", "pin")
            view = form.get("result_view")
            config["result_view"] = view if view in RESULT_VIEWS else config.get("result_view", "heat")
            # analysis only (computed at export): may change at any time
            config["density_m"] = _int(form.get("density_m"), 1, MAX_DENSITY_M, self.density_m(item))
        lt["questions"] = qtexts
        texts[lang] = lt
        item.config, item.texts = config, texts

    @staticmethod
    def _qtext(text, lefts, rights, n):
        return {"text": text, "left": (lefts[n] if n < len(lefts) else "").strip()[:100],
                "right": (rights[n] if n < len(rights) else "").strip()[:100]}

    def limits(self, item):
        c = read_cfg(item)
        hi = min(c.get("max") or 3, MAX_SLOTS)
        return min(c.get("min") or 0, hi), hi

    def question_code(self, item, index, q):
        return q.get("code") or f"q{index + 1}"

    def questions(self, item, lang=None):
        """Follow-up questions with their texts in `lang` (falling back to the default language). A single
        choice has "labels": the wording of its options in their order (code = place, from 1)."""
        own = ((item.texts or {}).get(lang) or {}).get("questions", {}) if lang else {}
        base = item.text("questions") or {}
        out = []
        for i, q in enumerate(read_cfg(item).get("questions", [])):
            t = {**(base.get(q["id"]) or {}), **{k: v for k, v in (own.get(q["id"]) or {}).items() if v}}
            entry = {**q, "export_code": self.question_code(item, i, q), "text": t.get("text", ""),
                     "left": t.get("left", ""), "right": t.get("right", "")}
            if q.get("kind") == "choice":
                mine, fallback = (own.get(q["id"]) or {}).get("options") or {}, (base.get(q["id"]) or {}).get("options") or {}
                entry["labels"] = [mine.get(o) or fallback.get(o) or "" for o in q.get("options") or []]
            out.append(entry)
        return out

    def option_label(self, q, value):
        """The wording of a single choice's answer (its code), or "" for none."""
        labels = q.get("labels") or []
        return labels[value - 1] if isinstance(value, int) and 1 <= value <= len(labels) else ""

    def validate(self, item):
        c = read_cfg(item)
        problems = []
        lo, hi = c.get("min") or 0, c.get("max") or 0
        if not 1 <= hi <= MAX_SLOTS:
            problems.append(de_en("Höchstanzahl der Marker muss zwischen 1 und {n} liegen", "The maximum number of markers must be between 1 and {n}", n=MAX_SLOTS))
        elif lo > hi:
            problems.append(de_en("Mindestanzahl ist größer als Höchstanzahl", "The minimum number is larger than the maximum number"))
        center = c.get("center") or []
        if len(center) != 2 or not valid_position(_float(center[0]), _float(center[1])):
            problems.append(de_en("Startausschnitt: ungültige Koordinaten", "Starting view: invalid coordinates"))
        qs = self.questions(item)
        codes = [q["export_code"] for q in qs]
        if len(set(codes)) != len(codes):
            problems.append(de_en("Variablennamen der Zusatzfragen sind nicht eindeutig", "The variable names of the follow-up questions are not unique"))
        bad = [x for x in codes if not CODE_RE.match(x) or x in ("lat", "lon")]
        if bad:
            problems.append(de_en("Variablenname „{name}“ ist ungültig (Buchstabe am Anfang; nicht lat/lon)", "Variable name “{name}” is invalid (letter at the start; not lat/lon)", name=bad[0]))
        if any(not q["text"] for q in qs):
            problems.append(de_en("Zusatzfrage ohne Text", "Follow-up question without text"))
        for q in qs:
            if q.get("kind") == "choice" and len([x for x in q["labels"] if x]) < 2:
                problems.append(de_en("Zusatzfrage „{q}“: Eine Einfachauswahl braucht mindestens zwei Antwortoptionen", "Follow-up question “{q}”: a single choice needs at least two answer options", q=q["text"] or q["export_code"]))
            elif q.get("kind") == "choice" and not all(q["labels"]):
                problems.append(de_en("Zusatzfrage „{q}“: Antwortoption ohne Text", "Follow-up question “{q}”: answer option without text", q=q["text"] or q["export_code"]))
        return problems

    # --- participant side ---------------------------------------------------------------

    def map_settings(self, item, lang):
        """Everything the browser needs, as one JSON object for data-map."""
        c = read_cfg(item)
        cfg = current_app.config
        lo, hi = self.limits(item)
        search_url = cfg.get("MAP_SEARCH_URL", NOMINATIM)
        from app import theme

        dark = {}
        if cfg.get("MAP_TILE_URL_DARK") and theme.maps_mode(item.survey) != "never":
            # the survey's look asks for dark maps and the operator has a server for them
            dark = {"tiles_dark": cfg["MAP_TILE_URL_DARK"], "dark": theme.maps_mode(item.survey),
                    "attribution_dark": cfg.get("MAP_TILE_ATTRIBUTION_DARK") or cfg.get("MAP_TILE_ATTRIBUTION", OSM_ATTRIBUTION)}
        return {"center": c.get("center") or DEFAULT_CENTER, "zoom": c.get("zoom") or DEFAULT_ZOOM, **dark,
                "min": lo, "max": hi, "shape": c.get("shape", "pin"), "color": c.get("color") or DEFAULT_COLOR,
                "tiles": cfg.get("MAP_TILE_URL", OSM_TILES), "attribution": cfg.get("MAP_TILE_ATTRIBUTION", OSM_ATTRIBUTION),
                "search": search_url if c.get("search", True) else "",
                "questions": [{**{k: q[k] for k in ("id", "kind", "points", "required", "text", "left", "right")},
                               **({"options": q["labels"]} if q.get("kind") == "choice" else {})}
                              for q in self.questions(item, lang)]}

    def _read(self, item, raw):
        """Markers from the browser's JSON, leniently: [(lat, lon, answers), ...] without judging them."""
        data = load_json(raw or "[]", [])
        if not isinstance(data, list):
            return []
        out = []
        for m in data[:MAX_SLOTS + 5]:
            if isinstance(m, dict):
                q = m.get("q") if isinstance(m.get("q"), dict) else {}
                out.append((_float(m.get("lat")), _float(m.get("lon")), q))
        return out

    def _answer(self, q, raw):
        """A follow-up answer or None; raises ValueError for an impossible value."""
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        if q["kind"] in ("scale", "choice"):
            v = _float(raw)
            top = q.get("points", 5) if q["kind"] == "scale" else len(q.get("options") or [])
            if v is None or not v.is_integer() or not 1 <= v <= top:
                raise ValueError
            return int(v)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            raw = str(raw)
        if not isinstance(raw, str):  # a list or an object is no text (audit 2026-10-13, TN-20)
            raise ValueError
        return clean_text(raw).strip()[:MAX_TEXT]

    @staticmethod
    def _at_least(n, lang):
        return (tr(lang, "Bitte setzen Sie mindestens {n} Marker auf der Karte.", "Please place at least {n} markers on the map.", n=n)
                if n > 1 else tr(lang, "Bitte setzen Sie mindestens einen Marker auf der Karte.", "Please place at least one marker on the map."))

    def parse_answer(self, item, form, lang="de", meta=None):
        read = self._read(item, form.get(f"i{item.id}"))
        lo, hi = self.limits(item)
        if not read:
            if item.required:
                raise AnswerError(self._at_least(max(lo, 1), lang))
            return None
        if len(read) < lo:
            raise AnswerError(self._at_least(lo, lang))
        if len(read) > hi:
            raise AnswerError(tr(lang, "Bitte setzen Sie höchstens {n} Marker.", "Please place at most {n} markers.", n=hi))
        questions = read_cfg(item).get("questions", [])
        markers = []
        for n, (lat, lon, raw) in enumerate(read, start=1):
            if not valid_position(lat, lon):
                raise AnswerError(tr(lang, "Marker {n} hat eine ungültige Position.", "Marker {n} has an invalid position.", n=n))
            answers = {}
            for q in questions:
                try:
                    v = self._answer(q, raw.get(q["id"]))
                except ValueError:
                    raise AnswerError(tr(lang, "Ungültige Antwort bei Marker {n}.", "Invalid answer for marker {n}.", n=n))
                if v is None and q.get("required"):
                    raise AnswerError(tr(lang, "Bitte beantworten Sie die Fragen zu Marker {n}.",
                                         "Please answer the questions for marker {n}.", n=n))
                if v is not None:
                    answers[q["id"]] = v
            markers.append({"lat": round(lat, 6), "lon": round(lon, 6), "q": answers})
        return markers

    def display_value(self, item, form, meta=None):
        """The markers as sent (valid positions only), so nothing is lost when the page is shown again."""
        return [{"lat": lat, "lon": lon, "q": q} for lat, lon, q in self._read(item, form.get(f"i{item.id}"))
                if valid_position(lat, lon)][:MAX_SLOTS] or None

    def is_missing(self, item, answer):
        if not answer:
            return True
        ids = [q["id"] for q in read_cfg(item).get("questions", [])]
        return len(answer) < self.limits(item)[0] or any(m.get("q", {}).get(i) in (None, "") for m in answer for i in ids)

    # --- results page -------------------------------------------------------------------

    def result_column(self, item, column):
        # only the spread is summarised per column (M, Md, SD); the markers themselves go on the map, see results()
        if column in (f"{item.code}_spread_m", f"{item.code}_maxdist_m") or column in {
                f"{item.code}_{x}" for x in NETWORK if x.endswith("_m")}:
            return "auto", "m", None
        if column == f"{item.code}_density":
            return "auto", "", None
        return None, "", None

    def density_m(self, item):
        """The threshold of the density in metres (editor), the default for items from before it existed."""
        v = read_cfg(item).get("density_m")
        return v if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_DENSITY_M else DENSITY_M

    def results(self, item, table, passes=(), lang=None):
        """All markers of the rows in `table` (the export rows after the results filter) for the map on
        the results page, plus counts and the follow-up answers summarised over all markers (and, for scales, over
        persons: each person's markers averaged first). Each point carries the weight 1/k of a person with k
        markers ("w"), so the heatmap gives every person the same weight; beyond MAX_RESULT_POINTS a random
        sample (the same on every visit) is drawn."""
        lang = lang or item.survey.default_language
        qs = self.questions(item, lang)
        cols = [f"{item.code}_json_{n}" for n in passes] or [f"{item.code}_json"]
        points, persons, per_person = [], 0, []
        values = {q["export_code"]: [] for q in qs}
        person_means = {q["export_code"]: [] for q in qs if q["kind"] == "scale"}
        for row in table:
            n = 0
            own_points, own_values = [], {code: [] for code in person_means}
            for col in cols:
                try:
                    markers = json.loads(row.get(col) or "[]")
                except (TypeError, ValueError):
                    markers = []
                for k, m in enumerate(markers if isinstance(markers, list) else [], start=1):
                    lat, lon = _float((m or {}).get("lat")), _float((m or {}).get("lon"))
                    if not valid_position(lat, lon):
                        continue
                    n += 1
                    answers = []
                    for q in qs:
                        v = m.get(q["export_code"])
                        if v not in (None, ""):
                            values[q["export_code"]].append(v)
                            if q["export_code"] in own_values and _float(v) is not None:
                                own_values[q["export_code"]].append(float(v))
                            said = self.option_label(q, int(float(v))) if q["kind"] == "choice" and _float(v) is not None else ""
                            answers.append([q["text"], said or v])
                    own_points.append({"lat": lat, "lon": lon, "p": row.get("resp_id"), "k": k, "a": answers})
            for p in own_points:
                p["w"] = round(1 / len(own_points), 4)
            points += own_points
            for code, vs in own_values.items():
                if vs:
                    person_means[code].append(statistics.fmean(vs))
            if n:
                persons += 1
                per_person.append(n)
        if len(points) > MAX_RESULT_POINTS:  # a random sample, not the first rows; the same on every visit
            keep = sorted(random.Random(item.id).sample(range(len(points)), MAX_RESULT_POINTS))
            points = [points[i] for i in keep]
        summary = []
        for q in qs:
            vs = values[q["export_code"]]
            if q["kind"] == "scale":
                nums = [float(v) for v in vs if _float(v) is not None]
                means = person_means[q["export_code"]]
                summary.append({"q": q, "n": len(nums), "mean": statistics.fmean(nums) if nums else None,
                                "sd": statistics.stdev(nums) if len(nums) > 1 else None,
                                "persons": len(means), "person_mean": statistics.fmean(means) if means else None})
            elif q["kind"] == "choice":  # how often each option was chosen, most frequent first
                codes = [int(float(v)) for v in vs if _float(v) is not None and float(v).is_integer()]
                counts = [(label, codes.count(k)) for k, label in enumerate(q["labels"], start=1)]
                summary.append({"q": q, "n": len(codes), "counts": sorted(counts, key=lambda c: -c[1])})
            else:
                summary.append({"q": q, "n": len(vs)})
        c = read_cfg(item)
        settings = {"center": c.get("center") or DEFAULT_CENTER, "zoom": c.get("zoom") or DEFAULT_ZOOM,
                    "shape": c.get("shape", "pin"), "color": c.get("color") or DEFAULT_COLOR,
                    "tiles": current_app.config.get("MAP_TILE_URL", OSM_TILES),
                    "attribution": current_app.config.get("MAP_TILE_ATTRIBUTION", OSM_ATTRIBUTION),
                    "view": c.get("result_view") if c.get("result_view") in RESULT_VIEWS else "heat"}
        return {"points": points, "n": sum(per_person), "persons": persons, "truncated": sum(per_person) > len(points),
                "mean": statistics.fmean(per_person) if per_person else None, "summary": summary, "settings": settings}

    # --- export -------------------------------------------------------------------------

    def open_columns(self, item):
        text_codes = {q["export_code"] for q in self.questions(item) if q.get("kind") == "text"}
        positions = self.position_columns(item)
        return [c for c, _ in self.export_columns(item) if c in positions or any(c.endswith("_" + t) for t in text_codes)]

    def export_columns(self, item):
        code, hi = item.code, self.limits(item)[1]
        qs = self.questions(item)
        cols = [(f"{code}_n", "Anzahl gesetzter Marker"),
                (f"{code}_spread_m", "Streuung: mittlerer Abstand zwischen den eigenen Markern (Luftlinie, m)"),
                (f"{code}_maxdist_m", "Streuung: größter Abstand zwischen zwei eigenen Markern (Luftlinie, m)"),
                (f"{code}_mindist_m", "Netz: kleinster Abstand zwischen zwei eigenen Markern (Luftlinie, m)"),
                (f"{code}_path_m", "Netz: kürzester Pfad durch alle eigenen Marker (Luftlinie, m)"),
                (f"{code}_mst_m", "Netz: Länge des minimalen Spannbaums der eigenen Marker (Luftlinie, m)"),
                (f"{code}_density", f"Netz: Dichte, Anteil der Markerpaare höchstens {self.density_m(item)} m auseinander")]
        for k in range(1, hi + 1):
            cols += [(f"{code}_{k}_lat", f"Marker {k}: Breitengrad (WGS84)"),
                     (f"{code}_{k}_lon", f"Marker {k}: Längengrad (WGS84)")]
            cols += [(f"{code}_{k}_{q['export_code']}", f"Marker {k}: {q['text']}") for q in qs]
        cols.append((f"{code}_json", "Alle Marker als JSON (lat, lon, Zusatzfragen nach Variablenname)"))
        return cols

    def fixed_summary(self, item, answer, lang):
        """The markers in words for an answer fixed by feedback (UI/UX audit 2026-10-17, W-A03): how many, and per
        marker the answers to the follow-up questions."""
        markers = [m for m in (answer or []) if isinstance(m, dict)]
        out = [("", tr(lang, "Gesetzte Marker: {n}", "Markers placed: {n}", n=len(markers)))]
        qs = self.questions(item, lang)
        for k, m in enumerate(markers, 1):
            said = []
            for q in qs:
                v = (m.get("q") or {}).get(q["id"])
                if v in (None, ""):
                    continue
                text = self.option_label(q, whole(v)) if q.get("kind") == "choice" else str(v)
                said.append(f"{q['text']}: {text}" if q["text"] else text)
            if said:
                out.append((tr(lang, "Marker {k}", "Marker {k}", k=k), "; ".join(said)))
        return out

    def export_values(self, item, answer):
        # the follow-up questions and limits once per export, not per participation (code review 2026-10-07, R4)
        code, hi, qs, density = prepared(item, "map_export", lambda: (
            item.code, self.limits(item)[1], self.questions(item), self.density_m(item)))
        markers = answer or []
        out = {f"{code}_n": len(markers) if answer is not None else None}
        out[f"{code}_spread_m"], out[f"{code}_maxdist_m"] = spread(markers)
        out.update({f"{code}_{k}": v for k, v in network(markers, density).items()})
        for k in range(1, hi + 1):
            m = markers[k - 1] if k <= len(markers) else {}
            out[f"{code}_{k}_lat"], out[f"{code}_{k}_lon"] = m.get("lat"), m.get("lon")
            for q in qs:
                out[f"{code}_{k}_{q['export_code']}"] = (m.get("q") or {}).get(q["id"])
        full = [{"lat": m.get("lat"), "lon": m.get("lon"),
                 **{q["export_code"]: (m.get("q") or {}).get(q["id"]) for q in qs}} for m in markers]
        out[f"{code}_json"] = json.dumps(full, ensure_ascii=False, separators=(",", ":")) if answer is not None else None
        return out

    def position_columns(self, item):
        """The columns with exact positions: latitude and longitude per marker and the JSON list."""
        slots = range(1, self.limits(item)[1] + 1)
        return {f"{item.code}_{k}_{axis}" for k in slots for axis in ("lat", "lon")} | {f"{item.code}_json"}

    def codebook_info(self, item, column, lang, ui=None):
        code = item.code
        metres = in_lang(ui, "Meter (ganzzahlig)", "metres (whole)")
        if column == f"{code}_n":
            lo, hi = self.limits(item)
            return "Ganzzahl", in_lang(ui, "0 bis {hi}", "0 to {hi}", hi=hi), [
                in_lang(ui, "mindestens {lo}, höchstens {hi} Marker", "at least {lo}, at most {hi} markers", lo=lo, hi=hi) if lo
                else in_lang(ui, "höchstens {hi} Marker", "at most {hi} markers", hi=hi)]
        if column in (f"{code}_spread_m", f"{code}_maxdist_m"):
            if column.endswith("_spread_m"):
                what = in_lang(ui, "Mittelwert der Luftlinien-Abstände", "mean of the straight-line distances")
            else:
                what = in_lang(ui, "Maximum der Luftlinien-Abstände", "maximum of the straight-line distances")
            return "Zahl", metres, [
                what + in_lang(ui, " (Großkreis, Haversine) über alle Paare eigener Marker",
                               " (great circle, haversine) over all pairs of the person's own markers"),
                in_lang(ui, "leer bei weniger als 2 Markern; klein = Marker dicht beieinander",
                        "empty with fewer than 2 markers; small = markers close together")]
        if column in {f"{code}_{x}" for x in NETWORK}:
            short, empty = column[len(code) + 1:], in_lang(ui, "leer bei weniger als 2 Markern", "empty with fewer than 2 markers")
            if short == "density":
                return "Zahl", in_lang(ui, "0 bis 1", "0 to 1"), [in_lang(
                    ui, "Anteil der Paare eigener Marker mit Luftlinien-Abstand höchstens {m} m (Schwelle im Editor) an "
                        "allen Paaren; Graphendichte nach Wasserman & Faust (1994)",
                    "share of the pairs of the person's own markers at most {m} m apart in a straight line (limit in the "
                    "editor) among all pairs; graph density after Wasserman & Faust (1994)", m=self.density_m(item)), empty]
            how = {"mindist_m": in_lang(ui, "Kleinster Luftlinien-Abstand (Großkreis, Haversine) zwischen zwei eigenen Markern",
                                        "smallest straight-line distance (great circle, haversine) between two of the "
                                        "person's own markers"),
                   "path_m": in_lang(ui, "Kürzester offener Pfad, der jeden eigenen Marker genau einmal besucht (Start und "
                                         "Ende frei), Luftlinie; exakt nach Held & Karp (1962) bis {n} Marker, darüber "
                                         "Näherung (nächster Nachbar von jedem Start plus 2-opt nach Croes, 1958)",
                                     "shortest open path that visits each of the person's own markers exactly once (start "
                                     "and end free), straight line; exact after Held & Karp (1962) up to {n} markers, above "
                                     "that an approximation (nearest neighbour from every start plus 2-opt after Croes, 1958)",
                                     n=EXACT_PATH),
                   "mst_m": in_lang(ui, "Gesamtlänge des minimalen Spannbaums der eigenen Marker (Prim, 1957), Luftlinie",
                                    "total length of the minimum spanning tree of the person's own markers (Prim, 1957), "
                                    "straight line")}[short]
            return "Zahl", metres, [how, empty]
        if column == f"{code}_json":
            return "JSON", "", [in_lang(ui, "Liste in Setz-Reihenfolge: [{lat, lon, <Zusatzfrage>: Wert}]",
                                        "list in the order placed: [{lat, lon, <follow-up question>: value}]")]
        slots = range(1, self.limits(item)[1] + 1)
        if column in self.position_columns(item):  # not a follow-up question whose code ends in _lat
            lat = column.endswith("_lat")
            return "Zahl", in_lang(ui, "-90 bis 90", "-90 to 90") if lat else in_lang(ui, "-180 bis 180", "-180 to 180"), \
                [in_lang(ui, "Grad (WGS84, 6 Nachkommastellen); leer, wenn weniger Marker gesetzt wurden",
                         "degrees (WGS84, 6 decimal places); empty if fewer markers were placed")]
        for q in self.questions(item, lang):
            if column in {f"{code}_{k}_{q['export_code']}" for k in slots}:
                if q.get('required'):
                    notes = [in_lang(ui, "Zusatzfrage je Marker (Pflicht)", "follow-up question per marker (required)")]
                else:
                    notes = [in_lang(ui, "Zusatzfrage je Marker (freiwillig)", "follow-up question per marker (optional)")]
                if q["kind"] == "scale":
                    ends = f"1 = {q['left']}" if q["left"] else "1"
                    ends += f"; {q['points']} = {q['right']}" if q["right"] else ""
                    return "Skala", ends, notes
                if q["kind"] == "choice":
                    return "Auswahl", "; ".join(f"{k} = {label.replace(';', ',')}" for k, label in enumerate(q["labels"], start=1)), notes
                return "Text", "", notes
        return None

    # --- long format: one row per marker (data page and saved states; Zenodo only with exact positions ticked) ---
    long_export = "map"
    long_export_label = Both("Person × Marker: Position und Zusatzfragen", "Person × marker: position and follow-up questions")
    long_export_note = ("- `map_*_long.csv`: one row per marker (participant × marker): number in the order placed, "
                        "latitude and longitude (WGS84), the follow-up answers by their variable names (single choice: "
                        "code and label).")
    long_private = True  # exact positions: in a public data package only where they are wanted (open answers)

    def long_columns(self, item):
        from app.export import _loop_rows

        cols = ["pass"] if _loop_rows(item) else []
        cols += ["marker", "lat", "lon"]
        for q in self.questions(item):
            cols.append(q["export_code"])
            if q.get("kind") == "choice":
                cols.append(q["export_code"] + "_label")
        return cols

    def long_rows(self, item, response):
        """One row per marker, in the order placed; inside a loop the rows of each pass."""
        from app.export import _loop_rows

        qs = self.questions(item, item.survey.default_language)
        stored = response.answers or {}
        passes = _loop_rows(item)
        keys = [(n, f"{item.id}#{row}") for n, row in passes] if passes else [(None, str(item.id))]
        for n, key in keys:
            markers = stored.get(key)
            for k, m in enumerate(markers if isinstance(markers, list) else [], start=1):
                if not isinstance(m, dict):
                    continue
                row = ([n] if passes else []) + [k, m.get("lat"), m.get("lon")]
                for q in qs:
                    v = (m.get("q") or {}).get(q["id"])
                    row.append("" if v is None else v)
                    if q.get("kind") == "choice":
                        row.append(self.option_label(q, v))
                yield row
