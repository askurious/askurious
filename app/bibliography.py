"""The references of the constructs as BibTeX and RIS, for reference managers and manuscripts.

A construct names its sources as one APA-style text (Construct.citation, sometimes two or three references in a row,
e.g. the paper and the ZIS documentation) plus a DOI (Construct.doi). This module splits that text into references
and reads each one: authors, year, title, and the source as a journal ("Journal, 41(1), 203–212"), a chapter in a
book ("In Book (Series, S. 453–471). Publisher"), an entry of the ZIS collection, an arXiv preprint, or nothing
(unpublished), or a report with only its publisher after the title. An organisation as author is read when it
is one of CORPORATE. A DOI or link written into a reference belongs to it; the construct's DOI goes to the reference
that has none: a ZIS DOI (10.6102/zis…) to the ZIS entry, any other to the first other reference. What cannot be
read becomes a "misc" entry with the whole text as a note, so nothing is lost. tests/exports/test_bibliography.py reads
every seed construct, so a new citation that does not parse shows up there."""
import re
import unicodedata

ZIS = "Zusammenstellung sozialwissenschaftlicher Items und Skalen (ZIS)"
_FAMILY = r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž][\w'’\-]*(?: [A-Za-zÀ-ÖØ-öø-ÿĀ-ž][\w'’\-]*)*"
_INITIALS = r"[A-ZÀ-ÖØ-ÞĀ-Ž]\.(?:[ \-]?[A-ZÀ-ÖØ-ÞĀ-Ž]\.)*"
_AUTHOR = rf"{_FAMILY}, {_INITIALS}"
_AUTHORS = re.compile(rf"(?:[A-ZÄÖÜ][\w ]*: )?({_AUTHOR}(?:, {_AUTHOR})*(?:,? & {_AUTHOR})?)")
_YEAR = re.compile(r" \((\d{4})[a-z]?\)\. ")
_DOI_URL = re.compile(r"https?://(?:dx\.)?doi\.org/(10\.\S+?)\.?(?=\s|$)")
_URL = re.compile(r"(https?://\S+?)\.?(?=\s|$)")
_ARTICLE = re.compile(r"^(?P<journal>[^,]+(?:, [^,\d][^,]*)*), (?P<volume>\d+)(?:\((?P<issue>[^)]+)\))?, "
                      r"(?P<pages>[\dA-Za-z]+(?:[–-][\dA-Za-z]+)?)\.?$")
_CHAPTER = re.compile(r"^In (?P<book>.+?) \((?:(?P<series>[^,()]+), )?(?:S\.|pp?\.) (?P<pages>\d+(?:[–-]\d+)?)\)\.?"
                      r"(?: (?P<publisher>.+?)\.?)?$")
_ARXIV = re.compile(r"^arXiv:(?P<id>[\w./-]+)\.?$")
_PUBLISHER = re.compile(r"[A-ZÄÖÜ][^\d,;()]*")  # a name alone, e.g. "RatSWD", "Zenodo"
# organisations that are authors (APA "group author"): their name stands where the authors stand
CORPORATE = ("Rat für Sozial- und Wirtschaftsdaten",)


def _title_end(body):
    """Where the title ends: after the first ".", "?" or "!" followed by a space or the end, outside parentheses
    ("Katalog (2. Aufl.; Reihe Nr. 4b). Verlag"). 0 if there is none."""
    depth = 0
    for n, ch in enumerate(body):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth <= 0 and ch in ".?!" and (n + 1 == len(body) or body[n + 1] == " "):
            return n + 1
    return 0


def split(citation):
    """The single references of a citation text, in order."""
    text = " ".join((citation or "").split())
    years = list(_YEAR.finditer(text))
    if len(years) < 2:
        return [text] if text else []
    starts = [0]
    for prev, year in zip(years, years[1:]):
        # the next reference starts after the ". " that is followed by nothing but its author list (and a label
        # like "Dokumentation: "); the earliest such point after the previous year, so the whole list is taken
        start = None
        for m in re.finditer(r"\. ", text[prev.end():year.start()]):
            at = prev.end() + m.end()
            if _AUTHORS.fullmatch(text[at:year.start()]):
                start = at
                break
        if start is not None:
            starts.append(start)
    starts.append(len(text))
    return [text[a:b].strip() for a, b in zip(starts, starts[1:]) if text[a:b].strip()]


def _authors(text):
    text = re.sub(r"^[A-ZÄÖÜ][\w ]*: ", "", text.strip())  # "Dokumentation: ", "Englische Fassung: "
    parts = re.findall(rf"({_FAMILY}), ({_INITIALS})", text)
    return [(family, given) for family, given in parts]


def parse(reference):
    """One reference as a dict: kind (article | chapter | zis | preprint | unpublished | misc), authors [(family,
    given)], year, title, and per kind journal/volume/issue/pages, book/series/pages/publisher, publisher, eprint;
    doi and url where written in it; text: the reference as given."""
    entry = {"text": reference, "kind": "misc", "authors": [], "year": None, "title": ""}
    rest = reference
    doi = _DOI_URL.search(rest)
    if doi:
        entry["doi"] = doi.group(1)
        rest = (rest[:doi.start()] + rest[doi.end():]).strip()
    url = _URL.search(rest)
    if url:
        entry["url"] = url.group(1)
        rest = (rest[:url.start()] + rest[url.end():]).strip()
    year = _YEAR.search(" " + rest)
    if not year:
        return entry
    head, body = (" " + rest)[:year.start()], (" " + rest)[year.end():]
    authors = _authors(head)
    if head.strip().rstrip(".") in CORPORATE:  # an organisation as author: a name without a given name
        authors = [(head.strip().rstrip("."), "")]
    elif not authors or not _AUTHORS.fullmatch(head.strip()):
        return entry
    entry["authors"], entry["year"] = authors, int(year.group(1))
    m = _title_end(body)
    title, source = (body[:m], body[m:].strip()) if m else (body, "")
    entry["title"] = title.rstrip(".").strip()
    source = source.rstrip(" .")
    if not source:
        entry["kind"] = "unpublished"
    elif source == ZIS:
        entry.update(kind="zis", publisher=ZIS)
    elif _ARXIV.match(source):
        entry.update(kind="preprint", eprint=_ARXIV.match(source).group("id"))
    elif _CHAPTER.match(source):
        entry.update(kind="chapter", **{k: v for k, v in _CHAPTER.match(source).groupdict().items() if v})
    elif _ARTICLE.match(source):
        entry.update(kind="article", **{k: v for k, v in _ARTICLE.match(source).groupdict().items() if v})
    elif _PUBLISHER.fullmatch(source):  # a report or book: only the publisher after the title
        entry.update(kind="report", publisher=source)
    else:
        entry["note"] = source
    return entry


def entries(construct):
    """The references of a construct, with its DOI placed (see the module text)."""
    out = [parse(r) for r in split(construct.citation)]
    doi = (construct.doi or "").strip()
    if doi and not any(e.get("doi") == doi for e in out):
        zis = doi.lower().startswith("10.6102/zis")
        target = next((e for e in out if not e.get("doi") and (e["kind"] == "zis") == zis), None)
        if target is None:
            target = next((e for e in out if not e.get("doi")), None)
        if target is not None:
            target["doi"] = doi
        elif not out:
            out.append({"text": doi, "kind": "misc", "authors": [], "year": None, "title": "", "doi": doi})
    return out


def collect(constructs):
    """The references of several constructs, each once (the same DOI or the same text), in the order of the
    constructs."""
    seen, out = set(), []
    for c in constructs:
        for e in entries(c):
            key = (e.get("doi") or "").lower() or e["text"]
            if key not in seen:
                seen.add(key)
                out.append(e)
    return out


# --- BibTeX ---------------------------------------------------------------------------------------------------------

def _ascii(text):
    return re.sub(r"[^A-Za-z0-9]", "", unicodedata.normalize("NFKD", text.replace("ß", "ss")).encode("ascii", "ignore").decode())


def _keys(items):
    """Citation keys like Rammstedt2007, with a, b, ... where two would be the same."""
    base = [(_ascii(e["authors"][0][0]) if e["authors"] else "ref") + str(e["year"] or "") for e in items]
    out = []
    for n, b in enumerate(base):
        if base.count(b) > 1:
            b += "abcdefghijklmnopqrstuvwxyz"[base[:n].count(b) % 26]
        out.append(b)
    return out


def _bib(value):
    """A field value in braces: braces of its own and the characters LaTeX reads as commands are escaped."""
    value = str(value).replace("\\", "\\textbackslash{}")
    value = re.sub(r"([{}])", r"\\\1", value)
    return re.sub(r"([&%$#_])", r"\\\1", value)


def _bib_name(family, given):
    """One name of an author field, escaped like every field. Without a given name it is an organisation ("literal" in
    CSL, e.g. "Research and Development Corporation"): in braces of its own, so BibTeX neither splits it at "and" nor
    reads its last word as the family name."""
    family, given = str(family or "").strip(), str(given or "").strip()
    if not given:
        return "{" + _bib(family) + "}"
    return f"{_bib(family)}, {_bib(given)}"


def bibtex(items):
    """BibTeX in UTF-8 (biber/biblatex or BibTeX with inputenc). Titles in double braces: they stay as written."""
    types = {"article": "article", "chapter": "incollection", "zis": "misc", "preprint": "misc",
             "unpublished": "unpublished", "misc": "misc", "report": "techreport"}
    lines = ["% Literatur der Konstrukte, aus Askurious exportiert (UTF-8)", ""]
    for key, e in zip(_keys(items), items):
        fields = []
        if e["authors"]:
            fields.append(("author", " and ".join(_bib_name(f, g) for f, g in e["authors"])))
        if e["title"]:
            fields.append(("title", "{" + _bib(e["title"]) + "}"))
        if e["kind"] == "article":
            fields += [("journal", _bib(e["journal"]))]
            fields += [(k, _bib(e[k])) for k in ("volume",) if e.get(k)]
            fields += [("number", _bib(e["issue"]))] if e.get("issue") else []
        elif e["kind"] == "chapter":
            fields.append(("booktitle", _bib(e["book"])))
            fields += [("series", _bib(e["series"]))] if e.get("series") else []
            fields += [("publisher", _bib(e["publisher"]))] if e.get("publisher") else []
        elif e["kind"] == "zis":
            fields.append(("publisher", _bib(ZIS)))
            fields.append(("howpublished", _bib(ZIS)))
        elif e["kind"] == "preprint":
            fields += [("eprint", _bib(e["eprint"])), ("archiveprefix", "arXiv"), ("eprinttype", "arxiv")]
        elif e["kind"] == "report":
            fields.append(("institution", _bib(e["publisher"])))
        if e.get("pages"):
            fields.append(("pages", _bib(e["pages"].replace("–", "--"))))
        if e["year"]:
            fields.append(("year", str(e["year"])))
        if e.get("doi"):
            fields.append(("doi", e["doi"]))  # verbatim in biblatex: no escaping
        if e.get("url"):
            fields.append(("url", e["url"]))
        if e["kind"] == "unpublished":
            fields.append(("note", "Unveröffentlicht"))
        if e["kind"] == "misc" and not e["year"]:
            fields.append(("note", _bib(e["text"])))
        elif e.get("note"):
            fields.append(("note", _bib(e["note"])))
        lines.append(f"@{types[e['kind']]}{{{key},")
        lines += [f"  {name} = {{{value}}}," for name, value in fields]
        lines += ["}", ""]
    return "\n".join(lines)


# --- RIS ------------------------------------------------------------------------------------------------------------

def ris(items):
    """RIS (Zotero, Citavi, EndNote, Mendeley): one record per reference, lines "TAG  - value", CRLF."""
    types = {"article": "JOUR", "chapter": "CHAP", "zis": "DATA", "preprint": "UNPB", "unpublished": "UNPB",
             "misc": "GEN", "report": "RPRT"}
    lines = []
    for e in items:
        lines.append(f"TY  - {types[e['kind']]}")
        lines += [f"AU  - {f}, {g}" if g else f"AU  - {f}" for f, g in e["authors"]]  # an organisation: its name
        if e["year"]:
            lines.append(f"PY  - {e['year']}")
        lines.append(f"TI  - {e['title'] or e['text']}")
        if e["kind"] == "article":
            lines.append(f"T2  - {e['journal']}")
            lines += [f"VL  - {e['volume']}"] + ([f"IS  - {e['issue']}"] if e.get("issue") else [])
        elif e["kind"] == "chapter":
            lines.append(f"T2  - {e['book']}")
            if e.get("series"):
                lines.append(f"T3  - {e['series']}")
            if e.get("publisher"):
                lines.append(f"PB  - {e['publisher']}")
        elif e["kind"] == "zis":
            lines.append(f"PB  - {ZIS}")
        elif e["kind"] == "preprint":
            lines += ["PB  - arXiv", f"M1  - arXiv:{e['eprint']}"]
        elif e["kind"] == "report":
            lines.append(f"PB  - {e['publisher']}")
        if e.get("pages"):
            first, _, last = e["pages"].replace("–", "-").partition("-")
            lines.append(f"SP  - {first}")
            if last:
                lines.append(f"EP  - {last}")
        if e.get("doi"):
            lines.append(f"DO  - {e['doi']}")
        if e.get("url"):
            lines.append(f"UR  - {e['url']}")
        if e.get("note") or (e["kind"] == "misc" and not e["year"]):
            lines.append(f"N1  - {e.get('note') or e['text']}")
        lines.append("ER  - ")
        lines.append("")
    return "\r\n".join(lines)
