"""Researcher start page after login: the surveys this person co-owns, the pinned (starred) ones on top."""
from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.extensions import db, get_or_create, json_given, json_missing
from app.lang import de_en
from app import access, kind_access
from app.models import Survey, SurveyGroup, SurveyHide, SurveyPin

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@bp.route("/")
@login_required
def index():
    from app import groups

    show_archived = request.args.get("archived") == "1"
    # "Ausgeblendet": the surveys this person took out of their own list (SurveyHide; demo-befragungen-konzept.md)
    show_hidden = request.args.get("hidden") == "1" and not show_archived
    mine = access.survey_ids(current_user)
    hidden = db.select(SurveyHide.survey_id).filter(SurveyHide.user_id == current_user.id)
    # groups of this researcher that have surveys: a filter above the list
    shared = set(db.session.scalars(db.select(SurveyGroup.group_id).distinct()))
    group_filter = [g for g in groups.of(current_user) if g.id in shared]
    group = next((g for g in group_filter if g.id == request.args.get("group", type=int)), None)
    if show_hidden:  # one's own and the example surveys alike
        query = db.select(Survey).filter(Survey.id.in_(hidden), Survey.archived.is_(False),
                                         db.or_(Survey.id.in_(mine), Survey.id.in_(access.showcase_ids(current_user))))
    else:
        query = db.select(Survey).filter(Survey.id.in_(mine), Survey.archived.is_(show_archived))
        if not show_archived:
            query = query.filter(Survey.id.not_in(hidden))
    if group is not None:
        query = query.filter(Survey.id.in_(db.select(SurveyGroup.survey_id).filter_by(group_id=group.id)))
    # example surveys stand only in their own section below, one's own too (Ralf 2026-10-11); where that section is
    # not shown (archive, hidden, a group chosen), they stay in the list
    with_examples_section = not show_archived and not show_hidden and group is None
    if with_examples_section:
        query = query.filter(Survey.id.not_in(access.showcase_query(current_user)))
    # live votes (app/live/) and interview guides (app/interview.py) stand in the same list (live
    # concept 6.1). Two filters that combine (UI audit 2026-10-04, R29, R96): the kind (art=fragebogen,
    # leitfaden, live; live=1 and interview=1 of older links still work) and the group
    has_live = bool(db.session.scalar(db.select(Survey.id).filter(Survey.id.in_(mine), json_given(Survey.live)).limit(1)))
    has_interview = bool(db.session.scalar(db.select(Survey.id).filter(Survey.id.in_(mine), json_given(Survey.interview)).limit(1)))
    has_diary = bool(db.session.scalar(db.select(Survey.id).filter(Survey.id.in_(mine), json_given(Survey.sampling)).limit(1)))
    kind = request.args.get("art") or ("live" if request.args.get("live") == "1" else "leitfaden" if request.args.get("interview") == "1" else "")
    kind = kind if kind in ("fragebogen", "leitfaden", "live", "tagebuch") else ""
    live_only, interview_only = kind == "live", kind == "leitfaden"
    # the members and group links of every card in one query each, not one per card (audit 2026-10-06, P8)
    surveys = db.session.scalars(_of_kind(query, kind).order_by(Survey.updated_at.desc())
                                 .options(selectinload(Survey.members), selectinload(Survey.group_links))).all()
    pinned = set(db.session.scalars(db.select(SurveyPin.survey_id).filter_by(user_id=current_user.id)))
    from app import plugins

    surveys = [s for s in surveys if not plugins.hidden(s)]  # what an add-on module keeps for itself
    surveys = sorted(surveys, key=lambda s: s.id not in pinned)  # pinned first; otherwise last changed first (stable)
    # example surveys (Survey.showcase): a folded section below one's own, open while one has nothing of one's own yet
    # (demo-befragungen-konzept.md, 2b); their team opens and edits them there (Ralf 2026-10-11)
    showcase = []
    if with_examples_section:
        found = db.select(Survey).filter(Survey.id.in_(access.showcase_ids(current_user)), Survey.id.not_in(hidden))
        showcase = db.session.scalars(_of_kind(found, kind).order_by(Survey.updated_at.desc())
                                      .options(selectinload(Survey.members), selectinload(Survey.group_links))).all()
        showcase = sorted(showcase, key=lambda s: s.id not in pinned)
    mine_ids = set(db.session.scalars(db.select(Survey.id).filter(Survey.id.in_([s.id for s in showcase]),
                                                                  Survey.id.in_(mine)))) if showcase else set()
    foreign_examples = any(s.id not in mine_ids for s in showcase)  # what "Alle für mich ausblenden" takes away
    # the section opens while one has nothing of one's own besides examples
    has_own = bool(db.session.scalar(db.select(Survey.id).filter(Survey.id.in_(mine),
                                                                 Survey.id.not_in(access.showcase_query(current_user))).limit(1)))
    cards = surveys + showcase  # no survey stands in both
    done = completed_counts([s.id for s in cards])
    done.update(diary_counts([s.id for s in cards if s.sampling is not None]))
    done.update(interview_counts([s.id for s in cards if s.interview is not None]))
    dummies = dummy_counts([s.id for s in cards if s.status == Survey.DRAFT])
    archived_count = db.session.scalar(
        db.select(db.func.count(Survey.id)).filter(Survey.id.in_(mine), Survey.archived.is_(True))
    )
    hidden_count = db.session.scalar(
        db.select(db.func.count(Survey.id)).filter(Survey.id.in_(hidden), Survey.archived.is_(False),
                                                   db.or_(Survey.id.in_(mine), Survey.id.in_(access.showcase_ids(current_user))))
    )
    return render_template(
        "dashboard/index.html", surveys=surveys, done=done, dummies=dummies, show_archived=show_archived, archived_count=archived_count,
        show_hidden=show_hidden, hidden_count=hidden_count, showcase=showcase, foreign_examples=foreign_examples, has_own=has_own,
        group_filter=group_filter, group=group, access=access, has_live=has_live, live_only=live_only,
        has_interview=has_interview, interview_only=interview_only, kind=kind, has_diary=has_diary,
        lifecycle=__import__("app.lifecycle", fromlist=["deletable"]), pinned=pinned,
        live_now=__import__("app.live", fromlist=["live_now"]).live_now,
        may_create=__import__("app.auth", fromlist=["may_create_surveys"]).may_create_surveys(current_user),
        # the survey types this person sees and may create (app/kind_access.py): buttons and the filter "Art"
        kinds_seen=kind_access.visible_kinds(current_user),
        kinds_made=[k for k in kind_access.KINDS if kind_access.may_create(current_user, k)],
        submissions=__import__("app.submissions", fromlist=["unseen"]).unseen(current_user),
    )


def _of_kind(query, kind):
    """The query narrowed to one kind of survey (the filter "Art"); "" keeps all."""
    if kind == "live":
        return query.filter(json_given(Survey.live))
    if kind == "leitfaden":
        return query.filter(json_given(Survey.interview))
    if kind == "tagebuch":
        return query.filter(json_given(Survey.sampling))
    if kind == "fragebogen":
        return query.filter(json_missing(Survey.live), json_missing(Survey.interview), json_missing(Survey.sampling))
    return query


def completed_counts(ids):
    """{survey id: completed participations} as Survey.completed_count counts them, for all cards in one query."""
    from app.models import Response

    if not ids:
        return {}
    return dict(db.session.execute(
        db.select(Response.survey_id, db.func.count(Response.id)).filter(
            Response.survey_id.in_(ids), Response.completed_at.is_not(None), Response.is_preview.is_(False),
            Response.not_live_extra())  # a further word of a live vote is no participation (Ralf 2026-10-18: 94, not 40)
        .group_by(Response.survey_id)).all())


def diary_counts(ids):
    """{survey id: persons} of diary studies (app/diary.py): a person counts once the entry part is completed; their
    entries are participations of their own, but the card counts people."""
    from app.models import Enrolment

    if not ids:
        return {}
    return dict(db.session.execute(
        db.select(Enrolment.survey_id, db.func.count(Enrolment.id)).filter(
            Enrolment.survey_id.in_(ids), Enrolment.first_day.is_not(None), Enrolment.source == "real")
        .group_by(Enrolment.survey_id)).all())


def interview_counts(ids):
    """{survey id: interviews} of interview guides, counted as under "Interviews" (interview_data.interviews): every
    interview begun, trial interviews left out (concept "Befragungstypen im Vergleich" 2026-10-17, T13)."""
    from app.models import Response

    if not ids:
        return {}
    return dict(db.session.execute(
        db.select(Response.survey_id, db.func.count(Response.id)).filter(
            Response.survey_id.in_(ids), Response.interview.is_not(None), Response.is_preview.is_(False),
            Response.source == "real")
        .group_by(Response.survey_id)).all())


def dummy_counts(ids):
    """{survey id: completed dummy participations} for the drafts among the cards: the results, dashboards and quality
    control of a draft count them, the card says so beside the real ones (UI/UX audit 2026-10-09, X63)."""
    from app.models import Response

    if not ids:
        return {}
    return dict(db.session.execute(
        db.select(Response.survey_id, db.func.count(Response.id)).filter(
            Response.survey_id.in_(ids), Response.completed_at.is_not(None), Response.source == "dummy",
            Response.not_live_extra())
        .group_by(Response.survey_id)).all())


@bp.route("/export")
@login_required
def export():
    """Several surveys as one ZIP file (app/survey_files.py), a folder each; read in again as a whole under "Neue
    Befragung". Every survey this person may duplicate, archived ones too: "Bearbeiten" or "Admin", as
    surveys.export_file (access.may_copy_out; audit 2026-10-18, AUTHZ-05)."""
    from app import survey_files

    surveys = [s for s in db.session.scalars(db.select(Survey).filter(Survey.id.in_(access.survey_ids(current_user)))
                                             .order_by(Survey.archived, Survey.updated_at.desc()))
               if access.may_copy_out(current_user, s)]
    wanted = set(request.args.getlist("survey"))
    if request.args.get("download"):
        from app import heavy

        if not heavy.hold():
            return heavy.busy()
        chosen = [s for s in surveys if s.code in wanted]
        if chosen:
            files = request.args.getlist("files")
            try:
                body = survey_files.write(chosen, with_files=not files or "1" in files)
            except survey_files.transfer.ExportTooLarge as exc:
                abort(413, description=str(exc))
            response = send_file(body, mimetype="application/zip",
                                 as_attachment=True, download_name=survey_files.file_name(chosen))
            response.headers["Cache-Control"] = "no-store"
            return response
        flash(de_en("Bitte mindestens eine Befragung auswählen.", "Please choose at least one survey."), "error")
    return render_template("dashboard/export.html", surveys=surveys, wanted=wanted)


@bp.route("/pin/<sid:survey_id>", methods=["POST"])
@login_required
def pin(survey_id):
    """Pin or unpin (star) a survey of one's own for the top of the list."""
    survey = db.session.get(Survey, survey_id)
    if survey is None or survey.role_of(current_user) is None:
        abort(404)
    row = db.session.scalar(db.select(SurveyPin).filter_by(user_id=current_user.id, survey_id=survey.id))
    if row:
        db.session.delete(row)
    else:  # a double click pins once, not a 500 on the second (audit 2026-10-10, C4)
        get_or_create(SurveyPin, user_id=current_user.id, survey_id=survey.id)
    db.session.commit()
    return redirect(url_for("dashboard.index", **({"archived": "1"} if request.form.get("archived") else {}))
                    + f"#survey-{survey.id}")


@bp.route("/hide/<sid:survey_id>", methods=["POST"])
@login_required
def hide(survey_id):
    """Take a survey out of one's own list ("Für mich ausblenden") or bring it back (hidden=0). Only for this
    person; nothing about the rights changes. Admins of the survey archive it instead (for everyone)."""
    survey = db.session.get(Survey, survey_id)
    role = survey.role_of(current_user) if survey is not None else None
    if role is None:
        abort(404)
    back = url_for("dashboard.index", **({"hidden": "1"} if request.form.get("view") == "hidden" else {}))
    if role == "admin":
        flash(de_en("Als Admin archivieren Sie die Befragung statt sie auszublenden; das gilt dann für alle Beteiligten.",
                    "As an admin you archive the survey instead of hiding it; that then applies to everyone involved."),
              "error")
        return redirect(back)
    row = db.session.scalar(db.select(SurveyHide).filter_by(user_id=current_user.id, survey_id=survey.id))
    if request.form.get("hidden") == "0":
        if row is not None:
            db.session.delete(row)
        flash(de_en("„{title}“ steht wieder in Ihrer Liste.", "“{title}” is back in your list.", title=survey.title),
              "success")
    else:
        get_or_create(SurveyHide, user_id=current_user.id, survey_id=survey.id)  # a double click hides once
        flash(de_en("„{title}“ ist für Sie ausgeblendet; Sie finden sie unter „Ausgeblendet“ unten auf der Seite. Ihre "
                    "Rechte an der Befragung bleiben.",
                    "“{title}” is hidden for you; you find it under “Hidden” at the bottom of the page. Your rights to "
                    "the survey stay.", title=survey.title), "success")
    db.session.commit()
    return redirect(back)


@bp.route("/hide-examples", methods=["POST"])
@login_required
def hide_examples():
    """The button "Alle ausblenden" of the section "Beispielbefragungen": every example survey one sees only as one."""
    shown = db.session.scalars(db.select(Survey.id).filter(
        Survey.id.in_(access.showcase_ids(current_user)), Survey.id.not_in(access.survey_ids(current_user)),
        Survey.id.not_in(db.select(SurveyHide.survey_id).filter(SurveyHide.user_id == current_user.id)))).all()
    for survey_id in shown:
        get_or_create(SurveyHide, user_id=current_user.id, survey_id=survey_id)
    db.session.commit()
    if shown:
        flash(de_en("Die Beispielbefragungen sind für Sie ausgeblendet; Sie finden sie unter „Ausgeblendet“. Neue "
                    "Beispiele erscheinen wieder im Abschnitt.",
                    "The example surveys are hidden for you; you find them under “Hidden”. New examples appear in the "
                    "section again."), "success")
    return redirect(url_for("dashboard.index"))
