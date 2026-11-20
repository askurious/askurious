"""Personal invitation links for several people at once (Ralf 2026-11-20, concept "persönliche Einladungslinks für
eine Befragung", E1-E9 as recommended).

Admins of a survey who may bring in new people (invites.invite_refusal: the right to create surveys, no account with
a period, the switches of the organisation) open "Mehrere Personen per Link einladen" under "Person hinzufügen": one
row per person with a name and, if known, an address (at most MAX_PEOPLE), one role for all ("Bearbeiten" or
"Ansehen", never "Admin": E2), how long the links hold (DAYS) and the day the access ends ("Zugang endet am", at most
MAX_MONTHS ahead: E9).

Every person gets an invitation of their own (MemberInvite with name and access_until): a link used once, whose token
is stored only as a hash, shown once on the next page with "Kopieren", "Per Mail senden" (mail on and an address) and
"Alle als CSV". It works also with mail off (MemberInvite.handed_out, as the lists of app/bulk_invites.py). Accepting
goes the way of every invitation (invites.accept): signed in, one click; else ORCID or a new account with an address
and a password. The address of the link is not checked (E5). The invitations count for the inviter's limit of
invitations within 24 hours (invites.PER_DAY), mailed or not.

The end (E9): the membership that came by such a link keeps access_until, and the hourly sweep (end_due) ends it then,
as an admin removing the person would. A new account that came in by such links alone gets the same day as the end of
its period (account_types.start_new → period_from_links): it is blocked then as every account whose period ended. An
account that existed already keeps its own period; only the membership ends.
"""
import csv
import io
import re
import secrets
from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.lang import de_en
from app.models import MemberInvite, SurveyMember, utcnow

bp = Blueprint("invite_links", __name__, url_prefix="/invite/links")

MAX_PEOPLE = 20
DAYS = (7, 14, 30)
DEFAULT_DAYS = 14
ROLES = ("editor", "viewer")  # never "admin" by a link (E2): who comes in this way adds nobody
MAX_MONTHS = 12
DEFAULT_MONTHS = 6
FIELD_ROWS = 3  # rows the dialog starts with; "+ Person" adds more up to MAX_PEOPLE


def _months_ahead(n, today=None):
    from app.retention import _add_months
    from app.timeutil import local_today

    return _add_months(today or local_today(), n)


def default_end():
    return _months_ahead(DEFAULT_MONTHS)


def latest_end():
    return _months_ahead(MAX_MONTHS)


def parse_end(text):
    """The day "Zugang endet am" names: (date, error)."""
    from app.timeutil import local_today

    try:
        day = date.fromisoformat((text or "").strip())
    except ValueError:
        return None, de_en("Bitte ein Datum für „Zugang endet am“ angeben.", "Please give a date for “Access ends on”.")
    if day <= local_today():
        return None, de_en("„Zugang endet am“ muss nach heute liegen.", "“Access ends on” must be after today.")
    if day > latest_end():
        return None, de_en("„Zugang endet am“ darf höchstens {n} Monate voraus liegen.",
                           "“Access ends on” may be at most {n} months ahead.", n=MAX_MONTHS)
    return day, None


def people_of(form):
    """[(name, email or "")] of the rows, and the problems: a row with an address and no name, an invalid address,
    an address twice. Empty rows are left out."""
    from app import passwords

    people, problems, seen = [], [], set()
    names, emails = form.getlist("person_name"), form.getlist("person_email")
    for i in range(max(len(names), len(emails))):
        name = " ".join((names[i] if i < len(names) else "").split())[:200]
        raw = (emails[i] if i < len(emails) else "").strip()
        if not name and not raw:
            continue
        email = ""
        if raw:
            email = passwords.normal_email(raw)
            if email is None:
                problems.append(de_en("„{e}“ ist keine gültige E-Mail-Adresse.", "“{e}” is not a valid email address.",
                                      e=raw[:254]))
                continue
            if email in seen:
                problems.append(de_en("{e} steht zweimal in der Liste.", "{e} is in the list twice.", e=email))
                continue
            seen.add(email)
        if not name:
            problems.append(de_en("Zu {e} fehlt der Name.", "The name for {e} is missing.", e=email))
            continue
        people.append((name, email))
    return people, problems


def make(survey, people, role, days, end_day, by):
    """The invitations, one per person: [(invite, token)], or a message why not. An address that has an account
    already becomes a link as well (its holder accepts it signed in); a new link for the same address replaces the
    open one of this survey."""
    from app import invites, mail, mailqueue
    from app.timeutil import end_of_local_day_utc

    refused = invites.invite_refusal(by, survey=survey)
    if refused:
        return refused
    if role not in ROLES or days not in DAYS:
        abort(400)
    if not people:
        return de_en("Bitte mindestens eine Person mit Namen eintragen.", "Please enter at least one person with a name.")
    if len(people) > MAX_PEOPLE:
        return de_en("Höchstens {n} Personen auf einmal.", "At most {n} people at once.", n=MAX_PEOPLE)
    invites.sweep()
    mailqueue.lock_account(by.id)
    sent = mailqueue.sent_by(by.id, invites.KIND, utcnow() - timedelta(days=1))
    if sent + len(people) > invites.PER_DAY:
        return de_en("Mehr als {n} Einladungen in 24 Stunden sind nicht möglich (heute noch {r}).",
                     "More than {n} invitations within 24 hours are not possible ({r} left today).",
                     n=invites.PER_DAY, r=max(0, invites.PER_DAY - sent))
    until = end_of_local_day_utc(end_day)
    rows = []
    for name, email in people:
        if email:
            db.session.execute(db.delete(MemberInvite).where(MemberInvite.survey_id == survey.id,
                                                             MemberInvite.email == email))
        token = secrets.token_urlsafe(32)
        invite = MemberInvite(survey_id=survey.id, role=role, email=email, name=name, token_hash=invites._hash(token),
                              created_by_id=by.id, expires_at=utcnow() + timedelta(days=days), access_until=until,
                              handed_out=True)
        db.session.add(invite)
        mailqueue.record(by.id, invites.KIND)  # counts for PER_DAY as a mailed invitation does
        rows.append((invite, token))
    db.session.commit()
    from app import security_log

    security_log.event("invitation_links", by=by.id, role=role, target=f"survey {survey.id}",
                       counts={"links": len(rows), "with_address": sum(1 for _n, e in people if e),
                               "mail": int(mail.enabled())})
    return rows


def _fresh_or(survey):
    from app import accounts

    return accounts.need_fresh(url_for("surveys.detail", survey_id=survey.id) + "#members")


def _shown(survey, rows):
    """The page that shows the links once (no-store: its content is the tokens)."""
    from app import invites, mail

    links = [{"invite": i, "token": t, "link": invites.link_of(t)} for i, t in rows]
    page = render_template("invites/links_made.html", survey=survey, links=links, mail_on=mail.enabled(),
                           role=SurveyMember.roles().get(rows[0][0].role, ("", ""))[0] if rows else "")
    response = current_app.make_response(page)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/survey/<int:survey_id>", methods=["POST"])
@login_required
def create(survey_id):
    from app.surveys import _back_to, get_survey

    survey = get_survey(survey_id, need="admin")
    again = _fresh_or(survey)
    if again:
        return again
    people, problems = people_of(request.form)
    end_day, wrong = parse_end(request.form.get("access_until"))
    days = int(request.form.get("days") or 0) if (request.form.get("days") or "").isdigit() else 0
    role = request.form.get("role", "editor")
    if problems or wrong:
        for said in problems + ([wrong] if wrong else []):
            flash(said, "error")
        return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")
    made = make(survey, people, role, days, end_day, current_user)
    if isinstance(made, str):
        flash(made, "error")
        return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")
    return _shown(survey, made)


def _held(survey, tokens):
    """The open invitations of this survey behind tokens a page showed: [(invite, token)]."""
    from app import invites

    out = []
    for token in tokens[:MAX_PEOPLE]:
        invite = invites.valid(token)
        if invite is not None and invite.survey_id == survey.id and invite.name is not None:
            out.append((invite, token))
    return out


@bp.route("/survey/<int:survey_id>/mail", methods=["POST"])
@login_required
def send(survey_id):
    """"Per Mail senden" on the page of the links: the token comes back from that page (the platform keeps only its
    hash), the mail goes to the address of the invitation."""
    from app import invites, mail, mailqueue
    from app.surveys import get_survey

    survey = get_survey(survey_id, need="admin")
    held = _held(survey, [request.form.get("token", "")])
    if not mail.enabled() or not held or not held[0][0].email:
        abort(400)
    invite, token = held[0]
    mailqueue.lock_account(current_user.id)
    if not mailqueue.room_for(current_user.id):
        return {"ok": False, "said": de_en("Ihr Konto hat die Grenze der E-Mails für 24 Stunden erreicht.",
                                            "Your account has reached the limit of emails for 24 hours.")}, 429
    mailqueue.record(current_user.id, "notice")  # the mail counts for the account's limit of mails
    db.session.commit()
    invites._send(invite, token, current_user, survey=survey)
    return {"ok": True, "said": de_en("An {e} gesendet.", "Sent to {e}.", e=invite.email)}


@bp.route("/survey/<int:survey_id>/csv", methods=["POST"])
@login_required
def as_csv(survey_id):
    """"Alle als CSV": name, address, link, valid until, access ends."""
    from app import invites, lang
    from app.surveys import get_survey
    from app.timeutil import fmt

    survey = get_survey(survey_id, need="admin")
    held = _held(survey, request.form.getlist("token"))
    if not held:
        abort(400)
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow([de_en("Name", "Name"), de_en("E-Mail-Adresse", "Email address"), de_en("Link", "Link"),
                     de_en("Link gültig bis", "Link valid until"), de_en("Zugang endet am", "Access ends on")])
    for invite, token in held:
        writer.writerow([_cell(invite.name), _cell(invite.email), invites.link_of(token),
                         fmt(invite.expires_at, lang=lang.current()),
                         fmt(invite.access_until, with_time=False, lang=lang.current()) if invite.access_until else ""])
    response = current_app.make_response(("﻿" + out.getvalue()).encode("utf-8"))
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", survey.title or "")[:40].strip("-") or "befragung"
    response.headers["Content-Disposition"] = f'attachment; filename="einladungslinks-{name}.csv"'
    response.headers["Cache-Control"] = "no-store"
    return response


def _cell(text):
    """A cell a spreadsheet does not read as a formula."""
    text = text or ""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@bp.route("/<int:invite_id>/renew", methods=["POST"])
@login_required
def renew(invite_id):
    """"Neuer Link": the old link stops working, the new one holds as long as the old one did, shown once."""
    from app import invites, security_log
    from app.surveys import get_survey

    invite = db.session.get(MemberInvite, invite_id)
    if invite is None or not invite.survey_id or invite.name is None:
        abort(404)
    survey = get_survey(invite.survey_id, need="admin")
    again = _fresh_or(survey)
    if again:
        return again
    if not invites.authorised(invite) or invite.expires_at < utcnow():
        flash(de_en("Diese Einladung gilt nicht mehr. Bitte neu einladen.", "This invitation no longer holds. Please "
                    "invite again."), "error")
        return redirect(url_for("surveys.detail", survey_id=survey.id) + "#members")
    token = secrets.token_urlsafe(32)
    invite.token_hash = invites._hash(token)
    db.session.commit()
    security_log.event("invitation_link_renewed", by=current_user.id, target=f"survey {survey.id}")
    return _shown(survey, [(invite, token)])


# --- the end of the access (E9) ---------------------------------------------------------------------------------------

def end_due(now=None):
    """Hourly (lifecycle._rare_sweeps): end the memberships whose day has passed, as an admin removing the person
    (accounts.leave_survey: the name stays where participants consented under it). Never an admin's (a link gives
    none; an admin made later keeps the survey). Returns how many."""
    from app import accounts, security_log
    from app.models import Survey

    now = now or utcnow()
    n = 0
    for member in list(db.session.scalars(db.select(SurveyMember).where(SurveyMember.access_until.is_not(None),
                                                                        SurveyMember.access_until < now))):
        if member.role == "admin" or member.user_id is None:
            member.access_until = None
            continue
        survey = db.session.get(Survey, member.survey_id)
        if survey is None:
            continue
        uid = member.user_id
        accounts.leave_survey(survey, member)
        db.session.commit()
        security_log.event("membership_ended", account=uid, target=f"survey {survey.id}")
        n += 1
    db.session.commit()
    return n


def period_from_links(user, row_for, now=None):
    """E9 for a new account (account_types.start_new): when every survey it is involved in came by a link with an
    end and it is in no group, its period ends on the latest of those days, unless it ends earlier anyway."""
    from app.models import WorkGroupMember

    ends = list(db.session.scalars(db.select(SurveyMember.access_until).where(SurveyMember.user_id == user.id)))
    if not ends or any(e is None for e in ends):
        return
    if db.session.scalar(db.select(WorkGroupMember.id).where(WorkGroupMember.user_id == user.id).limit(1)) is not None:
        return
    end = max(ends)
    row = row_for(user)
    if row.valid_until is None or row.valid_until > end:
        row.valid_from = row.valid_from or now or utcnow()
        row.valid_until = end
        row.valid_spec = None
