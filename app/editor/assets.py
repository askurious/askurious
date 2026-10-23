"""Static assets of an element: upload, generate, review."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ai, media, undo
from app.editor.base import _get_item, _lang, _require_draft, bp
from app.editor.images import _has_image_set, _replace_static_asset, _weg
from app.extensions import db
from app.lang import de_en
from app.models import Asset
from app.surveys import get_survey


# --- image assets ------------------------------------------------------------------


def _commit_assets():
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()  # retains old files and removes registered new files
        raise


def _item_page(survey, item, lang):
    return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang) + "#assets")


@bp.route("/image-rights", methods=["GET", "POST"])
@login_required
def image_rights(survey_id):
    from app import asset_rights

    survey = get_survey(survey_id, need="editor")
    if request.method == "POST":
        target = request.form.get("asset_id", "")
        candidates = asset_rights.image_assets(survey, False) + asset_rights.theme_images(survey)
        asset = next((a for a in candidates if str(a.id) == target), None)
        if asset is None:
            abort(404)
        try:
            asset_rights.record(survey, asset, request.form, current_user)
        except asset_rights.RightsError as exc:
            flash(str(exc), "error")
        else:
            db.session.commit()
            flash(de_en("Bildrechte gespeichert.", "Image rights saved."), "success")
        return redirect(url_for("editor.image_rights", survey_id=survey.id) + f"#image-{asset.id}")
    rows = [{"asset": a, "values": asset_rights.suggested(survey, a), "confirmed": asset_rights.declared(survey, a)}
            for a in asset_rights.image_assets(survey, False) + asset_rights.theme_images(survey)]
    return render_template("items/image/rights.html", survey=survey, rows=rows)


@bp.route("/items/<int:item_id>/upload", methods=["POST"])
@login_required
def upload(survey_id, item_id):
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    f = request.files.get("file")
    if not _require_draft(survey) or not item.kind.has_image or f is None:
        return _item_page(survey, item, lang)
    try:
        name = media.save_bytes(survey, media.participant_upload(f.read()))
    except ValueError as e:
        flash(str(e), "error")
        return _item_page(survey, item, lang)
    step = undo.begin(survey)
    _replace_static_asset(survey, item, Asset(key="", filename=name, source="upload", status=Asset.APPROVED))
    item.config = {**item.config, "source": "upload"}
    # the file itself is outside the history: only the setting "uploaded, not generated" can be taken back
    step.done(undo.both("Bildquelle von „{code}“ auf „hochgeladen“ gestellt", "Image source of “{code}” set to “uploaded”",
                    code=item.code), page="item", item=item.id)
    _commit_assets()
    flash(de_en("Bild hochgeladen.", "Image uploaded."), "success")
    return _item_page(survey, item, lang)


@bp.route("/items/<int:item_id>/generate", methods=["POST"])
@login_required
def generate(survey_id, item_id):
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    if not _require_draft(survey) or not item.kind.has_image:
        return _item_page(survey, item, lang)
    step = undo.begin(survey)
    item.kind.update_ai_settings(item, request.form)
    step.done(undo.both("Bild-Prompt von „{code}“ geändert", "Image prompt of “{code}” changed", code=item.code), page="item",
              item=item.id)
    _commit_assets()
    if item.kind.is_dynamic(item):
        flash(de_en("Prompt gespeichert. Er enthält Platzhalter: Die Bilder je Kombination werden unter „Bilder erzeugen "
                    "und prüfen“ erzeugt.",
                    "Prompt saved. It contains placeholders: the images per combination are generated under “Generate and "
                    "check images”."), "success")
        return _item_page(survey, item, lang)
    if not item.config.get("prompt"):
        flash(de_en("Bitte einen Prompt eingeben.", "Please enter a prompt."), "error")
        return _item_page(survey, item, lang)
    prompt = item.kind.full_prompt(item)
    try:
        b64 = ai.generate_image(prompt, item.config.get("size", "1536x1024"), purpose="image", item_type=item.type)
        name = media.save_b64(survey, b64)
    except (ai.AIError, ValueError) as e:
        flash(str(e), "error")
        return _item_page(survey, item, lang)
    from flask import current_app

    # A new generation is a candidate: keep it next to the current approved image until reviewed
    from app import image_styles

    media.delete_after_rollback(survey, name)
    item.assets.append(Asset(key="", filename=name, source="ai", prompt=prompt, style=image_styles.style_id(survey),
                             model=current_app.config["OPENAI_IMAGE_MODEL"], status=Asset.PENDING))
    _commit_assets()
    flash(de_en("Bild erzeugt. Bitte prüfen und freigeben.", "Image generated. Please check and approve it."), "success")
    return _item_page(survey, item, lang)


@bp.route("/assets/<int:asset_id>/<action>", methods=["POST"])
@login_required
def review_asset(survey_id, asset_id, action):
    survey = get_survey(survey_id)
    asset = db.session.get(Asset, asset_id)
    if asset is None or asset.item.block.survey_id != survey.id:
        abort(404)
    item = asset.item
    if _require_draft(survey):
        if action == "approve":
            for other in item.assets:
                if other.key == asset.key and other.id != asset.id and other.status == Asset.APPROVED:
                    other.status = Asset.REJECTED
            asset.status = Asset.APPROVED
        elif action == "reject":
            asset.status = Asset.REJECTED
        elif action == "delete":
            media.delete_after_commit(survey, asset.filename)
            item.assets.remove(asset)
        else:
            abort(400)
        _commit_assets()
    if _has_image_set(item):
        return redirect(url_for("editor.item_images", survey_id=survey.id, item_id=item.id, lang=_lang(survey), weg=_weg())
                        + f"#img-{asset_id}")
    return _item_page(survey, item, _lang(survey))
