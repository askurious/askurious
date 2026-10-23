"""Images and media of an element: generate, upload, names, approve, review, scene."""
from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ai, autosave, experimental, media, undo
from app.editor.base import _get_item, _lang, _require_draft, bp
from app.extensions import db
from app.lang import de_en
from app.models import Asset
from app.surveys import get_survey


def _image_rows(survey, item):
    """One row per image the item needs (conjoint combinations or loop variable combinations)."""
    rows = []
    for key in _page_keys(item):
        label = item.kind.key_label(item, key)
        assets = sorted([a for a in item.assets if a.key == key], key=lambda a: a.created_at, reverse=True)
        rows.append({"key": key, "label": label, "assets": assets,
                     "name": item.kind.upload_name(item, key) if hasattr(item.kind, "upload_name") else "",
                     "approved": next((a for a in assets if a.status == "approved"), None),
                     "pending": [a for a in assets if a.status == "pending"]})
    return rows


def _has_image_set(item):
    return item.kind.has_image_set(item)


def _page_keys(item):
    """The keys of the images page: the combinations, or with conjoint's "Tabelle mit Bildern je Stufe" the levels."""
    return item.kind.image_page_keys(item) if hasattr(item.kind, "image_page_keys") else item.kind.required_keys(item)


def _weg():
    """The way the images page was opened: "hochladen" (own files) or "ki" (generated), or None (both, e.g. answer
    options as images); kept by the forms of the page (hidden field weg)."""
    return request.values.get("weg") if request.values.get("weg") in ("hochladen", "ki") else None


@bp.route("/items/<int:item_id>/images")
@login_required
def item_images(survey_id, item_id):
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if not _has_image_set(item):
        abort(404)
    from app import image_styles

    rows = _image_rows(survey, item)
    return render_template("items/images.html", survey=survey, item=item, kind=item.kind, lang=_lang(survey),
                           rows=rows, weg=_weg(), structural=survey.is_editable and survey.can(current_user, "editor"), style_label=image_styles.style_label(survey),
                           style_id=image_styles.style_id(survey))


@bp.route("/items/<int:item_id>/images/generate", methods=["POST"])
@login_required
def item_images_generate(survey_id, item_id):
    """Generate one image for one key (called repeatedly by the batch in the browser)."""
    from flask import current_app, jsonify

    from app import image_styles

    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if not _has_image_set(item) or not survey.is_editable:
        return jsonify(error=de_en("Nur im Entwurf möglich.", "Only possible in a draft.")), 400
    key = request.form.get("key", "")
    keys = _page_keys(item)
    if key not in keys:
        return jsonify(error=de_en("Diese Kombination gibt es nicht mehr. Bitte die Seite neu laden.",
                                   "This combination no longer exists. Please reload the page.")), 400
    if len(keys) > 500:
        return jsonify(error=de_en("Zu viele Bilder (über 500).", "Too many images (over 500).")), 400
    if request.form.get("force") != "1" and any(a.key == key and a.status in ("approved", "pending") for a in item.assets):
        return jsonify(ok=True, skipped=True)
    if len(key) > Asset.__table__.c.key.type.length:  # checked before paying for an image that cannot be stored
        return jsonify(error=de_en("Diese Kombination lässt sich nicht speichern (Schlüssel zu lang).",
                                   "This combination cannot be stored (key too long).")), 400
    prompt = item.kind.prompt_for_key(item, key)
    size = item.kind.image_size(item)
    try:
        name = media.save_b64(survey, ai.generate_image(prompt, size, purpose="image_set", item_type=item.type))
    except (ai.AIError, ValueError) as e:
        return jsonify(error=str(e)), 502
    asset = Asset(key=key, filename=name, source="ai", prompt=prompt, style=image_styles.style_id(survey),
                  model=current_app.config["OPENAI_IMAGE_MODEL"], status=Asset.PENDING)
    item.assets.append(asset)
    db.session.commit()
    return jsonify(ok=True, asset=asset.id)


@bp.route("/items/<int:item_id>/images/upload", methods=["POST"])
@login_required
def item_images_upload(survey_id, item_id):
    """An uploaded image for one key of an image set whose type allows uploads (answer options as images,
    conjoint combinations): it takes the place of the earlier images of that key and is approved at once (as the
    upload of an image). With ajax=1 (several files, sent one by one from the image page) the answer is JSON."""
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if not _has_image_set(item) or not getattr(item.kind, "image_uploads", False):
        abort(404)
    ajax = request.form.get("ajax") == "1"
    back = redirect(url_for("editor.item_images", survey_id=survey.id, item_id=item.id, lang=_lang(survey), weg=_weg()))
    key, f = request.form.get("key", ""), request.files.get("file")
    if ajax and not survey.is_editable:
        return jsonify(error=de_en("Nur im Entwurf möglich.", "Only possible in a draft.")), 400
    if not _require_draft(survey) or key not in _page_keys(item) or f is None or not f.filename:
        return (jsonify(error=de_en(
            "Keine Datei gewählt, oder die Kombination gibt es nicht mehr. Bitte eine Bilddatei (JPG, PNG, GIF, WebP) "
            "auswählen; hilft das nicht, die Seite neu laden.",
            "No file chosen, or the combination no longer exists. Please choose an image file (JPG, PNG, GIF, WebP); "
            "if that does not help, reload the page.")), 400) if ajax else back
    side = item.kind.image_upload_side(item, key) if hasattr(item.kind, "image_upload_side") else None
    try:
        data = f.read()
        name = media.save_bytes(survey, media.fit_still(data, side) if side else media.strip_metadata(data))
    except ValueError as e:
        if ajax:
            return jsonify(error=str(e)), 400
        flash(str(e), "error")
        return back
    _replace_static_asset(survey, item, Asset(key=key, filename=name, source="upload", status=Asset.APPROVED))
    db.session.commit()
    if ajax:
        return jsonify(ok=True)
    flash(de_en("Bild für „{key}“ hochgeladen und freigegeben.", "Image for “{key}” uploaded and approved.",
                key=item.kind.key_label(item, key)), "success")
    return back


@bp.route("/items/<int:item_id>/media", methods=["POST"])
@login_required
def item_media_upload(survey_id, item_id):
    """A video or audio file of a stimulus (items/stimulus.py), sent by the editor's script in pieces of
    stimulus.CHUNK_BYTES: upload (32 hex), index, last (1 on the last piece), file; with the last piece what the
    browser read from the file: kind (video or audio) and seconds. The finished file takes the place of the earlier
    one of the element. Answers JSON."""
    from app.items import stimulus

    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if item.type != "media":
        abort(404)
    if not survey.is_editable:
        return jsonify(error=de_en("Nur im Entwurf möglich.", "Only possible in a draft.")), 400
    upload, piece = request.form.get("upload", ""), request.files.get("file")
    key = request.form.get("key", "")  # "" or the key of a value of the loop column (stimulus.required_keys)
    if key not in dict(item.kind.required_keys(item)):
        return jsonify(error=de_en("Unbekannter Wert der Schleife.", "Unknown value of the loop.")), 400
    old = next((a for a in item.assets if a.key == key), None)
    try:
        index = int(request.form.get("index", ""))
        if piece is None:
            raise ValueError(de_en("Keine Datei gewählt. Bitte eine Video- oder Audiodatei (MP4, WebM, MP3, M4A, OGG, WAV) "
                                   "auswählen.",
                                   "No file chosen. Please choose a video or audio file (MP4, WebM, MP3, M4A, OGG, WAV)."))
        slot = media.part_slot(item, key)
        media.add_media_part(survey, slot, upload, index, piece.read(stimulus.CHUNK_BYTES + 1),
                             replaced=old.filename if old else None)
        if request.form.get("last") != "1":
            return jsonify(ok=True)
        name, mime, size = media.finish_media(survey, slot, upload)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    kind = request.form.get("kind")
    kind = kind if kind in stimulus.KINDS else stimulus.MIMES[mime][0]
    try:
        seconds = float(request.form.get("seconds", ""))
    except ValueError:
        seconds = 0.0
    seconds = round(seconds, 2) if 0 < seconds <= stimulus.MAX_SECONDS else 0.0
    _replace_static_asset(survey, item, Asset(key=key, filename=name, source="upload", status=Asset.APPROVED))
    files = dict((item.config or {}).get("files") or {})
    files[key] = {"kind": kind, "mime": mime, "bytes": size, "seconds": seconds,
                  "name": " ".join((piece.filename or "").split())[:200]}
    item.config = {**item.config, "files": files}  # the file is outside the history (as an uploaded image)
    db.session.commit()
    return jsonify(ok=True, kind=kind, seconds=seconds, bytes=size)


@bp.route("/items/<int:item_id>/images/filenames.csv")
@login_required
def item_images_names(survey_id, item_id):
    """The file names that assign uploads to the combinations, with the combination next to each (for preparing
    the images outside Askurious)."""
    import csv
    import io

    from flask import Response

    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if not _has_image_set(item) or not hasattr(item.kind, "upload_name"):
        abort(404)
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Dateiname", "Kombination"])
    from app.export import safe_text
    for key in _page_keys(item):  # level texts that start like a formula stay text in a spreadsheet (audit 2026-10-09, D4)
        writer.writerow([safe_text(item.kind.upload_name(item, key)), safe_text(item.kind.key_label(item, key))])
    return Response("﻿" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{item.code}-dateinamen.csv"'})


@bp.route("/items/<int:item_id>/images/approve-all", methods=["POST"])
@login_required
def item_images_approve_all(survey_id, item_id):
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if not _has_image_set(item):  # like the image page: an item without an image set has no keys (was a 500)
        abort(404)
    if _require_draft(survey):
        for row in _image_rows(survey, item):
            if row["approved"] is None and row["pending"]:
                row["pending"][0].status = Asset.APPROVED
        db.session.commit()
    return redirect(url_for("editor.item_images", survey_id=survey.id, item_id=item.id, lang=_lang(survey), weg=_weg()))


@bp.route("/items/<int:item_id>/images/review", methods=["POST"])
@login_required
def item_images_review(survey_id, item_id):
    """AI feedback on the base prompt and every image hint (app/prompt_review.py): suggestions to tick."""
    from app import image_styles, prompt_review

    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if not hasattr(item.kind, "visual_attributes") or not survey.can(current_user, "editor"):
        abort(404)
    if not _require_draft(survey):
        return redirect(url_for("editor.item_images", survey_id=survey.id, item_id=item.id, lang=_lang(survey), weg=_weg()))
    try:
        result = prompt_review.review(item)
    except ai.AIError as e:
        flash(str(e), "error")
        return redirect(url_for("editor.item_images", survey_id=survey.id, item_id=item.id, lang=_lang(survey), weg=_weg()))
    return render_template("items/prompt_review.html", survey=survey, item=item, kind=item.kind, lang=_lang(survey),
                           result=result, style_label=image_styles.style_label(survey))


@bp.route("/items/<int:item_id>/images/review/apply", methods=["POST"])
@login_required
def item_images_review_apply(survey_id, item_id):
    """Take over the ticked suggestions of the AI feedback, as one step (undo)."""
    from app import prompt_review

    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if not hasattr(item.kind, "visual_attributes") or not survey.can(current_user, "editor"):
        abort(404)
    if _require_draft(survey):
        scene = request.form.get("scene") if request.form.get("take_scene") else None
        hints = {lid: request.form.get(f"hint_{lid}", "") for lid in request.form.getlist("take_level")}
        if scene is None and not hints:
            flash(de_en("Kein Vorschlag ausgewählt; nichts geändert.", "No suggestion chosen; nothing changed."), "warning")
        else:
            step = undo.begin(survey)
            prompt_review.apply(item, scene, hints)
            step.done(undo.both("KI-Vorschläge für die Bild-Prompts von {code} übernommen",
                            "AI suggestions for the image prompts of {code} accepted", code=item.code), item=item.id)
            db.session.commit()
            parts = (([de_en("Grundprompt", "Base prompt")] if scene is not None else [])
                     + ([de_en("{n} Bildhinweise", "{n} image hints", n=len(hints))] if hints else []))
            flash(de_en("{parts} übernommen; „Neu erzeugen“ verwendet sie.", "{parts} accepted; “Generate anew” uses them.",
                        parts=de_en(" und ", " and ").join(parts)), "success")
    return redirect(url_for("editor.item_images", survey_id=survey.id, item_id=item.id, lang=_lang(survey), weg=_weg()))


@bp.route("/items/<int:item_id>/images/scene", methods=["POST"])
@login_required
@autosave.aware
def item_images_scene(survey_id, item_id):
    """The base prompt of the images of a choice task (what every image shows; the levels and the style are added),
    saved from the images page; the same field as on the tab "Darstellung" of the item."""
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if not hasattr(item.kind, "visual_attributes"):
        abort(404)
    if not survey.can(current_user, "editor"):
        abort(403)
    if _require_draft(survey):
        step = undo.begin(survey)
        item.config = {**item.config, "scene": request.form.get("scene", "").strip()[:2000]}
        step.done(undo.both("Grundprompt der Bilder von {code}", "Base prompt of the images of {code}", code=item.code),
                  item=item.id, merge=autosave.merge(f"scene:{item.id}"))
        db.session.commit()
        flash(de_en("Grundprompt gespeichert; neu erzeugte Bilder verwenden ihn.", "Base prompt saved; newly generated images use it."),
              "success")
    return redirect(url_for("editor.item_images", survey_id=survey.id, item_id=item.id, lang=_lang(survey), weg=_weg()))


def _replace_static_asset(survey, item, asset):
    media.delete_after_rollback(survey, asset.filename)
    for old in [a for a in item.assets if a.key == asset.key]:
        media.delete_after_commit(survey, old.filename)
        item.assets.remove(old)
    item.assets.append(asset)
