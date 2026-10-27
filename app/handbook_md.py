"""Markdown of the handbook (app/data/handbook/<lang>/*.md, app/handbook.py) as HTML: the little the pages use, with
everything escaped. Headings get ids (the side navigation lists the second level), images come from
static/handbook/<lang>/ (or static/handbook/ when a picture has no version in the language), links to another page
("build.md#4-settings") go to its address under /docs/; a link to the former page "dialogs.md#…" goes to the page that
now has the heading.

Blocks: headings (#, ##, ###), paragraphs (lines joined), a highlighted box ("> " lines), bullet and numbered
lists (continuation lines indented),
tables (| a | b |), images on a line of their own (![alt](img/x.jpg)), fenced code (```), a rule (---). Inline:
**bold**, *italic*, `code`, [text](link)."""
import re

from flask import url_for
from markupsafe import Markup, escape

# the pages in the order of the side navigation; "Seiten und Dialoge" was one page until 2026-10-22 (Ralf: split it,
# with guides by goal in front), /docs/dialogs is now an overview that leads to them
PAGES = ("index", "guides", "build", "field", "analysis", "kinds", "more", "metadata", "ai")


def slug(text):
    """The id of a heading, as GitHub makes it: lower case, punctuation gone, spaces as hyphens."""
    text = re.sub(r"[^\w\s-]", "", str(text).lower(), flags=re.UNICODE)
    return re.sub(r"\s", "-", text.strip())


def _href(target):
    if re.match(r"https?://|mailto:", target):
        return target, True
    page, _, anchor = target.partition("#")
    if not page:
        return "#" + anchor, False
    name = page.removesuffix(".md")
    if name in ("README", "index"):
        name = "index"
    if name == "dialogs" and anchor:  # the former single page: the page that has the heading now
        from app.handbook import page_of

        name = page_of(anchor) or name
    if name == "dialogs":
        return url_for("handbook.page", name="dialogs") + ("#" + anchor if anchor else ""), False
    if name in PAGES:
        url = url_for("handbook.page", name=name) if name != "index" else url_for("handbook.index")
        return url + ("#" + anchor if anchor else ""), False
    return target, False


def shot(src):
    """The file of a screenshot under static/: the one taken in the reader's language (static/handbook/de/, /en/,
    tools/handbook_shots), else the one without a language."""
    import os

    from flask import current_app

    from app.lang import current

    name = src.rsplit("/", 1)[-1]
    own = f"handbook/{current()}/{name}"
    return own if os.path.exists(os.path.join(current_app.static_folder, own)) else "handbook/" + name


def _image(src):
    return url_for("static", filename=shot(src))


def inline(text):
    out = str(escape(text))
    codes = []

    def keep(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    out = re.sub(r"`([^`]+)`", keep, out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", out)

    def link(m):
        href, outside = _href(m.group(2).replace("&amp;", "&"))
        extra = ' target="_blank" rel="noopener"' if outside else ""
        return f'<a href="{escape(href)}"{extra}>{m.group(1)}</a>'

    def picture(m):  # a screenshot inside a line, e.g. two phones side by side in a table
        from app import media

        src = _image(m.group(2))
        width, height = media.size_static(shot(m.group(2)))
        dimensions = f' width="{width}" height="{height}"' if width and height else ""
        return (f'<a href="{escape(src)}" target="_blank" rel="noopener"><img src="{escape(src)}" alt="{m.group(1)}"'
                f'{dimensions} class="handbook-inline-shot" loading="lazy" decoding="async"></a>')

    out = re.sub(r"!\[([^\]]*)\]\((img/[^)\s]+)\)", picture, out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, out)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", out)


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def render(md):
    """(HTML, [(id, text) of the second-level headings])."""
    lines = md.splitlines()
    html, toc, para = [], [], []
    i = 0

    def flush():
        if para:
            html.append("<p>" + " ".join(inline(x) for x in para) + "</p>")
            para.clear()

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line:
            flush()
            i += 1
        elif line.startswith("```"):
            flush()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            html.append("<pre><code>" + str(escape("\n".join(block))) + "</code></pre>")
            i += 1
        elif re.match(r"^#{1,4}\s", line):
            flush()
            level = len(line) - len(line.lstrip("#"))
            text = line.lstrip("#").strip()
            hid = slug(text)
            if level == 2:
                toc.append((hid, text))
            html.append(f'<h{level} id="{escape(hid)}">{inline(text)}</h{level}>')
            i += 1
        elif line.startswith(">"):
            flush()
            quote, block = [], []
            while i < len(lines) and lines[i].strip().startswith(">"):
                text = lines[i].strip()[1:].strip()
                if text:
                    block.append(text)
                elif block:
                    quote.append(block)
                    block = []
                i += 1
            if block:
                quote.append(block)
            html.append('<div class="handbook-callout">' + "".join("<p>" + " ".join(inline(x) for x in b) + "</p>"
                                                                   for b in quote) + "</div>")
        elif line == "---":
            flush()
            html.append("<hr>")
            i += 1
        elif re.match(r"^!\[[^\]]*\]\([^)]+\)$", line):
            flush()
            alt, src = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", line).groups()
            from app import media

            filename = shot(src)
            width, height = media.size_static(filename)
            dimensions = f' width="{width}" height="{height}"' if width and height else ""
            html.append(f'<figure class="handbook-shot"><a href="{escape(_image(src))}" target="_blank" rel="noopener">'
                        f'<img src="{escape(_image(src))}" alt="{escape(alt)}"{dimensions} loading="lazy" decoding="async"></a>'
                        f'<figcaption>{inline(alt)}</figcaption></figure>')
            i += 1
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            flush()
            head = _cells(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            html.append('<div class="res-scroll"><table class="handbook-table"><thead><tr>'
                        + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                        + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
                        + "</tbody></table></div>")
        elif re.match(r"^(- |\d+\. )", line):
            flush()
            ordered = not line.startswith("- ")
            items = []
            while i < len(lines):
                cur = lines[i]
                s = cur.strip()
                if re.match(r"^(- |\d+\. )", s) and (len(cur) - len(cur.lstrip())) < 2:
                    items.append([re.sub(r"^(- |\d+\. )", "", s)])
                elif s and cur.startswith("  ") and items:
                    items[-1].append(s)
                else:
                    break
                i += 1
            tag = "ol" if ordered else "ul"
            html.append(f"<{tag}>" + "".join("<li>" + _item(it) + "</li>" for it in items) + f"</{tag}>")
        else:
            para.append(line)
            i += 1
    flush()
    return Markup("\n".join(html)), toc


def _item(parts):
    """A list item: its first line, then nested bullets ("- …" indented) as a list of their own."""
    head, sub = [], []
    for p in parts:
        if p.startswith("- "):
            sub.append(p[2:])
        elif sub:
            sub[-1] += " " + p
        else:
            head.append(p)
    out = " ".join(inline(x) for x in head)
    if sub:
        out += "<ul>" + "".join(f"<li>{inline(s)}</li>" for s in sub) + "</ul>"
    return out
