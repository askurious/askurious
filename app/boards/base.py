"""Shared parts of dashboards: blueprints, limits, labels, languages, shapes of items, coding."""
import contextvars
import re
import statistics
from datetime import datetime, timedelta

from flask import Blueprint, request

from app.lang import de_en, english
from app.models import utcnow
from app.ui_i18n import tr


bp = Blueprint("boards", __name__, url_prefix="/surveys/<sid:survey_id>/dashboards")
share_bp = Blueprint("board_share", __name__, url_prefix="/d")

FORMAT = 4  # 4: scale rows carry their variable IDs for public wording restrictions
MIN_TILE = 10   # answers a tile needs before it shows numbers (the standard; a dashboard may choose another, TILE_MINS)
TILE_MINS = (1, 5, 10, 25, 50)  # what the designer offers (Ralf, 2026-10-03); 1: "sofort", from the first answer
_TILE_MIN = contextvars.ContextVar("tile_min", default=MIN_TILE)  # the threshold of the dashboard being built
_CELL_MIN = contextvars.ContextVar("cell_min", default=None)  # its rule of small cells (None: MIN_CELL)


def min_tile_of(board):
    """The answers a tile of this dashboard needs before it shows numbers: its own choice, else MIN_TILE."""
    value = (board.settings or {}).get("min_tile") if board is not None else None
    return value if value in TILE_MINS else MIN_TILE


def tile_min():
    """The threshold while a dashboard is built (build), MIN_TILE elsewhere."""
    return _TILE_MIN.get()


def cell_min_of(board):
    """Below how many answers an answer is put together with others: MIN_CELL, and 1 (nothing put together) for a
    dashboard that shows results "sofort" (Ralf, 2026-10-03: from the first answer; its designer says that single
    answers can then be recognised)."""
    return 1 if min_tile_of(board) == 1 else MIN_CELL


def cell_min():
    """The rule of small cells while a dashboard is built (build), MIN_CELL elsewhere."""
    value = _CELL_MIN.get()
    return MIN_CELL if value is None else value
MIN_CELL = 5    # an answer named less often is put together with others
MAX_BOARDS = 100
MAX_TILES = 80
MAX_SHARES = 20
# links for one value of a URL parameter each (e.g. one per student, made together on "Datenerhebung"): they do not
# count against MAX_SHARES, the allowed values bound them (panel.MAX_ALLOWED per parameter)
MAX_VALUE_SHARES = 400
AFTER_FIELD = 14  # days a link works beyond the field end
VALIDITY = (("field", f"bis {AFTER_FIELD} Tage nach dem Feldende"), ("7", "7 Tage"), ("30", "30 Tage"), ("90", "90 Tage"),
            ("never", "unbegrenzt (bis zum Widerruf)"))
VALIDITY_EN = {"field": f"until {AFTER_FIELD} days after the field end", "7": "7 days", "30": "30 days", "90": "90 days",
               "never": "unlimited (until revoked)"}
# a link without an end: the column must hold a date, so it holds the last one there is
UNLIMITED = datetime(9999, 12, 31)
DAYS_SHOWN = 60
# how wide a tile is, in twelfths of the row
WIDTHS = ((3, "ein Viertel"), (4, "ein Drittel"), (6, "die Hälfte"), (8, "zwei Drittel"), (9, "drei Viertel"), (12, "ganze Zeile"))
WIDTHS_EN = {3: "a quarter", 4: "a third", 6: "half", 8: "two thirds", 9: "three quarters", 12: "whole row"}
OTHERS = "Weitere Antworten"
# where a tile begins in its row: 0 = where the tiles before it leave room, else the column (1 to 12); a tile
# that begins further right leaves a gap on purpose
STARTS = ((0, "automatisch"), (1, "ganz links"), (4, "nach einem Viertel"), (5, "nach einem Drittel"), (7, "ab der Mitte"),
          (9, "nach zwei Dritteln"), (10, "nach drei Vierteln"))
STARTS_EN = {0: "automatic", 1: "far left", 4: "after a quarter", 5: "after a third", 7: "from the middle",
             9: "after two thirds", 10: "after three quarters"}
# tiles that show no data: a heading over the tiles below it, or a text (formatted like the long texts of a
# questionnaire, items.base.rich). Their key is "text:" and letters and digits the designer made up.
TEXT_KEY = re.compile(r"^text:[a-z0-9]{4,16}\Z")
TEXT_KINDS = {"heading": "Überschrift", "text": "Text"}
TEXT_KINDS_EN = {"heading": "Heading", "text": "Text"}
MAX_TEXT = 2000

VIEWS = {"map": "Karte", "bars": "Balken", "columns": "Säulen", "donut": "Ring", "table": "Tabelle", "stacked": "Gestapelte Balken",
         "means": "Mittelwerte mit Streuung", "number": "Kennzahl", "dist": "Verteilung in Klassen", "ring": "Ring",
         "progress": "Fortschrittsbalken", "importance": "Wichtigkeit der Merkmale", "levels": "Anteile je Stufe",
         "points": "Preispunkte", "compare": "Verteilung: Soll und erreicht", "cloud": "Wortwolke",
         "marks": "Text mit Markierungen", "top": "Am häufigsten markiert", "heat": "Heatmap (Raster)"}
SHAPE_VIEWS = {"cats": ("bars", "columns", "donut", "table"), "multi": ("bars", "columns", "table"),
               "scale": ("stacked", "means", "table"), "num": ("number", "dist", "table"), "nums": ("means", "table"),
               "score": ("bars", "table"), "conjoint": ("importance", "levels", "table"), "points": ("points", "table"),
               "states": ("map", "bars", "table"), "dist": ("columns", "table"), "quota": ("bars", "compare", "ring", "table"),
               "words": ("cloud", "table"), "highlight": ("marks", "top")}
FIELD = {  # figures of the fieldwork: name, views
    "count": ("Teilnahmen", ("number",)),
    "rate": ("Abschlussquote", ("number", "ring")),
    "duration": ("Mittlere Dauer", ("number",)),
    "target": ("Fortschritt zum Stichprobenziel", ("progress", "ring", "number")),
    "days": ("Teilnahmen pro Tag", ("columns", "table")),
}
FIELD_NAMES = {"de": {name: label for name, (label, _) in FIELD.items()},
               "en": {"count": "Participations", "rate": "Completion rate", "duration": "Typical duration",
                      "target": "Progress towards the sample target", "days": "Participations per day"}}
TEMPLATES = {"field": "Feldbericht", "results": "Ergebnisbericht", "empty": "Leer"}
TEMPLATE_NOTES = {"field": "Teilnahmen, Verlauf und Quoten", "results": "alle Fragen", "empty": "selbst auswählen"}
# the researcher-facing labels above in English (the designer in English, app/lang.py); the German ones stay the keys' labels
VIEWS_EN = {"map": "Map", "bars": "Bars", "columns": "Columns", "donut": "Ring", "table": "Table", "stacked": "Stacked bars",
            "means": "Means with spread", "number": "Key figure", "dist": "Distribution in classes", "ring": "Ring",
            "progress": "Progress bar", "importance": "Importance of the attributes", "levels": "Shares per level",
            "points": "Price points", "compare": "Distribution: target and reached", "cloud": "Word cloud",
            "marks": "Text with highlights", "top": "Most often highlighted", "heat": "Heatmap (grid)"}
TEMPLATES_EN = {"field": "Field report", "results": "Results report", "empty": "Empty"}
TEMPLATE_NOTES_EN = {"field": "participations, course and quotas", "results": "all questions", "empty": "choose yourself"}


def validity():
    """VALIDITY with its labels in the language of the page."""
    return tuple((key, VALIDITY_EN[key] if english() else name) for key, name in VALIDITY)


def widths():
    """WIDTHS with its labels in the language of the page."""
    return tuple((w, WIDTHS_EN[w] if english() else name) for w, name in WIDTHS)


def starts():
    """STARTS with its labels in the language of the page."""
    return tuple((x, STARTS_EN[x] if english() else name) for x, name in STARTS)


def text_kinds():
    """TEXT_KINDS in the language of the page."""
    return TEXT_KINDS_EN if english() else TEXT_KINDS


def view_name(view):
    """The name of a view of a tile (VIEWS) in the language of the page."""
    return VIEWS_EN[view] if english() else VIEWS[view]


def templates():
    """TEMPLATES in the language of the page."""
    return TEMPLATES_EN if english() else TEMPLATES


def template_notes():
    """TEMPLATE_NOTES in the language of the page."""
    return TEMPLATE_NOTES_EN if english() else TEMPLATE_NOTES

# why a question cannot go onto a dashboard
NEVER = "Antworten einzelner Personen: nie auf einem Dashboard"
LATER = "folgt in einer späteren Ausbaustufe"
NEVER_TYPES = {"cloze", "map_marker", "position2d", "image_mark"}
# the heatmap of a location (items/geolocation.py): people per square of the grid, never single positions; a square
# with fewer than cell_min() people is left out
HEAT_LAT, HEAT_LON = 0.1, 0.15  # degrees: about 11 km by 10 km in Germany
# a word cloud (decision L4, live concept): short answers of free text (up to WORDS_MAX words) and
# free associations, a word only from MIN_CELL mentions on, never a longer text; at most CLOUD_MAX words
WORDS_MAX = 3
CLOUD_MAX = 60
# A question inside a loop is asked once per row of the loop's table: every pass is a tile of its own, keyed
# "item:<id>@<row id>", built from the columns of that pass (<column>_<n>); the price points of a pass too (its four
# prices are columns like any other). Not the choice tasks: they cannot stand in a loop.
LOOP_SHAPES = ("cats", "multi", "scale", "num", "nums", "states", "dist", "points")
ROW_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}\Z")


# --- the language of a dashboard ------------------------------------------------------------------------
# A dashboard is shown in the languages of its survey: questions and answers as translated on the page
# "Übersetzung", the dashboard's own words through app/ui_i18n.py. The language is set once for a build and for
# showing it (speaking), so the functions that make labels and numbers need not pass it along.

_speaking = contextvars.ContextVar("board_language", default="de")


def _lang():
    return _speaking.get()


class speaking:
    """with speaking(lang): what is built and shown inside is in this language."""

    def __init__(self, lang):
        self.lang = lang

    def __enter__(self):
        self.token = _speaking.set(self.lang or "de")

    def __exit__(self, *exc):
        _speaking.reset(self.token)


def language(survey):
    """The language a request asks for (?lang=), if the survey has it; else the survey's own."""
    asked = request.args.get("lang")
    return asked if asked in (survey.languages or []) else survey.default_language


# --- numbers as text ------------------------------------------------------------------------------------


def _point():
    from app.units import writes_like_english

    return writes_like_english(_lang())  # a decimal point; everywhere else a comma


def _num(value, places=2):
    """A number with a fixed number of decimals: values of one kind (means, medians, standard deviations) are
    all rounded alike, so 3 next to 2,7 reads 3,0. Written as on the results page and in Word (units.stat, UI/UX
    audit 2026-10-05, V70): a dot between thousands (93.367), M and SD with two decimals."""
    from app import units

    return units.stat(value, places, "en" if _point() else "de")


def _pct(share):
    """A share as a whole percentage, rounded as on the results page (half up: 12,5 % reads 13 %)."""
    from app import units

    return f"{units.percent(share)}%" if _point() else f"{units.percent(share)} %"


def _time(seconds):
    """A duration as everywhere in the researcher pages (duration.short, Qualitätssicherung, Zeitverlauf): "3:18 Min."
    (UI/UX audit 2026-10-05, V73)."""
    from app import units

    seconds = units.round_half_up(seconds)
    if seconds >= 60:
        return tr(_lang(), "{m}:{s} Min.", "{m}:{s} min", m=seconds // 60, s=f"{seconds % 60:02d}")
    return tr(_lang(), "{s} Sek.", "{s} sec", s=seconds)


# --- what can be picked ---------------------------------------------------------------------------------


def shape_of(item):
    """(shape, reason): how the answers of a question are summarised on a dashboard ("cats" one answer of
    several, "multi" several answers, "scale" statements on a common scale, "num" a number, "nums" several
    numbers, "states" people per federal state, "dist" dates or times in classes, "score" best-worst scores,
    "conjoint" attributes and their levels, "points" price points), or (None, why not)."""
    from app import export

    if item.type == "consent":
        return None, de_en("nichts auszuwerten: Wer teilnimmt, hat eingewilligt", "nothing to analyse: anyone who takes part has consented")
    if item.type in NEVER_TYPES:
        return None, de_en(NEVER, "answers of individual people: never on a dashboard")
    shape, reason = _shape(item)
    if shape not in LOOP_SHAPES and shape is not None and export._loop_rows(item):
        return None, de_en("Frage in einer Schleife: {later}", "question in a loop: {later}", later=_later())
    return shape, reason


def _later():
    return de_en(LATER, "follows in a later stage")


def _shape(item):
    kind = item.kind
    if item.type == "single_choice":
        return "cats", ""
    if item.type in ("federal_state", "postal_code", "geolocation"):
        return "states", ""
    if item.type in ("ranking", "constant_sum"):
        return "nums", ""
    if item.type in ("maxdiff", "paired", "conjoint", "acbc", "psm", "cva"):
        return {"maxdiff": "score", "paired": "score", "psm": "points"}.get(item.type, "conjoint"), ""
    if item.type == "date_time":
        return ("dist" if kind.date_kind(item, item.code) else "num"), ""
    if item.type == "multiple_choice":
        return "multi", ""
    if item.type in ("likert", "semantic"):
        return "scale", ""
    if item.type == "rating":
        return ("scale" if kind.scale_values(item, item.code) else "num"), ""
    if item.type == "slider":
        return ("nums" if len(item.config.get("rows", [])) > 1 else "num"), ""
    if item.type == "text_input":
        return ("num", "") if kind.level(item) == "metric" else ("words", "")
    if item.type == "association":
        return "words", ""
    if item.type == "highlight":  # the text with how many marked each word, as on the results page (Ralf, 2026-10-03)
        return "highlight", ""
    if item.type == "side_by_side":  # one tile per column (sbs_columns)
        return "sbs", ""
    return None, _later()


def sbs_columns(item, lang):
    """The columns of a side-by-side matrix a tile can show, [(row id "c-<column id>", question of the column, shape,
    views, reason)]: a choice or drop-down column as a scale over its rows (stacked bars; means only where it is
    metric), a number as the mean per row, tick boxes as the share of each answer per row (shape "multi"), short
    texts as the words named in any row (shape "words", with the rules of a free text)."""
    from app.items.base import plain

    kind, out = item.kind, []
    for col in kind.columns(item):
        title = " ".join(plain(kind.title(item, col, lang)).split()) or col["id"]
        if col["kind"] in ("single", "dropdown"):
            shape = "scale"
            views = ("stacked", "means", "table") if col.get("level") == "metric" else ("stacked", "table")
        elif col["kind"] == "number":
            shape, views = "nums", SHAPE_VIEWS["nums"]
        elif col["kind"] == "multi":
            shape, views = "multi", SHAPE_VIEWS["multi"]
        else:
            shape, views = "words", SHAPE_VIEWS["words"]
        out.append((f"c-{col['id']}", title, shape, tuple((v, view_name(v)) for v in views), ""))
    return [c for c in out if ROW_ID.match(c[0])]


def _sbs_data(item, cid, table, lang):
    """What the tile of one column of a side-by-side matrix shows: per row of the matrix its answers on the scale of
    the column (shape "scale") or its numbers (shape "nums"); -77 (not shown, not asked) and -98 do not count."""
    from app import results
    from app.items import side_by_side as _sbs
    from app.items.base import plain

    kind = item.kind
    col = kind.column(item, cid)
    i = next(n for n, c in enumerate(kind.columns(item)) if c["id"] == cid)
    code = f"{item.code}_{_sbs.column_code(i, col)}"
    rtexts = item.text("rows", lang) or {}
    lines = [(plain(rtexts.get(r["id"], "")) or r_code, f"{code}_{r_code}")
             for n, r in enumerate(item.config.get("rows", [])) for r_code in [_sbs.row_code(n, r)]]
    if col["kind"] == "multi":  # per row and answer: ticked of those who answered the row (-77: not asked there)
        labels = [plain(x) or str(n) for n, x in enumerate(kind.labels(item, col, lang), start=1)]
        bars, n = [], set()
        for label, column in lines:
            for k, answer in enumerate(labels, start=1):
                seen = [(i, x) for i, x in enumerate(results._num(v) for v in _values(table, f"{column}_{k}")) if x in (0, 1)]
                n.update(i for i, _ in seen)
                # every row and answer, also one nobody here answered (seen 0): a tile split by groups lays the
                # groups side by side by position (_compare), so each group needs the same bars
                bars.append((f"{label}: {answer}", sum(1 for _, x in seen if x == 1), len(seen)))
        return {"shape": "multi", "labels": [b[0] for b in bars if b[2]], "n": len(n), "_rows": bars, "_other": [],
                "partial": any(seen != len(n) for _, _, seen in bars if seen), "_drop_unseen": True}
    if col["kind"] == "text":  # the mentions in any row, as the short answers of a text field
        from collections import Counter

        from app.rows import NOT_SHOWN

        counts, shown, n = Counter(), {}, 0
        for row in table:
            said = [" ".join(str(row.get(c) or "").split()) for _, c in lines]
            said = [t for t in said if t and t != str(NOT_SHOWN)]  # -77: not asked in that row
            if not said:
                continue
            n += 1
            for text in dict.fromkeys(t.casefold() for t in said if len(t.split()) <= WORDS_MAX and len(t) <= 60):
                counts[text] += 1
            for t in said:
                shown.setdefault(t.casefold(), t)
        return {"shape": "words", "n": n, "_rows": [(shown[k], c) for k, c in counts.most_common()]}
    if col["kind"] == "number":
        rows = []
        for label, column in lines:
            gone = {str(m) for m in kind.missing_codes(item, column)}
            xs = [x for x in (results._num(v) for v in _values(table, column) if results._key(v) not in gone) if x is not None]
            rows.append({"label": label, "n": len(xs), "_xs": xs})
        return {"shape": "nums", "unit": kind.unit(item, col, lang), "bounds": None, "rows": rows, "_scale": False,
                "n": max((r["n"] for r in rows), default=0)}
    levels = [plain(x) or str(n) for n, x in enumerate(kind.labels(item, col, lang), start=1)]
    k, out = len(levels), []
    for label, column in lines:
        numbers = [x for x in (results._num(v) for v in _values(table, column)) if x is not None and float(x).is_integer() and 1 <= x <= k]
        counts = [0] * k
        for x in numbers:
            counts[int(x) - 1] += 1
        out.append({"label": label, "n": len(numbers), "_counts": counts, "reverse": False,
                    "_mean": statistics.fmean(numbers) if numbers else None,
                    "_sd": statistics.stdev(numbers) if len(numbers) > 1 else None})
    return {"shape": "scale", "levels": levels, "first": 1, "rows": out, "n": max((r["n"] for r in out), default=0),
            "ordinal": col.get("level") != "metric"}


def _question(item, lang):
    from app.items.base import plain

    return " ".join(plain(item.text("question", lang) or "").split()) or item.kind.label


def coding_key(item, column):
    """The tile of the categories of an open answer (app/coding.py): "item:<id>@k<rest of the column>", the column's
    key (coding.key_of) as a row of the element, so copies and files keep it as they keep a pass of a loop (remap).
    Categorised columns lie outside loops, so the row never meets a pass."""
    from app import coding

    return f"item:{item.id}@k{coding.key_of(item, column)[len(str(item.id)):]}"


def _coding_rows(item, coded, lang):
    """The rows of the designer for the categorised columns of `item` (coded: coding.plan): one tile each with the
    frequencies of the categories, never the texts. One category per answer: as single choice; several: as multiple
    choice."""
    entries = coded.get(item.id) or []
    rows = []
    for col, _, _, found in entries:
        key = coding_key(item, col)
        if item_ref(key)[0] is None:  # a column name too long for a tile key
            continue
        shape = "cats" if found["single"] else "multi"
        rows.append({"key": key, "label": f"{_question(item, lang)} · {de_en('Kategorien', 'Categories')}"
                     + (f" · {col}" if len(entries) > 1 else ""),
                     "hint": de_en("{code} · kategorisierte offene Antworten", "{code} · categorised open answers", code=col),
                     "shape": shape, "reason": "",
                     "views": [(v, view_name(v)) for v in SHAPE_VIEWS[shape]
                               if not (v == "donut" and len(found["cats"]) > MAX_RING)]})
    return rows


def _coding_entry(coded, item, row_id):
    """The entry of coding.plan a tile row names ("k..."), None if it names none."""
    if not row_id or not row_id.startswith("k"):
        return None
    return next((e for e in coded.get(item.id) or [] if coding_key(item, e[0]) == f"item:{item.id}@{row_id}"), None)


def _coded(entry):
    """The maker of the tile of one categorised column: per category how many persons got it, of the persons whose
    answer is categorised (empty columns: no answer or not categorised yet, as in the export). The categories
    "Sonstiges" and "nicht verwertbar" count as the residual answers (last when sorted)."""
    from app import coding

    col, _, _, found = entry
    cats = found["cats"]
    other = [c["name"] for c in cats if c["id"] in coding.FIXED]

    def make(item, table, lang):
        if found["single"]:
            got = [g for g in _values(table, f"{col}_kat") if g not in (None, "")]
            rows = [(c["name"], sum(1 for g in got if str(g) == str(c["id"]))) for c in cats]
            return {"shape": "cats", "labels": [r[0] for r in rows], "n": len(got), "_rows": rows, "nominal": True,
                    "_other": other}
        cols = [(c, f"{col}_k{c['id']}") for c in cats]
        people = [r for r in table if any(r.get(x) in (0, 1) for _, x in cols)]
        rows = [(c["name"], sum(1 for r in people if r.get(x) == 1), len(people)) for c, x in cols]
        return {"shape": "multi", "labels": [r[0] for r in rows], "n": len(people), "_rows": rows, "_other": other,
                "partial": False}

    return make


def _fixed_order(rows, entry):
    """The categories "Sonstiges" and "nicht verwertbar" among the sorted rows of a tile in their own order, on the
    places by_frequency gave them (rows with a number only)."""
    from app import coding

    order = {c["name"]: c["id"] for c in entry[3]["cats"] if c["id"] in coding.FIXED}
    slots = [i for i, r in enumerate(rows) if r.get("label") in order and not r.get("rest") and r.get("count") is not None]
    for i, row in zip(slots, sorted((rows[i] for i in slots), key=lambda r: order[r["label"]])):
        rows[i] = row


def item_ref(key):
    """(item id, loop row id | None) of a tile key "item:<id>" or "item:<id>@<row id>"; (None, None) otherwise."""
    kind, _, ref = str(key or "").partition(":")
    ref, _, row = ref.partition("@")
    if kind != "item" or not (ref.isascii() and ref.isdigit() and len(ref) < 12) or (row and not ROW_ID.match(row)):
        return None, None
    return int(ref), row or None


def _fill(value, variables):
    """Every text of what a tile shows with the placeholders of a loop pass filled in ({{robot}})."""
    from app.items.base import PLACEHOLDER

    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: str(variables.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: _fill(v, variables) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_fill(v, variables) for v in value)
    return value


def _pass_title(item, row_id, lang):
    """The question as one pass of its loop asks it; where it reads the same in every pass, the values of the
    row follow it."""
    from app import loops

    asked = _question(item, lang)
    variables = loops.variables(item.block, row_id, lang)
    filled = _fill(asked, variables)
    values = " · ".join(str(v).strip() for v in variables.values() if str(v).strip())
    return filled if filled != asked or not values else f"{asked} · {values}"


def _pass_table(table, n):
    """The table as one pass of a loop gave it: its columns (<column>_<n>) under the names they have outside a
    loop, so every maker reads them as usual."""
    from app import export

    suffix = f"_{n}"
    return export.Table(({**r, **{c[:-len(suffix)]: v for c, v in r.items() if c.endswith(suffix)}} for r in table), table.responses)


def _tile(key, row, width=6):
    return {"key": key, "view": row["views"][0][0], "w": width, "x": 0, "pct": True, "n": True, "by": "", "title": ""}


# --- the tiles ------------------------------------------------------------------------------------------


def _values(table, column):
    return [r.get(column) for r in table]
MAX_RING = 5  # a ring only for up to five answers: more parts are not compared by angle (V81)


def expiry(share):
    """When a link runs out. One made "until after the field end" follows the field end of its survey: if that
    is moved, the link works until AFTER_FIELD days after the new one. Without a field end, or with one that had
    passed when the link was made, it keeps the date it got then (30 days)."""
    end = share.board.survey.field_end if share.valid == "field" and share.board is not None else None
    if end is not None and (share.created_at is None or end > share.created_at):
        return end + timedelta(days=AFTER_FIELD)
    return share.expires_at


def has_link(board):
    """Has the dashboard a valid link? (live_shares without making the addresses)"""
    now = utcnow()
    return any(not s.revoked and expiry(s) > now for s in board.shares)
