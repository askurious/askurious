"""Places of an organisation and requests for more (Philipp 2026-11-13).

The places are the accounts that may have their home in an organisation (Organisation.max_accounts, None: no limit;
orgs.accounts_left). Invitations by link end when they are taken. Under Verwaltung > Personen the admins of the
organisation see how many are taken and ask for more with a short reason; the site admins hear of it in Slack
(notify.seat_request) and, on the organisation's page under Administration, grant it with a number of their own
(preset: twice as many, -1 for no limit) or refuse it. One open request per organisation at a time.
"""
from app import orgs
from app.extensions import db
from app.models import OrgSeatRequest, utcnow

REASON_LIMIT = 1000  # characters of the reason
UNLIMITED = -1  # what a site admin types for "no limit"
SHOWN_DAYS = 30  # the outcome of a request stays on the organisation's page this long


def seats(org):
    """{"max": the places or None, "taken": accounts at home there, "left": places free or None}."""
    taken = orgs.counts(org)["homes"]
    return {"max": org.max_accounts or None, "taken": taken, "left": orgs.accounts_left(org)}


def open_request(org):
    return db.session.scalar(db.select(OrgSeatRequest).where(OrgSeatRequest.org_id == org.id,
                                                             OrgSeatRequest.status == "open")
                             .order_by(OrgSeatRequest.id.desc()).limit(1))


def last_decided(org):
    """The latest decided request of the last SHOWN_DAYS days, for the admins of the organisation, or None."""
    from datetime import timedelta

    return db.session.scalar(db.select(OrgSeatRequest).where(
        OrgSeatRequest.org_id == org.id, OrgSeatRequest.status != "open",
        OrgSeatRequest.decided_at > utcnow() - timedelta(days=SHOWN_DAYS)).order_by(OrgSeatRequest.id.desc()).limit(1))


def all_open():
    """Open requests on the platform, oldest first, as (request, organisation)."""
    rows = db.session.scalars(db.select(OrgSeatRequest).where(OrgSeatRequest.status == "open")
                              .order_by(OrgSeatRequest.id))
    pairs = ((r, orgs.get(r.org_id)) for r in rows)
    return [(r, org) for r, org in pairs if org is not None]


def suggested(org):
    """The preset of the number a site admin grants: twice the places now, or twice the accounts at home when there
    is no limit (at least 10)."""
    now = org.max_accounts or orgs.counts(org)["homes"]
    return max(2 * (now or 0), 10)


def ask(org, by, reason):
    """Make the request; returns (request, problem). A second one while one is open is refused."""
    from app.lang import de_en

    reason = (reason or "").strip()
    if not reason:
        return None, de_en("Bitte begründen Sie kurz, wofür Sie mehr Plätze brauchen.",
                           "Please say briefly what you need more places for.")
    if not org.max_accounts:
        return None, de_en("Die Organisation hat keine Grenze für Konten.", "The organisation has no limit on accounts.")
    if open_request(org) is not None:
        return None, de_en("Es liegt schon ein Antrag vor.", "There is already a request.")
    row = OrgSeatRequest(org_id=org.id, by_id=by.id, reason=reason[:REASON_LIMIT], seats_before=org.max_accounts,
                         status="open")
    db.session.add(row)
    db.session.flush()
    return row, None


def parse_number(text):
    """The number a site admin typed: a whole number of at least 1, or -1 for no limit (returned as UNLIMITED);
    None when it is neither."""
    try:
        n = int(str(text).strip())
    except (TypeError, ValueError):
        return None
    return n if n == UNLIMITED or n >= 1 else None


def grant(row, org, by, number):
    """Grant `row` with `number` places (UNLIMITED: no limit) and set it on the organisation."""
    org.max_accounts = None if number == UNLIMITED else number
    row.status, row.seats_after, row.decided_by_id, row.decided_at = "granted", org.max_accounts, by.id, utcnow()


def refuse(row, by):
    row.status, row.decided_by_id, row.decided_at = "refused", by.id, utcnow()


def delete_of(org):
    """The requests go with the organisation (orgs.delete)."""
    db.session.execute(db.delete(OrgSeatRequest).where(OrgSeatRequest.org_id == org.id))
