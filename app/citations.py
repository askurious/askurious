"""Publications that used a toolbox construct (concept "Konstrukte", stage 3; decision K8).

Researchers enter the DOI of an article or data set for their survey (page Export, section "Veröffentlichungen");
it then counts for every toolbox construct the survey uses. The operator enters DOIs at a construct (Administration
> Konstrukte). Title, authors, year and journal come once from doi.org (CSL JSON by content negotiation); without an
answer the DOI alone is kept. Data sets the surveys published on Zenodo through Askurious count as well, without an
entry. A survey is never named on the construct's page: only the DOI it published (licence.md section 4: studies
are named only with consent; entering the DOI is that consent).

The construct's page lists them ("Verwendet in", BibTeX and RIS) and names the toolbox constructs most often used
together with it, counted over the surveys.
"""
import json
import re
import time
from urllib.parse import urljoin, urlsplit

import requests

from app import bounded, constructs
from app.extensions import db
from app.models import Construct, ConstructCitation, Item
from app.lang import de_en

DOI_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)\Z", re.I)
TIMEOUT = 8
# doi.org answers a request for CSL JSON with a redirect to the registration agency: followed by hand, only there
AGENCIES = {"api.crossref.org", "data.crossref.org", "api.datacite.org"}
MAX_HOPS = 5
MAX_ANSWER = 1024 * 1024  # bytes of the answer; a record has a few kilobytes
WHOLE_SECONDS = 20  # all hops together (app/bounded.py; audit 2026-10-13, IN-7)


def doi_of(value):
    match = DOI_RE.match((value or "").strip())
    return match.group(1) if match else None


def fetch(doi):
    """{"title", "authors": [[family, given]], "year", "journal", "kind"} from doi.org, or {} without an answer."""
    try:
        data = _get_json(f"https://doi.org/{doi}")
    except (requests.RequestException, ValueError, RecursionError):
        return {}
    if not isinstance(data, dict):
        return {}
    title = data.get("title")
    title = title[0] if isinstance(title, list) and title else title if isinstance(title, str) else ""
    journal = data.get("container-title")
    journal = journal[0] if isinstance(journal, list) and journal else journal if isinstance(journal, str) else ""
    authors = [[str(a.get("family") or a.get("literal") or "")[:100], str(a.get("given") or "")[:100]]
               for a in (data.get("author") or [])[:50] if isinstance(a, dict)]
    year = None
    for key in ("issued", "published", "published-print", "published-online", "created"):
        parts = ((data.get(key) or {}).get("date-parts") or [[None]])[0]
        if parts and isinstance(parts[0], int):
            year = parts[0]
            break
    kind = "dataset" if data.get("type") == "dataset" else "article"
    return {"title": str(title)[:500], "authors": authors, "year": year, "journal": str(journal)[:300], "kind": kind,
            "volume": str(data.get("volume") or "")[:20], "issue": str(data.get("issue") or "")[:20],
            "pages": str(data.get("page") or "")[:40]}


def _get_json(url):
    """The JSON doi.org gives for a DOI: redirects followed only to the agencies (AGENCIES, https), each answer read up
    to MAX_ANSWER and all hops together within WHOLE_SECONDS (app/bounded.py). ValueError or a RequestException for
    anything else."""
    deadline = time.monotonic() + WHOLE_SECONDS
    for _ in range(MAX_HOPS + 1):
        left = deadline - time.monotonic()
        if left <= 0:
            raise bounded.TooSlow("the whole lookup took too long")
        r = bounded.request("GET", url, max_bytes=MAX_ANSWER, seconds=left, via=requests.get, timeout=min(TIMEOUT, left),
                            headers={"Accept": "application/vnd.citationstyles.csl+json"}, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get("Location") or "")
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname not in AGENCIES or parts.port not in (None, 443):
                raise ValueError("redirect elsewhere")
            continue
        r.raise_for_status()
        return json.loads((r.content or b"").decode("utf-8"))
    raise ValueError("too many redirects")


def survey_keys(survey):
    """The toolbox constructs in a survey (keys, in the order of the questionnaire)."""
    return list(dict.fromkeys(k for b in survey.blocks for i in b.items if (k := constructs.toolbox_key(i))))


def for_survey(survey):
    """[(doi, meta)] the researchers entered for this survey, each once."""
    rows = db.session.scalars(db.select(ConstructCitation).filter_by(survey_id=survey.id).order_by(ConstructCitation.id))
    seen = {}
    for row in rows:
        seen.setdefault(row.doi, row.meta or {})
    return list(seen.items())


def add_for_survey(survey, doi, user):
    """The publication counts for every toolbox construct in the survey. Returns the number of constructs."""
    keys = survey_keys(survey)
    if not keys:
        raise ValueError(de_en("Die Befragung enthält kein validiertes Konstrukt.",
                               "The survey contains no validated construct."))
    meta = fetch(doi)
    found = list(db.session.scalars(db.select(Construct).filter(Construct.key.in_(keys))))
    for c in found:
        if not db.session.scalar(db.select(ConstructCitation.id).filter_by(construct_id=c.id, doi=doi)):
            db.session.add(ConstructCitation(construct_id=c.id, doi=doi, meta=meta, survey_id=survey.id, added_by_id=user.id))
    return len(found)


def remove_for_survey(survey, doi):
    db.session.execute(db.delete(ConstructCitation).where(ConstructCitation.survey_id == survey.id,
                                                          ConstructCitation.doi == doi))


def add_for_construct(c, doi, user):
    if db.session.scalar(db.select(ConstructCitation.id).filter_by(construct_id=c.id, doi=doi)):
        raise ValueError(de_en("Diese DOI steht schon beim Konstrukt.", "This DOI is already listed at the construct."))
    db.session.add(ConstructCitation(construct_id=c.id, doi=doi, meta=fetch(doi), added_by_id=user.id))


def remove_for_construct(c, doi):
    db.session.execute(db.delete(ConstructCitation).where(ConstructCitation.construct_id == c.id, ConstructCitation.doi == doi))


def _zenodo(c, uses=None):
    """Data sets published on Zenodo from surveys that use the construct: [(doi, meta)]. uses: constructs.usage, if
    read already."""
    out = {}
    for _, survey in (constructs.usage(c.key) if uses is None else uses):
        z = survey.zenodo or {}
        if z.get("state") == "published" and z.get("doi") and z.get("target") == "zenodo":
            out.setdefault(z["doi"], {"kind": "dataset", "title": "", "authors": [], "year": None, "journal": "Zenodo"})
    return list(out.items())


def listing(c, uses=None):
    """[{"doi", "meta", "from_survey", "zenodo"}] of the construct, newest year first; Zenodo data sets included.
    uses: constructs.usage, if read already (the construct page reads it once, code review 2026-10-07, I14)."""
    rows = list(db.session.scalars(db.select(ConstructCitation).filter_by(construct_id=c.id)))
    out = {row.doi: {"doi": row.doi, "meta": row.meta or {}, "from_survey": row.survey_id is not None, "zenodo": False}
           for row in rows}
    for doi, meta in _zenodo(c, uses):
        out.setdefault(doi, {"doi": doi, "meta": meta, "from_survey": True, "zenodo": True})
    return sorted(out.values(), key=lambda e: (-(e["meta"].get("year") or 0), e["doi"]))


def counts():
    """{construct key: number of publications} for the toolbox list (Zenodo data sets not counted there)."""
    rows = db.session.execute(db.select(Construct.key, db.func.count(ConstructCitation.id))
                              .join(ConstructCitation, ConstructCitation.construct_id == Construct.id)
                              .group_by(Construct.key))
    return dict(rows.all())


def entries(c):
    """The listing as entries for app/bibliography.py (BibTeX, RIS)."""
    out = []
    for e in listing(c):
        m = e["meta"]
        title = m.get("title") or ""
        out.append({"text": title or f"https://doi.org/{e['doi']}", "kind": "article" if m.get("kind") == "article" and m.get("journal") else "misc",
                    "authors": [tuple(a) for a in m.get("authors") or []], "year": m.get("year"), "title": title,
                    "journal": m.get("journal") or "", "volume": m.get("volume") or "", "issue": m.get("issue") or "",
                    "pages": m.get("pages") or "", "doi": e["doi"]})
    return out


def together(c, limit=5, uses=None):
    """[(construct, surveys)] of the toolbox constructs used most often in the same surveys as `c`. uses:
    constructs.usage, if read already."""
    from app.models import Block

    mine = {s.id for _, s in (constructs.usage(c.key) if uses is None else uses)}
    if not mine:
        return []
    counts_by_key = {}
    # the survey of each element read with it, not its block one by one (I14)
    query = db.select(Item, Block.survey_id).join(Item.block).filter(db.cast(Item.config, db.Text).like('%"construct"%'))
    for item, survey_id in db.session.execute(query):
        key = constructs.toolbox_key(item)
        if key and key != c.key and survey_id in mine:
            counts_by_key.setdefault(key, set()).add(survey_id)
    found = {x.key: x for x in db.session.scalars(constructs.visible().filter(Construct.key.in_(list(counts_by_key))))} if counts_by_key else {}
    ranked = sorted(((found[k], len(v)) for k, v in counts_by_key.items() if k in found), key=lambda p: (-p[1], p[0].name.lower()))
    return ranked[:limit]
