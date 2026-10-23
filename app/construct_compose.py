"""A composition of the pooled construct data (licence.md section 4): several toolbox constructs in one data set
for a meta-analysis, operator only, on the page Administration > Konstrukte > Zusammenstellung.

One row per participation (study_id + participant_id, the pseudonyms of constructs.pool, the same in every
pooled file), the items of each chosen construct as columns. The rows are built from constructs.pool, so what
counts is the same as in the pooled file of one construct: completed real participations of closed surveys (the
quality assurance of a survey is not applied; the page and the README say so). A construct used twice in one
survey gives the columns of its second use the suffix _2.

Filters: languages, only the current Fassung of each construct, only uses as in the toolbox (not adapted), a
range of months, and only participations that have every chosen construct (a construct counts for a participation
when at least one of its items was answered).
"""
import csv
import io
import zipfile

from app import constructs
from app.extensions import db

BASE = ["study_id", "participant_id", "language", "month"]
BASE_LABELS = {"study_id": "Befragung (Pseudonym)", "participant_id": "Teilnahme (Pseudonym)",
               "language": "Sprache", "month": "Monat der Teilnahme (JJJJ-MM)"}
MAX_CONSTRUCTS = 30
FORMATS = ("csv", "sav", "omv", "rds")


def choices():
    """The toolbox constructs with data in a closed survey, for the page: [(construct, closed surveys, n)]."""
    from app import construct_pool

    return [(row["construct"], row["closed"], row["n"]) for row in construct_pool.overview() if row["closed"]]


def filters(args):
    """The filters of the page's form (a MultiDict)."""
    month = lambda v: v if len(v) == 7 and v[:4].isdigit() and v[4] == "-" and v[5:].isdigit() else ""  # noqa: E731
    return {"langs": {l for l in args.getlist("lang") if isinstance(l, str) and 0 < len(l) <= 8},
            "current": args.get("fassung") == "aktuell", "plain": args.get("angepasst") == "nein",
            "since": month(args.get("von", "").strip()), "until": month(args.get("bis", "").strip()),
            "complete": args.get("vollstaendig") == "1"}


def build(keys, f):
    """The composed data set for the construct keys (in this order) and filters `f` (filters()):
    {"header", "rows", "columns": [{name, label, construct, field, use}], "constructs", "studies"}.
    `field` is a variable of the construct, or "version" / "adapted"; `use` 1 or 2 … (a second use in a survey)."""
    from app.models import Construct

    wanted = [k for k in dict.fromkeys(keys) if isinstance(k, str) and not constructs.is_linked_key(k)][:MAX_CONSTRUCTS]
    found = {c.key: c for c in db.session.scalars(db.select(Construct).filter(Construct.key.in_(wanted)))} if wanted else {}
    chosen = [found[k] for k in wanted if k in found]
    pools = []
    seen_names = {}  # variable -> keys of the constructs that have it: a name in two of them gets the key as prefix
    for c in chosen:
        header, rows = constructs.pool(c.key)
        variables = header[header.index("points") + 1:header.index("added_items")]
        for v in variables:
            seen_names.setdefault(v, set()).add(c.key)
        pools.append((c, header, rows, variables))

    people, order, columns = {}, [], []
    for c, header, rows, variables in pools:
        at = {h: n for n, h in enumerate(header)}
        uses, width = {}, 0
        for row in rows:
            month = row[at["month"]] or ""
            if ((f["langs"] and row[at["language"]] not in f["langs"])
                    or (f["current"] and str(row[at["construct_version"]]) != str(c.seed_version))
                    or (f["plain"] and row[at["adapted"]])
                    or (f["since"] and month < f["since"]) or (f["until"] and month > f["until"])):
                continue
            if all(row[at[v]] in (None, "") for v in variables):
                continue  # the question was shown but not answered: no data of this construct
            who = (row[at["study_id"]], row[at["participant_id"]])
            n = uses[who] = uses.get(who, 0) + 1
            width = max(width, n)
            if who not in people:
                people[who] = {"language": row[at["language"]], "month": month, "values": {}}
                order.append(who)
            values = {v: row[at[v]] for v in variables}
            values.update(version=row[at["construct_version"]], adapted=row[at["adapted"]])
            people[who]["values"][(c.key, n)] = values
        for n in range(1, width + 1):
            suffix, nth = ("", "") if n == 1 else (f"_{n}", f" ({n}. Verwendung)")
            for v in variables:
                name = f"{c.key}_{v}" if len(seen_names[v]) > 1 else v
                columns.append({"name": name + suffix, "label": f"{c.name}: {v}{nth}", "construct": c, "field": v, "use": n})
            columns.append({"name": f"{c.key}_version{suffix}", "label": f"{c.name}: Fassung{nth}", "construct": c,
                            "field": "version", "use": n})
            columns.append({"name": f"{c.key}_adapted{suffix}", "label": f"{c.name}: angepasst (1 = ja){nth}",
                            "construct": c, "field": "adapted", "use": n})

    if f["complete"]:
        order = [who for who in order if all((c.key, 1) in people[who]["values"] for c in chosen)]
    out, studies = [], {}
    for who in order:
        p = people[who]
        row = [who[0], who[1], p["language"], p["month"]]
        for col in columns:
            row.append((p["values"].get((col["construct"].key, col["use"])) or {}).get(col["field"]))
        out.append(row)
        for c in chosen:
            if (c.key, 1) in p["values"]:
                studies.setdefault(who[0], {}).setdefault(c.key, 0)
                studies[who[0]][c.key] += 1
    return {"header": BASE + [col["name"] for col in columns], "rows": out, "columns": columns, "constructs": chosen,
            "studies": sorted(studies.items())}


def dictionary(data):
    """[{name, label, kind, whole, measure, value_labels, missing, decimals, width}] as the file writers take them
    (app/linked_pool.py writes them)."""
    from app import linked_pool

    out = []
    for n, name in enumerate(data["header"]):
        col = data["columns"][n - len(BASE)] if n >= len(BASE) else None
        values = [row[n] for row in data["rows"] if row[n] not in (None, "")]
        numeric = bool(values) and all(linked_pool._is_number(v) for v in values)
        whole = numeric and all(float(v).is_integer() for v in values)
        labels, measure = {}, "nominal" if not numeric else "scale"
        if col is not None and col["field"] not in ("version", "adapted"):
            c = col["construct"]
            labels = linked_pool._value_labels(c, col["field"]) if numeric else {}
            if numeric:
                measure = "ordinal" if (c.spec or {}).get("type") in ("likert", "semantic") else "nominal" if labels else "scale"
        elif col is not None:
            measure = "nominal"
        out.append({"name": name, "label": BASE_LABELS.get(name) or (col["label"] if col else name),
                    "kind": "num" if numeric else "str", "whole": whole, "measure": measure, "value_labels": labels,
                    "missing": [], "decimals": 0 if whole else 2,
                    "width": max([len(str(v).encode("utf-8")) for v in values] or [1])})
    return out


def _csv(header, rows):
    from app.export import safe_text

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(["" if v is None else safe_text(v) for v in row] for row in rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def readme(data, f, when):
    """The README of the ZIP: what is in it, the filters, the contributing studies (pseudonyms only)."""
    keys = [c.key for c in data["constructs"]]
    lines = ["# Zusammengestellte Konstruktdaten (Askurious)", "",
             f"Erstellt am {when}. Grundlage: licence.md Abschnitt 4 (gemeinsame Auswertung durch den Betreiber).", "",
             "## Inhalt", "",
             "- `daten.csv`, `daten.sav`, `daten.omv`, `daten.rds`: eine Zeile je Teilnahme, je Konstrukt seine Items "
             "(umgepolte Items sind rekodiert), dazu Fassung und ob die Befragung das Konstrukt angepasst hat.",
             "- `codebuch.csv`: Variablen, Beschriftungen und Wertelabels.",
             "- `quellen.bib`: die Quellen der Konstrukte.", "",
             "Einbezogen: abgeschlossene echte Teilnahmen geschlossener Befragungen (keine Test- und Dummy-Daten). "
             "Die Qualitätssicherung der einzelnen Befragungen ist nicht angewandt. Befragungen und Teilnahmen tragen "
             "Pseudonyme, dieselben wie in den gepoolten Dateien je Konstrukt.", "",
             "## Konstrukte", ""]
    lines += [f"- `{c.key}`: {c.name} (aktuelle Fassung {c.seed_version})" for c in data["constructs"]]
    chosen = [", ".join(sorted(f["langs"])) or "alle",
              "nur aktuelle Fassung" if f["current"] else "alle Fassungen",
              "nur unverändert wie unter „Konstrukte“" if f["plain"] else "auch angepasste Verwendungen",
              f"Monate {f['since'] or 'von Anfang an'} bis {f['until'] or 'heute'}",
              "nur Teilnahmen mit allen Konstrukten" if f["complete"] else "alle Teilnahmen mit mindestens einem Konstrukt"]
    lines += ["", "## Filter", "", f"Sprachen: {chosen[0]}; {'; '.join(chosen[1:])}.", "",
              "## Beitragende Befragungen", "", f"{len(data['studies'])} Befragungen, {len(data['rows'])} Teilnahmen.", "",
              "| Befragung | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    lines += [f"| {sid} | " + " | ".join(str(n.get(k, 0)) for k in keys) + " |" for sid, n in data["studies"]]
    return "\n".join(lines) + "\n"


def zip_file(data, f, when):
    """The ZIP with data (CSV, SPSS/JASP, jamovi, R), codebook, sources and README."""
    from app import bibliography, linked_pool

    columns = dictionary(data)
    book = [[col["name"], col["label"], "; ".join(f"{int(k) if float(k).is_integer() else k} = {v}"
                                                   for k, v in col["value_labels"].items())] for col in columns]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("daten.csv", _csv(data["header"], data["rows"]))
        for fmt in ("sav", "omv", "rds"):
            z.writestr(f"daten.{fmt}", linked_pool.write(columns, data["rows"], fmt, "Konstrukte (Zusammenstellung)"))
        z.writestr("codebuch.csv", _csv(["variable", "label", "value_labels"], book))
        z.writestr("quellen.bib", bibliography.bibtex(bibliography.collect(data["constructs"])))
        z.writestr("README.md", readme(data, f, when))
    return buf.getvalue()
