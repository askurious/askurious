"""Pre-registration snapshots and the seal chain."""
import os

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db
from app.lang import de_en
from app.surveys.base import bp, get_survey


@bp.route("/<sid:survey_id>/export/snapshot/<name>/<fname>")
@login_required
def snapshot_file(survey_id, name, fname):
    from flask import send_file

    from app import snapshots

    survey = get_survey(survey_id)
    path = snapshots.path(survey, name, fname)
    if not path:
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"{name}_{fname}")


@bp.route("/<sid:survey_id>/export/preregistration/<name>")
@login_required
def prereg_file(survey_id, name):
    """The pre-registration snapshot frozen at the start of the fieldwork (app/prereg.py); no answers in it, for
    every role."""
    from flask import send_file

    from app import export, prereg

    survey = get_survey(survey_id)
    path = prereg.path(survey, name)
    if not path:
        abort(404)
    return send_file(path, as_attachment=True, mimetype="application/zip",
                     download_name=f"{export.slug(survey.title)}-praeregistrierung-{name}")


@bp.route("/<sid:survey_id>/seal-chain", methods=["POST"])
@login_required
def seal_enable(survey_id):
    """Switch the seal chain on (app/seal.py): a tick of its own with a question first, not part of the settings that
    save themselves, since it cannot be switched off again once it has an entry. off=1, or toggle=1 without on=1 (the
    tick taken away): off again, as long as the chain has no entry (a new survey starts with it on, SEAL_DEFAULT)."""
    from app import seal

    survey = get_survey(survey_id)
    # the tick on the settings page (UI audit 2026-10-12, U16): toggle=1, ticked or not
    off = request.form.get("off") or (request.form.get("toggle") and not request.form.get("on"))
    if off:
        if survey.seal and not seal.has_chain(survey):
            survey.seal = False
            db.session.commit()
            flash(de_en("Die Siegelkette ist ausgeschaltet.", "The seal chain is switched off."), "success")
        return redirect(url_for("surveys.detail", survey_id=survey.id) + "#seal")
    if not survey.seal:
        seal.enable(survey)
        db.session.commit()
        flash(de_en("Die Siegelkette ist eingeschaltet.", "The seal chain is switched on."), "success")
    return redirect(url_for("surveys.detail", survey_id=survey.id) + "#seal")


@bp.route("/<sid:survey_id>/export/preregistration/<name>/timestamp")
@login_required
def prereg_stamp(survey_id, name):
    """The timestamp of the DFN for a pre-registration snapshot (app/prereg.py): the reply of the service (.tsr)."""
    from flask import send_file

    from app import export, prereg

    survey = get_survey(survey_id)
    found = prereg.stamp_path(survey, name)
    if not found:
        abort(404)
    return send_file(found, as_attachment=True, mimetype="application/timestamp-reply",
                     download_name=f"{export.slug(survey.title)}-praeregistrierung-{name[:-4]}.tsr")


@bp.route("/<sid:survey_id>/export/seal_verify.py")
@login_required
def seal_checker(survey_id):
    """The checker for seal chains and timestamps (app/seal_verify.py), a file of its own that needs no Askurious."""
    from flask import send_file

    get_survey(survey_id)
    return send_file(os.path.join(current_app.root_path, "seal_verify.py"), as_attachment=True,
                     mimetype="text/x-python", download_name="seal_verify.py")


@bp.route("/<sid:survey_id>/export/seal-chain.zip")
@login_required
def seal_chain(survey_id):
    """The seal chain (experimental, app/seal.py) with the checker and a README; stamped first if its newest entry
    is not stamped yet (the download waits for the DFN a few seconds at most, and goes without if it does not answer)."""
    import io
    import json
    import zipfile

    from flask import send_file

    from app import export, lang as ui_lang, seal

    survey = get_survey(survey_id)
    if not seal.has_chain(survey):
        if not survey.seal:
            abort(404)
        # on, but no entry yet (a new survey, SEAL_DEFAULT): say so instead of "page not found" (pre-test 2026-10-20, 30)
        flash(de_en("Die Siegelkette hat noch keine Einträge; es gibt noch nichts herunterzuladen. Sie beginnt mit dem "
                    "ersten beendeten Fall.",
                    "The seal chain has no entries yet, so there is nothing to download. It begins with the first case "
                    "that ends."), "info")
        return redirect(url_for("surveys.data", survey_id=survey.id) + "#seal")
    try:
        seal.anchor(survey.uid)
    except Exception as exc:
        db.session.rollback()
        current_app.logger.warning("timestamp of the seal chain %s at the download failed: %s", survey.uid, exc)
    data = seal.chain(survey, url=seal.chain_url(survey))  # where seal_verify.py finds the chain to compare (S2)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("seal.json", json.dumps(data, ensure_ascii=False, indent=1))
        with open(os.path.join(current_app.root_path, "seal_verify.py"), "rb") as f:
            z.writestr("seal_verify.py", f.read())
        z.writestr("LIESMICH.txt" if ui_lang.current() != "en" else "README.txt", seal.readme(survey, data))
    buf.seek(0)
    return send_file(buf, as_attachment=True, mimetype="application/zip",
                     download_name=f"{export.slug(survey.title)}-siegelkette.zip")


@bp.route("/<sid:survey_id>/export/seal-chain/check", methods=["POST"])
@login_required
def seal_check(survey_id):
    """Check the seal chain against itself, its timestamps and the participations stored now. Answers the page's
    script (X-Requested-With: fetch) with a block of HTML marked X-Seal-Result, also for "nothing to check", "checked
    too often" and an error, so that it shows what happened instead of a general failure (pre-test 2026-10-20, 30)."""
    from markupsafe import escape

    from app import limits, seal

    survey = get_survey(survey_id)
    fetch = request.headers.get("X-Requested-With") == "fetch"
    back = redirect(url_for("surveys.data", survey_id=survey.id) + "#seal")

    def say(text, status=200):
        response = current_app.response_class(f'<p class="small">{escape(text)}</p>', status=status,
                                              mimetype="text/html")
        response.headers["X-Seal-Result"] = "1"
        return response

    if not seal.has_chain(survey):
        if not survey.seal:
            abort(404)
        # on (a new survey starts with it, SEAL_DEFAULT) but no entry yet: the first comes with the first case
        empty = de_en("Keine Einträge, nichts zu prüfen: Die Kette beginnt mit dem ersten beendeten Fall.",
                      "No entries, nothing to check: the chain begins with the first case that ends.")
        if fetch:
            return say(empty)
        flash(empty, "info")
        return back
    if not limits.allow_seal_check(survey.uid):  # each check reads the whole chain and every participation (S5)
        busy = de_en("Die Siegelkette dieser Befragung wurde gerade mehrmals geprüft. Bitte in ein paar Minuten erneut "
                     "prüfen; die stündliche Prüfung läuft ohnehin.",
                     "The seal chain of this survey was just checked several times. Please check again in a few "
                     "minutes; the hourly check runs anyway.")
        if fetch:
            return say(busy, 429)
        flash(busy, "error")
        return back
    try:
        stamp_note = seal.stamp_at_check(survey)  # only the newest stamp; the check itself needs no outside service
        result = seal.check(survey)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("check of the seal chain %s failed", survey.uid)
        failed = de_en("Die Prüfung ist auf dem Server abgebrochen; der Fehler steht im Protokoll des Servers "
                       "(„check of the seal chain … failed“). Die stündliche Prüfung läuft weiter.",
                       "The check stopped on the server; the error is in the server's log (“check of the seal "
                       "chain … failed”). The hourly check continues.")
        if fetch:
            return say(failed, 500)
        flash(failed, "error")
        return back
    if fetch:
        # the result block under the button (surveys/_seal_result.html), filled by app.js
        response = current_app.response_class(render_template(
            "surveys/_seal_result.html", result=result, info=seal.summary(survey), stamp_note=stamp_note),
            mimetype="text/html")
        response.headers["X-Seal-Result"] = "1"
        return response
    if stamp_note:
        flash(stamp_note, "info")
    if result["problems"]:
        flash(de_en("Die Siegelkette hält nicht: {p}", "The seal chain does not hold: {p}",
                    p="; ".join(result["problems"][:10]) + (" …" if len(result["problems"]) > 10 else "")), "error")
    else:
        flash(de_en("Die Siegelkette hält: {n} Einträge, jeder Fall vorhanden oder mit Löschvermerk.",
                    "The seal chain holds: {n} entries, every case present or with a deletion record.",
                    n=result["summary"]["entries"]), "success")
    return redirect(url_for("surveys.data", survey_id=survey.id) + "#seal")
