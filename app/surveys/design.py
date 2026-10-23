"""Tab Erscheinungsbild: image style, theme, logos."""
import os
import secrets

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, edit_guard, undo
from app.extensions import db
from app.ids import db_id
from app.lang import de_en
from app.surveys.base import _settings_conflict, _settings_saved, apply_image_style, bp, get_survey
from app.surveys.settings import detail


# --- image style ------------------------------------------------------------------------


@bp.route("/<sid:survey_id>/image-style", methods=["POST"])
@login_required
@autosave.aware
def update_image_style(survey_id):
    survey = get_survey(survey_id)
    conflict = _settings_conflict(survey, "image_style")
    apply_image_style(survey, request.form)
    if conflict:
        return edit_guard.show_again(lambda: detail(survey.id))
    _settings_saved(survey, "image_style")
    db.session.commit()
    flash(de_en("Bildstil gespeichert. Bereits erzeugte Bilder behalten ihren Stil; die Startprüfung zeigt Abweichungen.",
                "Image style saved. Images already created keep their style; the pre-launch check shows deviations."),
          "success")
    return redirect(url_for("surveys.detail", survey_id=survey.id) + "#image-style")


# --- theme: the look of the participant pages (app/theme.py) -------------------------------


def theme_locked():
    """Why the appearance cannot be changed now, in the language of the page."""
    return de_en("Das Erscheinungsbild gehört zu den Erhebungsbedingungen und ist wie der Fragebogen nur im Entwurf "
                 "änderbar. Pausieren Sie die Befragung und öffnen Sie sie zur Bearbeitung (Reiter „Datenerhebung“).",
                 "The appearance belongs to the conditions of data collection and, like the questionnaire, can only be "
                 "changed in a draft. Pause the survey and open it for editing (tab “Data collection”).")


def _theme_from(values, survey):
    """The theme a form or the query of the live preview describes (app/theme_edit.py)."""
    from app import theme_edit

    return theme_edit.from_form(values, survey)


def _take_platform_logo(survey, new):
    """An organisation design brings the logo of the survey's organisation (Ralf 2026-10-16: no logo per server any
    more), unless the survey already has one."""
    from app import theme

    if new.get("preset") == theme.ORG and not new.get("logo"):
        take_org_logo(survey, new)


def take_org_logo(survey, new=None):
    """Copy the logo of the survey's organisation (light and dark, uploaded under Verwaltung > Design) into the
    survey's theme: as with every theme, the survey keeps its own copy. Returns whether there was one."""
    import shutil

    from flask import current_app

    from app import media, orgs, theme

    new = new if new is not None else (theme.of(survey) or {})
    org_id = orgs.of(survey)
    source = theme.org_logo(org_id)
    if not source:
        return False
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], theme.org_folder(org_id))
    org = orgs.get(org_id)
    name = (org.long_name or org.name) if org is not None else ""
    logo = {"alt": {lang: name for lang in survey.languages}}
    for key, w, h in (("file", "w", "h"), ("file_dark", "w_dark", "h_dark")):
        name = source.get(key)
        if name and os.path.isfile(os.path.join(folder, name)):
            copy = f"{secrets.token_urlsafe(12)}{os.path.splitext(name)[1]}"
            shutil.copyfile(os.path.join(folder, name), os.path.join(media._folder(survey), copy))
            logo.update({key: copy, w: source.get(w, 1), h: source.get(h, 1)})
    if logo.get("file"):
        survey.theme = theme.clean({**new, "logo": logo})
        return True
    return False


@bp.route("/<sid:survey_id>/design/org-logo", methods=["POST"])
@login_required
@autosave.aware
def use_org_logo(survey_id):
    """The logo of the survey's organisation into its appearance (Ralf 2026-10-16)."""
    survey = get_survey(survey_id, need="editor")
    if not survey.is_editable:
        flash(theme_locked(), "error")
    elif take_org_logo(survey):
        _settings_saved(survey, "theme")
        db.session.commit()
        flash(de_en("Logo der Organisation übernommen.", "Logo of the organisation applied."), "success")
    else:
        flash(de_en("Die Organisation hat noch kein Logo.", "The organisation has no logo yet."), "error")
    return redirect(url_for("surveys.design", survey_id=survey.id))


def _design_urls(survey):
    """The addresses the theme page posts to and reads from (the editor of a design has its own, app/designs.py)."""
    return {name: url_for(f"surveys.{endpoint}", survey_id=survey.id) for name, endpoint in (
        ("save", "update_theme"), ("sample", "theme_sample"), ("check", "theme_check"), ("logo", "upload_logo"),
        ("logo_remove", "remove_logo"), ("partner", "partner_logo"), ("colours", "logo_colours"))}


@bp.route("/<sid:survey_id>/design")
@login_required
def design(survey_id):
    from app import theme

    survey = get_survey(survey_id)
    current = theme.of(survey)
    from app import designs

    from app import org_designs

    mine = designs.available(current_user)
    return render_template("surveys/design.html", survey=survey, current=current, designs=mine, design_look=designs.look,
                           org_designs=org_designs.for_user(current_user),
                           u=_design_urls(survey), uid=survey.uid, langs=survey.languages,
                           can_save=survey.can(current_user, "editor") and designs.may_create(current_user),
                           save_refused=designs.not_allowed() if survey.can(current_user, "editor") and not designs.may_create(current_user) else "",
                           presets=theme.presets_for(current_user, current["preset"]),
                           can_edit=survey.can(current_user, "editor") and survey.is_editable,
                           locked=not survey.is_editable)


@bp.route("/<sid:survey_id>/design", methods=["POST"])
@login_required
@autosave.aware
def update_theme(survey_id):
    survey = get_survey(survey_id)
    if not survey.is_editable:
        flash(theme_locked(), "error")
        return redirect(url_for("surveys.design", survey_id=survey.id))
    conflict = _settings_conflict(survey, "theme")
    from app import theme

    before = theme.of(survey)
    if request.form.get("reset"):  # colours, font and corners; a logo stays (it has its own "Entfernen")
        survey.theme = theme.clean({"logo": before.get("logo"), "partners": before.get("partners"),
                                    "image_rights": before.get("image_rights")})
    else:
        new = _theme_from(request.form, survey)
        key = (new or {}).get("preset")
        survey.theme = new
        if new and key != before["preset"]:
            _take_platform_logo(survey, new)
    if conflict:
        return edit_guard.show_again(lambda: design(survey.id))
    _settings_saved(survey, "theme")
    db.session.commit()
    if request.form.get("reset"):  # no question beforehand: the notice offers "Rückgängig" (W28)
        flash(de_en("Erscheinungsbild auf Standard gesetzt.", "Appearance set to the standard."), "undo")
    else:
        flash(de_en("Erscheinungsbild gespeichert.", "Appearance saved."), "success")
    from app import theme_edit

    saved = theme.of(survey)
    ignored = [] if request.form.get("reset") else theme_edit.ignored_by_template(saved, request.form)
    if ignored:
        flash(de_en("Nicht übernommen: {what}. Beim Farbschema „{name}“ sind sie durch das Corporate Design festgelegt "
                    "(Schrift: {font}). Für eine andere Schrift ein anderes Farbschema oder eigene Farben wählen.",
                    "Not applied: {what}. With the colour scheme “{name}” they are fixed by the corporate design "
                    "(font: {font}). For another font, choose another colour scheme or your own colours.",
                    what=de_en(" und ", " and ").join(ignored), name=theme.name(saved),
                    font=theme.font_name(saved['font'])), "warning")
    return redirect(url_for("surveys.design", survey_id=survey.id))


def apply_theme_choice(survey, key):
    """Take a built-in template ("papier"), the design of one of the researcher's organisations ("org:3") or one of
    their designs ("design:12") for the survey. A template sets its colours and what it fixes, and font, corners and
    text size if it has a look of its own; logo and the choice "automatisch" stay. An organisation design and a design
    are copied whole. Returns (done, message for the researcher); nothing is committed here."""
    from app import theme

    key = key or ""
    if not survey.is_editable:
        return False, theme_locked()
    if key.startswith("org:"):
        from app import org_designs

        entry = org_designs.chosen(current_user, key)
        if entry is None:
            return False, de_en("Dieses Organisationsdesign steht Ihnen nicht zur Verfügung.",
                                "This organisation design is not available to you.")
        before = theme.of(survey)
        new = theme.clean({**entry["theme"], **{k: before[k] for k in ("logo", "partners", "image_rights", "maps")
                                                 if before.get(k)}})
        survey.theme = new
        _take_platform_logo(survey, new)
        return True, de_en("Organisationsdesign „{name}“ übernommen. Die Befragung hat jetzt ihre eigene Kopie.",
                           "Organisation design “{name}” applied. The survey now has its own copy.", name=entry["name"])
    if key.startswith("design:"):
        from app import designs
        from app.models import Design

        design = db.session.get(Design, int(key[7:])) if key[7:].isascii() and key[7:].isdigit() and len(key) < 19 else None
        if not designs.usable(current_user, design):
            return False, de_en("Dieses Design steht Ihnen nicht zur Verfügung.", "This design is not available to you.")
        designs.apply_to(survey, design)
        return True, de_en("Design „{name}“ übernommen. Die Befragung hat jetzt ihre eigene Kopie.",
                           "Design “{name}” applied. The survey now has its own copy.", name=design.name)
    if key not in theme.BUILTIN:
        return False, de_en("Bitte ein Farbschema wählen.", "Please choose a colour scheme.")
    before = theme.of(survey)
    kept = {k: v for k, v in before.items() if k not in ("name", "tokens", "tokens_dark", "fixed", "logo_position", "swatches", "org")}
    new = theme.clean({**kept, **theme.BUILTIN[key].get("look", {}), "preset": key, "colors": theme.BUILTIN[key]["colors"],
                       "colors_dark": None})
    survey.theme = new
    return True, de_en("Farbschema „{name}“ übernommen.", "Colour scheme “{name}” applied.", name=theme.name({"preset": key}))


@bp.route("/<sid:survey_id>/design/choose", methods=["POST"])
@login_required
@autosave.aware
def choose_theme(survey_id):
    """A template or a design, chosen in the settings. Applies on the click, like every setting (autosave, one step
    for "Rückgängig"; UI/UX audit 2026-10-05, V86): the page says that the former colours were replaced."""
    survey = get_survey(survey_id)
    conflict = _settings_conflict(survey, "theme") if survey.is_editable else None
    done, message = apply_theme_choice(survey, request.form.get("preset"))
    if done and conflict:
        return edit_guard.show_again(lambda: detail(survey.id))
    if done:
        _settings_saved(survey, "theme")
        db.session.commit()
    else:
        db.session.rollback()
    if done and autosave.wanted():  # the page shows warnings only: the replaced colours are worth one
        flash(de_en("{message} Die bisherigen Farben wurden ersetzt; „Rückgängig“ holt sie zurück.",
                    "{message} The previous colours were replaced; “Undo” brings them back.", message=message), "warning")
    else:
        flash(message, "success" if done else "error")
    if request.form.get("back") == "design":  # a tile on the page "Erscheinungsbild"
        return redirect(url_for("surveys.design", survey_id=survey.id))
    return redirect(url_for("surveys.detail", survey_id=survey.id) + "#design")


@bp.route("/<sid:survey_id>/design/sample")
@login_required
def theme_sample(survey_id):
    """A page with typical elements in the theme given by the query (the live preview while choosing colours)."""
    from app import theme_edit

    survey = get_survey(survey_id)
    lang = request.args.get("lang") if request.args.get("lang") in survey.languages else survey.default_language
    return theme_edit.sample_page(survey, request.args, lang)


@bp.route("/<sid:survey_id>/design/check")
@login_required
def theme_check(survey_id):
    """Contrast of the theme given by the query, for the list next to the live preview."""
    from app import theme_edit

    return theme_edit.check(get_survey(survey_id), request.args)


@bp.route("/<sid:survey_id>/design/apply", methods=["POST"])
@login_required
def apply_design(survey_id):
    """Copy one of the researcher's designs (own or shared) into the survey."""
    from app import designs
    from app.models import Design

    survey = get_survey(survey_id)
    back = redirect(url_for("surveys.design", survey_id=survey.id))
    if not survey.is_editable:
        flash(theme_locked(), "error")
        return back
    design = db.session.get(Design, request.form.get("design", type=db_id))
    if not designs.usable(current_user, design):
        flash(de_en("Dieses Design steht Ihnen nicht zur Verfügung.", "This design is not available to you."), "error")
        return back
    from flask import g

    from app import theme

    before = theme.of(survey)
    had_files = bool((before.get("logo") or {}).get("file") or before.get("partners"))
    g.settings_step = undo.begin(survey)  # one step for "Rückgängig" (_settings_saved ends it)
    designs.apply_to(survey, design)
    _settings_saved(survey, "theme")
    db.session.commit()
    # no question beforehand unless a logo is lost (the page asks then): the notice offers "Rückgängig" (W28)
    flash(de_en("Design „{name}“ übernommen. Die Befragung hat jetzt ihre eigene Kopie.",
                "Design “{name}” applied. The survey now has its own copy.", name=design.name), "success" if had_files else "undo")
    return back


@bp.route("/<sid:survey_id>/design/save", methods=["POST"])
@login_required
def save_design(survey_id):
    """Keep the survey's saved theme as a design of one's own (also while the survey is running)."""
    from app import designs

    survey = get_survey(survey_id)
    back = redirect(url_for("surveys.design", survey_id=survey.id) + "#designs")
    if not designs.may_create(current_user):
        flash(designs.not_allowed(), "error")
        return back
    if len(designs.mine(current_user)) >= designs.MAX_DESIGNS:
        flash(de_en("Mehr als {n} Designs lassen sich nicht anlegen.", "You cannot create more than {n} designs.",
                    n=designs.MAX_DESIGNS), "error")
        return back
    design = designs.create(current_user, request.form.get("name") or survey.title, survey.theme, survey.uid)
    db.session.commit()
    flash(de_en("Als Design „{name}“ gespeichert. Unter „Designs“ lässt es sich teilen.",
                "Saved as design “{name}”. You can share it under “Designs”.", name=design.name), "success")
    return back


def _theme_files_step(survey_id, step, anchor):
    """One step on the files of the survey's theme (logo, partner logos; app/theme_edit.py): draft only, the new
    theme is stored, files that are no longer used are deleted after the commit."""
    from app import media, theme_edit

    survey = get_survey(survey_id)
    back = redirect(url_for("surveys.design", survey_id=survey.id) + anchor)
    if not survey.is_editable:
        flash(theme_locked(), "error")
        return back
    try:
        new, message, gone = step(survey)
    except theme_edit.Refused as e:
        if str(e):
            flash(str(e), "error")
        return back
    survey.theme = new
    _settings_saved(survey, "theme")
    db.session.commit()
    for name in gone:
        media.delete(survey, name)
    flash(message, "success")
    return back


@bp.route("/<sid:survey_id>/design/logo", methods=["POST"])
@login_required
def upload_logo(survey_id):
    """A logo for the survey pages, or its variant for dark backgrounds (variant=dark). The file is stored at
    once; texts, position and size belong to the form of the page."""
    from app import theme_edit

    return _theme_files_step(survey_id, lambda s: theme_edit.upload_logo(s, request.form, request.files), "#logo")


@bp.route("/<sid:survey_id>/design/logo/remove", methods=["POST"])
@login_required
def remove_logo(survey_id):
    from app import theme_edit

    return _theme_files_step(survey_id, lambda s: theme_edit.remove_logo(s, request.form), "#logo")


@bp.route("/<sid:survey_id>/design/partner", methods=["POST"])
@login_required
def partner_logo(survey_id):
    """Add the logo of a funder or partner (upload), or remove one (remove=<position>)."""
    from app import theme_edit

    return _theme_files_step(survey_id, lambda s: theme_edit.partner_logo(s, request.form, request.files), "#partner")


@bp.route("/<sid:survey_id>/design/logo/colours")
@login_required
def logo_colours(survey_id):
    """The main colours of the survey's logo, as suggestions for the accent colour."""
    from app import theme_edit

    return theme_edit.logo_colours(get_survey(survey_id))
