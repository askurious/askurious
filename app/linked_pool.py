"""The shared analysis of a linked element (app/linked.py): its answers from every survey that uses it, as one
data set and as figures per survey (concept geteilte-elemente-konzept.md, section 6).

Only surveys the person may open take part (E7): the analysis shows nothing that person could not export from each
survey by themselves, so no data reach anyone new. Other uses are only counted. From each survey come the real
participations of its own data pool (page "Qualitätssicherung": also unfinished ones if it says so; what quality
control marks is left out, as on the results page); test runs and dummy data never. Running surveys take part
unless the request leaves them out; drafts have no real data.

A participation counts only if the question was linked when it began (the consent then had the paragraph on the
shared analysis, app/items/consent.py): a question linked later, e.g. when duplicating a closed survey, left the
participants uninformed, and their answers come in only with `without_hint` (the page asks).

Answers are put on the variables of the element's template: Likert statements and choice codes by their id, so
uses with other variable names line up; reverse coded statements are recoded as in every export. Participations
get a pseudonym (the same one as in the operator's pool, constructs._participant_ref): other linked elements of
the same participation stand in the same row.
"""
import json
import statistics

from app import access, constructs, export, linked, loops
from app.extensions import db
from app.models import Survey

BASE = ["survey", "participant_id", "item", "loop_pass", "language", "month", "version", "adapted", "modifications", "consent_hint"]


def values(item, c, answer):
    """({variable of the template: value}, {added variable: value}) for one answer of a use of `c`."""
    group = {"likert": "statements", "semantic": "pairs"}.get(item.type)
    if group:  # the export's values (reverse coded, -77 for not shown), put on the template's codes by statement id
        canon = {r.get("id"): r.get("code") for r in ((c.spec or {}).get("config") or {}).get(group) or []}
        exported = list(item.kind.export_values(item, answer).values())
        out, extra = {}, {}
        for n, (r, value) in enumerate(zip((item.config or {}).get(group) or [], exported)):
            if r.get("id") in canon:
                out[canon[r["id"]]] = value
            else:  # added in this survey (adapted)
                extra[r.get("code") or f"{item.code}_{n + 1}"] = value
        return out, extra
    if item.type in ("single_choice", "text_input"):
        return constructs._canonical_values(item, c, answer)
    base = (c.spec or {}).get("code") or item.code
    out = {}
    for name, value in item.kind.export_values(item, answer).items():
        out[base + name[len(item.code):] if name.startswith(item.code) else name] = value
    return out, {}


def variables(c):
    """The variables of the template, in order (statements of a matrix, else the element's own)."""
    config = (c.spec or {}).get("config") or {}
    rows = config.get("statements") or config.get("pairs")
    if rows:
        return [r.get("code") for r in rows]
    return [(c.spec or {}).get("code")]


def _participations(survey):
    """[Response] of the survey's data pool, without what quality assurance marks (as on the results page)."""
    from app import export
    from app.surveys import stored_pool

    pool = stored_pool(survey, drop=True)
    table = export.rows(survey, pool["all"], ("real",), pool["speeder"], True, with_hb=False, with_quality=False,
                        drop_speeders=True)
    return [table.numbered[row["resp_id"]] for row in table if row["resp_id"] in table.numbered]


CACHE_SIZE = 32
_cache = {}  # {(key, user id, running, without_hint): (fingerprint, data)}, per server process


def _fingerprint(c, uses):
    """What the shared data depend on, read in two small queries: the element, every use (its survey's status and
    data pool settings, the question), and per survey the count, newest id, completions and summed versions of
    its real participations. Anything that changes the data changes this (a new or deleted participation, a
    completion, a page answered by a running participation, another data pool or marking rule, a changed
    question)."""
    import hashlib

    from app.models import Response

    ids = sorted({s.id for _, s in uses})
    stats = db.session.execute(
        db.select(Response.survey_id, db.func.count(Response.id), db.func.max(Response.id),
                  db.func.count(Response.completed_at), db.func.max(Response.completed_at),
                  db.func.sum(Response.version))  # a participation that answers another page raises its version
        .filter(Response.survey_id.in_(ids), Response.source == "real", Response.is_preview.is_(False))
        .group_by(Response.survey_id)).all() if ids else []
    parts = [c.key, c.seed_version, c.spec, [(i.id, i.code, i.config, s.id, s.status, s.data_pool, s.speeder_pct, s.speeder_rule, s.speeder_rsi,
                                             s.quality_rules) for i, s in uses], [tuple(map(str, row)) for row in stats]]
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def collect(c, user, running=True, without_hint=False):
    """The shared data of element `c` for `user`: {"header", "rows", "surveys": [per use survey: what came in and
    what not], "hidden": number of surveys the person cannot open or only views}. Kept while nothing it depends on
    changed (_fingerprint): the page reads the data pool of every survey, which takes long with many of them."""
    uses = linked.uses(c)
    key = (c.key, user.id, bool(running), bool(without_hint))
    own = _editable(user, uses)
    fingerprint = _fingerprint(c, uses) + str(sorted(own))
    hit = _cache.get(key)
    if hit and hit[0] == fingerprint:
        return hit[1]
    data = _collect(c, user, running, without_hint, uses, own)
    _cache.pop(key, None)
    _cache[key] = (fingerprint, data)
    # no lock: another request may evict the same oldest key at once (code review 2026-10-15, L5)
    while len(_cache) > CACHE_SIZE:
        _cache.pop(next(iter(_cache), None), None)
    return data


def _editable(user, uses):
    """Ids of the using surveys whose data `user` may take in: "Bearbeiten" or more ("Ansehen" downloads no data)."""
    ids = set(db.session.scalars(access.survey_ids(user)))
    return {s.id for _, s in uses if s.id in ids and s.can(user, "editor")}


def _collect(c, user, running, without_hint, uses, own):
    from datetime import datetime

    hidden = len({s.id for _, s in uses if s.id not in own})
    by_survey = {}
    for item, s in uses:
        if s.id in own:
            by_survey.setdefault(s.id, (s, []))[1].append(item)
    names = variables(c)
    others, rows, surveys = {}, [], []
    for s, items in by_survey.values():
        info = {"survey": s, "items": items, "n": 0, "no_hint": 0, "left_out": None, "values": {},
                "versions": sorted({linked.version_of(i) for i in items})}
        surveys.append(info)
        if s.status == Survey.DRAFT:
            info["left_out"] = "Entwurf"
            continue
        if s.status == Survey.RUNNING and not running:
            info["left_out"] = "läuft"
            continue
        # other linked elements of the same survey: their variables stand beside (one use each)
        mates = {}
        for b in s.blocks:
            for i in b.items:
                key = linked.key_of(i)
                if key and key != c.key and key not in mates:
                    other = linked.get(key)
                    if other is not None:
                        mates[key] = (i, other)
                        others.setdefault(key, other)
        # per use: its differences (once, not per participation) and its passes in a loop (app/loops.py)
        prepared = []
        for item in items:
            meta = item.config.get("construct") or {}
            mods = constructs.modifications(item, c)
            since = meta.get("since")
            passes = export._loop_rows(item) or [(None, None)]
            prepared.append((item, meta, mods, datetime.fromisoformat(since) if since else None,
                             [(n, rid, loops.row_caption(item.block, rid, n) if rid else "") for n, rid in passes]))
        for r in _participations(s):
            answers = r.answers or {}
            for item, meta, mods, since, passes in prepared:
                hint = since is None or (r.started_at is not None and r.started_at >= since)
                if not hint:
                    info["no_hint"] += 1
                    if not without_hint:
                        continue
                for n, rid, caption in passes:
                    key = f"{item.id}#{rid}" if rid else str(item.id)
                    if rid and key not in answers:
                        continue  # a pass this person was not shown
                    vals, extra = values(item, c, answers.get(key))
                    info["n"] += 1
                    for name in names:
                        info["values"].setdefault(name, []).append(vals.get(name))

                    def mate(i, other):  # the same pass, if the other element is in the same loop
                        own = f"{i.id}#{rid}"
                        return values(i, other, answers.get(own) if rid and own in answers else answers.get(str(i.id)))[0]

                    rows.append({"base": [s.title, constructs._participant_ref(r.id), item.code, caption, r.language,
                                          r.completed_at.strftime("%Y-%m") if r.completed_at else r.started_at.strftime("%Y-%m"),
                                          meta.get("version", ""), int(bool(mods) or bool(meta.get("unlocked"))),
                                          "; ".join(mods), int(hint)],
                                 "values": vals, "extra": extra,
                                 "mates": {k: mate(i, other) for k, (i, other) in mates.items()}})
    header, columns = list(BASE) + names + ["added_items"], []
    taken = set(header)
    for key, other in others.items():
        for name in variables(other):
            column = name
            while column in taken:
                column += "_"
            taken.add(column)
            columns.append((key, name, column))
    header += [column for _, _, column in columns]
    out = []
    for row in rows:
        line = row["base"] + [row["values"].get(name) for name in names]
        line.append(json.dumps(row["extra"], ensure_ascii=False) if row["extra"] else "")
        line += [row["mates"].get(key, {}).get(name) for key, name, _ in columns]
        out.append(line)
    return {"header": header, "rows": out, "surveys": surveys, "hidden": hidden, "variables": names}


def _numbers(xs):
    return [x for x in xs if isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0]


def _describe(xs):
    xs = _numbers(xs)
    if not xs:
        return None
    return {"n": len(xs), "mean": statistics.fmean(xs), "sd": statistics.stdev(xs) if len(xs) > 1 else None}


def _reliability(matrix):
    from app import results, scales

    complete = [row for row in matrix if all(isinstance(v, (int, float)) and v >= 0 for v in row)]
    if not complete or len(complete[0]) < 2:
        return None
    alpha = results.cronbach_alpha(complete) if len(complete[0]) > 2 else scales.spearman_brown(complete)
    return {"n": len(complete), "alpha": alpha, "omega": scales.mcdonald_omega(complete), "two": len(complete[0]) == 2}


def summary(c, data):
    """Figures per survey and for all together: per variable n, mean and SD (numbers) or the shares of the codes
    (single choice), and for a matrix of two or more statements its reliability (alpha or Spearman-Brown, omega)."""
    names = data["variables"]
    kind = (c.spec or {}).get("type")
    categorical = kind in ("single_choice", "federal_state")
    labels = {}
    if categorical:
        lang = next(iter((c.spec or {}).get("texts") or {}), "de")
        texts = ((c.spec or {}).get("texts") or {}).get(lang, {}).get("options", {})
        labels = {str(o.get("code")): texts.get(o.get("id"), o.get("code")) for o in (c.spec or {}).get("config", {}).get("options", [])}
    groups = [(info["survey"].title, info) for info in data["surveys"] if info["n"]]
    together = {"n": sum(info["n"] for _, info in groups),
                "values": {name: [v for _, info in groups for v in info["values"].get(name, [])] for name in names}}
    # the range the bars of the means are drawn on: the scale of a matrix, else what was answered
    points = ((c.spec or {}).get("config") or {}).get("points")
    numbers = _numbers([v for _, info in groups for name in names for v in info["values"].get(name, [])])
    low, high = (1, points) if kind in ("likert", "semantic") and points else (min(numbers, default=0), max(numbers, default=1))
    out = []
    for title, info in groups + ([("Alle zusammen", together)] if len(groups) > 1 else []):
        entry = {"title": title, "n": info["n"], "rows": [], "low": low, "high": high}
        if categorical:
            answers = [str(v) for v in info["values"].get(names[0], []) if v not in (None, "")]
            total = len(answers)
            entry["shares"] = [{"label": label, "count": answers.count(code), "share": answers.count(code) / total if total else None}
                               for code, label in labels.items()]
            entry["answered"] = total
        else:
            entry["rows"] = [{"name": name, **(_describe(info["values"].get(name, [])) or {"n": 0})} for name in names]
            if len(names) > 1 and kind in ("likert", "semantic"):
                matrix = list(zip(*[info["values"].get(name, []) for name in names]))
                entry["reliability"] = _reliability([list(row) for row in matrix])
        out.append(entry)
    return out


# ---- the data set as a file of a statistics program (as the survey exports: .sav, .omv, .rds) ----

BASE_LABELS = {"survey": "Befragung", "participant_id": "Teilnahme (Pseudonym)", "item": "Variablenname in der Befragung",
               "loop_pass": "Durchgang der Schleife", "language": "Sprache", "month": "Monat der Teilnahme", "version": "Fassung des Elements",
               "adapted": "in dieser Befragung angepasst (1 = ja)", "modifications": "Abweichungen vom Element",
               "consent_hint": "Einwilligung nannte die gemeinsame Auswertung (1 = ja)", "added_items": "hinzugefügte Aussagen (JSON)"}


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _value_labels(c, name):
    """{code: label} of one variable of element `c` (scale points of a matrix, options of a choice)."""
    from app.items.base import plain

    spec = c.spec or {}
    texts = next(iter((spec.get("texts") or {}).values()), {})
    config = spec.get("config") or {}
    if spec.get("type") == "likert" and name in variables(c):
        from app.items.likert import shown_labels

        labels = shown_labels(texts.get("labels") or [], config.get("labeling"))  # ends only: no label in between
        return {float(n): plain(l) for n, l in enumerate(labels, 1) if l}
    if spec.get("type") == "single_choice" and name == spec.get("code"):
        out = {}
        for o in config.get("options", []):
            try:
                out[float(o.get("code"))] = plain((texts.get("options") or {}).get(o.get("id"), ""))
            except (TypeError, ValueError):
                return {}  # codes that are no numbers: the column stays text
        return out
    return {}


def dictionary(c, data):
    """[{name, label, kind ("num" | "str"), measure, value_labels, missing, decimals, width}] for the columns of
    `data` (collect): what the writers of the survey exports need."""
    out = []
    names = set(data["variables"])
    spec_type = (c.spec or {}).get("type")
    for n, column in enumerate(data["header"]):
        values = [row[n] for row in data["rows"] if row[n] not in (None, "")]
        numeric = bool(values) and all(_is_number(v) for v in values)
        whole = numeric and all(float(v).is_integer() for v in values)
        labels = _value_labels(c, column) if column in names else {}
        measure = ("ordinal" if spec_type in ("likert", "semantic") else "nominal" if labels else "scale") \
            if column in names and numeric else ("nominal" if not numeric else "scale")
        if column in ("adapted", "consent_hint", "version"):
            measure = "nominal"
        out.append({"name": column, "label": BASE_LABELS.get(column) or (f"{c.name}: {column}" if column in names else column),
                    "kind": "num" if numeric else "str", "whole": whole, "measure": measure, "value_labels": labels,
                    "missing": [-77.0] if numeric and column in names else [],
                    "decimals": 0 if whole else 2,
                    "width": max([len(str(v).encode("utf-8")) for v in values] or [1])})
    return out


def file(c, data, fmt):
    """The data set of `collect` as bytes in format `fmt` (sav, omv, rds)."""
    return write(dictionary(c, data), data["rows"], fmt, c.name)


def write(columns, rows, fmt, title):
    """Rows with the columns of a dictionary (see dictionary()) as bytes in format `fmt` (sav, omv, rds); also used
    by the composed construct data (app/construct_compose.py)."""
    from app import omv, rds, sav

    rows = [list(row) for row in rows]
    if fmt == "sav":
        taken, variables = set(), []
        for col in columns:
            var = {"name": sav.valid_name(col["name"], taken), "label": col["label"], "kind": col["kind"],
                   "measure": col["measure"], "decimals": col["decimals"], "print_width": 8}
            if col["kind"] == "str":
                var["width"] = min(col["width"], sav.MAX_STRING)
            else:
                var["value_labels"], var["missing"] = col["value_labels"], col["missing"]
            variables.append(var)
        return sav.write(variables, [[None if v == "" else v for v in row] for row in rows], file_label=title)
    if fmt == "omv":
        variables = []
        for col in columns:
            var = {"name": col["name"], "label": col["label"],
                   "measure": {"scale": "continuous"}.get(col["measure"], col["measure"])}
            if col["kind"] == "str":
                var["kind"] = "id" if col["name"] in ("study_id", "participant_id", "modifications", "added_items") else "text"
            else:
                var["kind"] = "int" if col["whole"] else "num"
                var["value_labels"] = {int(k): v for k, v in col["value_labels"].items()}
                var["missing"] = col["missing"]
            variables.append(var)
        return omv.write(variables, rows)
    if fmt == "rds":
        variables = []
        for n, col in enumerate(columns):
            if col["missing"]:  # "not shown" is NA in R, as the survey export has it
                for row in rows:
                    if _is_number(row[n]) and float(row[n]) in col["missing"]:
                        row[n] = None
            variables.append({"name": col["name"], "label": col["label"],
                              "kind": "str" if col["kind"] == "str" else ("int" if col["whole"] else "num"),
                              "value_labels": col["value_labels"]})
        return rds.write(variables, rows)
    raise ValueError(fmt)
