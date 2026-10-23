"""Waves of a longitudinal design: surveys linked as wave 1 … n ("vorher", "nachher") and merged by the personal code
(items/personal_code.py) on the Export page of any wave.

Where it is set: the page "Studienplan", in its own panel below the plan (the design "Messwiederholung" or
"Längsschnitt" is chosen there; no new tab or menu row, decision "No extra menu band"). It is no part of the plan
itself: linking waves after the start is no deviation from a pre-registration. Stored in survey.waves = {"waves":
[{"survey": id, "label": "vorher"}, ...]}, the survey itself among them; a save writes the same list to every wave
(the person may download the data of each of them, so they may edit it too) and empties it on a wave taken out.

Matching (link()): by the exact code, the code in the column of the first personal code of each wave. Codes that
stand twice or more in one wave are flagged "duplicate" and not linked: whose answers are whose cannot be told. A
row without a code, or with "weiß nicht" in every part, is "no_code". With the tick "tolerant" a code still alone
is also linked to a code of another wave that differs in exactly one part (Direnga et al., 2016: "off-1"; Kearney et
al., 1984; Schnell et al., 2010 with the Levenshtein distance, which for codes of the same length and parts in a
fixed order comes to the same as one differing part here, since a single changed character lies in one part), but
only where it is unique both ways: the code has exactly one such neighbour in the other waves and that neighbour
has only it, and neither stands twice in the wave of the other. Such links are flagged "tolerant" in the column
match_quality; everything else is "exact" (linked by the same code) or "unmatched" (in one wave only).
A code with "weiß nicht" (?) in some of its parts tells less: two people who do not know the same parts and agree
in the rest share it by chance far more often. It is linked only with at least MIN_KNOWN known parts (the fewest
parts a scheme may have, personal_code.MIN_PARTS: no code is linked on less than the smallest allowed scheme), then
exactly only (never tolerantly) and flagged "partial"; with fewer known parts it counts as "no_code" (audit
2026-10-21, F4).

Output: wide (one row per person, the columns of each wave prefixed t1_, t2_ …) or long (one row per person and
wave, columns wave and wave_label), as CSV, .sav or .rds with the dictionaries of the waves' own exports, plus a
matching report (abgleich.csv). Each wave brings its data pool and the marks of its quality assurance as in its own
export (surveys.stored_pool: the saved sources, speeders and marks as columns, nothing left out). Only waves the
person may download (role "Bearbeiten" or "Admin"); the download is written to the access log like the other
raw-data exports (audit.ACTIONS, with the ids of the waves)."""
import csv
import io
import sys
import zipfile

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, heavy, report_jobs
from app.extensions import db
from app.items import personal_code
from app.lang import de_en
from app.models import Survey

bp = Blueprint("waves", __name__, url_prefix="/surveys")

MAX_WAVES = 6
MAX_LABEL = 40
QUALITIES = ("exact", "tolerant", "partial", "unmatched", "duplicate", "no_code")
LINKED = ("exact", "tolerant", "partial")  # the qualities of a person found in more than one wave
CODED = LINKED + ("unmatched",)  # the qualities of a person with a usable code
MIN_KNOWN = personal_code.MIN_PARTS  # known parts a code needs to be linked at all (F4)
LAYOUTS = ("wide", "long")
FORMATS = ("csv", "sav", "rds")
# cited next to the tolerant matching (items/personal_code.py has them in full)
SOURCES = (personal_code.DIRENGA, personal_code.SCHNELL)
# the columns the merged files put in front; a column of a wave with one of these names stays out of the long file
OWN_COLUMNS = ("match_code", "match_quality", "waves_n", "wave", "wave_label")


# --- the list of waves --------------------------------------------------------------------------------------------

def of(survey):
    """[{"survey": id, "label": str}] of the waves this survey belongs to, in their order; [] for none."""
    rows = ((survey.waves or {}).get("waves") or []) if survey is not None else []
    out = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("survey"), int):
            out.append({"survey": row["survey"], "label": str(row.get("label") or "")[:MAX_LABEL]})
    return out if any(r["survey"] == survey.id for r in out) else []


def prefix(n):
    """The prefix of the columns of wave n (from 1) in the wide file."""
    return f"t{n}_"


def code_item(survey):
    """The first personal code of the main part of the questionnaire (not after the split, not in a loop), or None."""
    from app import export

    return next((i for i in export._items(survey) if i.type == "personal_code" and not export._loop_rows(i)), None)


def scheme(survey):
    item = code_item(survey)
    return item.kind.scheme(item) if item is not None else None


def may_download(user, survey):
    return survey is not None and survey.can(user, "editor")


def candidates(user):
    """The surveys `user` may download the data of, newest first, without live polls and interview guides: [(id,
    title)]."""
    from app import access

    found = db.session.scalars(db.select(Survey).filter(Survey.id.in_(access.survey_ids(user)))
                               .order_by(Survey.created_at.desc(), Survey.id.desc())).all()
    return [(s.id, s.title) for s in found if s.live is None and s.interview is None and may_download(user, s)]


def view(user, survey):
    """What the pages show of the waves: [{"n", "survey" (Survey or None), "label", "allowed", "problem"}] and whether
    they can be merged; problem is a text for the researcher or "". The scheme of the first wave with a code decides
    what the others must match."""
    rows, first = [], None
    for n, row in enumerate(of(survey), start=1):
        s = db.session.get(Survey, row["survey"])
        allowed = may_download(user, s)
        problem = ""
        if s is None:
            problem = de_en("Befragung gibt es nicht mehr", "survey no longer exists")
        elif not allowed:
            problem = de_en("keine Berechtigung zum Herunterladen der Daten: bleibt beim Zusammenführen weg",
                            "no right to download the data: left out when merging")
        else:
            sch = scheme(s)
            if sch is None:
                problem = de_en("kein Element „Persönlicher Code“ (außerhalb von Schleifen)",
                                "no element “Personal code” (outside loops)")
            elif first is None:
                first = sch
            elif sch != first:
                problem = de_en("die Teile des Codes weichen von der ersten Welle ab: Codes lassen sich so nicht vergleichen",
                                "the parts of the code differ from the first wave: codes cannot be compared like this")
        # the title only for who may open that survey; else a neutral label without title and id (audit 2026-10-10,
        # A3/T2)
        if s is None:
            title = "#" + str(row["survey"])
        elif s.can(user, "viewer"):
            title = s.title
        else:
            title = de_en("Befragung ohne Zugang für Sie", "survey you have no access to")
        rows.append({"n": n, "survey": s, "id": row["survey"], "label": row["label"], "allowed": allowed, "problem": problem,
                     "title": title})
    usable = [r for r in rows if not r["problem"]]
    return {"rows": rows, "usable": len(usable), "ready": len(usable) >= 2}


def save(survey, user, ids, labels):
    """Set the waves of `survey` from the form: ids and labels in their order (empty rows left out). Returns a message
    if the list cannot be taken, else None. The list goes to every wave in it; a wave taken out loses it."""
    allowed = {sid for sid, _ in candidates(user)} | {survey.id}
    rows, seen = [], set()
    for raw, label in zip(ids, labels):
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            continue
        if sid in seen:
            return de_en("Eine Befragung steht zweimal in der Liste.", "A survey is in the list twice.")
        if sid not in allowed:
            return de_en("Wellen können nur Befragungen sein, deren Daten Sie herunterladen dürfen.",
                         "Waves can only be surveys whose data you may download.")
        seen.add(sid)
        rows.append({"survey": sid, "label": " ".join((label or "").split())[:MAX_LABEL]})
    if len(rows) > MAX_WAVES:
        return de_en("Höchstens {n} Wellen.", "At most {n} waves.", n=MAX_WAVES)
    if rows and survey.id not in seen:
        return de_en("Diese Befragung muss eine der Wellen sein.", "This survey must be one of the waves.")
    for n, row in enumerate(rows, start=1):
        row["label"] = row["label"] or de_en("Welle {n}", "Wave {n}", n=n)
    value = {"waves": rows} if rows else None  # one wave alone is kept too: the next one is chosen in the next save
    if value and (survey.waves or {}).get("model"):  # what the results page models over the waves (app/panel_models.py)
        value["model"] = survey.waves["model"]
    before, old = {r["survey"] for r in of(survey)}, survey.waves
    for sid in before | seen:
        s = db.session.get(Survey, sid)
        if s is None or not may_download(user, s):
            continue
        if sid in seen:
            s.waves = value
        elif s.waves == old:  # taken out: forgets the list, unless it had been given another one meanwhile
            s.waves = None
    if survey.id in seen or not rows:
        survey.waves = value
    return None


# --- matching ------------------------------------------------------------------------------------------------------

def _parts(parts, code):
    from app.items.personal_code import split

    return split(parts, code)


def _no_code(code):
    # every part "weiß nicht" (? since audit 2026-10-10, N-11; X in codes written before): tells nothing
    return not code or set(code) <= {"?"} or set(code) <= {"X"}


def _unknown(parts, code):
    """How many parts of the code are "weiß nicht" (?); None if the code does not fit the scheme. Only "?" marks an
    unknown part here: an X in a code written before N-11 may as well be a letter."""
    split = _parts(parts, code)
    if split is None:
        return None
    return sum(1 for p in split if set(p) <= {personal_code.PLACEHOLDER})


def _too_unknown(parts, code):
    """Fewer than MIN_KNOWN known parts: too little to tell two people apart (audit 2026-10-21, F4)."""
    unknown = _unknown(parts, code)
    return bool(unknown) and len(parts) - unknown < MIN_KNOWN


def link(codes, parts, tolerant=False):
    """Link the rows of the waves. codes: [[code or None per row] per wave]; parts: the scheme (keys of PARTS).
    Returns persons [{"code", "rows": {wave index: row index}, "quality"}], one per person found; a duplicate or a
    row without a code (or with fewer than MIN_KNOWN known parts) is a person of its own."""
    persons, groups, order = [], {}, []
    for w, column in enumerate(codes):
        counts = {}
        for c in column:
            if not _no_code(c) and not _too_unknown(parts, c):
                counts[c] = counts.get(c, 0) + 1
        for r, c in enumerate(column):
            if _no_code(c) or _too_unknown(parts, c):
                persons.append({"code": c or None, "rows": {w: r}, "quality": "no_code", "at": (w, r)})
            elif counts[c] > 1:
                persons.append({"code": c, "rows": {w: r}, "quality": "duplicate", "at": (w, r)})
                g = groups.setdefault(c, {"code": c, "rows": {}, "blocked": set(), "at": (w, r)})
                g["blocked"].add(w)
                if c not in order:
                    order.append(c)
            else:
                g = groups.setdefault(c, {"code": c, "rows": {}, "blocked": set(), "at": (w, r)})
                g["rows"][w] = r
                if c not in order:
                    order.append(c)
    merged = {}  # code -> the code of the group it joins
    if tolerant:
        by_key = {}
        # a code with unknown parts is linked exactly only: one differing part on top of unknown ones is too little
        split_of = {c: None if _unknown(parts, c) else _parts(parts, c) for c in order}
        for c in order:
            p = split_of[c]
            if p is None:
                continue
            for i in range(len(p)):
                by_key.setdefault((i, tuple(p[:i] + p[i + 1:])), []).append(c)

        def taken(g):
            return set(g["rows"]) | g["blocked"]

        def potential(c):
            g, p = groups[c], split_of[c]
            if p is None or not g["rows"]:
                return set()
            out = set()
            for i in range(len(p)):
                for other in by_key.get((i, tuple(p[:i] + p[i + 1:])), ()):
                    if other != c and not (set(g["rows"]) & taken(groups[other])):
                        out.add(other)
            return out

        near = {c: potential(c) for c in order}
        for c in order:
            if c in merged or len(near[c]) != 1:
                continue
            (other,) = near[c]
            g, h = groups[c], groups[other]
            if other in merged or near.get(other) != {c} or not h["rows"] or (set(g["rows"]) & h["blocked"]) \
                    or (set(h["rows"]) & g["blocked"]):
                continue
            first, second = (c, other) if g["at"] <= h["at"] else (other, c)
            merged[second] = first
    for c in order:
        g = groups[c]
        if not g["rows"] or c in merged:
            continue
        rows = dict(g["rows"])
        quality = ("partial" if _unknown(parts, c) else "exact") if len(rows) > 1 else "unmatched"
        for other, into in merged.items():
            if into == c:
                rows.update(groups[other]["rows"])
                quality = "tolerant"
        persons.append({"code": c, "rows": dict(sorted(rows.items())), "quality": quality, "at": g["at"]})
    persons.sort(key=lambda p: (p["quality"] not in CODED, p["at"]))
    for p in persons:
        p.pop("at", None)
    return persons


def report(persons, n_waves):
    """Counts of the matching: {"waves": [{"rows", "no_code", "duplicate", "exact", "tolerant", "partial",
    "unmatched"}], "persons", "linked", "tolerant", "partial", "complete", "unmatched", "duplicate", "no_code"}."""
    waves = [{"rows": 0, **{q: 0 for q in QUALITIES}} for _ in range(n_waves)]
    for p in persons:
        for w in p["rows"]:
            waves[w]["rows"] += 1
            waves[w][p["quality"]] += 1
    coded = [p for p in persons if p["quality"] in CODED]
    return {"waves": waves, "persons": len(coded),
            "linked": sum(1 for p in coded if p["quality"] in LINKED),
            "tolerant": sum(1 for p in coded if p["quality"] == "tolerant"),
            "partial": sum(1 for p in coded if p["quality"] == "partial"),
            "complete": sum(1 for p in coded if len(p["rows"]) == n_waves),
            "unmatched": sum(1 for p in coded if p["quality"] == "unmatched"),
            "duplicate": sum(1 for p in persons if p["quality"] == "duplicate"),
            "no_code": sum(1 for p in persons if p["quality"] == "no_code")}


# --- the merged files ----------------------------------------------------------------------------------------------

def _wave_tables(user, survey):
    """[(view row, cols, data, meta, codes)] of the waves that can be merged, each with its stored data pool."""
    from app import export
    from app.surveys import stored_pool

    out = []
    for row in view(user, survey)["rows"]:
        if row["problem"]:
            continue
        s = row["survey"]
        pool = stored_pool(s)
        cols, data, meta = export._spss_dictionary(s, pool["all"], pool["sources"], None, True, pool["sources"])
        code = code_item(s).code
        codes = [("".join(str(r.get(code) or "").split()).upper() or None) for r in data]
        out.append((row, cols, data, meta, codes))
    return out


def _text_meta(values):
    given = [str(v) for v in values if v not in (None, "")]
    return {"kind": "str", "level": "NOMINAL", "pairs": [], "missing": [], "free": False, "text_pairs": [], "decimals": 0,
            "num_width": 8, "missing_all": [], "missing_range": None, "missing_text": [], "missing_text_all": [],
            "date": None, "width": max([len(v) for v in given] + [8]),
            "bytes": max([len(v.encode("utf-8")) for v in given] + [1])}


def _int_meta(pairs=()):
    return {"kind": "int", "level": "NOMINAL", "pairs": [(str(v), lab) for v, lab in pairs], "missing": [], "free": False,
            "text_pairs": [], "decimals": 0, "num_width": 8, "missing_all": [], "missing_range": None, "missing_text": [],
            "missing_text_all": [], "date": None, "width": 8, "bytes": 1}


def merged(user, survey, layout="wide", tolerant=False):
    """(cols [(name, label)], rows [dict], meta {name: dictionary entry}, report, waves [view rows]) of the merge."""
    tables = _wave_tables(user, survey)
    if len(tables) < 2:
        return None
    parts = list(scheme(tables[0][0]["survey"]))
    persons = link([t[4] for t in tables], parts, tolerant)
    rep = report(persons, len(tables))
    labels = {q: q for q in QUALITIES}
    head = [("match_code", de_en("Persönlicher Code, über den verknüpft wurde", "Personal code the waves were linked by")),
            ("match_quality", de_en("Verknüpfung: exact, tolerant (ein Teil abweichend), partial (gleicher Code mit "
                                    "„weiß nicht“ in einzelnen Teilen), unmatched (nur eine Welle), duplicate (Code "
                                    "mehrfach in einer Welle), no_code (ohne Code oder mit weniger als {n} bekannten Teilen)",
                                    "Link: exact, tolerant (one part differs), partial (same code with “don’t know” in some "
                                    "parts), unmatched (one wave only), duplicate (code several times in one wave), no_code "
                                    "(no code, or fewer than {n} known parts)", n=MIN_KNOWN))]
    if layout == "wide":
        cols = head + [("waves_n", de_en("Anzahl der Wellen dieser Person", "Number of waves of this person"))]
        meta = {}
        for n, (row, wcols, _data, wmeta, _codes) in enumerate(tables, start=1):
            for c, label in wcols:
                cols.append((prefix(n) + c, f"{row['label']}: {label or c}"))
                meta[prefix(n) + c] = wmeta[c]
        rows = []
        for p in persons:
            out = {"match_code": p["code"], "match_quality": labels[p["quality"]], "waves_n": len(p["rows"])}
            for w, r in p["rows"].items():
                data = tables[w][2][r]
                out.update({prefix(w + 1) + c: data.get(c) for c, _ in tables[w][1]})
            rows.append(out)
        meta["waves_n"] = _int_meta()
    else:
        wave_pairs = [(n, row["label"]) for n, (row, *_rest) in enumerate(tables, start=1)]
        cols = head + [("wave", de_en("Nummer der Welle", "Number of the wave")),
                       ("wave_label", de_en("Bezeichnung der Welle", "Label of the wave"))]
        meta, seen = {"wave": _int_meta(wave_pairs)}, set(OWN_COLUMNS)
        for _row, wcols, _data, wmeta, _codes in tables:
            for c, label in wcols:
                if c not in seen:
                    seen.add(c)
                    cols.append((c, label))
                    meta[c] = dict(wmeta[c])
                else:
                    meta[c] = _joined(meta.get(c), wmeta[c]) if c in meta else meta.get(c)
        rows = []
        for p in persons:
            for w, r in p["rows"].items():
                data = tables[w][2][r]
                out = {c: data.get(c) for c, _ in tables[w][1] if c not in OWN_COLUMNS}
                out.update({"match_code": p["code"], "match_quality": labels[p["quality"]], "wave": w + 1,
                            "wave_label": tables[w][0]["label"]})
                rows.append(out)
    for c in ("match_code", "match_quality", "wave_label"):
        if any(name == c for name, _ in cols):
            meta[c] = _text_meta(r.get(c) for r in rows)
    return cols, rows, meta, rep, [t[0] for t in tables]


def _joined(a, b):
    """The dictionary entry of a column that stands in several waves (long file): text if one wave has text, a number
    with the most decimals of both; value labels only where the waves agree."""
    if a is None:
        return dict(b)
    out = dict(a)
    if a["kind"] != b["kind"]:
        if {a["kind"], b["kind"]} == {"int", "num"}:
            out.update(kind="num", decimals=max(a["decimals"], b["decimals"]), num_width=max(a["num_width"], b["num_width"]))
        else:
            out.update(kind="str", level="NOMINAL", pairs=[], text_pairs=[], missing=[], missing_all=[], missing_range=None,
                       missing_text=[], missing_text_all=[], date=None)
    if out["kind"] == "str":
        out.update(width=max(a["width"], b["width"]), bytes=max(a["bytes"], b["bytes"]))
        out["free"] = a["free"] or b["free"]
    if a.get("pairs") != b.get("pairs"):
        out["pairs"] = []
    if a.get("text_pairs") != b.get("text_pairs"):
        out["text_pairs"] = []
    return out


def report_csv(rep, waves, tolerant):
    from app.export import safe_text

    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["welle", "bezeichnung", "befragung", "zeilen", "exact", "tolerant", "partial", "unmatched", "duplicate",
                "no_code"])
    for n, (row, counts) in enumerate(zip(waves, rep["waves"]), start=1):
        # labels and titles are typed by people: no formula for Excel (as in the data file beside it, E-10)
        w.writerow([n, safe_text(row["label"]), safe_text(row["survey"].title), counts["rows"], counts["exact"], counts["tolerant"],
                    counts["partial"], counts["unmatched"], counts["duplicate"], counts["no_code"]])
    w.writerow([])
    w.writerow(["personen_mit_code", rep["persons"]])
    w.writerow(["verknuepft", rep["linked"]])
    w.writerow(["davon_tolerant", rep["tolerant"]])
    w.writerow(["davon_teilweise_unbekannt", rep["partial"]])
    w.writerow(["in_allen_wellen", rep["complete"]])
    w.writerow(["nur_eine_welle", rep["unmatched"]])
    w.writerow(["zeilen_mit_doppeltem_code", rep["duplicate"]])
    w.writerow(["zeilen_ohne_code", rep["no_code"]])
    w.writerow(["toleranter_abgleich", "ja" if tolerant else "nein"])
    return buf.getvalue()


def data_csv(cols, rows):
    from app.export import _cell

    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([c for c, _ in cols])
    for r in rows:
        w.writerow([_cell(r.get(c), False) for c, _ in cols])
    return buf.getvalue()


def zip_file(user, survey, layout, fmt, tolerant):
    """The ZIP of the merge: wellen-<name>-<layout>.<fmt> and abgleich.csv; None if fewer than two waves can be merged."""
    from app import export

    found = merged(user, survey, layout, tolerant)
    if found is None:
        return None, []
    cols, rows, meta, rep, waves = found
    base = f"wellen-{export.slug(survey.title)}-{'breit' if layout == 'wide' else 'lang'}"
    if fmt == "sav":
        body = export.sav_file(cols, rows, meta, survey.title)
    elif fmt == "rds":
        body = export.rds_file(cols, rows, meta)
    else:
        body = data_csv(cols, rows).encode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{base}.{fmt}", body)
        z.writestr("abgleich.csv", report_csv(rep, waves, tolerant).encode("utf-8"))
    return out.getvalue(), waves


# --- routes ------------------------------------------------------------------------------------------------------------

@bp.route("/<sid:survey_id>/waves", methods=["POST"])
@login_required
@autosave.aware
def update(survey_id):
    """The panel "Wellen" of the page "Studienplan" (people who may download the data)."""
    from app import surveys

    survey = surveys.get_survey(survey_id, need="editor")
    problem = save(survey, current_user, request.form.getlist("wave_survey"), request.form.getlist("wave_label"))
    if not problem and request.form.get("panel_form") == "1":
        from app import panel_models

        panel_models.save_settings(survey, current_user, request.form)
    if problem:
        flash(problem, "error")  # the page's own save shows it as the reason it was refused (app/autosave.py)
        return redirect(url_for("study_plan.page", survey_id=survey.id) + "#wellen")
    db.session.commit()
    flash(de_en("Wellen gespeichert.", "Waves saved."), "success")
    # the rows come back drawn anew: a new empty row below the last one, and the notes on each wave
    autosave.redraw(render_template("surveys/_waves_form.html", survey=survey, **form_context(current_user, survey)))
    return redirect(url_for("study_plan.page", survey_id=survey.id) + "#wellen")


def participations(survey):
    """The participations of all waves of the survey together (of itself without waves): from REPORT_JOB_THRESHOLD of
    them the merge runs in a process of its own like the other large exports (app/report_jobs.py; Ralf 2026-10-17,
    thread "Rechenknecht")."""
    from app.models import Response

    ids = [r["survey"] for r in of(survey)] or [survey.id]
    return db.session.scalar(db.select(db.func.count(Response.id)).where(Response.survey_id.in_(ids))) or 0


@bp.route("/<sid:survey_id>/waves/merged.zip")
@login_required
@report_jobs.background(count=participations)
@heavy.limited
def export_merged(survey_id):
    """"Wellen zusammenführen" of the Export page: the merged data of the waves the person may download."""
    from flask import Response as FlaskResponse

    from app import audit, export, surveys

    survey = surveys.get_survey(survey_id, need="editor")
    layout = request.args.get("layout") if request.args.get("layout") in LAYOUTS else "wide"
    fmt = request.args.get("fmt") if request.args.get("fmt") in FORMATS else "csv"
    body, waves = zip_file(current_user, survey, layout, fmt, request.args.get("tolerant") == "1")
    if body is None:
        flash(de_en("Zum Zusammenführen braucht es mindestens zwei Wellen mit passendem persönlichem Code, deren Daten Sie "
                    "herunterladen dürfen.",
                    "Merging needs at least two waves with a matching personal code whose data you may download."), "error")
        return redirect(url_for("surveys.data", survey_id=survey.id) + "#wellen")
    audit.note("waves=" + "+".join(str(w["id"]) for w in waves))
    from app.timeutil import local_today  # the date in Europe/Berlin, not the container's UTC (G-07)
    name = f"wellen-{export.slug(survey.title)}-{local_today():%Y%m%d}.zip"
    return FlaskResponse(body, mimetype="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def _codes(survey):
    """The personal code of every row of the survey's stored data pool, in the order of the data file (export.rows):
    only the answers are read, not the whole data set (audit 2026-10-10, P3)."""
    from app.models import Response
    from app.surveys import stored_pool

    pool, item = stored_pool(survey), code_item(survey)
    query = db.select(Response.answers, Response.status).where(
        Response.survey_id == survey.id, Response.source.in_(pool["sources"] or ("real",))).order_by(Response.id)
    out = []
    for answers, status in db.session.execute(query):
        if not pool["all"] and status != Response.COMPLETED:
            continue
        value = item.kind.export_values(item, (answers or {}).get(str(item.id))).get(item.code)
        out.append("".join(str(value or "").split()).upper() or None)
    return out


def page_report(user, survey):
    """The matching report for the Export page (?waves=report), or None. Counted from the codes alone, in a place for
    heavy evaluations (audit 2026-10-10, A2 and P3); {"busy": True} when none became free."""
    if request.args.get("waves") != "report" or not of(survey) or not may_download(user, survey):
        return None
    rows = [r for r in view(user, survey)["rows"] if not r["problem"]]
    if len(rows) < 2:
        return None
    if not heavy.hold():
        return {"busy": True}
    tolerant = request.args.get("tolerant") == "1"
    persons = link([_codes(r["survey"]) for r in rows], list(scheme(rows[0]["survey"])), tolerant)
    return {"report": report(persons, len(rows)), "waves": rows, "tolerant": tolerant}


def form_context(user, survey):
    """What surveys/_waves_form.html needs besides the survey: the rows of view() and the surveys to choose from."""
    from app import panel_models

    shown = view(user, survey)
    rows = panel_models.usable(user, survey)[0] if shown["ready"] else []
    return {"waves_view": shown, "wave_choices": candidates(user),
            "can_waves": may_download(user, survey), "waves": sys.modules[__name__],
            "panel_choices": panel_models.candidates(user, survey, rows) if len(rows) >= 2 else [],
            "panel_settings": panel_models.settings(survey), "panel_waves": len(rows)}
