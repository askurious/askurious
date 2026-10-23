"""Groups: named lists of researchers, to share things with all of them at once.

A group owns nothing. Surveys and designs stay with persons; a group only carries access to what was shared
with it: surveys (app/access.py) and designs (app/designs.py). Deleting a group or leaving one therefore loses nothing, it only ends
the access that came through the group. Members are stored by ORCID iD and added directly, like the co-owners
of a survey; everyone can leave at any time. Two roles: "lead" manages the group, "member" sees it.

Who may create groups: who may create surveys (auth.may_create_surveys), and site admins without becoming a member,
naming the first lead (app/admin.py group_new, gruppen-freischaltung-konzept.md). Being a member of a group lets a
person sign in, like an invitation to a survey does (auth._may_sign_in). Two things only a site admin can give
a group (app/admin.py): that its members may create surveys of their own, and the theme of an institution.
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, kind_access, orcid_api, orgs, people_keys
from app.auth import ORCID_RE, may_admit, may_create_surveys, not_admitted
from app.extensions import db
from app.lang import de_en
from app.models import (Construct, ConstructGroupShare, Design, DesignGroupShare, Survey, SurveyGroup, SurveyMember, User,
                        UserLogin, WorkGroup, WorkGroupMember)

bp = Blueprint("groups", __name__, url_prefix="/groups")
MAX_NAME = 80
MAX_DESCRIPTION = 500
MAX_GROUPS = 50     # memberships per researcher
MAX_MEMBERS = 200   # per group


def of(user, within=None):
    """The groups this researcher is a member of, by name; with `within` (a survey, design, linked element or
    research project) only those of its organisation, the ones it can be shared with (app/orgs.py, A8)."""
    query = (db.select(WorkGroup).join(WorkGroupMember).filter(WorkGroupMember.user_id == user.id)
             .order_by(db.func.lower(WorkGroup.name), WorkGroup.id))
    if within is not None:
        from app import orgs

        query = query.filter(orgs.column(WorkGroup.org_id) == orgs.of(within))
    return list(db.session.scalars(query))


def ids_of(user):
    """Subquery: ids of the groups this researcher is a member of."""
    return db.select(WorkGroupMember.group_id).filter(WorkGroupMember.user_id == user.id)


def count_for(user_id):
    """In how many groups this account is (MAX_GROUPS)."""
    if user_id is None:
        return 0
    return db.session.scalar(db.select(db.func.count(WorkGroupMember.id)).filter_by(user_id=user_id))


def count_of(key):
    """count_for the account with this key (ORCID iD or "q-..."), for callers that still hold a key."""
    user = people_keys.account(key, create=False)
    return count_for(user.id) if user is not None else 0


def in_nav(user):
    """Show "Gruppen" in the header? Only to those who are in a group or may create one."""
    return may_create_surveys(user) or count_for(user.id) > 0


def colleagues(user, like=None):
    """Members of this researcher's groups (without the researcher), for the people search: (account key, name)."""
    query = (db.select(WorkGroupMember).filter(WorkGroupMember.group_id.in_(ids_of(user)),
                                               WorkGroupMember.user_id.is_not(None),
                                               WorkGroupMember.user_id != user.id)
             .order_by(WorkGroupMember.id))
    found = {}
    for m in db.session.scalars(query):
        name = m.display_name
        if m.user_id not in found and (like is None or like.lower() in name.lower()):
            found[m.user_id] = (people_keys.key_for(m.user), name)
    return list(found.values())


def label(group):
    """A group's name with its lead, wherever a group is named to people who may not know it: names are free
    and not unique."""
    leads = [m.display_name for m in group.leads()]
    if not leads:
        return group.name
    return de_en("{name} (Leitung: {leads}{more})", "{name} (lead: {leads}{more})", name=group.name, leads=", ".join(leads[:2]),
                 more=de_en(" u. a.", " et al.") if len(leads) > 2 else "")


def _name(value):
    return " ".join(str(value or "").split())[:MAX_NAME]


def _group(group_id, lead=False):
    """The group if the current user is a member (with lead=True: one of its leads); to everyone else it does
    not exist."""
    group = db.session.get(WorkGroup, group_id)
    if group is None or group.member_of(current_user) is None:
        abort(404)
    if lead and not group.led_by(current_user):
        flash(de_en("Das kann nur die Leitung der Gruppe.", "Only the lead of the group can do that."), "error")
        abort(redirect(url_for("groups.detail", group_id=group.id)))
    return group


def _back(group, anchor=""):
    return redirect(url_for("groups.detail", group_id=group.id) + anchor)


def end_shares_of(group, key):
    """end_shares_of_user for the account with this key (ORCID iD or "q-..."), for callers that still hold a key."""
    return end_shares_of_user(group, people_keys.account(key, create=False))


def end_shares_of_user(group, owner):
    """Someone leaves the group (also by deleting the account): what this person shared with it is no longer
    shared (surveys that already use a design or a linked element keep it)."""
    if owner is None:
        return []
    for share in list(group.design_shares):
        if share.design.owner_id == owner.id:
            db.session.delete(share)
    for share in list(group.construct_shares):  # linked elements this person made (only the maker shares them)
        if db.session.get(Construct, share.construct_id).created_by_id == owner.id:
            group.construct_shares.remove(share)
    # surveys this person shared with the group: no longer shared, unless another admin of the survey is still in
    # the group (who could share it again at once). Returns the titles of the surveys that stay shared for that reason
    staying = {m.user_id for m in group.members if m.user_id is not None and m.user_id != owner.id}
    kept = []
    for link in list(group.survey_links):
        if link.added_by_id != owner.id:
            continue
        if any(m.role == "admin" and m.user_id in staying for m in link.survey.members):
            kept.append(link.survey.title)
        else:
            group.survey_links.remove(link)
    return kept


def remove(group):
    """Delete a group: memberships and shares go, the shared things stay with their owners. Research projects
    (AInstein) name the groups they are shared with in a list (ResearchProject.shares): the group goes from there too."""
    from app.models import MemberInvite, ResearchProject

    db.session.execute(db.delete(MemberInvite).where(MemberInvite.group_id == group.id))  # open invitations (app/invites.py)
    from app.models import GroupJoinLink

    db.session.execute(db.delete(GroupJoinLink).where(GroupJoinLink.group_id == group.id))  # its join links (app/joinlinks.py)
    for project in db.session.scalars(db.select(ResearchProject).where(ResearchProject.shares.isnot(None))):
        shares = project.shares if isinstance(project.shares, list) else []
        kept = [s for s in shares if not (isinstance(s, dict) and s.get("kind") == "group" and s.get("group_id") == group.id)]
        if len(kept) != len(shares):
            project.shares = kept
    db.session.delete(group)


@bp.route("/")
@login_required
def index():
    return render_template("groups/index.html", groups=of(current_user), may_create=may_create_surveys(current_user),
                           max_groups=MAX_GROUPS)


@bp.route("/new", methods=["POST"])
@login_required
def new():
    if not may_create_surveys(current_user):
        abort(403)
    name = _name(request.form.get("name"))
    if not name:
        flash(de_en("Bitte einen Namen für die Gruppe angeben.", "Please give the group a name."), "error")
    elif count_for(current_user.id) >= MAX_GROUPS:
        flash(de_en("Mehr als {n} Gruppen je Person sind nicht möglich.", "More than {n} groups per person are not possible.", n=MAX_GROUPS), "error")
    elif orgs.group_name_taken(name, orgs.for_new(current_user)):  # unique within the organisation (2026-10-16)
        flash(orgs.name_taken_said(name), "error")
    else:
        group = WorkGroup(name=name, description=(request.form.get("description") or "").strip()[:MAX_DESCRIPTION],
                          created_by_id=current_user.id)
        group.members.append(WorkGroupMember(user_id=current_user.id, name=current_user.name,
                                             role="lead", invited_by_id=current_user.id))
        db.session.add(group)
        db.session.commit()
        flash(de_en("Gruppe „{name}“ angelegt. Sie leiten sie; fügen Sie jetzt Mitglieder hinzu.",
                    "Group “{name}” created. You lead it; now add members.", name=name), "success")
        return _back(group)
    return redirect(url_for("groups.index"))


@bp.route("/<int:group_id>")
@login_required
def detail(group_id):
    from app.surveys import people_keys_of, search_people

    group = _group(group_id)
    lead = group.led_by(current_user)
    q = request.args.get("q", "").strip()
    results = search_people(q, people_keys_of(group.members)) if q and lead else None
    designs = list(db.session.scalars(
        db.select(Design).join(DesignGroupShare).filter(DesignGroupShare.group_id == group.id).order_by(Design.name)))
    linked = list(db.session.scalars(
        db.select(Construct).join(ConstructGroupShare).filter(ConstructGroupShare.group_id == group.id).order_by(Construct.name)))
    from app import joinlinks

    links = joinlinks.open_for(group) if lead else []
    join = {"may": joinlinks.may_make(current_user, group), "links": links,
            "paused": {link.id for link in links if joinlinks.paused(link, group)},  # A1 of the audit 2026-10-10
            "new": joinlinks.take_shown(group), "days": joinlinks.DAYS, "default_days": joinlinks.DEFAULT_DAYS,
            "default_uses": joinlinks.DEFAULT_USES} if lead else None
    from app import account_types, bulk_invites

    # a course (app/account_types.py): its type and end, each member's end, who the lead may give a one-time link
    course = None
    if group.account_type_id is not None:
        ids = [m.user_id for m in group.members if m.user_id]
        rows = {r.user_id: r for r in db.session.scalars(db.select(UserLogin).where(UserLogin.user_id.in_(ids)))}
        course = {"type": account_types.get(group.account_type_id), "spec": account_types.course_spec(group),
                  "ends": {uid: account_types.summary(r) for uid, r in rows.items()} if lead else {},
                  "links": {u.id for u in bulk_invites.course_accounts(group)} if lead else set(),
                  "list": bulk_invites.may_invite(current_user, group)}
    return render_template("groups/detail.html", group=group, lead=lead, me=group.member_of(current_user), q=q, join=join,
                           course=course,
                           results=results, designs=designs, linked=linked, offer=_offer(group),
                           makers={u.id: u.display_name for u in db.session.scalars(
                               db.select(User).filter(User.id.in_({c.created_by_id for c in linked})))},
                           roles=WorkGroupMember.roles(), max_members=MAX_MEMBERS)


def _offer(group):
    """What the current user may still share with the group from its page (UI audit 2026-10-04, R95): surveys in
    which they are admin, their own designs and the linked elements they made, each not shared with it yet."""
    linked_surveys = {l.survey_id for l in group.survey_links}
    surveys = [s for s in db.session.scalars(
        db.select(Survey).join(SurveyMember).filter(SurveyMember.user_id == current_user.id, SurveyMember.role == "admin")
        .order_by(Survey.title)) if s.id not in linked_surveys and kind_access.edits(current_user, s.kind)]
    shared_designs = {s.design_id for s in group.design_shares}
    designs = [d for d in db.session.scalars(db.select(Design).filter_by(owner_id=current_user.id).order_by(Design.name))
               if d.id not in shared_designs]
    shared_linked = {s.construct_id for s in group.construct_shares}
    from app import linked as linked_mod
    elements = [c for c in db.session.scalars(
        db.select(Construct).filter_by(created_by_id=current_user.id, status=linked_mod.STATUS).order_by(Construct.name))
        if c.id not in shared_linked]
    from app import orgs  # only what belongs to the group's organisation can be shared with it (A8)

    home = orgs.of(group)
    return {k: [x for x in v if orgs.of(x) == home] for k, v in
            {"survey": surveys, "design": designs, "linked": elements}.items()}


@bp.route("/<int:group_id>/share", methods=["POST"])
@login_required
def share(group_id):
    """Share a survey (as admin), an own design or an own linked element with the group from its page; the same as
    sharing it from the survey, the design or the element."""
    group = _group(group_id)
    kind, thing = request.form.get("kind"), request.form.get("id", type=int) or 0
    offer = _offer(group)
    chosen = next((x for x in offer.get(kind, []) if x.id == thing), None)
    if chosen is None:
        flash(de_en("Bitte etwas wählen, das Sie teilen dürfen und das noch nicht geteilt ist.",
                    "Please choose something you may share that is not shared yet."), "error")
        return _back(group)
    if kind == "survey":
        chosen.group_links.append(SurveyGroup(group_id=group.id, role="viewer", added_by_id=current_user.id))
        anchor, what = "#surveys", de_en("Die Befragung „{t}“", "The survey “{t}”", t=chosen.title)
    elif kind == "design":
        chosen.group_shares.append(DesignGroupShare(group_id=group.id))
        anchor, what = "#designs", de_en("Das Design „{t}“", "The design “{t}”", t=chosen.name)
    else:
        chosen.group_shares.append(ConstructGroupShare(group_id=group.id))
        anchor, what = "#linked", de_en("Das verknüpfte Element „{t}“", "The linked element “{t}”", t=chosen.name)
    db.session.commit()
    flash(de_en("{what} ist jetzt mit der Gruppe „{g}“ geteilt.", "{what} is now shared with the group “{g}”.", what=what, g=group.name),
          "success")
    return _back(group, anchor)


@bp.route("/<int:group_id>/edit", methods=["POST"])
@login_required
@autosave.aware
def edit(group_id):
    group = _group(group_id, lead=True)
    name = _name(request.form.get("name"))
    if not name:
        flash(de_en("Bitte einen Namen für die Gruppe angeben.", "Please give the group a name."), "error")
    elif orgs.group_name_taken(name, group.org_id, without=group.id):
        flash(orgs.name_taken_said(name), "error")
    else:
        group.name = name
        group.description = (request.form.get("description") or "").strip()[:MAX_DESCRIPTION]
        db.session.commit()
        flash(de_en("Gruppe gespeichert.", "Group saved."), "success")
    return _back(group)


@bp.route("/<int:group_id>/members", methods=["POST"])
@login_required
def add_member(group_id):
    from app.surveys import _remember_contact

    from app import accounts

    group = _group(group_id, lead=True)
    orcid = request.form.get("orcid", "")
    local = accounts.local_account(orcid)
    if not ORCID_RE.match(orcid) and local is None:
        abort(400)
    known = people_keys.account(orcid, create=False)
    already = known is not None and any(m.user_id == known.id for m in group.members)
    if not may_admit(current_user, orcid) and not already:
        flash(not_admitted(), "error")  # being in a group lets a person sign in (rechte-uebersicht-konzept.md H3)
        return _back(group, "#members")
    role = request.form.get("role") if request.form.get("role") in WorkGroupMember.ROLES else "member"
    name = local.name if local else request.form.get("name", "").strip()[:200]
    from app import orgs

    if already:
        flash(de_en("Diese Person ist schon in der Gruppe.", "This person is already in the group."), "error")
    elif not orgs.may_join_group(known or orcid, group):
        flash(orgs.not_in_org(), "error")
    elif len(group.members) >= MAX_MEMBERS:
        flash(de_en("Mehr als {n} Mitglieder je Gruppe sind nicht möglich.", "More than {n} members per group are not possible.",
                    n=MAX_MEMBERS), "error")
    elif count_for(known.id if known is not None else None) >= MAX_GROUPS:
        flash(de_en("Diese Person ist schon in {n} Gruppen; mehr sind nicht möglich.",
                    "This person is already in {n} groups; more are not possible.", n=MAX_GROUPS), "error")
    else:
        if not name and not local:
            found = orcid_api.lookup(orcid)
            if not found:
                flash(de_en("Diese ORCID iD gibt es nicht. Bitte die 16 Ziffern prüfen (Form 0000-0000-0000-0000).",
                            "This ORCID iD does not exist. Please check the 16 digits (form 0000-0000-0000-0000)."), "error")
                return _back(group, "#members")
            name = found["name"]
        user = people_keys.account(orcid, name)  # a pending account for an iD that never signed in
        group.members.append(WorkGroupMember(user=user, name=name or (user.name if user else ""), role=role,
                                             invited_by_id=current_user.id))
        from app import kind_access

        signed_up = None if user.pending else user  # who never signed in gets it at the first sign-in (start_new)
        kind_access.joined(group, signed_up, current_user)  # "darf Befragungen anlegen": "create" once (E3)
        _remember_contact(user, name)
        db.session.commit()
        from app import invites

        invites.notice(signed_up, current_user, group=group, role=role)  # a mail, if switched on (M5a)
        flash(de_en("{name} ist jetzt in der Gruppe (Rolle „{role}“).", "{name} is now in the group (role “{role}”).",
                    name=name or de_en("Person", "Person"), role=WorkGroupMember.roles()[role][0]), "success")
    return _back(group, "#members")


def _member(group, member_id):
    member = next((m for m in group.members if m.id == member_id), None)
    if member is None:
        abort(404)
    return member


@bp.route("/<int:group_id>/members/<int:member_id>/role", methods=["POST"])
@login_required
def set_role(group_id, member_id):
    group = _group(group_id, lead=True)
    member = _member(group, member_id)
    role = request.form.get("role", "")
    if role not in WorkGroupMember.ROLES:
        abort(400)
    if member.role == "lead" and role != "lead" and len(group.leads()) == 1:
        flash(de_en("Mindestens eine Person muss die Gruppe leiten. Geben Sie die Leitung zuerst an jemand anderen.",
                    "At least one person must lead the group. Hand over the lead to someone else first."), "error")
    else:
        if member.role == "lead" and role != "lead" and member.user_id:
            from app import invites

            invites.revoke_by(member.user_id, group_id=group.id)
        member.role = role
        db.session.commit()
        flash(de_en("{name}: Rolle „{role}“.", "{name}: role “{role}”.", name=member.display_name,
                    role=WorkGroupMember.roles()[role][0]), "success")
    return _back(group, "#members")


@bp.route("/<int:group_id>/members/<int:member_id>/remove", methods=["POST"])
@login_required
def remove_member(group_id, member_id):
    """A lead removes a member, or a member leaves."""
    group = _group(group_id)
    member = _member(group, member_id)
    leaving = member.user_id == current_user.id
    if not leaving and not group.led_by(current_user):
        flash(de_en("Das kann nur die Leitung der Gruppe.", "Only the lead of the group can do that."), "error")
        return _back(group, "#members")
    if len(group.members) == 1:
        flash(de_en("Sie sind das einzige Mitglied. Löschen Sie die Gruppe, wenn sie nicht mehr gebraucht wird.",
                    "You are the only member. Delete the group if it is no longer needed."), "error")
        return _back(group, "#members")
    if member.role == "lead" and len(group.leads()) == 1:
        flash(de_en("Mindestens eine Person muss die Gruppe leiten. Geben Sie die Leitung zuerst an jemand anderen.",
                    "At least one person must lead the group. Hand over the lead to someone else first."), "error")
        return _back(group, "#members")
    name = member.display_name
    kept = end_shares_of_user(group, member.user)
    if member.user_id:
        from app import invites

        invites.revoke_by(member.user_id, group_id=group.id)
    group.members.remove(member)
    db.session.commit()
    still = de_en(" Geteilt bleibt {titles}: Dort gehört noch jemand aus der Gruppe zu den Admins.",
                  " Still shared: {titles}; someone from the group is still among the admins there.",
                  titles=", ".join(de_en("„{t}“", "“{t}”", t=title) for title in kept)) if kept else ""
    if leaving:
        flash(de_en("Sie haben die Gruppe „{g}“ verlassen. Was Sie mit ihr geteilt hatten, ist nicht mehr geteilt.{still}",
                    "You have left the group “{g}”. What you had shared with it is no longer shared.{still}",
                    g=group.name, still=still), "success")
        return redirect(url_for("groups.index"))
    flash(de_en("{name} ist nicht mehr in der Gruppe; was {name} mit ihr geteilt hatte, ist nicht mehr geteilt.{still}",
                "{name} is no longer in the group; what {name} had shared with it is no longer shared.{still}",
                name=name, still=still), "success")
    return _back(group, "#members")


@bp.route("/<int:group_id>/delete", methods=["POST"])
@login_required
def delete(group_id):
    from app import audit

    group = _group(group_id, lead=True)
    name = group.name
    audit.note(name[:40])  # the access log: who deleted which group (audit 2026-10-09, A3)
    remove(group)
    db.session.commit()
    flash(de_en("Gruppe „{name}“ gelöscht. Geteilte Befragungen und Designs bleiben bei den Personen, denen sie gehören.",
                "Group “{name}” deleted. Shared surveys and designs stay with their owners.", name=name), "success")
    return redirect(url_for("groups.index"))
