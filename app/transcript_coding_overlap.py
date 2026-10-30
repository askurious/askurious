"""Interviews kodieren: overlaps of categories (Kuckartz & Rädiker 2022), Ralf 2026-10-30.

A table category × category: how often a passage of the one and a passage of the other overlap in the same interview
(at least one shared character; with "im selben Absatz" also when they stand in the same paragraph). The diagonal
counts the passages of the category itself. On the level "Hauptkategorien" a passage counts for the main category it
stands under (two passages under the same main category are not an overlap); on "alle" every category counts only its
own passages, so a category and the one above it do not overlap by construction. Overlaps show where borders blur or
where topics go together. A cell lists its pairs of passages (place and quote), with the ways to tidy up of the
overview (view the passages, merge).

The overview counts what the actor sees (transcript_coding_review.all_codings: blind double coding, own interviews);
the Excel file of the category system (team only) counts every interview, on the level of all categories."""
from app.transcript_coding import live, top_of, tree
from app.transcript_coding_view import paragraphs, spot, transcript

LEVELS = ("main", "all")


def categories(project, level):
    """The categories of the table, in the order of the category system: the main categories and candidates, or all."""
    mains, candidates = tree(project)
    if level == "main":
        return [m for m, _subs in mains] + list(candidates)
    return [c for m, subs in mains for c in [m] + subs] + list(candidates)


def _para_span(paras, start, end):
    """(first, last) paragraph index a passage touches."""
    first = last = None
    for i, (a, b) in enumerate(paras):
        if a <= end and b >= start:  # touching is enough
            first = i if first is None else first
            last = i
    return (first, last) if first is not None else (-1, -1)


def table(project, items, level="main", paragraph=False):
    """{"cats": [category], "count": {(a, b): n} (a ≠ b, both ways), "diag": {a: n}, "pairs": {(a, b): [pair]}} over
    `items` = [(interview row, placed coding with its category now as "cat")]. A pair is
    {"a": {"where", "quote", "id"}, "b": {...}, "label"} with a of the first category of the key."""
    level = level if level in LEVELS else "main"
    cats = categories(project, level)
    keep = {c.id for c in cats}
    by_id = {c.id: c for c in live(project)}

    def counts_for(cid):
        cat = by_id.get(cid)
        if cat is None:
            return None
        if level == "main":
            top = top_of(cat, by_id)
            return top.id if top is not None and top.id in keep else None
        return cid if cid in keep else None

    per_row = {}
    for row, c in items:
        k = counts_for(c.get("cat"))
        if k is not None:
            per_row.setdefault(row.id, (row, []))[1].append((k, c))
    diag, count, pairs = {}, {}, {}
    for row, found in per_row.values():
        text = transcript(row)
        label = (row.interview or {}).get("label", str(row.id))
        paras = paragraphs(text) if paragraph else None
        spans = [_para_span(paras, c["start"], c["end"]) for _k, c in found] if paragraph else None
        for k, _c in found:
            diag[k] = diag.get(k, 0) + 1
        for i, (ka, a) in enumerate(found):
            for j in range(i + 1, len(found)):
                kb, b = found[j]
                if ka == kb:
                    continue
                hit = min(a["end"], b["end"]) - max(a["start"], b["start"]) > 0
                if not hit and paragraph:
                    (fa, la), (fb, lb) = spans[i], spans[j]
                    hit = fa >= 0 and fb >= 0 and fa <= lb and fb <= la
                if not hit:
                    continue
                for x, y, cx, cy in ((ka, kb, a, b), (kb, ka, b, a)):
                    count[(x, y)] = count.get((x, y), 0) + 1
                    pairs.setdefault((x, y), []).append({
                        "label": label, "rid": row.id,
                        "a": {"id": cx.get("id"), "quote": cx.get("quote") or "", "where": spot(label, text, cx["start"], paras)},
                        "b": {"id": cy.get("id"), "quote": cy.get("quote") or "", "where": spot(label, text, cy["start"], paras)}})
    return {"cats": cats, "count": count, "diag": diag, "pairs": pairs, "level": level, "paragraph": paragraph}


def sheet(project, items):
    """The sheet "Überschneidungen" of the Excel file: all categories, with their numbers."""
    from app.lang import de_en
    from app.transcript_coding import numbers

    t = table(project, items, level="all")
    number = numbers(tree(project)[0])
    head = [de_en("Kategorie", "Category")] + [f"{number.get(c.id, '')} {c.name}".strip() for c in t["cats"]]
    rows = []
    for a in t["cats"]:
        rows.append([f"{number.get(a.id, '')} {a.name}".strip()] +
                    [t["diag"].get(a.id, 0) if a.id == b.id else t["count"].get((a.id, b.id), 0) for b in t["cats"]])
    return (de_en("Überschneidungen", "Overlaps"), head, rows, [30] + [12] * len(t["cats"]))
