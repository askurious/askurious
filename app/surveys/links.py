"""Participation link block: panel, link label, QR codes and links per value."""
import re

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, edit_guard, lifecycle, links, undo
from app.extensions import db
from app.lang import de_en
from app.models import Survey
from app.surveys.base import _hide_names, _refused, _settings_conflict, _settings_saved, bp, get_survey
from app.surveys.field import field


# --- entry and exit via external systems ---------------------------------------------------


@bp.route("/<sid:survey_id>/panel", methods=["POST"])
@login_required
@autosave.aware
def update_panel(survey_id):
    from app import panel

    survey = get_survey(survey_id)
    conflict = _settings_conflict(survey, "panel")
    links_before = (panel.config(survey)["redirects"], panel.config(survey)["modes"], panel.sign_config(survey))
    error = panel.update_from_form(survey, request.form)
    if error:
        survey.panel = panel.as_entered(request.form, survey)  # on trial, for the page only
        return _refused(survey, "settings:panel", error, page=field)
    if conflict:
        return edit_guard.show_again(lambda: field(survey.id))
    _settings_saved(survey, "panel")
    if survey.status != Survey.DRAFT and links_before != (panel.config(survey)["redirects"], panel.config(survey)["modes"],
                                                          panel.sign_config(survey)):
        lifecycle.note(survey, "links")  # completion links changed during fieldwork: kept in the status log
    flash(de_en("Teilnahmelinks gespeichert.", "Participation links saved."), "success")
    db.session.commit()
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#panel")  # on "Datenerhebung" (Ralf, 2026-10-06)


@bp.route("/<sid:survey_id>/panel/paste", methods=["POST"])
@login_required
def paste_panel_links(survey_id):
    """"Links vom Panel einfügen" (Ralf, 2026-10-08): the pasted list is read (panel.parse_panel_list) and shown as
    a preview under the field; "Übernehmen" takes the ID parameter and the completion links over."""
    from flask import session

    from app import panel

    survey = get_survey(survey_id, need="admin")
    left_key = f"panel_paste_left:{survey.id}"  # speeder and Geo-IP lines, not taken over: kept to copy (X55)
    back = redirect(url_for("surveys.field", survey_id=survey.id) + "#panel-paste")
    action = request.form.get("action")
    if action == "cancel":
        panel.forget_paste(current_user.id, survey.id)
        return back
    if action == "dismiss":
        session.pop(left_key, None)
        return back
    if action == "apply":
        from flask import g

        rows = panel.parse_panel_list(panel.pasted(current_user.id, survey.id))
        g.settings_step = undo.begin(survey)  # "Rückgängig" takes it back, as a save of the form below
        before = panel.config(survey)["redirects"]
        done, error = panel.apply_panel_list(survey, rows)
        if error:
            flash(de_en("Nicht übernommen: {error}", "Not applied: {error}", error=error), "error")
            return back
        panel.forget_paste(current_user.id, survey.id)
        _settings_saved(survey, "panel")
        if survey.status != Survey.DRAFT and before != panel.config(survey)["redirects"]:
            lifecycle.note(survey, "links")
        db.session.commit()
        # speeder and Geo-IP have no outcome of their own: their lines stay under "Links vom Panel einfügen" with a
        # copy button until they are put at an end of the flow, and the message names them (UI/UX audit 2026-10-09, X55)
        left = [{"label": r["label"], "url": r["url"], "kind": r["kind"]} for r in rows if r["kind"] in ("speeder", "geo")]
        if left:
            session[left_key] = left
        else:
            session.pop(left_key, None)
        message = de_en("Übernommen: {list}.", "Applied: {list}.",
                        list=", ".join(panel.OUTCOMES[o] for o in done) or de_en("nichts", "nothing"))
        if left:
            message += " " + de_en("Nicht übernommen: {list}; diese Links stehen unter „Links vom Panel einfügen“ zum Kopieren.",
                                   "Not applied: {list}; these links appear under “Paste links from the panel” to copy.",
                                   list=", ".join(r["label"] for r in left))
        flash(message, "success")
        return redirect(url_for("surveys.field", survey_id=survey.id) + ("#panel-paste" if left else "#panel"))
    text = (request.form.get("text") or "")[:20000]
    if not panel.parse_panel_list(text):
        flash(de_en("In dem Text steht keine Adresse mit https://.", "The text holds no address with https://."), "error")
        panel.forget_paste(current_user.id, survey.id)
    else:
        panel.keep_paste(current_user.id, survey.id, text)  # on the server, not in the cookie (code review 2026-10-15, I9)
    return back


# --- link label, QR codes, links per group ----------------------------------------------


@bp.route("/<sid:survey_id>/label", methods=["POST"])
@login_required
def set_label(survey_id):
    survey = get_survey(survey_id)
    back = request.form.get("next") if request.form.get("next") == "wizard" else None
    target = (url_for("wizard.step", survey_id=survey.id, key="ablauf") if back
              else url_for("surveys.field", survey_id=survey.id)) + "#links"
    if request.form.get("confirm") != "1":
        flash(de_en("Der Kurzname wurde nicht festgelegt: bitte bestätigen.", "The short name was not set: please confirm."), "error")
        return redirect(target)
    label = links.normalize(request.form.get("label"))
    error = links.set_label(survey, label, current_user)
    if error:
        flash(error, "error")
    else:
        db.session.commit()
        flash(de_en("Kurzname „{label}“ festgelegt: {url}", "Short name “{label}” set: {url}", label=label,
                    url=links.participant_url(survey)), "success")
    return redirect(target)


@bp.route("/label-check")
@login_required
def check_label():
    label = links.normalize(request.args.get("label"))
    return {"label": label, "error": links.label_error(label, current_user)}


def _link_params():
    """The group parameter of a QR request (?name=...&value=...), if any."""
    from app import panel

    name, value = request.args.get("name", ""), request.args.get("value", "")
    return [(name, value[:100])] if name and panel.NAME_RE.match(name) else []


@bp.route("/<sid:survey_id>/qr")
@login_required
def qr_page(survey_id):
    params = _link_params()
    # a link per value only from "Bearbeiten" up, as on the page (Abgleich 2026-10-15 B-2); the link for all for everyone
    survey = get_survey(survey_id, need="editor" if params else "viewer")
    return render_template("surveys/qr.html", survey=survey, url=links.participant_url(survey, params),
                           params=params, query=dict(params and {"name": params[0][0], "value": params[0][1]}))


@bp.route("/<sid:survey_id>/qr.<fmt>")
@login_required
def qr_image(survey_id, fmt):
    from flask import Response as FlaskResponse

    if fmt not in ("svg", "png"):
        abort(404)
    params = _link_params()
    survey = get_survey(survey_id, need="editor" if params else "viewer")  # B-2, as qr_page
    name = "qr-" + survey.public_key + ("-" + "-".join(f"{k}-{v}" for k, v in params) if params else "")
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", name)
    headers = {"Cache-Control": "no-store"}
    if request.args.get("download"):
        headers["Content-Disposition"] = f'attachment; filename="{name}.{fmt}"'
    return FlaskResponse(links.qr(links.participant_url(survey, params), fmt),
                         mimetype="image/svg+xml" if fmt == "svg" else "image/png", headers=headers)


# --- links per person (concept "Werbelinks", 2026-10-08) --------------------------------------------------
# One link per student of a seminar: a random value of a stored URL parameter each, with the person's name only
# here (panel.value_labels), counted on "Datenerhebung" and given out as CSV or printed cards.


@bp.route("/<sid:survey_id>/value-links", methods=["POST"])
@login_required
def add_value_links(survey_id):
    from app import panel

    survey = get_survey(survey_id, need="admin")
    count = request.form.get("count", type=int) or 0
    name = (request.form.get("param") or "w").strip()
    with_board = bool(request.form.get("board"))
    existing = next((p for p in panel.config(survey)["params"] if p["name"] == name and p.get("allowed")), None)
    only_board = with_board and existing is not None and not request.form.get("names", "").strip() and count <= 0
    added, error = (0, None) if only_board else panel.add_values(survey, name, request.form.get("names", ""),
                                                                 min(max(count, 0), panel.MAX_ALLOWED))
    shared = 0
    if not error and with_board:
        # "Dashboard für jede Person anlegen" (Ralf, 2026-10-09): one dashboard with the number of participations,
        # made at the first run and used again at the next, and a link of it for each link without one, also for
        # links made before (the tick alone, without new names, does just that)
        from app import boards

        param = next(p for p in panel.config(survey)["params"] if p["name"] == name)
        shared, error = boards.ensure_value_board(survey, param)
    if error:
        db.session.rollback()
        flash(error, "error")
    else:
        db.session.commit()
        if only_board:
            flash(de_en("{n} Dashboard-Links angelegt (Spalte „Ihr Stand“).", "{n} dashboard links created (column “Their status”).",
                        n=shared), "success")
        else:
            flash(de_en("{n} Links angelegt.", "{n} links created.", n=added)
                  + (de_en(" Jeder hat einen eigenen Dashboard-Link (Spalte „Ihr Stand“).",
                           " Each has its own dashboard link (column “Their status”).") if with_board else ""),
                  "success")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#value-links")


@bp.route("/<sid:survey_id>/value-links/limit", methods=["POST"])
@login_required
@autosave.aware
def limit_value_links(survey_id):
    """"Höchstens N je Link": an upper limit of completed participations per link, as a quota (panel.set_cap)."""
    from app import panel

    survey = get_survey(survey_id, need="admin")
    raw = (request.form.get("limit") or "").strip()
    n = min(int(raw), 100_000) if raw.isascii() and raw.isdigit() and len(raw) < 7 else 0
    name = request.form.get("param", "")
    before = panel.cap_of(survey, name)
    step = undo.begin(survey) if survey.is_editable else None
    message, error = panel.set_cap(survey, name, n)
    if error:
        db.session.rollback()
        flash(error, "error")
    else:
        if step is not None:
            step.done(undo.both("Höchstzahl je Link geändert", "Maximum per link changed"), page="settings",
                      node="value-links", merge=autosave.merge("value-links-limit"))
        elif before != n:
            lifecycle.note(survey, "quota", f"Höchstzahl je Link ({name}): {before} → {n}")
        db.session.commit()
        flash(message, "success")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#value-links")


@bp.route("/<sid:survey_id>/value-links/remove", methods=["POST"])
@login_required
def remove_value_link(survey_id):
    """The "×" of a row of "Eigene Links": the link stops working (panel.remove_value)."""
    from app import panel

    survey = get_survey(survey_id, need="admin")
    name, _, value = (request.form.get("remove") or "").partition(":")
    who = panel.value_labels(survey).get(name, {}).get(value) or value
    # "Rückgängig" instead of a question first (UI/UX audit 2026-10-09, X59): the step holds the links and the quota
    step = undo.begin(survey)
    if panel.remove_value(survey, name, value):
        step.done(undo.both("Link für „{v}“ entfernt", "Link for “{v}” removed", v=who), page="settings", node="value-links")
        db.session.commit()
        flash(de_en("Link für „{v}“ entfernt. Wer ihn öffnet, sieht „Dieser Link ist unvollständig“; bisherige Teilnahmen "
                    "zählen unter „ohne eigenen Link“.",
                    "Link for “{v}” removed. Anyone who opens it sees “This link is incomplete”; participations so far count "
                    "under “without their own link”.", v=who), "undo")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#value-links")


@bp.route("/<sid:survey_id>/value-links/names", methods=["POST"])
@login_required
@autosave.aware
def name_value_links(survey_id):
    from app import panel

    survey = get_survey(survey_id, need="admin")
    panel.set_value_labels(survey, request.form)
    db.session.commit()
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#value-links")


def _value_rows(survey):
    """The links per value with their counts, address and (for admins) dashboard link, for the CSV and the cards;
    names and flags only from "Bearbeiten" up (sees_link_names)."""
    from app import boards, panel

    shares = boards.value_share_urls(survey) if survey.role_of(current_user) == "admin" else {}
    return [{**row, "url": "" if row["rest"] else links.participant_url(survey, [(row["name"], row["value"])]),
             "board": shares.get((row["name"], row["value"]), "")} for row in _hide_names(panel.group_counts(survey), survey)]


@bp.route("/<sid:survey_id>/value-links/usable")
@login_required
def value_links_usable(survey_id):
    """{"usable": {"parameter=value": usable participations}, "flags": {"parameter=value": [why it is conspicuous]}}
    for the table of links per value, fetched after the page: it reads the quality assurance, which may read every
    participation."""
    from app import panel

    survey = get_survey(survey_id)
    rows = _hide_names(panel.group_counts(survey), survey)
    return {"usable": {row["key"]: row["usable"] for row in rows},
            "flags": {row["key"]: row["flags"] for row in rows if row["flags"]}}


@bp.route("/<sid:survey_id>/value-links.csv")
@login_required
def value_links_csv(survey_id):
    import csv
    import io

    from flask import Response as FlaskResponse

    survey = get_survey(survey_id, need="editor")  # the links per value from "Bearbeiten" up (Abgleich 2026-10-15 B-2)
    rows = _value_rows(survey)
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    head = [de_en("Name", "Name"), de_en("Parameter", "Parameter"), de_en("Wert", "Value"),
            de_en("Teilnahmelink", "Participation link"), de_en("begonnen", "started"),
            de_en("abgeschlossen", "completed"), de_en("verwertbar", "usable"), de_en("auffällig", "conspicuous")]
    with_board = any(row["board"] for row in rows)
    writer.writerow(head + ([de_en("Dashboard-Link", "Dashboard link")] if with_board else []))
    from app.export import safe_text
    for row in rows:
        name = de_en("(ohne Link je Wert)", "(without a link per value)") if row["rest"] else row["label"]
        # every cell that starts like a formula stays text in a spreadsheet (audit 2026-10-09, D4)
        writer.writerow([safe_text(cell) for cell in
                         [name, row["name"], row["value"] or "", row["url"], row["started"], row["completed"], row["usable"],
                          "; ".join(row["flags"])] + ([row["board"]] if with_board else [])])
    filename = re.sub(r"[^A-Za-z0-9_-]+", "_", f"links-{survey.public_key}") + ".csv"
    return FlaskResponse("\ufeff" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                         headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@bp.route("/<sid:survey_id>/value-links/cards")
@login_required
def value_link_cards(survey_id):
    from app.editor import qr_svg

    survey = get_survey(survey_id, need="editor")  # B-2, as value_links_csv
    rows = [{**row, "qr": qr_svg(row["url"])} for row in _value_rows(survey) if not row["rest"]]
    return render_template("surveys/value_cards.html", survey=survey, rows=rows)
