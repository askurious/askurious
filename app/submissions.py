"""Linked elements submitted for the construct toolbox (concept "Konstrukte", stage 2; decisions K4 to K9).

Whoever made a linked element (app/linked.py) submits it with what a toolbox construct needs: a description, the
source with its DOI, how it is scored, which statements are reverse coded, and the confirmation that the wording
may be used and shared. The operator sees the open submissions in Administration > Konstrukte and asks back,
rejects with a reason, or accepts. Accepting makes a new toolbox construct as a copy of the element's template
(status "review" until the wording is checked against the original; then "verified"); the element and its uses
stay linked elements and never enter the operator's pool (licence.md section 4 covers toolbox constructs only).

No mail: the operator gets a Slack message (app/notify.py); the submitter sees the latest decision on the start
page until they open the element (`seen`).
"""
import copy
import re

from app import brand
from app import linked
from app.extensions import db
from app.lang import de_en, english
from app.models import Construct, ConstructSubmission, utcnow

DOI_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)\Z", re.I)
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}\Z")
FIELDS = (("description", "Beschreibung des Konstrukts", 3000), ("citation", "Quelle", 2000),
          ("scoring", "Auswertung", 1000), ("reverse", "Umgepolte Aussagen", 500),
          # the terms the source sets for the wording; they become the construct's "Nutzung" (copyright audit
          # 2026-10-16, U14)
          ("licence", "Nutzungsbedingungen des Wortlauts", 1000))
FIELDS_EN = {"description": "Description of the construct", "citation": "Source", "scoring": "Scoring",
             "reverse": "Reverse-coded statements", "licence": "Terms of use of the wording"}
REQUIRED = ("description", "citation", "scoring", "licence")


def labels():
    """{field: label} of the submit form in the language of the page."""
    return {k: FIELDS_EN[k] if english() else label for k, label, _ in FIELDS}


def latest(c):
    """The newest submission of element `c`, or None."""
    return db.session.scalar(db.select(ConstructSubmission).filter_by(construct_id=c.id)
                             .order_by(ConstructSubmission.id.desc()).limit(1))


def open_ones():
    return list(db.session.scalars(db.select(ConstructSubmission).filter(ConstructSubmission.state.in_(ConstructSubmission.OPEN))
                                   .order_by(ConstructSubmission.created_at)))


def decided_ones(limit=20):
    return list(db.session.scalars(db.select(ConstructSubmission).filter(~ConstructSubmission.state.in_(ConstructSubmission.OPEN))
                                   .order_by(ConstructSubmission.decided_at.desc()).limit(limit)))


def unseen(user):
    """The submitter's decided or questioned submissions they have not looked at yet (start page)."""
    return list(db.session.scalars(db.select(ConstructSubmission).filter_by(user_id=user.id, seen=False)
                                   .order_by(ConstructSubmission.decided_at.desc())))


def read_form(form):
    """(fields, errors) of the submit form."""
    fields = {k: (form.get(k) or "").strip()[:limit] for k, _, limit in FIELDS}
    names = labels()
    errors = [names[k] for k, _, _ in FIELDS if k in REQUIRED and not fields[k]]
    match = DOI_RE.match((form.get("doi") or "").strip())
    if not match:
        errors.append(de_en("DOI (z. B. 10.1037/0022-3514.84.4.822)", "DOI (e.g. 10.1037/0022-3514.84.4.822)"))
    else:
        fields["doi"] = match.group(1)
    if form.get("rights") != "1":
        errors.append(de_en("Bestätigung der Nutzungsrechte", "confirmation of the rights of use"))
    return fields, errors


def submit(c, user, fields):
    """A new submission of element `c` (no other may be open). Returns it; the caller commits and notifies."""
    if not linked.may_change(user, c):
        raise PermissionError
    now = latest(c)
    if now is not None and now.state in ConstructSubmission.OPEN:
        raise ValueError(de_en("Das Element ist schon eingereicht.", "The element has already been submitted."))
    if now is not None and now.state == ConstructSubmission.ACCEPTED:
        raise ValueError(de_en("Das Element ist schon in „Konstrukte“ aufgenommen.",
                               "The element has already been added to “Constructs”."))
    s = ConstructSubmission(construct_id=c.id, user_id=user.id, fields=fields)
    db.session.add(s)
    return s


def answer(s, user, text):
    """The submitter answers the operator's question: back to "submitted"."""
    if s.state != ConstructSubmission.QUESTION or s.user_id != user.id:
        raise PermissionError
    s.fields = {**(s.fields or {}), "answer": text.strip()[:2000]}
    s.state = ConstructSubmission.SUBMITTED
    s.seen = True


def decide(s, state, note):
    """The operator asks back (QUESTION) or rejects (REJECTED) with a note."""
    if s.state not in ConstructSubmission.OPEN or state not in (ConstructSubmission.QUESTION, ConstructSubmission.REJECTED):
        raise ValueError(de_en("Diese Einreichung ist schon entschieden.", "This submission has already been decided."))
    note = (note or "").strip()[:2000]
    if not note:
        raise ValueError(de_en("Bitte eine Rückfrage oder Begründung schreiben.", "Please write a question or a reason."))
    s.state, s.note, s.seen, s.decided_at = state, note, False, utcnow()


def proposal(s):
    """What the accept form starts with: key, name, short, authors, year (from the first source)."""
    from app import bibliography

    c, f = s.construct, s.fields or {}
    first = next(iter(bibliography.split(f.get("citation", ""))), "")
    entry = bibliography.parse(first) if first else {}
    name = c.name.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    base = re.sub(r"[^a-z0-9_]+", "_", name).strip("_")[:30]
    base = base if len(base) > 1 and base[0].isalpha() else "k_" + base if base else "konstrukt"
    key, n = base, 2
    while db.session.scalar(db.select(Construct.id).filter_by(key=key)):
        key, n = f"{base}_{n}", n + 1
    surnames = [a[0] for a in entry.get("authors") or [] if a and a[0]]
    names = " & ".join(surnames) if len(surnames) <= 2 else ", ".join(surnames[:-1]) + " & " + surnames[-1]
    return {"key": key, "name": c.name, "short": "", "authors": names, "year": entry.get("year") or ""}


def accept(s, form):
    """Make the toolbox construct; returns it. Raises ValueError with what is missing."""
    from app import constructs

    if s.state not in ConstructSubmission.OPEN:
        raise ValueError(de_en("Diese Einreichung ist schon entschieden.", "This submission has already been decided."))
    key, name = (form.get("key") or "").strip(), (form.get("name") or "").strip()[:200]
    if not KEY_RE.match(key):
        raise ValueError(de_en("Schlüssel: Kleinbuchstaben, Ziffern und _, mit einem Buchstaben vorn (z. B. vertrauen_ki).",
                               "Key: lower-case letters, digits and _, starting with a letter (e.g. vertrauen_ki)."))
    if db.session.scalar(db.select(Construct.id).filter_by(key=key)):
        raise ValueError(de_en("Den Schlüssel „{key}“ gibt es schon.", "The key “{key}” already exists.", key=key))
    if not name:
        raise ValueError(de_en("Bitte einen Namen eingeben.", "Please enter a name."))
    year = (form.get("year") or "").strip()
    element, f = s.construct, s.fields or {}
    contributor = s.user.display_name if s.user else "Forschende"
    notes = (f"Nutzung: {f['licence']} " if f.get("licence") else "") + f"Eingereicht von {contributor} (verknüpftes Element, {element.key})."
    if f.get("reverse"):
        notes += f" Umgepolt: {f['reverse']}."
    c = Construct(key=key, name=name, short=(form.get("short") or "").strip()[:300], description=f.get("description", ""),
                  authors=(form.get("authors") or "").strip()[:300], year=int(year) if year.isdigit() and len(year) == 4 else None,
                  citation=f.get("citation", ""), doi=f.get("doi", ""), scoring=f.get("scoring", ""), notes=notes,
                  spec=copy.deepcopy(element.spec or {}), status="review", visibility="public",
                  created_by_id=s.user_id, seed_version=1)
    db.session.add(c)
    db.session.flush()
    c.search_text = constructs._search_text(c)
    s.state, s.toolbox_key, s.note, s.seen, s.decided_at = ConstructSubmission.ACCEPTED, key, (form.get("note") or "").strip()[:2000], False, utcnow()
    return c


def notify(s):
    """A Slack message to the operators about a new or answered submission."""
    from flask import url_for

    from app import notify as slack

    who = s.user.display_name if s.user else "Jemand"
    what = "beantwortet die Rückfrage zu" if (s.fields or {}).get("answer") else "reicht ein"
    # the configured address, not the host the request named (security audit 2026-10-06, H3)
    link = brand.public_base() + url_for("admin.submission", sid=s.id)
    return slack.slack(f":inbox_tray: *Konstrukt für „Konstrukte“*: {slack._plain(who)} {what} „{slack._plain(s.construct.name)}“ <{link}|prüfen>")
