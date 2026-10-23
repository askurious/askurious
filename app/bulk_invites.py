"""Sammeleinladung: a list of e-mail addresses for a course (konzept-nutzertypen-2026-10-15.md, N14, N15).

A course is a group a site admin gave a user type and an end (app/account_types.py). Its lead (who may bring in new
people, auth.may_admit) or a site admin pastes a list of addresses; a preview sorts them into new, with an account,
invalid and twice. Each new address gets a personal invitation to the group (app/invites.py: one-time, only its hash
stored, INVITE_DAYS or up to 30 days). Then:

- mail on: the platform mails each invitation;
- mail off (the RWTH server today): the platform sends nothing and hands the links out once as a CSV file
  (address, link, valid until) to send with one's own mail program or the learning platform. The invitation
  (MemberInvite.handed_out) can then be accepted without mail; the address is not confirmed by the platform, as with
  the one-time link of an account a site admin makes.

Addresses that have an account already become members at once; an account whose period ended, of the course's type,
opens again with the course's end (N8).

The lead of a course also makes one-time links for accounts without ORCID that came in through the course (N15):
without mail, "Passwort vergessen?" otherwise ends with the platform's admins for every student.
"""
import csv
import io
import re
import secrets
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.lang import de_en
from app.models import LOCAL_PREFIX, MemberInvite, User, UserLogin, WorkGroup, WorkGroupMember, utcnow

bp = Blueprint("bulk_invites", __name__, url_prefix="/course")

MAX_LIST = 250  # addresses per list (the group has at most groups.MAX_MEMBERS members)
DAYS = (14, 30)
_SPLIT = re.compile(r"[\s,;<>]+")


def _site_admin(user):
    return user.orcid in current_app.config["ADMIN_ORCIDS"]


def may_invite(user, group):
    """The lead of a course who may bring in new people, or a site admin."""
    from app import auth

    if group is None or group.account_type_id is None:
        return False
    return _site_admin(user) or (group.led_by(user) and auth.may_admit(user))


def _course(group_id):
    group = db.session.get(WorkGroup, group_id)
    if group is None or not may_invite(current_user, group):
        abort(404)
    return group


def sort_list(text, group):
    """{"new": [...], "account": [(address, user)], "member": [...], "invalid": [...], "twice": [...]} of a pasted list."""
    from app import passwords

    out = {"new": [], "account": [], "member": [], "invalid": [], "twice": []}
    seen = set()
    for part in _SPLIT.split(text or ""):
        part = part.strip().strip('"\'').removeprefix("mailto:")
        if not part:
            continue
        email = passwords.normal_email(part)
        if email is None:
            if "@" in part or "." in part:
                out["invalid"].append(part[:254])
            continue
        if email in seen:
            out["twice"].append(email)
            continue
        seen.add(email)
        user = db.session.scalar(db.select(User).join(UserLogin, UserLogin.user_id == User.id)
                                 .filter(UserLogin.email == email))
        if user is None:
            out["new"].append(email)
        elif group.member_of(user) is not None:
            out["member"].append(email)
        else:
            out["account"].append((email, user))
    return out


def _too_many(group, sorted_):
    from app import groups as groups_mod

    n = len(sorted_["new"]) + len(sorted_["account"])
    if n > MAX_LIST:
        return de_en("Höchstens {n} Adressen je Liste.", "At most {n} addresses per list.", n=MAX_LIST)
    if len(group.members) + n > groups_mod.MAX_MEMBERS:
        return de_en("Die Gruppe hätte damit mehr als {n} Mitglieder.", "The group would then have more than {n} members.",
                     n=groups_mod.MAX_MEMBERS)
    return None


def _add_existing(group, user):
    """An account with an address of the list: a member at once; an ended one of the course's type opens again."""
    from app import account_types, groups as groups_mod, invites

    if groups_mod.count_for(user.id) >= groups_mod.MAX_GROUPS:
        return False
    account_types.reopen_for_course(user, group, by=current_user)
    group.members.append(WorkGroupMember(user=user, name=user.name or "", role="member",
                                         invited_by_id=current_user.id, via="list"))
    from app import kind_access

    kind_access.joined(group, user, current_user)  # "darf Befragungen anlegen": "create" once (E3)
    db.session.flush()
    invites.notice(user, current_user, group=group, role="member")
    return True


@bp.route("/<int:group_id>/list", methods=["GET", "POST"])
@login_required
def invite_list(group_id):
    """Paste, preview, invite."""
    from app import accounts, invites, mail, security_log

    group = _course(group_id)
    text = request.form.get("addresses", "")[:60000]
    days = int(request.form.get("days") or DAYS[0]) if (request.form.get("days") or "").isdigit() else DAYS[0]
    days = days if days in DAYS else DAYS[0]
    sorted_ = sort_list(text, group) if request.method == "POST" else None
    if request.method == "POST" and request.form.get("step") == "send":
        again = accounts.need_fresh(url_for("bulk_invites.invite_list", group_id=group.id))
        if again:
            return again
        problem = _too_many(group, sorted_)
        if problem:
            flash(problem, "error")
        elif not sorted_["new"] and not sorted_["account"]:
            flash(de_en("Keine neuen Adressen in der Liste.", "No new addresses in the list."), "error")
        else:
            invites.sweep()
            rows = []
            for email in sorted_["new"]:
                db.session.execute(db.delete(MemberInvite).where(MemberInvite.group_id == group.id,
                                                                 MemberInvite.email == email))
                token = secrets.token_urlsafe(32)
                invite = MemberInvite(group_id=group.id, role="member", email=email, token_hash=invites._hash(token),
                                      created_by_id=current_user.id, expires_at=utcnow() + timedelta(days=days),
                                      handed_out=not mail.enabled())
                db.session.add(invite)
                rows.append((invite, token))
            added = sum(1 for _email, user in sorted_["account"] if _add_existing(group, user))
            db.session.commit()
            security_log.event("invitation_list", by=current_user.id, target=f"group {group.id}",
                               counts={"new": len(rows), "added": added})
            if mail.enabled():
                for invite, token in rows:
                    invites._send(invite, token, current_user, group=group)
                flash(de_en("{n} Einladungen verschickt, {a} Konten direkt aufgenommen.",
                            "{n} invitations sent, {a} accounts added at once.", n=len(rows), a=added), "success")
                return redirect(url_for("groups.detail", group_id=group.id) + "#members")
            return _csv(group, rows, added)
    return render_template("groups/invite_list.html", group=group, sorted=sorted_, text=text, days=days,
                           day_choices=DAYS, mail_on=mail.enabled(), max_list=MAX_LIST)


def _csv(group, rows, added):
    """The links of a list, once, as a file (mail off): the platform keeps only their hashes."""
    from app import invites, lang
    from app.timeutil import fmt

    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow([de_en("E-Mail-Adresse", "Email address"), de_en("Link", "Link"), de_en("gültig bis", "valid until")])
    for invite, token in rows:
        writer.writerow([invite.email, invites.link_of(token),
                         fmt(invite.expires_at, lang=lang.current())])
    response = current_app.make_response(("﻿" + out.getvalue()).encode("utf-8"))
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", group.name)[:40].strip("-") or "kurs"
    response.headers["Content-Disposition"] = f'attachment; filename="einladungen-{name}.csv"'
    response.headers["Cache-Control"] = "no-store"
    if added:
        response.headers["X-Added"] = str(added)
    return response


# --- one-time links by the lead (N15) ---------------------------------------------------------------------------------

def course_accounts(group):
    """Accounts without ORCID that came in through this course and are still members, with no roles beyond it
    (beyond_course): the lead may make them a one-time link for a new password."""
    ids = {m.user_id for m in group.members if m.user_id}
    if not ids:
        return []
    found = db.session.scalars(db.select(User).join(UserLogin, UserLogin.user_id == User.id).filter(
        User.id.in_(ids), UserLogin.origin_group_id == group.id, db.or_(User.orcid.is_(None), User.orcid.startswith(LOCAL_PREFIX))))
    return [u for u in found if not beyond_course(u, group, ids)]


def beyond_course(user, group, ids=None):
    """Whether the account holds anything beyond the course, which a one-time link of the lead would hand over with it
    (audit 2026-10-18, AUTHZ-03): a site admin, a role in an organisation (admin anywhere, member of one other than
    the course's and the guests), another group, or a survey with someone outside the course or shared with another
    group. Its own surveys among the people of the course stay within it."""
    from app import orgs
    from app.models import OrganisationMember, SurveyGroup, SurveyMember

    if orgs._site_admin(user):
        return True
    ids = ids if ids is not None else {m.user_id for m in group.members if m.user_id}
    for role, org_id in db.session.execute(db.select(OrganisationMember.role, OrganisationMember.org_id).where(
            OrganisationMember.user_id == user.id, orgs._valid())):
        if role == "admin" or org_id not in (orgs.of(group), orgs.guest_id()):
            return True
    if db.session.scalar(db.select(WorkGroupMember.id).where(WorkGroupMember.user_id == user.id,
                                                             WorkGroupMember.group_id != group.id).limit(1)) is not None:
        return True
    mine = db.select(SurveyMember.survey_id).where(SurveyMember.user_id == user.id)
    others = db.select(SurveyMember.id).where(SurveyMember.survey_id.in_(mine), db.or_(
        SurveyMember.user_id.is_(None), SurveyMember.user_id.not_in(ids | {user.id}))).limit(1)
    if db.session.scalar(others) is not None:
        return True
    shared = db.select(SurveyGroup.id).where(SurveyGroup.survey_id.in_(mine), SurveyGroup.group_id != group.id).limit(1)
    if db.session.scalar(shared) is not None:
        return True
    # a design, linked element or AInstein project someone outside the course shared with it (audit 2026-10-22, B-01)
    return any(owner not in ids | {user.id} for _org, owner in orgs.personal_shares(user.id))


@bp.route("/<int:group_id>/link/<int:user_id>", methods=["POST"])
@login_required
def one_time_link(group_id, user_id):
    from app import accounts, passwords, security_log

    group = db.session.get(WorkGroup, group_id)
    if group is None or group.account_type_id is None or not group.led_by(current_user):
        abort(404)
    user = db.session.get(User, user_id)
    if user is None or user.has_orcid or user not in course_accounts(group):
        abort(404)
    again = accounts.need_fresh(url_for("groups.detail", group_id=group.id))
    if again:
        return again
    from app import brand

    token = passwords.new_link_token(user, current_user)
    row = passwords.login_row(user)
    if row is not None:
        row.reset_requested_at = None
    db.session.commit()
    security_log.event("one_time_link_by_lead", account=user.id, by=current_user.id, target=f"group {group.id}")
    link = brand.public_base() + url_for("auth.set_password", token=token)
    response = current_app.make_response(render_template("groups/one_time_link.html", group=group, person=user,
                                                         link=link, hours=passwords.LINK_HOURS))
    response.headers["Cache-Control"] = "no-store"
    return response
