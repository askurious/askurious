"""The fonts of every PDF the platform makes (reportlab), in one place: texts in every survey language.

The standard fonts of PDF (Helvetica, Times, Courier) print only Western European letters (cp1252); Polish ł, Turkish
ş ğ ı, Cyrillic, Arabic and Chinese came out as "?". Now:

- Noto Sans (Regular, Bold, Italic) lies in the repository (app/data/fonts, SIL Open Font License 1.1, OFL.txt next to
  it): Latin with all its accents, Greek and Cyrillic, in every installation.
- Arabic (Noto Sans Arabic) and Chinese (Noto Sans SC) are large and come with the Docker image
  (deploy/docker/Dockerfile) into PDF_FONT_DIR (default /usr/share/fonts/askurious). Where they are missing (a Plesk
  server, a development machine), their characters stay "?" as before, without an error.

reportlab sets a whole string in one font, so a text is split into runs, each in the first font of the chain that has
its characters (runs). Paragraphs get those runs as <font> tags (markup, paragraph); lines drawn on a canvas are drawn
run by run (draw, width). Arabic letters take their joined forms (arabic-reshaper, MIT) and lines that read from
right to left are put into visual order line by line (python-bidi, LGPL-3.0): paragraph() breaks such a text into
lines itself and sets it right-aligned. Chinese breaks between any two characters (wordWrap "CJK").

Word files need none of this: Word sets them in the fonts of the reader's computer."""
import functools
import os
import re
from html import escape, unescape

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fonts")
EXTRA_DIR = "/usr/share/fonts/askurious"  # where deploy/docker/Dockerfile puts the large fonts

REGULAR, BOLD, ITALIC = "NotoSans", "NotoSans-Bold", "NotoSans-Italic"
# the fonts of the repository: (name, file)
OWN = ((REGULAR, "NotoSans-Regular.ttf"), (BOLD, "NotoSans-Bold.ttf"), (ITALIC, "NotoSans-Italic.ttf"))
# the large fonts of the image: (family, regular file, bold file); a missing bold file takes the regular one
EXTRA = (("NotoSansArabic", "NotoSansArabic-Regular.ttf", "NotoSansArabic-Bold.ttf"),
         ("NotoSansSC", "NotoSansSC-Regular.ttf", "NotoSansSC-Bold.ttf"))
# the standard fonts some PDFs keep on purpose (the transcript in Courier, like Courier New); they print cp1252
STANDARD = {"Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique", "Helvetica", "Helvetica-Bold",
            "Helvetica-Oblique", "Times-Roman", "Times-Bold"}

_ARABIC = re.compile("[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_RTL = re.compile("[֐-ࣿיִ-﷿ﹰ-﻿]")
_STRONG = re.compile(r"[^\W\d_]")
_CJK = re.compile("[⺀-鿿가-힯豈-﫿＀-￯]")
_TAG = re.compile(r"<[^>]+>")


def extra_dir():
    return os.environ.get("PDF_FONT_DIR") or EXTRA_DIR


@functools.lru_cache(maxsize=None)
def _registered(folder):
    """Register the fonts once per process: {"regular": [...], "bold": [...], "italic": [...]} as fallback chains."""
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name, file in OWN:
        pdfmetrics.registerFont(TTFont(name, os.path.join(HERE, file)))
    for bold in (0, 1):
        for italic in (0, 1):
            addMapping(REGULAR, bold, italic, BOLD if bold else ITALIC if italic else REGULAR)
    chains = {"regular": [REGULAR], "bold": [BOLD], "italic": [ITALIC]}
    for family, regular, bold in EXTRA:
        path = os.path.join(folder, regular)
        if not os.path.isfile(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(family, path))
            bold_name = family
            if os.path.isfile(os.path.join(folder, bold)):
                bold_name = family + "-Bold"
                pdfmetrics.registerFont(TTFont(bold_name, os.path.join(folder, bold)))
        except Exception:  # noqa: BLE001 - a broken file is left out: its characters stay "?" as without it
            continue
        for b in (0, 1):
            for i in (0, 1):
                addMapping(family, b, i, bold_name if b else family)
        chains["regular"].append(family)
        chains["italic"].append(family)
        chains["bold"].append(bold_name)
    return chains


def file_of(name):
    """The TrueType file of a registered font name (for Pillow, app/svg_png.py)."""
    from reportlab.pdfbase import pdfmetrics

    register()
    return pdfmetrics.getFont(name).face.filename


def register():
    """Make the fonts known to reportlab (once); returns the fallback chains."""
    return _registered(extra_dir())


def font(bold=False, italic=False):
    """The name of the text font for a style."""
    register()
    return BOLD if bold else ITALIC if italic else REGULAR


def available(family):
    """Whether a large font of the image ("NotoSansArabic", "NotoSansSC") is there."""
    return family in register()["regular"]


@functools.lru_cache(maxsize=None)
def _covers(name, ch):
    if name in STANDARD:
        try:
            ch.encode("cp1252")
            return True
        except UnicodeEncodeError:
            return False
    from reportlab.pdfbase import pdfmetrics

    return ord(ch) in pdfmetrics.getFont(name).face.charToGlyph


def _chain(base):
    chains = register()
    if base in (BOLD, "Helvetica-Bold", "Times-Bold", "Courier-Bold", "Courier-BoldOblique"):
        rest = chains["bold"]
    elif base in (ITALIC, "Helvetica-Oblique", "Courier-Oblique"):
        rest = chains["italic"]
    else:
        rest = chains["regular"]
    return [base] + [name for name in rest if name != base]


def shape(text):
    """Arabic letters in their joined forms (logical order kept); other texts as they are."""
    text = str(text or "")
    if not _ARABIC.search(text):
        return text
    try:
        import arabic_reshaper
    except ImportError:  # pragma: no cover - in requirements.lock; without it the letters stay unjoined
        return text
    return arabic_reshaper.reshape(text)


def rtl(text):
    """Whether a text reads from right to left: its first letter is Arabic or Hebrew (Unicode bidi rule P2)."""
    first = _STRONG.search(str(text or ""))
    return bool(first and _RTL.match(first.group(0)))


def visual(line, base=None):
    """One line in the order it is drawn from left to right: joined Arabic forms, right-to-left runs turned round.
    `base` "R" or "L"; by default the direction of the line's first letter."""
    line = shape(line)
    if not _RTL.search(line):
        return line
    try:
        from bidi import get_display
    except ImportError:  # pragma: no cover - in requirements.lock
        return line
    return get_display(line, base_dir=base or ("R" if rtl(line) else "L"))


def runs(text, base=REGULAR):
    """[(font name, text)]: the text split where the font has to change, each run in the first font of the chain
    (`base`, then Noto Sans, Arabic, Chinese) that has its characters; a character none has is "?" in `base`.
    Spaces, digits and punctuation stay in the font of the run they are in when that font has them."""
    register()
    chain = _chain(base)
    out = []
    for ch in str(text or ""):
        current = out[-1][0] if out else None
        if current and not ch.isalpha() and _covers(current, ch):
            name = current
        else:
            name = next((n for n in chain if _covers(n, ch)), None)
            if name is None:
                name, ch = base, "?"
        if out and out[-1][0] == name:
            out[-1][1].append(ch)
        else:
            out.append((name, [ch]))
    return [(name, "".join(chars)) for name, chars in out]


def width(text, size, base=REGULAR):
    """Width in points of a line as draw() sets it."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return sum(stringWidth(part, name, size) for name, part in runs(visual(text), base))


def draw(c, x, y, text, size, base=REGULAR, anchor="start"):
    """Draw one line on a reportlab canvas at (x, y), anchor "start", "middle" or "end" (of the line drawn); the
    canvas keeps its font afterwards."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    parts = runs(visual(text), base)
    total = sum(stringWidth(part, name, size) for name, part in parts)
    x -= total / 2 if anchor == "middle" else total if anchor == "end" else 0
    keep = (c._fontname, c._fontsize)
    for name, part in parts:
        c.setFont(name, size)
        c.drawString(x, y, part)
        x += stringWidth(part, name, size)
    c.setFont(*keep)


def strings(x, y, text, size, base=REGULAR, anchor="start", **kw):
    """[reportlab.graphics String] of one line for a Drawing (one String per font run), anchor "start", "middle" or
    "end"; kw as for String (fillColor, ...)."""
    from reportlab.graphics.shapes import String
    from reportlab.pdfbase.pdfmetrics import stringWidth

    parts = runs(visual(text), base)
    total = sum(stringWidth(part, name, size) for name, part in parts)
    x -= total / 2 if anchor == "middle" else total if anchor == "end" else 0
    out = []
    for name, part in parts:
        out.append(String(x, y, part, fontName=name, fontSize=size, textAnchor="start", **kw))
        x += stringWidth(part, name, size)
    return out


def markup(text, base=REGULAR, order=True):
    """A plain text escaped for a reportlab Paragraph whose style font is `base`, runs in another font as <font> tags.
    A text that reads left to right gets its right-to-left words in visual order; a right-to-left text keeps its
    reading order (paragraph() orders it line by line). `order` False: the text is in visual order already."""
    text = str(text or "")
    if order and _RTL.search(text):
        text = shape(text) if rtl(text) else visual(text, "L")
    out = []
    for name, part in runs(text, base):
        part = escape(part, quote=False)
        out.append(part if name == base else f'<font face="{name}">{part}</font>')
    return "".join(out)


def lines(text, size, width_pt, base=REGULAR):
    """The text broken into lines no wider than `width_pt`: at spaces, inside a word only when it is wider than the
    line, between any two Chinese characters; its own line breaks kept."""
    out = []
    for para in str(text or "").split("\n"):
        words = re.findall(r"[⺀-鿿가-힯豈-﫿＀-￯]|[^\s⺀-鿿가-힯"
                           r"豈-﫿＀-￯]+|\s+", para)
        line = ""
        for word in words:
            candidate = line + word
            if not line or width(candidate.strip(), size, base) <= width_pt:
                line = candidate
                continue
            out.append(line.strip())
            line = "" if word.isspace() else word
            while line and width(line, size, base) > width_pt and len(line) > 1:
                cut = next((n for n in range(len(line) - 1, 0, -1) if width(line[:n], size, base) <= width_pt), 1)
                out.append(line[:cut])
                line = line[cut:]
        out.append(line.strip())
    return out


def _plain(text):
    return unescape(_TAG.sub("", str(text or "").replace("<br/>", "\n").replace("<br />", "\n")))


def paragraph(text, style, **kw):
    """A reportlab Paragraph of `text` (Paragraph markup, as markup() makes it): Chinese breaks between characters, a
    text that reads right to left is broken into lines here and set right-aligned in visual order (its inline tags
    other than line breaks are dropped then)."""
    from reportlab.platypus import Paragraph

    plain = _plain(text)
    if rtl(plain):
        return _RtlParagraph(plain, style, **kw)
    if _CJK.search(plain):
        from reportlab.lib.styles import ParagraphStyle

        style = ParagraphStyle(style.name + "-cjk", parent=style, wordWrap="CJK")
    return Paragraph(text, style, **kw)


@functools.lru_cache(maxsize=None)
def _rtl_paragraph_class():
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    class RtlParagraph(Paragraph):
        """A right-to-left paragraph: broken into lines for the width it gets, each line in visual order."""

        def __init__(self, text, style=None, *args, _plain=None, **kw):
            if _plain is None:  # the halves of split() are plain paragraphs of finished lines
                super().__init__(text, style, *args, **kw)
                return
            self._rtl_text = _plain
            self._rtl_style = ParagraphStyle(style.name + "-rtl", parent=style, alignment=TA_RIGHT)
            super().__init__(markup(visual(_plain, "R"), style.fontName, order=False), self._rtl_style, *args, **kw)

        def wrap(self, availWidth, availHeight):
            if getattr(self, "_rtl_text", None) is not None:
                st = self._rtl_style
                room = availWidth - st.leftIndent - st.rightIndent
                if room != getattr(self, "_rtl_room", None):  # broken anew for every width it is offered
                    body = "<br/>".join(markup(visual(line, "R"), st.fontName, order=False)
                                        for line in lines(self._rtl_text, st.fontSize, room, st.fontName))
                    text, self._rtl_text = self._rtl_text, None
                    Paragraph.__init__(self, body, st)
                    self._rtl_text, self._rtl_room = text, room
            return super().wrap(availWidth, availHeight)

    return RtlParagraph


def _RtlParagraph(plain, style, **kw):
    return _rtl_paragraph_class()(None, style, _plain=plain, **kw)
