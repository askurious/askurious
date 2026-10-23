"""Tab Einstellungen: the settings forms, consent facts and end texts."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, edit_guard, lifecycle, links, undo
from app.conditions import ByLanguage
from app.extensions import db
from app.lang import de_en
from app.models import utcnow
from app.surveys.base import (EDIT_ENDPOINTS, LANGUAGE_NAMES, SECTION_ENDPOINTS, _follow_consent, _my_projects,
                              _refused, _rule_conditions, _settings_conflict, _settings_saved, _with_consent,
                              apply_languages, apply_options_with_consent, apply_participation, apply_privacy,
                              apply_project, apply_quality_options, bp, get_survey)
from app.surveys.field import field
from app.surveys.people import _search_people
from app.timeutil import last_day, to_local


@bp.route("/<sid:survey_id>")
@login_required
def detail(survey_id):
    survey = get_survey(survey_id)
    q = request.args.get("q", "").strip()
    # only admins can add people, so only they search (contacts, registered users, the ORCID registry)
    results = _search_people(q, survey) if q and survey.can(current_user, "admin") else None
    default_end = last_day(survey.field_end or lifecycle.default_field_end())
    from app.models import Response as R
    from app.participate import DEFAULT_END_TEXTS
    from app.ui_i18n import tr

    tally = survey.tally()
    counts = {
        "completed": sum(n for (_, preview, status), n in tally.items() if not preview and status == R.COMPLETED),
        "declined": survey.declined_count or 0,
        "active": sum(n for (_, preview, status), n in tally.items() if not preview and status == R.ACTIVE),
    }
    from app import contact as contact_mod
    from app import image_styles
    from app import settings_page

    return render_template(
        "surveys/detail.html",
        seal_started=__import__("app.seal", fromlist=["has_chain"]).has_chain(survey),  # off only before its first entry
        min_age=__import__("app.min_age", fromlist=["age_item"]),  # the screen-out for younger people (pre-test 2026-10-16)
        counts=counts,
        contact=contact_mod,
        panel=__import__("app.panel", fromlist=["config"]),
        **_rule_conditions(survey),
        image_styles=image_styles,
        style_cfg=image_styles.settings(survey),
        # what participants see without a text of their own, in each survey language as participate.end_text gives
        # it: "Deutsch (du)" in German with "du", other languages from the catalog (before: English for all but de/en)
        default_end_texts={lang: {k: tr(lang, DEFAULT_END_TEXTS["de"][k], DEFAULT_END_TEXTS["en"][k])
                                  for k in DEFAULT_END_TEXTS["en"]} for lang in {*survey.languages, survey.default_language}},
        survey=survey,
        q=q,
        results=results,
        languages=LANGUAGE_NAMES,
        default_end=default_end,
        today=to_local(utcnow()).date(),
        projects=_my_projects(),
        participant_url=links.participant_url(survey),
        groups=settings_page.GROUPS,
        locking=settings_page.LOCKING,
        summaries=settings_page.summaries(survey),
        consent_mentions=settings_page.consent_mentions(survey),
        open_groups=_open_groups(q),
    )


# the group of the settings page that holds each form (section, or `part` of the section "meta")
FORM_GROUPS = {"befragung": "befragung", "teilnahme": "teilnahme", "datenschutz": "einwilligung", "meta": "befragung",
               "end_texts": "abschlusstexte", "image_style": "gestaltung", "theme": "gestaltung",
               "consent_facts": "einwilligung"}


def _open_groups(q):
    """Groups of the settings page that are open from the start, whatever the browser remembers: the one whose
    form comes back refused or after a conflict (shown again within its POST), and the people search."""
    from flask import g

    out = {"beteiligte"} if q else set()
    if request.method == "POST":
        key = (g.get("form_error") or g.get("edit_conflict") or {}).get("key", "")
        section = request.form.get("part") or key.partition(":")[2]
        if request.endpoint == "surveys.consent_facts":
            section = "consent_facts"
        if section in FORM_GROUPS:
            out.add(FORM_GROUPS[section])
    return out


@bp.route("/<sid:survey_id>/quality-options", methods=["POST"])
@login_required
@autosave.aware
def update_quality_options(survey_id):
    """Part "Datenqualität steigern" of the page "Datenerhebung": paradata and the tempo hint, a part of the settings
    section "Projektangaben" (edit guard, "Rückgängig"); the consent text follows as from the settings page."""
    survey = get_survey(survey_id)
    conflict = _settings_conflict(survey, "meta")
    consent = survey.consent_item
    before = {lang: consent.kind.template_body(consent, lang) for lang in survey.languages} if consent else {}
    apply_quality_options(survey, request.form)
    _follow_consent(survey, before)
    if conflict:
        return edit_guard.show_again(lambda: field(survey.id))
    _settings_saved(survey, "meta", name=undo.both("Qualitätssicherung", "Quality assurance"))
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return redirect(url_for("surveys.quality", survey_id=survey.id) + "#prevent")


@bp.route("/<sid:survey_id>/meta", methods=["POST"])
@login_required
@autosave.aware
def update_meta(survey_id):
    """The groups "Befragung", "Teilnahme" and "Einwilligung und Datenschutz" of the settings page, each a form
    of its own (`part`), all one section for the edit guard and "Rückgängig". Without `part` (scripts, older
    pages) everything at once, as before."""
    survey = get_survey(survey_id)
    part = request.form.get("part", "")
    conflict = _settings_conflict(survey, "meta")
    error = None
    if part in ("", "datenschutz"):
        from app import retention

        privacy_error = retention.order_error(survey, request.form)  # audit 2026-10-10, L-4
        if privacy_error:
            return _refused(survey, "settings:privacy", privacy_error)
    if part in ("", "befragung"):
        error = apply_project(survey, request.form)
        apply_languages(survey, request.form)
    if part == "teilnahme":
        _with_consent(survey, lambda: apply_participation(survey, request.form))
    elif part == "datenschutz":
        _with_consent(survey, lambda: apply_privacy(survey, request.form))
    elif not part:
        apply_options_with_consent(survey, request.form)
    if error:
        return _refused(survey, "settings:meta", error)
    if conflict:
        return edit_guard.show_again(lambda: detail(survey.id))
    _settings_saved(survey, "meta", name=undo.both(dict.__getitem__(PART_NAMES, part), PART_NAMES.en[part])
                    if part in PART_NAMES else None)
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return redirect(url_for("surveys.detail", survey_id=survey.id) + (f"#{part}" if part else ""))


# the forms of the section "meta" on the settings page, named as the groups that hold them (label of a step)
PART_NAMES = ByLanguage({"befragung": "Befragung", "teilnahme": "Teilnahme", "datenschutz": "Datenschutz"},
                        {"befragung": "Survey", "teilnahme": "Participation", "datenschutz": "Data protection"})


@bp.route("/<sid:survey_id>/test-runs", methods=["POST"])
@login_required
@autosave.aware
def update_test_runs(survey_id):
    """Feld, "Test- und Dummy-Daten": keep the test runs of the preview (or let each new one replace the last)."""
    survey = get_survey(survey_id)
    survey.keep_test_runs = bool(request.form.get("keep_test_runs"))
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#testing")
# steps of the settings page that change a question, made by a route only admins may use: the key facts of
# the informed consent (consent_facts; the step holds the consent question, whose text "Bearbeiten" may change)
ADMIN_STEP_NODES = {"consent-facts"}


def section_role(section):
    """The role that changes a section of the settings page: "editor" if its route is open to "Bearbeiten"
    (EDIT_ENDPOINTS), else "admin" (also for a section this list does not know)."""
    if section in ("quality", "weighting"):  # the page "Qualitätssicherung" (undo part "settings:quality") and the
        return "editor"                       # weighting on the page "Quoten": open to "Bearbeiten"
    return "editor" if SECTION_ENDPOINTS.get(section) in EDIT_ENDPOINTS else "admin"


@bp.route("/<sid:survey_id>/consent-facts", methods=["POST"])
@login_required
@autosave.aware
def consent_facts(survey_id):
    """Key facts of the informed consent (also asked in the setup wizard); an untouched standard text follows."""
    survey = get_survey(survey_id)
    item = survey.consent_item
    if item is None:
        abort(404)
    problem = item.kind.facts_problem(request.form)
    if problem:
        return _refused(survey, "settings:consent", problem)
    step = undo.begin(survey)
    item.kind.apply_facts(item, request.form, survey.default_language)
    from app import min_age

    min_age.follow(survey, item.config.get("min_age"))  # a screen-out built for the minimum age takes the new one
    what = autosave.what()
    step.done(undo.both("Einwilligung, Eckdaten: {what} geändert", "Consent, key facts: {what} changed", what=what) if what
              else undo.both("Eckdaten der Einwilligung geändert", "Key facts of the consent changed"), page="settings",
              node="consent-facts", merge=autosave.merge("consent-facts"))
    db.session.commit()
    flash(de_en("Eckdaten der Einwilligung gespeichert.", "Key facts of the consent saved."), "success")
    return redirect(url_for("surveys.detail", survey_id=survey.id) + "#consent-facts")


@bp.route("/<sid:survey_id>/min-age-screenout", methods=["POST"])
@login_required
def min_age_screenout(survey_id):
    """Younger than the minimum age ended behind the age question (app/min_age.py), or that end taken out again."""
    from app import min_age

    survey = get_survey(survey_id)
    back = redirect(url_for("surveys.detail", survey_id=survey.id) + "#consent-facts")
    item = survey.consent_item
    if item is None or not survey.can(current_user, "editor"):
        abort(404)
    if not survey.is_editable:
        from app.editor.base import not_draft_message

        flash(not_draft_message(), "error")
        return back
    step = undo.begin(survey)
    if request.form.get("action") == "remove":
        min_age.remove(survey)
        step.done(undo.both("Screen-out für Jüngere entfernt", "Screen-out of younger people removed"), page="flow")
        flash(de_en("Das Ende für Jüngere ist aus dem Ablauf entfernt.", "The end for younger people has been removed from the flow."), "success")
    elif min_age.apply(survey, item.config.get("min_age")):
        step.done(undo.both("Screen-out für Jüngere angelegt", "Screen-out of younger people added"), page="flow")
        flash(de_en("Im Ablauf steht jetzt hinter der Altersfrage eine Bedingung „Jünger als {n}“ mit einem Ende (Screen-out).",
                    "The flow now has a condition “Younger than {n}” with an end (screen-out) behind the age question.",
                    n=item.config.get("min_age")), "success")
    else:
        flash(de_en("Dafür braucht der Fragebogen eine Altersfrage (z. B. das Konstrukt „Alter“) und ein Mindestalter.",
                    "For this the questionnaire needs an age question (e.g. the construct “Age”) and a minimum age."), "error")
        return back
    db.session.commit()
    return back


END_TEXT_KEYS = ("thanks", "declined", "closed", "screenout", "quotafull", "excluded")


@bp.route("/<sid:survey_id>/end-texts", methods=["POST"])
@login_required
@autosave.aware
def update_end_texts(survey_id):
    survey = get_survey(survey_id)
    conflict = _settings_conflict(survey, "end_texts")
    # the settings page sends the source language only, the other languages are on the page "Übersetzung": a
    # language without fields in the form keeps its texts
    texts = dict(survey.end_texts or {})
    for lang in survey.languages:
        if not any(f"{k}_{lang}" in request.form for k in END_TEXT_KEYS):
            continue
        texts[lang] = {k: request.form.get(f"{k}_{lang}", "").strip()[:3000] for k in END_TEXT_KEYS}
    survey.end_texts = texts
    if conflict:
        return edit_guard.show_again(lambda: detail(survey.id))
    _settings_saved(survey, "end_texts")
    db.session.commit()
    flash(de_en("Texte der Endseiten gespeichert.", "End page texts saved."), "success")
    return redirect(url_for("surveys.detail", survey_id=survey.id) + "#end-texts")
