"""The tiles of a dashboard: which items fit, the designer's form, and the figures of each shape."""
import json
import math
import statistics
from datetime import timedelta

from app.boards.base import (CLOUD_MAX, DAYS_SHOWN, FIELD, FIELD_NAMES, HEAT_LAT, HEAT_LON, MAX_RING, MAX_TEXT,
                             MAX_TILES, MIN_CELL, MIN_TILE, OTHERS, SHAPE_VIEWS, STARTS, TEXT_KEY, TEXT_KINDS, TILE_MINS, WIDTHS,
                             WORDS_MAX, _coding_rows, _lang, _num, _pass_title, _pct, _point, _question, _tile, _time,
                             _values, cell_min, has_link, sbs_columns, shape_of, tile_min, view_name)
from app.extensions import db
from app.lang import de_en, english
from app.models import Response
from app.ui_i18n import day_month, german, pick, tr


def candidates(survey):
    """Everything that can go onto a dashboard, in the wording of the questionnaire:
    [{"title", "rows": [{"key", "label", "hint", "views": [(view, name)], "reason"}]}]."""
    from app import coding, export, loops

    lang = survey.default_language
    coded = coding.plan(survey)  # categorised open answers: a tile each (_coding_rows)
    groups = [{"title": de_en("Kennzahlen des Feldes", "Key figures of the field"),
               "rows": [{"key": f"field:{name}", "label": FIELD_NAMES["en"][name] if english() else label, "hint": "",
                         "views": [(v, view_name(v)) for v in views], "reason": ""}
                        for name, (label, views) in FIELD.items()]}]
    if survey.quotas:
        groups.append({"title": de_en("Quoten", "Quotas"), "rows": [
            {"key": f"quota:{q.id}", "label": q.name or q.code, "hint": de_en("Quote", "Quota"),
             "views": [(v, view_name(v)) for v in SHAPE_VIEWS["quota"]], "reason": ""} for q in survey.quotas]})
    listed = {i.id for i in export._items(survey)}
    for number, block in enumerate(survey.blocks, start=survey.block_start):  # as the questionnaire counts (consent: 0)
        rows = []
        for item in block.items:
            if item.id not in listed or item.kind.result_kind is None or item.type == "consent":
                continue  # page breaks, texts, images, feedback, the consent (Ralf, 2026-10-03): nothing to show; the separated section never
            shape, reason = shape_of(item)
            views = [(v, view_name(v)) for v in SHAPE_VIEWS.get(shape, ()) if not (v == "means" and _ordinal_scale(item, shape))
                     and not (v == "donut" and len((item.config or {}).get("options") or []) > MAX_RING)]
            if item.type == "geolocation" and item.kind.has_positions(item):
                views.insert(1, ("heat", view_name("heat")))  # people per square, from coordinates
            passes = export._loop_rows(item) if shape else None
            node = loops.node_of(block) if passes else None
            if node is not None and node.get("source") == "factors":
                # vignettes: one tile per factor with the mean per level, not one per vignette
                if shape in ("scale", "num") and len(item.kind.export_columns(item)) == 1:
                    rows += [{"key": f"item:{item.id}@f-{a['id']}",
                              "label": de_en("{question} · nach {factor}", "{question} · by {factor}", question=_question(item, lang), factor=label),
                              "shape": "nums", "views": [(v, view_name(v)) for v in SHAPE_VIEWS["nums"]],
                              "hint": de_en("{kind} · Mittelwert je Stufe", "{kind} · mean per level", kind=item.kind.label), "reason": ""}
                             for a, label in _factor_labels(survey, node, lang)]
                continue
            if shape == "sbs":  # one tile per column of a side-by-side matrix
                rows += [{"key": f"item:{item.id}@{ref}", "label": f"{_question(item, lang)} · {title}", "shape": col_shape,
                          "views": list(col_views), "hint": f"{item.code} · {item.kind.label}", "reason": why}
                         for ref, title, col_shape, col_views, why in sbs_columns(item, lang)]
                rows += _coding_rows(item, coded, lang)
                continue
            if passes:  # one tile per pass of the loop
                rows += [{"key": f"item:{item.id}@{rid}", "label": _pass_title(item, rid, lang), "shape": shape, "views": views,
                          "hint": f"{item.kind.label} · {loops.row_caption(block, rid, n, lang='en' if english() else 'de')}", "reason": ""} for n, rid in passes]
                continue
            rows.append({"key": f"item:{item.id}", "label": _question(item, lang), "hint": f"{item.code} · {item.kind.label}", "shape": shape,
                         "views": views, "reason": reason})
            rows += _coding_rows(item, coded, lang)
        if rows:
            title = "" if block.default_named(lang) else " ".join(((block.titles or {}).get(lang) or "").split())
            groups.append({"title": f"Block {number}" + (f": {title}" if title else ""), "rows": rows, "block": block.id})
    return groups


SPLIT_SHAPES = ("cats", "multi", "scale", "num", "nums")  # what can be compared between groups


def groupers(survey):
    """The questions a tile can be split by, {key: item}: single choice with two to eight answers, outside
    loops. Every group is then one answer of that question."""
    from app import export

    out = {}
    for item in export._items(survey):
        if item.type != "single_choice" or export._loop_rows(item):
            continue
        gone = {str(m) for m in item.kind.missing_codes(item, item.code)}
        if 2 <= sum(1 for o in item.config.get("options", []) if str(o["code"]) not in gone) <= 8:
            out[f"item:{item.id}"] = item
    return out


def _known(survey):
    """{key: row} of everything that can be picked right now."""
    return {row["key"]: row for group in candidates(survey) for row in group["rows"] if not row["reason"]}


def split_vars(survey, lang=None):
    """{key: group variable} a tile can be split by (app/groupvars.py): the questions as before ("item:<id>"),
    randomizers drawing one element, multiple choice options, numbers in classes and quota cells; no link parameters,
    a public dashboard does not tell over which sources a survey ran (concept gruppen-aufteilen-konzept.md, E7); no
    per-element split of a randomizer drawing several (its key names a block, which a copy renumbers)."""
    from app import groupvars

    out = {}
    for key, var in groupvars.all_of(survey, lang, parameters=False).items():
        if key.startswith("rand:") and key.count(":") > 1:
            continue
        out[f"item:{key}" if key.isdigit() else key] = var
    return out


def _splits_itself(by_key, var, tile_key):
    return by_key == tile_key or (var is not None and var.item is not None and tile_key == f"item:{var.item.id}")


def tiles_of(board):
    """The tiles of the dashboard as they are shown: what is gone from the questionnaire is left out, a view
    the element does not have falls back to its first one. New questions never follow by themselves."""
    known = _known(board.survey)
    split = split_vars(board.survey)
    out, seen = [], set()
    for raw in board.tiles or []:
        key = raw.get("key") if isinstance(raw, dict) else None
        if is_text(key) and key not in seen:
            seen.add(key)
            out.append(_text_tile(key, raw))
            continue
        if key not in known or key in seen:
            continue
        seen.add(key)
        views = [v for v, _ in known[key]["views"]]
        out.append({"key": key, "view": raw.get("view") if raw.get("view") in views else views[0],
                    "w": width_of(raw), "x": start_of(raw, width_of(raw)), "pct": raw.get("pct", True) is not False,
                    "n": raw.get("n", True) is not False,
                    "by": raw.get("by") if raw.get("by") in split and not _splits_itself(raw.get("by"), split[raw.get("by")], key)
                    and known[key].get("shape") in SPLIT_SHAPES else "",
                    "title": " ".join(str(raw.get("title") or "").split())[:200]})
    return out[:MAX_TILES]


def is_text(key):
    return isinstance(key, str) and TEXT_KEY.match(key) is not None


def _text_tile(key, raw):
    width = raw.get("w")
    width = width if width in dict(WIDTHS) and not isinstance(width, bool) else 12
    return {"key": key, "view": "text", "w": width, "x": start_of(raw, width), "pct": True, "n": True,
            "title": "", "kind": raw.get("kind") if raw.get("kind") in TEXT_KINDS else "text",
            "text": str(raw.get("text") or "")[:MAX_TEXT]}


def width_of(raw):
    """The width of a stored tile in twelfths; tiles from before the finer grid were half a row or a whole one."""
    width = raw.get("w")
    if isinstance(width, int) and not isinstance(width, bool) and width in dict(WIDTHS):
        return width
    return 12 if raw.get("wide") else 6


def start_of(raw, width):
    """The column a stored tile begins in (0: automatic); one that would run over the edge of the row is automatic."""
    start = raw.get("x")
    if isinstance(start, int) and not isinstance(start, bool) and start in dict(STARTS) and start + width - 1 <= 12:
        return start
    return 0


def template_tiles(survey, template):
    known = _known(survey)
    if template == "field":
        # the progress towards the sample target only where there is one: an empty bar beside the count of
        # participations reads as a second, different number (UI/UX audit 2026-10-03, U14)
        keys = ["field:count"] + (["field:target"] if survey.max_responses else []) + ["field:rate", "field:duration", "field:days"]             + [k for k in known if k.startswith("quota:")]
    elif template == "results":
        # a word cloud of free text only when picked on purpose, never by a template (decision L4)
        keys = ["field:count"] + [k for k in known if k.startswith("item:") and known[k].get("shape") != "words"]
    else:
        keys = []
    def width(key):
        if template == "field":  # the four figures in one row, the course and the quotas below over the whole row
            return 12 if key == "field:days" or key.startswith("quota:") else 3
        return 12 if key == "field:count" else 6

    return [_tile(k, known[k], width(k)) for k in keys if k in known][:MAX_TILES]


def from_form(board, form):
    """The dashboard as the designer's form names it: the order of the tiles already there (field "order"),
    ticked elements not yet there at the end, unticked ones gone; per tile its view and ticks."""
    known = _known(board.survey)
    picked = [k for k in form.getlist("pick") if k in known or is_text(k)]
    order = [k for k in form.get("order", "").split(",") if k in picked]
    keys = list(dict.fromkeys(order + picked))[:MAX_TILES]
    before = {t["key"]: t for t in tiles_of(board)}
    tiles = []
    for key in keys:
        if is_text(key):  # a new one comes with its kind only
            tiles.append(_text_tile(key, {"kind": form.get(f"kind-{key}"), "w": form.get(f"w-{key}", type=int), "x": form.get(f"x-{key}", type=int),
                                          "text": form.get(f"text-{key}", "").replace("\r\n", "\n").strip()}))
            continue
        views = [v for v, _ in known[key]["views"]]
        if f"view-{key}" in form:  # its row was on the page: the ticks are as sent
            view = form.get(f"view-{key}")
            width = form.get(f"w-{key}", type=int)
            width = width if width in dict(WIDTHS) else 6
            tiles.append({"key": key, "view": view if view in views else views[0], "w": width,
                          "x": start_of({"x": form.get(f"x-{key}", type=int)}, width),
                          "pct": form.get(f"pct-{key}") == "1", "n": form.get(f"n-{key}") == "1", "by": form.get(f"by-{key}", ""),
                          "title": " ".join(form.get(f"title-{key}", "").split())[:200]})
        else:
            tiles.append(before.get(key) or _tile(key, known[key]))
    board.tiles = tiles
    name = " ".join(form.get("name", "").split())[:200]
    if name:
        board.name = name
    if "min_tile" in form:  # from how many answers a tile shows numbers (the right column of the designer)
        chosen = form.get("min_tile", type=int)
        settings = {k: v for k, v in (board.settings or {}).items() if k != "min_tile"}
        board.settings = {**settings, "min_tile": chosen} if chosen in TILE_MINS and chosen != MIN_TILE else settings


# --- the rules for small numbers ------------------------------------------------------------------------


def merge_small(rows):
    """rows [(label, count)] without small cells: answers named 1 to MIN_CELL - 1 times become one row
    "Weitere Antworten"; while that row itself is small, the next smallest answer joins it, so that no single
    count follows from the sum. Returns (rows, merged?, ok?); ok is False if less than two rows with answers
    are left (then the tile shows nothing)."""
    small = [r for r in rows if 0 < r[1] < cell_min()]
    if not small:
        return list(rows), False, sum(1 for r in rows if r[1]) >= 1
    keep = [r for r in rows if not 0 < r[1] < cell_min()]
    rest = sum(r[1] for r in small)
    while rest < cell_min():
        named = [r for r in keep if r[1]]
        if not named:
            break
        least = min(named, key=lambda r: r[1])
        keep.remove(least)
        rest += least[1]
    out = keep + [(OTHERS, rest)]
    return out, True, rest >= cell_min() and sum(1 for r in out if r[1]) >= 2


def merge_adjacent(counts):
    """Ordered levels without small cells: a level with 1 to MIN_CELL - 1 answers is put together with the
    neighbour that has fewer answers, until no group is small. Returns [(first index, last index, count)]."""
    groups = [[i, i, c] for i, c in enumerate(counts)]
    while len(groups) > 1:
        small = [g for g in groups if 0 < g[2] < cell_min()]
        if not small:
            break
        i = groups.index(min(small, key=lambda g: g[2]))
        j = min((j for j in (i - 1, i + 1) if 0 <= j < len(groups)), key=lambda j: groups[j][2])
        a, b = sorted((i, j))
        groups[a:b + 1] = [[groups[a][0], groups[b][1], groups[a][2] + groups[b][2]]]
    return [tuple(g) for g in groups]


def _cats(item, table, lang):
    from app import results
    from app.items.base import answer_label, plain

    kind = item.kind
    # in the language of the dashboard, without the colon of "Anderes, und zwar:" (UI audit 2026-10-12, U27)
    labels = {o["code"]: answer_label(plain(o["label"])) for o in kind.options(item, lang)}
    gone = {str(m) for m in kind.missing_codes(item, item.code)}
    freq = results.frequencies(_values(table, item.code), labels, missing=gone)
    rows = [(r["label"] or r["value"], r["count"]) for r in freq["rows"]]
    others = results.other_codes(item)
    return {"shape": "cats", "labels": [r[0] for r in rows], "n": freq["n"], "_rows": rows, "nominal": kind.level(item) != "ordinal",
            "_other": [r["label"] or r["value"] for r in freq["rows"] if r["value"] in others]}


def _multi(item, table, lang):
    """Per option (label, ticked, seen): the share of an option is taken of the people who saw it and answered
    the question (0 or 1; -77: option not shown), as on the results page (results.multi_lists); n: the people
    who answered the question."""
    from app import results
    from app.items.base import answer_label, plain

    others = results.other_codes(item)
    rows, other, columns = [], [], []
    for o in item.kind.options(item, lang):
        column = f"{item.code}_{o['code']}"
        columns.append(column)
        seen = [x for x in (results._num(v) for v in _values(table, column)) if x in (0, 1)]
        label = answer_label(plain(o["label"])) or o["code"]
        rows.append((label, sum(1 for x in seen if x == 1), len(seen)))
        if str(o["code"]) in others:
            other.append(label)
    n = sum(1 for r in table if any(results._num(r.get(c)) in (0, 1) for c in columns))
    return {"shape": "multi", "labels": [r[0] for r in rows], "n": n, "_rows": rows, "_other": other,
            "partial": any(seen != n for _, _, seen in rows)}


def _states(item, table, lang):
    """People per federal state: the answers of the element Bundesland, or the states the postal codes lie in
    (never the codes themselves)."""
    from app.items import federal_state, postal_code

    names = {code: tr(lang, de, en) for code, de, en in federal_state.STATES}
    if item.type == "geolocation":  # the state of the shared position (or of the IP estimate on a refusal)
        values = [str(v) for v in _values(table, f"{item.code}_state") if v not in (None, "")]
        codes = [v for v in values if v in names]
        abroad = values.count("0")
        cells = {}
        if item.kind.has_positions(item):
            for row in table:
                lat, lon = row.get(f"{item.code}_lat"), row.get(f"{item.code}_lon")
                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and str(row.get(f"{item.code}_state")) in names:
                    key = (math.floor(lat / HEAT_LAT), math.floor(lon / HEAT_LON))
                    cells[key] = cells.get(key, 0) + 1
        rows = [(code, names[code], codes.count(code)) for code in federal_state.STATE_CODES]
        if abroad:
            rows.append(("0", tr(lang, "nicht in Deutschland", "not in Germany"), abroad))
        return {"shape": "states", "labels": [names[c] for c in federal_state.STATE_CODES], "n": len(codes) + abroad,
                "_rows": rows, "_cells": cells, "from_location": True}
    values = [v for v in _values(table, item.code) if v not in (None, "")]
    if item.type == "postal_code":
        found = [postal_code.state(v) for v in values]
        codes, abroad = [c for c in found if c in names], 0
    else:
        codes = [str(v) for v in values if str(v) in names]
        abroad = sum(1 for v in values if str(v) == federal_state.ABROAD["code"])
    rows = [(code, names[code], codes.count(code)) for code in federal_state.STATE_CODES]
    if abroad:
        rows.append(("0", tr(lang, "nicht in Deutschland", "not in Germany"), abroad))
    return {"shape": "states", "labels": [names[c] for c in federal_state.STATE_CODES], "n": len(codes) + abroad, "_rows": rows,
            "from_plz": item.type == "postal_code"}


STEPS = {"de": {"Stunde": "Stunde", "Tag": "Tag", "Monat": "Monat", "Jahr": "Jahr", "Jahrzehnt": "Jahrzehnt"},
         "en": {"Stunde": "Hour", "Tag": "Day", "Monat": "Month", "Jahr": "Year", "Jahrzehnt": "Decade"}}


def _dates(item, table, lang):
    """Dates or times in the classes of the results page (days, months, years, decades; hours)."""
    found = item.kind.results(item, table)

    def label(text):  # "14 Uhr" is German; a date reads the same everywhere
        return text if german(lang) or not text.endswith(" Uhr") else text[:-4] + ":00"

    return {"shape": "dist", "labels": [], "n": found["n"], "step": pick(STEPS, lang).get(found.get("step", ""), ""),
            "_rows": [(label(r["label"]), r["count"]) for r in found["rows"]]}


def _ranks(item, table, lang):
    """Mean rank per option (ranking), or mean share per option (constant sum)."""
    from app import results
    from app.items.base import plain

    kind = item.kind
    options = kind.options(item, lang)
    rows = []
    for o in options:
        gone = {str(m) for m in kind.missing_codes(item, f"{item.code}_{o['code']}")}  # -77: not shown to this person
        xs = [x for x in (results._num(v) for v in _values(table, f"{item.code}_{o['code']}") if results._key(v) not in gone)
              if x is not None]
        rows.append({"label": plain(o["label"]) or o["code"], "n": len(xs), "_xs": xs})
    if item.type == "ranking" and kind.partial_columns(item):
        # not everyone saw every option: raw ranks do not compare (2 of 2 is last, 2 of 5 second), normalised do
        found = kind.normalized(item, table)
        for o, row in zip(options, rows):
            row["_xs"] = found.get(o["code"], ([], 0))[0]
            row["n"] = len(row["_xs"])
        return {"shape": "nums", "unit": "", "bounds": (0, 1), "rows": rows, "_scale": True, "ranks": "norm",
                "note": tr(lang, "Normierter Rang: 1 = oben, 0 = unten (nicht alle sahen alle Optionen)",
                           "Normalised rank: 1 = top, 0 = bottom (not everyone saw every option)"),
                "n": max((r["n"] for r in rows), default=0)}
    if item.type == "ranking":
        return {"shape": "nums", "unit": "", "bounds": (1, max(len(options), 2)), "rows": rows, "_scale": True, "ranks": True,
                "note": tr(lang, "Mittlerer Rang; 1 = oben", "Mean rank; 1 = top"), "n": max((r["n"] for r in rows), default=0)}
    if kind.personal(item):
        # every person distributed their own total: the shares in percent of it
        totals = [results._num(v) for v in _values(table, kind.total_column(item))]
        for o, row in zip(options, rows):
            column = f"{item.code}_{o['code']}"
            gone = {str(m) for m in kind.missing_codes(item, column)}  # -77: not shown to this person, no amount
            amounts = [results.valid_number(v, gone) for v in _values(table, column)]
            row["_xs"] = [100 * a / t for a, t in zip(amounts, totals) if a is not None and t]
            row["n"] = len(row["_xs"])
        return {"shape": "nums", "unit": "%", "bounds": (0, 100), "rows": rows, "_scale": True,
                "note": tr(lang, "Mittlerer Anteil an der persönlichen Summe", "Mean share of the personal total"),
                "n": max((r["n"] for r in rows), default=0)}
    total = item.config.get("total", 100)
    unit = {"percent": "%", "currency": item.config.get("currency", "EUR")}.get(item.config.get("format"), "")
    return {"shape": "nums", "unit": unit, "bounds": (0, total), "rows": rows, "_scale": True,
            "note": tr(lang, "Mittlerer Anteil; alle zusammen ergeben {total}", "Mean share; all together make {total}",
                       total=kind.amount(item, total, lang)), "n": max((r["n"] for r in rows), default=0)}


def _maxdiff(item, table, lang):
    """Best-worst scores from the counts (no model is estimated for a dashboard): the share of the times a
    statement was picked as best minus the share as worst, of the times it was shown."""
    from app import maxdiff_analysis
    from app.items.base import plain

    labels = [plain(x) for x in item.kind.labels(item, lang)]
    design = (item.config.get("design") or {}).get("versions") or []
    answers, _ = maxdiff_analysis.from_table(item, table)
    shown, best, worst, persons = maxdiff_analysis.counts(len(labels), design, answers)
    scores = maxdiff_analysis.scores(shown, best, worst)[0]
    return {"shape": "score", "labels": labels, "n": persons,
            "rows": [{"label": label, "_score": None if scores[i] != scores[i] else float(scores[i])} for i, label in enumerate(labels)]}


def _paired(item, table, lang):
    """Scores of a paired comparison from the counts (no model for a dashboard; concept paarvergleich-konzept.md, E13):
    the share of the comparisons an option won minus the share it lost."""
    from app import paired_analysis
    from app.items.base import plain

    labels = [plain(x) for x in item.kind.labels(item, lang)]
    persons = paired_analysis.persons_from_table(item, table)
    comp, win, loss, _, _, _, people = paired_analysis.counts(len(labels), persons)
    return {"shape": "score", "labels": labels, "n": people, "pairs": True,
            "rows": [{"label": label, "_score": float((win[i] - loss[i]) / comp[i]) if comp[i] else None}
                     for i, label in enumerate(labels)]}


def _conjoint(item, table, lang):
    """Attributes and levels. The shares per level are counts: choice-based, how often a level was chosen when it
    was shown; adaptive, the share of people whose preferred variant has the level. The importance of the attributes
    comes from the same model as on the results page (_importances): the span of the counted shares gave other
    numbers under the same name (Ralf, 2026-10-12: 37/24/15/13/11 % there, 41/26/14/10/9 % here)."""
    from app.items.base import plain

    if item.type == "cva":
        return _cva(item, table, lang)
    if item.type == "acbc":
        from app import acbc_analysis

        answers = acbc_analysis.answers_for(item, table)
        found = acbc_analysis.analyse(item, answers, lang)
        n, key, count = found["winners"], "win_share", "wins"
        basis = tr(lang, "Anteil der Personen, deren bevorzugte Variante die Stufe hat",
                   "Share of people whose preferred variant has the level")
    else:
        from app import cbc_analysis
        from app.choice_model import responses_for

        answers = responses_for(item, table)
        found = cbc_analysis.counts(item, answers, lang)
        n, key, count = found["persons"], "share", "chosen"
        basis = tr(lang, "Anteil der Wahlen, wenn die Stufe gezeigt wurde", "Share of choices when the level was shown")
    importances, method = _importances(item, table, answers, lang)
    by_code = {row["code"]: row["importance"] for row in importances if row.get("code")}
    # _count: the choices (choice-based) or people (adaptive) a share rests on, for the rule on small cells
    attributes = [{"label": plain(a["label"]), "importance": by_code.get(a["code"]),
                   "levels": [{"label": plain(lv["label"]), "_share": lv[key], "_count": lv[count]} for lv in a["levels"]]}
                  for a in found["attributes"]]
    return {"shape": "conjoint", "n": n, "basis": basis, "attributes": attributes, "_whole": item.type == "acbc",
            "importances": [{"label": row["label"], "importance": row["importance"]} for row in importances],
            "method": method}


def _cva(item, table, lang):
    """A ratings-based conjoint (app/cva_analysis.py): the importances as on the results page (mean of the importances
    per person) and per level the share of people for whom it is the best level of its attribute (ties split), so the
    bars read as shares like those of CBC."""
    from app import cva_analysis
    from app.choice_model import responses_for
    from app.items.base import plain

    found = cva_analysis.analyse(item, responses_for(item, table), lang)
    model = found.get("model")
    people = [pw for _, pw in found.get("person_pw") or []]
    n = len(people)
    attributes, importances = [], []
    for j, a in enumerate(item.kind.attributes(item, lang)):
        best = [0.0] * len(a["level_labels"])
        for pw in people:
            top = max(pw[j])
            winners = [k for k, x in enumerate(pw[j]) if x >= top - 1e-9]
            for k in winners:
                best[k] += 1 / len(winners)
        importance = model["attributes"][j]["importance"] if model else None
        attributes.append({"label": plain(a["label"]), "importance": importance,
                           "levels": [{"label": plain(label), "_share": best[k] / n if n else None, "_count": round(best[k])}
                                      for k, label in enumerate(a["level_labels"])]})
        importances.append({"label": plain(a["label"]), "importance": importance})
    return {"shape": "conjoint", "n": n, "attributes": attributes, "_whole": True, "method": "persons" if model else None,
            "basis": tr(lang, "Anteil der Personen, für die die Stufe die beste ihres Merkmals ist",
                        "Share of people for whom the level is the best of its attribute"),
            "importances": sorted(importances, key=lambda r: -(r["importance"] or 0))}


def _importances(item, table, answers, lang):
    """The importance of the attributes as the results page shows it, ([{"code", "label", "importance"}], method):
    from the hierarchical Bayes estimation where it is current for these answers ("hb", the mean of the individual
    importances), else from the aggregate logit ("logit"); ([], None) without a model. The price, when it enters
    linearly, has a row of its own (code: the price attribute, or None for the summed price of ACBC). An attribute
    whose span the data do not set has the importance None, as on the results page."""
    from app import conjoint_hb
    from app.items.base import plain

    attrs = item.kind.attributes(item, lang)
    model, method = conjoint_hb.view(item, table, lang).get("result"), "hb"
    if model is None:
        if item.type == "acbc":
            from app import acbc_logit

            model = acbc_logit.estimate(item, answers, lang)
        else:
            from app import cbc_analysis

            model = cbc_analysis.estimate(item, answers, lang, cbc_analysis.table_sets(item, table, answers))
        method = "logit"
    if not model:
        return [], None
    rows = [{"code": a["code"], "label": plain(a["label"]), "importance": a["importance"]} for a in model["attributes"]]
    price = model.get("price")
    if price and price.get("importance") is not None:
        pa = item.kind.price_attribute(item) if item.type == "conjoint" else None
        rows.append({"code": attrs[pa]["code"] if pa is not None else None,
                     "label": plain(attrs[pa]["label"]) if pa is not None else tr(lang, "Preis", "Price"),
                     "importance": price["importance"]})
    return sorted(rows, key=lambda r: (r["importance"] is None, -(r["importance"] or 0))), method


PRICE_POINTS = {
    "de": {"OPP": ["Optimaler Preis", "gleich viele finden ihn zu billig wie zu teuer"],
           "IPP": ["Indifferenzpreis", "gleich viele finden ihn günstig wie teuer"],
           "PMC": ["Untere Grenze", "darunter wirkt das Produkt zu billig"],
           "PME": ["Obere Grenze", "darüber wirkt das Produkt zu teuer"]},
    "en": {"OPP": ["Optimal price", "as many find it too cheap as too expensive"],
           "IPP": ["Indifference price", "as many find it cheap as expensive"],
           "PMC": ["Lower limit", "below it the product seems too cheap"],
           "PME": ["Upper limit", "above it the product seems too expensive"]}}


def _prices(item, table, lang):
    """The four price points of the price sensitivity meter. The curves themselves are not shown: they run
    through every single price that was named, the lowest and the highest included."""
    from app import psm_analysis, results, units

    found = psm_analysis.analyse(table, item)
    places = item.config.get("decimals", 2)

    def text(value):
        """The price with the decimals of the question, every point alike (84,50 € next to 92,67 €)."""
        if value is None:
            return None
        currency = item.config.get("currency")
        if currency in units.CURRENCIES:
            return units.fmt(results._round_half_up(value, places), "currency", currency, "", money, places)
        return units.fmt(results._round_half_up(value, places), "number", "EUR", item.text("unit", lang) or "", money, places)

    money = "en" if _point() else "de"  # how units.fmt writes numbers: 1,299.50 or 1.299,50
    points = found.get("points") or {}
    return {"shape": "points", "n": found["n"],
            "rows": [{"key": key, "label": label, "hint": hint, "_value": text(points.get(key))}
                     for key, (label, hint) in pick(PRICE_POINTS, lang).items()]}


def _scale_rows(item, lang):
    """[(label, column, reversed?)] and the labels of the levels, lowest first."""
    from app.items.base import plain

    kind = item.kind
    if item.type == "likert":
        checks = kind.check_codes(item)
        rows = [(plain(s["text"]), s["export_code"], bool(s.get("reverse"))) for s in kind.statements(item, lang)
                if s["export_code"] not in checks]
        return rows, [plain(x) for x in kind.labels(item, lang)]
    if item.type == "semantic":
        points = item.config["points"]
        rows = [(f"{plain(p['left'])} – {plain(p['right'])}", p["export_code"], bool(p.get("reverse"))) for p in kind.pairs(item, lang)]
        return rows, [str(n) for n in range(1, points + 1)]
    return [("", item.code, False)], [str(v) for v in kind.scale_values(item, item.code)]


def _ordinal_scale(item, shape):
    """A scale set to "ordinal" in the editor: medians, never mean and SD (as on the results page)."""
    return shape == "scale" and item.kind.level(item) == "ordinal"


def _median(counts, labels, first=1):
    """(position, text) of the median of counts per level in scale order, by the rule of the results page
    (results._quantile): the first level up to which at least half of the answers lie; when exactly half lie up
    to it, the median falls between it and the next level that was chosen, and both are named. counts may be
    groups of levels [(first index, last index, count)]; a group is named by its first and last level."""
    groups = [g if isinstance(g, tuple) else (i, i, g) for i, g in enumerate(counts)]
    n = sum(c for _, _, c in groups)
    if not n:
        return None, None

    def name(i):
        return labels[i] if i < len(labels) and labels[i] else str(first + i)

    def text(g):
        return name(g[0]) if g[0] == g[1] else tr(_lang(), "{a} bis {b}", "{a} to {b}", a=name(g[0]), b=name(g[1]))

    total = 0
    for j, g in enumerate(groups):
        total += g[2]
        if g[2] and total >= 0.5 * n:
            upper = next((h for h in groups[j + 1:] if h[2]), None) if 2 * total == n else None
            if upper is None:
                return first + (g[0] + g[1]) / 2, text(g)
            return first + (g[0] + g[1] + upper[0] + upper[1]) / 4, f"{text(g)} – {text(upper)}"
    return None, None


def _scale(item, table, lang):
    from app import results

    kind = item.kind
    rows, levels = _scale_rows(item, lang)
    k = len(levels)
    first = 1 if item.type != "rating" else int(kind.scale_values(item, item.code)[0])
    out = []
    for label, column, reverse in rows:
        gone = {str(m) for m in kind.missing_codes(item, column)}
        numbers = [results._num(v) for v in _values(table, column) if v not in (None, "") and results._key(v) not in gone]
        numbers = [x for x in numbers if x is not None and float(x).is_integer() and first <= x < first + k]
        counts = [0] * k
        for x in numbers:
            # a reversed statement is stored recoded; the levels are shown as they were answered
            index = int(x) - first
            counts[k - 1 - index if reverse else index] += 1
        out.append({"label": label, "variable": column, "n": len(numbers), "_counts": counts, "reverse": reverse,
                    "_mean": statistics.fmean(numbers) if numbers else None,
                    "_sd": statistics.stdev(numbers) if len(numbers) > 1 else None})
    return {"shape": "scale", "levels": levels, "first": first, "rows": out, "n": max((r["n"] for r in out), default=0),
            "ordinal": _ordinal_scale(item, "scale")}


def _nice(step):
    if step <= 0:
        return 1
    power = 10 ** math.floor(math.log10(step))
    return next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= step)


def _classes(numbers, scale=False):
    """([(from, to, count)], places) for a column of numbers: the classes of the results page's histogram between
    the 10th and the 90th percentile (single whole values as from = to) and an open class at each end (from or to is
    None), so the lowest and the highest answer are never named. scale: the answers lie on a scale the page offers (stars, a slider); with few whole values each
    is a class (from = to)."""
    xs = sorted(numbers)
    if not xs:
        return [], 0
    distinct = sorted(set(xs))
    if scale and len(distinct) <= 12 and all(float(x).is_integer() for x in distinct):
        return [(v, v, xs.count(v)) for v in distinct], 0
    lo, hi = xs[int(0.1 * (len(xs) - 1))], xs[int(math.ceil(0.9 * (len(xs) - 1)))]
    from collections import Counter

    from app import units

    # the classes of the histogram on the results page (results.histogram: about twelve of a round width over the
    # whole range), so both show the same bars (Ralf, 2026-10-06); the ends stay open below the 10th and above the
    # 90th percentile, so the lowest and the highest answer are never named on a dashboard
    every_lo, every_hi = xs[0], xs[-1]
    single = all(float(x).is_integer() for x in xs) and every_hi - every_lo <= 30
    step = 1 if single else (_nice((every_hi - every_lo) / 12) if every_hi > every_lo else 1)
    # edges as start + i·step on the step's places, not added up (0.2 + 0.2 + 0.2 is 0.6000000000000001), and
    # every value sorted in with the tolerance of units.class_of: a value on an edge is in the upper class (R-3)
    places = units.decimals(step) + 1
    start = units.round_half_up(math.floor(lo / step + 1e-9) * step, places)
    edges = [start]
    while edges[-1] < hi and len(edges) < 32:
        edges.append(units.round_half_up(start + len(edges) * step, places))
    found = Counter(min(max(units.class_of(x, start, step), -1), len(edges) - 1) for x in xs)
    out = [(None, edges[0], found[-1])]
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        out.append((a, a, found[i]) if single else (a, b, found[i]))
    out.append((edges[-1], None, found[len(edges) - 1]))
    return [row for i, row in enumerate(out) if row[2] or 0 < i < len(out) - 1], 0 if float(step).is_integer() else 2


def _class_label(low, high, places):
    """The label of a class, or of several put together (from the first one's start to the last one's end)."""
    if low is None:
        return tr(_lang(), "unter {x}", "under {x}", x=_num(high, places))
    if high is None:
        return tr(_lang(), "ab {x}", "{x} and more", x=_num(low, places))
    return tr(_lang(), "{a} bis unter {b}", "{a} to under {b}", a=_num(low, places), b=_num(high, places))


def classes(numbers, scale=False):
    """[(label, count)] of _classes()."""
    bins, places = _classes(numbers, scale)
    return [(_num(low, 0) if low == high else _class_label(low, high, places), count) for low, high, count in bins]


def _words(item, table, lang):
    """The mentions of short answers: free text as typed (up to WORDS_MAX words, case and spaces do not count),
    associations word by word. n: the people who answered. The rule of small numbers comes in _reduce."""
    from collections import Counter

    if item.type == "association":
        columns = [f"{item.code}_{k}" for k in item.kind.slots(item)]
        key = item.kind.normalize  # as the word list of the results page: NFC, without punctuation ("Natur." = "Natur")
    else:
        columns = [item.code]
        key = str.casefold
    counts, shown, n = Counter(), {}, 0
    for row in table:
        said = [" ".join(str(row.get(c) or "").split()) for c in columns]
        said = [t for t in said if t]
        if not said:
            continue
        n += 1
        for text in dict.fromkeys(key(t) for t in said if len(t.split()) <= WORDS_MAX and len(t) <= 60):
            if text:
                counts[text] += 1
        for t in said:
            shown.setdefault(key(t), t)
    return {"shape": "words", "n": n, "_rows": [(shown[k], c) for k, c in counts.most_common()]}


def _highlight(item, table, lang):
    """Text highlighting: per word of the text the people who marked it, per category (items.highlight.results).
    n: the people who marked anything. The rule of small numbers comes in _reduce."""
    res = item.kind.results(item, table, lang)
    return {"shape": "highlight", "n": res["persons"], "cats": [{"label": label, "color": color} for _, label, color in res["cats"]],
            "_tokens": res["tokens"]}


def _numbers(item, table, lang):
    from app import results
    from app.items.base import plain

    kind = item.kind
    if item.type == "slider":
        texts = item.text("rows", lang) or {}
        columns = [(plain(texts.get(r["id"], "")), kind.row_code(item, i, r)) for i, r in enumerate(item.config.get("rows", []))]
        bounds = (item.config.get("min", 0), item.config.get("max", 100))
        # the unit as the results page names it: €, % or the free unit text ("Minuten"; V70)
        unit = kind.unit_label(item, lang)
    else:
        columns = [("", item.code)]
        bounds = kind.bounds(item) if item.type == "rating" else None
        # the text after the box of a typed number is its unit, in the language of the dashboard
        unit = (item.text("suffix", lang) or "").strip() if item.type == "text_input" else kind.result_column(item, item.code)[1] or ""
    rows = []
    for label, column in columns:
        gone = {str(m) for m in kind.missing_codes(item, column)}  # "weiß nicht" (-98) is no number of the scale
        xs = [x for x in (results.valid_number(v, gone) for v in _values(table, column)) if x is not None]
        rows.append({"label": label, "n": len(xs), "_xs": xs})
    return {"shape": "nums" if len(rows) > 1 else "num", "unit": unit, "bounds": bounds, "rows": rows, "_scale": bounds is not None,
            "n": max((r["n"] for r in rows), default=0)}


def _factor_labels(survey, node, lang):
    """[(factor, its name in `lang`)] of a vignette loop (app/vignettes.py)."""
    from app import vignettes

    f = vignettes.factors(survey, node)
    names = (f.t(lang).get("attributes") or {})
    return [(a, names.get(a["id"]) or a["code"]) for a in node.get("attributes") or []]


def _level_means(item, aid, table, lang):
    """The answers to a question of a vignette loop per level of one factor, as several numbers (shape "nums"): every
    rating a person gave counts at the level its vignette had."""
    from app import loops, results, vignettes

    node = loops.node_of(item.block)
    if not vignettes.is_vignettes(node) or not vignettes.plan_current(item.block.survey, node):
        return {"shape": "nums", "unit": "", "bounds": None, "rows": [], "_scale": False, "n": 0}
    attrs = node.get("attributes") or []
    j = next((n for n, a in enumerate(attrs) if a["id"] == aid), None)
    if j is None:
        return {"shape": "nums", "unit": "", "bounds": None, "rows": [], "_scale": False, "n": 0}
    f = vignettes.factors(item.block.survey, node)
    labels = (f.t(lang).get("levels") or {})
    plan = node.get("plan") or {}
    number = {r["id"]: n for n, r in enumerate(node.get("rows") or [], start=1)}
    level_of = {number[rid]: p[j] for rid, p in zip(plan.get("ids") or [], plan.get("profiles") or []) if rid in number}
    values = [[] for _ in attrs[j]["levels"]]
    people = [set() for _ in attrs[j]["levels"]]
    for r in table:
        for part in str(r.get(f"{node['code']}_order") or "").split("|"):
            if part.isdigit() and int(part) in level_of:
                x = results._num(r.get(f"{item.code}_{part}"))
                if x is not None:
                    values[level_of[int(part)]].append(x)
                    people[level_of[int(part)]].add(r.get("resp_id"))
    bounds = item.kind.bounds(item) if item.type == "rating" else None
    rows = [{"label": labels.get(lv["id"], "?"), "n": len(people[li]), "_xs": values[li]}
            for li, lv in enumerate(attrs[j]["levels"])]
    return {"shape": "nums", "unit": "", "bounds": bounds, "rows": rows, "_scale": bounds is not None,
            "n": min((r["n"] for r in rows), default=0)}


def _reduce(data):
    """The numbers of a tile as a link may show them (rules 2 to 4 of the module text); what the rules do not
    let through is not part of the result."""
    shape, n = data["shape"], data["n"]
    data["few"] = n < tile_min()
    other = set(data.pop("_other", ()))  # options with a text field ("Anderes, und zwar"): last when sorted
    if shape == "cats":
        rows = data.pop("_rows")
        if not data["few"]:
            rows, data["merged"], ok = merge_small(rows)
            data["few"] = not ok
        if not data["few"]:
            data["rows"] = [{"label": label, "count": count, "share": count / n, **({"other": True} if label in other else {})}
                            for label, count in rows]
            if data["merged"]:  # the last row: the answers put together
                data["rows"][-1].update(label=tr(_lang(), "Weitere Antworten", "Other answers"), rest=True)
    elif shape == "words":  # only words named MIN_CELL times or more; the rest is left out, not put together
        rows = data.pop("_rows")
        kept = [(label, count) for label, count in rows if count >= max(MIN_CELL, cell_min())][:CLOUD_MAX]
        data["dropped"] = len(kept) < len(rows)
        if not data["few"]:
            data["few"] = not kept
        if not data["few"]:
            top = kept[0][1]
            data["rows"] = [{"label": label, "count": count, "share": count / n, "size": count / top} for label, count in kept]
    elif shape == "highlight":  # a word marked by fewer than MIN_CELL people counts as unmarked; the text always stays
        tokens = data.pop("_tokens")
        data["tokens"] = [{"text": t["text"]} if t.get("n") is None or data["few"] else
                          {"text": t["text"], "shares": [c / n if c >= max(MIN_CELL, cell_min()) else 0 for c in t["counts"]],
                           "counts": [c if c >= max(MIN_CELL, cell_min()) else 0 for c in t["counts"]]} for t in tokens]
        data["word"] = [t.get("n") is not None for t in tokens]
        if not data["few"]:
            data["top"] = [[(t["text"], t["counts"][i]) for t in sorted((t for t in data["tokens"] if t.get("counts") and t["counts"][i]),
                                                                       key=lambda t: -t["counts"][i])[:8]] for i in range(len(data["cats"]))]
            data["dropped"] = any(0 < c < max(MIN_CELL, cell_min()) for t in tokens if t.get("n") is not None for c in t["counts"])
    elif shape == "states":
        rows = data.pop("_rows")
        cells = data.pop("_cells", None)
        if cells is not None and not data["few"]:
            data["heat"], data["heat_left"] = _heat(cells)
        if not data["few"]:
            kept, merged, ok = merge_small([(code, count) for code, _, count in rows])
            data["few"] = not ok
        if not data["few"]:
            counts = dict(kept)
            data["rows"] = [{"code": code, "label": label, "count": counts[code], "share": counts[code] / n}
                            for code, label, _ in rows if code in counts]
            if merged:  # which states were put together is said, how many answers each has is not
                rest = [label for code, label, _ in rows if code not in counts]
                data["rest"] = {"count": counts[OTHERS], "share": counts[OTHERS] / n, "labels": rest,
                                "codes": [code for code, _, _ in rows if code not in counts]}
    elif shape == "dist":
        rows = data.pop("_rows")
        if not data["few"]:
            groups = merge_adjacent([c for _, c in rows])
            data["few"] = len(groups) < 2
            if not data["few"]:
                data["merged"] = any(a != b for a, b, _ in groups)
                data["rows"] = [{"label": rows[a][0] if a == b else _join(rows[a][0], rows[b][0]), "count": c, "share": c / n}
                                for a, b, c in groups]
    elif shape == "score":
        for row in data["rows"]:
            score = row.pop("_score")
            if not data["few"]:
                row["score"] = score
        if not data["few"]:
            data["rows"] = sorted((r for r in data["rows"] if r["score"] is not None), key=lambda r: -r["score"])
    elif shape == "conjoint":
        whole = data.pop("_whole", False)
        for attribute in data["attributes"]:
            counts = [level.pop("_count", None) for level in attribute["levels"]]
            shares = [level.pop("_share") for level in attribute["levels"]]
            if data["few"]:
                continue
            # no small cells: a share that rests on 1 to MIN_CELL - 1 choices or people is not shown. Where the
            # shares of an attribute add up to the whole (adaptive: every preferred variant has one level of
            # it), a single hidden share would follow from the others: the next smallest is hidden with it
            hidden = {i for i, c in enumerate(counts) if c is not None and 0 < c < cell_min()}
            while whole and hidden and sum(counts[i] for i in hidden) < cell_min():
                named = [i for i, c in enumerate(counts) if i not in hidden and c]
                if not named:
                    break
                hidden.add(min(named, key=lambda i: counts[i]))
            for i, level in enumerate(attribute["levels"]):
                level["share"] = None if i in hidden else shares[i]
                if i in hidden:
                    level["small"] = True
        if data["few"]:  # the importances rest on the same answers as the shares: none without them
            data["importances"] = []
            for attribute in data["attributes"]:
                attribute["importance"] = None
    elif shape == "points":
        for row in data["rows"]:
            value = row.pop("_value")
            if not data["few"]:
                row["value"] = value
    elif shape == "multi":
        rows = data.pop("_rows")
        if data.pop("_drop_unseen", False):  # side-by-side matrix: a row nobody answered has no share
            rows = [r for r in rows if r[2]]
        if not data["few"]:
            data["rows"] = []
            for label, count, seen in rows:
                low, high = 0 < count < cell_min(), 0 < seen - count < cell_min()  # nearly nobody, nearly everybody
                data["rows"].append({"label": label, "small": low or high, "high": high, **({"other": True} if label in other else {}),
                                     **({} if low or high or not seen else {"count": count, "share": count / seen})})
    elif shape == "scale":
        for row in data["rows"]:
            counts, mean, sd = row.pop("_counts"), row.pop("_mean"), row.pop("_sd")
            row["few"] = row["n"] < tile_min()
            if row["few"]:
                row.pop("n")
                continue
            groups = merge_adjacent(counts)
            row["few"] = len(groups) < 2
            if row["few"]:
                row.pop("n")
                continue
            row["segs"] = [{"a": a, "b": b, "count": c, "share": c / row["n"]} for a, b, c in groups]
            if data.get("ordinal"):
                # the median of the levels as shown (a reversed statement in its recoded order, as the results
                # page and the codebook read it); no mean and SD on an ordinal scale
                levels = data["levels"]
                k = len(counts)
                order = [(k - 1 - b, k - 1 - a, c) for a, b, c in reversed(groups)] if row.get("reverse") else groups
                row["median"] = _median(order, levels[::-1] if row.get("reverse") else levels, data.get("first", 1))[1]
                mean = sd = None
            elif any(a != b for a, b, _ in groups):
                # levels were put together: the exact mean next to them would give the single levels back
                # (n times the mean is the sum). Mean and SD are taken from the levels as shown
                k, first = len(counts), data.get("first", 1)
                values = [first + ((k - 1 - (a + b) / 2) if row.get("reverse") else (a + b) / 2) for a, b, _ in groups]
                mean = sum(v * c for v, (_, _, c) in zip(values, groups)) / row["n"]
                sd = math.sqrt(sum(c * (v - mean) ** 2 for v, (_, _, c) in zip(values, groups)) / (row["n"] - 1))
            row["mean"], row["sd"] = mean, sd
            data["merged"] = data.get("merged") or any(a != b for a, b, _ in groups)
        data["few"] = data["few"] or all(r["few"] for r in data["rows"])
        data["n"] = max((r.get("n", 0) for r in data["rows"]), default=0) if not data["few"] else None
    else:
        scale = data.pop("_scale")
        for row in data["rows"]:
            xs = row.pop("_xs")
            row["few"] = row["n"] < tile_min()
            if row["few"]:
                row.pop("n")
                continue
            row["mean"], row["median"] = statistics.fmean(xs), statistics.median(xs)
            row["sd"] = statistics.stdev(xs) if len(xs) > 1 else None
            # decimals per measure as on the results page (V71): none from 1.000 on, medians of whole answers none
            from app import units

            # whole answers (age, minutes): one decimal is enough outside (UI/UX audit 2026-10-09, X65: "57,3 Jahre")
            row["places"] = units.stat_places([row["mean"]])
            if row["places"] and units.whole(xs):
                row["places"] = 1
            row["md_places"] = (0 if row["median"] == int(row["median"]) else min(row["places"], 1)) if units.whole(xs) else row["places"]
            if shape == "num":
                bins, places = _classes(xs, scale)
                groups = merge_adjacent([c for _, _, c in bins])
                if len(groups) > 1:
                    data["bins"] = [{"label": _span(bins[a], bins[b], places), "count": c, "share": c / row["n"]} for a, b, c in groups]
    if data["few"]:
        data["n"] = None
    return data


def _join(first, last):
    """The label of periods put together: ranges ("1910–1919", "1920–1929") from the start of the first to the end of
    the last ("1910–1929"), not "1910–1919 bis 1920–1929"; single periods (days, months, hours) "a bis b"."""
    if "–" in first and "–" in last:
        return first.split("–")[0] + "–" + last.split("–")[-1]
    return tr(_lang(), "{a} bis {b}", "{a} to {b}", a=first, b=last)


def _span(first, last, places):
    """The label of the classes from `first` to `last` put together: single values 2 and 4 give "2 bis 4", ranges
    run from the start of the first to the end of the last. A single value next to an open end (audit 2026-10-06,
    E-5): "2" and "ab 3" give "ab 2", "unter 5" to "7" gives "bis 7" (it holds the 7s)."""
    if last[1] is None:  # open at the top: from the start of the first
        return _class_label(first[0], None, places) if first[0] is not None else ""
    if last[0] is not None and last[0] == last[1]:  # ends on a single value of a scale
        if first is last:
            return _num(first[0], 0)
        if first[0] is None:
            return tr(_lang(), "bis {x}", "{x} or less", x=_num(last[1], 0))
        return tr(_lang(), "{a} bis {b}", "{a} to {b}", a=_num(first[0], 0), b=_num(last[1], 0))
    return _class_label(first[0], last[1], places)


def _groups(make, item, by, table, lang):
    """The answers of `item` apart for every group of the group variable `by` (app/groupvars.py): [{"label", "raw",
    "rest"?}], raw as the maker gives it for the people of that group. A group with fewer than MIN_TILE answers is
    put together with the other small ones ("Weitere Gruppen"), and that group with the next smallest one while it is
    small itself, so no small group follows by subtracting. None if fewer than two groups are left."""
    by.prepare(table)

    def group(label, codes, rest=False):
        people = table.keep(lambda r: by.member(r) in codes)
        return {"label": label, "codes": codes, "raw": make(item, people, lang), "rest": rest}

    groups = [group(label, {code}) for code, label in by.groups]
    groups = [g for g in groups if g["raw"]["n"]]
    while len(groups) > 1:
        small = [g for g in groups if g["raw"]["n"] < tile_min()]
        if not small:
            break
        others = [g for g in groups if g not in small]
        codes = set().union(*(g["codes"] for g in small))
        if len(small) == 1 and others:  # a small group alone (also the one just put together): the next smallest joins
            least = min(others, key=lambda g: g["raw"]["n"])
            others.remove(least)
            codes |= least["codes"]
        groups = others + [group(tr(lang, "Weitere Gruppen", "Other groups"), codes, rest=True)]
    return groups if len(groups) > 1 and all(g["raw"]["n"] >= tile_min() for g in groups) else None


def _common_groups(counts_list):
    """[(first index, last index)] of ordered levels: the coarsest grouping in which every grouping merge_adjacent
    makes of one of the count lists (the tile of everyone, each group) lies, so no list has a small cell in it."""
    k = len(counts_list[0]) if counts_list else 0
    joined = [False] * max(k - 1, 0)  # joined[i]: level i and i + 1 are in one group
    for counts in counts_list:
        for a, b, _ in merge_adjacent(counts):
            for i in range(a, b):
                joined[i] = True
    out, start = [], 0
    for i in range(k):
        if i == k - 1 or not joined[i]:
            out.append((start, i))
            start = i + 1
    return out


def _shown(mean):
    """A mean of a split tile as precise as its label (one decimal), so the position of its bar tells no more."""
    from app import units

    return units.round_half_up(mean, 1)


def _compare(shape, groups, lang, whole=None):
    """What a tile shows of its groups, by the same rules as for everyone together: no answer of a group named 1
    to MIN_CELL - 1 times (answers are put together until none is, the same ones in every group, so the groups
    stay comparable), means only from MIN_TILE answers on. None if nothing is left to compare. whole: the raw
    figures of everyone (a scale), whose levels put together stay together in the groups as well."""
    from app.constructs import _pseudonym

    raws = [g["raw"] for g in groups]
    # An opaque identifier of the same group/merged groups across languages and refreshes. Public outputs can
    # use it instead of restricted source labels without disclosing which small groups were combined.
    out = {"groups": [{"label": g["label"], "n": g["raw"]["n"],
                       **({"id": _pseudonym("G", "dashboard-group:" + json.dumps(sorted(map(str, g["codes"]))), 12)}
                          if "codes" in g else {})}
                      for g in groups], "merged_groups": any(g["rest"] for g in groups)}
    if shape == "cats":
        # an answer that is no option any more is a row only in the groups it occurs in: every group gets every row
        labels = list(dict.fromkeys(label for raw in raws for label, _ in raw["_rows"]))
        counts = []
        for raw in raws:
            found = {}
            for label, count in raw["_rows"]:
                found[label] = found.get(label, 0) + count
            counts.append([found.get(label, 0) for label in labels])

        def total(i):
            return sum(group[i] for group in counts)

        together = set()
        while True:
            apart = [i for i in range(len(labels)) if i not in together]
            small = [i for i in apart if any(0 < group[i] < cell_min() for group in counts)]
            if small:
                together.add(min(small, key=total))
            elif together and apart and any(0 < sum(group[i] for i in together) < cell_min() for group in counts):
                together.add(min(apart, key=total))
            else:
                break
        if raws[0].get("nominal"):  # answers without an order: the most frequent first
            from app.results import residual

            other = set(raws[0].get("_other", ()))
            apart = sorted(apart, key=lambda i: (labels[i] in other or residual(labels[i]), -total(i)))
        if len(apart) + bool(together) < 2:
            return None
        out["cats"] = [labels[i] for i in apart] + ([tr(lang, "Weitere Antworten", "Other answers")] if together else [])
        out["shares"] = [[group[i] / raw["n"] for i in apart] + ([sum(group[i] for i in together) / raw["n"]] if together else [])
                         for group, raw in zip(counts, raws)]
        out["merged"] = bool(together)
    elif shape == "multi":
        rows = [{"label": label, "shares": [], "seen": 0} for label, _, _ in raws[0]["_rows"]]
        for raw in raws:
            for row, (_, count, seen) in zip(rows, raw["_rows"]):  # each option on the people who saw it
                hidden = not seen or 0 < count < cell_min() or 0 < seen - count < cell_min()
                row["shares"].append(None if hidden else count / seen)
                row["seen"] += seen
        if raws[0].get("_drop_unseen"):  # side-by-side matrix: a row no group answered is left out
            rows = [r for r in rows if r["seen"]]
        for row in rows:
            row.pop("seen")
        from app.results import residual

        other = set(raws[0].get("_other", ()))
        out["rows"] = sorted(rows, key=lambda r: (r["label"] in other or residual(r["label"]), -sum(s or 0 for s in r["shares"])))
        out["lo"], out["hi"] = 0, 1
    else:
        ordinal = shape == "scale" and raws[0].get("ordinal")
        rows = [{"label": row["label"], **({"variable": row["variable"]} if "variable" in row else {}),
                 "reverse": bool(row.get("reverse")), "means": [], **({"texts": []} if ordinal else {})}
                for row in raws[0]["rows"]]
        # a scale: levels put together in the tile of everyone (whole) or in any group stay together in every
        # group, and means and medians come from the levels as shown, as in _reduce; else n times the exact mean
        # of each group would give back a level the tile of everyone hides (B-1 of the audit 2026-10-05)
        spans = [_common_groups([own["_counts"] for own in ([whole["rows"][r]] if whole else []) + [raw["rows"][r] for raw in raws]])
                 for r in range(len(rows))] if shape == "scale" and raws[0].get("shape") == "scale" else []
        for raw in raws:
            for r, (row, own) in enumerate(zip(rows, raw["rows"])):
                if own["n"] < tile_min():
                    row["means"].append(None)
                    if ordinal:
                        row["texts"].append(None)
                elif ordinal:  # the median, in the recoded order of a reversed statement (as its mean)
                    levels, k = raws[0]["levels"], len(own["_counts"])
                    groups = [(a, b, sum(own["_counts"][a:b + 1])) for a, b in spans[r]]
                    order = [(k - 1 - b, k - 1 - a, c) for a, b, c in reversed(groups)] if own["reverse"] else groups
                    at, text = _median(order, levels[::-1] if own["reverse"] else levels, raws[0]["first"])
                    row["means"].append(at)
                    row["texts"].append(text)
                elif spans:
                    k, first = len(own["_counts"]), raws[0]["first"]
                    mean = own["_mean"]
                    if any(a != b for a, b in spans[r]):
                        mean = sum((first + ((k - 1 - (a + b) / 2) if own["reverse"] else (a + b) / 2)) * sum(own["_counts"][a:b + 1])
                                   for a, b in spans[r]) / own["n"]
                    row["means"].append(_shown(mean))
                else:
                    row["means"].append(_shown(own["_mean"] if "_mean" in own else statistics.fmean(own["_xs"])))
        out["ordinal"] = bool(ordinal)
        out["rows"] = rows
        if shape == "scale":
            levels = raws[0]["levels"]
            out["lo"], out["hi"] = raws[0]["first"], raws[0]["first"] + len(levels) - 1
            out["ends"] = [levels[0], levels[-1]]
        else:
            means = [m for row in rows for m in row["means"] if m is not None]
            bounds = raws[0].get("bounds")
            out["lo"], out["hi"] = bounds if bounds else (0, max(means + [1]) * 1.2)
            out["unit"], out["note"] = raws[0].get("unit", ""), raws[0].get("note", "")
        if not any(m is not None for row in rows for m in row["means"]):
            return None
    return out


def _quota(survey, quota_id, only=None, until=None):
    from app import quotas

    # until: the step of a link (step_cutoff), the completions up to then; a quota tile must not show the +1 of
    # a single completion either, it would tell which cell that person fell into
    entry = next((e for e in quotas.report(survey, _lang(), only, until=until) if e["quota"].id == quota_id), None)
    if entry is None:
        return None
    total = entry["sum"]
    # the distribution the targets describe (for quotas from the assistant: the population) next to the one reached
    return {"shape": "quota", "title": entry["quota"].name or entry["quota"].code,
            "rows": [{"label": c["name"] or c["code"], "target": c["target"], "done": c["done"], "share": c["share"],
                      "state": c["state"], "target_share": c["target"] / total["target"] if total["target"] else 0,
                      "done_share": c["done"] / total["done"] if total["done"] else 0} for c in entry["cells"]],
            "sum": {"target": total["target"], "done": total["done"],
                    "share": min(total["done"] / total["target"], 1) if total["target"] else 0},
            # what "erreicht" counts, as the other tiles name theirs (V73)
            "basis": tr(_lang(), "Grundlage: abgeschlossene echte Teilnahmen; was die Qualitätssicherung markiert, zählt nicht",
                        "Basis: completed real participations; what quality assurance flags does not count")}


def _field(survey, name, table, pool, only=None, until=None, excluded=0):
    from app.timeutil import to_local

    lang = _lang()
    if until is not None and not only:
        # the step of a link (step_cutoff): begun and completed as they were then, so the figures match the tiles
        def counted(*conditions):
            return db.session.scalar(db.select(db.func.count(Response.id)).filter(
                Response.survey_id == survey.id, Response.source.in_(pool["sources"]), Response.not_live_extra(), *conditions))

        started = counted(Response.started_at <= until)
        done = counted(Response.status == Response.COMPLETED, Response.completed_at <= until)
    elif only:  # a provider's own participations: real ones that came with its value of the URL parameter
        from app import quotas

        by_status = quotas._from_responses(survey, only)[1]
        started, done = sum(by_status.values()), by_status.get(Response.COMPLETED, 0)
    else:
        # begun and completed participations of the sources the dashboard rests on (the data pool): with test or
        # example data in the pool they count too, as they do in the tiles
        tally = survey.tally()
        started = sum(n for (source, _, _), n in tally.items() if source in pool["sources"])
        done = sum(n for (source, _, status), n in tally.items() if source in pool["sources"] and status == Response.COMPLETED)
    data = {"shape": "field", "name": name, "title": pick(FIELD_NAMES, lang)[name]}
    if name == "count":
        sub = tr(lang, "{a} begonnen · {b} abgeschlossen", "{a} started · {b} completed", a=started, b=done)
        if excluded > 0:  # the data pool, without what quality assurance marks: why the big number is smaller
            sub += " · " + tr(lang, "{n} davon von der Qualitätssicherung ausgeschlossen",
                              "{n} of them left out by quality assurance", n=excluded)
        data.update(value=str(len(table)), sub=sub)
    elif name == "rate":
        share = done / started if started else None
        data.update(value=_pct(share) if share is not None else "–", share=share or 0,
                    sub=tr(lang, "{b} von {a} begonnenen Teilnahmen abgeschlossen", "{b} of {a} started participations completed",
                           a=started, b=done))
    elif name == "duration":
        times = [r["duration_s"] for r in table if isinstance(r.get("duration_s"), (int, float)) and r.get("status") == Response.COMPLETED]
        enough = len(times) >= cell_min()
        data.update(value=_time(statistics.median(times)) if enough else "–",
                    sub=(tr(lang, "Median der abgeschlossenen Teilnahmen im Datenpool", "Median of the completed participations in the data pool")
                         + (" · " + tr(lang, "ohne die von der Qualitätssicherung ausgeschlossenen", "without those left out by quality assurance")
                            if excluded > 0 else "")) if enough
                    else tr(lang, "noch zu wenige abgeschlossene Teilnahmen", "too few completed participations yet"))
    elif name == "target":
        target = survey.max_responses
        if target:
            data.update(value=tr(lang, "{a} von {b}", "{a} of {b}", a=done, b=target), share=min(done / target, 1),
                        sub=tr(lang, "{p} des Stichprobenziels", "{p} of the sample target", p=_pct(min(done / target, 1)))
                        + " · " + tr(lang, "abgeschlossene Teilnahmen, vor der Qualitätssicherung",
                                     "completed participations, before quality assurance"))
        else:
            data.update(value="–", share=0, sub=tr(lang, "kein Stichprobenziel gesetzt", "no sample target set"), open=True)
    elif name == "days":
        found = db.session.execute(db.select(Response.completed_at, Response.params).filter(
            Response.survey_id == survey.id, Response.status == Response.COMPLETED, Response.completed_at.is_not(None),
            Response.source.in_(["real"] if only else pool["sources"]),
            *([Response.completed_at <= until] if until is not None and not only else [])))
        stamps = [stamp for stamp, params in found if not only or (params or {}).get(only[0]) == only[1]]
        per_day = {}
        for stamp in stamps:
            day = to_local(stamp).date()
            per_day[day] = per_day.get(day, 0) + 1
        days = sorted(per_day)[-DAYS_SHOWN:]
        if days:
            span = [days[0] + timedelta(days=n) for n in range((days[-1] - days[0]).days + 1)][-DAYS_SHOWN:]
            data["rows"] = [{"label": day_month(d, lang), "count": per_day.get(d, 0)}
                            for d in span]
            top = max(r["count"] for r in data["rows"])
            for row in data["rows"]:
                row["share"] = row["count"] / top if top else 0
        else:
            data["rows"] = []
    return data


def _heat(cells):
    """The squares of a heatmap that keep cell_min() people or more, in the units of the state drawing:
    ([{"x", "y", "w", "h", "count", "share"}], number of people in squares left out)."""
    from app.items import federal_state

    kept = {k: c for k, c in cells.items() if c >= cell_min()}
    left = sum(c for k, c in cells.items() if k not in kept)
    top = max(kept.values(), default=0)
    out = []
    for (i, j), count in sorted(kept.items()):
        corner = federal_state.project(j * HEAT_LON, (i + 1) * HEAT_LAT)
        far = federal_state.project((j + 1) * HEAT_LON, i * HEAT_LAT)
        if corner is None or far is None:
            return [], left + sum(kept.values())
        out.append({"x": round(corner[0], 2), "y": round(corner[1], 2), "w": round(far[0] - corner[0], 2),
                    "h": round(far[1] - corner[1], 2), "count": count, "share": count / top})
    return out, left


def _nominal(data):
    """Answers without an order: one or several of a list (unless the question is set to "ordinal"), states."""
    return data["shape"] in ("multi", "states") or (data["shape"] == "cats" and data.get("nominal"))


def by_frequency(rows):
    """Frequencies of answers without an order are shown sorted, the most frequent first; the row of answers
    put together and rows whose number is not shown come last."""
    from app.results import residual

    return sorted(rows, key=lambda r: (bool(r.get("rest")), r.get("count") is None, bool(r.get("other")) or residual(r.get("label")),
                                       -(r.get("count") or 0)))


def pool_of(survey):
    """The data pool of the dashboards: the survey's own (page "Qualitätssicherung"), without what quality
    control marks, as on the results page, and never with test runs: a preview of the editor makes one for every
    role from "Ansehen" up (H10 of the security audit 2026-10-03), and a link shows what leaves the survey. The
    members' view of a dashboard shows what its links show, so it leaves them out too (the results page keeps
    the pool as it is). A pool of test runs alone counts the real participations."""
    from app.surveys import stored_pool

    pool = stored_pool(survey, drop=True)
    return {**pool, "sources": [x for x in pool["sources"] if x != "test"] or ["real"], "speed_real": True}


def survey_has_link(survey):
    """Does a dashboard of the survey have a valid link (one that shows it without signing in)?"""
    return any(has_link(board) for board in survey.boards)
