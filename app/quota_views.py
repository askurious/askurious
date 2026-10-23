"""The page "Quoten" of a survey, steering the quotas during fieldwork, and the state of the quotas to read,
print, download and share (app/quotas.py does the work).

The page is the tab "Quoten" of a survey (left of "Datenerhebung"). Its routes hang on the flow blueprint (/surveys/<code>/flow/quotas/...,
where the page stood before it became a tab), and saving needs the same role as editing the flow ("Bearbeiten"). Steering during fieldwork (targets, closing a
cell, recounting, links for providers) is for admins of the survey. The state itself holds numbers only, so
every role may see and load it; a share link (/q/<token>) shows it without signing in.
"""
import secrets
from datetime import timedelta

from flask import Blueprint
from flask import Response as FlaskResponse
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, lifecycle, quotas, undo
from app.editor import _lang, _require_draft, flow_bp
from app.extensions import db
from app.lang import de_en
from app.models import QuotaShare, Survey, utcnow
from app.surveys import LANGUAGE_NAMES, get_survey

share_bp = Blueprint("quota_share", __name__, url_prefix="/q")
SHARE_DAYS = 60       # how long a share link works when no field end is known
SHARE_AFTER_FIELD = 14  # days a share link works beyond the field end
MAX_SHARES = 20


def _only(survey):
    """The provider chosen with ?qp=name=value, or None."""
    return quotas.provider(survey, request.args.get("qp", ""))


def _provider_counts(survey, only, kept=False):
    """What `quotas._from_responses` says for a provider. kept: for a share link (/q/<token>), from at most
    BALANCE_SECONDS ago per server process (app/balance.py): counting a provider reads every real participation
    of the survey, and whoever holds the link could otherwise make the server do that with every request (S10 of
    the security audit of 2026-10-03)."""
    if not kept:
        return quotas._from_responses(survey, only)
    from app import balance

    return balance.cached(("quota-share", survey.id, tuple(only)), lambda: quotas._from_responses(survey, only))


def without_caps(entries):
    """The report without the quota of "Höchstzahl je Link" (panel.set_cap): its cells are named after the codes of the
    links per person, and whoever holds a shared link could build those links from them. A quota link (/q/) leaves
    it out; the researchers see it in the app (audit 2026-10-09b, V2)."""
    from app.panel import CAP_PREFIX

    return [e for e in entries if not (e["quota"].code or "").startswith(CAP_PREFIX)]


def state_context(survey, lang=None, only=None, conds=True, providers=True, kept=False, public=False):
    """What surveys/_quotas.html and the stand-alone state page need. The participations of a provider (`only`)
    are counted once for the tables, the text to copy and the totals. conds=False: nothing in it says the
    conditions of the cells. providers=False: for a page that offers no choice of provider (a share link).
    kept: the count of a provider may be a few seconds old (_provider_counts). public: a quota link, without the
    quota of "Höchstzahl je Link" (without_caps)."""
    counts = _provider_counts(survey, only, kept) if only else None
    report = quotas.report(survey, lang, only, conds, counts) if survey.quotas else []
    if public:
        report = without_caps(report)
    sums = quotas.totals(survey, only, counts)
    return dict(quota_report=report,
                # leaps of the running places of a cell, for the researchers' tabs (not on a share link; S20)
                quota_jumps=quotas.jumps(survey) if survey.quotas and not kept and survey.status == Survey.RUNNING else [],
                quota_text=quotas.report_text(survey, lang, conds=conds, only=only, entries=report, sums=sums) if survey.quotas else "",
                quota_totals=sums, quota_now=utcnow(), quota_only=only,
                quota_providers=quotas.providers(survey) if providers and survey.quotas else [],
                quota_share_after=SHARE_AFTER_FIELD, link_validity=__import__("app.boards", fromlist=["VALIDITY"]).VALIDITY)


def _page(survey_id, **anchor):
    return redirect(url_for("flow_page.quotas", survey_id=survey_id, **anchor))


@flow_bp.route("/quotas/", endpoint="quotas")
@login_required
def quota_page(survey_id):
    survey = get_survey(survey_id)
    lang = _lang(survey)
    findings = [(severity, message) for severity, message in quotas.check(survey)]
    described = quotas.describe(survey, lang)
    from app import conditions

    quota_data = conditions.page_data(described, quotas.MAX_CONDS, quotas.MAX_RANGES)
    # the state of a started survey is here too, next to the steering (UI audit 2026-10-04, G4)
    state = state_context(survey, only=quotas.provider(survey, request.args.get("qp", "")))         if survey.quotas and survey.status != survey.DRAFT else {"quota_report": []}
    return render_template(
        "editor/quotas.html", survey=survey, lang=lang, languages=LANGUAGE_NAMES, quotas=quotas, report=quotas.report(survey, lang),
        editable=survey.is_editable and survey.can(current_user, "editor"),
        steering=not survey.is_editable and survey.can(current_user, "admin"),
        findings=findings, described=described, quota_data=quota_data, check_nodes=quotas.nodes(survey),
        weighting=weighting_context(survey), shares=shares(survey) if survey.quotas else [], **state)


def weighting_context(survey):
    """What the section "Gewichtung" shows: the setting, the attributes it may tick, the targets and problems."""
    from app import census, weights

    found = weights.census_questions(survey)
    margins, problems = weights.margins(survey, shown=True)
    return {"setting": weights.setting(survey), "kinds": weights.kinds(), "attributes": census.attributes(),
            "census_found": found, "missing": weights.missing(), "margins": margins, "problems": problems, "active": weights.active(survey),
            "low": weights.LOW, "high": weights.HIGH, "may": survey.can(current_user, "editor"),
            "sources": [(de_en("Gewichtung: Raking (iterative proportionale Anpassung) und effektive Fallzahl",
                                "Weighting: raking (iterative proportional fitting) and effective sample size"),
                          [weights.DEMING_STEPHAN, weights.KISH]),
                         (de_en("Gewichtung und Repräsentativität nicht zufälliger Stichproben",
                                "Weighting and representativeness of non-probability samples"), [weights.AAPOR])]}


@flow_bp.route("/quotas/weighting", methods=["POST"], endpoint="quota_weighting")
@login_required
@autosave.aware
def quota_weighting(survey_id):
    """The weighting of the results (app/weights.py, Survey.weighting): role "Bearbeiten", in a draft and in the field
    alike (it changes no participation, only how the results are counted); in a draft a step for "Rückgängig"."""
    from app import weights

    survey = get_survey(survey_id, need="editor")
    wanted = weights.from_form(request.form)
    if wanted != survey.weighting:
        step = undo.begin(survey)
        survey.weighting = wanted
        step.done(undo.both("Gewichtung geändert", "Weighting changed") if wanted
                  else undo.both("Gewichtung ausgeschaltet", "Weighting switched off"),
                  page="quotas", merge=autosave.merge("weighting"))
        db.session.commit()
    flash(de_en("Gewichtung gespeichert.", "Weighting saved.") if wanted else de_en("Keine Gewichtung.", "No weighting."), "success")
    return _page(survey.id, _anchor="weighting")


@flow_bp.route("/quotas/add", methods=["POST"], endpoint="quota_add")
@login_required
def quota_add(survey_id):
    survey = get_survey(survey_id)
    if not _require_draft(survey):
        return _page(survey.id)
    if len(survey.quotas) >= quotas.MAX_QUOTAS:
        flash(de_en("Höchstens {n} Quoten je Befragung.", "At most {n} quotas per survey.", n=quotas.MAX_QUOTAS), "error")
        return _page(survey.id)
    step = undo.begin(survey)
    quota = quotas.add(survey)
    db.session.flush()
    if request.form.get("with_flow"):  # the quota as one step: its calculation and an end "Quote voll" come along
        quotas.add_to_flow(survey, quota)
        flash(de_en("Im Ablauf stehen jetzt am Ende eine Quotenberechnung und ein Ende „Quote voll“ für diese Quote. Die "
                    "Berechnung gehört hinter die Fragen, auf die sich die Teilquoten beziehen: dort hinziehen.",
                    "The flow now ends with a quota calculation and an end “Quota full” for this quota. The "
                    "calculation belongs after the questions the sub-quotas refer to: drag it there."), "success")
    step.done(undo.both("Quote „{name}“ angelegt", "Quota “{name}” created", name=quota.name), page="quotas", node=str(quota.id))
    db.session.commit()
    return _page(survey.id, _anchor=f"quota-{quota.id}")


@flow_bp.route("/quotas/<int:quota_id>", methods=["POST"], endpoint="quota_save")
@login_required
@autosave.aware
def quota_save(survey_id, quota_id):
    survey = get_survey(survey_id)
    quota = quotas.get(survey, quota_id)
    if quota is None:
        abort(404)
    if not _require_draft(survey):
        return _page(survey.id)
    step = undo.begin(survey)
    error = quotas.update_from_form(survey, quota, request.form)
    if error:
        db.session.rollback()
        flash(de_en("Nicht gespeichert: {error}", "Not saved: {error}", error=error), "error")
    else:
        db.session.flush()
        what = autosave.what()
        step.done(undo.both("Quote „{name}“: {what} geändert", "Quota “{name}”: {what} changed", name=quota.name, what=what) if what
                  else undo.both("Quote „{name}“ geändert", "Quota “{name}” changed", name=quota.name), page="quotas",
                  node=str(quota.id), merge=autosave.merge(f"quota:{quota.id}"))
        autosave.fill({f"{p}_id": cell.id for p, cell in getattr(quota, "form_rows", {}).items()})
        db.session.commit()
        flash(de_en("Quote „{name}“ gespeichert.", "Quota “{name}” saved.", name=quota.name), "success")
    return _page(survey.id, _anchor=f"quota-{quota_id}")


@flow_bp.route("/quotas/quality", methods=["POST"], endpoint="quota_marked")
@login_required
@autosave.aware
def quota_marked(survey_id):
    """The setting of the page "Quoten" for the whole survey: participations that quality assurance marks as they
    complete fill no place (Survey.quota_drop_marked, quotas.settle). In a draft with the role "Bearbeiten" (a
    step for "Rückgängig"); in the field it steers the quotas, as the other steering for admins, and goes into the
    log; it applies to the completions from then on."""
    survey = get_survey(survey_id)
    if not survey.is_editable:
        survey = get_survey(survey_id, need="admin")
    wanted = bool(request.form.get("drop_marked"))
    if wanted != bool(survey.quota_drop_marked):
        step = undo.begin(survey)
        survey.quota_drop_marked = wanted
        step.done(undo.both("Quoten: Markierte Teilnahmen füllen keinen Platz", "Quotas: marked participations fill no place") if wanted
                  else undo.both("Quoten: Markierte Teilnahmen füllen ihren Platz", "Quotas: marked participations fill their place"),
                  page="quotas")
        if not survey.is_editable:
            lifecycle.note(survey, "quota", "von der Qualitätssicherung markierte Teilnahmen "
                           + ("füllen ab jetzt keinen Quotenplatz" if wanted else "füllen ab jetzt ihren Quotenplatz"))
        db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return _page(survey.id, _anchor="quota-marked")


@flow_bp.route("/quotas/<int:quota_id>/delete", methods=["POST"], endpoint="quota_delete")
@login_required
def quota_delete(survey_id, quota_id):
    from sqlalchemy.orm.attributes import flag_modified

    survey = get_survey(survey_id)
    quota = quotas.get(survey, quota_id)
    if quota is None:
        abort(404)
    if not _require_draft(survey):
        return _page(survey.id)
    name = quota.name
    step = undo.begin(survey)
    quotas.remove(survey, quota)
    flag_modified(survey, "flow")
    step.done(undo.both("Quote „{name}“ gelöscht", "Quota “{name}” deleted", name=name), page="quotas", node=str(quota_id))
    db.session.commit()
    # no question before deleting: "Rückgängig" brings the quota back, with its Teilquoten and its places in the flow
    flash(de_en("Quote „{name}“ gelöscht.", "Quota “{name}” deleted.", name=name), "undo")
    return _page(survey.id)


@flow_bp.route("/quotas/<int:quota_id>/cross", methods=["POST"], endpoint="quota_generate")
@login_required
def quota_generate(survey_id, quota_id):
    """Cross the attributes picked in the form into cells (quotas.attributes_from_form, generate_cells);
    afterwards they are cells like any other."""
    survey = get_survey(survey_id)
    quota = quotas.get(survey, quota_id)
    if quota is None:
        abort(404)
    if not _require_draft(survey):
        return _page(survey.id)

    def number(key):
        raw = request.form.get(key, "").strip()
        return min(int(raw), 10_000_000) if quotas._digits(raw) else 0

    step = undo.begin(survey)
    try:
        n = quotas.generate_cells(survey, quota, quotas.attributes_from_form(survey, request.form), number("target"),
                                  number("total"), bool(request.form.get("replace")))
    except ValueError as e:
        db.session.rollback()
        flash(de_en("Nichts erzeugt: {e}", "Nothing created: {e}", e=e), "error")
    else:
        db.session.flush()
        step.done(undo.both("Teilquoten von „{name}“ erzeugt", "Sub-quotas of “{name}” created", name=quota.name), page="quotas", node=str(quota.id))
        db.session.commit()
        flash(de_en("{n} Teilquote erzeugt (alle Kombinationen der Merkmale).", "{n} sub-quota created (all combinations of the attributes).", n=n)
              if n == 1 else de_en("{n} Teilquoten erzeugt (alle Kombinationen der Merkmale).",
                                   "{n} sub-quotas created (all combinations of the attributes).", n=n), "success")
    return _page(survey.id, _anchor=f"quota-{quota_id}")


@flow_bp.route("/quotas/assistant", methods=["GET", "POST"], endpoint="quota_census")
@login_required
def quota_census(survey_id):
    """The assistant "Bevölkerung Deutschland" (app/census.py): the choices and a preview of what they give
    (GET, nothing is saved), and making it (POST, a draft only)."""
    from app import census

    survey = get_survey(survey_id)
    lang = _lang(survey)
    opts = census.options(request.form if request.method == "POST" else request.args)
    chosen = {key: value for key, value in opts.items() if value not in (None, 0, "")}
    if request.method == "POST":
        if not _require_draft(survey):
            return _page(survey.id)
        planned = census.plan(opts)
        errors = census.problems(survey, opts) + planned["errors"]
        if not opts["n"]:
            errors.insert(0, de_en("Bitte die angestrebte Stichprobengröße angeben.", "Please enter the intended sample size."))
        if census.own_quotas(survey) and not request.form.get("replace"):
            errors.append(de_en("Der Assistent hat hier schon Quoten angelegt. Bitte ankreuzen, dass sie ersetzt werden sollen.",
                                 "The assistant has already created quotas here. Please tick that they are to be replaced."))
        if errors:
            flash(de_en("Nichts angelegt: {errors}", "Nothing created: {errors}", errors=" ".join(errors)), "error")
            return redirect(url_for("flow_page.quota_census", survey_id=survey.id, lang=lang, **chosen))
        step = undo.begin(survey)
        made = census.apply(survey, opts)
        db.session.flush()
        # questions, blocks, flow elements and quotas: one step
        step.done(undo.both("Quotenassistent: Bevölkerung Deutschland angelegt", "Quota assistant: population of Germany created"),
                  page="quotas")
        db.session.commit()
        values = {"n": len(made), "cells": sum(len(q.cells) for q in made), "date": census.reference_date()}
        rest_de = (" mit zusammen {cells} Teilquoten angelegt (Bevölkerung Deutschland, Stichtag {date}). Die Quotenberechnung "
                   "und ein Ende mit einem Auslöser je Teilquote stehen im Ablauf hinter den drei Fragen.")
        rest_en = (" with {cells} sub-quotas in all created (population of Germany, reference date {date}). The quota "
                   "calculation and an end with one trigger per sub-quota come in the flow after the three questions.")
        flash(de_en("{n} Quote" + rest_de, "{n} quota" + rest_en, **values) if len(made) == 1
              else de_en("{n} Quoten" + rest_de, "{n} quotas" + rest_en, **values), "success")
        return _page(survey.id)
    # an empty size takes the completions of the sample model (UI/UX audit 2026-10-05, V10; 2026-10-07, S3): the
    # target of the study plan, else what its power analysis asks for, else the end of data collection
    from_plan = False
    from app import sample_model

    model = sample_model.of(survey)
    if not opts["n"] and "n" not in request.args and model:
        opts["n"], from_plan = min(model["target"] or model["completions"], census.MAX_N), True
    planned = census.plan(opts)
    from app import flow

    found = census.chosen_questions(survey, opts)
    return render_template(
        "editor/quota_census.html", survey=survey, lang=lang, languages=LANGUAGE_NAMES, census=census, opts=opts, plan=planned,
        from_plan=from_plan, model=model,
        grids={q["code"]: census.grid(q) for q in planned["quotas"]}, steps=census.steps(survey, opts),
        problems=census.problems(survey, opts), old=census.own_quotas(survey), sources=census.state_sources(survey),
        others=census.others_on(survey, found),
        state_item=census.questions(survey, opts["state"])["state"], found=found,
        gender_answers=flow.answer_codes(found["gender"]) if found["gender"] is not None else [],
        age_sources=census.age_sources(survey), gender_sources=census.gender_sources(survey),
        editable=survey.is_editable and survey.can(current_user, "editor"))


@flow_bp.route("/quotas/<int:quota_id>/fieldwork", methods=["POST"], endpoint="quota_steer")
@login_required
def quota_steer(survey_id, quota_id):
    """Steering during fieldwork: targets, hard or soft, tolerance, how long a place is held, closing a cell.
    The conditions stay as they are (they are part of the questionnaire). Every change goes into the log."""
    survey = get_survey(survey_id, need="admin")
    quota = quotas.get(survey, quota_id)
    if quota is None:
        abort(404)
    if survey.is_editable:
        return _page(survey.id, _anchor=f"quota-{quota_id}")  # a draft is edited as a whole
    before = _steering_state(quota)
    error = quotas.update_from_form(survey, quota, request.form, structural=False)
    if error:
        db.session.rollback()
        flash(de_en("Nicht gespeichert: {error}", "Not saved: {error}", error=error), "error")
        return _page(survey.id, _anchor=f"quota-{quota_id}")
    changes = _steering_changes(before, _steering_state(quota))
    if changes:
        lifecycle.note(survey, "quota", f"{quota.name}: " + "; ".join(changes))
        lifecycle.close_if_sample_complete(survey)  # e.g. the last open cell was closed by hand
    db.session.commit()
    flash(de_en("Quote „{name}“: {changes}.", "Quota “{name}”: {changes}.", name=quota.name,
                changes="; ".join(changes) if changes else de_en("nichts geändert", "nothing changed")), "success")
    return _page(survey.id, _anchor=f"quota-{quota_id}")


def _steering_state(quota):
    return {"mode": quota.mode, "tolerance": (quota.tolerance, quota.tolerance_pct), "hold": quota.hold_minutes,
            "cells": {c.id: (c.name, c.target, c.closed) for c in quota.cells}}


def _steering_changes(before, after):
    out = []
    if before["mode"] != after["mode"]:
        out.append(f"Art {quotas.MODES[before['mode']]} → {quotas.MODES[after['mode']]}")
    if before["tolerance"] != after["tolerance"]:
        out.append("Toleranz " + " → ".join(f"{n}{' %' if pct else ' Plätze'}" for n, pct in (before["tolerance"], after["tolerance"])))
    if before["hold"] != after["hold"]:
        out.append(f"Platz gehalten {before['hold']} → {after['hold']} min")
    for cid, (name, target, closed) in after["cells"].items():
        old = before["cells"].get(cid)
        if old and old[1] != target:
            out.append(f"Soll „{name}“ {old[1]} → {target}")
        if old and old[2] != closed:
            out.append(f"Teilquote „{name}“ {'geschlossen' if closed else 'geöffnet'}")
    return out


@flow_bp.route("/quotas/recount", methods=["POST"], endpoint="quota_recount")
@login_required
def quota_recount(survey_id):
    """Rebuild the counters from the participations (after a server fault, or to be sure)."""
    survey = get_survey(survey_id, need="admin")
    changed = quotas.recount(survey)
    if changed:
        lifecycle.note(survey, "quota", "neu gezählt: " + "; ".join(
            f"„{cell.name}“ {quotas.COUNTER_NAMES.get(name, name)} {old} → {new}" for cell, name, old, new in changed[:6])
            + (f" und {len(changed) - 6} weitere" if len(changed) > 6 else ""))
    db.session.commit()
    if not changed:
        flash(de_en("Neu gezählt: Alle Zähler stimmten.", "Recounted: all counters were right."), "success")
    elif len(changed) == 1:
        flash(de_en("Neu gezählt: {n} Zähler korrigiert.", "Recounted: {n} counter corrected.", n=1), "success")
    else:
        flash(de_en("Neu gezählt: {n} Zähler korrigiert.", "Recounted: {n} counters corrected.", n=len(changed)), "success")
    return redirect(url_for("flow_page.quotas", survey_id=survey.id) + "#quotas")


def _csv(survey, lang, only, conds, kept=False, public=False):
    from app.timeutil import to_local

    separator = ";" if request.args.get("sep") == "semicolon" else ","
    entries = sums = None
    if only and kept:  # a share link: the provider counted at most BALANCE_SECONDS ago (_provider_counts)
        counts = _provider_counts(survey, only, kept=True)
        entries, sums = quotas.report(survey, lang, only, conds, counts), quotas.totals(survey, only, counts)
    if public:
        entries = without_caps(entries if entries is not None else quotas.report(survey, lang, only, conds))
    body = "\ufeff" + quotas.report_text(survey, lang, separator, conds=conds, only=only, entries=entries, sums=sums)
    name = f"quotenstand_{survey.code}_{to_local(utcnow()):%Y%m%d-%H%M}.csv"
    return FlaskResponse(body, mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store",
        "X-Robots-Tag": "noindex, nofollow"})


@flow_bp.route("/quotas/state.csv", endpoint="quota_state_csv")
@login_required
def quota_state_csv(survey_id):
    """The state of the quotas as a file: numbers only, so every role may load it."""
    survey = get_survey(survey_id)
    return _csv(survey, _lang(survey), _only(survey), request.args.get("conds") != "0")


@flow_bp.route("/quotas/state", endpoint="quota_state")
@login_required
def quota_state(survey_id):
    """The state on a page of its own: to print or save as PDF; ?part=1 is the piece the fieldwork page reads
    again every now and then."""
    survey = get_survey(survey_id)
    only = _only(survey)
    if request.args.get("part") == "1":
        return render_template("surveys/_quotas.html", survey=survey, **state_context(survey, only=only),
                               shares=[], share_url=None)
    return render_template("surveys/quota_state.html", survey=survey, shared=None,
                           csv_url=url_for("flow_page.quota_state_csv", survey_id=survey.id, qp=request.args.get("qp") or None),
                           **state_context(survey, only=only))


# --- share links ----------------------------------------------------------------------------------------


def share_url(share):
    from app.hosts import participant_base

    return participant_base() + url_for("quota_share.show", token=share.token)


def shares(survey):
    """The links that still work, with their address: [(share, url)]."""
    now = utcnow()
    return [(s, share_url(s)) for s in survey.quota_shares if not s.revoked and (s.expires_at is None or s.expires_at > now)]


@flow_bp.route("/quotas/link", methods=["POST"], endpoint="quota_share_add")
@login_required
def quota_share_add(survey_id):
    survey = get_survey(survey_id, need="admin")
    back = redirect(url_for("flow_page.quotas", survey_id=survey.id) + "#quota-shares")
    if not survey.quotas:
        abort(404)
    if len(shares(survey)) >= MAX_SHARES:
        flash(de_en("Höchstens {n} Links zugleich; bitte zuerst einen widerrufen.", "At most {n} links at a time; please revoke one first.",
                    n=MAX_SHARES), "error")
        return back
    only = quotas.provider(survey, request.form.get("qp", ""))
    if request.form.get("qp") and only is None:
        flash(de_en("Diesen Anbieter gibt es nicht (mehr) unter den gespeicherten URL-Parametern.",
                    "This provider is not (or no longer) among the saved URL parameters."), "error")
        return back
    from app.boards.compute import _personal_params

    if only and only[0] in _personal_params(survey):
        flash(de_en("Für persönliche Links können keine öffentlichen Quotenlinks angelegt werden.",
                    "Public quota links cannot be created for personal links."), "error")
        return back
    days, valid = request.form.get("days", ""), request.form.get("valid", "")
    if valid == "never":  # the choices of a dashboard link (boards.VALIDITY): the same for both kinds of link
        expires = None
    elif valid in ("7", "30", "90"):
        expires = utcnow() + timedelta(days=int(valid))
    elif quotas._digits(days) and 1 <= int(days) <= 730:
        expires = utcnow() + timedelta(days=int(days))
    elif survey.field_end and survey.field_end > utcnow():
        expires = survey.field_end + timedelta(days=SHARE_AFTER_FIELD)
    else:
        expires = utcnow() + timedelta(days=SHARE_DAYS)
    share = QuotaShare(survey_id=survey.id, token=secrets.token_urlsafe(24), label=request.form.get("label", "").strip()[:100],
                       param=only[0] if only else "", value=only[1] if only else "",
                       show_conds=request.form.get("conds") == "1", expires_at=expires, revoked=False)
    survey.quota_shares.append(share)
    db.session.commit()
    flash(de_en("Link angelegt. Er zeigt nur zusammengefasste Zahlen und lässt sich jederzeit widerrufen.",
                "Link created. It shows only summarised numbers and can be revoked at any time."), "success")
    return back


@flow_bp.route("/quotas/link/<int:share_id>/withdraw", methods=["POST"], endpoint="quota_share_revoke")
@login_required
def quota_share_revoke(survey_id, share_id):
    survey = get_survey(survey_id, need="admin")
    share = next((s for s in survey.quota_shares if s.id == share_id), None)
    if share is None:
        abort(404)
    share.revoked = True
    db.session.commit()
    flash(de_en("Link widerrufen: Er zeigt ab sofort nichts mehr.", "Link revoked: it shows nothing from now on."), "success")
    return redirect(url_for("flow_page.quotas", survey_id=survey.id) + "#quota-shares")


def _shared(token):
    """(share, survey) of a link that works, else 404: revoked, run out and unknown links look the same, and so
    does the link of an archived survey, as for a dashboard link (/d/). Requests count against the same limit
    per sender as those for dashboard links (limits.allow_board, BOARD_LIMIT within BOARD_WINDOW): 429."""
    from app import limits

    if not limits.allow_board():
        abort(429)
    from app.shared_tokens import find

    share = find(QuotaShare, token)
    if share is None or share.revoked or (share.expires_at is not None and share.expires_at <= utcnow()):
        abort(404)
    survey = db.session.get(Survey, share.survey_id)
    if survey is None or survey.archived:
        abort(404)
    from app.boards.compute import _personal_params

    if share.param in _personal_params(survey):
        abort(404)
    return share, survey


@share_bp.route("/<token>")
def show(token):
    """The state of the quotas for whoever holds the link: read-only, numbers only, never indexed."""
    share, survey = _shared(token)
    only = (share.param, share.value) if share.param else None
    page = render_template("surveys/quota_state.html", survey=survey, shared=share,
                           csv_url=url_for("quota_share.csv", token=token),
                           **state_context(survey, only=only, conds=share.show_conds, providers=False, kept=True,
                                           public=True))
    return page, 200, {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@share_bp.route("/<token>/state.csv", endpoint="csv")
def show_csv(token):
    share, survey = _shared(token)
    return _csv(survey, survey.default_language, (share.param, share.value) if share.param else None, share.show_conds, kept=True,
                public=True)
