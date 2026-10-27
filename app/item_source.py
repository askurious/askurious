"""Where the items of a scale come from (user test 2026-10-27, point 7: "Nichts ist schlimmer als sich später zu
fragen, wo die Items herkommen").

Likert matrices, semantic differentials and sliders have a field "Quelle der Items" beside their statements:
an APA reference, or a DOI that Askurious turns into one through doi.org (app/citations.py), or the tick
"selbst formuliert". Stored in the item's config as {"source": {"text": ..., "doi": ..., "own": bool}}. A construct
from the toolbox brings its source (app/constructs.py) and needs nothing here. A scale without either gets a
warning in the pre-launch check (app/design.py), never an error, so that a pretest is not blocked.

The methods draft and the README of the data export name the source next to the construct citations."""
import re

from app.lang import de_en

SCALE_TYPES = ("likert", "semantic", "slider")
MAX_TEXT = 600


def applies(item):
    return item.type in SCALE_TYPES


def stored(item):
    raw = (item.config or {}).get("source")
    return raw if isinstance(raw, dict) else {}


def construct_citation(item):
    from app import constructs

    info = constructs.info(item)
    return ((info or {}).get("citation") or "").strip() if info else None


def text(item):
    """The source to cite: the construct's, else the one typed in, else "" (also for "selbst formuliert")."""
    if not applies(item):
        return ""
    cited = construct_citation(item)
    if cited:
        return cited
    return (stored(item).get("text") or "").strip()


def own(item):
    return bool(stored(item).get("own"))


def missing(item):
    """True for a scale whose source nobody gave: not from the toolbox, no source, not ticked as self-written."""
    if not applies(item) or construct_citation(item) is not None:
        return False
    if not item.kind.collects_answer:
        return False
    return not (stored(item).get("text") or "").strip() and not own(item)


def _apa(meta, doi):
    """A reference in APA style from the record doi.org gave (app/citations.fetch)."""
    authors = meta.get("authors") or []

    def name(a):
        family, given = (a + ["", ""])[:2]
        initials = " ".join(p[0] + "." for p in re.split(r"[\s-]+", given) if p)
        return f"{family}, {initials}".strip().rstrip(",")
    names = [name(a) for a in authors if a and a[0]]
    who = (", ".join(names[:-1]) + ", & " + names[-1]) if len(names) > 1 else (names[0] if names else "")
    year = f"({meta['year']})" if meta.get("year") else "(o. J.)"
    title = (meta.get("title") or "").strip().rstrip(".")
    journal = (meta.get("journal") or "").strip()
    vol = meta.get("volume") or ""
    issue = f"({meta['issue']})" if meta.get("issue") else ""
    pages = meta.get("pages") or ""
    tail = ", ".join(p for p in [journal, (vol + issue) if vol else "", pages] if p)
    parts = [p for p in [f"{who} {year}." if who else f"{year}.", f"{title}." if title else "", f"{tail}." if tail else "",
                         f"https://doi.org/{doi}"] if p]
    return " ".join(parts)


def from_form(item, form):
    """Store what the editor sent (fields item_source, item_source_own). A bare DOI is looked up once."""
    if not applies(item) or "item_source_shown" not in form:
        return
    from app import citations

    typed = " ".join((form.get("item_source") or "").split())[:MAX_TEXT]
    before = stored(item)
    entry = {"text": typed, "own": bool(form.get("item_source_own"))}
    doi = citations.doi_of(typed)
    if doi:
        if before.get("doi") == doi and before.get("text"):
            entry.update(text=before["text"], doi=doi)  # looked up already
        else:
            meta = citations.fetch(doi)
            entry.update(text=_apa(meta, doi) if meta.get("title") else f"https://doi.org/{doi}", doi=doi)
    else:
        found = re.search(r"10\.\d{4,9}/\S+", typed)
        if found:
            entry["doi"] = found.group(0).rstrip(".,;)")
    config = dict(item.config or {})
    if entry["text"] or entry["own"]:
        config["source"] = entry
    else:
        config.pop("source", None)
    item.config = config

