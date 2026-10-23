"""Sources in APA style (Ralf, 2026-10-05): in the running text only a short reference such as "(Conrad et al., 2017)",
linked to its entry; at the end of the page one fold with the reference list, sorted by author, formatted after APA 7
(journal and volume, books and reports in italics, DOI as link).

The citations themselves stay plain strings next to the logic that uses them (quality.SOURCES, tempo.CONRAD, ...),
written in APA order: "Family, I. I., & Family, I. (Year). Title. Journal, 12(3), 1–20. https://doi.org/...". This
module reads them: `ref(source)` gives the parts, `cite(sources)` the short reference, `references(groups)` the list.
The rules are heuristics over that order; a string they cannot read is shown as it is, never dropped.
"""
import re
import unicodedata
from functools import lru_cache

from markupsafe import Markup, escape

_HEAD = re.compile(r"^(?P<authors>.+?)\.? \((?P<year>(?:1[5-9]|20)\d\d[a-z]?|o\. ?J\.|n\.d\.)(?:, [^)]*)?\)\. (?P<rest>.*)$", re.S)
_INITIALS = re.compile(r"^(?:[A-ZÀ-Þ][a-z]?\.(?:[ -](?=[A-ZÀ-Þ]))?)+$")
_LINK = re.compile(r"https?://\S+?(?=[.,;]?(?:\s|$))")
# Journal, 12(3), 45–61.  Journal, 3, 11.IT.3.1.  Journal, 1(1), Art. 22.  Journal, 8(1), 33267.
_JOURNAL = re.compile(r"^(?P<title>.+?[.?!…]) (?P<journal>[^.()]+?), (?P<vol>\d+)(?P<issue>\([^)]+\))?"
                      r"(?P<pages>, (?:Art\. |Article )?[^ ,]+?)?\.(?: (?P<after>.*))?$", re.S)
# Title. Proceedings of the 40th Conference, PMLR 202, 1–9.  (published proceedings: their name in italics)
_PROCEEDINGS = re.compile(r"^(?P<title>.+?[.?!…]) (?P<proc>Proceedings of [^,.]+)(?P<after>[,.].*)$", re.S)
# Title. In I. Editor (Ed.), Book title (2nd ed., pp. 1–20). Publisher.  /  Title. In Proceedings of X (pp. 1–9). IEEE.
_CHAPTER = re.compile(r"^(?P<title>.+?[.?!…]) In (?P<eds>.+?\((?:Hrsg|Eds?)\.\), )?(?P<book>.+?)"
                      r"(?P<paren> \([^()]*(?:pp?\.|S\.) [^()]+\))?\.(?: (?P<after>.*))?$", re.S)
# Title (2nd ed.). Publisher.  /  Title [Kursfolien]. Publisher.
_BOOK = re.compile(r"^(?P<title>.+?)(?P<paren> [(\[][^()\[\]]*[)\]])?\.(?: (?P<after>.*))?$", re.S)


def _families(authors):
    """The family names of "Family, I. I., Family, I., & Family, I.", or None for a group author."""
    parts = re.split(r",\s(?:&\s)?|\s&\s", authors.strip().rstrip("."))
    if len(parts) < 2 or len(parts) % 2:
        return None
    names, initials = parts[0::2], parts[1::2]
    if not all(_INITIALS.match(i.strip().rstrip(".") + ".") for i in initials):
        return None
    return [n.strip() for n in names]


def _plain(text):
    """For sorting: without accents, case-folded (APA sorts Ä as A)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def _link(url):
    return Markup('<a href="{0}" target="_blank" rel="noopener">{0}</a>').format(url)


def _tail(text):
    """Publisher, series, DOI or URL after the italic part; each address becomes a link."""
    out, at = Markup(""), 0
    for m in _LINK.finditer(text or ""):
        out += escape(text[at:m.start()]) + _link(m.group())
        at = m.end()
    return out + escape((text or "")[at:])


def _body(rest):
    """The part after "(Year). " in APA 7 with italics: journal and volume, or the title of a book, report or
    proceedings."""
    rest = rest.strip()
    m = _CHAPTER.match(rest)
    if m:
        return (escape(m["title"]) + Markup(" In ") + escape(m["eds"] or "") + Markup("<i>{}</i>").format(m["book"])
                + escape(m["paren"] or "") + "." + (" " + _tail(m["after"]) if m["after"] else ""))
    m = _JOURNAL.match(rest)
    if m:
        return (escape(m["title"]) + " " + Markup("<i>{}, {}</i>").format(m["journal"], m["vol"]) + escape(m["issue"] or "")
                + escape(m["pages"] or "") + "." + (" " + _tail(m["after"]) if m["after"] else ""))
    m = _PROCEEDINGS.match(rest)
    if m:
        return escape(m["title"]) + " " + Markup("<i>{}</i>").format(m["proc"]) + _tail(m["after"])
    if rest.startswith("http"):
        return _tail(rest)
    m = _BOOK.match(rest)
    if m:
        return (Markup("<i>{}</i>").format(m["title"]) + escape(m["paren"] or "") + "."
                + (" " + _tail(m["after"]) if m["after"] else ""))
    return _tail(rest)


_MONTHS = {"Januar": "January", "Februar": "February", "März": "March", "April": "April", "Mai": "May",
           "Juni": "June", "Juli": "July", "August": "August", "September": "September", "Oktober": "October",
           "November": "November", "Dezember": "December"}
_RETRIEVED = re.compile(r" (?P<url>https?://\S+) \(abgerufen am (?P<day>\d{1,2})\. (?P<month>\w+) (?P<year>\d{4})\)\.?$")


def _english(source):
    """A citation written with the German APA elements in their English form for an English page (UI/UX audit
    2026-10-07, W109): "o. J." becomes "n.d.", "(abgerufen am 3. Oktober 2026)" after the address "Retrieved
    October 3, 2026, from <address>", "(Hrsg.)" "(Eds.)" and "S." before pages "pp."; a label in front of a reference
    ("Dokumentation:", "Englische Fassung:") and a German legal source in their English form (X86)."""
    from app import retention

    if source == " ".join(retention.GDPR.split()):
        return retention.GDPR_EN
    source = re.sub(r"(^|\. )Dokumentation: ", r"\1Documentation: ", source)
    source = re.sub(r"(^|\. )Englische Fassung: ", r"\1English version: ", source)
    source = re.sub(r"\(o\. ?J\.\)", "(n.d.)", source)
    m = _RETRIEVED.search(source)
    if m and m["month"] in _MONTHS:
        source = (source[:m.start()] + f" Retrieved {_MONTHS[m['month']]} {m['day']}, {m['year']}, from {m['url']}")
    source = source.replace("(Hrsg.)", "(Eds.)")
    return re.sub(r"(\(|, )S\. (?=\d)", r"\1pp. ", source)


def ref(source):
    """{"key", "short", "narrative", "html", "sort"} of one full citation. short: "Conrad et al., 2017" for the
    parentheses, narrative: "Conrad et al. (2017)" for a sentence; key: the anchor of its entry (ref-<key>). On an
    English page with the German APA elements in English (n.d., Retrieved …, and)."""
    from app import lang

    return _ref(" ".join(str(source).split()), lang.current())


@lru_cache(maxsize=4096)
def _ref(source, language):
    if language == "en":
        source = _english(source)
    m = _HEAD.match(source)
    if not m:
        words = re.findall(r"\w+", _plain(source))
        return {"key": "-".join(words[:3]) or "quelle", "short": source[:40], "narrative": source[:40],
                "html": _tail(source), "sort": _plain(source), "source": source}
    authors, year = m["authors"].strip(), m["year"].replace("o.J.", "o. J.")
    families = _families(authors)
    if families is None:
        who = authors.rstrip(".")
        narrative_who = who
    elif len(families) == 1:
        who = narrative_who = families[0]
    elif len(families) == 2:
        who = f"{families[0]} & {families[1]}"
        narrative_who = f"{families[0]} {'and' if language == 'en' else 'und'} {families[1]}"
    else:
        who = narrative_who = f"{families[0]} et al."
    first_word = (re.findall(r"\w+", _plain(m["rest"])) or ["x"])[0]
    key = re.sub(r"[^a-z0-9]+", "", _plain(families[0] if families else who)) + re.sub(r"\W", "", year) + "-" + first_word
    head = escape(authors if authors.endswith(".") else authors + ".") + f" ({year}). "
    return {"key": key, "short": f"{who}, {year}", "narrative": f"{narrative_who} ({year})",
            "html": Markup(head) + _body(m["rest"]), "sort": _plain(f"{authors} {year} {m['rest']}"), "source": source}


def _flat(sources):
    """Citations from a string, a list of them, or groups [(heading, [citations])]."""
    if isinstance(sources, str):
        return [sources]
    out = []
    for s in sources or []:
        if isinstance(s, (tuple, list)) and len(s) == 2 and isinstance(s[0], str) and not isinstance(s[1], str):
            out.extend(_flat(s[1]))
        elif s:
            out.extend(_flat(s))
    return out


def cite(sources, narrative=False, link=True):
    """The short reference of one or more citations: "(Conrad et al., 2017; Greszki et al., 2014)", each linked to
    its entry in the list at the end of the page; narrative: "Conrad et al. (2017)" for a sentence."""
    refs, seen = [], set()
    for s in _flat(sources):
        r = ref(s)
        if r["key"] not in seen:
            seen.add(r["key"])
            refs.append(r)
    if not refs:
        return Markup("")
    refs.sort(key=lambda r: r["sort"])

    def one(r, text):
        if not link:
            return escape(text)
        return Markup('<a class="cite" href="#ref-{}">{}</a>').format(r["key"], text)

    if narrative:
        return Markup(", ").join(one(r, r["narrative"]) for r in refs)
    return Markup('<span class="cite-group">(') + Markup("; ").join(one(r, r["short"]) for r in refs) + Markup(")</span>")


def references(sources):
    """The reference list of a page: each citation once (the fuller one when a work stands twice), sorted by author
    and year. sources: a list of citations or groups [(heading, [citations])]; for groups each entry also carries
    what it is cited for ("uses", in the order of the page)."""
    groups = [(None, [s]) if isinstance(s, str) else s for s in (sources or [])]
    entries = {}
    for heading, cited in groups:
        for s in _flat(cited):
            r = ref(s)
            e = entries.get(r["key"])
            if e is None:
                e = entries[r["key"]] = {**r, "uses": []}
            elif len(r["source"]) > len(e["source"]):  # the same work twice: keep the one with more (a DOI)
                e.update({k: v for k, v in r.items() if k != "key"})
            if heading and heading not in e["uses"]:
                e["uses"].append(heading)
    return sorted(entries.values(), key=lambda e: e["sort"])
