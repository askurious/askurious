"""Verwaltung: what the admins of an organisation manage for it (konzept-abteilungen-2026-10-15.md, stage 3, A15-A20).

An organisation admin manages the people, groups and surveys of their organisation, never more than its caps allow
(A16: survey types, creating surveys, user types, number of accounts; set by site admins under Administration >
Organisationen):
- Personen: take a person in (by ORCID iD; someone without an account can then sign in and has their home here),
  remove someone who also belongs to another organisation (with the organisation's groups; a home here moves there),
  and for accounts at home here (not those of other admins of the organisation) the user type, the period, the survey types, the right to create surveys, blocking,
  the one-time link of an account without ORCID and deleting (app/org_delete.py: blocked at once, deleted after 30
  days, what belongs to the person alone passes to a person of the organisation or goes).
- Gruppen: create one with its lead, appoint a lead, rename, delete, "darf Befragungen anlegen", the preset of survey
  types for new accounts and the course (user type and end); adding and removing members stays with the group.
- Befragungen, the first tab and the page the management opens with (Ralf 2026-10-17): the dashboard of the
  organisation's surveys and participations in a chosen period (app/admin_stats.py, as on Administration >
  Befragungen) and the list of its surveys with status, people and participations, never their content or answers
  (A15); surveys whose admins asked to be left out of overviews are only counted.
Like the administration it asks for the second factor after a sign-in with a password (A20), and a fresh sign-in for
changes of rights. Every change goes into the security log. Admins of the organisation are deleted by site admins.
"""
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import account_types, autosave, kind_access, orgs
from app.extensions import db, json_given
from app.lang import de_en
from app.models import Response, SignInOrcid, Survey, SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember, utcnow

bp = Blueprint("org", __name__, url_prefix="/org")


JOIN_DAYS = 14        # an invitation link to join (people_action, join) is valid this long
JOIN_SALT = "org-join"


@bp.before_request
@login_required
def _org_admins_only():
    if request.endpoint == "org.join":  # the invited person accepts: signed in, not an admin
        return None
    org = orgs.get((request.view_args or {}).get("org_id"))
    if org is None or org.key == orgs.GUEST or not (orgs.is_admin_of(current_user, org.id) or current_user.is_admin):
        abort(404)
    g.org = org
    from app import twofactor

    return twofactor.org_gate()


def _fresh(endpoint, **kw):
    from app import accounts

    return accounts.need_fresh(url_for(endpoint, **kw))


def _log(name, **fields):
    from app import security_log

    security_log.event(name, by=current_user.id, **fields)


# --- Personen ----------------------------------------------------------------------------------------------------------

@bp.route("/<int:org_id>/")
def index(org_id):
    """The management opens with "Befragungen" (Ralf 2026-10-17); an address of the former first page "Personen" with
    its list's state (p_…) goes on to /people."""
    if any(k.startswith("p_") for k in request.args):
        return redirect(url_for("org.people", org_id=org_id, **request.args), 308)
    return redirect(url_for("org.surveys", org_id=org_id))


@bp.route("/<int:org_id>/people")
def people(org_id):
    """Everyone in the organisation, sorted, searched and in pages of 10, 20 or 50 by the database like the platform's
    list (Administration > Personen, Ralf 2026-10-18); the numbers count only the organisation's own surveys: what
    someone does elsewhere is not the organisation's business."""
    org = g.org
    from datetime import timedelta

    from app.models import OrganisationMember
    from app.tables import ROW_SIZES, Listing, by_text

    def count(column, *where, join=None):
        query = db.select(db.func.count(column))
        if join is not None:
            query = query.join(*join)
        return query.where(*where).scalar_subquery()

    here = orgs.column(Survey.org_id) == org.id
    real = (Response.source == "real", Response.is_preview.is_(False), Response.not_live_extra(),
            Response.status == Response.COMPLETED)
    n_surveys = count(SurveyMember.id, SurveyMember.user_id == User.id, here,
                      join=(Survey, Survey.id == SurveyMember.survey_id))
    n_running = count(SurveyMember.id, SurveyMember.user_id == User.id, here, Survey.status == Survey.RUNNING,
                      join=(Survey, Survey.id == SurveyMember.survey_id))
    n_own = count(Survey.id, Survey.created_by_id == User.id, here)
    n_completed = (db.select(db.func.count(Response.id)).join(SurveyMember, SurveyMember.survey_id == Response.survey_id)
                   .join(Survey, Survey.id == Response.survey_id).where(SurveyMember.user_id == User.id, here, *real)
                   .scalar_subquery())
    group_here = orgs.column(WorkGroup.org_id) == org.id
    first_group = (db.select(db.func.min(by_text(WorkGroup.name))).join(WorkGroupMember)
                   .where(WorkGroupMember.user_id == User.id, group_here).scalar_subquery())
    home_row = db.exists().where(OrganisationMember.user_id == User.id, OrganisationMember.is_home.is_(True),
                                 OrganisationMember.org_id == org.id)
    if org.id == orgs.default_id():  # "main" is the home of everyone without a home row
        home_row = db.or_(home_row, ~db.exists().where(OrganisationMember.user_id == User.id,
                                                       OrganisationMember.is_home.is_(True)))
    listing = Listing("p", {
        "name": by_text(User.name), "home": db.case((home_row, 0), else_=1), "groups": first_group,
        "surveys": (n_surveys, "desc"), "running": (n_running, "desc"), "own": (n_own, "desc"),
        "completed": (n_completed, "desc"), "since": (User.created_at, "desc"), "last": (User.last_login_at, "desc"),
    }, default="name", then=[User.id], anchor="personen", sizes=ROW_SIZES, search=[
        User.name, User.orcid,
        lambda match: db.exists().where(WorkGroupMember.user_id == User.id, WorkGroupMember.group_id == WorkGroup.id,
                                        group_here, match(WorkGroup.name))])
    base = (db.select(User.id).outerjoin(UserLogin, UserLogin.user_id == User.id)
            .where(User.id.in_(orgs.user_ids_in([org.id])), User.pending.is_(False)))
    groups_here = list(db.session.scalars(db.select(WorkGroup).where(group_here)
                                          .order_by(db.func.lower(WorkGroup.name), WorkGroup.id)))
    chosen = next((x for x in groups_here if x.id == request.args.get("p_group", type=int)), None)
    if chosen is not None:  # by group (Ralf 2026-10-16)
        base = base.where(User.id.in_(db.select(WorkGroupMember.user_id).where(WorkGroupMember.group_id == chosen.id)))
    kinds_only = request.args.get("p_kinds") == "1"  # as on the platform: survey types below "Bearbeiten"
    if kinds_only:
        base = base.where(kind_access.below_edit_clause())
    period = request.args.get("p_end") if request.args.get("p_end") in ("soon", "ended") else None
    if period == "soon":  # accounts by their period (app/account_types.py, N16)
        base = base.where(UserLogin.valid_until.is_not(None), UserLogin.valid_until > utcnow(),
                          UserLogin.valid_until < utcnow() + timedelta(days=account_types.NOTICE_DAYS[0] + 1))
    elif period == "ended":
        base = base.where(UserLogin.block_reason == account_types.EXPIRED)
    ids = [row[0] for row in listing.rows(base)]
    users = {u.id: u for u in db.session.scalars(db.select(User).where(User.id.in_(ids)))}
    logins = {row.user_id: row for row in db.session.scalars(db.select(UserLogin).where(UserLogin.user_id.in_(ids)))}
    numbers = {row[0]: row for row in db.session.execute(
        db.select(User.id, n_surveys, n_running, n_own, n_completed).where(User.id.in_(ids)))}
    members = {m.user_id: m for m in org.members if m.user_id is not None}
    groups_of = {}
    for user_id, name in db.session.execute(
            db.select(WorkGroupMember.user_id, WorkGroup.name).join(WorkGroup).where(
                group_here, WorkGroupMember.user_id.in_(ids)).order_by(by_text(WorkGroup.name))):
        groups_of.setdefault(user_id, []).append(name)
    from app import org_delete

    marks = org_delete.of_org(org)
    rows = []
    for uid in ids:
        user, login, (_, n_all, running, own, completed) = users[uid], logins.get(uid), numbers[uid]
        managed = orgs.manages(current_user, user, org)
        home = orgs.home_id(user) == org.id
        rows.append({"user": user, "member": members.get(user.id), "home": home,
                     "login": login, "period": account_types.summary(login), "groups": groups_of.get(user.id, []),
                     "surveys": n_all, "running": running, "own": own, "completed": completed,
                     "kinds": kind_access.clean(login.kinds) if login is not None else None,
                     "blocked": login is not None and login.status == UserLogin.BLOCKED,
                     "managed": managed, "why": None if managed or not home else orgs.not_managed_said(user, org),
                     "deletion": marks.get(uid), "deletion_due": uid in marks and (marks[uid].due_at <= utcnow() or bool(marks[uid].open_points)),
                     "removable": home and bool(org_delete.other_orgs(user, org))
                     and orgs.may_remove(current_user, user, org)})
    from app import org_invites

    invites, invite_rows = org_invites.page_of(org)  # the block "Offene Einladungen" (Ralf 2026-10-20)
    from flask import session

    from app import secrets_box

    invitation = session.get("org_join_link")  # the invitation link just made (people_action), shown once
    if isinstance(invitation, dict) and invitation.get("org") == org.id:
        session.pop("org_join_link")
        invitation = {"name": invitation.get("name") or "", "link": secrets_box.decrypt(invitation.get("link")),
                      "days": JOIN_DAYS}
    else:
        invitation = None
    return render_template("org/people.html", org=org, rows=rows, invites=invites, invite_rows=invite_rows,
                           types=orgs.allowed_types(org),
                           invitation=invitation,
                           default_type=account_types.default_type(org.id), left=orgs.accounts_left(org),
                           active="people", page=listing, chosen=chosen, kinds_only=kinds_only, period=period,
                           cap=kind_access.org_cap(org), group_options=[(x.id, x.name) for x in groups_here])


@bp.route("/<int:org_id>/people", methods=["POST"])
def people_action(org_id):
    """Take a person in by ORCID iD, or remove someone who also belongs elsewhere (org_delete.remove). Like "Person
    hinzufügen" of the platform (admin.sign_in_orcids), only the organisation is given (Ralf 2026-10-18)."""
    from app import signin
    from app.tables import back_args

    org = g.org
    back = redirect(url_for("org.people", org_id=org.id, **back_args(request.form.get("back")), _anchor="personen"))
    adding = request.form.get("action") != "remove"
    again = _fresh_add(org) if adding else _fresh("org.people", org_id=org.id)
    if again:
        return again
    if not adding:
        from app import org_delete

        user = db.session.get(User, request.form.get("user_id", type=int) or 0)
        row = orgs.row_of(org.id, user.id) if user is not None else None
        home_here = user is not None and orgs.home_id(user) == org.id
        in_groups = user is not None and org.id in orgs.ids_of(user)
        if home_here:
            # at home here: removed only when the person belongs to another organisation too, where the home moves
            # (org-personen-loeschen-konzept.md, 5a); else "Löschen" on the person's page
            # taking out reaches nothing beyond the organisation, so roles elsewhere do not stop it (orgs.may_remove)
            refused = None
            if orgs.is_admin_of(user, org.id):
                refused = de_en("Admins der Organisation entfernt ein Admin der Plattform.",
                                "A platform admin removes admins of the organisation.")
            elif not orgs.may_remove(current_user, user, org):
                refused = de_en("Dieses Konto verwalten Sie nicht.", "You do not manage this account.")
            if refused is None and not org_delete.other_orgs(user, org):
                refused = de_en("{n} gehört nur zu dieser Organisation; auf der Seite der Person lässt sich das Konto "
                                "löschen.", "{n} belongs to this organisation only; the account can be deleted on the "
                                "person's page.", n=user.display_name)
            if refused:
                flash(refused, "error")
                return back
        elif row is None and not in_groups:
            flash(de_en("Diese Person gehört nicht zur Organisation.", "This person does not belong to the organisation."),
                  "error")
            return back
        new_home, left = org_delete.remove(org, user)
        db.session.commit()
        orgs.forget()
        _log("organisation_member_removed", account=user.id if user else None, target=f"org {org.id}",
             kind=f"groups={left}" + (f" home={new_home.id}" if new_home is not None else ""))
        said = de_en("{n} gehört nicht mehr zur Organisation.", "{n} no longer belongs to the organisation.",
                     n=user.display_name)
        if left:
            said += " " + de_en("Aus {k} Gruppen der Organisation ausgetreten.", "Left {k} groups of the organisation.",
                                k=left)
        if new_home is not None:
            said += " " + de_en("Die Heimat ist jetzt {o}.", "The home is now {o}.", o=orgs.label(new_home))
        flash(said, "success")
        return back
    orcid = signin.clean(request.form.get("orcid"))
    if not orcid:
        return _refused("allow-orcid", de_en("Das ist keine gültige ORCID iD (0000-0000-0000-0000 oder die Adresse des "
                                             "Profils).", "This is not a valid ORCID iD (0000-0000-0000-0000 or the "
                                             "address of the profile)."), "orcid")
    user = db.session.scalar(db.select(User).filter_by(orcid=orcid, pending=False))
    if user is not None:
        # an account that exists joins only by its own consent: the admin gets an invitation link to pass on, the
        # person accepts it signed in (join below); a guest's home moves only then (audit 2026-10-18, AUTHZ-01/02)
        if orgs.is_member(user, org.id):
            flash(de_en("{n} gehört schon zur Organisation.", "{n} already belongs to the organisation.",
                        n=user.display_name), "success")
            return back
        from flask import session

        from app import brand, secrets_box

        link = brand.public_base() + url_for("org.join", token=_join_token(org, user, current_user))
        session["org_join_link"] = {"org": org.id, "name": user.display_name, "link": secrets_box.encrypt(link)}
        _log("organisation_invitation", account=user.id, target=f"org {org.id}")
        return back
    pending = db.session.scalar(db.select(User).filter_by(orcid=orcid, pending=True))
    if pending is not None and orgs.outside_roles(pending, org):
        # named elsewhere before its first sign-in (a group, a survey, an organisation): its home is not decided here
        return _refused("allow-orcid", de_en("Diese iD hat schon Rollen in einer anderen Organisation. Nach ihrer "
                                             "ersten Anmeldung können Sie die Person einladen; sie tritt dann selbst "
                                             "bei.", "This iD already has roles in another organisation. After its "
                                             "first sign-in you can invite the person; they then join themselves."),
                        "orcid")
    # no account yet: may sign in from now on, with its home here and the type chosen (A21, A22)
    t, spec, problem = _new_type(org)
    if problem:
        return _refused("allow-orcid", problem, "valid_n")
    listed = db.session.get(SignInOrcid, orcid)
    if listed is not None and listed.org_id not in (None, org.id):
        return _refused("allow-orcid", de_en("Diese iD ist schon für eine andere Organisation vorgemerkt.",
                                             "This iD is already noted for another organisation."), "orcid")
    if listed is None:
        listed = SignInOrcid(orcid=orcid, note=(request.form.get("note") or "").strip()[:200], added_by_id=current_user.id)
        db.session.add(listed)
    listed.org_id = org.id
    if t is not None:
        listed.account_type_id, listed.valid_spec = t.id, spec
    listed.kinds = kind_access.clean(_kinds_of_form(org, t))
    db.session.commit()
    _log("organisation_member_added", target=f"org {org.id}", kind=(t.key or str(t.id)) if t is not None else "default")
    flash(de_en("Die iD kann sich jetzt anmelden; ihre Heimat ist dann diese Organisation.",
                "The iD can sign in now; its home will be this organisation."), "success")
    return back


@bp.route("/<int:org_id>/people/invitations", methods=["POST"])
def withdraw_invitation(org_id):
    """Take back an open invitation of the block "Offene Einladungen" (app/org_invites.py)."""
    from app import org_invites
    from app.tables import back_args

    org = g.org
    back = redirect(url_for("org.people", org_id=org.id, **back_args(request.form.get("back")), _anchor="einladungen"))
    again = _fresh("org.people", org_id=org.id)
    if again:
        return again
    name = org_invites.withdraw(org, (request.form.get("key") or "")[:40])
    if name is None:
        flash(de_en("Diese Einladung ist nicht mehr offen.", "This invitation is no longer open."), "error")
        return back
    db.session.commit()
    _log("organisation_invitation_withdrawn", target=f"org {org.id}")
    flash(de_en("Einladung für {n} zurückgezogen.", "Invitation for {n} withdrawn.", n=name), "success")
    return back


@bp.route("/<int:org_id>/people/outside", methods=["POST"])
@autosave.aware
def outside(org_id):
    """Who brings people in from outside (orgs.SCOPES): the organisation's admins make it only stricter than what the
    platform set; "zurückziehen" ends the join links and invitations by e-mail that rest because of it (E12)."""
    org = g.org
    back = redirect(url_for("org.people", org_id=org.id, _anchor="von-aussen"))
    again = _fresh("org.people", org_id=org.id)
    if again:
        return again
    if request.form.get("action") == "withdraw":
        rows = orgs.open_from_outside(org.id)
        for kind, row in rows:
            db.session.delete(row)
        db.session.commit()
        _log("organisation_outside_withdrawn", target=f"org {org.id}", count=len(rows))
        flash(de_en("{n} Links und Einladungen zurückgezogen.", "{n} links and invitations withdrawn.", n=len(rows)),
              "success")
        return back
    scope = request.form.get("invite_scope")
    if scope not in orgs.SCOPES:
        abort(400)
    floor = orgs.platform_scope(org)
    org.invite_scope_own = None if orgs.SCOPES.index(scope) <= orgs.SCOPES.index(floor) else scope
    db.session.commit()
    _log("organisation_outside", target=f"org {org.id}", scope=orgs.scope_of(org))
    flash(de_en("Gespeichert: {s}.", "Saved: {s}.", s=orgs.scope_label(orgs.scope_of(org))), "success")
    return back


@bp.route("/<int:org_id>/people/names", methods=["POST"])
@autosave.aware
def names_choice(org_id):
    """Whether members see each other's names (app/names.py): the organisation's admins decide only when the platform
    left it to them (names_mode "admins", E15)."""
    org = g.org
    back = redirect(url_for("org.people", org_id=org.id, _anchor="namen"))
    again = _fresh("org.people", org_id=org.id)
    if again:
        return again
    if org.names_mode != "admins":
        abort(403)
    org.names_hidden_own = request.form.get("hidden") == "1"
    db.session.commit()
    _log("organisation_names", target=f"org {org.id}", hidden=org.names_hidden_own)
    flash(de_en("Gespeichert.", "Saved."), "success")
    return back


@bp.route("/<int:org_id>/people/new", methods=["POST"])
def new_account(org_id):
    """An account without ORCID at home here (konzept-abteilungen A15, A21), as admin.new_account: the admin enters
    name and address and passes the one-time link on (or it goes by mail). Within the organisation's caps: number of
    accounts, its user types, survey types and the right to create surveys (A16)."""
    from flask import session

    from app import accounts, brand, mail, passwords, secrets_box

    org = g.org
    again = _fresh_add(org)
    if again:
        return again
    name = " ".join(request.form.get("name", "").split())[:200]
    email = passwords.normal_email(request.form.get("email"))
    if not name or not email:
        return _refused("new-account", de_en("Bitte einen Namen und eine gültige E-Mail-Adresse angeben.",
                                             "Please enter a name and a valid email address."),
                        "name" if not name else "email")
    if not accounts.email_free(email):
        return _refused("new-account", de_en("Mit dieser E-Mail-Adresse gibt es schon ein Konto.",
                                             "There is already an account with this email address."), "email")
    t, spec, problem = _new_type(org)
    if problem:
        return _refused("new-account", problem, "valid_n")
    user = accounts.create_local(name, email, "", False, current_user)
    kind_access.store(user, _kinds_of_form(org, t), current_user)
    if request.form.get("may_create") == "1":
        kind_access.give_create(user, current_user)  # within the organisation's cap, which home_cap applies
    account_types.assign(user, t, spec, by=current_user, supervisor=current_user, start=False)  # starts with the password (N4)
    orgs.add(org, user, by=current_user, home=True)
    db.session.commit()
    orgs.forget()
    _log("organisation_member_added", account=user.id, target=f"org {org.id}", kind="local")
    link = brand.public_base() + url_for("auth.set_password", token=passwords.new_link_token(user, current_user))
    db.session.commit()
    if mail.enabled() and request.form.get("send_mail") == "1":
        passwords.mail_link(user, link, reset=False)  # by mail and not shown (M4a); a new one on the account's page
        flash(de_en("Konto für {name} angelegt. Der Einmal-Link ist per E-Mail an {email} unterwegs.",
                    "Account for {name} created. The one-time link is on its way to {email} by email.",
                    name=name, email=email), "success")
    else:
        flash(de_en("Konto für {name} angelegt.", "Account for {name} created.", name=name), "success")
        # shown once on the account's page that follows (as admin._show_link_once), encrypted in the session cookie
        session["org_link"] = {"user": user.id, "link": secrets_box.encrypt(link)}
    return redirect(url_for("org.account", org_id=org.id, user_id=user.id))


def _fresh_add(org):
    """As making an account on the platform: a computer left signed in is not enough; back at the dialog."""
    from app import accounts

    return accounts.need_fresh(url_for("org.people", org_id=org.id) + "#person-hinzufuegen")


def _new_type(org):
    """User type and period of a new person (the dialog "Person hinzufügen"): (type, spec, problem). Only the types of
    the organisation; no account left is a problem too."""
    if orgs.accounts_left(org) == 0:
        return None, "", de_en("Die Organisation hat ihre Höchstzahl an Konten erreicht.",
                               "The organisation has reached its maximum number of accounts.")
    chosen = request.form.get("account_type")
    t = account_types.get(chosen) if chosen else None
    if chosen and t not in orgs.allowed_types(org):
        return None, "", de_en("Diesen Nutzertyp gibt es in der Organisation nicht.",
                               "This user type does not exist in the organisation.")
    spec, error = account_types.spec_from_form(request.form, t) if t is not None else ("", None)
    return t, spec, error


def _kinds_of_form(org, t):
    """The four survey-type selects: "" takes the type's level; never above the organisation's cap (A16)."""
    levels = kind_access.full(t.kinds if t is not None else kind_access.default())
    for k in kind_access.KINDS:
        if request.form.get("kind_" + k) in kind_access.LEVELS:
            levels[k] = request.form.get("kind_" + k)
    return kind_access.capped(levels, kind_access.org_cap(org))


def _refused(key, message, field):
    """The page "Personen" once more with the refused form of "Person hinzufügen" open, what was entered kept and the
    field marked (as admin._refused, W84)."""
    g.form_error = {"key": key, "message": message, "field": field}
    return people(g.org.id), 422


def _target(org, user_id):
    user = db.session.get(User, user_id)
    if user is None or not orgs.manages(current_user, user, org):
        abort(404)
    return user


@bp.route("/<int:org_id>/people/<int:user_id>", methods=["GET", "POST"])
@autosave.aware
def account(org_id, user_id):
    """One account at home here: user type, period, survey types, creating surveys, block, one-time link."""
    org = g.org
    user = _target(org, user_id)
    row = account_types._row(user)
    if request.method == "POST":
        again = _fresh("org.account", org_id=org.id, user_id=user.id)
        if again:
            return again
        action = request.form.get("action")
        if action == "kind":
            kind, lvl = request.form.get("kind"), request.form.get("level")
            cap = kind_access.org_cap(org)
            if kind not in kind_access.KINDS or lvl not in kind_access.LEVELS:
                abort(400)
            if kind_access._RANK[lvl] > kind_access._RANK[cap[kind]]:
                flash(de_en("Höher, als die Organisation erlaubt.", "Higher than the organisation allows."), "error")
            else:
                kind_access.set_level(user, kind, lvl, current_user)
                flash(de_en("{t}: {l}.", "{t}: {l}.", t=kind_access.label(kind), l=kind_access.level_label(lvl)), "success")
        elif action == "account_type":
            t = account_types.get(request.form.get("account_type"))
            if t is not None and t not in orgs.allowed_types(org):
                abort(400)
            account_types.change_type(user, t, current_user)
        elif action == "extend":
            spec, error = account_types.spec_from_form(request.form, None)
            if error:
                flash(error, "error")
                return redirect(url_for("org.account", org_id=org.id, user_id=user.id))
            account_types.extend(user, spec, current_user)
        elif action in ("block", "unblock"):
            from app import accounts

            from app import org_delete

            if action == "unblock" and org_delete.marked(user) is not None:
                flash(de_en("Das Konto ist zur Löschung vorgemerkt; zum Entsperren die Löschung zurücknehmen.",
                            "The account is marked for deletion; take the deletion back to unblock it."), "error")
                return redirect(url_for("org.account", org_id=org.id, user_id=user.id))
            if action == "unblock" and account_types.is_expired(row):
                flash(de_en("Die Laufzeit ist abgelaufen; bitte verlängern, das entsperrt das Konto.",
                            "The period has ended; please extend it, that unblocks the account."), "error")
                return redirect(url_for("org.account", org_id=org.id, user_id=user.id))
            accounts.set_blocked(user, action == "block", current_user)
            if request.form.get("back") is not None:  # the lock in the list of people: back to the same page of it
                from app.tables import back_args

                db.session.commit()
                kind_access.forget()
                flash(de_en("{n} ist gesperrt.", "{n} is blocked.", n=user.display_name) if action == "block" else
                      de_en("{n} ist entsperrt.", "{n} is unblocked.", n=user.display_name), "success")
                return redirect(url_for("org.people", org_id=org.id, **back_args(request.form.get("back")),
                                        _anchor="personen"))
        elif action == "link" and row.email and not user.has_orcid:
            from app import brand, passwords

            token = passwords.new_link_token(user, current_user)  # shown on the page that follows and nowhere else
            row.reset_requested_at = None
            db.session.commit()
            link = brand.public_base() + url_for("auth.set_password", token=token)
            _log("one_time_link", account=user.id, target=f"org {org.id}")
            return render_template("org/account.html", org=org, user=user, row=row, link=link, **_account_context(org, user))
        else:
            abort(400)
        db.session.commit()
        kind_access.forget()
        if action not in ("kind",):
            flash(de_en("Gespeichert.", "Saved."), "success")
        return redirect(url_for("org.account", org_id=org.id, user_id=user.id))
    from flask import session

    from app import secrets_box

    waiting = session.get("org_link")  # the one-time link of an account just made (new_account), shown once
    link = None
    if waiting and waiting.get("user") == user.id:
        session.pop("org_link")
        link = secrets_box.decrypt(waiting.get("link"))
    return render_template("org/account.html", org=org, user=user, row=row, link=link, **_account_context(org, user))


def _account_context(org, user):
    from app import org_delete

    deletion = org_delete.marked(user)
    return {"deletion": deletion, "deletion_refused": org_delete.refusal(current_user, user, org),
            "deletion_due": deletion is not None and (deletion.due_at <= utcnow() or bool(deletion.open_points)), "why_label": org_delete.why_label,
            "other_orgs": [orgs.label(orgs.get(i)) for i in sorted(org_delete.other_orgs(user, org))],
            "levels": kind_access.full(kind_access.stored(user)), "cap": kind_access.org_cap(org),
            "period": account_types.summary(db.session.get(UserLogin, user.id)), "types": orgs.allowed_types(org),
            "current_type": account_types.of(user), "active": "people",
            "blocked": (db.session.get(UserLogin, user.id) or UserLogin()).status == UserLogin.BLOCKED}


@bp.route("/<int:org_id>/people/<int:user_id>/delete", methods=["GET", "POST"])
def delete_account(org_id, user_id):
    """Mark an account at home here for deletion, change what becomes of its things, or take the mark back
    (app/org_delete.py). The account is blocked at once and deleted by the hourly sweep after org_delete.DAYS."""
    from app import accounts, org_delete

    org = g.org
    user = _target(org, user_id)
    row = org_delete.marked(user)
    if request.method == "POST":
        again = _fresh("org.delete_account", org_id=org.id, user_id=user.id)
        if again:
            return again
        if request.form.get("action") == "undo":
            if org_delete.undo(user, current_user):
                flash(de_en("Die Löschung von {n} ist zurückgenommen; das Konto ist wieder offen.",
                            "The deletion of {n} has been taken back; the account is open again.",
                            n=user.display_name), "success")
            return redirect(url_for("org.account", org_id=org.id, user_id=user.id))
        refused = org_delete.refusal(current_user, user, org)
        if refused:
            flash(refused, "error")
            return redirect(url_for("org.account", org_id=org.id, user_id=user.id))
        if row is None and not accounts.name_matches(user, request.form.get("name")):
            flash(de_en("Der eingegebene Name stimmt nicht. Es wurde nichts vorgemerkt.",
                        "The name entered does not match. Nothing was marked."), "error")
            return redirect(url_for("org.delete_account", org_id=org.id, user_id=user.id))
        plan = accounts.plan(user)
        able = org_delete.heirs(org, user)
        choices = org_delete.choices_from_form(request.form, org_delete.items(plan), able)
        _, problems = org_delete.resolve(user, org, choices, plan)
        if problems:  # nothing is marked that the sweep could not carry out: back with what is missing
            for p in problems:
                flash(de_en("„{t}“: {w}.", "“{t}”: {w}.", t=p["title"], w=org_delete.why_label(p["why"])), "error")
            return _delete_page(org, user, row, plan, able, choices), 422
        name = user.display_name
        row = org_delete.mark(user, org, current_user, choices)
        if row.due_at <= utcnow() and org_delete.execute(row):  # was due already: the open points are settled now
            flash(de_en("Das Konto von {n} wurde gelöscht.", "The account of {n} has been deleted.", n=name), "success")
            return redirect(url_for("org.people", org_id=org.id))
        flash(de_en("{n} ist gesperrt und wird am {d} gelöscht. Bis dahin lässt sich das zurücknehmen.",
                    "{n} is blocked and will be deleted on {d}. Until then this can be taken back.",
                    n=name, d=org_delete.when(row.due_at)), "success")
        return redirect(url_for("org.account", org_id=org.id, user_id=user.id))
    refused = org_delete.refusal(current_user, user, org)
    if refused and row is None:
        flash(refused, "error")
        return redirect(url_for("org.account", org_id=org.id, user_id=user.id))
    plan = accounts.plan(user)
    return _delete_page(org, user, row, plan, org_delete.heirs(org, user), row.choices if row is not None else None)


def _delete_page(org, user, row, plan, able, choices):
    from app import org_delete
    from app.models import SurveyMember

    choices = choices or {}
    return render_template("org/delete.html", org=org, user=user, row=row, plan=plan, heirs=able,
                           items=org_delete.items(plan), choices=choices, days=org_delete.DAYS,
                           heir=choices.get("heir") or (able[0].id if len(able) == 1 else None),
                           role_names=SurveyMember.roles(), active="people", why_label=org_delete.why_label)


# --- joining by one's own consent (audit 2026-10-18, AUTHZ-01, AUTHZ-02) --------------------------------------------

def _join_serializer():
    from flask import current_app
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(current_app.secret_key, salt=JOIN_SALT)


def _join_token(org, user, by):
    """A signed invitation for one account to join `org`, valid JOIN_DAYS; nothing is stored."""
    return _join_serializer().dumps({"o": org.id, "u": user.id, "b": by.id})


def _join_of(token):
    """(org, inviter) of a valid invitation for the signed-in account, or None: the inviter must still be an admin of
    the organisation (or a site admin)."""
    from itsdangerous import BadSignature

    try:
        data = _join_serializer().loads(token, max_age=JOIN_DAYS * 86400)
    except (BadSignature, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("u") != current_user.id:
        return None
    org = orgs.get(data.get("o"))
    inviter = db.session.get(User, data.get("b") or 0)
    if org is None or org.key == orgs.GUEST or inviter is None:
        return None
    if not (orgs.is_admin_of(inviter, org.id) or orgs._site_admin(inviter)):
        return None
    return org, inviter


@bp.route("/join/<token>", methods=["GET", "POST"])
def join(token):
    """The invited person decides: join the organisation, or not. A guest's home moves here with it (A5), so its admins
    manage the account from then on, as far as orgs.manages allows."""
    found = _join_of(token)
    if found is None:
        flash(de_en("Diese Einladung gilt nicht (mehr): Sie ist abgelaufen, für ein anderes Konto gedacht, oder wer sie "
                    "erstellt hat, ist kein Admin der Organisation mehr.",
                    "This invitation is not valid (any more): it has expired, is meant for another account, or whoever "
                    "made it is no longer an admin of the organisation."), "error")
        return redirect(url_for("dashboard.index"))
    org, inviter = found
    member = orgs.is_member(current_user, org.id)
    guest = orgs.is_guest(current_user)
    if request.method == "POST" and not member:
        if request.form.get("answer") != "join":
            flash(de_en("Sie sind der Organisation nicht beigetreten.", "You have not joined the organisation."),
                  "success")
            return redirect(url_for("dashboard.index"))
        if guest and orgs.accounts_left(org) == 0:
            flash(de_en("{o} hat ihre Höchstzahl an Konten erreicht.", "{o} has reached its maximum number of accounts.",
                        o=org.name), "error")
            return redirect(url_for("org.join", token=token))
        orgs.add(org, current_user, by=inviter)
        if guest:
            orgs.set_home(current_user, org)
        db.session.commit()
        orgs.forget()
        from app import security_log

        security_log.event("organisation_member_joined", account=current_user.id, by=inviter.id, target=f"org {org.id}",
                           kind="home" if guest else "member")
        flash(de_en("Sie gehören jetzt zur Organisation {o}.", "You now belong to the organisation {o}.",
                    o=orgs.label(org)), "success")
        return redirect(url_for("dashboard.index"))
    return _no_store(render_template("org/join.html", org=org, inviter=inviter, member=member,
                                     guest=guest, token=token))


def _no_store(page):
    """The address of the page is the token: the browser keeps no copy."""
    from flask import current_app

    response = current_app.make_response(page)
    response.headers["Cache-Control"] = "no-store"
    return response


# --- Gruppen -----------------------------------------------------------------------------------------------------------

@bp.route("/<int:org_id>/groups")
def groups(org_id):
    org = g.org
    found = list(db.session.scalars(db.select(WorkGroup).where(orgs.column(WorkGroup.org_id) == org.id)
                                    .order_by(db.func.lower(WorkGroup.name), WorkGroup.id)))
    people = [u for u, _ in orgs.members_of(org)]  # the lead is picked from them (Ralf, 2026-10-16)
    return render_template("org/groups.html", org=org, groups=found, types=orgs.allowed_types(org), people=people,
                           active="groups")


@bp.route("/<int:org_id>/groups", methods=["POST"])
@autosave.aware
def groups_action(org_id):
    org = g.org
    back = redirect(url_for("org.groups", org_id=org.id))
    again = _fresh("org.groups", org_id=org.id)
    if again:
        return again
    from app import groups as groups_mod

    action = request.form.get("action")
    if action == "new":
        name = groups_mod._name(request.form.get("name"))
        lead = (request.form.get("lead") or "").strip()
        picked = request.form.get("lead_user", type=int)
        from app.auth import ORCID_RE

        if not name or not (picked or ORCID_RE.match(lead)):
            flash(de_en("Bitte Name und Leitung angeben: ein Mitglied auswählen oder eine ORCID iD eintragen.",
                        "Please give a name and the lead: choose a member or enter an ORCID iD."), "error")
            return back
        if orgs.group_name_taken(name, org.id):
            flash(orgs.name_taken_said(name), "error")
            return back
        if picked:  # a member chosen from the list wins over a typed ORCID iD
            user = db.session.get(User, picked)
            if user is None:
                abort(400)
            person = user
        else:
            from app import people_keys

            person = people_keys.account(lead)  # an iD before its first sign-in gets a pending account (B2)
            user = person if person is not None and not person.pending else None
        if user is not None and not orgs.is_member(user, org.id):
            flash(orgs.not_in_org(), "error")
            return back
        group = WorkGroup(name=name, created_by_id=current_user.id, org_id=None if org.key == orgs.MAIN else org.id,
                          may_create=org.may_create is not False and request.form.get("members_create") == "1")
        group.members.append(WorkGroupMember(user=person, role="lead",
                                             name=user.name if user else orgs.display_name(lead),
                                             invited_by_id=current_user.id))
        db.session.add(group)
        kind_access.joined(group, user, current_user)  # "darf Befragungen anlegen": "create" once (E3)
        db.session.commit()
        _log("organisation_group_created", target=f"group {group.id}")
        flash(de_en("Gruppe „{g}“ angelegt.", "Group “{g}” created.", g=name), "success")
        return back
    group = db.session.get(WorkGroup, request.form.get("group_id", type=int) or 0)
    if group is None or orgs.of(group) != org.id:
        abort(404)
    if action == "rename":
        name = groups_mod._name(request.form.get("name"))
        if name and orgs.group_name_taken(name, org.id, without=group.id):
            flash(orgs.name_taken_said(name), "error")
            return back
        if name:
            group.name = name
    elif action == "delete":
        groups_mod.remove(group)
        _log("organisation_group_deleted", target=f"group {group.id}")
    elif action == "lead":
        member = next((m for m in group.members if m.id == request.form.get("member_id", type=int)), None)
        if member is None:
            abort(400)
        member.role = "lead"
    elif action == "may_create":
        if request.form.get("value") == "1" and org.may_create is False:
            flash(de_en("Die Organisation lässt niemanden Befragungen anlegen.",
                        "The organisation lets nobody create surveys."), "error")
            return back
        group.may_create = request.form.get("value") == "1"
        kind_access.group_allowed(group, current_user)
        _log("group_may_create", target=f"group {group.id}", kind=str(group.may_create))
    elif action == "kinds_preset":
        wanted = kind_access.full(kind_access.from_form(request.form))
        group.kinds_preset = None if request.form.get("none") == "1" else kind_access.capped(wanted, kind_access.org_cap(org))
        _log("kinds_changed", target=f"group {group.id}", kind=kind_access.encode(group.kinds_preset) or "none")
    elif action == "course":
        from app.timeutil import end_of_local_day_utc, local_today

        t = account_types.get(request.form.get("account_type"))
        if t is None:
            group.account_type_id, group.course_until = None, None
        else:
            if t not in orgs.allowed_types(org):
                abort(400)
            try:
                until = date.fromisoformat(request.form.get("course_until") or "")
            except ValueError:
                until = None
            if until is not None and until < local_today():
                until = None
            if until is None and account_types.needs_input(t.duration):
                flash(de_en("Bitte ein Enddatum ab heute angeben.", "Please give an end date from today on."), "error")
                return back
            group.account_type_id = t.id
            group.course_until = end_of_local_day_utc(until) if until is not None else None
        _log("course_changed", target=f"group {group.id}", kind=(t.key or str(t.id)) if t is not None else "none")
    else:
        abort(400)
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return back


# --- Befragungen -------------------------------------------------------------------------------------------------------

@bp.route("/<int:org_id>/surveys")
def surveys(org_id):
    """The dashboard of the organisation (app/admin_stats.py, only its surveys and people, Ralf 2026-10-17) and its
    surveys: title, kind, status, people, real participations in the period and in all; no content, no answers (A15)."""
    from flask import current_app

    from app import admin_stats
    from app.tables import ROW_SIZES, Listing, by_text

    org = g.org
    period = request.args.get("period") if request.args.get("period") in admin_stats.PERIODS else admin_stats.DEFAULT
    stats = admin_stats.overview(current_app._get_current_object(), period,
                                 admin_stats.read_since(request.args.get("since")), org_id=org.id)
    real = (Response.survey_id == Survey.id, Response.status == Response.COMPLETED, Response.source == "real",
            Response.not_live_extra(),
            Response.is_preview.is_(False))
    done = db.select(db.func.count(Response.id)).where(*real).scalar_subquery()
    in_period = db.select(db.func.count(Response.id)).where(
        *real, Response.completed_at >= stats["window"]["start"]).scalar_subquery()
    first_member = (db.select(by_text(SurveyMember.name)).where(SurveyMember.survey_id == Survey.id)
                    .order_by(SurveyMember.position, SurveyMember.id).limit(1).scalar_subquery())
    # sortable, searchable and in pages of 10, 20 or 50 like the platform's list (Ralf, 2026-10-17)
    listing = Listing("b", {
        "title": by_text(Survey.title),
        "kind": db.case((json_given(Survey.live), 1), (json_given(Survey.interview), 2),
                        (json_given(Survey.sampling), 3), else_=0),
        "status": db.case({Survey.DRAFT: 0, Survey.RUNNING: 1, Survey.CLOSED: 2}, value=Survey.status, else_=3),
        "members": first_member, "period": ([in_period, done], "desc"), "completed": (done, "desc"),
    }, default="period", then=[by_text(Survey.title), Survey.id], anchor="befragungen", sizes=ROW_SIZES, search=[
        Survey.title, Survey.code,
        lambda match: db.exists().where(SurveyMember.survey_id == Survey.id, match(SurveyMember.name))])
    from app import plugins

    # imported analyses of an add-on module are no surveys of the organisation
    base = db.select(Survey, done, in_period).where(orgs.column(Survey.org_id) == org.id, *plugins.counted_surveys())
    rows = listing.rows(base.where(Survey.admin_hidden.is_(False)))
    hidden = db.session.scalar(db.select(db.func.count(Survey.id)).where(orgs.column(Survey.org_id) == org.id,
                                                                         Survey.admin_hidden.is_(True)))
    members = {}
    for sid, name in db.session.execute(db.select(SurveyMember.survey_id, SurveyMember.name).where(
            SurveyMember.survey_id.in_([s.id for s, _, _ in rows])).order_by(SurveyMember.position, SurveyMember.id)):
        members.setdefault(sid, []).append(name)
    return render_template("org/surveys.html", org=org, rows=rows, listing=listing, members=members, hidden=hidden, active="surveys",
                           stats=stats, stats_mod=admin_stats, period=stats["window"]["key"],
                           periods=admin_stats.period_labels())


# --- Konstrukte: the organisation's "Schnellbox" (konzept-konstrukt-schnellbox-2026-10-16.md, K9) ------------------

@bp.route("/<int:org_id>/constructs")
def constructs(org_id):
    """The constructs the organisation recommends: listed in the questionnaire editor of all its members after their
    own favourites (app/shortlist.py); without a list of its own the platform's list holds."""
    from app import shortlist
    from app import constructs as toolbox

    org = g.org
    own = shortlist.keys(org.id)
    platform = shortlist.keys(None)
    return render_template("org/constructs.html", org=org, active="constructs", keep=str(org.id),
                           rows=[toolbox.shown(toolbox.get(k)) for k in own],
                           platform=[toolbox.shown(toolbox.get(k)) for k in platform], max=shortlist.MAX)


# --- Design (Ralf 2026-10-16) ----------------------------------------------------------------------------------------

@bp.route("/<int:org_id>/design")
def design(org_id):
    """The organisation's logo (light and dark) for all its members, and the organisation design, light and dark: the
    standard of the organisation (app/org_designs.py, Ralf 2026-10-19)."""
    from app import org_designs, theme

    org = g.org
    sides = [{"mode": mode, "label": org_designs.mode_label(mode), "design": org_designs.get(org, mode),
              "template": org_designs.get(org, mode, template=True)} for mode in org_designs.MODES]
    return render_template("org/design.html", org=org, logo=theme.org_logo(org.id), folder=theme.org_folder(org.id),
                           sides=sides, sources=org_designs.sources(current_user, org),
                           template_differs=org_designs.template_differs(org), active="design")


@bp.route("/<int:org_id>/design/<any(light, dark):mode>.json")
def design_file(org_id, mode):
    """One side of the organisation design as a design file, to change it elsewhere and upload it again."""
    from app import org_designs

    return _design_download(org_designs.get(g.org, mode), f"{g.org.name}-{mode}")


def _org_editor_urls(org, mode):
    return {name: url_for(f"org.{endpoint}", org_id=org.id, mode=mode) for name, endpoint in (
        ("save", "design_edit_save"), ("sample", "design_edit_sample"), ("check", "design_edit_check"))}


@bp.route("/<int:org_id>/design/<any(light, dark):mode>")
def design_edit(org_id, mode):
    """The organisation design of one side in the design editor (Ralf 2026-10-19: org admins edit it in place), with
    the live preview and the contrast check; the logo stays the organisation's own (above on the page "Design")."""
    from types import SimpleNamespace

    from app import org_designs, theme

    holder = org_designs.Holder(g.org, mode)
    current = theme.clean(holder.theme) or theme.standard()
    return render_template("surveys/design.html", survey=None, design=SimpleNamespace(name=theme.name(current)),
                           org_edit={"org": g.org, "mode": mode, "label": org_designs.mode_label(mode)}, current=current,
                           u=_org_editor_urls(g.org, mode), uid=holder.uid, langs=holder.languages,
                           presets=theme.presets_for(current_user, current["preset"]), can_edit=True, locked=False,
                           designs=[], can_save=False)


@bp.route("/<int:org_id>/design/<any(light, dark):mode>", methods=["POST"])
@autosave.aware
def design_edit_save(org_id, mode):
    from app import org_designs, theme, theme_edit

    org = g.org
    back = redirect(url_for("org.design_edit", org_id=org.id, mode=mode))
    holder = org_designs.Holder(org, mode)
    before = theme.clean(holder.theme) or theme.standard()
    new = theme_edit.from_form(request.form, holder) or theme.standard()
    new = {**new, "scheme": "fixed"}  # each side is its own design; "automatisch" pairs them for the surveys
    new.pop("colors_dark", None)
    found, why = org_designs.check(theme.clean(new) or theme.standard(), mode)
    if found is None:
        flash(why, "error")
        return back
    org_designs.put(org, mode, found, name=theme.name(before))
    db.session.commit()
    _log("organisation_design", target=f"org {org.id}", kind=f"edit {mode}")
    flash(de_en("Organisationsdesign ({side}) gespeichert. Es gilt für Befragungen, die ab jetzt in der Organisation "
                "entstehen; bestehende Befragungen behalten ihr Aussehen.", "Organisation design ({side}) saved. It applies to "
                "surveys made in the organisation from now on; existing surveys keep their look.",
                side=org_designs.mode_label(mode)), "success")
    return back


@bp.route("/<int:org_id>/design/<any(light, dark):mode>/sample")
def design_edit_sample(org_id, mode):
    from app import org_designs, theme_edit

    holder = org_designs.Holder(g.org, mode)
    lang = request.args.get("lang") if request.args.get("lang") in holder.languages else "de"
    return theme_edit.sample_page(holder, request.args, lang)


@bp.route("/<int:org_id>/design/<any(light, dark):mode>/check")
def design_edit_check(org_id, mode):
    from app import org_designs, theme_edit

    return theme_edit.check(org_designs.Holder(g.org, mode), request.args)


def _design_download(data, stem):
    """A design file as a download (also for the template, app/admin.py)."""
    import json
    import re

    from flask import Response as FlaskResponse

    from app import theme

    if data is None:
        abort(404)
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:60] or "design"
    return FlaskResponse(json.dumps(theme.design_file(data), ensure_ascii=False, indent=2), mimetype="application/json",
                         headers={"Content-Disposition": f'attachment; filename="{name}.askurious-design.json"'})


@bp.route("/<int:org_id>/design", methods=["POST"])
@autosave.aware
def design_action(org_id):
    import types

    from app import media, theme

    org = g.org
    back = redirect(url_for("org.design", org_id=org.id))
    action = request.form.get("action")
    if action == "names":
        name = " ".join((request.form.get("name") or "").split())[:orgs.MAX_NAME]
        if not name:
            flash(de_en("Bitte einen Kurznamen angeben.", "Please give a short name."), "error")
            return back
        org.name, org.long_name = name, " ".join((request.form.get("long_name") or "").split())[:200]
        db.session.commit()
        _log("organisation_renamed", target=f"org {org.id}")
        flash(de_en("Namen gespeichert. Neue Beteiligte bekommen den ausführlichen Namen in ihre Befragungstexte; "
                    "bestehende Einträge ändern Sie in der Einwilligung der Befragung.",
                    "Names saved. People newly involved get the full name in their survey texts; existing entries are "
                    "changed in the survey's consent."), "success")
        return back
    if action in ("design_upload", "design_take", "design_remove", "design_template"):
        return _org_design_action(org, action, back)
    if action == "remove_logo":
        current = theme.org_logo(org.id) or {}
        holder = types.SimpleNamespace(uid=theme.org_folder(org.id))
        for key in ("file", "file_dark"):
            if current.get(key):
                media.delete(holder, current[key])
        theme.save_org_logo(org.id, {})
        _log("organisation_logo", target=f"org {org.id}", kind="removed")
        flash(de_en("Logo entfernt. Befragungen, die es schon haben, behalten ihre Kopie.",
                    "Logo removed. Surveys that have it already keep their copy."), "success")
        return back
    if action != "logo":
        abort(400)
    dark = request.form.get("variant") == "dark"
    current = theme.org_logo(org.id) or {}
    f = request.files.get("file")
    if f is None or not f.filename:
        flash(de_en("Bitte eine Datei auswählen.", "Please choose a file."), "error")
    elif dark and not current:
        flash(de_en("Bitte zuerst das Logo für helle Hintergründe hochladen.",
                    "Please upload the logo for light backgrounds first."), "error")
    else:
        holder = types.SimpleNamespace(uid=theme.org_folder(org.id))  # media stores by folder name
        try:
            saved = media.save_logo(holder, f.read(media.LOGO_MAX_BYTES + 1))
        except ValueError as e:
            flash(str(e), "error")
        else:
            old = current.get("file_dark" if dark else "file")
            current.update({"file_dark": saved["file"], "w_dark": saved["w"], "h_dark": saved["h"]} if dark
                           else {"file": saved["file"], "w": saved["w"], "h": saved["h"]})
            theme.save_org_logo(org.id, current)
            if old:
                media.delete(holder, old)
            _log("organisation_logo", target=f"org {org.id}", kind="dark" if dark else "light")
            flash(de_en("Logo gespeichert. Mitglieder übernehmen es im Erscheinungsbild einer Befragung; bestehende "
                        "Befragungen behalten ihres.", "Logo saved. Members apply it in the appearance of a survey; "
                        "existing surveys keep theirs."), "success")
    return back


def _org_design_action(org, action, back):
    """Upload, take over or remove a side of the organisation design, or take over the site admins' template."""
    from app import org_designs

    if action == "design_template":
        taken = org_designs.take_template(org, overwrite=True)
        if not taken:
            flash(de_en("Es gibt keine Vorlage der Plattform.", "There is no template of the platform."), "error")
            return back
        db.session.commit()
        _log("organisation_design", target=f"org {org.id}", kind="template")
        flash(de_en("Vorlage übernommen. Sie gilt für Befragungen, die ab jetzt in der Organisation entstehen; bestehende "
                    "Befragungen behalten ihr Aussehen.", "Template applied. It applies to surveys made in the organisation "
                    "from now on; existing surveys keep their look."), "success")
        return back
    mode = request.form.get("mode")
    if mode not in org_designs.MODES:
        abort(400)
    if action == "design_remove":
        org_designs.put(org, mode, None)
        db.session.commit()
        _log("organisation_design", target=f"org {org.id}", kind=f"removed {mode}")
        flash(de_en("Organisationsdesign ({side}) entfernt. Befragungen, die es schon haben, behalten ihre Kopie.",
                    "Organisation design ({side}) removed. Surveys that have it already keep their copy.",
                    side=org_designs.mode_label(mode)), "success")
        return back
    if action == "design_upload":
        found, why = org_designs.read_upload(request.files.get("file"), mode)
    else:
        source = org_designs.from_source(current_user, org, request.form.get("source"))
        found, why = (org_designs.check(source, mode) if source is not None
                      else (None, de_en("Bitte ein Farbschema oder Design auswählen.", "Please choose a colour scheme or design.")))
    if found is None:
        flash(why, "error")
        return back
    org_designs.put(org, mode, found)
    db.session.commit()
    _log("organisation_design", target=f"org {org.id}", kind=f"{action[7:]} {mode}")
    flash(de_en("Organisationsdesign ({side}) gespeichert. Es gilt für Befragungen, die ab jetzt in der Organisation "
                "entstehen, und steht allen Mitgliedern zur Auswahl; bestehende Befragungen behalten ihr Aussehen.",
                "Organisation design ({side}) saved. It applies to surveys made in the organisation from now on and all "
                "members can choose it; existing surveys keep their look.", side=org_designs.mode_label(mode)), "success")
    return back


# --- Nutzertypen (Ralf 2026-10-16: per organisation, each starts with "Mitarbeitende" only) ----------------------------

@bp.route("/<int:org_id>/types")
def types(org_id):
    from app import lang

    org = g.org
    found = account_types.all_types(org_id=org.id)
    have = {t.name_de for t in found}
    return render_template("org/types.html", org=org, types=found, active="types",
                           counts={t.id: account_types.count_of(t) for t in found},
                           templates=[{**t, "label": t["name_en"] if lang.english() else t["name_de"]}
                                      for t in account_types.TEMPLATES if t["name_de"] not in have])


@bp.route("/<int:org_id>/types/<int:type_id>")
def type_page(org_id, type_id):
    org = g.org
    t = account_types.get(type_id)
    if t is None or account_types.org_of(t) != org.id:
        abort(404)
    left_out = len(account_types.exempt_of(t, current_user))  # changes stop short of them (orgs.manages)
    return render_template("org/type.html", org=org, t=t, count=account_types.count_of(t), active="types",
                           exempt=account_types.exempt_said(left_out) if left_out else "")


@bp.route("/<int:org_id>/types", methods=["POST"])
@autosave.aware
def types_action(org_id):
    org = g.org
    again = _fresh("org.types", org_id=org.id)
    if again:
        return again
    for message, category in account_types.form_action(request.form, org.id, current_user):
        flash(message, category)
    _log("account_type_changed", target=f"org {org.id}", kind=(request.form.get("action") or "")[:40])
    return redirect(url_for("org.types", org_id=org.id))
