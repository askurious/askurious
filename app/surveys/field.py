"""Tab Datenerhebung: the field page, live view and pretest links."""
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import lifecycle, links
from app.extensions import db
from app.lang import de_en
from app.models import Survey, utcnow
from app.surveys.base import (_hide_names, _rule_conditions, bp, get_survey, pool_counts, pool_frozen,
                              pool_frozen_note, stored_pool)
from app.timeutil import last_day, to_local


@bp.route("/<sid:survey_id>/field")
@login_required
def field(survey_id):
    """Fieldwork: status (start, pause, close, reopen, archive, sample target) and the timeline."""
    from app import timeline

    survey = get_survey(survey_id)
    by_source = pool_counts(survey)
    from app import design, panel

    # what would stop the start (lifecycle.start checks the same), shown before anyone clicks
    findings = design.check(survey, start=True) if survey.status == Survey.DRAFT else []
    start_errors = design.blocking(findings)
    # what stops the dummy data: the errors of the draft's check, which leaves out what only the start needs (X60)
    dummy_errors = design.blocking(design.check(survey)) if start_errors else []
    start_warnings = [f for f in findings if f.severity == design.WARNING]
    from app import quota_views, quotas

    quota_state = quota_views.state_context(survey, only=quotas.provider(survey, request.args.get("qp", ""))) \
        if survey.quotas else {"quota_report": []}
    from app import external
    from app.editor import PRETEST_DAYS, qr_svg, share_url
    from app.models import PreviewComment

    # a pretest link is made on request (?pretest=7) and shown once; it is signed, nothing is stored (R11)
    days = request.args.get("pretest", type=int)
    pretest = None
    if days in PRETEST_DAYS and survey.can(current_user, "editor"):
        url = share_url(survey, days)
        pretest = {"url": url, "days": days, "qr": qr_svg(url)}
    open_comments = db.session.scalar(db.select(db.func.count()).select_from(PreviewComment)
                                      .filter_by(survey_id=survey.id, status=PreviewComment.OPEN))
    plan_end = lifecycle.planned_end(survey) if survey.start_at else (None, None)

    from app import diary

    # a diary study: the block "Beitreten" (join code, QR code, test codes, invitation codes) in place of the link
    diary_join = diary.join_context(survey) if survey.sampling is not None else {}
    return render_template("surveys/field.html", survey=survey, tl=timeline.build(survey), today=to_local(utcnow()).date(), **diary_join,
                           pretest=pretest, pretest_days=PRETEST_DAYS, open_comments=open_comments, start_warnings=start_warnings,
                           outside=external.outside(survey),
                           start_errors=start_errors, dummy_errors=dummy_errors, shares=quota_views.shares(survey) if survey.quotas and survey.can(current_user, "admin") else [], **quota_state,
                           by_source=by_source, pool=stored_pool(survey), pool_frozen=pool_frozen(survey), pool_frozen_note=pool_frozen_note(),
                           participant_url=links.participant_url(survey), links=links,
                           groups=_hide_names(panel.group_counts(survey, usable=False), survey), panel_cfg=panel.config(survey),
                           value_shares=__import__("app.boards", fromlist=["value_share_urls"]).value_share_urls(survey)
                           if survey.role_of(current_user) == "admin" else {},
                           value_board=__import__("app.boards", fromlist=["value_board"]).value_board,
                           panel=panel, **_rule_conditions(survey), **_copy_links(survey),
                           geo_counts=__import__("app.geoip", fromlist=["field_counts"]).field_counts(survey),
                           cap_notes=__import__("app.limits", fromlist=["cap_notes"]).cap_notes(survey),
                           default_end=last_day(plan_end[0] or survey.field_end or lifecycle.default_field_end()),
                           paper=_paper_overview(survey), runs=_runs(survey), **_plan_context(survey, plan_end))


def _runs(survey):
    """The runs of a questionnaire ("Neuer Durchgang", lifecycle.runs) with the completed real participations begun in
    each; None where there are none (a draft, a live vote, an interview guide, a diary study)."""
    if not lifecycle.may_start_run(survey):
        return None
    from app.models import Response

    out = [{**r, "n_done": 0} for r in lifecycle.runs(survey)]
    starts = db.session.scalars(db.select(Response.started_at).filter(
        Response.survey_id == survey.id, Response.source == "real", Response.is_preview.is_(False),
        Response.status == Response.COMPLETED))
    for at in starts:
        out[lifecycle.run_of(out, at) - 1]["n_done"] += 1
    return out


def _paper_overview(survey):
    """The block "Papierfragebogen" (app/paper.py), or None for a live vote, an interview guide and a diary study
    (joined only in the app)."""
    from app import paper

    if survey.live is not None or survey.interview is not None or survey.sampling is not None:
        return None
    return paper.overview(survey)


def _plan_context(survey, plan_end):
    """The block "Start planen" (_status.html): the planned start or tomorrow at 8:00 as its default, the end criteria
    it goes with, and the note of a planned start that did not go through."""
    from datetime import timedelta

    planned = to_local(survey.start_at) if survey.start_at else None
    day = planned.date() if planned else to_local(utcnow()).date() + timedelta(days=1)
    return {"plan_day": day.isoformat(), "plan_time": planned.strftime("%H:%M") if planned else "08:00",
            "plan_end": plan_end if survey.start_at else None, "start_failure": lifecycle.planned_failure(survey)}


LIVE_WINDOW = 600  # seconds: who was active within the last 10 minutes counts as "in the survey"


@bp.route("/<sid:survey_id>/pretest/revoke", methods=["POST"])
@login_required
def revoke_pretest(survey_id):
    """Every pretest and phone link made so far stops working (they carry the generation they were made in)."""
    survey = get_survey(survey_id, need="editor")
    survey.panel = {**(survey.panel or {}), "pretest_gen": int((survey.panel or {}).get("pretest_gen") or 0) + 1}
    db.session.commit()
    flash(de_en("Alle bisherigen Pretest-Links sind ungültig, auch die aus der Vorschau fürs Handy.",
                "All previous pretest links are invalid, also those from the preview for phones."), "success")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#testing")


@bp.route("/<sid:survey_id>/field/live")
@login_required
def field_live(survey_id):
    """Who is in the survey right now, as a feeling rather than a count: per active participation only the
    progress (0..1 of its own pages), the seconds since its last page and a stable vertical jitter.
    No ids, no answers. Real participations only (no preview, test or dummy)."""
    import hashlib
    from datetime import timedelta

    from app import duration, runtime
    from app.models import Response as R

    survey = get_survey(survey_id)
    now = utcnow()
    since = now - timedelta(seconds=LIVE_WINDOW)
    rows = db.session.scalars(db.select(R).filter(R.survey_id == survey.id, R.status == R.ACTIVE, R.is_preview.is_(False),
                                                  R.source == "real", db.func.coalesce(R.last_active_at, R.started_at) >= since))
    people = []
    for r in rows:
        pages = runtime.page_entries(r.layout) if r.layout else []
        done = pages.index(r.position) if r.position in pages else 0
        seen = r.last_active_at or r.started_at
        digest = hashlib.sha1(f"{survey.uid}:{r.id}".encode()).hexdigest()
        jitter, spread = int(digest[:6], 16) / 0xFFFFFF, int(digest[6:12], 16) / 0xFFFFFF
        # somewhere within the current page (not exactly on it), so a few pages do not become columns
        progress = (done + 0.15 + 0.7 * spread) / max(len(pages), 1)
        people.append({"p": round(progress, 3), "a": max(0, int((now - seen).total_seconds())), "j": round(jitter, 3)})
    from app import external

    return {"people": people, "estimate": round(duration.estimate(survey)["typical"]), "window": LIVE_WINDOW,
            "outside": external.outside(survey, now)}


def _copy_links(survey):
    """The link for a panel (all declared URL parameters) and one link per allowed value of a parameter that is
    not stored (stored ones are in "Links je Gruppe" with their counts), for the page "Datenerhebung"."""
    from app import panel

    url = links.participant_url(survey)
    stored = {p["name"] for p in panel.config(survey)["params"] if p.get("mode") == "store"}
    return {"panel_link": panel.entry_link(survey, url),
            "value_links": [row for row in panel.group_links(survey, url) if row[0] not in stored]}


def _board_tests(survey, f):
    """The test runs of this pool the survey's dashboards leave out (boards.pool_of), 0 without dashboards: the
    results page says so, so the two do not seem to disagree (Ralf, 2026-10-06)."""
    from app.models import Response

    if not survey.boards or "test" not in f["sources"]:
        return 0
    return sum(n for (source, _, status), n in survey.tally().items()
               if source == "test" and (f["all"] or status == Response.COMPLETED))


def _to_field(survey, anchor=""):
    return redirect(url_for("surveys.field", survey_id=survey.id) + anchor)


def _diary_scales(survey, f, key, table):
    """The scales of the modules as two-level models (app/diary_scales.py), kept like the course over time."""
    import hashlib
    import json

    from app import diary_scales, export, results_store

    stamp = key and f"{key}:" + hashlib.sha256(json.dumps(survey.sampling, sort_keys=True, default=str).encode()).hexdigest()
    kept = results_store.load(survey, stamp, f, name="diaryscales") if stamp else None
    if kept is not None:
        return kept[0]
    rows = table if table is not None and not f["all"] else export.rows(
        survey, sources=tuple(f["sources"]), with_hb=False, with_quality=False)
    found = diary_scales.scales(survey, rows)
    if stamp:
        results_store.save(survey, stamp, found, f, name="diaryscales", basis=key)
    return found


def _diary_time(survey, f, key, table):
    """The entries of a diary study over time (diary.time_results), kept next to the results under their
    fingerprint and the schedule: a page from the kept results used to build every row of the export again, and a
    fresh one built them twice (audit 2026-10-11, M10). table: the rows the page read, if any."""
    import hashlib
    import json

    from app import diary, results_store

    stamp = key and f"{key}:" + hashlib.sha256(json.dumps(survey.sampling, sort_keys=True, default=str).encode()).hexdigest()
    kept = results_store.load(survey, stamp, f, name="diarytime") if stamp else None
    if kept is not None:
        return kept[0]
    # the page's rows are the rows time_results reads, unless they hold unfinished participations too
    found = diary.time_results(survey, f["sources"], rows=table if table is not None and not f["all"] else None)
    if stamp:
        results_store.save(survey, stamp, found, f, name="diarytime", basis=key)
    return found
