"""A plain SVG drawn as PNG with Pillow, without an outside renderer (reportlab's renderPM needs an extension that is not
installed; Ralf 2026-10-29: no new dependency). It knows what Askurious's own drawings use: rect (with rx), circle,
line, path with M, L and C (cubic Bézier), text (x, y, text-anchor, font-size, font-weight bold, fill), fill, stroke,
stroke-width, stroke-dasharray, fill-opacity and groups. Anything else is left out. The fonts are those of the PDFs
(app/pdf_fonts.py): Noto Sans for Latin, Greek and Cyrillic, Arabic and Chinese where the image has their fonts, a text
drawn font run by font run; Pillow's own font lacks even the umlauts.

First use: the diagram of a category system (app/transcript_coding_review.py, map_svg) as PNG and in the Word coding
guide."""
import io
import re
from xml.etree import ElementTree as ET

from PIL import Image, ImageColor, ImageDraw, ImageFont

from app import pdf_fonts

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
MAX_SIDE = 12000  # pixels of the longer side at most
WIDER = 0.95  # Noto Sans runs a little wider than Arial, which the drawings are laid out for


def _f(value, default=0.0):
    try:
        return float(str(value).replace("px", ""))
    except (TypeError, ValueError):
        return default


def _colour(value, opacity=1.0):
    if not value or value == "none":
        return None
    if opacity <= 0.05:
        return None
    try:
        return ImageColor.getrgb(value)
    except ValueError:
        return None


def _bezier(p0, p1, p2, p3, steps=24):
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def _path_points(d):
    """[[points]] of a path with M, L and C (absolute)."""
    tokens = re.findall(r"[MLC]|-?\d+(?:\.\d+)?", d or "")
    lines, cur, cmd, i = [], None, None, 0
    while i < len(tokens):
        if tokens[i] in "MLC":
            cmd = tokens[i]
            i += 1
            continue
        if cmd == "M":
            cur = (float(tokens[i]), float(tokens[i + 1]))
            lines.append([cur])
            i += 2
            cmd = "L"
        elif cmd == "L" and cur is not None:
            cur = (float(tokens[i]), float(tokens[i + 1]))
            lines[-1].append(cur)
            i += 2
        elif cmd == "C" and cur is not None and i + 5 < len(tokens):
            p1, p2, p3 = ((float(tokens[i + k]), float(tokens[i + k + 1])) for k in (0, 2, 4))
            lines[-1].extend(_bezier(cur, p1, p2, p3))
            cur = p3
            i += 6
        else:
            i += 1
    return lines


def _dashed(draw, points, fill, width, dash):
    on, off = dash
    left, drawing = on, True
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        pos = 0.0
        while pos < length:
            step = min(left, length - pos)
            if drawing:
                a, b = pos / length, (pos + step) / length
                draw.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a), (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)], fill=fill, width=width)
            pos += step
            left -= step
            if left <= 0:
                drawing = not drawing
                left = on if drawing else off


def render(svg, scale=2.0):
    """The PNG bytes of an SVG text."""
    root = ET.fromstring(svg)
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    box = [_f(v) for v in _NUM.findall(root.get("viewBox") or "")]
    width = box[2] if len(box) == 4 else _f(root.get("width"), 600)
    height = box[3] if len(box) == 4 else _f(root.get("height"), 300)
    scale = min(scale, MAX_SIDE / max(width, height, 1))
    img = Image.new("RGB", (max(1, int(width * scale)), max(1, int(height * scale))), "white")
    draw = ImageDraw.Draw(img)
    fonts = {}

    def font(size, name):
        size = max(6, int(round(size * scale)))
        if (size, name) not in fonts:
            fonts[(size, name)] = ImageFont.truetype(pdf_fonts.file_of(name), size)
        return fonts[(size, name)]

    def walk(el, inherited):
        style = dict(inherited)
        for key in ("fill", "stroke", "stroke-width", "font-size", "font-weight", "text-anchor", "fill-opacity", "stroke-dasharray"):
            if el.get(key) is not None:
                style[key] = el.get(key)
        tag = el.tag
        s = scale
        fill = _colour(style.get("fill", "#000000"), _f(style.get("fill-opacity"), 1.0))
        stroke = _colour(style.get("stroke"))
        sw = max(1, int(round(_f(style.get("stroke-width"), 1.0) * s)))
        if tag == "rect":
            x, y = _f(el.get("x")), _f(el.get("y"))
            w = width if el.get("width") == "100%" else _f(el.get("width"))
            h = height if el.get("height") == "100%" else _f(el.get("height"))
            if w > 0 and h > 0:
                draw.rounded_rectangle([x * s, y * s, (x + w) * s, (y + h) * s], radius=_f(el.get("rx")) * s,
                                       fill=fill, outline=stroke, width=sw if stroke else 0)
        elif tag == "circle":
            cx, cy, r = _f(el.get("cx")), _f(el.get("cy")), _f(el.get("r"))
            draw.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill=fill, outline=stroke)
        elif tag in ("path", "line"):
            if tag == "line":
                lines = [[(_f(el.get("x1")), _f(el.get("y1"))), (_f(el.get("x2")), _f(el.get("y2")))]]
            else:
                lines = _path_points(el.get("d"))
            colour = stroke or (fill if tag == "line" else None)
            if colour:
                dash = [_f(v) * s for v in _NUM.findall(style.get("stroke-dasharray") or "")]
                for pts in lines:
                    pts = [(x * s, y * s) for x, y in pts]
                    if len(pts) < 2:
                        continue
                    if len(dash) >= 2:
                        _dashed(draw, pts, colour, sw, dash[:2])
                    else:
                        draw.line(pts, fill=colour, width=sw, joint="curve")
        elif tag == "text":
            content = "".join(el.itertext())
            if content and fill:
                bold = style.get("font-weight") in ("bold", "600", "700")
                size = _f(style.get("font-size"), 13) * WIDER
                parts = [(font(size, name), part) for name, part in
                         pdf_fonts.runs(pdf_fonts.visual(content), pdf_fonts.font(bold=bold))]
                x, y = _f(el.get("x")) * s, _f(el.get("y")) * s
                length = sum(draw.textlength(part, font=f) for f, part in parts)
                anchor = style.get("text-anchor", "start")
                x -= length if anchor == "end" else length / 2 if anchor == "middle" else 0
                for f, part in parts:
                    draw.text((x, y), part, font=f, fill=fill, anchor="ls")
                    x += draw.textlength(part, font=f)
            return
        elif tag == "title":
            return
        for child in el:
            walk(child, style)

    walk(root, {})
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
