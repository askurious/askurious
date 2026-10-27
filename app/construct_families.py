"""Short and long versions of one instrument, linked (user test 2026-10-27, point 13: "Kurzskalen und Langskalen nicht
nur zwei Einträge, sondern miteinander verknüpft").

A construct belongs to a family (e.g. "Big Five") with a label of its own version (e.g. "BFI-10, 10 Items"). Site
admins set both on the construct's page under Administration > Konstrukte; they are kept as a site setting
("fam:<key>" = "<family>|<version>", app/models.SiteSetting), so no seed update of the construct overwrites them.
The list of constructs shows the other versions on each card, the construct's page compares them side by side."""
from app.extensions import db
from app.models import SiteSetting, utcnow

PREFIX = "fam:"
MAX_KEY = 40


def _key(construct_key):
    key = PREFIX + construct_key
    return key if len(key) <= MAX_KEY else None


def all_():
    """{construct key: (family, version)} of every construct with a family."""
    out = {}
    for row in db.session.scalars(db.select(SiteSetting).filter(SiteSetting.key.like(PREFIX + "%"))):
        family, _, version = (row.value or "").partition("|")
        if family.strip():
            out[row.key[len(PREFIX):]] = (family.strip(), version.strip())
    return out


def of(construct_key, known=None):
    known = all_() if known is None else known
    return known.get(construct_key)


def siblings(construct_key, known=None):
    """[(key, version)] of the other constructs of the same family, by version label."""
    known = all_() if known is None else known
    mine = known.get(construct_key)
    if not mine:
        return []
    return sorted(((k, v[1]) for k, v in known.items() if v[0] == mine[0] and k != construct_key), key=lambda kv: kv[1])


def put(construct_key, family, version, user=None):
    """Set (or with an empty family remove) the family of a construct. False if its key is too long to keep."""
    key = _key(construct_key)
    if key is None:
        return False
    family, version = " ".join((family or "").split())[:100], " ".join((version or "").split())[:95]
    row = db.session.get(SiteSetting, key)
    if not family:
        if row is not None:
            db.session.delete(row)
        return True
    if row is None:
        row = SiteSetting(key=key)
        db.session.add(row)
    row.value = f"{family}|{version}"
    row.changed_by_id = getattr(user, "id", None)
    row.changed_at = utcnow()
    return True
