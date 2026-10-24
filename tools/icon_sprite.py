"""Writes app/static/icons.svg from the icons of app/templates/_icons.html.

The icon() macro used to put every icon as a whole inline SVG into each page: 717 of them were 184 KB of the
577 KB editor overview of a survey with 66 elements (audit 2026-10-22, G-05). Now a page carries only
<svg class="icon"><use href="/static/icons.svg?v=…#name"/></svg>, and the drawings come once, as a static file
that is compressed (nginx gzips /static/) and cached under its content hash.

Run after changing an icon:  python tools/icon_sprite.py
tests/ui/test_icon_sprite.py refuses a sprite that is not what this script writes."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "app/templates/_icons.html"
SPRITE = ROOT / "app/static/icons.svg"
# what every icon is drawn with; on the <symbol> it reaches the shapes inside the <use> (and currentColor takes the
# colour of the text around the icon, as before)
LOOK = ('viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"')
_ENTRY = re.compile(r"""^\s*"([\w-]+)":\s*'([^']*)',?\s*$""", re.M)


def icons(text=None):
    """{name: inner SVG} in the order of _icons.html."""
    text = TEMPLATE.read_text(encoding="utf-8") if text is None else text
    block = text.split("{% set _paths = {", 1)[1].split("} %}", 1)[0]
    found = dict(_ENTRY.findall(block))
    names = re.findall(r'^\s*"([\w-]+)":', block, re.M)
    missing = [n for n in names if n not in found]
    if missing:  # an entry this reader cannot take (another quote, a line break): better loud than a blank icon
        raise ValueError(f"_icons.html: cannot read the icons {missing}")
    return found


def sprite(text=None):
    """The whole sprite file."""
    symbols = "\n".join(f'<symbol id="{name}" {LOOK}>{body}</symbol>' for name, body in icons(text).items())
    return ('<svg xmlns="http://www.w3.org/2000/svg">\n'
            "<!-- written by tools/icon_sprite.py from app/templates/_icons.html; do not edit by hand -->\n"
            f"{symbols}\n</svg>\n")


def main():
    SPRITE.write_bytes(sprite().encode("utf-8"))
    print(f"{SPRITE.relative_to(ROOT)}: {len(icons())} icons")


if __name__ == "__main__":
    sys.exit(main())
