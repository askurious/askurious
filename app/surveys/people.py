"""Tab Personen: co-owners, their roles, group access and the showcase."""
from flask import abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app import orcid_api, people_keys
from app.auth import ORCID_RE
from app.extensions import db
from app.ids import db_id
from app.lang import de_en
from app.models import Contact, SurveyGroup, SurveyMember, User, UserLogin, WorkGroup
from app.surveys.base import ORCID_IN_TEXT, _add_member, _back_to, _remember_contact, bp, get_survey


# --- co-owners ---------------------------------------------------------------


def people_keys_of(rows):
    """The account keys (ORCID iD or "q-...") of member or share rows, what the people search leaves out (its
    results carry keys: an iD from the registry may have no account yet)."""
    from app import people_keys

    return {people_keys.key_for(r.user) for r in rows if r.user is not None}


def _search_people(q, survey):
    return search_people(q, people_keys_of(survey.members))


def search_people(q, existing):
    """Members of the own groups first, then contacts, registered users of one's own organisations and shared
    surveys, and the public ORCID registry; without the iDs in `existing`."""
    from app import groups, people_keys

    seen, results = set(existing), []

    def add(orcid, name, affiliation, source):
        if orcid and orcid not in seen:
            seen.add(orcid)
            results.append({"orcid": orcid, "name": name, "affiliation": affiliation, "source": source})

    m = ORCID_IN_TEXT.search(q)
    if m:
        found = orcid_api.lookup(m.group(1))
        if found:
            add(found["orcid"], found["name"], "", "orcid")
        return results
    from app import passwords

    if passwords.normal_email(q):
        # a complete address finds exactly the account that signs in with it; addresses are never shown or
        # searched by parts. Only accounts a site admin made, who entered the address: an ORCID account picks
        # its sign-in name itself, unverified, and could take a colleague's address to be invited in their place.
        owner = db.session.scalar(db.select(User).join(UserLogin, UserLogin.user_id == User.id)
                                  .filter(UserLogin.email == passwords.normal_email(q)))
        if owner is not None and not owner.has_orcid:
            add(people_keys.key_for(owner), owner.name, "", "user")
        return results

    for orcid, name in groups.colleagues(current_user, q)[:10]:
        add(orcid, name, "", "group")
    # "%" and "_" are letters here, not wildcards: "%" listed every account (security audit 2026-10-06, S23)
    for c in db.session.scalars(
        db.select(Contact).filter(Contact.owner_id == current_user.id, Contact.name.icontains(q, autoescape=True))
        .order_by(Contact.name, Contact.id).limit(10)
    ):
        add(people_keys.key_for(c.user) if c.user is not None else c.orcid, c.name, "", "contact")  # no account: the iD noted
    if len(q) >= 3:  # registered accounts only from three letters on: no listing of all names letter by letter
        # and only those of one's own organisations and of shared surveys (app/orgs.py, A11): other organisations
        # find a person by the full iD (above), not by name
        from app import orgs

        mine = db.select(SurveyMember.survey_id).filter(SurveyMember.user_id == current_user.id)
        known = db.or_(User.id.in_(orgs.user_ids_in(orgs.searchable_ids(current_user))),
                       User.id.in_(db.select(SurveyMember.user_id).filter(SurveyMember.survey_id.in_(mine))))
        for u in db.session.scalars(db.select(User).filter(User.name.icontains(q, autoescape=True), known, User.pending.is_(False))
                                    .order_by(User.name, User.id).limit(10)):
            add(people_keys.key_for(u), u.name, orgs.affiliation_of(u), "user")
    if len(q) >= 3:
        for r in orcid_api.search(q):
            add(r["orcid"], r["name"], r["affiliation"], "orcid")
    return results


def search_to_share(q, existing):
    """search_people for sharing a design or a linked element (S8): the same search; an ORCID iD that the registry
    does not answer for (e.g. offline) is still offered as it is, as the field for the iD did before."""
    results = search_people(q, existing)
    m = ORCID_IN_TEXT.search(q)
    if not results and m and m.group(1) not in existing:
        results.append({"orcid": m.group(1), "name": "", "affiliation": "", "source": "orcid"})
    return results


@bp.route("/<sid:survey_id>/members", methods=["POST"])
@login_required
def add_member(survey_id):
    from app import accounts, auth

    survey = get_survey(survey_id)
    orcid = request.form.get("orcid", "")
    local = accounts.local_account(orcid)
    if not ORCID_RE.match(orcid) and local is None:
        abort(400)
    known = people_keys.account(orcid, create=False)
    already = known is not None and any(m.user_id == known.id for m in survey.members)
    if not auth.may_admit(current_user, orcid) and not already:
        flash(auth.not_admitted(), "error")  # being added lets a person sign in (rechte-uebersicht-konzept.md H3)
        return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")
    from app import orgs

    if not orgs.may_invite_across(survey, known or orcid) and not already:
        flash(orgs.not_across(), "error")  # the switches of both organisations (app/orgs.py, A10)
        return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")
    if not orgs.may_bring_in(current_user, orgs.of(survey), known or orcid) and not already:
        flash(orgs.not_brought_in(orgs.of(survey)), "error")  # who brings in people from outside (orgs.SCOPES)
        return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")
    name = local.name if local else request.form.get("name", "").strip()[:200]
    if not name and not local:
        found = orcid_api.lookup(orcid)
        if not found:
            flash(de_en("Diese ORCID iD gibt es nicht. Bitte die 16 Ziffern prüfen (Form 0000-0000-0000-0000).",
                        "This ORCID iD does not exist. Please check the 16 digits (form 0000-0000-0000-0000)."), "error")
            return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")
        name = found["name"]
    role = request.form.get("role", "editor")
    signed_up = known if known is not None and not known.pending else None
    affiliation = orgs.affiliation_of(signed_up) if signed_up is not None else ""  # the organisation, no institution
    user = people_keys.account(orcid, name)  # a pending account for an iD that never signed in
    added = _add_member(survey, user, name, affiliation, role=role)
    if added:
        _remember_contact(user, name)
        db.session.commit()
        from app import invites

        invites.notice(signed_up, current_user, survey=survey, role=added.role)  # a mail, if switched on (M5a)
        names = SurveyMember.roles()
        flash(de_en("{name} ist jetzt beteiligt (Rolle „{role}“).", "{name} is now involved (role “{role}”).",
                    name=name or de_en("Person", "Person"), role=names.get(role, names["editor"])[0]), "success")
    return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")


@bp.route("/<sid:survey_id>/members/<int:member_id>/move/<direction>", methods=["POST"])
@login_required
def move_member(survey_id, member_id, direction):
    survey = get_survey(survey_id)
    members = list(survey.members)
    i = next((n for n, m in enumerate(members) if m.id == member_id), None)
    if i is None:
        abort(404)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(members):
        members[i], members[j] = members[j], members[i]
        for n, m in enumerate(members):
            m.position = n
        db.session.commit()
    return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")


def _admins_locked(survey):
    """How many admins the survey has, read again with its member rows locked (PostgreSQL; SQLite writes one at a
    time anyway): two admins demoting or removing each other at once can no longer leave none (code review
    2026-10-15, H6). The lock holds until the request commits."""
    rows = db.session.scalars(db.select(SurveyMember).where(SurveyMember.survey_id == survey.id).with_for_update()
                              .execution_options(populate_existing=True)).all()
    return sum(1 for m in rows if m.role == "admin")


@bp.route("/<sid:survey_id>/members/<int:member_id>/remove", methods=["POST"])
@login_required
def remove_member(survey_id, member_id):
    survey = get_survey(survey_id, need="viewer")  # everyone may leave; removing others needs admin
    member = next((m for m in survey.members if m.id == member_id), None)
    if member is None:
        abort(404)
    leaving_self = member is survey.member_of(current_user)
    if not leaving_self:
        get_survey(survey_id, need="admin")
    if len(survey.members) == 1:
        flash(de_en("Die letzte Person kann nicht entfernt werden.", "The last person cannot be removed."), "error")
        return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")
    if member.role == "admin" and _admins_locked(survey) <= 1:
        flash(de_en("Die letzte Admin-Person kann nicht entfernt werden. Bitte zuerst jemand anderen zum Admin machen.",
                    "The last admin cannot be removed. Please make someone else an admin first."), "error")
        return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")
    if member.is_former:
        survey.members.remove(member)  # a kept name the admins take out deliberately
        for n, m in enumerate(survey.members):
            m.position = n
    else:
        from app import accounts

        accounts.leave_survey(survey, member)
    db.session.commit()
    if leaving_self:
        flash(de_en("Sie haben die Befragung verlassen.", "You have left the survey."), "success")
        return redirect(url_for("dashboard.index"))
    return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")


@bp.route("/<sid:survey_id>/members/<int:member_id>/role", methods=["POST"])
@login_required
def set_member_role(survey_id, member_id):
    survey = get_survey(survey_id)
    member = next((m for m in survey.members if m.id == member_id), None)
    role = request.form.get("role", "")
    if member is None or role not in SurveyMember.ROLES:
        abort(404)
    if member.is_former:
        flash(de_en("Diese Person ist nicht mehr beteiligt. Der Eintrag nennt nur noch den Namen und kann keine Rolle bekommen.",
                    "This person is no longer involved. The entry only keeps the name and cannot get a role."), "error")
    elif member.role == "admin" and role != "admin" and _admins_locked(survey) <= 1:
        flash(de_en("Es muss mindestens eine Admin-Person geben. Bitte zuerst jemand anderen zum Admin machen.",
                    "There must be at least one admin. Please make someone else an admin first."), "error")
    else:
        if member.role == "admin" and role != "admin" and member.user_id:
            from app import invites

            invites.revoke_by(member.user_id, survey_id=survey.id)
        member.role = role
        db.session.commit()
        flash(de_en("{name}: Rolle „{role}“.", "{name}: role “{role}”.", name=member.display_name,
                    role=SurveyMember.roles()[role][0]), "success")
    return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")


# --- lifecycle -------------------------------------------------------------------


# --- groups with access (app/access.py) -------------------------------------------------------------
# Like the co-owners, only admins of the survey decide about them. A group gets "viewer" or "editor" (with the raw
# data; Ralf, 2026-10-05), never "admin", and its members do not become co-owners (authors).

GROUP_ROLE_NOTE = {"viewer": "ansehen", "editor": "bearbeiten und ihre Daten herunterladen"}
GROUP_ROLE_NOTE_EN = {"viewer": "view", "editor": "edit and download its data"}


def _group_link(survey):
    link = next((l for l in survey.group_links if l.id == request.form.get("link", type=int)), None)
    if link is None:
        abort(404)
    return link


@bp.route("/<sid:survey_id>/access-groups", methods=["POST"])
@login_required
def add_group_access(survey_id):
    from app import access

    survey = get_survey(survey_id)
    back = _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")
    group = db.session.get(WorkGroup, request.form.get("group", type=db_id) or 0)
    role = request.form.get("role") if request.form.get("role") in access.GROUP_ROLES else "viewer"
    from app import orgs

    if group is None or group.member_of(current_user) is None:  # only groups one is in (others stay invisible)
        flash(de_en("Bitte eine Ihrer Gruppen wählen.", "Please choose one of your groups."), "error")
    elif not orgs.same(group, survey):  # only within the survey's organisation (app/orgs.py, A8)
        flash(orgs.fenced_out(), "error")
    elif any(l.group_id == group.id for l in survey.group_links):
        flash(de_en("Diese Gruppe hat schon Zugriff.", "This group already has access."), "error")
    else:
        survey.group_links.append(SurveyGroup(group_id=group.id, role=role, added_by_id=current_user.id))
        db.session.commit()
        flash(de_en("Die Gruppe „{name}“ kann die Befragung jetzt {what}. Das gilt für alle Mitglieder, auch künftige; "
                    "in der Autorenliste stehen sie nicht.",
                    "The group “{name}” can now {what} the survey. This applies to all members, also future ones; "
                    "they are not in the author list.", name=group.name,
                    what=de_en(GROUP_ROLE_NOTE[role], GROUP_ROLE_NOTE_EN[role])), "success")
    return back


@bp.route("/<sid:survey_id>/access-groups/role", methods=["POST"])
@login_required
def set_group_access(survey_id):
    from app import access

    survey = get_survey(survey_id)
    link = _group_link(survey)
    role = request.form.get("role", "")
    if role not in access.GROUP_ROLES:
        flash(de_en("Eine Gruppe kann eine Befragung ansehen oder bearbeiten; Admin wird nur, wer als Person beteiligt ist.",
                    "A group can view or edit a survey; only someone involved as a person becomes an admin."), "error")
        return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")
    link.role = role
    db.session.commit()
    flash(de_en("Gruppe „{name}“: Rolle „{role}“.", "Group “{name}”: role “{role}”.", name=link.group.name,
                role=SurveyMember.roles()[role][0]), "success")
    return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")


@bp.route("/<sid:survey_id>/access-groups/remove", methods=["POST"])
@login_required
def remove_group_access(survey_id):
    survey = get_survey(survey_id)
    from app import audit

    link = _group_link(survey)
    name = link.group.name
    audit.note(f"group={link.group_id} {name[:40]}")  # which group: the link id of the form is gone (audit 2026-10-09, A3)
    survey.group_links.remove(link)
    db.session.commit()
    flash(de_en("Die Gruppe „{name}“ hat keinen Zugriff mehr. Wer zusätzlich als Person beteiligt ist, behält seinen.",
                "The group “{name}” no longer has access. Anyone also involved as a person keeps theirs.", name=name), "success")
    return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#members")


@bp.route("/<sid:survey_id>/showcase", methods=["POST"])
@login_required
def set_showcase(survey_id):
    """Beispielbefragung on or off: every signed-in researcher may then view the survey (app/access.py). Only site
    admins who are admins of the survey decide it, so nobody can put something into everyone's list
    (demo-befragungen-konzept.md, 4a); the access log writes it down."""
    from app import twofactor

    survey = get_survey(survey_id, need="admin")
    if not twofactor.admin_rights(current_user):  # with the second factor while ADMIN_TOTP is on (audit 2026-10-11, M7)
        abort(403)
    from app import constructs

    if request.form.get("state") == "on" and constructs.restricted_keys(survey):
        flash(constructs.permission_notice(), "error")
        return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#showcase")
    survey.showcase = request.form.get("state") == "on"
    db.session.commit()
    if survey.showcase:
        flash(de_en("„{title}“ ist jetzt eine Beispielbefragung: Alle angemeldeten Forschenden können sie ansehen und "
                    "als Kopie übernehmen, aber nicht ändern oder kommentieren.",
                    "“{title}” is now an example survey: all signed-in researchers can view it and take a copy, but "
                    "not change or comment on it.", title=survey.title), "success")
    else:
        flash(de_en("„{title}“ ist keine Beispielbefragung mehr; sehen kann sie nur noch, wer beteiligt ist.",
                    "“{title}” is no longer an example survey; only those involved can see it.", title=survey.title),
              "success")
    return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#showcase")


@bp.route("/<sid:survey_id>/organisation", methods=["POST"])
@login_required
def move_organisation(survey_id):
    """Move the survey into another organisation of whoever is its admin (app/orgs.py, A6): groups of the old one lose
    their access (a group shares only within its organisation), personal entries stay, an example of the old
    organisation stops being one."""
    from app import orgs

    survey = get_survey(survey_id, need="admin")
    back = _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#organisation")
    target = orgs.get(request.form.get("org"))
    if target is None or target not in orgs.creatable(current_user):
        flash(de_en("Bitte eine Ihrer Organisationen wählen.", "Please choose one of your organisations."), "error")
        return back
    if target.id == orgs.of(survey):
        return back
    gone = [link.group.name for link in survey.group_links if orgs.of(link.group) != target.id]
    survey.group_links = [link for link in survey.group_links if orgs.of(link.group) == target.id]
    survey.org_id = None if target.key == orgs.MAIN else target.id
    survey.org_showcase = False
    db.session.commit()
    from app import security_log

    security_log.event("survey_moved", by=current_user.id, target=f"survey {survey.id}", reason=f"org {target.id}")
    said = de_en("„{t}“ gehört jetzt zur Organisation „{o}“.", "“{t}” now belongs to the organisation “{o}”.",
                 t=survey.title, o=target.name)
    if gone:
        said += " " + de_en("Keinen Zugriff mehr haben die Gruppen {g}.", "The groups {g} no longer have access.",
                            g=", ".join(gone))
    flash(said, "success")
    return back


@bp.route("/<sid:survey_id>/org-showcase", methods=["POST"])
@login_required
def set_org_showcase(survey_id):
    """Example survey of its organisation on or off (A13): its members may then view it and take a copy. Admins of
    the survey who are admins of its organisation decide it."""
    from app import orgs

    survey = get_survey(survey_id, need="admin")
    if not orgs.is_admin_of(current_user, orgs.of(survey)):
        abort(403)
    from app import constructs

    if request.form.get("state") == "on" and constructs.restricted_keys(survey):
        flash(constructs.permission_notice(), "error")
        return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#organisation")
    survey.org_showcase = request.form.get("state") == "on"
    db.session.commit()
    flash(de_en("„{t}“ ist jetzt eine Beispielbefragung der Organisation.",
                "“{t}” is now an example survey of the organisation.", t=survey.title) if survey.org_showcase else
          de_en("„{t}“ ist keine Beispielbefragung der Organisation mehr.",
                "“{t}” is no longer an example survey of the organisation.", t=survey.title), "success")
    return _back_to(survey, url_for("surveys.detail", survey_id=survey.id) + "#organisation")
