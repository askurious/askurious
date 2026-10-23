"""Data exports: SPSS, jamovi, R, voice messages, comments, Word questionnaire, conjoint, loops."""
from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app import heavy, report_jobs
from app.extensions import db
from app.lang import de_en
from app.surveys.base import bp, data_pool, get_survey
from app.surveys.data import rds_body, with_scales, with_standard


@bp.route("/<sid:survey_id>/export/spss.zip")
@login_required
@report_jobs.background
@heavy.limited
def export_spss(survey_id):
    """The data for SPSS: CSV plus a syntax file with variable and value labels (ZIP)."""
    from flask import Response as FlaskResponse

    from app import export

    survey = get_survey(survey_id)
    pool = data_pool(survey)
    body = export.spss_zip(survey, include_incomplete=pool["all"], sources=pool["sources"],
                           speed_sources=pool["sources"], drop_speeders=pool["drop"], scales=with_scales(),
                           standard=with_standard())
    return FlaskResponse(body, mimetype="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "spss")[:-4]}.zip"',
        "Cache-Control": "no-store",
    })


@bp.route("/<sid:survey_id>/export/voice-messages.zip")
@login_required
@heavy.limited
def export_voice(survey_id):
    """The recordings of the voice messages (app/items/voice.py) of the data pool, named as in <code>_audio."""
    from flask import send_file

    from app import export
    from app.items import voice

    survey = get_survey(survey_id)
    if not voice.used(survey):
        abort(404)
    pool = data_pool(survey)
    responses = export._responses(survey, pool["all"], pool["sources"])
    response = send_file(voice.zip_file(survey, responses), mimetype="application/zip", as_attachment=True, conditional=False,
                         download_name=f'{export.filename(survey, "sprachnachrichten")[:-4]}.zip')
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<sid:survey_id>/export/comments.csv")
@login_required
@heavy.limited
def export_comments(survey_id):
    """All comments from test runs and how they were handled."""
    from flask import Response as FlaskResponse

    from app import access, comments, export

    survey = get_survey(survey_id)
    if access.showcase_only(current_user, survey):  # the team's notes on an example survey stay with the team (M6)
        abort(404)
    return FlaskResponse(comments.to_csv(survey), mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "comments")}"', "Cache-Control": "no-store"})


@bp.route("/<sid:survey_id>/export/comments.zip")
@login_required
@heavy.limited
def export_comments_zip(survey_id):
    """The comments with their images: comments.csv and bilder/<comment id>.png|jpg."""
    from flask import send_file

    from app import access, comment_shots, comments, export

    survey = get_survey(survey_id)
    if access.showcase_only(current_user, survey):  # the team's notes on an example survey stay with the team (M6)
        abort(404)
    data = comment_shots.zip_file(survey, comments.to_csv(survey), comments.all_for(survey))
    response = send_file(data, mimetype="application/zip", as_attachment=True, conditional=False,
                         download_name=f'{export.filename(survey, "comments")[:-4]}.zip')
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<sid:survey_id>/export/data.sav")
@login_required
@report_jobs.background
@heavy.limited
def export_sav(survey_id):
    """The data as an SPSS system file (.sav) with variable and value labels and measurement levels."""
    from flask import Response as FlaskResponse

    from app import export

    survey = get_survey(survey_id)
    pool = data_pool(survey)
    body = export.spss_sav(survey, include_incomplete=pool["all"], sources=pool["sources"],
                           speed_sources=pool["sources"], drop_speeders=pool["drop"], scales=with_scales(),
                           standard=with_standard())
    return FlaskResponse(body, mimetype="application/x-spss-sav", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "spss")[:-4]}.sav"',
        "Cache-Control": "no-store",
    })


@bp.route("/<sid:survey_id>/export/data.omv")
@login_required
@report_jobs.background
@heavy.limited
def export_omv(survey_id):
    """The data as a jamovi file (.omv) with descriptions, levels and measurement levels."""
    from flask import Response as FlaskResponse

    from app import export

    survey = get_survey(survey_id)
    pool = data_pool(survey)
    body = export.jamovi_omv(survey, include_incomplete=pool["all"], sources=pool["sources"],
                             speed_sources=pool["sources"], drop_speeders=pool["drop"], scales=with_scales(),
                           standard=with_standard())
    return FlaskResponse(body, mimetype="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "jamovi")[:-4]}.omv"',
        "Cache-Control": "no-store",
    })


@bp.route("/<sid:survey_id>/export/data.rds")
@login_required
@report_jobs.background
@heavy.limited
def export_rds(survey_id):
    """The data as an R file (.rds): a data frame with descriptions and value labels."""
    from flask import Response as FlaskResponse

    from app import export

    survey = get_survey(survey_id)
    body = rds_body(survey, data_pool(survey), with_scales(), with_standard())
    return FlaskResponse(body, mimetype="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "r")[:-4]}.rds"',
        "Cache-Control": "no-store",
    })


@bp.route("/<sid:survey_id>/export/model.R")
@login_required
def export_sem_r(survey_id):
    """The structural equation model of the study plan as an R script for the .rds export (lavaan; seminr for PLS),
    app/sem_model.r_script."""
    from flask import Response as FlaskResponse

    from app import export, sem_model

    survey = get_survey(survey_id)
    rds_name = f'{export.filename(survey, "r")[:-4]}.rds'
    lang = "en" if request.args.get("lang") == "en" else ("en" if _english() else "de")
    script = sem_model.r_script(survey, rds_name, lang)
    if script is None:
        abort(404)
    return FlaskResponse(script, mimetype="text/plain; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "sem")[:-4]}.R"',
        "Cache-Control": "no-store",
    })


def _english():
    from app.lang import english

    return english()


@bp.route("/<sid:survey_id>/export/questionnaire.docx")
@bp.route("/<sid:survey_id>/export/questionnaire-<version>.docx")
@login_required
@heavy.limited
def export_docx(survey_id, version="codebook"):
    """The questionnaire as a Word document (app/questionnaire_docx.py), with codebook or for printing; no answers
    in it, for every role."""
    from flask import Response as FlaskResponse

    from app import export

    survey = get_survey(survey_id)
    try:
        from app import questionnaire_docx
    except ImportError:  # python-docx is missing on a server whose vendor/ was built before it was needed
        current_app.logger.exception("Word export without python-docx")
        flash(de_en("Der Word-Export geht auf diesem Server gerade nicht: Das Paket python-docx fehlt. Die Python-Pakete "
                    "(vendor/) müssen neu gebaut und hochgeladen werden; /healthz zeigt, welche Pakete fehlen.",
                    "The Word export does not work on this server at the moment: the package python-docx is missing. "
                    "The Python packages (vendor/) must be rebuilt and uploaded; /healthz shows which packages are "
                    "missing."), "error")
        return redirect(url_for("surveys.data", survey_id=survey.id))
    if version not in questionnaire_docx.VERSIONS:
        abort(404)
    name = "fragebogen-codebuch" if version == "codebook" else "fragebogen-papier"
    from app import transfer

    try:
        body = questionnaire_docx.build(survey, version)
    except transfer.ExportTooLarge as exc:
        abort(413, description=str(exc))
    return FlaskResponse(body, headers={
        "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": f'attachment; filename="{export.filename(survey, name)[:-4]}.docx"',
        "Cache-Control": "no-store",
    })


@bp.route("/<sid:survey_id>/export/survey-material.pdf")
@login_required
@heavy.limited
def export_study_material(survey_id):
    """The survey material as PDF (app/study_material.py): the questionnaire as participants see it and with notes on
    logic, randomisation and checks; no answers in it, for every role, in the language of the person downloading."""
    from flask import Response as FlaskResponse

    from app import export, study_material

    survey = get_survey(survey_id)
    body = study_material.build(survey)
    return FlaskResponse(body, headers={
        "Content-Type": "application/pdf",
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "befragungsmaterial")[:-4]}.pdf"',
        "Cache-Control": "no-store",
    })


@bp.route("/<sid:survey_id>/export/datenfluss.docx")
@login_required
@heavy.limited
def export_data_flow(survey_id):
    """The data flow of the survey as a Word table, filled from its settings (app/data_flow_docx.py); no answers in
    it, for every role (comparison with the RWTH example table of data flows 2026-10-20, F)."""
    from flask import Response as FlaskResponse

    from app import data_flow_docx, export

    survey = get_survey(survey_id)
    return FlaskResponse(data_flow_docx.build(survey), headers={
        "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "datenfluss")[:-4]}.docx"',
        "Cache-Control": "no-store",
    })


@bp.route("/<sid:survey_id>/export/constructs.md")
@login_required
def export_constructs(survey_id):
    from flask import Response as FlaskResponse

    from app import export

    survey = get_survey(survey_id)
    return FlaskResponse(export.constructs_md(survey), mimetype="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "constructs")[:-4]}.md"',
        "Cache-Control": "no-store",
    })


@bp.route("/<sid:survey_id>/export/references.<fmt>")
@login_required
def export_references(survey_id, fmt):
    """The references of the survey's constructs for a reference manager (app/bibliography.py): BibTeX or RIS."""
    from flask import Response as FlaskResponse

    from app import bibliography, export

    if fmt not in ("bib", "ris"):
        abort(404)
    survey = get_survey(survey_id)
    items = bibliography.collect(c for c, _ in export.used_constructs(survey))
    body = bibliography.bibtex(items) if fmt == "bib" else bibliography.ris(items)
    return FlaskResponse(body, mimetype=("application/x-bibtex" if fmt == "bib" else "application/x-research-info-systems")
                         + "; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "literatur")[:-4]}.{fmt}"',
        "Cache-Control": "no-store",
    })


@bp.route("/<sid:survey_id>/export/conjoint/<int:item_id>.csv")
@login_required
def export_conjoint(survey_id, item_id):
    from flask import Response as FlaskResponse

    from app import export
    from app.models import Item

    survey = get_survey(survey_id)
    item = db.session.get(Item, item_id)
    if item is None or not item.kind.long_export or item.block.survey_id != survey.id:
        abort(404)
    pool = data_pool(survey)
    body = export.long_csv(survey, item, include_incomplete=pool["all"],
                           separator="semicolon" if request.args.get("sep") == "semicolon" else "comma",
                           sources=pool["sources"], drop_speeders=pool["drop"])
    return FlaskResponse(body, mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, f"{item.kind.long_export}-{item.code}")}"',
        "Cache-Control": "no-store"})


@bp.route("/<sid:survey_id>/export/vignettes/<nid>.csv")
@login_required
def export_vignettes(survey_id, nid):
    """Long format of a vignette loop (app/vignettes.py)."""
    from flask import Response as FlaskResponse

    from app import export, flow, vignettes

    survey = get_survey(survey_id)
    node, _ = flow.find(flow.tree(survey), nid)
    if not vignettes.is_vignettes(node):
        abort(404)
    pool = data_pool(survey)
    body = export.vignettes_long_csv(survey, node, include_incomplete=pool["all"],
                                     separator="semicolon" if request.args.get("sep") == "semicolon" else "comma",
                                     sources=pool["sources"], drop_speeders=pool["drop"])
    name = export.filename(survey, "vignetten-" + str(node.get("code") or nid))
    return FlaskResponse(body, mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Cache-Control": "no-store"})


@bp.route("/<sid:survey_id>/export/looplong/<nid>.csv")
@login_required
def export_loop_long(survey_id, nid):
    """Long format of a loop with a table (export.loop_long_csv): one row per person and pass."""
    from flask import Response as FlaskResponse

    from app import export, flow

    survey = get_survey(survey_id)
    node, _ = flow.find(flow.tree(survey), nid)
    if node is None or node.get("id") not in {n["id"] for n in export.loop_long_nodes(survey)}:
        abort(404)
    pool = data_pool(survey)
    body = export.loop_long_csv(survey, node, include_incomplete=pool["all"],
                                separator="semicolon" if request.args.get("sep") == "semicolon" else "comma",
                                sources=pool["sources"], drop_speeders=pool["drop"])
    name = export.filename(survey, "schleife-" + str(node.get("code") or nid) + "-lang")
    return FlaskResponse(body, mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Cache-Control": "no-store"})


@bp.route("/<sid:survey_id>/export/loop/<nid>.csv")
@login_required
def export_loop_table(survey_id, nid):
    """The table of a loop: one row per pass, a column per column of the table (export.loop_table_rows). Holds no
    answers, so every role may download it, like the codebook."""
    from flask import Response as FlaskResponse

    from app import export, flow

    survey = get_survey(survey_id)
    node, _ = flow.find(flow.tree(survey), nid)
    if node is None or node.get("t") != "loop":
        abort(404)
    body = export.loop_table_csv(survey, node, separator="semicolon" if request.args.get("sep") == "semicolon" else "comma")
    name = export.filename(survey, export.loop_table_name(node)[:-4])  # "…-loop1_table-<date>.csv", as the codebook names it
    return FlaskResponse(body, mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Cache-Control": "no-store"})
