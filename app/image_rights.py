"""Bounded attribution metadata, independent of camera/location/private image metadata.

Only these six fields survive. Values are untrusted plain text, not evidence of permission.
Rebuild metadata instead of copying EXIF, Photoshop or XMP packets: those also carry GPS,
camera serial numbers, editing history and arbitrary XML. No network requests are made.
"""
import io
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
import zlib
from html.parser import HTMLParser
from html import unescape
from itertools import islice
from urllib.parse import urlsplit

FIELDS = ("creator", "copyright", "credit", "source", "license", "license_url")
MAX_PACKET = 128 * 1024
MAX_VALUE = 4096
MAX_TOTAL = 24 * 1024
MAX_VALUES = 16
GIF_PREFIX = b"Askurious image rights\n"
NS = {"x": "adobe:ns:meta/", "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
      "dc": "http://purl.org/dc/elements/1.1/", "photoshop": "http://ns.adobe.com/photoshop/1.0/",
      "xmpRights": "http://ns.adobe.com/xap/1.0/rights/", "cc": "http://creativecommons.org/ns#"}


def _tag(prefix, name):
    return "{" + NS[prefix] + "}" + name


XMP_FIELDS = {_tag("dc", "creator"): "creator", _tag("dc", "rights"): "copyright",
              _tag("dc", "source"): "source", _tag("photoshop", "Credit"): "credit",
              _tag("photoshop", "Source"): "source", _tag("xmpRights", "UsageTerms"): "license",
              _tag("xmpRights", "WebStatement"): "license_url", _tag("cc", "license"): "license_url",
              _tag("cc", "attributionName"): "credit", _tag("cc", "attributionURL"): "source"}
PNG_FIELDS = {"author": "creator", "artist": "creator", "creator": "creator", "copyright": "copyright",
              "credit": "credit", "source": "source", "license": "license", "licence": "license",
              "license url": "license_url", "licence url": "license_url"}


class _Text(HTMLParser):
    MARKUP = {"a", "b", "i", "em", "strong", "p", "div", "span", "br", "hr", "ul", "ol", "li", "dl", "dt", "dd",
              "img", "iframe", "object", "embed", "svg", "math", "style", "script", "html", "head", "body", "title",
              "meta", "link", "table", "thead", "tbody", "tr", "td", "th", "form", "input", "button", "label",
              "small", "sub", "sup", "code", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden and tag not in self.MARKUP:
            self.parts.append(self.get_starttag_text())  # a literal name such as "Copyright <Studio> Ltd"

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag not in self.MARKUP:
            self.parts.append("</" + tag + ">")

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def _plain(value):
    if isinstance(value, bytes):
        value = value[:MAX_VALUE * 4]
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            value = value.decode("latin-1")  # legacy IPTC IIM text without a UTF-8 character-set marker
    if not isinstance(value, str):
        return ""
    value = value[:MAX_VALUE]
    # These formats already supply text, not HTML entity source. A bare '&copy=' in
    # a licence URL is a query parameter and must not turn into a copyright glyph.
    if "<" in value:
        parser = _Text()
        try:
            parser.feed(value)
            parser.close()  # flush a literal ampersand at EOF, e.g. rights held by AT&T
            value = "".join(parser.parts)
        except (AssertionError, ValueError):
            # Broken markup is optional metadata, not grounds for rejecting the image. This remains
            # text: the XML/PNG encoders escape it; raw HTML/XML is never inserted into an output tree.
            value = unescape(re.sub(r"<[^>]*>", "", value))
    return " ".join("".join(" " if c in "\x00\n\t" else c for c in value
                            if not unicodedata.category(c).startswith("C") or c in "\x00\n\t").split())


def _add(result, field, value):
    if field not in FIELDS:
        return
    if isinstance(value, (list, tuple)):
        for part in value[:MAX_VALUES]:
            _add(result, field, part)
        return
    value = _plain(value)
    if not value:
        return
    if field == "license_url":
        try:
            url = urlsplit(value)
            if url.scheme.lower() not in {"https", "http"} or not url.hostname or url.username or url.password:
                return
        except ValueError:
            return
    existing = result.setdefault(field, [])
    used = sum(len(v.encode("utf-8")) for values in result.values() for v in values)
    if value not in existing and len(existing) < MAX_VALUES and used + len(value.encode("utf-8")) <= MAX_TOTAL:
        existing.append(value)


def normalize(values):
    """Bounded, plain attribution supplied separately from an image; no permission grant is inferred."""
    result = {}
    if isinstance(values, dict):
        for field in FIELDS:
            value = values.get(field)
            if isinstance(value, (list, tuple)):
                for part in value[:MAX_VALUES]:
                    if isinstance(part, (str, bytes)):
                        _add(result, field, part)
            elif isinstance(value, (str, bytes)):
                _add(result, field, value)
    return {field: value for field, value in result.items() if value}


def extract_xmp(raw):
    """Read only known rights properties, including RDF arrays/resource attributes; never retain raw XML."""
    result = {}
    if isinstance(raw, str):
        try:
            raw = raw.encode("utf-8")
        except UnicodeError:
            return result
    if not isinstance(raw, bytes) or len(raw) > MAX_PACKET:
        return result
    # NUL catches UTF-16/32 packets whose declarations could otherwise evade the ASCII scan.
    if b"\x00" in raw or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        return result
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError, LookupError):
        return result
    return extract_xmp_tree(root)


def extract_xmp_tree(root):
    """Read an already safely parsed XML tree (e.g. SVG); same rights allowlist and output limits."""
    result = {}
    for n, node in enumerate(root.iter()):
        if n > 4096:
            break
        for key, value in node.attrib.items():
            if key in XMP_FIELDS:
                _add(result, XMP_FIELDS[key], value)
        field = XMP_FIELDS.get(node.tag)
        if field:
            resource = node.get(_tag("rdf", "resource"))
            if resource:
                _add(result, field, resource)
            entries = [li.text for li in islice(node.iter(_tag("rdf", "li")), MAX_VALUES) if li.text]
            if not entries and field in {"creator", "copyright"}:
                # Inkscape/CC SVG metadata uses a named Agent instead of an RDF list.
                entries = [title.text for title in islice(node.iter(_tag("dc", "title")), MAX_VALUES) if title.text]
            _add(result, field, entries or node.text)
    return result


def _png_metadata(img):
    """Pillow has not read text after IDAT yet. Inspect bounded metadata chunks without decoding pixels."""
    stream = getattr(img, "fp", None)
    if stream is None:
        return []  # a loaded image already has its text in info
    found, consumed = [], 0
    position = stream.tell()
    try:
        stream.seek(8)
        for _ in range(4096):
            header = stream.read(8)
            if len(header) != 8:
                break
            size, kind = int.from_bytes(header[:4], "big"), header[4:]
            if kind == b"IEND":
                break
            if kind not in {b"tEXt", b"zTXt", b"iTXt", b"eXIf"} or size > MAX_PACKET:
                stream.seek(size + 4, 1)
                continue
            consumed += size
            if consumed > 4 * MAX_PACKET:
                break
            data = stream.read(size)
            stream.seek(4, 1)  # CRC; normal image loading remains responsible for image validity
            if kind == b"eXIf":
                found.append(("exif", data))
                continue
            key, separator, value = data.partition(b"\x00")
            if not separator or len(key) > 79:
                continue
            key = key.decode("latin-1")
            if key.lower() not in PNG_FIELDS and key != "XML:com.adobe.xmp":
                continue
            compressed, encoding = False, "latin-1"
            if kind == b"iTXt":
                if len(value) < 2 or value[0] not in (0, 1) or value[1] != 0:
                    continue
                compressed, encoding = bool(value[0]), "utf-8"
                parts = value[2:].split(b"\x00", 2)  # language tag, translated keyword, text
                if len(parts) != 3:
                    continue
                value = parts[2]
            elif kind == b"zTXt":
                if not value or value[0] != 0:
                    continue
                compressed, value = True, value[1:]
            if compressed:
                try:
                    decoder = zlib.decompressobj()
                    value = decoder.decompress(value, MAX_PACKET + 1)
                    if len(value) > MAX_PACKET or not decoder.eof:
                        continue
                except zlib.error:
                    continue
            found.append((key, value.decode(encoding, errors="replace")))
    except (OSError, ValueError):
        pass  # optional malformed metadata does not replace the image loader's validation
    finally:
        stream.seek(position)
    return found


def _read_exif(result, exif):
    for tag, field in ((315, "creator"), (33432, "copyright")):
        _add(result, field, exif.get(tag))
    xp_author = exif.get(40093)
    if isinstance(xp_author, (bytes, tuple)) and len(xp_author) <= MAX_VALUE * 2:
        try:
            _add(result, "creator", bytes(xp_author).decode("utf-16-le"))
        except (ValueError, UnicodeError):
            pass


def read(img):
    """Read a Pillow image's rights before conversion/transpose clears its metadata."""
    result = {}
    try:
        if img.format == "PNG":
            from PIL import Image

            # PNG.getexif() may decode every pixel while searching for a late eXIf chunk.
            exif = Image.Exif()
            if img.info.get("exif"):
                exif.load(img.info["exif"])
        else:
            exif = img.getexif()
        _read_exif(result, exif)
        packets = [exif.get(700)]
    except (ValueError, TypeError, OSError, SyntaxError):
        packets = []
    extra = _png_metadata(img) if img.format == "PNG" else []
    for key, value in extra:
        if key == "exif":
            from PIL import Image

            try:
                exif = Image.Exif()
                exif.load(value)
                _read_exif(result, exif)
                packets.append(exif.get(700))
            except (ValueError, TypeError, OSError, SyntaxError):
                pass
    packets += [img.info.get("xmp"), img.info.get("XML:com.adobe.xmp")]
    packets += [v for k, v in extra if k == "XML:com.adobe.xmp"]
    for packet in packets:
        for field, values in extract_xmp(packet).items():
            _add(result, field, values)
    if img.format == "PNG":
        for key, value in [*img.info.items(), *extra]:
            if key.lower() in PNG_FIELDS:
                _add(result, PNG_FIELDS[key.lower()], value)
    if img.format in {"JPEG", "TIFF"}:
        from PIL import IptcImagePlugin

        try:
            iptc = IptcImagePlugin.getiptcinfo(img) or {}
            for tag, field in ((80, "creator"), (110, "credit"), (115, "source"), (116, "copyright")):
                _add(result, field, iptc.get((2, tag)))
        except (ValueError, TypeError, OSError, SyntaxError, IndexError):
            pass  # malformed optional IPTC must not prevent a valid image from being used
    comment = img.info.get("comment", b"")
    if isinstance(comment, bytes) and comment.startswith(GIF_PREFIX) and len(comment) <= MAX_PACKET:
        try:
            stored = json.loads(comment[len(GIF_PREFIX):])
            if isinstance(stored, dict):
                for field, values in stored.items():
                    _add(result, field, values)
        except (ValueError, TypeError, RecursionError):
            pass
    return {k: v for k, v in result.items() if v}


def from_bytes(data):
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            return read(img)
    except (ValueError, OSError, SyntaxError):
        return {}


def xmp(rights):
    """A fresh packet containing only canonical plain-text rights values."""
    root = ET.Element(_tag("x", "xmpmeta"))
    rdf = ET.SubElement(root, _tag("rdf", "RDF"))
    description = ET.SubElement(rdf, _tag("rdf", "Description"), {_tag("rdf", "about"): ""})
    tags = {"creator": ("dc", "creator"), "copyright": ("dc", "rights"), "credit": ("photoshop", "Credit"),
            "source": ("dc", "source"), "license": ("xmpRights", "UsageTerms"), "license_url": ("cc", "license")}
    for field, (prefix, name) in tags.items():
        if field == "creator" and rights.get(field):
            sequence = ET.SubElement(ET.SubElement(description, _tag(prefix, name)), _tag("rdf", "Seq"))
            for value in rights[field]:
                ET.SubElement(sequence, _tag("rdf", "li")).text = value
            continue
        for value in rights.get(field, []):
            node = ET.SubElement(description, _tag(prefix, name))
            if field == "license_url":
                node.set(_tag("rdf", "resource"), value)
            elif field in {"copyright", "license"}:
                alternative = ET.SubElement(node, _tag("rdf", "Alt"))
                ET.SubElement(alternative, _tag("rdf", "li"), {"{http://www.w3.org/XML/1998/namespace}lang": "x-default"}).text = value
            else:
                node.text = value
    return ET.tostring(root, encoding="utf-8")


def save_options(rights, fmt):
    """Explicit encoder options; do not pass the source image's info/EXIF through."""
    if not rights:
        return {}
    if fmt == "GIF":
        return {"comment": GIF_PREFIX + json.dumps(rights, ensure_ascii=False).encode("utf-8")}
    from PIL import Image

    exif = Image.Exif()
    for tag, field in ((315, "creator"), (33432, "copyright")):
        if len(rights.get(field, [])) == 1:
            # EXIF ASCII cannot reliably carry international names; the authoritative UTF-8 XMP does.
            value = "; ".join(rights[field])
            if value.isascii():
                exif[tag] = value
    options = {"exif": exif.tobytes()} if exif else {}
    if fmt == "PNG":
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        info.add_itxt("XML:com.adobe.xmp", xmp(rights).decode("utf-8"))
        for field, key in (("creator", "Author"), ("copyright", "Copyright"), ("credit", "Credit"),
                           ("source", "Source"), ("license", "License"), ("license_url", "License URL")):
            if len(rights.get(field, [])) == 1:
                info.add_itxt(key, rights[field][0])
        options["pnginfo"] = info
    else:
        options["xmp"] = xmp(rights)
    return options
