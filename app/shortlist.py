"""The "Schnellbox" of the questionnaire editor (konzept-konstrukt-schnellbox-2026-10-16.md, Ralf: as recommended).

The editor's construct group lists the researcher's own stars and then the constructs their organisation recommends
(K1, K2). An organisation's admins keep its list under Verwaltung > Konstrukte (K9); the site admins keep the
platform's list under Administration > Konstrukte, and it holds for every organisation without a list of its own.
What counts is the organisation of the survey being edited (K12). Everything else stays reachable in the full box,
for everyone (K11). A list is a few constructs by key (table construct_shortlist), in the order its keepers give.
"""
from app.extensions import db
from app.models import ConstructShortlist

MAX = 12  # entries of one list: a quick box, not a second toolbox


def _rows(org_id):
    col = ConstructShortlist.org_id
    stmt = db.select(ConstructShortlist).filter(col.is_(None) if org_id is None else col == org_id)
    return list(db.session.scalars(stmt.order_by(ConstructShortlist.position, ConstructShortlist.id)))


def keys(org_id):
    """The keys of one list as kept (org_id None: the platform's), constructs no longer in the toolbox left out."""
    from app import constructs

    return [r.key for r in _rows(org_id) if constructs.get(r.key) is not None]


def for_org(org_id):
    """(keys, label) of the list that holds in organisation `org_id`: its own, else the platform's. The label names who
    recommends it: the organisation's short name, or the platform's name."""
    from app import brand, orgs

    own = keys(org_id) if org_id is not None else []
    if own:
        return own, orgs.label(orgs.get(org_id))
    return keys(None), brand.name()


def add(org_id, key):
    """Put construct `key` at the end of the list; False if it is there already, unknown or the list is full."""
    from app import constructs

    rows = _rows(org_id)
    if constructs.get(key) is None or any(r.key == key for r in rows) or len(rows) >= MAX:
        return False
    db.session.add(ConstructShortlist(org_id=org_id, key=key, position=len(rows)))
    return True


def remove(org_id, key):
    rows = _rows(org_id)
    for r in rows:
        if r.key == key:
            db.session.delete(r)
    _renumber([r for r in rows if r.key != key])


def order(org_id, new_keys):
    """Put the list in the order of `new_keys` (keys not named keep their place after them)."""
    rows = _rows(org_id)
    rank = {k: n for n, k in enumerate(new_keys)}
    rows.sort(key=lambda r: (rank.get(r.key, len(rank)), r.position))
    _renumber(rows)


def _renumber(rows):
    for n, r in enumerate(rows):
        r.position = n


def keeper_of(user, org_id):
    """May `user` keep the list of `org_id` (None: the platform's)? Site admins both, organisation admins their own."""
    from app import orgs

    if user is None or not user.is_authenticated:
        return False
    if org_id is None:
        return bool(user.is_admin)
    return bool(user.is_admin) or orgs.is_admin_of(user, org_id)
