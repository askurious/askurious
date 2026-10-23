"""Links to join a group (gruppen-freischaltung-konzept.md, Ralf 2026-10-10, E5-E8 as recommended).

A lead makes a link with a validity (1, 7, 14 or 30 days) and a highest number of joins, and may revoke it. Whoever
opens it and signs in becomes a member at once (E7). The link brings in people nobody named, so only a lead who may
create surveys makes one (E5, the rule of auth.may_admit, rechte-uebersicht-konzept.md H3); in a group with "darf
Befragungen anlegen" the people who join may create surveys too, through the group, as members added by hand.

A site admin makes the same kind of link for the role "lead", used once: the lead link, for a group whose lead is
not known by ORCID iD or account (E2, app/admin.py).

The way in: signed in, one click; else with ORCID (the token waits encrypted in the session for SESSION_SECONDS, as
with invitations by mail, app/invites.py), and with mail switched on also by e-mail address, which sends an
invitation by mail from the lead who made the link: the address is confirmed by the person opening it (E8).
Only the hash of a token is stored.
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
from app.models import GroupJoinLink, User, UserLogin, WorkGroup, WorkGroupMember, utcnow

bp = Blueprint("joinlinks", __name__, url_prefix="/join")

DAYS = (1, 7, 14, 30)
DEFAULT_DAYS = 14
DEFAULT_USES = 30
LEAD_DAYS = 14
MAX_OPEN = 20               # open links per group
SESSION_KEY = "join"        # the token while the person signs in at ORCID
SESSION_SECONDS = 30 * 60
SHOW_KEY = "join_link_new"  # a new link, shown once on the next page (Post/Redirect/Get)


def _hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def sweep():
    """Expired links go (called before every listing and every new link)."""
    db.session.execute(db.delete(GroupJoinLink).where(GroupJoinLink.expires_at < utcnow()))


def open_for(group, role="member"):
    sweep()
    return list(db.session.scalars(db.select(GroupJoinLink).filter_by(group_id=group.id, role=role)
                                   .order_by(GroupJoinLink.created_at)))


def open_lead_links(group_ids):
    """{group id: number of open lead links} for the groups listed in the administration."""
    from app.models import GroupJoinLink as L

    if not group_ids:
        return {}
    rows = db.session.execute(db.select(L.group_id, db.func.count(L.id)).where(
        L.group_id.in_(group_ids), L.role == "lead", L.expires_at >= utcnow(), L.uses < L.max_uses).group_by(L.group_id))
    return dict(rows.all())


def valid(token):
    """The link for this token while it may still be used, or None."""
    row = db.session.scalar(db.select(GroupJoinLink).filter_by(token_hash=_hash(token))) if token else None
    if row is None or row.expires_at < utcnow() or row.uses >= row.max_uses:
        return None
    return row


def may_make(user, group):
    """A lead who may bring in people nobody named: who may create surveys (E5, auth.may_admit)."""
    from app import auth

    return group.led_by(user) and auth.may_admit(user)


def paused(link, group=None):
    """Whether the link rests because the person who made it may no longer make it (audit 2026-10-10, A1): blocked,
    no longer lead of the group, no longer allowed to create surveys; a lead link when its maker is no longer a site
    admin. Checked at every use, as invitations by mail check auth.may_admit: the link is kept and works again when
    the rights come back; revoking it stays with the group."""
    maker = db.session.get(User, link.created_by_id) if link.created_by_id else None
    if maker is None:
        return True
    row = db.session.get(UserLogin, maker.id)
    if row is not None and row.status == UserLogin.BLOCKED:
        return True
    if link.role == "lead":
        return maker.orcid not in current_app.config["ADMIN_ORCIDS"]
    group = group if group is not None else db.session.get(WorkGroup, link.group_id)
    return group is None or not may_make(maker, group)


def _paused_said():
    return de_en("Dieser Link ist zurzeit ausgesetzt: Die Person, die ihn erzeugt hat, darf keine Personen mehr in die "
                 "Gruppe holen. Bitte fragen Sie die Leitung der Gruppe nach einem neuen Link.",
                 "This link is suspended for now: the person who created it may no longer bring people into the group. "
                 "Please ask the lead of the group for a new link.")


def _take_use(link):
    """Count one use, in the database and only while uses < max_uses: two people at the same moment cannot both take
    the last place (audit 2026-10-10, A4). True if the use was taken."""
    done = db.session.execute(db.update(GroupJoinLink).where(GroupJoinLink.id == link.id,
                                                             GroupJoinLink.uses < GroupJoinLink.max_uses)
                              .values(uses=GroupJoinLink.uses + 1)
                              .execution_options(synchronize_session=False)).rowcount
    db.session.refresh(link)
    return done == 1


def _used_up():
    return de_en("Dieser Link ist inzwischen aufgebraucht.", "This link has been used up in the meantime.")


def create(group, by, days=DEFAULT_DAYS, max_uses=DEFAULT_USES, role="member"):
    """A new link; returns its address (the token is not kept), or a message why not."""
    from app import groups as groups_mod

    sweep()
    if len(open_for(group, role)) >= MAX_OPEN:
        return None, de_en("Mehr als {n} offene Links je Gruppe sind nicht möglich; bitte zuerst einen widerrufen.",
                           "More than {n} open links per group are not possible; please revoke one first.", n=MAX_OPEN)
    token = secrets.token_urlsafe(32)
    db.session.add(GroupJoinLink(group_id=group.id, role=role, token_hash=_hash(token),
                                 max_uses=max(1, min(int(max_uses), groups_mod.MAX_MEMBERS)),
                                 created_by_id=by.id, expires_at=utcnow() + timedelta(days=days)))
    db.session.commit()
    from app import security_log

    security_log.event("join_link_created", by=by.id, role=role, target=f"group {group.id}")  # Abgleich 2026-10-15, AU-7
    return brand.public_base() + url_for("joinlinks.page", token=token), None


def show_once(group, link):
    """The new link waits in the session, encrypted (the cookie is signed, not encrypted), for the next page."""
    from app import secrets_box

    session[SHOW_KEY] = {"group": group.id, "link": secrets_box.encrypt(link)}


def take_shown(group):
    """The link made just now for this group, once; else None."""
    from app import secrets_box

    held = session.get(SHOW_KEY)
    if not isinstance(held, dict) or held.get("group") != group.id:
        return None
    session.pop(SHOW_KEY, None)
    return secrets_box.decrypt(held.get("link"))


def join(link, user=None, orcid=None, name=""):
    """Make the person a member (or lead) as the link says. Returns (url to go to, message or None)."""
    from app import groups as groups_mod, people_keys

    if user is not None:
        orcid, name = user.orcid, user.name
    person = user or people_keys.account(orcid, create=False)  # an iD added before has a pending account
    group = db.session.get(WorkGroup, link.group_id)
    if group is None:
        db.session.delete(link)
        db.session.commit()
        return url_for("dashboard.index"), de_en("Die Gruppe gibt es nicht mehr.", "The group no longer exists.")
    if paused(link, group):
        return url_for("public.index"), _paused_said()
    member = next((m for m in group.members if person is not None and m.user_id == person.id), None)
    if member is not None:
        if link.role == "lead" and member.role != "lead":
            if not _take_use(link):
                db.session.rollback()
                return url_for("public.index"), _used_up()
            member.role = "lead"
            db.session.delete(link)
            db.session.commit()
            _used(link, user, group)
            return url_for("groups.detail", group_id=group.id), None
        return url_for("groups.detail", group_id=group.id), de_en("Sie sind schon in dieser Gruppe.",
                                                                  "You are already in this group.")
    if len(group.members) >= groups_mod.MAX_MEMBERS or groups_mod.count_for(person and person.id) >= groups_mod.MAX_GROUPS:
        return url_for("dashboard.index"), de_en("Die Gruppe ist voll, oder Sie sind schon in zu vielen Gruppen.",
                                                 "The group is full, or you are already in too many groups.")
    if not _take_use(link):
        db.session.rollback()
        return url_for("public.index"), _used_up()
    group.members.append(WorkGroupMember(user=person or people_keys.account(orcid, name), name=name or "",
                                         role="lead" if link.role == "lead" else "member",
                                         invited_by_id=link.created_by_id,
                                         via="leadlink" if link.role == "lead" else "link"))
    from app import kind_access

    kind_access.joined(group, user)  # "darf Befragungen anlegen": "create" once (E3)
    if link.role == "lead":
        db.session.delete(link)
    db.session.commit()
    _used(link, user, group)
    return url_for("groups.detail", group_id=group.id), None


def _used(link, user, group):
    """The security log (audit 2026-10-13, AU-7): a join link gave a role in a group. Numbers only, no address."""
    from app import security_log

    security_log.event("join_link_used", account=user.id if user is not None else None, target=group.id,
                       role=link.role, by=link.created_by_id)


def accept_after_orcid(orcid, name):
    """Called by the ORCID callback before it asks whether this iD may sign in: a link the person opened before going
    to ORCID makes the iD a member first, which is its reason to sign in. Returns the url to go to, or None."""
    from app import secrets_box

    held = session.pop(SESSION_KEY, None)
    token = None
    if isinstance(held, dict) and time.time() - float(held.get("at") or 0) <= SESSION_SECONDS:
        token = secrets_box.decrypt(held.get("box"))
    link = valid(token) if token else None
    if link is None:
        return None
    if paused(link):
        flash(_paused_said(), "error")
        return None
    user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
    goal, said = join(link, user=user, orcid=orcid, name=(name or "")[:200])
    flash(said or de_en("Sie sind jetzt in der Gruppe.", "You are now in the group."), "error" if said else "success")
    return goal


def reopen_expired(orcid):
    """Called by the ORCID callback before it checks a block: an account whose period ended, of the type of the course
    whose link this browser opened, opens again with the course's end (app/account_types.py, N8). The token stays in
    the session for accept_after_orcid."""
    from app import account_types, secrets_box

    held = session.get(SESSION_KEY)
    if not isinstance(held, dict) or time.time() - float(held.get("at") or 0) > SESSION_SECONDS:
        return False
    link = valid(secrets_box.decrypt(held.get("box")))
    group = db.session.get(WorkGroup, link.group_id) if link is not None and link.role == "member" else None
    user = db.session.scalar(db.select(User).filter_by(orcid=orcid)) if group is not None else None
    if user is None or group.account_type_id is None or paused(link, group):
        return False
    if account_types.reopen_for_course(user, group):
        db.session.commit()
        return True
    return False


def course_way(link, group):
    """A course without mail: whoever has the link creates an account with e-mail address and password here
    (konzept-nutzertypen-2026-10-15.md, N14). With mail on, the address is confirmed by an invitation instead."""
    from app import mail

    return group.account_type_id is not None and link.role == "member" and not mail.enabled()


def _create_with_password(link, group):
    """The account of the course way: (response, None) or (None, message)."""
    from app import account_types, accounts, auth, passwords

    name = " ".join(request.form.get("name", "").split())[:200]
    email = passwords.normal_email(request.form.get("email"))
    password = request.form.get("password", "")
    if not name or email is None:
        return None, de_en("Bitte Namen und eine gültige E-Mail-Adresse angeben.", "Please enter your name and a valid "
                           "email address.")
    error = passwords.problem(password, name, email)
    if not error and password != request.form.get("repeat", ""):
        error = de_en("Die beiden Eingaben stimmen nicht überein.", "The two entries do not match.")
    if error:
        return None, error
    if not accounts.email_free(email):
        # without mail no confirmation can stand in for the answer, so it says no more than needed and counts as a try
        # that led nowhere: whoever holds the link cannot ask address after address (audit 2026-10-18, AUTH-5)
        from app import limits

        limits.allow_invite_try()
        return None, de_en("Mit diesen Angaben lässt sich hier kein neues Konto anlegen. Haben Sie schon ein Konto, "
                           "melden Sie sich an und öffnen Sie den Link danach noch einmal.",
                           "No new account can be created here with these details. If you already have an account, "
                           "please sign in and open the link again afterwards.")
    maker = db.session.get(User, link.created_by_id) if link.created_by_id else None
    user = accounts.create_local(name, email, "", False, maker)
    db.session.flush()
    passwords.set_password(user, password)
    user.last_login_at = utcnow()
    goal, said = join(link, user)
    if said:
        db.session.rollback()
        return None, said
    account_types.new_account_levels(user, account_types.start_new(user))
    db.session.commit()
    auth.remember(user, via="password")
    from app import notify

    try:
        notify.new_user(user)
    except Exception as e:  # noqa: BLE001 - the note to the operators must not break the page
        current_app.logger.warning("New account not reported in Slack: %s", type(e).__name__)
    flash(de_en("Konto angelegt, Sie sind jetzt in „{g}“.", "Account created, you are now in “{g}”.", g=group.name),
          "success")
    return redirect(goal), None


def _invalid():
    flash(de_en("Dieser Link gilt nicht mehr: Er ist abgelaufen, aufgebraucht oder wurde widerrufen. Bitte fragen Sie "
                "die Person, die ihn Ihnen gegeben hat.",
                "This link is no longer valid: it has expired, been used up or been revoked. Please ask the person who "
                "gave it to you."), "error")
    return redirect(url_for("public.index"))


def _no_store(page):
    """The address of the page is the token, so the browser keeps no copy."""
    response = current_app.make_response(page)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<token>", methods=["GET", "POST"])
def page(token):
    """The link itself: signed in, join with one click; else sign in with ORCID or, with mail on, get an invitation
    by e-mail."""
    from app import invites, limits, mail, passwords, secrets_box

    if not limits.allow_invite_try(count=False):
        abort(429)  # too many links that led nowhere from this sender (security audit 2026-10-13, TN-2)
    link = valid(token)
    group = db.session.get(WorkGroup, link.group_id) if link is not None else None
    if group is None:
        limits.allow_invite_try()
        return _invalid()
    if paused(link, group):
        flash(_paused_said(), "error")
        return redirect(url_for("public.index"))
    if current_user.is_authenticated:
        if request.method == "POST":
            goal, said = join(link, current_user._get_current_object())
            flash(said or de_en("Sie sind jetzt in der Gruppe „{g}“.", "You are now in the group “{g}”.", g=group.name),
                  "error" if said else "success")
            return redirect(goal)
        if group.member_of(current_user) and not (link.role == "lead" and not group.led_by(current_user)):
            return redirect(url_for("groups.detail", group_id=group.id))
    elif request.method == "POST" and request.form.get("way") == "orcid":
        session[SESSION_KEY] = {"box": secrets_box.encrypt(token), "at": time.time()}
        return redirect(url_for("auth.login"))
    elif request.method == "POST" and request.form.get("way") == "password" and course_way(link, group):
        done, said = _create_with_password(link, group)
        if done is not None:
            return done
        flash(said, "error")
    elif request.method == "POST" and request.form.get("way") == "email" and mail.enabled() and link.role == "member":
        email = passwords.normal_email(request.form.get("email"))
        maker = db.session.get(User, link.created_by_id) if link.created_by_id else None
        if email is None:
            flash(de_en("Bitte eine gültige E-Mail-Adresse angeben.", "Please enter a valid email address."), "error")
        elif maker is None:
            return _invalid()
        elif not _take_use(link):  # each mail counts, so a passed-on link cannot send without end
            db.session.rollback()
            return _invalid()
        else:
            said = invites.create(email, "member", maker, group=group)  # commits the use with the invitation
            if said:
                db.session.rollback()
                flash(said, "error")
            else:
                flash(de_en("Wir haben eine E-Mail an {e} geschickt. Mit dem Link darin treten Sie der Gruppe bei.",
                            "We have sent an email to {e}. With the link in it you join the group.", e=email), "success")
                return redirect(url_for("public.index"))
    leads = [m.display_name for m in group.leads()]
    return _no_store(render_template("joinlinks/join.html", link=link, group=group, leads=leads, token=token,
                                     orcid_on=bool(current_app.config.get("ORCID_CLIENT_ID")),
                                     by_mail=mail.enabled() and link.role == "member",
                                     by_password=course_way(link, group), min_length=passwords.MIN_LENGTH,
                                     form=request.form if request.method == "POST" else {}))


@bp.route("/group/<int:group_id>", methods=["POST"])
@login_required
def make(group_id):
    """A lead makes a join link (E5, E6)."""
    from app import groups as groups_mod

    group = groups_mod._group(group_id, lead=True)
    from app import accounts

    again = accounts.need_fresh(url_for("groups.detail", group_id=group.id))
    if again:
        return again
    if not may_make(current_user, group):
        abort(403)
    days = request.form.get("days", type=int)
    uses = request.form.get("max_uses", type=int)
    if days not in DAYS or not uses or uses < 1:
        abort(400)
    link, said = create(group, current_user, days=days, max_uses=uses)
    if said:
        flash(said, "error")
    else:
        show_once(group, link)
    return redirect(url_for("groups.detail", group_id=group.id) + "#join-links")


def revoke_lead_links(group):
    """A site admin takes back the open lead links of a group (audit 2026-10-10, A5). Returns how many."""
    return db.session.execute(db.delete(GroupJoinLink).where(GroupJoinLink.group_id == group.id,
                                                             GroupJoinLink.role == "lead")
                              .execution_options(synchronize_session=False)).rowcount


@bp.route("/revoke/<int:link_id>", methods=["POST"])
@login_required
def revoke(link_id):
    from app import groups as groups_mod

    link = db.session.get(GroupJoinLink, link_id)
    if link is None or link.role != "member":
        abort(404)
    group = groups_mod._group(link.group_id, lead=True)
    db.session.delete(link)
    db.session.commit()
    flash(de_en("Link widerrufen; er gilt nicht mehr.", "Link revoked; it no longer works."), "success")
    return redirect(url_for("groups.detail", group_id=group.id) + "#join-links")
