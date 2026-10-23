"""Lookups against the public ORCID registry (no auth needed)."""
import requests
from flask import current_app

from app import bounded

PUB = "https://pub.orcid.org/v3.0"
WHOLE_SECONDS = 15  # the whole call (app/bounded.py; audit 2026-10-13, IN-7)
MAX_ANSWER = 5 * 1024 * 1024  # bytes; a record or a page of search results has some kilobytes


def _get(path, params=None):
    try:
        r = bounded.request("GET", f"{PUB}{path}", max_bytes=MAX_ANSWER, seconds=WHOLE_SECONDS, via=requests.get,
                            params=params, headers={"Accept": "application/json"}, timeout=8)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        current_app.logger.warning("ORCID lookup failed: %s", e)
        return None


def lookup(orcid):
    """Return {orcid, name} for an iD, or None if it does not exist."""
    data = _get(f"/{orcid}/person")
    if not data or not data.get("name"):
        return None
    n = data["name"]
    given = (n.get("given-names") or {}).get("value", "")
    family = (n.get("family-name") or {}).get("value", "")
    credit = (n.get("credit-name") or {}).get("value", "")
    return {"orcid": orcid, "name": (credit or f"{given} {family}".strip())[:200], "affiliation": ""}  # name: String(200)


def search(query, rows=10):
    """Search the registry by name. Returns [{orcid, name, affiliation}]."""
    parts = query.split()
    if not parts:
        return []
    if len(parts) == 1:
        q = f"family-name:{parts[0]}* OR given-names:{parts[0]}*"
    else:
        q = f"given-names:{' '.join(parts[:-1])} AND family-name:{parts[-1]}*"
    data = _get("/expanded-search/", {"q": q, "rows": rows})
    results = []
    for r in (data or {}).get("expanded-result") or []:
        name = " ".join(x for x in [r.get("given-names"), r.get("family-names")] if x)
        insts = r.get("institution-name") or []
        results.append({"orcid": r.get("orcid-id"), "name": name[:200], "affiliation": ""})
    return results


