"""Part 3 of the survey material (app/study_material.py): all conditions of a randomised survey, how they were assigned
and how they differ (concept "Befragungsmaterial mit sichtbarer Randomisierung"; Data Colada 140).

A reader of framing or vignette material has to find the few words that make the manipulation among paragraphs that are
the same in every condition. app/variants.py works out what differs from the versions alone, without a base text. MODES
are the ways to show it, chosen on the export page (Philipp 2026-11-04):

    columns   the versions side by side, shared sentences once across all columns; preselected on the export page
    inline    one text, with the alternatives in place: [A: mit | B: trotz]
    full      every version in full one after the other, unmarked
    marked    the same with what differs highlighted

A person downloading the PDF starts from DOWNLOAD. What nobody chooses a view for, the pre-registration, its seal and the
packages, takes DEFAULT: they hold the stimuli themselves, not a comparison worked out from them (Philipp 2026-11-04).

Columns need room: from MAX_COLUMNS + 1 conditions on each version stands in full with what differs marked.

Every element of a level is compared with the element in the same place of the other levels; images side by side, with
their prompts compared like texts."""
import html
import itertools
import re

from app import pdf_fonts, variants
from app.items.base import plain
from app.study_material import (Paragraph, _answers, _picture_origin, _safe, colours, level_colour, level_letter, level_name,
                                level_tag, level_tint, page_table, picture_flowables, picture_prompt, tokens, tokens_markup)

MODES = ("columns", "inline", "full", "marked")
DEFAULT = "full"  # what nobody chooses a view for
DOWNLOAD = "marked"  # preselected on the export page, and a download without a choice
MAX_COLUMNS = 4  # more do not fit on a page of A4 next to each other
SHARED_TINT = "#e6e4dc"  # behind words some, but not all, conditions share


def path_note(doc, styles, rands):
    """The box at the start of part 1: this is one path of several, the one of the first levels."""
    from app import study_material

    total = study_material.variants_of(doc.survey)
    levels = ", ".join(f"{node.get('code')}: {len(node.get('children') or [])}" for node in rands)
    return [_box(doc, Paragraph(_safe(doc.say(
        "Dies ist eine von {n} möglichen Varianten (Stufen je Randomisierung: {levels}). Gezeigt ist die Variante mit der "
        "jeweils ersten Stufe; das heißt nicht, dass sie häufiger oder wichtiger wäre. Alle Bedingungen stehen in Teil 3.",
        "This is one of {n} possible variants (levels per randomisation: {levels}). Shown is the variant with the first "
        "level of each; that does not make it more frequent or more important. All conditions are in part 3.").format(
            n=total, levels=levels)), styles["box"]))]


def _box(doc, flowable):
    from reportlab.platypus import TableStyle

    table = page_table([[flowable]], colWidths=[_width()], hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), "#f3f1ea"), ("LINEBEFORE", (0, 0), (0, -1), 2.5, colours()["random"]),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return table


def _width():
    from reportlab.lib.units import mm

    return 174 * mm


# --- how they were assigned -----------------------------------------------------------------------------------


def _reached(doc, node):
    """The conditions a randomisation lies behind, or ""."""
    from app import flow

    path = flow.ancestors(flow.tree(doc.survey), node["id"])
    conds = [plain(flow.condition_text(doc.survey, n.get("cond") or {}, doc.ui)) for n in path if n.get("t") == "branch"]
    return "; ".join(conds)


def _others(doc, node, rands):
    """The randomisations this one is drawn apart from: all but those it lies in or that lie in it."""
    from app import flow

    tree = flow.tree(doc.survey)
    mine = {n.get("id") for n in flow.ancestors(tree, node["id"])}
    return [o.get("code") for o in rands if o is not node and o.get("id") not in mine
            and node["id"] not in {n.get("id") for n in flow.ancestors(tree, o["id"])}]


def assignment(doc, styles, node, rands):
    """The table of one randomisation: its levels, the value each has in the data, and how they are drawn."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Spacer, Table, TableStyle

    children = node.get("children") or []
    n, k = len(children), min(max(node.get("k", 1), 1), max(len(children), 1))
    code = node.get("code")
    drawn = doc.say("{k} von {n} zufällig je Person", "{k} of {n} at random per person").format(k=k, n=n)
    drawn += doc.say("; ausbalanciert: die seltenste Stufe zuerst, je Randomisierung, nicht je Zelle",
                     "; balanced: the rarest level first, per randomisation, not per cell") if node.get("balanced", True) else doc.say(
        "; nicht ausbalanciert", "; not balanced")
    if node.get("shuffle"):
        drawn += doc.say("; Reihenfolge der gezogenen Blöcke zufällig", "; drawn blocks in random order")
    lines = [doc.say("Ziehung: ", "Drawing: ") + drawn]
    others = _others(doc, node, rands)
    if others:
        lines.append(doc.say("Unabhängig gezogen von: ", "Drawn independently of: ") + ", ".join(others))
    reached = _reached(doc, node)
    lines.append(doc.say("Erreicht nur, wer diese Bedingung erfüllt: ", "Reached only by those who meet: ") + reached if reached
                 else doc.say("Erreicht: alle", "Reached by: everyone"))
    head = [Paragraph(f"<b>{_safe(t)}</b>", styles["cell"]) for t in (
        doc.say("Stufe", "Level"), doc.say("Wert in Spalte {c}", "Value in column {c}").format(c=code),
        doc.say("Anteil erwartet", "Expected share"))]
    rows = [head] + [[Paragraph(f'<font color="{level_colour(i)}"><b>{level_letter(i)}</b></font>', styles["cell"]),
                      Paragraph(_safe(level_name(doc.survey, node, i)), styles["cell"]),
                      Paragraph(f"{k}/{n}", styles["cell"])] for i in range(n)]
    table = Table(rows, colWidths=[16 * mm, _width() - 46 * mm, 30 * mm], hAlign="LEFT", repeatRows=1)
    table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.6, tokens()["--border-strong"]), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LEFTPADDING", (0, 0), (0, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return [Paragraph(_safe(line), styles["random"]) for line in lines] + [Spacer(1, 3), table, Spacer(1, 4)]


# --- what a level holds ---------------------------------------------------------------------------------------


def _blocks_of(child, blocks):
    from app import flow

    if child.get("t") == "block":
        found = blocks.get(child.get("block"))
        return [found] if found else []
    return [blocks[n.get("block")] for n, _, _ in flow.walk(child) if n.get("t") == "block" and n.get("block") in blocks]


def _rows(node, blocks):
    """[[item or None per level]]: the elements of the levels, one row per place."""
    levels = [[item for block in _blocks_of(child, blocks) for item in block.items if item.type not in ("page_break", "calc")]
              for child in node.get("children") or []]
    return [list(row) for row in itertools.zip_longest(*levels)]


def _source(doc, item):
    """The wording of an element with its formatting marks, for the comparison; what may not be passed on stays out."""
    if item is None:
        return ""
    if item.id in doc.withheld:
        return doc.say("Wortlaut nicht enthalten", "Wording not included")
    t = item.t(doc.lang)
    if item.type == "consent":  # its body names the people responsible; part 2 shows it where that is allowed
        return t.get("question") or ""
    lines = [t.get("question") or t.get("body") or ""]
    if item.kind.collects_answer:
        lines += [html.unescape(re.sub(r"<[^>]+>", " ", line)).replace("\xa0", " ") for line in _answers(doc, item)]
    return "\n".join(line for line in lines if line)


def _rtl(texts):
    return any(pdf_fonts.has_rtl(plain(text)) for text in texts)


# --- the ways to show a difference ----------------------------------------------------------------------------


def _mark(pieces, level, n, mark):
    """Markup of a level's text: what all n share as it is; what is its own and what only some share marked."""
    out = []
    for tokens_, shared in pieces:
        if not mark or shared == n:
            out.append(tokens_markup(tokens_))
        elif shared == 1:
            out.append(tokens_markup(tokens_, f'<font backColor="{level_tint(level)}"><u color="{level_colour(level)}">', "</u></font>"))
        else:
            out.append(tokens_markup(tokens_, f'<font backColor="{SHARED_TINT}">', "</font>"))
    return "".join(out)


def _framed(flowables, colour):
    """Flowables with a bar in the level's colour at their left."""
    from reportlab.platypus import TableStyle

    table = page_table([[flowables]], colWidths=[_width()], hAlign="LEFT")
    table.setStyle(TableStyle([("LINEBEFORE", (0, 0), (0, -1), 2.5, colour), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                               ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return table


def _one_by_one(doc, styles, node, segs, mark):
    """Each version in full; mark: with what differs marked."""
    n = len(node["children"])
    out = []
    for i in range(n):
        tag = Paragraph(f'<font color="{level_colour(i)}"><b>{_safe(level_tag(doc, node, i))}</b></font>', styles["small"])
        body = _mark(variants.flatten(segs, i), i, n, mark)
        out.append(_framed([tag, Paragraph(body or _nothing(doc), styles["body"])], level_colour(i)))
    return out


def _nothing(doc):
    return f'<font color="{tokens()["--muted"]}"><i>{_safe(doc.say("nichts", "nothing"))}</i></font>'


def _columns(doc, styles, node, segs):
    """columns: side by side; a shared stretch once across all of them. Long rows may split over a page."""
    from reportlab.platypus import TableStyle

    n = len(node["children"])
    width = _width() / n
    rows = [[Paragraph(f'<font color="{level_colour(i)}"><b>{_safe(level_tag(doc, node, i))}</b></font>', styles["cell"])
             for i in range(n)]]
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, tokens()["--border-strong"]),
             ("BACKGROUND", (0, 0), (-1, 0), "#f3f1ea")]
    for i in range(n):
        style.append(("LINEABOVE", (i, 0), (i, 0), 2.5, level_colour(i)))
    for kind, content in variants.rows(segs, n):
        at = len(rows)
        if kind == "differ":
            rows.append([Paragraph(_mark(variants.trim(content[i]), i, n, True) or _nothing(doc), styles["cell"])
                         for i in range(n)])
        else:
            rows.append([Paragraph(tokens_markup(_trim(content)), styles["cell"])] + [""] * (n - 1))
            style.append(("SPAN", (0, at), (-1, at)))
    table = page_table(rows, colWidths=[width] * n, hAlign="LEFT", repeatRows=1)
    table.setStyle(TableStyle(style))
    return [table]


def _trim(tokens_):
    tokens_ = list(tokens_)
    while tokens_ and not tokens_[0][0].strip():
        tokens_.pop(0)
    while tokens_ and not tokens_[-1][0].strip():
        tokens_.pop()
    return tokens_


def _letters(levels):
    return ", ".join(f'<font color="{level_colour(i)}"><b>{level_letter(i)}</b></font>' for i in levels)


def _after(segs, at):
    """The space after a mark in the text, unless the next word is a punctuation mark."""
    following = segs[at + 1].tokens if at + 1 < len(segs) else ()
    return "" if following and following[0][0][0] in ".,;:!?)\"“”" else " "


def _merged(doc, segs):
    """inline: one text, the alternatives in place. A fork of a fork stands inside its branch."""
    out = []
    for at, seg in enumerate(segs):
        if not seg.branches:
            out.append(tokens_markup(seg.tokens))
            continue
        alternatives = []
        for levels, inner in seg.branches:
            if len(levels) == 1:
                words = tokens_markup(_trim(t for s in inner for t in s.tokens),
                                      f'<font backColor="{level_tint(levels[0])}">', "</font>")
            else:
                words = _merged(doc, inner).strip()
            alternatives.append(f"{_letters(levels)}: {words or _nothing(doc)}")
        out.append(" [" + " | ".join(alternatives) + "]" + _after(segs, at))
    return "".join(out)


def _inline(doc, styles, node, segs):
    return [_framed([Paragraph(_merged(doc, segs), styles["body"])], tokens()["--border-strong"])]


def _show(doc, styles, node, segs, mode):
    if mode == "columns" and len(node["children"]) <= MAX_COLUMNS:
        return _columns(doc, styles, node, segs)
    if mode == "inline":
        return _inline(doc, styles, node, segs)
    return _one_by_one(doc, styles, node, segs, mode != "full")


def _legend(doc, styles, mode):
    """How to read the marks of this way of showing."""
    own = f'<font backColor="{level_tint(0)}"><u color="{level_colour(0)}">{_safe(doc.say("nur in dieser Bedingung", "in this condition only"))}</u></font>'
    some = f'<font backColor="{SHARED_TINT}">{_safe(doc.say("in einigen, nicht allen Bedingungen", "in some, not all conditions"))}</font>'
    if mode == "full":
        return Paragraph(_safe(doc.say("Die Fassungen stehen unverändert nacheinander, ohne Markierung.",
                                       "The versions stand unchanged one after the other, without marks.")), styles["small"])
    computed = _safe(doc.say("Der Unterschied ist aus den Fassungen selbst berechnet: Gemeinsam ist, was in allen vorkommt; "
                             "keine Fassung ist die Grundfassung.",
                             "The difference is computed from the versions themselves: shared is what all of them hold; "
                             "no version is the base."))
    if mode == "inline":
        return Paragraph(computed + " " + _safe(doc.say("[A: ... | B: ...] steht für die Stellen, an denen sich die Bedingungen "
                                                        "unterscheiden.", "[A: ... | B: ...] stands where the conditions differ.")),
                         styles["small"])
    return Paragraph(f"{computed} {own}, {some}.", styles["small"])


def _summary(doc, styles, segs, n):
    shared, own = variants.counts(segs, n)
    parts = ", ".join(f"{level_letter(i)}: {own[i]}" for i in range(n))
    return Paragraph(_safe(doc.say("{shared} Wörter in allen Bedingungen gleich; Wörter, die abweichen, je Bedingung: {parts}.",
                                   "{shared} words the same in all conditions; words that differ, per condition: {parts}.").format(
        shared=shared, parts=parts)), styles["variable"])


def _images(doc, styles, node, row, mode):
    """Images of the levels side by side, with where they come from; their prompts compared like texts."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Spacer, Table, TableStyle

    n = len(row)
    columns = min(n, MAX_COLUMNS)
    width = (_width() - 2 * mm * columns) / columns / mm
    cells = []
    for i, item in enumerate(row):
        cell = [Paragraph(f'<font color="{level_colour(i)}"><b>{_safe(level_tag(doc, node, i))}</b></font>', styles["cell"])]
        if item is not None:
            cell += picture_flowables(doc, styles, item, width) if item.type == "image" else [Paragraph(tokens_markup(variants.parse(_source(doc, item))), styles["cell"])]
            if item.type == "image":
                cell.append(Paragraph(_safe(_picture_origin(doc, item)), styles["source"]))
        cells.append(cell)
    grid = [cells[i:i + columns] + [""] * (columns - len(cells[i:i + columns])) for i in range(0, n, columns)]
    table = Table(grid, colWidths=[_width() / columns] * columns, hAlign="LEFT")
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    out = [table]
    prompts = [picture_prompt(item) if item is not None and item.type == "image" else "" for item in row]
    if any(prompts):
        segs = variants.align(prompts)
        out += [Spacer(1, 3), Paragraph(f"<b>{_safe(doc.say('Prompts im Vergleich', 'The prompts compared'))}</b>", styles["small"])]
        if len(segs) == 1 and not segs[0].branches:
            out.append(Paragraph(tokens_markup(segs[0].tokens), styles["body"]))
        else:
            out += [_summary(doc, styles, segs, n), Spacer(1, 2)] + _show(doc, styles, node, segs, "full" if _rtl(prompts) else mode)
    return out


def part(doc, styles, rands, blocks, compare=None):
    """The flowables of part 3: for each randomisation its table, then its levels element by element."""
    from reportlab.platypus import KeepTogether, Spacer

    mode = compare if compare in MODES else DEFAULT
    out = [_legend(doc, styles, mode), Spacer(1, 4)]
    for node in rands:
        head = Paragraph(_safe(node.get("name") or node.get("code")) + f" ({_safe(node.get('code'))})", styles["h2"])
        out.append(KeepTogether([head] + assignment(doc, styles, node, rands)))
        for row in _rows(node, blocks):
            codes = " / ".join(item.code if item is not None else "-" for item in row)
            label = Paragraph(_safe(doc.say("Element: ", "Element: ") + codes), styles["variable"])
            label.keepWithNext = True
            out.append(label)
            if any(item is not None and item.type == "image" for item in row):
                out += _images(doc, styles, node, row, mode)
            else:
                texts = [_source(doc, item) for item in row]
                segs = variants.align(texts)
                if len(segs) == 1 and not segs[0].branches and len(segs[0].levels) == len(row):
                    out.append(Paragraph(_safe(doc.say("In allen Bedingungen gleich:", "The same in all conditions:")), styles["small"]))
                    out.append(Paragraph(tokens_markup(segs[0].tokens), styles["body"]))
                elif segs:
                    out += [_summary(doc, styles, segs, len(row)), Spacer(1, 2)]
                    out += _show(doc, styles, node, segs, "full" if _rtl(texts) else mode)
            out.append(Spacer(1, 6))
    return out
