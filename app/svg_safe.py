"""Uploaded SVG logos: safe drawing and bounded attribution survive.

An SVG is a document that can carry scripts, event handlers, embedded HTML, and references that make the
browser of every participant call another server. A logo needs none of that. clean() parses the file and
writes it anew from an allow-list of elements and attributes; whatever is not on the list is dropped, and
references may only point into the file itself (#id). Files with a DOCTYPE or entities are refused before
parsing (external entities, "billion laughs").

Two more layers lie outside this module: logos are embedded with <img> only (no scripts run there, nothing
is loaded from elsewhere), and media.serve sends SVG files with a Content-Security-Policy that forbids
everything, for the case that someone opens the file's address directly.
"""
import math
import re
import xml.etree.ElementTree as ET

from app.lang import de_en

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
MAX_BYTES = 200 * 1024
MAX_ELEMENTS = 5000
MAX_DEPTH = 50        # elements inside each other; a logo has a handful, and the copy below walks them by recursion
MAX_USE_DEPTH = 200   # the same for the walk that follows every <use> (see _drawn)
MAX_DRAWN = 20000     # shapes the browser draws once every <use> is followed (see _drawn)
MAX_SIDE = 1_000_000  # largest width or height a viewBox may name

ELEMENTS = {
    "svg", "g", "defs", "symbol", "use", "title", "desc",
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan",
    "linearGradient", "radialGradient", "stop", "clipPath", "mask",
}
PRESENTATION = {
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap",
    "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset", "opacity", "color",
    "clip-path", "clip-rule", "mask", "stop-color", "stop-opacity", "display", "visibility",
    "font-family", "font-size", "font-weight", "font-style", "text-anchor", "dominant-baseline", "letter-spacing",
}
ATTRIBUTES = PRESENTATION | {
    "id", "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points",
    "dx", "dy", "transform", "preserveAspectRatio", "version",
    "gradientUnits", "gradientTransform", "spreadMethod", "offset", "fx", "fy",
    "clipPathUnits", "maskUnits", "maskContentUnits", "style", "href",
}
LOCAL_URL = re.compile(r"^url\(\s*['\"]?#[\w.-]+['\"]?\s*\)\Z")
LOCAL_REF = re.compile(r"^#[\w.-]+\Z")
NUMBER = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(px)?\s*$")
# nothing of this belongs into a value of a drawing
FORBIDDEN = re.compile(r"javascript:|data:|expression|@import|[<>\\]|&#", re.I)
# the only functions a value may call: colours, the reference into the file, and the transforms
FUNCTION = re.compile(r"([A-Za-z-]+)\s*\(")
# the encoding an XML declaration names; only UTF-8 and encodings that write ASCII as ASCII (Latin-1, Windows-1252)
# are read, so that the check for DOCTYPE and entities on the bytes sees what the parser sees (audit 2026-10-13, IN-8)
DECLARED = re.compile(rb"^(?:\xef\xbb\xbf)?\s*<\?xml[^>]*?encoding\s*=\s*[\"']([A-Za-z0-9._-]+)[\"']")
ASCII_ENCODINGS = re.compile(rb"(utf-?8|(us-)?ascii|iso-?8859-\d+|latin-?1|(windows|cp)-?125\d)\Z", re.I)
FUNCTIONS = {"url", "rgb", "rgba", "hsl", "hsla", "matrix", "translate", "scale", "rotate", "skewx", "skewy"}


# not accepted, and the drawing would look different without them; (German, English), chosen when refused (X85)
NEEDED = {
    "style": ("Die SVG-Datei legt Farben in einem eingebetteten Stylesheet fest (<style>). Bitte so exportieren, dass "
              "die Farben an den Formen stehen (Illustrator: Styling „Präsentationsattribute“; Inkscape: „Normales SVG“).",
              "The SVG file sets colours in an embedded stylesheet (<style>). Please export it so that the colours are "
              "on the shapes (Illustrator: styling “Presentation Attributes”; Inkscape: “Plain SVG”)."),
    "image": ("Die SVG-Datei enthält ein eingebettetes Pixelbild. Bitte ein reines Vektor-Logo oder eine PNG-Datei "
              "hochladen.", "The SVG file contains an embedded pixel image. Please upload a pure vector logo or a PNG "
              "file."),
    "foreignObject": ("Die SVG-Datei enthält eingebettetes HTML. Bitte ein reines Vektor-Logo hochladen.",
                      "The SVG file contains embedded HTML. Please upload a pure vector logo."),
}


class Refused(ValueError):
    """The file is no SVG a logo can be made of (message for the researcher, in the language of the page)."""


def looks_like_svg(data):
    head = data[:2048].lstrip(b"\xef\xbb\xbf").lstrip()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:4096]) or (
        head.startswith(b"<!--") and b"<svg" in data[:4096])


def _safe_value(name, value):
    value = value.strip()
    if len(value) > 20000 and name not in ("d", "points"):
        return None
    if FORBIDDEN.search(value):
        return None
    if name not in ("d", "points") and any(f.lower() not in FUNCTIONS for f in FUNCTION.findall(value)):
        return None  # e.g. image-set(...), which loads from elsewhere like url(...) does
    if "url(" in value.lower():
        return value if LOCAL_URL.match(value) else None
    return value


def _safe_style(value):
    """A style attribute: the same properties as attributes, checked one by one."""
    kept = []
    for part in value.split(";"):
        if ":" not in part:
            continue
        prop, val = (s.strip() for s in part.split(":", 1))
        if prop in PRESENTATION and _safe_value(prop, val) is not None:
            kept.append(f"{prop}: {_safe_value(prop, val)}")
    return "; ".join(kept)


def _local(tag):
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _copy(source, count, depth=1):
    count[0] += 1
    if count[0] > MAX_ELEMENTS:
        raise Refused(de_en("Die SVG-Datei ist zu umfangreich für ein Logo.", "The SVG file is too large for a logo."))
    if depth > MAX_DEPTH:
        # 1500 groups inside each other used to end in a RecursionError (a server error instead of a message)
        raise Refused(de_en("Die SVG-Datei ist zu tief verschachtelt für ein Logo.",
                                "The SVG file is nested too deeply for a logo."))
    target = ET.Element(f"{{{SVG_NS}}}{_local(source.tag)}")
    for name, value in source.attrib.items():
        if name.startswith("{") and not name.startswith(f"{{{XLINK_NS}}}"):
            continue  # attributes of other vocabularies (editors store their own data there)
        key = _local(name)
        if key not in ATTRIBUTES:
            continue  # among them every on… handler
        if key == "href":
            if LOCAL_REF.match(value.strip()):
                target.set("href", value.strip())
            continue
        if key == "style":
            style = _safe_style(value)
            if style:
                target.set("style", style)
            continue
        safe = _safe_value(key, value)
        if safe is not None:
            target.set(key, safe)
    if _local(source.tag) in ("text", "tspan", "title", "desc"):
        target.text = source.text
    for child in source:
        if not isinstance(child.tag, str):
            continue  # comments, processing instructions
        name = _local(child.tag)
        if child.tag.startswith(f"{{{SVG_NS}}}") and name in ELEMENTS:
            target.append(_copy(child, count, depth + 1))
            if _local(source.tag) in ("text", "tspan"):
                target[-1].tail = child.tail
        elif name in NEEDED:
            # dropping these would change how the logo looks: better say so than show a wrong logo
            raise Refused(de_en(*NEEDED[name]))
    return target


def _drawn(root):
    """How many elements the browser draws once every <use> is replaced by what it points at. A file of 2 KB
    can nest <use> so that this is a hundred billion (each level uses the one below ten times); the tab of
    everyone who sees the logo would hang. Refused beyond MAX_DRAWN, and for a <use> that points at itself."""
    by_id = {e.get("id"): e for e in root.iter() if e.get("id")}
    sizes, open_ = {}, set()

    def size(element, depth=1):
        key = id(element)
        if key in sizes:
            return sizes[key]
        if key in open_:
            raise Refused(de_en("Die SVG-Datei verweist mit <use> auf sich selbst.",
                                "The SVG file refers to itself with <use>."))
        if depth > MAX_USE_DEPTH:  # a long chain of <use>, each pointing at the next
            raise Refused(de_en("Die SVG-Datei ist zu tief verschachtelt für ein Logo.",
                                "The SVG file is nested too deeply for a logo."))
        open_.add(key)
        total = 1
        if _local(element.tag) == "use":
            target = by_id.get((element.get("href") or "")[1:])
            if target is not None:
                total += size(target, depth + 1)
        for child in element:
            total += size(child, depth + 1)
            if total > MAX_DRAWN:
                raise Refused(de_en("Die SVG-Datei ist zu umfangreich für ein Logo (zu viele verschachtelte <use>-Verweise).",
                                    "The SVG file is too large for a logo (too many nested <use> references)."))
        open_.discard(key)
        sizes[key] = total
        return total

    return size(root)


def clean(data):
    """(bytes of the cleaned SVG, width, height) or Refused. Width and height are the size the drawing asks for
    (from viewBox, else width/height), for the <img> element."""
    if len(data) > MAX_BYTES:
        raise Refused(de_en("Die SVG-Datei ist größer als {kb} KB.", "The SVG file is larger than {kb} KB.",
                            kb=MAX_BYTES // 1024))
    declared = DECLARED.match(data)
    if b"\x00" in data or data[:2] in (b"\xff\xfe", b"\xfe\xff") or (
            declared and not ASCII_ENCODINGS.match(declared.group(1))):
        # UTF-16 or UTF-32 (expat finds them by their zero bytes or the byte order mark) or another declared
        # encoding: "<!DOCTYPE" would not show in the bytes below, the parser would read it all the same (IN-8)
        raise Refused(de_en("SVG-Dateien bitte in UTF-8 speichern.", "Please save SVG files in UTF-8."))
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise Refused(de_en("SVG-Dateien mit DOCTYPE oder Entities werden nicht angenommen. Bitte als „Plain SVG“ "
                            "exportieren.", "SVG files with DOCTYPE or entities are not accepted. Please export as "
                            "“Plain SVG”."))
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise Refused(de_en("Die SVG-Datei lässt sich nicht lesen.", "The SVG file cannot be read.")) from e
    if root.tag != f"{{{SVG_NS}}}svg":
        raise Refused(de_en("Die Datei ist keine SVG-Grafik.", "The file is no SVG graphic."))
    out = _copy(root, [0])
    _drawn(out)
    width = height = None
    box = (out.get("viewBox") or "").replace(",", " ").split()
    if len(box) == 4:
        try:
            width, height = float(box[2]), float(box[3])
        except ValueError:
            width = height = None
        if width is not None and not (math.isfinite(width) and math.isfinite(height)
                                      and 0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
            width = height = None  # "inf", "nan" or absurd numbers: as if there were no viewBox
            out.attrib.pop("viewBox", None)
    if not width or not height or width <= 0 or height <= 0:
        w, h = NUMBER.match(out.get("width") or ""), NUMBER.match(out.get("height") or "")
        if not (w and h) or not 0 < float(w.group(1)) <= MAX_SIDE or not 0 < float(h.group(1)) <= MAX_SIDE:
            raise Refused(de_en("Die SVG-Datei nennt keine Größe (viewBox oder width und height).",
                                "The SVG file gives no size (viewBox or width and height)."))
        width, height = float(w.group(1)), float(h.group(1))
        out.set("viewBox", f"0 0 {width:g} {height:g}")
    if not any(True for _ in out.iter() if _ is not out and _local(_.tag) not in ("title", "desc", "defs")):
        raise Refused(de_en("Die SVG-Datei enthält keine Zeichnung, die übernommen werden kann.",
                            "The SVG file contains no drawing that can be used."))
    # Keep attribution in a newly built, inert packet; never retain source XML, editor
    # history, contact details, scripts or external references from <metadata>.
    from app import image_rights

    # Drawing elements must not consume the separate metadata parser's element budget.
    attribution = ET.Element("attribution")
    for node in root.iter(f"{{{SVG_NS}}}metadata"):
        attribution.append(node)
    rights = image_rights.extract_xmp_tree(attribution)
    if rights:
        metadata = ET.SubElement(out, f"{{{SVG_NS}}}metadata")
        metadata.append(ET.fromstring(image_rights.xmp(rights)))
    ET.register_namespace("", SVG_NS)
    return ET.tostring(out, encoding="utf-8", xml_declaration=False), round(width), round(height)
