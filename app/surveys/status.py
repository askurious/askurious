"""Starting, planning, closing, reopening, archiving, deleting and duplicating a survey."""
from datetime import date

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import lifecycle
from app.extensions import db
from app.lang import de_en
from app.models import Survey
from app.surveys.base import bp, get_survey
from app.surveys.field import _to_field
from app.timeutil import day_end_utc, fmt


def _parse_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _end_criteria():
    """The end criteria of the start form: (field_end, max_responses), or None after a flash for a date that is
    none."""
    field_end = None
    if request.form.get("use_end"):
        try:
            field_end = day_end_utc(date.fromisoformat(request.form.get("field_end", "")))
        except ValueError:
            flash(de_en("Das Feldende ist kein gültiges Datum. Bitte im Kalender wählen.",
                        "The end of data collection is not a valid date. Please choose it in the calendar."), "error")
            return None
    return field_end, (_parse_int(request.form.get("max_responses")) if request.form.get("use_max") else None)


@bp.route("/<sid:survey_id>/start", methods=["POST"])
@login_required
def start(survey_id):
    survey = get_survey(survey_id)
    criteria = _end_criteria()
    if criteria is None:
        return _to_field(survey)
    try:
        frozen, done = lifecycle.start_committed(survey, *criteria)
    except lifecycle.LifecycleError as e:
        flash(str(e), "error")
    else:
        if frozen:
            flash(de_en("Die Befragung ist gestartet. Der Fragebogen ist als Präregistrierung eingefroren (Reiter „Export“).",
                        "The survey has started. The questionnaire is frozen as a pre-registration (tab “Export”)."), "success")
        else:
            flash(de_en("Die Befragung ist gestartet.", "The survey has started."), "success")
        if done:  # the optional automatic upload to Coscine (app/coscine.py); never stops the start
            flash(done[1], "success" if done[0] else "warning")
        session["confetti"] = True  # the next page celebrates once (base.html, app.js)
    return _to_field(survey)


@bp.route("/<sid:survey_id>/start/plan", methods=["POST"])
@login_required
def plan_start(survey_id):
    """"Start planen": the survey starts by itself at a date and time (Europe/Berlin) with the end criteria of the
    same form (lifecycle.start_due_surveys). Who may start may plan; the checks of the start hold already."""
    from datetime import time as clock_time

    from app.lang import english
    from app.timeutil import local_to_utc

    survey = get_survey(survey_id)
    criteria = _end_criteria()
    if criteria is None:
        return _to_field(survey, "#status")
    try:
        at = local_to_utc(date.fromisoformat(request.form.get("start_date", "")),
                          clock_time.fromisoformat(request.form.get("start_time", "")))
    except ValueError:
        flash(de_en("Bitte Datum und Uhrzeit des Starts wählen.", "Please choose the date and time of the start."), "error")
        return _to_field(survey, "#status")
    try:
        lifecycle.plan_start(survey, at, *criteria, by=current_user)
    except lifecycle.LifecycleError as e:
        flash(str(e), "error")
    else:
        db.session.commit()
        flash(de_en("Der Start ist geplant: {d}. Bis dahin sehen Teilnehmende unter dem Link nur, wann die Befragung beginnt.",
                    "The start is planned: {d}. Until then participants see under the link only when the survey begins.",
                    d=fmt(at, lang="en" if english() else "de")), "success")
    return _to_field(survey, "#status")


@bp.route("/<sid:survey_id>/start/cancel", methods=["POST"])
@login_required
def cancel_start(survey_id):
    """No planned start any more (also removes the note of a planned start that did not go through)."""
    survey = get_survey(survey_id)
    failed = lifecycle.planned_failure(survey)
    lifecycle.cancel_plan(survey)
    db.session.commit()
    if not failed:
        flash(de_en("Der geplante Start ist aufgehoben.", "The planned start is cancelled."), "success")
    return _to_field(survey, "#status")


def _back(survey):
    if request.form.get("next") == "dashboard":
        return redirect(url_for("dashboard.index"))
    return _to_field(survey)


@bp.route("/<sid:survey_id>/close", methods=["POST"])
@login_required
def close(survey_id):
    survey = get_survey(survey_id)
    paused = request.form.get("pause") == "1"
    try:
        lifecycle.close(survey, "paused" if paused else "manual")
    except lifecycle.LifecycleError as e:
        flash(str(e), "error")
    else:
        db.session.commit()
        if not paused:
            _suggest_zenodo(survey)
        flash(de_en("{title}: pausiert.", "{title}: paused.", title=survey.title) if paused
              else de_en("{title}: geschlossen.", "{title}: closed.", title=survey.title), "success")
    return _back(survey)


@bp.route("/<sid:survey_id>/new-run", methods=["POST"])
@login_required
def new_run(survey_id):
    """"Neuer Durchgang": the live comparisons count from now on; all data stay (lifecycle.new_run)."""
    survey = get_survey(survey_id)
    try:
        name = lifecycle.new_run(survey, request.form.get("name", ""))
    except lifecycle.LifecycleError as e:
        flash(str(e), "error")
    else:
        db.session.commit()
        flash(de_en("„{name}“ hat begonnen: Menschenmenge, Streubild und Vergleiche zählen ab jetzt neu. Alle Daten bleiben; "
                    "der Export nennt den Durchgang jeder Teilnahme.",
                    "“{name}” has begun: crowd, scatter and comparisons count anew from now on. All data stay; the export "
                    "names the run of every participation.", name=name), "success")
    return _to_field(survey, "#status")


def _suggest_zenodo(survey):
    """After closing by hand: the next page recommends the Zenodo upload once, as a dialog (base.html, app.js;
    Philipp, 2026-10-08). Not for live votes, not without real participations, not when the data set is already
    published, and not on a server without Zenodo."""
    from app import zenodo_auth

    if (survey.live is not None or (survey.zenodo or {}).get("state") == "published" or not survey.has_real_responses()
            or not any(zenodo_auth.available(t) for t in ("zenodo", "sandbox"))):
        return
    session["zenodo_hint"] = {"title": survey.title, "url": url_for("surveys.data", survey_id=survey.id) + "#zenodo"}


@bp.route("/<sid:survey_id>/archive", methods=["POST"])
@login_required
def archive(survey_id):
    survey = get_survey(survey_id)
    if survey.status == Survey.RUNNING:
        flash(de_en("Laufende Befragungen müssen vor dem Archivieren geschlossen werden.",
                    "Running surveys must be closed before archiving."), "error")
        return _to_field(survey)
    survey.archived = request.form.get("archived") == "1"
    if survey.archived:
        lifecycle.cancel_plan(survey)  # an archived survey does not start at its planned moment (code review 2026-10-15, H4)
    db.session.commit()
    restore = (de_en("wiederherstellen oder endgültig löschen", "restore or delete permanently") if lifecycle.deletable(survey)
               else de_en("wiederherstellen", "restore"))
    flash(de_en("„{title}“ ist archiviert. Unter „Archiv“ am Ende der Übersicht lässt sie sich {restore}; "
                "Daten wurden nicht gelöscht.",
                "“{title}” is archived. Under “Archive” at the end of the overview you can {restore} it; "
                "no data was deleted.", title=survey.title, restore=restore) if survey.archived
          else de_en("„{title}“ ist wieder in der Übersicht.", "“{title}” is back in the overview.", title=survey.title), "success")
    return redirect(url_for("dashboard.index"))


@bp.route("/<sid:survey_id>/delete", methods=["POST"])
@login_required
def delete(survey_id):
    """Delete an archived survey; admins of the survey only. Archiving comes first, so nothing is deleted straight
    from the overview. A draft that never had a real participation goes at once (lifecycle.deletable); one with
    real participations goes with all its data after typing its code in the question and a fresh sign-in."""
    survey = get_survey(survey_id, need="admin")
    title = survey.title
    # an archived survey with real participations (Philipp, 2026-10-04): with its data, asked first on the card
    # ("with_data" says the form meant it) and after a fresh sign-in, since it cannot be undone
    with_data = survey.archived and not lifecycle.deletable(survey)
    if with_data:
        from app import accounts

        if request.form.get("with_data") != "1":
            flash(de_en("Diese Befragung hat echte Teilnahmen; bitte über „Mit allen Daten löschen“ im Archiv.",
                        "This survey has real participations; please use “Delete with all data” in the archive."), "error")
            return redirect(url_for("dashboard.index", archived=1))
        again = accounts.need_fresh(url_for("dashboard.index", archived=1))
        if again:
            return again
    try:
        if lifecycle.deletable(survey) and not survey.archived:
            raise lifecycle.LifecycleError(de_en("Entwürfe lassen sich nur aus dem Archiv löschen. Bitte zuerst archivieren.",
                                                 "Drafts can only be deleted from the archive. Please archive first."))
        lifecycle.delete(survey, force=with_data)
    except lifecycle.LifecycleError as e:
        flash(str(e), "error")
        return redirect(url_for("dashboard.index", archived=1 if survey.archived else None))
    db.session.commit()
    flash(de_en("„{title}“ wurde endgültig gelöscht.", "“{title}” was deleted permanently.", title=title), "success")
    return redirect(url_for("dashboard.index", archived=1))


@bp.route("/<sid:survey_id>/duplicate", methods=["GET", "POST"])
@login_required
def duplicate(survey_id):
    """A new draft with the questionnaire and settings of this survey, without anything collected with it
    (app/duplicate.py). The page asks for the new title and whether the co-owners come along."""
    from app import access, constructs, duplicate as dup, linked

    # an example survey seen only as one: "Als Kopie übernehmen", alone and without links to the original
    found = db.session.get(Survey, survey_id)
    copy_only = found is not None and access.showcase_only(current_user, found)
    survey = get_survey(survey_id, need="viewer" if copy_only else "editor")
    if constructs.restricted_keys(survey):
        flash(constructs.permission_notice(), "error")
        return redirect(url_for("surveys.detail", survey_id=survey.id))
    if not dup.may(current_user, survey):
        flash(dup.not_allowed(), "error")
        return redirect(url_for("dashboard.index"))
    # questions already linked stay linked only with the tick, which is set beforehand then (Ralf, 2026-10-28)
    has_linked = sum(1 for b in survey.blocks for i in b.items if linked.is_linked(i))
    offer_link = not copy_only and (survey.kind == "survey" or has_linked > 0)
    title, with_people, link, error = dup.suggested_title(survey), not copy_only, offer_link and has_linked > 0, None
    if request.method == "POST":
        title, with_people = request.form.get("title", ""), bool(request.form.get("with_people")) and not copy_only
        link = bool(request.form.get("link")) and offer_link
        error = dup.title_error(survey, title)
        if not error:
            new, notes = dup.create(survey, current_user, title, with_people, link=link)
            flash(de_en("„{new}“ ist als Entwurf angelegt: Fragebogen und Einstellungen von „{old}“, ohne Teilnahmen und Daten.",
                        "“{new}” is created as a draft: questionnaire and settings of “{old}”, without participations and data.",
                        new=new.title, old=survey.title), "success")
            for note in notes:
                flash(note, "warning")
            return redirect(url_for("surveys.detail", survey_id=new.id))
    others = [m for m in survey.members if m is not survey.member_of(current_user) and not m.is_former]
    groups = [l.group for l in survey.group_links if l.group.member_of(current_user) is not None]
    return render_template("surveys/duplicate.html", survey=survey, title=title, with_people=with_people, link=link, error=error,
                           others=others, groups=groups, skipped_groups=len(survey.group_links) - len(groups),
                           copy_only=copy_only, offer_link=offer_link, has_linked=has_linked)


@bp.route("/<sid:survey_id>/reopen", methods=["POST"])
@login_required
def reopen(survey_id):
    survey = get_survey(survey_id)
    if request.form.get("confirm") != "1":
        flash(de_en("Bitte bestätigen Sie, dass Sie die Folgen für die bisherigen Daten verstanden haben.",
                    "Please confirm that you understand the consequences for the data so far."), "error")
        return _to_field(survey, "#status")
    try:
        lifecycle.reopen_for_editing(survey)
    except lifecycle.LifecycleError as e:
        flash(str(e), "error")
        return _to_field(survey)
    db.session.commit()
    flash(de_en("Zur Bearbeitung geöffnet: Fragebogen-Fassung {v}. Der Stand der bisherigen Daten ist im Reiter „Export“ gesichert.",
                "Opened for editing: questionnaire revision {v}. The state of the data so far is saved in the tab “Export”.",
                v=survey.qversion), "success")
    return redirect(url_for("editor.index", survey_id=survey.id))
