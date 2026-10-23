"""Designs: themes a researcher keeps under a name, to use them in other surveys and to share them.

A design is a copy of a survey's theme (app/theme.py), logo files included. Using it copies it into the survey
again: a survey always carries its own theme, so editing, unsharing or deleting a design never changes a
survey, and a running survey keeps the look its answers were given under. Shared designs can be used and
duplicated by the people they are shared with, not changed, unless the owner gave that person the right to edit
(may_edit); a group never carries that right, also not for its leads. A design that is a copy of an organisation design
(app/org_designs.py) keeps everything of it, its fixed tokens too.

A design is shared with single researchers (by ORCID iD) or with a group (app/groups.py): then every member can
use it, also those who join later, and who leaves the group no longer can. The owner shares only with groups
they are a member of; leaving a group ends what they shared with it.
"""
import os
import secrets
import shutil
import types

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, people_keys, theme
from app.auth import ORCID_RE
from app.extensions import db
from app.ids import db_id
from app.lang import de_en
from app.media import upload_folder
from app.models import Contact, Design, DesignEditor, DesignGroupShare, DesignShare, User, WorkGroup

bp = Blueprint("designs", __name__, url_prefix="/designs")
MAX_NAME = 80
MAX_DESIGNS = 100  # per researcher


def folder_uid(design):
    return f"_design_{design.id}"


def copy_logo(logo, from_uid, to_uid):
    """The logo part of a theme with its files copied into another upload folder (new random names)."""
    if not logo:
        return None
    out = dict(logo)
    os.makedirs(upload_folder(to_uid), exist_ok=True)
    for key in ("file", "file_dark", "icon"):
        name = logo.get(key)
        if not name:
            continue
        if key == "icon" and name == logo.get("file"):  # an SVG logo is its own icon
            out["icon"] = out["file"]
            continue
        source = os.path.join(upload_folder(from_uid), name)
        if not os.path.isfile(source):
            if key == "file":
                return None  # the file is gone: a design or survey without a logo, not one with a broken image
            for k in {"file_dark": ("file_dark", "w_dark", "h_dark"), "icon": ("icon",)}[key]:
                out.pop(k, None)
            continue
        out[key] = f"{secrets.token_urlsafe(12)}{os.path.splitext(name)[1]}"
        from app import media

        media.copy_file(source, os.path.join(upload_folder(to_uid), out[key]), to_uid)
    return out


def copy_partners(partners, from_uid, to_uid):
    """The partner logos of a theme with their files copied into another upload folder."""
    out = []
    os.makedirs(upload_folder(to_uid), exist_ok=True)
    for p in partners or []:
        source = os.path.join(upload_folder(from_uid), p["file"])
        if os.path.isfile(source):
            name = f"{secrets.token_urlsafe(12)}{os.path.splitext(p['file'])[1]}"
            from app import media

            media.copy_file(source, os.path.join(upload_folder(to_uid), name), to_uid)
            out.append({**p, "file": name})
    return out


def mine(user):
    return list(db.session.scalars(db.select(Design).filter_by(owner_id=user.id).order_by(Design.name)))


def shared_with(user):
    """Designs of others this researcher can reach: shared with them in person or with one of their groups."""
    from app import groups

    from app import orgs

    personal = db.select(DesignShare.design_id).filter(DesignShare.user_id == user.id)
    by_group = db.select(DesignGroupShare.design_id).filter(DesignGroupShare.group_id.in_(groups.ids_of(user)))
    # shared with the whole organisation it belongs to (app/orgs.py, A13)
    by_org = db.and_(Design.org_shared.is_(True), orgs.column(Design.org_id).in_(orgs.searchable_ids(user)))
    return list(db.session.scalars(
        db.select(Design).filter(Design.owner_id != user.id, db.or_(Design.id.in_(personal), Design.id.in_(by_group),
                                                                    by_org))
        .order_by(Design.name)))


def via_groups(user, design):
    """The groups of this researcher the design is shared with (for "über Gruppe ..." next to a shared design)."""
    return [s.group for s in design.group_shares if s.group.member_of(user) is not None]


def available(user):
    """The designs this researcher can apply: own ones and shared ones (without those of a reserved theme)."""
    return [d for d in mine(user) + shared_with(user) if usable(user, d)]


def usable(user, design):
    """May this researcher apply the design? Own or shared (Ralf 2026-10-19: no theme is reserved any more)."""
    if design is None:
        return False
    return (design.owner_id == user.id or any(s.user_id == user.id for s in design.shares)
            or bool(via_groups(user, design)))


def look(design):
    return theme.clean(design.data) or theme.standard()


def create(user, name, data, from_uid=None):
    """A new design of `user` from a theme; logo files are copied from the folder `from_uid`."""
    design = Design(owner_id=user.id, name=_name(name), data={})
    db.session.add(design)
    db.session.flush()  # the id names the folder
    cleaned = theme.clean(data) or theme.standard()
    logo = copy_logo(cleaned.get("logo"), from_uid, folder_uid(design)) if from_uid else None
    partners = copy_partners(cleaned.get("partners"), from_uid, folder_uid(design)) if from_uid else []
    design.data = {**{k: v for k, v in cleaned.items() if k not in ("logo", "partners")},
                   **({"logo": logo} if logo else {}), **({"partners": partners} if partners else {})}
    if from_uid:
        from app import asset_rights

        design.data = asset_rights.copied_theme(cleaned, from_uid, design.data, folder_uid(design)) or theme.standard()
    return design


def apply_to(survey, design):
    """Copy the design into the survey (replacing its theme and logo files). The old files go once the change
    is committed: a failed commit keeps the survey with the files its theme names."""
    from app import asset_rights, media

    before = theme.clean(survey.theme) or {}
    old = before.get("logo") or {}
    data = look(design)
    logo = copy_logo(data.get("logo"), folder_uid(design), survey.uid)
    partners = copy_partners(data.get("partners"), folder_uid(design), survey.uid)
    survey.theme = asset_rights.copied_theme(data, folder_uid(design),
                                            {**{k: v for k, v in data.items() if k not in ("logo", "partners")},
                                             "logo": logo, "partners": partners}, survey.uid)
    for key in ("file", "file_dark", "icon"):
        if old.get(key):
            media.delete_after_commit(survey, old[key])
    for p in before.get("partners", []):
        media.delete_after_commit(survey, p["file"])


def _name(name):
    return " ".join(str(name or "").split())[:MAX_NAME] or "Design"


def _own(design_id):
    design = db.session.get(Design, design_id)
    if design is None or design.owner_id != current_user.id:
        abort(404)
    return design


def may_edit(user, design):
    """May this researcher change the design's look? Its owner, and those the owner shared it with in person and
    gave the right to edit. A group carries no such right: its members, the leads too, use and duplicate.
    Ownership stays with the person: renaming, sharing and deleting are the owner's."""
    if design is None:
        return False
    if design.owner_id == user.id:
        return True
    return (any(s.user_id == user.id for s in design.shares) and any(e.user_id == user.id for e in design.editors)
            and usable(user, design))


def _editable(design_id):
    design = db.session.get(Design, design_id)
    if not may_edit(current_user, design):
        abort(404)
    return design


def _back():
    return redirect(url_for("designs.index"))


# what an account that may not start surveys reads where a new design would be made (H9 of the security audit
# 2026-10-03): a design is an own project of its kind, with uploaded logo files, as a survey is
NOT_ALLOWED = ("Ihr Konto ist für die Mitarbeit in Befragungen und Gruppen freigeschaltet, zu denen Sie eingeladen wurden. "
               "Eigene Designs legt an, wer eigene Befragungen anlegen darf; dafür schreiben Sie über „Rückmeldung geben“ "
               "(🐞 oben) an die Admins der Plattform. Designs, die mit Ihnen geteilt sind, verwenden und bearbeiten Sie "
               "wie bisher.")
NOT_ALLOWED_EN = ("Your account is enabled for working on surveys and groups you were invited to. Designs of your own "
                  "are made by those who may create surveys of their own; for that, write to the platform admins via "
                  "“Give feedback” (🐞 at the top). Designs shared with you, you use and edit as before.")


def not_allowed():
    """NOT_ALLOWED in the language of the page."""
    return de_en(NOT_ALLOWED, NOT_ALLOWED_EN)


def may_create(user):
    """May this researcher make designs of their own (new, duplicate, a survey's look kept as a design)? Who may
    create surveys (auth.may_create_surveys). Using designs shared with them, and editing those whose owner allowed
    it, needs no such right."""
    from app.auth import may_create_surveys

    return may_create_surveys(user)


class Holder:
    """A design as app.theme_edit sees it: `.theme`, `.uid` (upload folder), `.languages`; and what the sample page
    asks of a survey."""

    def __init__(self, design):
        data = theme.clean(design.data) or theme.standard()
        self.design, self.theme, self.uid = design, design.data, folder_uid(design)
        alt = (data.get("logo") or {}).get("alt", {})
        self.languages = sorted({"de", "en"} | set(alt))
        self.default_language, self.title, self.id = "de", design.name, None


def _edit_urls(design):
    return {name: url_for(f"designs.{endpoint}", design_id=design.id) for name, endpoint in (
        ("save", "save"), ("sample", "sample"), ("check", "check"), ("logo", "upload_logo"),
        ("logo_remove", "remove_logo"), ("partner", "partner_logo"), ("colours", "logo_colours"))}


def _store(design, new):
    """A design always holds a complete theme (the standard look is a design like any other)."""
    design.data = new or theme.standard()


@bp.route("/<int:design_id>")
@login_required
def edit(design_id):
    """The page "Erscheinungsbild" for a design: colours, font, logo and partner logos, with the live preview."""
    design = _editable(design_id)
    holder = Holder(design)
    current = theme.clean(design.data) or theme.standard()
    return render_template("surveys/design.html", survey=None, design=design, current=current, u=_edit_urls(design),
                           uid=holder.uid, langs=holder.languages, presets=theme.presets_for(current_user, current["preset"]),
                           can_edit=True, locked=False, designs=[], can_save=False)


@bp.route("/<int:design_id>", methods=["POST"])
@login_required
@autosave.aware
def save(design_id):
    from app import theme_edit

    design = _editable(design_id)
    before = theme.clean(design.data) or theme.standard()
    back = redirect(url_for("designs.edit", design_id=design.id))
    if request.form.get("reset"):  # colours, font and corners; logo and partner logos stay
        new = theme.clean({"logo": before.get("logo"), "partners": before.get("partners"),
                           "image_rights": before.get("image_rights")})
    else:
        new = theme_edit.from_form(request.form, Holder(design))
    _store(design, new)
    db.session.commit()
    flash(de_en("Design gespeichert. Befragungen, die es schon verwenden, behalten ihre Kopie.",
                "Design saved. Surveys that already use it keep their copy.")
          + ("" if design.owner_id == current_user.id else de_en(" Es gehört {owner}; Sie haben das Recht, es zu bearbeiten.",
                                                                 " It belongs to {owner}; you have the right to edit it.",
                                                                 owner=design.owner.display_name)),
          "success")
    saved = theme.clean(design.data) or theme.standard()
    ignored = [] if request.form.get("reset") else theme_edit.ignored_by_template(saved, request.form)
    if ignored:
        flash(de_en("Nicht übernommen: {what}. Beim Farbschema „{name}“ sind sie durch das Corporate Design festgelegt.",
                    "Not applied: {what}. The colour scheme “{name}” fixes them by its corporate design.",
                    what=de_en(" und ", " and ").join(ignored), name=theme.name(saved)), "warning")
    return back


@bp.route("/<int:design_id>/sample")
@login_required
def sample(design_id):
    from app import theme_edit

    holder = Holder(_editable(design_id))
    return theme_edit.sample_page(holder, request.args, request.args.get("lang") if request.args.get("lang") in holder.languages else "de")


@bp.route("/<int:design_id>/look")
@login_required
def look_page(design_id):
    """The sample page in the stored look of a design, for the small view on its tile (page "Designs"): for the
    owner and for those the design is shared with. Nothing of a query is taken."""
    from app import theme_edit

    design = db.session.get(Design, design_id)
    if design is None or (design.owner_id != current_user.id and design not in shared_with(current_user)):
        abort(404)
    return theme_edit.sample_page(Holder(design), {}, "de", short=True)


@bp.route("/<int:design_id>/check")
@login_required
def check(design_id):
    from app import theme_edit

    return theme_edit.check(Holder(_editable(design_id)), request.args)


def _files_step(design_id, step, anchor):
    from app import media, theme_edit

    design = _editable(design_id)
    holder = Holder(design)
    back = redirect(url_for("designs.edit", design_id=design.id) + anchor)
    try:
        new, message, gone = step(holder)
    except theme_edit.Refused as e:
        if str(e):
            flash(str(e), "error")
        return back
    _store(design, new)
    db.session.commit()
    for name in gone:
        media.delete(holder, name)
    flash(message.replace(de_en(" der Befragung", " of the survey"), ""), "success")
    return back


@bp.route("/<int:design_id>/logo", methods=["POST"])
@login_required
def upload_logo(design_id):
    from app import theme_edit

    return _files_step(design_id, lambda holder: theme_edit.upload_logo(holder, request.form, request.files), "#logo")


@bp.route("/<int:design_id>/logo/remove", methods=["POST"])
@login_required
def remove_logo(design_id):
    from app import theme_edit

    return _files_step(design_id, lambda holder: theme_edit.remove_logo(holder, request.form), "#logo")


@bp.route("/<int:design_id>/partner", methods=["POST"])
@login_required
def partner_logo(design_id):
    from app import theme_edit

    return _files_step(design_id, lambda holder: theme_edit.partner_logo(holder, request.form, request.files), "#partner")


@bp.route("/<int:design_id>/logo/colours")
@login_required
def logo_colours(design_id):
    from app import theme_edit

    return theme_edit.logo_colours(Holder(_editable(design_id)))


@bp.route("/new", methods=["POST"])
@login_required
def new():
    """A design from scratch (the standard look), to be shaped in the editor."""
    if not may_create(current_user):
        flash(not_allowed(), "error")
        return _back()
    if len(mine(current_user)) >= MAX_DESIGNS:
        flash(de_en("Mehr als {n} Designs lassen sich nicht anlegen.", "You cannot create more than {n} designs.", n=MAX_DESIGNS), "error")
        return _back()
    design = create(current_user, request.form.get("name") or de_en("Neues Design", "New design"), None)
    db.session.commit()
    return redirect(url_for("designs.edit", design_id=design.id))


@bp.route("/")
@login_required
def index():
    contacts = list(db.session.scalars(db.select(Contact).filter_by(owner_id=current_user.id).order_by(Contact.last_used_at.desc())))
    own = mine(current_user)
    shared_to = {s.user_id for d in own for s in d.shares if s.user_id}  # the names of these people only
    names = {u.id: u.name for u in db.session.scalars(db.select(User).filter(User.id.in_(shared_to))) if u.name} \
        if shared_to else {}
    names.update({c.user_id: c.name for c in contacts if c.name and c.user_id and c.user_id not in names})
    from app import groups

    # "Person hinzufügen" of one tile (_people.html, S8): the search for the design in ?share=
    share_for, q, results = request.args.get("share", type=int), request.args.get("q", "").strip(), None
    target = next((d for d in own if d.id == share_for), None)
    if target is not None and q:
        from app.surveys import search_to_share

        from app.surveys import people_keys_of

        results = search_to_share(q, people_keys_of(target.shares) | {current_user.orcid})
    return render_template("designs/index.html", own=own, shared=shared_with(current_user),
                           share_for=target.id if target else None, q=q, results=results, rights=rights(),
                           contacts=contacts, names=names, look=look, folder_uid=folder_uid, usable=usable,
                           builtin=theme.presets_for(current_user), my_groups=groups.of(current_user),
                           via_groups=via_groups, may_edit=may_edit, may_create=may_create(current_user),
                           not_allowed=not_allowed())


@bp.route("/<int:design_id>/rename", methods=["POST"])
@login_required
@autosave.aware
def rename(design_id):
    design = _own(design_id)
    design.name = _name(request.form.get("name"))
    db.session.commit()
    flash(de_en("Name gespeichert.", "Name saved."), "success")
    return _back()


@bp.route("/<int:design_id>/delete", methods=["POST"])
@login_required
def delete(design_id):
    design = _own(design_id)
    uid = folder_uid(design)
    db.session.delete(design)
    db.session.commit()
    shutil.rmtree(upload_folder(uid), ignore_errors=True)
    flash(de_en("Design gelöscht. Befragungen, die es verwendet haben, behalten ihr Erscheinungsbild.",
                "Design deleted. Surveys that used it keep their look."), "success")
    return _back()


@bp.route("/<int:design_id>/duplicate", methods=["POST"])
@login_required
def duplicate(design_id):
    """An own, changeable copy of an own or a shared design."""
    source = db.session.get(Design, design_id)
    if not usable(current_user, source):
        abort(404)
    if not may_create(current_user):
        flash(not_allowed(), "error")
        return _back()
    if len(mine(current_user)) >= MAX_DESIGNS:
        flash(de_en("Mehr als {n} Designs lassen sich nicht anlegen.", "You cannot create more than {n} designs.", n=MAX_DESIGNS), "error")
        return _back()
    suffix = de_en(" (Kopie)", " (copy)")
    create(current_user, source.name[:MAX_NAME - len(suffix)] + suffix, source.data, folder_uid(source))
    db.session.commit()
    flash(de_en("Design dupliziert.", "Design duplicated."), "success")
    return _back()


def rights():
    """What a person a design is shared with may do: use it (apply and duplicate) or also edit it (DesignEditor)."""
    return {"use": (de_en("Anwenden", "Use"), de_en("anwenden und duplizieren, nicht ändern", "apply and duplicate, not change")),
            "edit": (de_en("Bearbeiten", "Edit"), de_en("auch ändern; Umbenennen, Teilen und Löschen bleiben bei Ihnen",
                                                        "also change it; renaming, sharing and deleting stay with you"))}


@bp.route("/<int:design_id>/share", methods=["POST"])
@login_required
def share(design_id):
    """Share with a person, found by "Person hinzufügen" (or an ORCID iD); right=edit also gives the right to edit,
    as "Bearbeiten erlauben" afterwards would."""
    design = _own(design_id)
    from app import accounts, auth
    from app.surveys import _remember_contact

    orcid = (request.form.get("orcid") or "").strip()
    known = people_keys.account(orcid, create=False)
    if not ORCID_RE.match(orcid) and accounts.local_account(orcid) is None:
        flash(de_en("Bitte eine ORCID iD angeben (0000-0000-0000-0000) oder eine Person aus den Kontakten wählen.",
                    "Please give an ORCID iD (0000-0000-0000-0000) or choose a person from your contacts."), "error")
    elif known is not None and known.id == current_user.id:
        flash(de_en("Das Design gehört Ihnen bereits.", "The design already belongs to you."), "error")
    elif known is not None and any(s.user_id == known.id for s in design.shares):
        flash(de_en("Mit dieser Person ist das Design schon geteilt.", "The design is already shared with this person."), "error")
    elif not auth.may_sign_in(orcid):
        flash(auth.cannot_sign_in(), "error")
    else:
        name = (request.form.get("name") or "").strip()[:200]
        user = people_keys.account(orcid, name)  # a pending account for an iD that never signed in
        design.shares.append(DesignShare(user=user))
        _remember_contact(user, name)  # the name stays next to the account
        if request.form.get("right") == "edit":
            design.editors.append(DesignEditor(user=user))
            flash(de_en("Design geteilt. Die Person kann es anwenden, duplizieren und bearbeiten; Umbenennen, Teilen und "
                        "Löschen bleiben bei Ihnen.",
                        "Design shared. The person can apply, duplicate and edit it; renaming, sharing and deleting stay "
                        "with you."), "success")
        else:
            flash(de_en("Design geteilt. Die Person kann es anwenden und duplizieren, aber nicht ändern.",
                        "Design shared. The person can apply and duplicate it, but not change it."), "success")
        db.session.commit()
    return redirect(url_for("designs.index") + f"#design-{design.id}")


def _person_id():
    """The account a form names: its id (field "user"), from a page of before its key (field "orcid")."""
    uid = request.form.get("user", type=int)
    if uid is not None:
        return uid
    user = people_keys.account(request.form.get("orcid"), create=False)
    return user.id if user is not None else None


@bp.route("/<int:design_id>/unshare", methods=["POST"])
@login_required
def unshare(design_id):
    design = _own(design_id)
    uid = _person_id()
    design.shares = [s for s in design.shares if s.user_id != uid]
    design.editors = [e for e in design.editors if e.user_id != uid]  # the right to edit ends with the share
    db.session.commit()
    flash(de_en("Freigabe beendet. Befragungen, die das Design schon verwenden, behalten ihr Erscheinungsbild.",
                "Sharing ended. Surveys that already use the design keep their look."), "success")
    return _back()


@bp.route("/<int:design_id>/editor", methods=["POST"])
@login_required
def editor(design_id):
    """The owner gives a person the design is shared with the right to change it (allow=1), or takes it."""
    design = _own(design_id)
    uid = _person_id()
    if uid is None or not any(s.user_id == uid for s in design.shares):
        abort(404)
    has = any(e.user_id == uid for e in design.editors)
    if request.form.get("allow"):
        if not has:
            design.editors.append(DesignEditor(user_id=uid))
        flash(de_en("Die Person kann das Design jetzt auch bearbeiten. Umbenennen, Teilen und Löschen bleiben bei Ihnen.",
                    "The person can now also edit the design. Renaming, sharing and deleting stay with you."), "success")
    else:
        design.editors = [e for e in design.editors if e.user_id != uid]
        flash(de_en("Die Person kann das Design weiter anwenden und duplizieren, aber nicht mehr bearbeiten.",
                    "The person can still apply and duplicate the design, but no longer edit it."), "success")
    db.session.commit()
    return _back()


@bp.route("/<int:design_id>/share-group", methods=["POST"])
@login_required
def share_group(design_id):
    """Share with one of the own groups: all its members, also future ones, can use and duplicate the design."""
    design = _own(design_id)
    group = db.session.get(WorkGroup, request.form.get("group", type=db_id) or 0)
    from app import orgs

    if group is None or group.member_of(current_user) is None:
        flash(de_en("Bitte eine Ihrer Gruppen wählen.", "Please choose one of your groups."), "error")
    elif not orgs.same(group, design):  # only within the design's organisation (app/orgs.py, A8)
        flash(orgs.fenced_out(), "error")
    elif any(s.group_id == group.id for s in design.group_shares):
        flash(de_en("Mit dieser Gruppe ist das Design schon geteilt.", "The design is already shared with this group."), "error")
    else:
        design.group_shares.append(DesignGroupShare(group_id=group.id))
        db.session.commit()
        flash(de_en("Design mit der Gruppe „{name}“ geteilt. Alle Mitglieder, auch künftige, können es anwenden und "
                    "duplizieren, aber nicht ändern.",
                    "Design shared with the group “{name}”. All members, future ones too, can apply and duplicate it, "
                    "but not change it.", name=group.name), "success")
    return _back()


@bp.route("/<int:design_id>/share-org", methods=["POST"])
@login_required
def share_org(design_id):
    """Share with the whole organisation the design belongs to, or stop (app/orgs.py, A13): every member, also future
    ones, can apply and duplicate it, not change it."""
    from app import orgs

    design = _own(design_id)
    design.org_shared = request.form.get("state") == "on"
    db.session.commit()
    name = orgs.label(orgs.get(orgs.of(design)))
    flash(de_en("Design mit der ganzen Organisation „{o}“ geteilt.", "Design shared with the whole organisation “{o}”.",
                o=name) if design.org_shared else
          de_en("Das Design ist nicht mehr mit der ganzen Organisation geteilt.",
                "The design is no longer shared with the whole organisation."), "success")
    return _back()


@bp.route("/<int:design_id>/unshare-group", methods=["POST"])
@login_required
def unshare_group(design_id):
    design = _own(design_id)
    group_id = request.form.get("group", type=int)
    design.group_shares = [s for s in design.group_shares if s.group_id != group_id]
    db.session.commit()
    flash(de_en("Freigabe beendet. Befragungen, die das Design schon verwenden, behalten ihr Erscheinungsbild.",
                "Sharing ended. Surveys that already use the design keep their look."), "success")
    return _back()


def holder(uid):
    """For app.media, which stores by the folder name of a survey."""
    return types.SimpleNamespace(uid=uid)
