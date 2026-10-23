"""Researchers by e-mail (mailversand-konzept.md, M5 and M5a; only with mail switched on, app/mail.py).

Inviting: whoever may add people to a survey (role "Admin") or a group (lead) types a complete address into "Person
hinzufügen". Has no account of a site admin that address (the search finds those and adds them at once), an
invitation goes out: a link valid INVITE_DAYS days and once, whose token is stored only as a hash. Whoever opens it
signs in with ORCID or sets a password for a new account with the invited address, and is a member with the role
chosen. Being invited is a reason to sign in, as before; it gives no right to create surveys (S11 of the audit
2026-10-06), and only who may create surveys sends one, as it brings in a new account (Ralf, 2026-10-09,
rechte-uebersicht-konzept.md H3). Open invitations stand under the people involved, with "zurückziehen".

Notices: an account that someone adds to a survey or a group gets a short mail, if it has an address and did not
switch the notices off on "Konto" (User.display["mail_notices"] False).
"""
import hashlib
import secrets
import time
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import brand
from app.extensions import db
from app.lang import de_en
from app.models import MemberInvite, Survey, SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember, utcnow

bp = Blueprint("invites", __name__, url_prefix="/invite")

INVITE_DAYS = 14
PER_DAY = 30         # invitations one person sends within 24 hours
KIND = "member"      # their rows in mail_outbox (sent at once, kept for the count; mailqueue.record)
SESSION_KEY = "invite"  # the token while the person signs in at ORCID
SESSION_SECONDS = 30 * 60  # that long after "Mit ORCID anmelden und annehmen" (audit 2026-10-09b, T6)


def _hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def sweep():
    """Expired invitations go (called before every listing and every new invitation)."""
    db.session.execute(db.delete(MemberInvite).where(MemberInvite.expires_at < utcnow()))


def open_for(survey=None, group=None):
    sweep()
    query = db.select(MemberInvite).order_by(MemberInvite.created_at)
    query = query.where(MemberInvite.survey_id == survey.id) if survey is not None else query.where(
        MemberInvite.group_id == group.id)
    return list(db.session.scalars(query))


def valid(token):
    """The open invitation for this token, or None."""
    row = db.session.scalar(db.select(MemberInvite).filter_by(token_hash=_hash(token)))
    return row if row is not None and row.expires_at >= utcnow() and authorised(row) else None


def authorised(invite):
    """Recheck the inviter at redemption, not only when the invitation was issued (AU-3)."""
    from app import accounts, auth

    maker = db.session.get(User, invite.created_by_id) if invite.created_by_id else None
    target = target_of(invite)
    if maker is None or target is None or accounts.is_blocked(maker) or not auth.may_admit(maker):
        return False
    if invite.survey_id:
        return target.can(maker, "admin")
    # a list for a course a site admin invited without leading it (app/bulk_invites.py)
    return target.led_by(maker) or (target.account_type_id is not None
                                    and maker.orcid in current_app.config["ADMIN_ORCIDS"])


def revoke_by(user_id, survey_id=None, group_id=None):
    query = db.delete(MemberInvite).where(MemberInvite.created_by_id == user_id)
    if survey_id is not None:
        query = query.where(MemberInvite.survey_id == survey_id)
    if group_id is not None:
        query = query.where(MemberInvite.group_id == group_id)
    db.session.execute(query)


def target_of(invite):
    return db.session.get(Survey, invite.survey_id) if invite.survey_id else db.session.get(WorkGroup, invite.group_id)


def _title(invite, target):
    return target.title if invite.survey_id else target.name


def _role_label(invite):
    if invite.survey_id:
        return SurveyMember.roles().get(invite.role, ("", ""))[0]
    return WorkGroupMember.roles().get(invite.role, ("", ""))[0]


def _account_of(email):
    return db.session.scalar(db.select(User).join(UserLogin, UserLogin.user_id == User.id).filter(UserLogin.email == email))


def create(email, role, by, survey=None, group=None):
    """A new invitation, mailed at once; one open per address and survey or group (a new one replaces it).
    Returns None, or why not (a message)."""
    from app import auth, mail, mailqueue

    if not auth.may_admit(by):  # an invitation by mail brings in a new account (rechte-uebersicht-konzept.md H3)
        return auth.not_admitted()
    sweep()
    # counted by the mails sent, not by the invitations still there: inviting the same address again or withdrawing
    # gives no room back; and every invitation counts for the account's limit (audit 2026-10-09b, E2)
    mailqueue.lock_account(by.id)
    if mailqueue.sent_by(by.id, KIND, utcnow() - timedelta(days=1)) >= PER_DAY:
        return de_en("Mehr als {n} Einladungen in 24 Stunden sind nicht möglich.",
                     "More than {n} invitations within 24 hours are not possible.", n=PER_DAY)
    if not mailqueue.room_for(by.id):
        return de_en("Ihr Konto hat die Grenze von {n} E-Mails in 24 Stunden erreicht. Bitte laden Sie morgen ein.",
                     "Your account has reached the limit of {n} emails within 24 hours. Please invite tomorrow.",
                     n=current_app.config.get("MAIL_PER_ACCOUNT_DAY"))
    where = (MemberInvite.survey_id == survey.id) if survey is not None else (MemberInvite.group_id == group.id)
    db.session.execute(db.delete(MemberInvite).where(where, MemberInvite.email == email))
    token = secrets.token_urlsafe(32)
    invite = MemberInvite(survey_id=survey.id if survey is not None else None,
                          group_id=group.id if group is not None else None, role=role, email=email,
                          token_hash=_hash(token), created_by_id=by.id,
                          expires_at=utcnow() + timedelta(days=INVITE_DAYS))
    db.session.add(invite)
    mailqueue.record(by.id, KIND)
    db.session.commit()
    from app import security_log

    security_log.event("invitation_created", by=by.id, role=role,  # Abgleich 2026-10-15, AU-7
                       target=f"survey {survey.id}" if survey is not None else f"group {group.id}")
    _send(invite, token, by, survey=survey, group=group)
    return None


def link_of(token):
    return brand.public_base() + url_for("invites.accept", token=token)


def _send(invite, token, by, survey=None, group=None):
    """The mail of an invitation (also of a list, app/bulk_invites.py)."""
    from app import mail

    email, link = invite.email, link_of(token)
    days = max(1, round((invite.expires_at - utcnow()).total_seconds() / 86400))
    # the language of the person inviting: nothing is known about the person invited
    if survey is not None:
        subject = de_en("Einladung zur Befragung „{t}“ in Askurious", "Invitation to the survey “{t}” in Askurious",
                        t=survey.title)
        what = de_en("an der Befragung „{t}“ mitzuarbeiten (Rolle „{r}“)", "to work on the survey “{t}” (role “{r}”)",
                     t=survey.title, r=_role_label(invite))
    else:
        subject = de_en("Einladung in die Gruppe „{t}“ in Askurious", "Invitation to the group “{t}” in Askurious",
                        t=group.name)
        what = de_en("in die Gruppe „{t}“ (Rolle „{r}“)", "to the group “{t}” (role “{r}”)", t=group.name,
                     r=_role_label(invite))
    text = de_en("Guten Tag,\n\n{who} lädt Sie in Askurious ein, {what}.\n\nMit diesem Link nehmen Sie an (gültig {d} Tage, "
                 "nur einmal); Sie melden sich dort mit ORCID an oder legen ein Passwort fest:\n\n{link}\n\n"
                 "Kennen Sie die Person nicht, ignorieren Sie diese E-Mail.",
                 "Hello,\n\n{who} invites you in Askurious {what}.\n\nAccept with this link (valid for {d} days, once only); "
                 "there you sign in with ORCID or choose a password:\n\n{link}\n\n"
                 "If you do not know the person, ignore this email.",
                 who=by.display_name, what=what, d=days, link=link)
    reply = _reply_to(by)
    mail.send(email, subject, text + mail.signature(), reply_to=reply)


def _reply_to(user):
    row = db.session.get(UserLogin, user.id)
    return row.email if row is not None and row.email else None


def notices_on(user):
    display = user.display if isinstance(user.display, dict) else {}
    return display.get("mail_notices", True) is not False


def notice(user, by, survey=None, group=None, role=""):
    """M5a: tell an account by mail that `by` added it to a survey or a group (mail on, an address, notices on, and
    room left in the limit of `by`, whose mails these count for: audit 2026-10-09b, E2)."""
    from app import lang, mail, mailqueue

    if user is None or user.id == by.id or not mail.enabled() or not notices_on(user):
        return None
    row = db.session.get(UserLogin, user.id)
    if row is None or not row.email:
        return None
    mailqueue.lock_account(by.id)
    if not mailqueue.room_for(by.id):
        db.session.commit()
        return None
    mailqueue.record(by.id, "notice")
    db.session.commit()
    base = brand.public_base()
    with mail.in_language(lang.of(user)):
        if survey is not None:
            label = SurveyMember.roles().get(role, ("", ""))[0]
            subject = de_en("Askurious: Sie sind an „{t}“ beteiligt", "Askurious: you are involved in “{t}”", t=survey.title)
            text = de_en("Guten Tag {name},\n\n{who} hat Sie an der Befragung „{t}“ beteiligt (Rolle „{r}“):\n\n{link}",
                         "Hello {name},\n\n{who} has involved you in the survey “{t}” (role “{r}”):\n\n{link}",
                         name=user.name, who=by.display_name, t=survey.title, r=label,
                         link=base + url_for("surveys.detail", survey_id=survey.id))
        else:
            label = WorkGroupMember.roles().get(role, ("", ""))[0]
            subject = de_en("Askurious: Sie sind in der Gruppe „{t}“", "Askurious: you are in the group “{t}”", t=group.name)
            text = de_en("Guten Tag {name},\n\n{who} hat Sie in die Gruppe „{t}“ aufgenommen (Rolle „{r}“):\n\n{link}",
                         "Hello {name},\n\n{who} has added you to the group “{t}” (role “{r}”):\n\n{link}",
                         name=user.name, who=by.display_name, t=group.name, r=label,
                         link=base + url_for("groups.detail", group_id=group.id))
        text += de_en("\n\nDiese Hinweise schalten Sie auf der Seite „Konto › Anzeige und Sprache“ ab.",
                      "\n\nYou can switch these notices off on the page “Account › Display and language”.") + mail.signature()
    return mail.send(row.email, subject, text, reply_to=_reply_to(by))


def join(invite, user=None, orcid=None, name=""):
    """Make the person a member as the invitation says and remove it: `user`, or (first sign-in with ORCID, no
    account yet) the iD with its name, as adding by iD does before a first sign-in. Returns (url to go to, message
    or None)."""
    from app import groups as groups_mod, people_keys

    if not authorised(invite):
        return url_for("dashboard.index"), de_en("Diese Einladung ist nicht mehr gültig.", "This invitation is no longer valid.")
    if user is not None:
        orcid, name = user.orcid, user.name
    target = target_of(invite)
    claimed = db.session.execute(db.delete(MemberInvite).where(
        MemberInvite.id == invite.id, MemberInvite.expires_at > utcnow())).rowcount
    if claimed != 1:
        return url_for("dashboard.index"), de_en("Diese Einladung ist nicht mehr gültig.", "This invitation is no longer valid.")
    if target is None:
        db.session.commit()
        return url_for("dashboard.index"), de_en("Die Befragung oder Gruppe gibt es nicht mehr.",
                                                 "The survey or group no longer exists.")
    person = user or people_keys.account(orcid, name)  # the iD's account, pending until its first sign-in
    inviter = invite.created_by_id
    if invite.survey_id:
        role = invite.role if invite.role in SurveyMember.ROLES else "editor"
        member = next((m for m in target.members if m.user_id == person.id), None)
        if member is None:
            target.members.append(SurveyMember(
                user=person, position=len(target.members), name=name or "",
                affiliation=__import__("app.orgs", fromlist=["x"]).affiliation_of(user), invited_by_id=inviter, role=role))
        elif SurveyMember.LEVEL.get(role, 0) > SurveyMember.LEVEL.get(member.role, 0):
            member.role = role  # already involved: the higher of both roles, as joinlinks.join (code review 2026-10-15, H7)
        db.session.commit()
        _accepted(invite, user, "survey", target.id)
        return url_for("surveys.detail", survey_id=target.id), None
    member = next((m for m in target.members if m.user_id == person.id), None)
    if member is not None and invite.role == "lead" and member.role != "lead":
        member.role = "lead"  # as joinlinks.join (H7)
    if member is None:
        if len(target.members) >= groups_mod.MAX_MEMBERS or groups_mod.count_for(person.id) >= groups_mod.MAX_GROUPS:
            db.session.commit()
            return url_for("dashboard.index"), de_en("Die Gruppe ist voll, oder Sie sind schon in zu vielen Gruppen.",
                                                     "The group is full, or you are already in too many groups.")
        target.members.append(WorkGroupMember(user=person, name=name or "",
                                              role=invite.role if invite.role in WorkGroupMember.ROLES else "member",
                                              invited_by_id=inviter))
        from app import kind_access

        kind_access.joined(target, user)  # "darf Befragungen anlegen": "create" once (E3)
    db.session.commit()
    _accepted(invite, user, "group", target.id)
    return url_for("groups.detail", group_id=target.id), None


def _accepted(invite, user, kind, target_id):
    """The security log (audit 2026-10-13, AU-7): an invitation gave a role. Numbers only, no address."""
    from app import security_log

    security_log.event("invitation_accepted", account=user.id if user is not None else None, kind=kind,
                       target=target_id, role=invite.role, by=invite.created_by_id)


def accept_after_orcid(orcid, name):
    """Called by the ORCID callback before it asks whether this iD may sign in: an invitation the person opened
    before going to ORCID (the token in the session) makes the iD a member first, which is its reason to sign in.
    Returns the url to go to afterwards, or None. The token counts only if the person chose to accept with ORCID
    within SESSION_SECONDS; it is dropped either way (T6)."""
    from app import secrets_box

    held = session.pop(SESSION_KEY, None)
    token = None
    if isinstance(held, dict) and time.time() - float(held.get("at") or 0) <= SESSION_SECONDS:
        token = secrets_box.decrypt(held.get("box"))  # only encrypted in the cookie, which is signed, not encrypted
    invite = valid(token) if token else None
    if invite is None:
        return None
    user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
    goal, said = join(invite, user=user, orcid=orcid, name=(name or "")[:200])
    if said:
        flash(said, "error")
    return goal


def _invalid():
    flash(de_en("Diese Einladung ist nicht mehr gültig: Sie wurde schon angenommen, zurückgezogen oder ist abgelaufen. "
                "Bitte fragen Sie die Person, die Sie eingeladen hat.",
                "This invitation is no longer valid: it was already accepted, withdrawn or has expired. Please ask the "
                "person who invited you."), "error")
    return redirect(url_for("public.index"))


@bp.route("/<token>", methods=["GET", "POST"])
def accept(token):
    """The link of an invitation: signed in, accept with one click; else sign in with ORCID, or choose a password
    for a new account with the invited address."""
    from app import accounts, limits, mail, passwords

    if not limits.allow_invite_try(count=False):
        abort(429)  # too many links that led nowhere from this sender (security audit 2026-10-13, TN-2)
    invite = valid(token)
    # an invitation of a list whose links the inviter passed on (app/bulk_invites.py) works also without mail
    if invite is None or not (mail.enabled() or invite.handed_out):
        if invite is None:
            limits.allow_invite_try()
        return _invalid()
    target = target_of(invite)
    if target is None:
        return _invalid()
    title = _title(invite, target)
    if current_user.is_authenticated:
        if request.method == "POST":
            goal, said = join(invite, current_user._get_current_object())
            flash(said or de_en("Einladung angenommen.", "Invitation accepted."), "error" if said else "success")
            return redirect(goal)
        return _no_store(render_template("invites/accept.html", invite=invite, title=title, role=_role_label(invite),
                                         token=token, signed_in=True))
    if request.method == "POST" and request.form.get("way") == "orcid":
        # the token goes into the session only now, for the way over ORCID (auth.orcid_callback), encrypted and for
        # SESSION_SECONDS: an invitation merely opened on a shared computer is not taken by the next ORCID sign-in (T6)
        from app import secrets_box

        session[SESSION_KEY] = {"box": secrets_box.encrypt(token), "at": time.time()}
        return redirect(url_for("auth.login"))
    taken = _account_of(invite.email) is not None
    if request.method == "POST" and request.form.get("way") == "password" and not taken:
        name = " ".join(request.form.get("name", "").split())[:200]
        password = request.form.get("password", "")
        error = None
        if not name:
            error = de_en("Bitte Ihren Namen angeben.", "Please enter your name.")
        if not error:
            error = passwords.problem(password, name, invite.email)
        if not error and password != request.form.get("repeat", ""):
            error = de_en("Die beiden Eingaben stimmen nicht überein.", "The two entries do not match.")
        if error:
            flash(error, "error")
        else:
            inviter = db.session.get(User, invite.created_by_id) if invite.created_by_id else None
            user = accounts.create_local(name, invite.email, "",
                                         False, inviter)
            db.session.flush()
            passwords.set_password(user, password)
            user.last_login_at = utcnow()
            goal, said = join(invite, user)
            from app import account_types

            # its user type and period (a course gives its own) and survey types, raised as far as the invitation
            # needs (F8)
            account_types.new_account_levels(user, account_types.start_new(user))
            db.session.commit()
            from app import auth, notify

            auth.remember(user, via="password")
            try:
                notify.new_user(user)
            except Exception as e:  # noqa: BLE001 - the note to the operators must not break the page
                current_app.logger.warning("New account not reported in Slack: %s", type(e).__name__)
            flash(said or de_en("Konto angelegt, Einladung angenommen.", "Account created, invitation accepted."),
                  "error" if said else "success")
            return redirect(goal)
    return _no_store(render_template(
        "invites/accept.html", invite=invite, title=title, role=_role_label(invite), token=token, signed_in=False,
        taken=taken, orcid_on=bool(current_app.config.get("ORCID_CLIENT_ID")), min_length=passwords.MIN_LENGTH,
        form=request.form if request.method == "POST" else {}))


def _no_store(page):
    """The page of an invitation: its address is the token, so the browser keeps no copy (T9)."""
    response = current_app.make_response(page)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/survey/<int:survey_id>", methods=["POST"])
@login_required
def to_survey(survey_id):
    from app import mail, passwords
    from app.surveys import get_survey

    from app.surveys import _back_to

    survey = get_survey(survey_id, need="admin")
    email = passwords.normal_email(request.form.get("email"))
    role = request.form.get("role", "editor")
    if not mail.enabled() or email is None or role not in SurveyMember.ROLES:
        abort(400)
    said = create(email, role, current_user, survey=survey)
    flash(said or de_en("Einladung an {e} verschickt.", "Invitation sent to {e}.", e=email), "error" if said else "success")
    return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")


@bp.route("/group/<int:group_id>", methods=["POST"])
@login_required
def to_group(group_id):
    from app import groups, mail, passwords

    group = groups._group(group_id, lead=True)
    email = passwords.normal_email(request.form.get("email"))
    role = request.form.get("role", "member")
    if not mail.enabled() or email is None or role not in WorkGroupMember.ROLES:
        abort(400)
    said = create(email, role, current_user, group=group)
    flash(said or de_en("Einladung an {e} verschickt.", "Invitation sent to {e}.", e=email), "error" if said else "success")
    return redirect(url_for("groups.detail", group_id=group.id) + "#members")


@bp.route("/withdraw/<int:invite_id>", methods=["POST"])
@login_required
def withdraw(invite_id):
    from app import groups
    from app.surveys import get_survey

    invite = db.session.get(MemberInvite, invite_id)
    if invite is None:
        abort(404)
    if invite.survey_id:
        get_survey(invite.survey_id, need="admin")
        goal = url_for("surveys.detail", survey_id=invite.survey_id)
    else:
        groups._group(invite.group_id, lead=True)
        goal = url_for("groups.detail", group_id=invite.group_id)
    db.session.delete(invite)
    db.session.commit()
    flash(de_en("Einladung zurückgezogen; der Link gilt nicht mehr.", "Invitation withdrawn; the link no longer works."),
          "success")
    return redirect(goal + "#members")
