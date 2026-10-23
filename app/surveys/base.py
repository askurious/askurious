"""Shared parts of the survey pages: the blueprint, rights (get_survey), settings helpers, the data pool."""
import re

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user

from app import autosave, edit_guard, undo
from app.conditions import ByLanguage
from app.extensions import db
from app.lang import de_en
from app.models import Contact, Survey, SurveyMember, utcnow


bp = Blueprint("surveys", __name__, url_prefix="/surveys")

# languages a survey can use, named in their own language (as in the language switch for participants). The texts of
# the platform around a survey (buttons, messages) exist in German and English; other languages get the English ones.
LANGUAGE_NAMES = {"de": "Deutsch (Sie)", "de-du": "Deutsch (du)", "en": "English", "es": "Español", "fr": "Français", "it": "Italiano",
                  "nl": "Nederlands", "pt": "Português", "pl": "Polski", "tr": "Türkçe", "ru": "Русский", "zh-Hans": "简体中文",
                  "ar": "العربية"}
ORCID_IN_TEXT = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])")


@bp.app_context_processor
def _roles():
    from app import access

    return {"role_names": SurveyMember.roles(), "role_names_for": SurveyMember.roles_for,
            # whether the current person may take the questionnaire out (survey file; AUTHZ-05)
            "may_copy_out": lambda survey: access.may_copy_out(current_user, survey)}


def _back_to(survey, default):
    """Redirect target from a hidden "next" field (only pages of this survey)."""
    target = request.form.get("next", "")
    base = url_for("surveys.detail", survey_id=survey.id)  # /surveys/<code>, as all links of this survey
    if target == base or target.startswith((base + "/", base + "#", base + "?")):
        return redirect(target)
    if survey.live is not None:  # a live vote: its own pages (the co-owners are managed in its settings)
        live = url_for("live.manage", code=survey.code)
        if target == live or target.startswith((live + "/", live + "#", live + "?")):
            return redirect(target)
    return redirect(default)


# --- setting groups, shared by the setup wizard and the settings page ------------------------


def apply_project(survey, form):
    title = " ".join(form.get("title", "").split())  # on one line: it goes into mail subjects (audit 2026-10-09b, E6)
    survey.project = form.get("project", "").strip()[:300]
    survey.description = form.get("description", "").strip()[:20000]  # as a survey file holds it (transfer._settings)
    if "research_question" in form:
        survey.research_question = form.get("research_question", "").strip()[:2000]
    if not title:  # the caller does not save; the other fields are set so the form can be shown again as filled in
        return de_en("Der Titel der Befragung darf nicht leer sein.", "The title of the survey must not be empty.")
    survey.title = title[:300]
    return None


def carry_texts(survey, source, target):
    """Texts written in `source` become those of `target` wherever `target` has none: element texts, block titles,
    end and tempo texts, and the texts of the flow (end pages, cases, loop tables). Texts are read in the default
    language only (Item.t); without this, switching the default language (e.g. "Deutsch (Sie)" to "Deutsch (du)")
    left every element written before it empty. Entry by entry: a `target` that is there but empty in places (an
    element opened in the new language before this existed, whose autosave stored "" for the question and every
    option) gets the missing entries too (gender in "Deutsch (du)" without labels, Philipp, 2026-10-06). Nothing
    written is overwritten or removed. Returns how many places got texts."""
    import copy

    if not survey.id or source == target:
        return 0
    n = 0
    empty = (None, "", {}, [])

    def merged(have, src):
        """`have` with the empty entries filled from `src` (dicts entry by entry); None when nothing was missing."""
        if have in empty:
            return copy.deepcopy(src) if src not in empty else None
        if isinstance(have, dict) and isinstance(src, dict):
            out, changed = dict(have), False
            for k, v in src.items():
                m = merged(have.get(k), v)
                if m is not None:
                    out[k], changed = m, True
            return out if changed else None
        return None

    def fill(d):
        nonlocal n
        if not isinstance(d, dict) or d.get(source) in empty:
            return d
        m = merged(d.get(target), d[source])
        if m is None:
            return d
        n += 1
        return {**d, target: m}

    for block in survey.blocks:
        block.titles = fill(block.titles)
        for item in block.items:
            item.texts = fill(item.texts)
    survey.end_texts = fill(survey.end_texts)
    survey.tempo_texts = fill(survey.tempo_texts)

    def walk(v):  # the flow: every dict keyed by languages only ({"de": ...}), wherever it stands
        if isinstance(v, list):
            return [walk(x) for x in v]
        if not isinstance(v, dict):
            return v
        if v and set(v) <= set(LANGUAGE_NAMES):
            return fill(v)
        return {k: walk(x) for k, x in v.items()}

    if survey.flow:
        survey.flow = walk(survey.flow)
    return n


def apply_languages(survey, form):
    if survey.is_editable:
        before = list(survey.languages or [])
        old_default = survey.default_language
        chosen = [l for l in form.getlist("languages") if l in LANGUAGE_NAMES]
        survey.default_language, survey.languages = _languages_from_form_data(form)
        # the texts of the former default language carry over; German "Sie" and "du" also fill each other, which
        # repairs surveys switched before this existed when their settings are saved (Philipp, 2026-10-06)
        carried = carry_texts(survey, old_default, survey.default_language) if old_default else 0
        sibling = {"de": "de-du", "de-du": "de"}.get(survey.default_language)
        if sibling:
            carried += carry_texts(survey, sibling, survey.default_language)
        if carried:
            flash(de_en("Texte aus {old} übernommen ({n} Stellen); bitte für {new} durchsehen (z. B. Anrede).",
                        "Texts copied from {old} ({n} places); please review them for {new} (e.g. form of address).",
                        old=LANGUAGE_NAMES.get(old_default if old_default != survey.default_language else sibling),
                        n=carried, new=LANGUAGE_NAMES[survey.default_language]), "warning")
        if form.get("language_choice_shown"):
            survey.language_choice = bool(form.get("language_choice"))
        if survey.default_language not in chosen and form.getlist("languages"):
            # the form had the default language unticked: it stays (texts fall back to it), and says so
            flash(de_en("{lang} bleibt als Ausgangssprache ausgewählt. Zum Entfernen zuerst eine andere Ausgangssprache wählen.",
                        "{lang} stays selected as the source language. To remove it, first choose another source language.",
                        lang=LANGUAGE_NAMES[survey.default_language]), "warning")
        removed = [l for l in before if l not in survey.languages]
        if removed and survey.id:  # not while the survey is being created
            flash(de_en("Sprache entfernt: {names}. Die Übersetzungen bleiben gespeichert und erscheinen wieder, wenn die "
                        "Sprache erneut gewählt wird.",
                        "Language removed: {names}. The translations stay stored and come back when the language is "
                        "chosen again.", names=", ".join(LANGUAGE_NAMES.get(l, l) for l in removed)), "warning")


REPEAT_MODES = ("session", "cookie", "multi")  # taking part again in the same browser (_fields_participation.html)


def apply_quality_options(survey, form):
    """Paradata and the tempo hint (_fields_quality.html): from the wizard, or from the page "Qualitätssicherung"
    (part "Datenqualität steigern"); only in a draft, since the consent names them."""
    if not survey.is_editable:
        return
    survey.paradata = bool(form.get("paradata"))
    survey.page_timing = bool(form.get("page_timing"))
    survey.focus_paste = bool(form.get("focus_paste"))
    survey.focus_inputs = survey.focus_paste  # the consent of a draft names the counts of today
    if "ai_bait_shown" in form:  # forms without the field (scripts, older pages) leave it as it is
        survey.ai_bait = bool(form.get("ai_bait"))
    from app import tempo

    if "tempo_hint" in form:  # forms without the field (scripts, older pages) leave the hint as it is
        survey.tempo_hint = form.get("tempo_hint") if form.get("tempo_hint") in dict(tempo.MODES) else "off"
        survey.tempo_dose = form.get("tempo_dose") if form.get("tempo_dose") in dict(tempo.DOSES) else "once"
    if "tempo_options_shown" in form:  # the threshold, what else is checked, and an own wording
        survey.tempo_options = tempo.options_from_form(form)
        survey.tempo_texts = tempo.texts_from_form(form, survey.languages, survey.tempo_texts)
    if tempo.active(survey):
        survey.page_timing = True  # the hint needs the page times, and the consent names them


def apply_participation(survey, form):
    """Group "Teilnahme" (_fields_participation.html): navigation and taking part again in the same browser."""
    mode = form.get("repeat_mode")
    if mode in REPEAT_MODES:  # the lab mode may change in the field, the cookie only in a draft (the consent names it)
        survey.panel = {**(survey.panel or {}), "multi": mode == "multi"}
        if survey.is_editable:
            survey.repeat_marker = mode == "cookie"
    elif form.get("multi_shown"):  # older pages and scripts: the two fields of before
        survey.panel = {**(survey.panel or {}), "multi": bool(form.get("multi"))}
    if survey.is_editable:
        survey.allow_back = bool(form.get("allow_back"))
        survey.auto_advance = bool(form.get("auto_advance"))
        survey.show_progress = bool(form.get("show_progress"))
        if "block_load_shown" in form:  # only where the page shows the field (app/blockload.py)
            survey.block_load = bool(form.get("block_load"))
        if mode not in REPEAT_MODES:
            survey.repeat_marker = bool(form.get("repeat_marker"))


def apply_privacy(survey, form):
    """Group "Einwilligung und Datenschutz" (_fields_privacy.html): deletion code, IP estimate, retention periods."""
    if survey.is_editable:
        if "erase_code_shown" in form:  # forms without the field (scripts, older pages) leave it as it is
            survey.erase_code = bool(form.get("erase_code"))
        survey.geo_ip = bool(form.get("geo_ip"))
    if "admin_hidden_shown" in form:  # not named in the consent: changeable at any time
        survey.admin_hidden = bool(form.get("admin_hidden"))
    if "retention_shown" in form:  # forms without the fields (scripts, older pages) leave the periods as they are
        from app import retention

        if retention.from_form(survey, form):
            flash(de_en("Die Aufbewahrungsfrist lässt sich nicht mehr verlängern: Es gibt echte Teilnahmen, und die "
                        "Einwilligung hat ihnen die bisherige Frist genannt. Kürzer geht jederzeit.",
                        "The retention period can no longer be extended: there are real participations, and the consent "
                        "named the previous period to them. Shorter is always possible."), "warning")


def apply_options(survey, form):
    """All options of a survey at once, as the setup wizard sends them (wizard/_options.html). The settings page
    sends its groups one by one (update_meta with `part`); paradata and the tempo hint are on the page
    "Qualitätssicherung", keeping the test runs on "Datenerhebung"."""
    apply_participation(survey, form)
    apply_privacy(survey, form)
    if not form.get("quality_elsewhere"):
        apply_quality_options(survey, form)
    survey.keep_test_runs = bool(form.get("keep_test_runs"))  # data handling, allowed at any time


def apply_options_with_consent(survey, form):
    """apply_options, and a consent that still has its untouched standard text follows: that text says whether
    technical information and page times are recorded. A text that was edited is left alone (the design
    check then reminds of what it does not mention)."""
    _with_consent(survey, lambda: apply_options(survey, form))


def _with_consent(survey, change):
    """Make `change()`; an untouched standard text of the consent follows it (_follow_consent)."""
    consent = survey.consent_item
    before = {lang: consent.kind.template_body(consent, lang) for lang in survey.languages} if consent else {}
    change()
    _follow_consent(survey, before)


def _follow_consent(survey, before):
    """The untouched standard text of the consent ({lang: text} before a change) follows the options."""
    consent = survey.consent_item
    if consent is None:
        return
    texts = dict(consent.texts or {})
    for lang, old in before.items():
        body = (texts.get(lang) or {}).get("body", "")
        new = consent.kind.template_body(consent, lang)
        if body and body == old and new != old:
            texts[lang] = {**texts.get(lang, {}), "body": new}
    consent.texts = texts


def apply_image_style(survey, form):
    from app.image_styles import IMAGE_STYLES

    key = form.get("key", "")
    text = form.get("custom", "").strip()[:1000]
    # an edited preset description is a custom style (also without JavaScript)
    if key in IMAGE_STYLES and text and text != IMAGE_STYLES[key][2]:
        key = "custom"
    survey.image_style = {
        "key": key if key in IMAGE_STYLES or key == "custom" else None,
        "custom": text if key == "custom" else (survey.image_style or {}).get("custom", ""),
    }


# Roles: reading needs "viewer"; changes to the questionnaire (editor, flow, images, test data) and the fieldwork
# (start, pause, resume, close, a new questionnaire version; Ralf, 2026-10-06) need "editor"; everything else
# (settings, archiving, members, publishing, contact data, deleting) needs "admin".
EDIT_ENDPOINTS = {"surveys.start", "surveys.plan_start", "surveys.cancel_start", "surveys.close", "surveys.new_run", "surveys.reopen", "surveys.make_dummy", "surveys.delete_dummy", "surveys.update_test_runs", "surveys.update_image_style", "surveys.results_hb", "surveys.quality", "surveys.update_pool", "surveys.results_sem_layout",
                  "surveys.update_theme", "surveys.choose_theme", "surveys.upload_logo", "surveys.remove_logo", "surveys.partner_logo",
                  "surveys.apply_design", "surveys.save_design", "surveys.seal_check",
                  "diary.schedule", "diary.add_module", "diary.remove_module", "diary.person_action", "diary.add_invites", "diary.release", "diary.block_part", "diary.take_over_page",  # the tab "Zeitplan" of a diary study (app/diary.py)
                  # what the docstrings give "Bearbeiten" and the default made admin-only (audit 2026-10-13, AZ-62):
                  # the study plan and the estimations started from the results page, like surveys.results_hb
                  "study_plan.update", "study_plan.assign", "study_plan.take_target", "study_plan.pilot",
                  "study_plan.sem_power", "study_plan.panel_power",
                  "surveys.results_bins", "surveys.results_sem_bootstrap", "surveys.results_lc", "surveys.results_panel"}
VIEW_POST_ENDPOINTS = {"editor.preview_survey", "editor.preview_comment", "editor.preview_comment_delete"}          # answering the preview changes nothing
ADMIN_GET_ENDPOINTS = {"surveys.export_contacts"}        # personal contact data
DATA_GET_ENDPOINTS = {"surveys.export_voice", "surveys.export_conjoint", "surveys.export_spss", "surveys.export_sav", "surveys.export_omv", "surveys.export_rds",
                      "surveys.export_vignettes", "surveys.export_loop_long", "surveys.seal_chain", "surveys.export_sem_scores"}  # response data: not for viewers (tests/audits/test_audit_2026_10_06_a.py)


def _snapshot_data(fname):
    from app import snapshots

    return snapshots.is_data_file(str(fname or ""))


def required_role():
    ep = request.endpoint or ""
    if ep in ADMIN_GET_ENDPOINTS:
        return "admin"
    args = request.view_args or {}
    if (ep in DATA_GET_ENDPOINTS or (ep == "surveys.export_csv" and args.get("kind") == "data")
            or (ep == "surveys.snapshot_file" and _snapshot_data(args.get("fname")))):
        return "editor"
    if request.method in ("GET", "HEAD") or ep in VIEW_POST_ENDPOINTS:
        return "viewer"
    if ep.startswith(("editor.", "flow_page.")) or ep in EDIT_ENDPOINTS:
        return "editor"
    return "admin"


def get_survey(survey_id, need=None):
    """The survey if the current user is a member with at least the role `need` (default: by request)."""
    from flask import g
    from sqlalchemy.orm import selectinload

    from app.models import Block

    # the blocks and their items in two queries, not one per block (every page of a survey walks them; audit
    # 2026-10-22, G-04: 13 queries for 13 blocks, 26 in the editor)
    survey = db.session.get(Survey, survey_id, options=[selectinload(Survey.blocks).selectinload(Block.items)])
    role = survey.role_of(current_user) if survey else None
    if role is None:
        abort(404)
    g.survey_role = role
    need = need or required_role()
    if SurveyMember.LEVEL[role] < SurveyMember.LEVEL[need]:
        names = SurveyMember.roles()
        flash(de_en("Dafür fehlen die Rechte: Ihre Rolle ist „{role}“, nötig ist „{need}“.",
                    "You lack the rights for this: your role is “{role}”, “{need}” is needed.",
                    role=names[role][0], need=names[need][0]), "error")
        target = request.referrer if request.method == "POST" and request.referrer else url_for("dashboard.index")
        abort(redirect(target))
    g.audit_survey = survey  # passed the check: the access log (app/audit.py) may note what is done with it
    return survey


def _remember_contact(user, name):
    """The account `user` into the current user's address book (Contact)."""
    if user is None or user.id == current_user.id:
        return
    c = db.session.scalar(db.select(Contact).filter_by(owner_id=current_user.id, user_id=user.id))
    if c is None:
        c = Contact(owner_id=current_user.id, user=user)
        db.session.add(c)
    c.user = user
    if name:
        c.name = name
    c.last_used_at = utcnow()


def _add_member(survey, user, name="", affiliation="", role="admin"):
    """`user` (an account, also a pending one: people_keys.account) into the survey's members, or None if it is one."""
    user = getattr(user, "_get_current_object", lambda: user)()  # current_user is a proxy
    if any(m.user_id == user.id for m in survey.members):
        return None
    if not affiliation and not user.pending:
        from app import orgs

        affiliation = orgs.affiliation_of(user)  # the home organisation stands where the institution stood
    m = SurveyMember(
        user=user,
        position=len(survey.members),
        name=name or user.name,
        affiliation=affiliation,
        invited_by_id=current_user.id,
        role=role if role in SurveyMember.ROLES else "editor",
    )
    survey.members.append(m)
    if user.id == current_user.id:  # the person creating it: a supervisor becomes admin too (app/account_types.py, N10)
        from app import account_types

        account_types.add_supervisor(survey, current_user)
    return m


def _languages_from_form_data(form):
    langs = [l for l in form.getlist("languages") if l in LANGUAGE_NAMES]
    default = form.get("default_language", "de")
    if default not in LANGUAGE_NAMES:
        default = "de"
    if default not in langs:
        langs.insert(0, default)
    return default, langs


def _my_projects():
    rows = db.session.scalars(
        db.select(Survey.project)
        .join(SurveyMember)
        .filter(SurveyMember.user_id == current_user.id, Survey.project != "")
        .distinct()
    )
    return sorted(rows)


def _rule_conditions(survey):
    """What the lines of the completion link rules need to pick a condition (_condition.html)."""
    from app import conditions, flow, panel

    described = panel.rule_described(survey)
    if not sees_link_names(survey):
        # the values of the links per person not on the page for "Ansehen" (code review 2026-10-15, H1); it cannot
        # change the rules anyway
        own = set(panel.value_labels(survey))
        described = {ref: ({**d, "options": []} if ref.startswith("p:") and ref[2:] in own else d)
                     for ref, d in described.items()}
    return {"rule_described": described, "rule_data": conditions.page_data(described), "cond_modes": conditions.MODES,
            "conditions": conditions, "condition_text": flow.condition_text}


def _refused(survey, key, message, page=None):
    """A settings form with an error: nothing is saved, the page comes back with what was entered and the
    reason inside that form (macro form_error), like after an edit conflict. `page`: the view that shows the form
    (the settings page unless said otherwise)."""
    from flask import g

    g.form_error = {"key": key, "message": message}
    from app.surveys.settings import detail  # here: settings imports base
    return edit_guard.show_again(lambda: (page or detail)(survey.id))


def _settings_conflict(survey, section):
    """Checked before a settings form is applied; on a conflict the page is shown again (app/edit_guard.py).
    Also where the step for "Rückgängig" begins (_settings_saved ends it) and where a page that saves by itself
    learns the fingerprint of the saved section."""
    from flask import g

    edit_guard.lock(survey)  # overlapping saves one after the other (audit 2026-10-23, S-02)
    conflict = edit_guard.check(survey, f"settings:{section}", edit_guard.settings_state(survey, section), request.form)
    g.settings_step = undo.begin(survey)
    autosave.base_later(lambda: edit_guard.settings_state(survey, section))
    return conflict


SECTION_NAMES = ByLanguage({"meta": "Einstellungen", "image_style": "Bildstil", "theme": "Erscheinungsbild",
                            "panel": "Teilnahmelinks", "end_texts": "Texte der Endseiten", "diary": "Zeitplan"},
                           {"meta": "Settings", "image_style": "Image style", "theme": "Appearance",
                            "panel": "Participation links", "end_texts": "End page texts", "diary": "Schedule"})
# the route that saves each section of the settings page (edit_guard.SETTINGS_FIELDS); the role it needs
# (required_role) is also what taking back or redoing a change of that section needs (undo.need)
SECTION_ENDPOINTS = {"meta": "surveys.update_meta", "image_style": "surveys.update_image_style", "theme": "surveys.update_theme",
                     "panel": "surveys.update_panel", "end_texts": "surveys.update_end_texts",
                     "interview": "interview.settings",  # the settings of an interview guide (app/interview.py)
                     "diary": "diary.schedule"}  # the tab "Zeitplan" of a diary study (app/diary.py)


def _settings_saved(survey, section, name=None):
    """A settings form is applied: note who saved (edit guard) and make the step (before the commit). `name`: the
    group the form belongs to, if the section has several forms (update_meta)."""
    from flask import g

    edit_guard.saved(survey, f"settings:{section}")
    step = g.pop("settings_step", None)
    if step is not None:
        what = autosave.what()
        name = name or undo.both(dict.__getitem__(SECTION_NAMES, section), SECTION_NAMES.en[section])
        step.done(undo.both("{name}: {what} geändert", "{name}: {what} changed", name=name, what=what) if what
                  else undo.both("{name} geändert", "{name} changed", name=name),
                  page="settings", node=section, merge=autosave.merge(f"settings:{section}"))


def stored_pool(survey, drop=False):
    """The survey's data pool as saved on the page "Qualitätssicherung": sources (default: real), also
    unfinished ones, and the speeder threshold. The results are computed for it, without the speeders
    (drop=True); the export page offers it first, there speeders are only marked unless one chooses otherwise."""
    from app import export

    saved = survey.data_pool or {}
    # a draft has no real participations: until a pool is saved its test runs and dummies are the pool, so quality
    # control and results show something while one tries them out; the start sets it to real ones (UI audit, question 9)
    default = list(export.SOURCES) if survey.status == Survey.DRAFT and not saved.get("sources") else ["real"]
    return {"sources": [x for x in export.SOURCES if x in (saved.get("sources") or [])] or default,
            "all": bool(saved.get("all")), "drop": drop, "speeder": export.threshold(survey)}


def data_pool(survey, values=None, drop=False):
    """The data pool of a request: what its address or form names (the choice on the export page, the hidden
    fields of an HB estimation), else the survey's saved pool (with `drop`: are speeders left out). The
    speeder threshold is always the survey's."""
    from app import export

    values = request.args if values is None else values
    if not any(key in values for key in ("source", "all", "drop")):
        return stored_pool(survey, drop)
    return {"sources": [x for x in values.getlist("source") if x in export.SOURCES] or ["real"],
            "all": values.get("all") == "1", "drop": values.get("drop") == "1",
            "speeder": export.threshold(survey)}


def pool_counts(survey):
    """Completed participations per source, shown next to the sources in the data pool."""
    from app.models import Response as R

    # by source only, like the export: test runs and dummy data carry is_preview=True, so filtering on it
    # would always show 0 for them
    tally = survey.tally()
    return {src: sum(n for (source, _, status), n in tally.items() if source == src and status == R.COMPLETED)
            for src in ("real", "test", "dummy")}


def pool_unfinished(survey):
    """Real participations that are not completed (broken off, screened out, excluded): the one choice left in the
    short basis panel of a survey an add-on module keeps without fieldwork of its own (_results_basis.html)."""
    from app.models import Response as R

    return sum(n for (source, _, status), n in survey.tally().items() if source == "real" and status != R.COMPLETED)


def pool_frozen_note():
    """Why the data pool is fixed for this person (pool_frozen), in the language of the page."""
    return de_en("Ein Dashboard dieser Befragung hat gültige Links, und der Datenpool bestimmt, was sie ohne Anmeldung "
                 "zeigen. Ändern kann ihn deshalb nur, wer die Links widerrufen darf (Rolle „Admin“).",
                 "A dashboard of this survey has valid links, and the data pool decides what they show without signing "
                 "in. So only someone who may revoke the links can change it (role “Admin”).")


def pool_frozen(survey, user=None):
    """Is the data pool fixed for this person? While a dashboard of the survey has a valid link, it decides what
    the link shows: only admins change it then (S8 of the security audit 2026-10-03). Speeder threshold and
    marking stay with "Bearbeiten": they only take participations out."""
    from app import boards

    user = user or current_user
    return not survey.can(user, "admin") and boards.survey_has_link(survey)


def sees_link_names(survey):
    """True if the current user may see whom the links per person are for and which look conspicuous: from the
    role "Bearbeiten" up, as the raw data (Ralf, 2026-10-09, security audit R3); "Ansehen", also through a group,
    sees the links and counts only."""
    return survey.can(current_user, "editor")


def _row_key(survey, row, sees):
    """The key that ties a cell of the table of links per value to its count in value_links_usable: "name=value"
    from "Bearbeiten" up, else a keyed hash, so that "Ansehen" cannot read the link values from the page or the
    JSON and build a person's link (code review 2026-10-15, H1)."""
    plain = f"{row['name']}={row.get('value') or ''}"
    if sees:
        return plain
    from app import subkeys

    return "h" + subkeys.mac("link-row", f"{survey.id}:{plain}")[:16]  # a key of its own (CFG-08)


def _hide_names(rows, survey):
    """The rows of panel.group_counts without names, flags and link values for a role below "Bearbeiten"
    (sees_link_names); every row gets its "key" (_row_key)."""
    if sees_link_names(survey):
        return [{**row, "key": _row_key(survey, row, True)} for row in rows]
    return [{**row, "key": _row_key(survey, row, False), "value": "" if row.get("value") else row.get("value"),
             "label": "", "flags": [] if row.get("flags") is not None else None} for row in rows]
