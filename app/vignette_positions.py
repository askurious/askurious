"""Data files with vignette loops by position (app/vignettes.py; concept V8).

The table of export.rows() keeps a column per vignette ("Q1_17": the answer to vignette 17), which the results page,
the dashboards and the analyses read. With 240 vignettes and 10 per person most of those columns are empty for
everyone. The data files (CSV, SPSS, jamovi, R, codebook) therefore turn the columns of a vignette loop into columns
per position, the order a person saw:
  <loop>_deck            the deck the person got
  <loop>_p<k>            the number of the vignette at position k
  <loop>_p<k>_<factor>   its level of each factor (number from 1)
  <col>_p<k>             each answer column of the loop's questions at position k
The per-vignette columns of those questions are left out of the files; <loop>_order stays."""
import re


def _plans(survey):
    """Per current vignette loop: (node, size of a deck, {vignette number: profile}, the codes of its questions,
    {vignette number: deck number}, the codes of every other question of the survey)."""
    from app import loops, vignettes

    out = []
    for node in vignettes.nodes(survey):
        if not vignettes.plan_current(survey, node):
            continue
        plan = node.get("plan") or {}
        decks = plan.get("decks_rows") or []
        if not decks:
            continue
        number = {r["id"]: n for n, r in enumerate(node.get("rows") or [], start=1)}
        profiles = {number[rid]: p for rid, p in zip(plan.get("ids") or [], plan.get("profiles") or []) if rid in number}
        deck_of = {number[rid]: d for d, deck in enumerate(decks, start=1) for rid in deck if rid in number}
        inside = {b.id for b in loops.blocks_in(survey, node)}
        codes = [it.code for b in survey.blocks if b.id in inside for it in b.items if it.code]
        others = [it.code for b in survey.blocks if b.id not in inside for it in b.items if it.code]
        out.append((node, max(len(d) for d in decks), profiles, codes, deck_of, others))
    return out


def _split(col, numbers):
    m = re.fullmatch(r"(.+)_(\d+)", col)
    if m and int(m.group(2)) in numbers:
        return m.group(1), int(m.group(2))
    return None, None


def _owned(col, base, codes, others):
    """Whether `col` (base `base` plus a vignette number) is an answer column of a question in the loop. The question
    whose code is the longest beginning of the column owns it: an item "bew_ges" (columns bew_ges_1 ...) or "bew_2"
    outside the loop keeps its columns even if the loop has an item "bew" (audit 2026-10-12, W-1)."""
    mine = max((len(c) for c in codes if base == c or base.startswith(c + "_")), default=0)
    theirs = max((len(c) for c in others if col == c or col.startswith(c + "_")), default=0)
    return mine > theirs


def columns(survey, cols):
    """[(column, label)] with the per-vignette columns of vignette loops replaced by columns per position."""
    plans = _plans(survey)
    if not plans:
        return cols
    out = list(cols)
    for node, size, profiles, codes, _, others in plans:
        numbers = set(profiles)
        bases, labels, drop = [], {}, set()
        for col, label in out:
            base, n = _split(col, numbers)
            if base and _owned(col, base, codes, others):
                drop.add(col)
                if base not in labels:
                    bases.append(base)
                    labels[base] = re.sub(r"\s*\[[^\]]*\]$", "", str(label))
        new = _new_columns(node, size, bases, labels)
        kept = [c for c in out if c[0] not in drop]
        at = next((i + 1 for i, (c, _) in enumerate(kept) if c == f"{node['code']}_order"), len(kept))
        out = kept[:at] + new + kept[at:]
    return out


def _new_columns(node, size, bases, labels):
    code = node["code"]
    new = [(f"{code}_deck", f"Vignetten „{node.get('name') or code}“: Deck")]
    for k in range(1, size + 1):
        new.append((f"{code}_p{k}", f"Vignette an Position {k}"))
        new += [(f"{code}_p{k}_{a['code']}", f"Stufe von {a['code']} (Vignette an Position {k})")
                for a in node.get("attributes") or []]
    for base in bases:
        new += [(f"{base}_p{k}", f"{labels[base]} [Position {k}]") for k in range(1, size + 1)]
    return new


def rows(survey, data):
    """Add the values per position to each row dict of export.rows() (in place); returns the rows."""
    plans = _plans(survey)
    if not plans:
        return data
    for node, size, profiles, codes, deck_of, others in plans:
        code = node["code"]
        attrs = node.get("attributes") or []
        for row in data:
            shown = [int(x) for x in str(row.get(f"{code}_order") or "").split("|") if x.isdigit()]
            row[f"{code}_deck"] = deck_of.get(shown[0]) if shown else None
            bases = {}
            for col in list(row):
                base, n = _split(col, set(profiles))
                if base and _owned(col, base, codes, others):
                    bases.setdefault(base, {})[n] = row[col]
            for k in range(1, size + 1):
                n = shown[k - 1] if k <= len(shown) else None
                row[f"{code}_p{k}"] = n
                profile = profiles.get(n) if n else None
                for j, a in enumerate(attrs):
                    row[f"{code}_p{k}_{a['code']}"] = profile[j] + 1 if profile else None
                for base, values in bases.items():
                    row[f"{base}_p{k}"] = values.get(n) if n else None
    return data


def codebook(survey, book, lang=None):
    """Codebook rows (variable, label, type, values, notes) with the per-vignette rows of vignette loops replaced.
    lang: the reader's language of the notes (app.items.base.reader); the type stays the German key ("Zahl"), which
    export.type_text names in the reader's language."""
    from app.items.base import in_lang, reader

    ui = reader(lang)
    plans = _plans(survey)
    if not plans:
        return book
    out = list(book)
    for node, size, profiles, codes, _, others in plans:
        numbers = set(profiles)
        first, drop, order = {}, set(), []
        for row in out:
            base, n = _split(row[0], numbers)
            if base and _owned(row[0], base, codes, others):
                drop.add(row[0])
                if base not in first:
                    first[base] = row
                    order.append(base)
        labels = {b: re.sub(r"\s*\[[^\]]*\]$", "", str(first[b][1])) for b in order}
        new = []
        attrs = node.get("attributes") or []
        # the factor of each level column as the columns are made (_new_columns): a match on the end of the name took
        # "alter" for the column of "x_alter" (code review 2026-10-15, C8)
        level_of = {f"{node['code']}_p{k}_{a['code']}": a for k in range(1, size + 1) for a in attrs}
        for col, label in _new_columns(node, size, order, labels):
            base, _, k = col.rpartition("_p")
            if base in first:
                r = first[base]
                new.append((col, label, r[2], r[3], r[4]))
            elif col.endswith("_deck"):
                new.append((col, label, "Zahl", "", in_lang(ui, "Deck des Versuchsplans, das die Person bekam",
                                                             "deck of the experimental design the person got")))
            elif re.fullmatch(rf"{re.escape(node['code'])}_p\d+", col):
                new.append((col, label, "Zahl", "", in_lang(ui, "Nummer der Vignette (wie in {code}_order)",
                                                             "number of the vignette (as in {code}_order)", code=node["code"])))
            else:
                a = level_of.get(col)
                values = ""
                if a:
                    from app import vignettes

                    f = vignettes.factors(survey, node)
                    labels_a = next((x["level_labels"] for x in f.attributes(f) if x["id"] == a["id"]), [])
                    values = "; ".join(f"{i} = {l}" for i, l in enumerate(labels_a, start=1))
                new.append((col, label, "Zahl", values, in_lang(ui, "Stufe des Faktors", "level of the factor")))
        kept = [r for r in out if r[0] not in drop]
        at = next((i + 1 for i, r in enumerate(kept) if r[0] == f"{node['code']}_order"), len(kept))
        out = kept[:at] + new + kept[at:]
    return out


def hidden(survey, names):
    """The columns of open answers that a public file leaves out (export.open_columns), with the columns per position
    of every hidden per-vignette column added."""
    names = set(names)
    for node, size, profiles, codes, _, others in _plans(survey):
        for col in list(names):
            base, n = _split(col, set(profiles))
            if base and _owned(col, base, codes, others):
                names |= {f"{base}_p{k}" for k in range(1, size + 1)}
    return names
