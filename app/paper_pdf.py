"""The paper questionnaire as a PDF with fixed geometry (papierfragebogen-konzept.md E2-E5, Ralf
2026-10-10: all as recommended).

Every sheet of a print run is laid out from its own drawn layout (runtime.build_layout: orders, split groups, loop
rows), so it is answered like an online participation. While laying out, every box to tick and every field to write
in is noted with its place in millimetres from the top left of its page: the plan. The scan is read against the plan
(app/paper_scan.py); askurious never needs to know where Word or a browser would have put a box.

On every page: four solid squares in the corners (the scan is straightened with them), along the foot a row of 64
cells, black or white (the sheet's number, the page and a check sum: CODE_BITS), and the number in plain writing.
Boxes are black on white, 4.2 mm, with no colour inside; the survey's design is not used on paper (Dillman et al.,
2014, ch. 12: one column, numbered questions, the box before the answer).

The plan of a sheet: {"pages": n, "fields": [field, ...], "groups": {group: {"q", "item", "row", "one"}}} with
field = {"p": page of the sheet (0-based), "x", "y", "w", "h" (mm), "k": "box" | "write", "pos": index of the page in
the layout, "f": form field, "v": its value (box), "g": group, "item", "row", "lab": a short label for the review,
"fmt": "text" | "num" | "int" (write), "rank": option code (a rank written for ranking)}.

Only Latin script for now (WIN_ANSI languages): the standard fonts of PDF hold no more. Built with reportlab (BSD)."""
import contextvars
import functools
import hashlib
import io
import json
import math
import os
import re
import tempfile

from app.items.base import DONT_KNOW, PLACEHOLDER, dont_know, plain

MM = 72 / 25.4  # points per millimetre
PAGE_W, PAGE_H = 210.0, 297.0
MARK = 7.0  # side of a corner square
MARKS = ((12.0, 12.0), (198.0, 12.0), (12.0, 285.0), (198.0, 285.0))  # centres: top left, top right, bottom left, right
CODE_X, CODE_Y, CODE_CELL, CODE_H = 30.0, 282.0, 2.35, 4.0  # the row of cells: start, top, cell width, height
CODE_BITS = 64  # 40 bits sheet key, 8 bits page, 16 bits CRC
LEFT, RIGHT, TOP, BOTTOM = 18.0, 192.0, 22.0, 272.0
WIDTH = RIGHT - LEFT
BOX = 4.2  # side of a box to tick
FONT, BOLD = "Helvetica", "Helvetica-Bold"
SIZE = 10.0
# survey languages whose texts the standard PDF fonts can print (cp1252); the others are offered once fonts are in
LANGUAGES = ("de", "de-du", "en", "es", "fr", "it", "nl", "pt")
# answered on paper: boxes, writing fields or both; every other type is left out of the paper version
ON_PAPER = ("single_choice", "multiple_choice", "federal_state", "likert", "semantic", "rating", "consent",
            "text_input", "slider", "date_time", "postal_code", "ranking", "constant_sum", "psm", "cloze",
            "conjoint", "maxdiff", "cva")
# choice experiments go on paper with the design version each sheet drew (one page per task, as online); their
# explanation page as text; the anchoring question of a MaxDiff stays online only. The ratings-based conjoint (CVA)
# likewise, its scale as boxes (the slider 0 to 100 as a field to write the number in)
TASKS = ("conjoint", "maxdiff", "cva")
SHOWN = ("info", "image", "page_break", "calc")  # printed (texts, images) or nothing to print, nothing to answer
B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# type size on the sheet: "large" (the default, 12 instead of 10 pt) for readers of every age (tester, Ralf
# 2026-10-11: "nicht so age-inclusive"); every size in this module is the normal one and is scaled where text is
# measured and placed (_width, line_height, Unit.text), so the layout follows on its own. Boxes keep their size.
SCALES = {"normal": 1.0, "large": 1.2}
PRINT_DPI = 300  # images of a print run are made no larger than this at the size they are printed
A85 = False  # PDF streams binary, without the ASCII85 layer (a third of the time of a print run, audit 2026-10-11)
_SCALE = contextvars.ContextVar("paper_scale", default=1.0)  # Crockford's base 32: no I, L, O, U


def sheet_number(key):
    """The 40-bit key as it is printed: 7K3Q-9F2M."""
    s = "".join(B32[(key >> (5 * n)) & 31] for n in range(7, -1, -1))
    return f"{s[:4]}-{s[4:]}"


def parse_number(text):
    """The key from a printed number (case, spaces, dashes and O/I/L typed for 0/1 forgiven), or None."""
    s = re.sub(r"[\s-]", "", str(text or "").upper()).replace("O", "0").replace("I", "1").replace("L", "1")
    if len(s) != 8 or any(ch not in B32 for ch in s):
        return None
    key = 0
    for ch in s:
        key = key * 32 + B32.index(ch)
    return key


def crc16(data):
    """CRC-16/CCITT-FALSE."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def code_bits(key, page):
    """The 64 cells of a page's code, most significant bit first: key (40), page (8), CRC-16 over both (16)."""
    body = (key << 8) | (page & 0xFF)
    value = (body << 16) | crc16(body.to_bytes(6, "big"))
    return [(value >> (CODE_BITS - 1 - n)) & 1 for n in range(CODE_BITS)]


def decode_bits(bits):
    """(key, page) from 64 cells read, or None if the check sum does not fit."""
    if len(bits) != CODE_BITS:
        return None
    value = 0
    for b in bits:
        value = (value << 1) | (1 if b else 0)
    body, crc = value >> 16, value & 0xFFFF
    if crc16(body.to_bytes(6, "big")) != crc or body == 0:
        return None
    return body >> 8, body & 0xFF


def code_cells():
    """(x, y, w, h) in mm of every cell of the code."""
    return [(CODE_X + n * CODE_CELL, CODE_Y, CODE_CELL, CODE_H) for n in range(CODE_BITS)]


# --- texts -------------------------------------------------------------------------------------


def _safe(text):
    """What the standard fonts can print: cp1252, the rest as "?"."""
    return str(text or "").encode("cp1252", "replace").decode("cp1252")


def _width(text, size, bold=False):
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(_safe(text), BOLD if bold else FONT, size * _SCALE.get()) / MM


def wrap(text, width, size=SIZE, bold=False):
    """Lines of `text` no wider than `width` mm; paragraphs ("\\n") kept, an overlong word broken. Kept per text,
    width, size and type scale: a print run lays out the same texts on every sheet (audit 2026-10-11, M11)."""
    return list(_wrapped(str(text or ""), float(width), float(size), bool(bold), _SCALE.get()))


@functools.lru_cache(maxsize=8192)
def _wrapped(text, width, size, bold, scale):
    token = _SCALE.set(scale)
    try:
        return tuple(_wrap(text, width, size, bold))
    finally:
        _SCALE.reset(token)


def _wrap(text, width, size=SIZE, bold=False):
    out = []
    for para in str(text or "").replace("\r\n", "\n").split("\n"):
        words, line = para.split(), ""
        if not words:
            out.append("")
            continue
        for word in words:
            trial = f"{line} {word}" if line else word
            if _width(trial, size, bold) <= width:
                line = trial
                continue
            if line:
                out.append(line)
            while _width(word, size, bold) > width and len(word) > 1:  # a word longer than the line
                cut = len(word)
                while cut > 1 and _width(word[:cut], size, bold) > width:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            line = word
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return out


def line_height(size):
    return size * _SCALE.get() * 1.3 / MM


class Unit:
    """A piece of the page that is never split: its height and what to draw, with y from its own top."""

    def __init__(self):
        self.h, self.ops, self.fields = 0.0, [], []

    def text(self, x, y, text, size=SIZE, bold=False, grey=0.0, align="left"):
        self.ops.append(("text", x, y, size * _SCALE.get(), bold, text, grey, align))

    def para(self, x, y, text, width, size=SIZE, bold=False, grey=0.0):
        """Wrapped text from (x, y); returns the y below it."""
        for line in wrap(text, width, size, bold):
            self.text(x, y, line, size, bold, grey)
            y += line_height(size)
        return y

    def rect(self, x, y, w, h, width=0.3, fill=None, stroke=True):
        self.ops.append(("rect", x, y, w, h, width, fill, stroke))

    def box(self, x, y, field):
        self.rect(x, y, BOX, BOX, 0.3, 1.0)
        self.fields.append({**field, "k": "box", "x": round(x, 2), "y": round(y, 2), "w": BOX, "h": BOX})

    def write(self, x, y, w, h, field):
        self.rect(x, y, w, h, 0.25, 1.0)
        self.fields.append({"fmt": "text", **field, "k": "write", "x": round(x, 2), "y": round(y, 2),
                            "w": round(w, 2), "h": round(h, 2)})

    def image(self, path, x, y, w, h):
        self.ops.append(("image", x, y, w, h, path))


class Layout:
    """Places units on pages, top to bottom; a unit that does not fit starts a new page."""

    def __init__(self):
        self.pages, self.y = [[]], TOP
        self.fields = []

    def new_page(self):
        self.pages.append([])
        self.y = TOP

    def room(self):
        return BOTTOM - self.y

    def place(self, unit, gap=0.0):
        if self.y > TOP and unit.h + gap > self.room():
            self.new_page()
        elif self.y > TOP:
            self.y += gap
        page = len(self.pages) - 1
        # every operation has its y in third place
        self.pages[page].extend((op[0], op[1], op[2] + self.y, *op[3:]) for op in unit.ops)
        for f in unit.fields:
            self.fields.append({**f, "p": page, "y": round(f["y"] + self.y, 2)})
        self.y += unit.h


# --- what a sheet holds ------------------------------------------------------------------------


class Ctx:
    def __init__(self, survey, lang, layout):
        self.survey, self.lang, self.layout = survey, lang, layout
        self.groups, self.number, self.variables = {}, 0, {}
        self.numbers = {}  # item id: the number it was printed with (the latest, in a loop)

    def fill(self, text):
        """Loop values put in; an answer to an earlier question (piping) as its text for an empty answer, or a gap."""
        def sub(m):
            if m.group(1) in self.variables:
                return str(self.variables[m.group(1)])
            return m.group(2).strip() if m.group(2) is not None else "________"

        return plain(PLACEHOLDER.sub(sub, str(text or "")))

    def tr(self, de, en):
        from app.ui_i18n import tr

        return tr(self.lang, de, en)


def _group(ctx, key, item, row, one, label=""):
    q = ctx.fill(item.t(ctx.lang).get("question") or "")
    ctx.groups[key] = {"q": (q[:160] + ("…" if len(q) > 160 else "")), "item": item.id, "row": row, "one": one,
                       **({"lab": label[:80]} if label else {})}
    return key


def _head(ctx, item, unit, numbered=True):
    """The question with its number; returns the y below it."""
    text = ctx.fill(item.t(ctx.lang).get("question") or "")
    y = 0.0
    if numbered:
        ctx.number += 1
        ctx.numbers[item.id] = ctx.number
        unit.text(LEFT, y, f"{ctx.number}.", SIZE, True)
    indent = 8 if numbered else 0
    lines = wrap(text, WIDTH - indent, SIZE, True)
    for line in lines:
        unit.text(LEFT + indent, y, line, SIZE, True)
        y += line_height(SIZE)
    return y + (1.5 if lines else 0)


def _base(ctx, item, pos, row):
    return {"pos": pos, "item": item.id, "row": row}


def _options_list(ctx, item, unit, y, pos, row, options, one, field_name, text_field=None):
    """Options one below the other, each with a box; two columns for many short options."""
    g = _group(ctx, field_name if one else f"{field_name}*", item, row, one)
    labels = [ctx.fill(o["label"]) for o in options]
    two = len(options) > 8 and all(_width(l, SIZE) < WIDTH / 2 - 14 for l in labels) and not any(o.get("text") for o in options)
    col_w = (WIDTH - 8) / 2 if two else WIDTH - 8
    rows_n = (len(options) + 1) // 2 if two else len(options)
    top = y
    for n, (o, label) in enumerate(zip(options, labels)):
        col, r = (n // rows_n, n % rows_n) if two else (0, n)
        x = LEFT + 8 + col * col_w
        yy = top + r * 6.2 if two else y
        unit.box(x, yy + 0.2, {**_base(ctx, item, pos, row), "f": field_name, "v": o["code"], "g": g, "lab": label[:80]})
        lines = wrap(label, col_w - 8 - (55 if o.get("text") else 0), SIZE)
        ly = yy + 0.6
        for line in lines:
            unit.text(x + 6.5, ly, line)
            ly += line_height(SIZE)
        if o.get("text") and text_field:
            unit.write(x + 8 + min(_width(label, SIZE) + 4, col_w - 60), yy - 0.8, 52, 7,
                       {**_base(ctx, item, pos, row), "f": text_field(o), "g": g, "lab": label[:80]})
        if not two:
            y = max(ly, yy + 6.0) + 0.6
    return (top + rows_n * 6.2) if two else y


def _choice(ctx, item, pos, row):
    kind = item.kind
    unit = Unit()
    y = _head(ctx, item, unit)
    one = item.type != "multiple_choice"
    options = kind.options(item, ctx.lang)
    unit.h = _options_list(ctx, item, unit, y, pos, row, options, one, f"i{item.id}",
                           text_field=lambda o: f"i{item.id}_t_{o['id']}") + 2
    return [unit]


def _scale_head(ctx, labels, first, col_w, dk):
    """The head of a matrix: the labels of the scale over their columns."""
    unit = Unit()
    names = labels + ([ctx.tr("weiß nicht", "don't know")] if dk else [])
    lines = [wrap(n, col_w - 1, 7.5) for n in names]
    h = max((len(l) for l in lines), default=1) * line_height(7.5) + 1.5
    for n, ls in enumerate(lines):
        cx = LEFT + first + (n + 0.5) * col_w
        yy = h - len(ls) * line_height(7.5) - 1.0
        for line in ls:
            unit.text(cx, yy, line, 7.5, False, 0.2, "center")
            yy += line_height(7.5)
    unit.h = h
    return unit


def _matrix(ctx, item, pos, row, rows, labels, dk, field, right=None):
    """Likert statements or semantic pairs: a row per entry, a box per point of the scale."""
    points = len(labels)
    cols = points + (1 if dk else 0)
    right_w = 38.0 if right else 0.0
    first = min(70.0, max(40.0, WIDTH - right_w - cols * 13.0))
    col_w = (WIDTH - first - right_w) / cols
    head = Unit()
    y = _head(ctx, item, head)
    scale = _scale_head(ctx, labels, first, col_w, dk) if any(labels) or dk else None
    units = []
    for n, entry in enumerate(rows):
        unit = Unit()
        top = 0.0
        if n == 0:
            unit.ops, unit.fields = head.ops, head.fields
            top = y
            if scale:
                unit.ops = unit.ops + [(op[0], op[1], op[2] + top, *op[3:]) for op in scale.ops]
                top += scale.h
        text = ctx.fill(entry["text"])
        lines = wrap(text, first - 3, 9.5)
        r_lines = wrap(ctx.fill(entry.get("right") or ""), right_w - 3, 9.5) if right else []
        h = max(len(lines), len(r_lines), 1) * line_height(9.5) + 2.6
        h = max(h, BOX + 2.6)
        if n % 2 == 0:
            unit.rect(LEFT, top, WIDTH, h, 0, 0.93, stroke=False)
        ty = top + 1.4
        for line in lines:
            if right:  # a semantic differential: the left word stands right before the scale
                unit.text(LEFT + first - 2, ty, line, 9.5, False, 0.0, "right")
            else:
                unit.text(LEFT + 1.5, ty, line, 9.5)
            ty += line_height(9.5)
        ry = top + 1.4
        for line in r_lines:
            unit.text(RIGHT - right_w + 2, ry, line, 9.5)
            ry += line_height(9.5)
        g = _group(ctx, f"{field}_{entry['id']}", item, row, True, text)
        by = top + (h - BOX) / 2
        for p in range(cols):
            value = str(p + 1) if p < points else DONT_KNOW
            unit.box(LEFT + first + (p + 0.5) * col_w - BOX / 2, by,
                     {**_base(ctx, item, pos, row), "f": f"{field}_{entry['id']}", "v": value, "g": g,
                      "lab": labels[p] if p < points and labels[p] else (str(p + 1) if p < points else "dk")})
        unit.h = top + h
        units.append(unit)
    if units:
        units[-1].h += 2
    return units


def _likert(ctx, item, pos, row):
    from app import runtime

    kind = item.kind
    order = (ctx.layout.get("stmt") or {}).get(runtime.answer_key(item.id, row))
    labels = [ctx.fill(l) for l in kind.labels(item, ctx.lang)]
    rows = [{"id": s["id"], "text": s["text"]} for s in kind.statements(item, ctx.lang, order=order)]
    return _matrix(ctx, item, pos, row, rows, labels, dont_know(item), f"i{item.id}")


def _semantic(ctx, item, pos, row):
    from app import runtime

    kind = item.kind
    order = (ctx.layout.get("stmt") or {}).get(runtime.answer_key(item.id, row))
    points = item.config["points"]
    rows = [{"id": p["id"], "text": p["left"], "right": p["right"]} for p in kind.pairs(item, ctx.lang, order=order)]
    return _matrix(ctx, item, pos, row, rows, [""] * points, False, f"i{item.id}", right=True)


def _number_field(ctx, item, unit, y, pos, row, field, label="", width=40.0, fmt="num", hint=""):
    """A field to write a number or a short text in, with its label before it."""
    x = LEFT + 8
    if label:
        lines = wrap(label, 80, SIZE)
        ly = y + 1.8
        for line in lines:
            unit.text(x, ly, line)
            ly += line_height(SIZE)
        x = LEFT + 92
    else:
        ly = y
    unit.write(x, y, width, 8.5, {**_base(ctx, item, pos, row), "f": field, "fmt": fmt,
                                  "g": _group(ctx, field, item, row, False, label), "lab": label[:80]})
    if hint:
        unit.text(x + width + 2.5, y + 2.4, hint, 8, False, 0.35)
    return max(ly, y + 8.5) + 2.0


def _rating(ctx, item, pos, row):
    kind = item.kind
    unit = Unit()
    y = _head(ctx, item, unit)
    lo, hi = kind.bounds(item)
    step = float(item.config.get("step") or 1)
    if float(lo).is_integer() and float(hi).is_integer() and step == 1 and hi - lo <= 10:
        g = _group(ctx, f"i{item.id}", item, row, True)
        x = LEFT + 8
        for v in range(int(lo), int(hi) + 1):
            unit.box(x, y, {**_base(ctx, item, pos, row), "f": f"i{item.id}", "v": str(v), "g": g, "lab": str(v)})
            unit.text(x + BOX / 2, y + BOX + 0.8, str(v), 8, False, 0.2, "center")
            x += 12
        if dont_know(item):
            x += 4
            unit.box(x, y, {**_base(ctx, item, pos, row), "f": f"i{item.id}_dk", "v": "1", "g": g, "lab": "dk"})
            unit.text(x + 6, y + 0.6, ctx.tr("weiß nicht", "don't know"), 9)
        unit.h = y + BOX + 6
    else:
        unit.h = _number_field(ctx, item, unit, y, pos, row, f"i{item.id}", hint=f"{lo:g} – {hi:g}") + 1
    return [unit]


def _consent(ctx, item, pos, row):
    kind = item.kind
    t = item.t(ctx.lang)
    units = []
    head = Unit()
    y = _head(ctx, item, head, numbered=False)
    head.h = y
    units.append(head)
    body = ctx.fill(t.get("body") or "")
    notice = kind.pool_notice(item, ctx.lang) if hasattr(kind, "pool_notice") else ""
    for para in [p for p in (body + ("\n\n" + ctx.fill(notice) if notice else "")).split("\n\n") if p.strip()]:
        unit = Unit()
        lines = []
        for line in para.split("\n"):
            line = line.strip()
            lines += wrap(("• " + line[2:]) if line.startswith("- ") else line, WIDTH, 9.5)
        yy = 0.0
        for line in lines:
            unit.text(LEFT, yy, line, 9.5)
            yy += line_height(9.5)
        unit.h = yy + 1.5
        units.append(unit)
    end = Unit()
    y = 1.0
    for c in kind.confirmations(item, ctx.lang):
        label = ctx.fill(c["text"])
        g = _group(ctx, f"i{item.id}_c_{c['key']}", item, row, False, label)
        end.box(LEFT, y, {**_base(ctx, item, pos, row), "f": f"i{item.id}_c_{c['key']}", "v": "1", "g": g, "lab": label[:80]})
        yy = y + 0.6
        for line in wrap(label, WIDTH - 8, 9.5):
            end.text(LEFT + 6.5, yy, line, 9.5)
            yy += line_height(9.5)
        y = max(yy, y + 5.5) + 1.0
    g = _group(ctx, f"i{item.id}", item, row, True)
    x = LEFT
    for value, key in (("1", "agree"), ("0", "disagree")):
        label = ctx.fill(kind.ui_text(item, ctx.lang, key))
        if not label:
            continue
        end.box(x, y + 1, {**_base(ctx, item, pos, row), "f": f"i{item.id}", "v": value, "g": g, "lab": label[:80]})
        end.text(x + 6.5, y + 1.6, label, SIZE, True)
        x += 12 + _width(label, SIZE, True)
    end.h = y + BOX + 5
    units.append(end)
    return units


def _text_input(ctx, item, pos, row):
    unit = Unit()
    y = _head(ctx, item, unit)
    v = item.config.get("validation", "none")
    if v in ("integer", "decimal"):
        y = _number_field(ctx, item, unit, y, pos, row, f"i{item.id}", fmt="int" if v == "integer" else "num")
    elif v in ("plz_de",):
        y = _number_field(ctx, item, unit, y, pos, row, f"i{item.id}", width=30, fmt="int")
    else:
        h = 26.0 if item.config.get("multiline") else 9.0
        unit.write(LEFT + 8, y, WIDTH - 8, h, {**_base(ctx, item, pos, row), "f": f"i{item.id}",
                                                "g": _group(ctx, f"i{item.id}", item, row, False)})
        y += h + 2
    unit.h = y + 1
    return [unit]


def _slider(ctx, item, pos, row):
    from app import runtime

    kind = item.kind
    unit = Unit()
    y = _head(ctx, item, unit)
    c = item.config
    hint = f"{kind.show(item, c.get('min', 0), ctx.lang)} – {kind.show(item, c.get('max', 100), ctx.lang)}"
    order = (ctx.layout.get("stmt") or {}).get(runtime.answer_key(item.id, row))
    for r in kind.rows(item, ctx.lang, order=order):
        y = _number_field(ctx, item, unit, y, pos, row, f"i{item.id}_{r['id']}", ctx.fill(r.get("text") or ""), hint=hint)
    unit.h = y + 1
    return [unit]


def _date_time(ctx, item, pos, row):
    kind = item.kind
    unit = Unit()
    y = _head(ctx, item, unit)
    names = {"d": ctx.tr("Tag", "Day"), "m": ctx.tr("Monat", "Month"), "y": ctx.tr("Jahr", "Year"),
             "h": ctx.tr("Stunde", "Hour"), "n": ctx.tr("Minute", "Minute")}
    widths = {"d": 12.0, "m": 12.0, "y": 20.0, "h": 12.0, "n": 12.0}
    x = LEFT + 8
    for part in kind.layout(item, ctx.lang):
        unit.write(x, y, widths[part], 8.5, {**_base(ctx, item, pos, row), "f": f"i{item.id}_{part}", "fmt": "int",
                                             "g": _group(ctx, f"i{item.id}_{part}", item, row, False, names[part]),
                                             "lab": names[part]})
        unit.text(x, y + 9.5, names[part], 7.5, False, 0.35)
        x += widths[part] + 4
    if dont_know(item):
        x += 4
        g = _group(ctx, f"i{item.id}_dk", item, row, False)
        unit.box(x, y + 2, {**_base(ctx, item, pos, row), "f": f"i{item.id}_dk", "v": "1", "g": g, "lab": "dk"})
        unit.text(x + 6, y + 2.6, ctx.tr("weiß nicht", "don't know"), 9)
    unit.h = y + 15
    return [unit]


def _postal_code(ctx, item, pos, row):
    unit = Unit()
    y = _head(ctx, item, unit)
    unit.h = _number_field(ctx, item, unit, y, pos, row, f"i{item.id}", width=30, fmt="int") + 1
    return [unit]


def _ranking(ctx, item, pos, row):
    unit = Unit()
    y = _head(ctx, item, unit)
    unit.text(LEFT + 8, y, ctx.tr("Bitte nummerieren Sie: 1 = an erster Stelle.", "Please number them: 1 = first."), 9, False, 0.3)
    y += line_height(9) + 1.5
    from app import runtime

    order = (ctx.layout.get("stmt") or {}).get(runtime.answer_key(item.id, row))
    options = item.kind.options(item, ctx.lang)
    if order:
        rank = {oid: n for n, oid in enumerate(order)}
        options.sort(key=lambda o: rank.get(o["id"], len(rank)))
    g = _group(ctx, f"i{item.id}", item, row, False)
    for o in options:
        label = ctx.fill(o["label"])
        unit.write(LEFT + 8, y, 12, 8, {**_base(ctx, item, pos, row), "f": f"i{item.id}", "rank": o["code"], "fmt": "int",
                                        "g": g, "lab": label[:80]})
        ly = y + 2.2
        for line in wrap(label, WIDTH - 26, SIZE):
            unit.text(LEFT + 24, ly, line)
            ly += line_height(SIZE)
        y = max(ly, y + 8) + 2.5
    unit.h = y + 1
    return [unit]


def _constant_sum(ctx, item, pos, row):
    from app import runtime, units

    kind = item.kind
    unit = Unit()
    y = _head(ctx, item, unit)
    order = (ctx.layout.get("stmt") or {}).get(runtime.answer_key(item.id, row))
    options = kind.options(item, ctx.lang)
    if order:
        rank = {oid: n for n, oid in enumerate(order)}
        options.sort(key=lambda o: rank.get(o["id"], len(rank)))
    for o in options:
        y = _number_field(ctx, item, unit, y, pos, row, f"i{item.id}_{o['code']}", ctx.fill(o["label"]), width=28)
    c = item.config
    if not kind.personal(item):
        total = units.fmt(c.get("total", 100), c.get("format"), c.get("currency"), item.text("unit", ctx.lang) or "", ctx.lang)
        unit.text(LEFT + 8, y + 0.5, ctx.tr("Summe", "Total") + f": {total}", SIZE, True)
        y += line_height(SIZE) + 1
    unit.h = y + 1
    return [unit]


def _psm(ctx, item, pos, row):
    kind = item.kind
    t = item.t(ctx.lang)
    unit = Unit()
    y = _head(ctx, item, unit)
    if t.get("product"):
        y = unit.para(LEFT + 8, y, ctx.fill(t["product"]), WIDTH - 8, 9.5) + 1.5
    unit_label = kind.unit_label(item, ctx.lang) or ""
    for k in kind.sequence(item):
        y = _number_field(ctx, item, unit, y, pos, row, f"i{item.id}_{k}", ctx.fill(t.get("q_" + k, "")), width=28,
                          hint=unit_label)
    unit.h = y + 1
    return [unit]


def _cloze(ctx, item, pos, row):
    kind = item.kind
    unit = Unit()
    y = _head(ctx, item, unit)
    text = ctx.fill(item.t(ctx.lang).get("body") or item.t(ctx.lang).get("text") or "")
    n = [0]

    def gap(_m):
        n[0] += 1
        return f"({n[0]}) ______"

    shown = re.sub(r"\[\s*\]", gap, text)
    y = unit.para(LEFT + 8, y, shown, WIDTH - 8, SIZE) + 1.5
    for k in range(1, len(kind.gaps(item, ctx.lang)) + 1):
        y = _number_field(ctx, item, unit, y, pos, row, f"i{item.id}_{k}", f"({k})", width=60, fmt="text")
    unit.h = y + 1
    return [unit]


def _task_head(ctx, item, unit, spec, total):
    """The question of a choice task with "Aufgabe n von N" before it; returns the y below it."""
    text = ctx.fill(item.t(ctx.lang).get("question") or "")
    n = spec.get("n") or ((spec.get("task") or 0) + 1)
    ctx.number += 1
    ctx.numbers[item.id] = ctx.number
    unit.text(LEFT, 0, f"{ctx.number}.", SIZE, True)
    y = 0.0
    lines = wrap(text, WIDTH - 8, SIZE, True)
    for line in lines:
        unit.text(LEFT + 8, y, line, SIZE, True)
        y += line_height(SIZE)
    unit.text(LEFT + 8, y + 0.5, ctx.tr("Aufgabe {n} von {total}", "Task {n} of {total}").format(n=n, total=total),
              8.5, False, 0.35)
    return y + line_height(8.5) + 2


def _conjoint_task(ctx, item, pos, row, spec):
    """One choice task: the options side by side with their levels, a box under each, "none" and the dual question."""
    from app import holdouts

    kind, c, t = item.kind, item.config, item.t(ctx.lang)
    state = (ctx.layout.get("cj") or {}).get(str(item.id)) or {}
    if "holdout" in spec:
        profiles = holdouts.cbc_profiles(item, spec["holdout"])
        key = f"h{spec['holdout']}"
    else:
        version = state.get("version", 0)
        if not kind.version_known(item, version):
            return []
        profiles = c["design"]["versions"][version][spec.get("task", 0)]
        key = f"t{spec.get('task', 0)}"
    if not profiles:
        return []
    config_attrs = c.get("attributes", [])
    index = {a["id"]: n for n, a in enumerate(config_attrs)}
    attrs = kind.attributes(item, ctx.lang, order=state.get("order"))
    # as online (items/conjoint/participant.html): with "image" one picture per option and no table, with "both" the
    # picture and below it the attributes not in the picture; a picture missing on disk falls back to the table
    images = _cbc_images(item, profiles) if kind.uses_images(item) else None
    if images and c.get("display") == "image":
        attrs = []
    elif images and not c.get("show_visual_in_table"):
        attrs = [a for a in attrs if not a.get("visual")]
    level_images = kind.level_images_on(item)  # "replace" or "add": a small picture per level in the table
    unit = Unit()
    y = _task_head(ctx, item, unit, spec, kind.shown_tasks(item))
    scene = t.get("scene") or c.get("scene")
    if scene:
        y = unit.para(LEFT + 8, y, ctx.fill(scene), WIDTH - 8, 9.5) + 1.5
    alts = len(profiles)
    alt_label = ctx.fill(t.get("alt_label") or "Option")
    y, first, col = _profiles_table(ctx, item, unit, y, profiles, attrs, index, images, level_images,
                                    [f"{alt_label} {a + 1}" for a in range(alts)])
    field = f"i{item.id}_{key}"
    g = _group(ctx, field, item, row, True, ctx.tr("Wahl", "choice"))
    y += 2
    for a in range(alts):
        unit.box(LEFT + first + (a + 0.5) * col - BOX / 2, y,
                 {**_base(ctx, item, pos, row), "f": field, "v": str(a + 1), "g": g, "lab": f"{alt_label} {a + 1}"})
    y += BOX + 3
    if c.get("none") and not kind.dual(item):
        label = ctx.fill(t.get("none_label") or "")
        unit.box(LEFT + first, y, {**_base(ctx, item, pos, row), "f": field, "v": "0", "g": g, "lab": label[:80]})
        unit.text(LEFT + first + 6.5, y + 0.6, label, SIZE)
        y += BOX + 3
    if kind.dual(item):
        dual = f"i{item.id}_d{key}"
        gd = _group(ctx, dual, item, row, True, ctx.fill(t.get("dr_question") or ""))
        y = unit.para(LEFT + 8, y, ctx.fill(t.get("dr_question") or ""), WIDTH - 8, 9.5) + 1
        x = LEFT + 8
        for value, text_key in (("1", "dr_yes"), ("0", "dr_no")):
            label = ctx.fill(t.get(text_key) or "")
            unit.box(x, y, {**_base(ctx, item, pos, row), "f": dual, "v": value, "g": gd, "lab": label[:80]})
            unit.text(x + 6.5, y + 0.6, label, SIZE)
            x += 14 + _width(label, SIZE)
        y += BOX + 3
    unit.h = y + 1
    return [unit]


def _profiles_table(ctx, item, unit, y, profiles, attrs, index, images, level_images, heads):
    """The offers of a task side by side: a head per column, the pictures (display with images), then a row per
    attribute with the level of each offer (as text or its small picture). Returns (y below, width of the first
    column, width of an offer's column)."""
    first = 42.0 if attrs else 0.0
    col = (WIDTH - first) / len(profiles)
    for a, head in enumerate(heads):
        unit.text(LEFT + first + (a + 0.5) * col, y, head, 9.5, True, 0.0, "center")
    y += line_height(9.5) + 1
    if images:
        side = min(col - 4, 70.0)
        sizes = [(side, side * ih / max(iw, 1)) for _, iw, ih in images]
        sizes = [(w * min(1.0, side / h), min(h, side)) for w, h in sizes]  # tall pictures no higher than wide
        top = max(h for _, h in sizes)
        for a, ((path, _, _), (w, h)) in enumerate(zip(images, sizes)):
            unit.image(path, LEFT + first + (a + 0.5) * col - w / 2, y + (top - h) / 2, w, h)
        y += top + 2
    for n, attr in enumerate(attrs):
        a_index = index.get(attr["id"])
        cells, pics = [], []
        for profile in profiles:
            level = profile[a_index] if a_index is not None and a_index < len(profile) else None
            label = attr["level_labels"][level] if level is not None and level < len(attr["level_labels"]) else ""
            pic = _level_image(item, attr, level) if level_images else None
            pics.append(pic)
            cells.append([] if pic and level_images == "replace" else wrap(ctx.fill(label) or "–", col - 3, 9))
        pic_h = 10.0 if any(pics) else 0.0
        head = wrap(ctx.fill(attr["label"]), first - 3, 9, True)
        h = max(len(head) * line_height(9), pic_h + (1 if pic_h else 0) + max(len(cl) for cl in cells) * line_height(9)) + 2.4
        if n % 2 == 0:
            unit.rect(LEFT, y, WIDTH, h, 0, 0.93, stroke=False)
        yy = y + 1.2
        for line in head:
            unit.text(LEFT + 1.5, yy, line, 9, True)
            yy += line_height(9)
        for a, lines in enumerate(cells):
            yy = y + 1.2
            if pics[a]:
                path, iw, ih = pics[a]
                w = min(col - 4, pic_h * iw / max(ih, 1))
                unit.image(path, LEFT + first + (a + 0.5) * col - w / 2, yy, w, w * ih / max(iw, 1))
            if pic_h:
                yy += pic_h + 1
            for line in lines:
                unit.text(LEFT + first + (a + 0.5) * col, yy, line, 9, False, 0.0, "center")
                yy += line_height(9)
        y += h
    return y, first, col


def _picture(item, filename):
    """(path, width px, height px) of an image of the item's survey, or None if the file is missing."""
    import os

    from flask import current_app
    from PIL import Image as PILImage

    if not filename:
        return None
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], item.block.survey.uid, filename)
    if not os.path.isfile(path):
        return None
    try:
        with PILImage.open(path) as img:
            return path, img.size[0], img.size[1]
    except Exception:  # noqa: BLE001 - a broken file is printed as text
        return None


def _cbc_images(item, profiles):
    """The picture of every option of a task (display "image" or "both"), or None if one is missing: then the task
    is printed as a table with text, as before (a sheet is never refused for a picture)."""
    kind = item.kind
    if not kind.visual_attributes(item):
        return None
    out = []
    for profile in profiles:
        asset = kind.asset_for_key(item, kind.image_key(item, profile))
        pic = _picture(item, asset.filename if asset else None)
        if pic is None:
            return None
        out.append(pic)
    return out


def _level_image(item, attr, level):
    """The small picture of a level (display "Tabelle mit Bildern je Stufe"), or None."""
    if level is None or level >= len(attr.get("level_images") or []):
        return None
    return _picture(item, attr["level_images"][level])


def _cva_task(ctx, item, pos, row, spec):
    """One task of a ratings-based conjoint: the offer (or the two offers of a pair) as a table, below it the scale as
    boxes with the end labels; the slider 0 to 100 as a field for the number."""
    from app.items.cva import DEFAULT_TEXTS

    kind, c, t = item.kind, item.config, item.t(ctx.lang)
    state = (ctx.layout.get("cj") or {}).get(str(item.id)) or {}
    profiles, key = kind.page_profiles(item, spec, spec.get("v", state.get("version", 0)))  # a task or holdout
    if not profiles:
        return []
    pairs = kind.form(item) == "pairs"
    index = {a["id"]: n for n, a in enumerate(c.get("attributes", []))}
    attrs = kind.attributes(item, ctx.lang)
    images = _cbc_images(item, profiles) if kind.uses_images(item) else None
    if images and c.get("display") == "image":
        attrs = []
    elif images and not c.get("show_visual_in_table"):
        attrs = [a for a in attrs if not a.get("visual")]
    fallback = DEFAULT_TEXTS["de" if ctx.lang in ("de", "de-du") else "en"]
    word = ctx.fill(t.get("alt_label") or ctx.tr(fallback["alt_label"], DEFAULT_TEXTS["en"]["alt_label"]))
    unit = Unit()
    y = _task_head(ctx, item, unit, spec, kind.shown_tasks(item))
    heads = [f"{word} {a + 1}" for a in range(len(profiles))] if pairs else [word]
    y, first, col = _profiles_table(ctx, item, unit, y, profiles, attrs, index, images, kind.level_images_on(item), heads)
    field = f"i{item.id}_{key}"
    y += 3
    if c.get("slider") and not pairs:
        lo, hi = kind.scale_range(item)
        unit.h = _number_field(ctx, item, unit, y, pos, row, field, hint=f"{lo} – {hi}") + 1
        return [unit]
    g = _group(ctx, field, item, row, True)
    values = kind.scale_points(item)
    step = min(14.0, (WIDTH - 16) / max(len(values), 1))
    x0 = LEFT + 8
    for n, v in enumerate(values):
        unit.box(x0 + n * step, y, {**_base(ctx, item, pos, row), "f": field, "v": str(v), "g": g, "lab": str(v)})
        if not pairs:
            unit.text(x0 + n * step + BOX / 2, y + BOX + 0.8, str(v), 8, False, 0.2, "center")
    y += BOX + (line_height(8) + 1.5 if not pairs else 1.5)
    if pairs:
        much, same = ctx.fill(t.get("much") or fallback["much"]), ctx.fill(t.get("same") or fallback["same"])
        ends = [(0, f"{word} 1 {much}", "left"), (len(values) // 2, same, "center"), (len(values) - 1, f"{word} 2 {much}", "right")]
    else:
        ends = [(0, ctx.fill(t.get("low") or fallback["low"]), "left"),
                (len(values) - 1, ctx.fill(t.get("high") or fallback["high"]), "right")]
    for n, text, align in ends:
        x = x0 + n * step + (BOX / 2 if align == "center" else (BOX if align == "right" else 0))
        unit.text(x, y, text, 8, False, 0.35, align)
    unit.h = y + line_height(8) + 3
    return [unit]


def _maxdiff_task(ctx, item, pos, row, spec):
    """One MaxDiff set: the statements as rows, a box for "most" and one for "least" important."""
    from app import holdouts

    kind, t = item.kind, item.t(ctx.lang)
    if "holdout" in spec:
        s = holdouts.maxdiff_set(item, spec["holdout"]) or []
        key = f"h{spec['holdout']}"
    else:
        s = kind.task_set(item, spec.get("v", 0), spec.get("task", 0))
        key = f"t{spec.get('task', 0)}"
    if not s:
        return []
    labels = kind.labels(item, ctx.lang)
    unit = Unit()
    total = kind.n_sets(item) + len(holdouts.maxdiff_active(item))
    y = _task_head(ctx, item, unit, spec, total)
    from app.items.maxdiff import DEFAULT_TEXTS

    fallback = DEFAULT_TEXTS["de" if ctx.lang in ("de", "de-du") else "en"]
    best = ctx.fill(t.get("best_label") or ctx.tr(fallback["best_label"], DEFAULT_TEXTS["en"]["best_label"]))
    worst = ctx.fill(t.get("worst_label") or ctx.tr(fallback["worst_label"], DEFAULT_TEXTS["en"]["worst_label"]))
    col = 30.0
    for n, head in enumerate((best, worst)):
        lines = wrap(head, col - 2, 8, True)
        yy = y
        for line in lines:
            unit.text(LEFT + n * col + col / 2, yy, line, 8, True, 0.2, "center")
            yy += line_height(8)
    y += 2 * line_height(8) + 1
    gb = _group(ctx, f"i{item.id}_{key}_b", item, row, True, best)
    gw = _group(ctx, f"i{item.id}_{key}_w", item, row, True, worst)
    for n, idx in enumerate(s):
        text = ctx.fill(labels[idx] if idx < len(labels) else "")
        lines = wrap(text, WIDTH - 2 * col - 3, 9.5)
        h = max(len(lines) * line_height(9.5) + 2.4, BOX + 2.4)
        if n % 2 == 0:
            unit.rect(LEFT, y, WIDTH, h, 0, 0.93, stroke=False)
        by = y + (h - BOX) / 2
        unit.box(LEFT + col / 2 - BOX / 2, by, {**_base(ctx, item, pos, row), "f": f"i{item.id}_{key}_b", "v": str(idx),
                                                "g": gb, "lab": text[:80]})
        unit.box(LEFT + col + col / 2 - BOX / 2, by, {**_base(ctx, item, pos, row), "f": f"i{item.id}_{key}_w",
                                                      "v": str(idx), "g": gw, "lab": text[:80]})
        yy = y + 1.2
        for line in lines:
            unit.text(LEFT + 2 * col + 2, yy, line, 9.5)
            yy += line_height(9.5)
        y += h
    unit.h = y + 2
    return [unit]


def _cva_ranking(ctx, item, pos, row, spec):
    """The ranking form of a ratings-based conjoint: every offer of the plan as a card with a box for its rank, as the
    ranking element (the ranks become the order, app/paper.py); one unit per card, so that long lists can break."""
    kind = item.kind
    state = (ctx.layout.get("cj") or {}).get(str(item.id)) or {}
    offers = kind.version_tasks(item, spec.get("v", state.get("version", 0)))
    if not offers:
        return []
    attrs = kind.attributes(item, ctx.lang)
    head = Unit()
    y = _head(ctx, item, head)
    head.text(LEFT + 8, y, ctx.tr("Bitte nummerieren Sie: 1 = an erster Stelle.", "Please number them: 1 = first."), 9, False,
              0.3)
    head.h = y + line_height(9) + 1.5
    units = [head]
    g = _group(ctx, f"i{item.id}", item, row, False)
    for t, profile in enumerate(offers):
        unit = Unit()
        lines = [(("" if item.config.get("hide_attr_labels") else ctx.fill(a["label"]) + ": ")
                  + ctx.fill(a["level_labels"][profile[n]])) for n, a in enumerate(attrs)]
        unit.write(LEFT + 8, 0, 12, 8, {**_base(ctx, item, pos, row), "f": f"i{item.id}", "rank": str(t), "fmt": "int",
                                        "g": g, "lab": "; ".join(lines)[:80]})
        ly = 0.5
        for line in lines:
            for part in wrap(line, WIDTH - 26, 9.5):
                unit.text(LEFT + 24, ly, part, 9.5)
                ly += line_height(9.5)
        unit.h = max(ly, 8) + 2.5
        units.append(unit)
    return units


def _task_explain(ctx, item):
    """The explanation page of a choice experiment, as text (the participant page's wording)."""
    t, kind, c = item.t(ctx.lang), item.kind, item.config
    if item.type == "cva" and kind.form(item) == "ranking":
        default = ctx.tr("Sie sehen gleich %d Angebote auf einer Seite. Bringen Sie sie in eine Reihenfolge: Oben das, das "
                         "Ihnen am meisten zusagt. Die Angebote unterscheiden sich in folgenden Merkmalen:",
                         "You will see %d offers on one page. Put them in order: at the top the one you like most. The "
                         "offers differ in these features:") % kind.n_tasks(item)
    elif item.type == "cva" and kind.form(item) == "pairs":
        default = ctx.tr("Sie sehen gleich %d Paare von Angeboten. Sagen Sie jeweils auf der Skala darunter, welches der beiden "
                         "Ihnen mehr zusagt und wie deutlich. Die Angebote unterscheiden sich in folgenden Merkmalen:",
                         "You will see %d pairs of offers. Each time, say on the scale below which of the two you like more "
                         "and how much. The offers differ in these features:") % kind.n_tasks(item)
    elif item.type == "cva":
        default = ctx.tr("Sie sehen gleich %d Angebote nacheinander. Bewerten Sie jedes auf der Skala darunter. Die Angebote "
                         "unterscheiden sich in folgenden Merkmalen:",
                         "You will see %d offers, one after the other. Rate each one on the scale below it. The offers "
                         "differ in these features:") % kind.n_tasks(item)
    elif item.type == "conjoint":
        default = ctx.tr("Sie sehen gleich %d Aufgaben mit je %d Möglichkeiten. Wählen Sie jeweils die Möglichkeit, die Sie "
                         "am meisten anspricht. Die Möglichkeiten unterscheiden sich in folgenden Merkmalen:",
                         "You will see %d tasks with %d options each. Each time, choose the option you like most. The "
                         "options differ in these features:") % (kind.shown_tasks(item), c.get("alternatives", 2))
    else:
        default = ctx.tr("Sie sehen gleich %d Sets mit je %d Aussagen. Wählen Sie in jedem Set die Aussage, die Ihnen "
                         "**am wichtigsten** ist, und die, die Ihnen **am wenigsten wichtig** ist. Manche Aussagen kommen "
                         "mehrmals vor, jeweils mit anderen zusammen.",
                         "You will see %d sets of %d statements each. In each set, choose the statement that is **most "
                         "important** to you and the one that is **least important**. Some statements appear more than "
                         "once, each time together with others.") % (kind.shown_tasks(item), c.get("set_size", 4))
    unit = Unit()
    y = unit.para(LEFT, 0, ctx.fill(t.get("explain_intro") or default), WIDTH, 9.5) + 1
    if item.type in ("conjoint", "cva"):
        for attr in kind.attributes(item, ctx.lang):
            line = f"{ctx.fill(attr['label'])}: " + " · ".join(ctx.fill(x) for x in attr["level_labels"] if x)
            y = unit.para(LEFT + 4, y, line, WIDTH - 4, 9) + 0.5
    unit.h = y + 2
    return [unit]


def _info(ctx, item, pos, row):
    t = item.t(ctx.lang)
    unit = Unit()
    y = unit.para(LEFT, 0, ctx.fill(t.get("question") or t.get("body") or ""), WIDTH, SIZE)
    unit.h = y + 2
    return [unit] if y else []


def _image(ctx, item, pos, row):
    import os

    from flask import current_app
    from PIL import Image as PILImage

    try:
        asset = item.kind.asset_for(item, ctx.variables)
    except Exception:  # noqa: BLE001 - no image, nothing printed
        return []
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], item.block.survey.uid, asset.filename) if asset else ""
    if not asset or not os.path.isfile(path):
        return []
    with PILImage.open(path) as img:
        w_px, h_px = img.size
    w = min(120.0, WIDTH)
    h = w * h_px / max(w_px, 1)
    if h > 120:
        w, h = w * 120 / h, 120.0
    unit = Unit()
    y = _head(ctx, item, unit, numbered=False) if item.type == "image" and item.t(ctx.lang).get("question") else 0.0
    unit.image(path, LEFT + (WIDTH - w) / 2, y, w, h)
    unit.h = y + h + 3
    return [unit]


def _hotspot(ctx, item, pos, row):
    """Hot Spot (Bild markieren, modes "areas" and "rate"): the image with its areas outlined and numbered, then a box
    per area (areas) or a "+" and a "−" box per area (rate). Marking points or lines stays online only."""
    kind = item.kind
    if not kind.on_areas(item):
        return []
    areas = kind.areas(item, ctx.lang)
    head = Unit()
    y = _head(ctx, item, head)
    pictures = _image(ctx, item, pos, row)
    units = []
    if pictures:
        pic = pictures[0]
        _, x, iy, w, h, path = next(op for op in pic.ops if op[0] == "image")
        for n, a in enumerate(areas, start=1):
            bx, by, bw, bh = (a.get("box") or [0, 0, 0, 0])[:4]
            pic.rect(x + bx * w, iy + by * h, bw * w, bh * h, 0.5, None)
            pic.text(x + bx * w + 1, iy + by * h + 0.8, str(n), 9, True)
        units.append(pic)
    rate = item.config.get("mode") == "rate"
    body = Unit()
    yy = 0.0
    g = _group(ctx, f"i{item.id}", item, row, False)
    if rate:
        body.text(LEFT + 8, yy, "+", 9, True)
        body.text(LEFT + 16, yy, "–", 9, True)
        yy += line_height(9) + 0.5
    for n, a in enumerate(areas, start=1):
        label = f"{n}. {ctx.fill(a['label']) or a['code']}"
        if rate:
            ga = _group(ctx, f"i{item.id}_{a['code']}", item, row, True, label)
            body.box(LEFT + 7, yy, {**_base(ctx, item, pos, row), "f": f"i{item.id}_{a['code']}", "v": "1", "g": ga,
                                    "lab": f"+ {label}"[:80]})
            body.box(LEFT + 15, yy, {**_base(ctx, item, pos, row), "f": f"i{item.id}_{a['code']}", "v": "-1", "g": ga,
                                     "lab": f"– {label}"[:80]})
            body.text(LEFT + 22, yy + 0.6, label)
        else:
            body.box(LEFT + 8, yy, {**_base(ctx, item, pos, row), "f": f"i{item.id}", "v": a["code"], "g": g,
                                    "lab": label[:80]})
            body.text(LEFT + 14.5, yy + 0.6, label)
        yy += 6.2
    body.h = yy + 2
    head.h = y
    return [head] + units + [body]


RENDER = {"single_choice": _choice, "multiple_choice": _choice, "federal_state": _choice, "likert": _likert,
          "semantic": _semantic, "rating": _rating, "consent": _consent, "text_input": _text_input, "slider": _slider,
          "date_time": _date_time, "postal_code": _postal_code, "ranking": _ranking, "constant_sum": _constant_sum,
          "psm": _psm, "cloze": _cloze, "info": _info, "image": _image, "image_mark": _hotspot}


def left_out(survey):
    """The questions of the survey that are not on paper: [(item, type label)]."""
    from app.lang import de_en

    out = []
    for block in survey.blocks:
        for item in block.items:
            if item.type in ON_PAPER or item.type in SHOWN or (item.type == "image_mark" and item.kind.on_areas(item)):
                continue
            if not item.kind.collects_answer and item.type not in ("sample_compare", "sample_map", "sample_crowd",
                                                                    "score_band", "scale_profile", "topic_map",
                                                                    "topic_profile", "iat_feedback"):
                continue
            out.append((item, item.kind.label if isinstance(item.kind.label, str) else str(item.kind.label)))
    from app import runtime
    import random

    layout = runtime.build_layout(survey, random.Random(0), separate=True)
    if layout.get("after_split"):  # the separated section (contact data) is not printed
        blocks = {b.id: b for b in survey.blocks}
        for spec in layout["after_split"]:
            block = blocks.get(spec.get("block"))
            for item_id in spec.get("items") or []:
                item = next((i for i in (block.items if block else []) if i.id == item_id), None)
                if item is not None and item.kind.collects_answer and all(o[0].id != item.id for o in out):
                    out.append((item, de_en("getrennter Abschnitt", "separated section")))
    return out


def _intro(ctx, title):
    unit = Unit()
    y = 0.0
    for line in wrap(title, WIDTH, 14, True):
        unit.text(LEFT, y, line, 14, True)
        y += line_height(14)
    y += 2
    note = Unit()
    hint = ctx.tr("So kreuzen Sie an:", "How to tick:")
    note.text(LEFT + 3, 2.5, hint, 9)
    x = LEFT + 5 + _width(hint, 9)
    note.rect(x, 2.0, BOX, BOX, 0.3, 1.0)
    note.ops.append(("cross", x, 2.0))
    x += BOX + 4
    fix = ctx.tr("Korrigieren: Kästchen ganz ausmalen und das richtige ankreuzen. Bitte schreiben Sie keinen Namen "
                 "auf den Bogen.",
                 "To correct: fill the box in completely and tick the right one. Please do not write your name on the "
                 "sheet.")
    ny = 2.5
    for line in wrap(fix, RIGHT - x - 3, 9):
        note.text(x, ny, line, 9)
        ny += line_height(9)
    unit.rect(LEFT, y, WIDTH, ny + 2, 0.3, 0.96)
    unit.ops += [(op[0], op[1], op[2] + y, *op[3:]) for op in note.ops]
    unit.h = y + ny + 6
    return unit


def _cond_words(ctx, cond):
    """A condition on earlier answers as the sheet says it ("question 3: ja"), or None if it reads something the
    sheet cannot name (a URL parameter, a quota, a question not printed before)."""
    from app import conditions, flow

    if not isinstance(cond, dict):
        return None
    if isinstance(cond.get("conds"), list):
        parts = [_cond_words(ctx, c) for c in flow.parts(cond)]
        if not parts or any(p is None for p in parts):
            return None
        joined = ctx.tr(" oder ", " or ") if cond.get("rule") == "any" else ctx.tr(" und ", " and ")
        return joined.join(parts)
    ref = cond.get("item")
    number = ctx.numbers.get(flow.source_id(ref)) if isinstance(ref, int) else None
    if number is None:
        return None
    what = conditions.text(ctx.survey, cond, ctx.lang, short=True, ui="de" if ctx.lang in ("de", "de-du") else "en")
    return ctx.tr("Frage {n}: {what}", "question {n}: {what}").format(n=number, what=plain(what))


def _branch_words(ctx, refs):
    """The branch conditions of a page (flow ids, "!id" for "Sonst") in words, or None."""
    from app import flow

    conds = flow.conditions_by_id(ctx.survey)
    parts = []
    for ref in refs or []:
        words = _cond_words(ctx, conds.get(ref.lstrip("!"), {}))
        if words is None:
            return None
        parts.append(ctx.tr("nicht ({cond})", "not ({cond})").format(cond=words) if ref.startswith("!") else words)
    return ctx.tr(" und ", " and ").join(parts) if parts else None


def _note(text, size=9):
    """A filter note in grey, over the full width."""
    unit = Unit()
    y = 0.0
    for line in wrap(text, WIDTH - 8, size, True):
        unit.text(LEFT + 8, y, line, size, True, 0.3)
        y += line_height(size)
    unit.h = y + 1.5
    return unit


def _end_note(ctx, spec):
    """A screen-out or exclusion end behind a branch on earlier answers: where the questionnaire ends for whom."""
    from app import flow

    node, _ = flow.find(flow.tree(ctx.survey), spec.get("end"))
    if node is None or not any(case.get("when") in ("screenout", "excluded") for case in flow.cases(node)):
        return None
    words = _branch_words(ctx, spec.get("cond"))
    if not words:
        return None
    unit = Unit()
    text = ctx.tr("Wenn {cond}: Der Fragebogen endet hier für Sie. Bitte geben Sie den Bogen trotzdem ab.",
                  "If {cond}: the questionnaire ends here for you. Please hand in the sheet all the same.").format(cond=words)
    lines = wrap(text, WIDTH - 6, 9.5, True)
    unit.rect(LEFT, 0, WIDTH, len(lines) * line_height(9.5) + 4, 0.3, 0.93)
    y = 2.0
    for line in lines:
        unit.text(LEFT + 3, y, line, 9.5, True)
        y += line_height(9.5)
    unit.h = y + 4
    return unit


def keep_lines(lang, code, number, url):
    """The lines of the page the participant keeps (the deletion code), in the survey language: heading, what to do
    with the pages, the code, how to use it, the sheet's number."""
    from app.ui_i18n import tr

    return [tr(lang, "Ihr Löschcode", "Your deletion code"),
            tr(lang, "Diese Seite behalten Sie. Geben Sie nur die Seiten mit den schwarzen Ecken zurück.",
               "Keep this page. Hand back only the pages with the black corners."),
            code,
            tr(lang, "Mit diesem Code können Sie Ihre Antworten jederzeit löschen lassen: {url}",
               "With this code you can have your answers deleted at any time: {url}").format(url=url),
            tr(lang, "Bogen {number}", "Sheet {number}").format(number=number)]


def build_sheet(survey, lang, layout, size="large"):
    """The pages and the plan of one sheet: (pages of drawing operations, plan); size: a key of SCALES."""
    token = _SCALE.set(SCALES.get(size, SCALES["large"]))
    try:
        return _build_sheet(survey, lang, layout)
    finally:
        _SCALE.reset(token)


def _build_sheet(survey, lang, layout):
    from app import item_condition, loops
    from app.models import Block, Item
    from app.extensions import db

    ctx = Ctx(survey, lang, layout)
    out = Layout()
    out.place(_intro(ctx, plain(survey.title_in(lang))))
    blocks = {b.id: b for b in survey.blocks}
    items = {i.id: i for b in survey.blocks for i in b.items}
    last_block, last_cond = None, None
    for pos, spec in enumerate(layout.get("pages") or []):
        if "end" in spec:
            note = _end_note(ctx, spec)
            if note is not None:
                out.place(note, gap=4)
            continue
        if "block" not in spec:
            continue
        if spec.get("task") is not None or spec.get("holdout") is not None or spec.get("explain") or spec.get("anchor")                 or spec.get("rank"):
            # a page of a choice experiment (one task, its explanation, the anchoring question)
            item = items.get((spec.get("items") or [None])[0]) or db.session.get(Item, (spec.get("items") or [0])[0])
            if item is None or item.type not in TASKS or spec.get("anchor"):
                continue
            if spec.get("explain"):
                units = _task_explain(ctx, item)
            elif item.type == "conjoint":
                units = _conjoint_task(ctx, item, pos, spec.get("row"), spec)
            elif item.type == "cva" and spec.get("rank"):
                units = _cva_ranking(ctx, item, pos, spec.get("row"), spec)
            elif item.type == "cva":
                units = _cva_task(ctx, item, pos, spec.get("row"), spec)
            else:
                units = _maxdiff_task(ctx, item, pos, spec.get("row"), spec)
            for unit in units:
                out.place(unit, gap=4)
            continue
        block = blocks.get(spec["block"]) or db.session.get(Block, spec["block"])
        if block is None:
            continue
        row = spec.get("row")
        ctx.variables = loops.variables(block, row, lang) if row else {}
        cond = tuple(spec.get("cond") or ())
        if cond and cond != last_cond:  # the pages behind a branch: who answers them
            words = _branch_words(ctx, cond)
            if words:
                out.place(_note(ctx.tr("Die folgenden Fragen nur beantworten, wenn {cond}.",
                                       "Answer the following questions only if {cond}.").format(cond=words)), gap=4)
        last_cond = cond
        if (block.id, row) != last_block:
            last_block = (block.id, row)
            title = block.title(lang)
            if block.show_title and title:
                head = Unit()
                yy = 0.0
                for line in wrap(ctx.fill(title), WIDTH, 12, True):
                    head.text(LEFT, yy, line, 12, True)
                    yy += line_height(12)
                head.h = yy + 2
                out.place(head, gap=4)
        for item_id in spec.get("items") or []:
            item = items.get(item_id) or db.session.get(Item, item_id)
            if item is None or item.type not in RENDER:
                continue  # also a choice experiment without a current design: it stays online only
            own = item_condition.of(item)
            words = _cond_words(ctx, own) if own else None  # before rendering: the item's own number is not yet set
            units = RENDER[item.type](ctx, item, pos, row)
            if words and units:  # "Nur zeigen, wenn …" of the question itself
                units = [_note(ctx.tr("Nur beantworten, wenn {cond}.", "Answer only if {cond}.").format(cond=words))] + units
            for n, unit in enumerate(units):
                if n == 0 and len(units) > 1:
                    # the question stays with the first row of its answers
                    unit_h = unit.h + units[1].h
                    if out.y > TOP and unit_h > out.room():
                        out.new_page()
                out.place(unit, gap=4 if n == 0 else 0)
    if len(out.pages) > 1 and not out.pages[-1]:
        out.pages.pop()
    plan = {"pages": len(out.pages), "fields": out.fields, "groups": ctx.groups}
    return out.pages, plan


def plan_key(plan):
    """A short key under which sheets with the same plan share it."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:16]


# --- drawing -----------------------------------------------------------------------------------


def _y(mm):
    return (PAGE_H - mm) * MM


def _draw_keep(c, title, lines):
    """The page the participant keeps: the deletion code on a page of its own after the sheet (E15). It carries no
    corner squares and no code row, so nothing the scan needs goes with it. A slip to cut off at the foot of the last
    page took the code row with it, however it was drawn (Ralf, 2026-10-11)."""
    head, keep, code, use, number = lines
    y = 30.0
    for line in wrap(title, WIDTH, 12, True):
        c.setFont(BOLD, 12)
        c.drawString(LEFT * MM, _y(y), _safe(line))
        y += line_height(12)
    y += 8
    c.setFont(BOLD, 16)
    c.drawString(LEFT * MM, _y(y), _safe(head))
    y += 9
    c.setFont(FONT, SIZE)
    for line in wrap(keep, WIDTH, SIZE):
        c.drawString(LEFT * MM, _y(y), _safe(line))
        y += line_height(SIZE)
    y += 8
    c.setLineWidth(0.4 * MM)
    c.rect(LEFT * MM, _y(y + 16), WIDTH * MM, 16 * MM, stroke=1, fill=0)  # the code in a frame
    c.setFont(BOLD, 20)
    c.drawCentredString((LEFT + WIDTH / 2) * MM, _y(y + 10.5), _safe(code))
    y += 26
    c.setFont(FONT, SIZE)
    for line in wrap(use, WIDTH, SIZE):
        c.drawString(LEFT * MM, _y(y), _safe(line))
        y += line_height(SIZE)
    y += 4
    c.setFillGray(0.35)
    c.drawString(LEFT * MM, _y(y), _safe(number))
    c.setFillGray(0)


def _draw_page(c, ops, key, page, pages, title, lang, images=None):
    from reportlab.lib.boxstuff import aspectRatioFix

    from app.ui_i18n import tr

    for op in ops:
        kind = op[0]
        if kind == "text":
            _, x, y, size, bold, text, grey, align = op
            c.setFont(BOLD if bold else FONT, size)
            c.setFillGray(grey)
            base = _y(y + size * 0.78 / MM)
            if align == "center":
                c.drawCentredString(x * MM, base, _safe(text))
            elif align == "right":
                c.drawRightString(x * MM, base, _safe(text))
            else:
                c.drawString(x * MM, base, _safe(text))
        elif kind == "rect":
            _, x, y, w, h, width, fill, stroke = op
            c.setLineWidth(width * MM)
            c.setStrokeGray(0)
            if fill is not None:
                c.setFillGray(fill)
            c.rect(x * MM, _y(y + h), w * MM, h * MM, stroke=1 if stroke and width else 0, fill=1 if fill is not None else 0)
        elif kind == "cross":
            _, x, y = op
            c.setLineWidth(0.4 * MM)
            c.setStrokeGray(0)
            c.line((x + 0.8) * MM, _y(y + 0.8), (x + BOX - 0.8) * MM, _y(y + BOX - 0.8))
            c.line((x + 0.8) * MM, _y(y + BOX - 0.8), (x + BOX - 0.8) * MM, _y(y + 0.8))
        elif kind == "image":
            _, x, y, w, h, path = op
            src, size = (path, None) if images is None else images.get(path, (None, None))
            if src is None:
                continue  # a broken image leaves its place empty
            box = (x * MM, _y(y + h), w * MM, h * MM)
            try:
                if size:  # made smaller: placed as the original would be, so the page stays exactly the same
                    box = aspectRatioFix(True, "c", *box, *size)[:4]
                # by its file name: reportlab reads and embeds a file once per PDF and only refers to it after that
                c.drawImage(src, *box, preserveAspectRatio=not size, mask="auto")
            except Exception:  # noqa: BLE001 - a broken image leaves its place empty
                if images is not None:
                    images[path] = (None, None)
    c.setFillGray(0)
    for cx, cy in MARKS:
        c.rect((cx - MARK / 2) * MM, _y(cy + MARK / 2), MARK * MM, MARK * MM, stroke=0, fill=1)
    for bit, (x, y, w, h) in zip(code_bits(key, page), code_cells()):
        if bit:
            c.rect(x * MM, _y(y + h), w * MM, h * MM, stroke=0, fill=1)
    c.setFont(FONT, 7)
    c.setFillGray(0.35)
    label = tr(lang, "Bogen {number} · Seite {page} von {pages}", "Sheet {number} · page {page} of {pages}")
    c.drawCentredString(PAGE_W / 2 * MM, _y(279.5), _safe(label.format(number=sheet_number(key), page=page + 1, pages=pages)
                                                         + " · " + title[:60]))
    c.setFillGray(0)


def render(sheets, title, lang, duplex=False):
    """The PDF of a print run: [(key, pages, keep)] in order, each page a list of operations; keep: None or the lines
    of the page the participant keeps (keep_lines), printed after the sheet. duplex: printed on both sides, so every
    sheet and the page to keep start on a paper of their own (an empty back where needed). Returns bytes."""
    from reportlab import rl_config
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    rl_config.useA85 = int(A85)  # only this module uses reportlab
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="askurious-paper-") as folder:
        images = _print_images(sheets, folder)
        c = canvas.Canvas(buf, pagesize=A4, pageCompression=1, invariant=1)
        c.setTitle(_safe(title))
        c.setCreator("Askurious")
        for key, pages, keep in sheets:
            for n, ops in enumerate(pages):
                _draw_page(c, ops, key, n, len(pages), title, lang, images)
                c.showPage()
            if duplex and len(pages) % 2:
                c.showPage()  # the empty back of the last page
            if keep:
                _draw_keep(c, title, keep)
                c.showPage()
                if duplex:
                    c.showPage()
        c.save()
    return buf.getvalue()


def _print_images(sheets, folder):
    """{path: (file to draw, None or the original size in pixels if made smaller)} for every image of a print run: each read once and, if it has
    more pixels than the print needs (PRINT_DPI at the largest size it is drawn in), made smaller once into `folder`
    (audit 2026-10-11, M11/F-P1: before, every sheet decoded every image anew)."""
    largest = {}
    for _key, pages, _keep in sheets:
        for ops in pages:
            for op in ops:
                if op[0] == "image":
                    _, _x, _y_, w, h, path = op
                    lw, lh = largest.get(path, (0.0, 0.0))
                    largest[path] = (max(lw, w), max(lh, h))
    return {path: _print_image(path, w, h, os.path.join(folder, str(n)))
            for n, (path, (w, h)) in enumerate(largest.items())}


def _print_image(path, w, h, stem):
    """The image at `path` as drawn into a box of w x h mm (aspect kept): the file itself if it is small enough,
    else a copy with PRINT_DPI (JPEG, or PNG where it is transparent)."""
    from PIL import Image

    try:
        with Image.open(path) as img:
            iw, ih = img.size
            fit = min(w / iw, h / ih)  # mm per pixel once fitted into the box
            tw = max(1, math.ceil(iw * fit / 25.4 * PRINT_DPI))
            th = max(1, math.ceil(ih * fit / 25.4 * PRINT_DPI))
            if tw >= iw or th >= ih:
                return path, None
            if img.format == "JPEG":
                img.draft("RGB" if img.mode not in ("L", "1") else "L", (tw, th))  # decode at a fraction already
            clear = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
            small = img.convert("RGBA" if clear else ("L" if img.mode in ("L", "1") else "RGB"))
            small = small.resize((tw, th), Image.LANCZOS)
        out = stem + (".png" if clear else ".jpg")
        if clear:
            small.save(out, "PNG")
        else:
            small.save(out, "JPEG", quality=90)
        return out, (iw, ih)
    except Exception:  # noqa: BLE001 - not made smaller: reportlab tries the file itself (broken: the place stays empty)
        return path, None
